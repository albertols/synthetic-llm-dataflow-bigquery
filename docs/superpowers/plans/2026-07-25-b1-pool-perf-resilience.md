# B.1 Free-Text Pool Performance & Setup Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the B.1 free-text pool build from ~26 min back to single-digit minutes and make one stubborn column unable to triple `DoFn.setup()` — the two root causes of the 2026-07-24 E2E slowdowns (3187 s and 4045 s vs the 1481–1679 s master/ws1 baseline).

**Architecture:** Five surgical changes, no new abstractions: (1) each pool HTTP round trip requests `n=4` array-completions server-side (vLLM parallel sampling) so per-call novel yield ×4 and the attempt budget scales down accordingly; (2) B.1 gains B.2's relaxed-shape fallback + a shape top-up for undersized pools, so echo-saturated identifier-ish columns (CHG_MESS_CARR_ID, CHANGE_USERID) stop raising `FreeTextEmptyYieldError` out of `setup()`; (3) per-column ladders run concurrently in a bounded thread pool (embedder work stays sequential — HF tokenizers are not thread-safe); (4) a process-level pool cache keyed on `(reference_digest, model_uri, column, target)` survives Dataflow bundle retries, so a retried `setup()` never rebuilds a sibling column's pool; (5) `DoFn.setup()` re-entry after a failure emits a `dofn_setup_retry` milestone + Beam counter so retries are visible in worker logs and job metrics.

**Tech Stack:** pure-Python stdlib (`threading`, `concurrent.futures`) in `sdfb-core`; Apache Beam metrics in `sdfb-beam`; pytest in `sdfb-tests`. No new dependencies.

## Global Constraints

- Laptop-only work: no `@pytest.mark.gpu` / `@pytest.mark.gcp` tests; everything runs with mocks/fakes.
- Test command (laptop quirk): `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q` (expect all green, 604+ tests).
- Lint: `uv run ruff check .` must pass after every task.
- `sdfb-core` must NOT import `apache_beam`, `torch`, `vllm`, `faiss`, `numpy` at module scope (`threading`/`concurrent.futures` are stdlib and fine).
- Milestone names must fully match `[a-z0-9_]+` (enforced by `sdfb_core.observability.format_milestone`).
- Existing milestone names (`freetext_pool_undersized`, `freetext_pool_stagnated`, `b1_pools_built`, …) are a log-mining API consumed by `scripts/e2e_gcp_probe.py` — only ADD names, never rename/remove.
- Never log reference VALUES in milestones — counts only (privacy contract, repeated in engine.py comments).
- Prompts must keep a stable prefix across attempts for a column (vLLM automatic-prefix-caching / future LMCache KV reuse) — vary only sampling params per attempt, never prepend text.
- Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01FzPHDr8vfd4if8P7AyUuKj`

## Evidence baseline (context for reviewers)

| Phase | 07-23 00:03 baseline | 07-24 12:46 run | 07-24 16:35 run |
|---|---|---|---|
| Pool build | 122 s | 1552 s | ~2645 s over 3 `setup()` attempts |
| Wall clock | 24.7 min | 53.1 min | 67.4 min |

Cause 1: WS2 §4b.2 raised pool target 32 → `min(num_rows, distinct, 512)` ⇒ up to 32 sequential vLLM calls/column at 20–48 s/call. Cause 2 (16:35 run only): `FreeTextEmptyYieldError` on `CHG_MESS_CARR_ID` (parroting: `distinct=1, novel=0`) crashed `setup()` twice; Dataflow silently retried; `_build_free_text_pools` is all-or-nothing, so every retry rebuilt every pool; the run PASSED with zero trace and collapsed diversity (`CHANGE_USERID` 229→31 distinct).

---

### Task 1: n-choice batched pool calls (`_pool_llm_yield`)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (constants block ~line 85; `_pool_llm_yield` ~line 602)
- Test: `packages/sdfb-tests/tests/unit/rag/test_pool_scaling.py` (append)

**Interfaces:**
- Produces: `_pool_llm_yield(client, prompt, json_schema, prof, seed_examples, target=..., n_choices=_POOL_PARALLEL_CHOICES) -> _PoolYield` — later tasks call it unchanged (default `n_choices`).
- Produces: module constant `_POOL_PARALLEL_CHOICES = 4`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/sdfb-tests/tests/unit/rag/test_pool_scaling.py` (read the file's existing imports first; it already imports the engine module — reuse its existing `ColumnProfile`/schema helpers if present, otherwise use exactly this self-contained block):

```python
# --- n-choice batched pool calls (2026-07-25 perf fix) ---------------------

from sdfb_core.engines.b1_rag import engine as b1_engine
from sdfb_core.engines.b1_rag.engine import _pool_llm_yield
from sdfb_core.engines.b1_rag.profile import ColumnKind, ColumnProfile


class _RecordingArrayClient:
    """Returns `n` distinct {"values": [...]} dicts per call and records
    the `n` each call requested."""

    def __init__(self, values_per_choice: int = 32, novel_per_call: int | None = None):
        self.calls: list[int] = []
        self._values_per_choice = values_per_choice
        # When set, ONLY this many values across the whole call are novel;
        # the rest repeat a fixed token (stagnation/cap scenarios).
        self._novel_per_call = novel_per_call
        self._counter = 0

    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        self.calls.append(n)
        out = []
        for choice in range(n):
            values = []
            for i in range(self._values_per_choice):
                if self._novel_per_call is not None and (
                    choice * self._values_per_choice + i >= self._novel_per_call
                ):
                    values.append("dup-fixed-token")
                else:
                    self._counter += 1
                    values.append(f"novel-{self._counter:05d}")
            out.append({"values": values})
        return out


def _free_text_profile() -> ColumnProfile:
    return ColumnProfile(
        name="col_a",
        bq_type="STRING",
        kind=ColumnKind.FREE_TEXT,
        observed_values=tuple(f"observed value number {i}" for i in range(600)),
        text_examples=("observed value number 0", "observed value number 1"),
    )


def test_pool_call_requests_parallel_choices():
    client = _RecordingArrayClient()
    y = _pool_llm_yield(
        client, "p", {}, _free_text_profile(), ["seed"], target=512
    )
    assert client.calls, "expected at least one LLM call"
    assert all(n == b1_engine._POOL_PARALLEL_CHOICES for n in client.calls)
    assert len(y.pool) >= 512
    # 4 choices x 32 values = 128 novel per call -> target hit in 4 calls.
    assert y.attempts == 4


def test_pool_call_budget_scales_with_choices():
    # Every call yields exactly 5 novel values (>= _POOL_STAGNATION_MIN_NOVEL,
    # so the stagnation exit never fires) -> the loop must stop at the
    # SCALED cap: max(len(levels), 2*ceil(512/(32*4))) = 8, not 32.
    client = _RecordingArrayClient(novel_per_call=5)
    y = _pool_llm_yield(
        client, "p", {}, _free_text_profile(), ["seed"], target=512
    )
    assert y.attempts == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_pool_scaling.py -q -k "parallel_choices or budget_scales"`
Expected: FAIL — `_POOL_PARALLEL_CHOICES` does not exist / `calls == [1, 1, ...]` / `attempts == 32`.

- [ ] **Step 3: Implement**

In `engine.py`, after the `_POOL_VALUES_PER_CALL = 32` line add:

```python
# Server-side parallel sampling (2026-07-25 perf fix): each pool HTTP round
# trip requests this many INDEPENDENT array-completions (`n=`) and de-dupes
# across them, multiplying per-call novel yield ~4x. Safe because pool
# requests are UNSEEDED — the 2026-07-16 collapse was n=32 single-value
# choices under a pinned seed, a different shape entirely. The prompt stays
# byte-identical across attempts (stable prefix ⇒ vLLM APC / LMCache-ready).
_POOL_PARALLEL_CHOICES = 4
```

In `_pool_llm_yield`:
- change the signature to
  ```python
  def _pool_llm_yield(
      client: ModelClient,
      prompt: str,
      json_schema: dict,
      prof: ColumnProfile,
      seed_examples: list[str],
      target: int = _DEFAULT_FREE_TEXT_POOL,
      n_choices: int = _POOL_PARALLEL_CHOICES,
  ) -> _PoolYield:
  ```
- replace the `max_calls` line with:
  ```python
  per_round = _POOL_VALUES_PER_CALL * max(1, n_choices)
  max_calls = max(len(levels), 2 * -(-target // per_round))
  ```
- in the `client.generate_json(...)` call change `n=1` to `n=max(1, n_choices)`.
- extend the docstring's final paragraph: replace the "No request seed" paragraph with:
  ```
  No request seed: a pinned seed with n>1 collapses all n vLLM choices
  into one completion (2026-07-15 run). Unseeded, the n choices sample
  independently, so one round trip carries n distinct 32-value arrays —
  the call budget scales down by the same factor (`per_round`).
  ```

- [ ] **Step 4: Run the new tests and the whole module**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_pool_scaling.py packages/sdfb-tests/tests/unit/engines/ -q`
Expected: the two new tests PASS. Some pre-existing tests may now fail **only** because their fakes yield 4× values per call (attempt-count assertions written for 1 choice/call). For each failure, verify the delta is exactly the ×4-yield consequence, then update the assertion (e.g. expected attempts 16 → 4). If a failure is anything else, STOP — that's a real regression.

- [ ] **Step 5: Full suite + lint, then commit**

Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`
Expected: all green.

```bash
git add packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py packages/sdfb-tests/tests/unit/rag/test_pool_scaling.py
git commit -m "perf(b1): batch n=4 array-completions per pool call, scale call budget

One HTTP round trip now carries 4 independent guided-JSON array
completions (unseeded vLLM parallel sampling), quadrupling per-call novel
yield; max_calls scales down by the same factor (32 -> 8 worst-case for
target=512). First of the 2026-07-24 E2E slowdown fixes (pool build was
1552s / 49% of wall clock).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FzPHDr8vfd4if8P7AyUuKj"
```

---

### Task 2: B.1 relaxed-shape fallback + undersized top-up

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (imports ~line 56; `_infer_free_text_pool` ~line 457; new method `_shape_fallback_pool`)
- Test: `packages/sdfb-tests/tests/unit/rag/test_b1_shape_fallback.py` (create)

**Interfaces:**
- Consumes: `build_relaxed_shapes`, `sample_relaxed_identifier` from `sdfb_core.engines.text_shapes` (exist; B.2 uses them at `b2_library/freetext.py:479-493`).
- Produces: `B1RagEngine._shape_fallback_pool(prof: ColumnProfile, count: int, exclude: set[str]) -> list[str]` (returns `[]` when no template fits or keyspace saturated).
- Produces: milestones `freetext_pool_shape_fallback` (same name/fields as B.2's) and `freetext_pool_shape_topup` (new).

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_b1_shape_fallback.py`:

```python
"""B.1 parity with B.2's relaxed-shape fallback (2026-07-24 16:35 E2E).

That run: CHG_MESS_CARR_ID parroted one exemplar on every escalation level
(distinct=1, novel=0) -> FreeTextEmptyYieldError out of DoFn.setup() ->
Dataflow silently retried the whole setup twice (~17 min of rework).
A relaxed per-position template generates verified-novel in-format values
without the LLM, exactly as b2_library/freetext.py already does.
"""

from __future__ import annotations

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag.engine import B1RagEngine
from sdfb_core.engines.base import FreeTextEmptyYieldError, GenerationContext
from sdfb_core.observability import parse_milestone


def _schema(col: str) -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.t"},
            "schema": [{"name": col, "type": "STRING", "mode": "REQUIRED"}],
        }
    )


# Mixed lengths defeat detect_identifier_shape (strict), no whitespace so
# build_relaxed_shapes CAN template them -> FREE_TEXT with
# identifier_shape=None, exactly the CHG_MESS_CARR_ID class.
_ID_VALUES = [f"USR{i:04d}X" for i in range(30)] + [f"USR{i:05d}XX" for i in range(30)]
# Whitespace -> build_relaxed_shapes returns None (prose stays prose).
_PROSE_VALUES = [f"support ticket about outage number {i}" for i in range(60)]


class _ParrotClient:
    """Always echoes shown observed values — parsed>0, novel=0."""

    def __init__(self, echoes: list[str]):
        self._echoes = echoes
        self.call_count = 0

    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        self.call_count += 1
        return [{"values": list(self._echoes[:8])} for _ in range(n)]


def _ctx(col: str, values: list[str]) -> GenerationContext:
    return GenerationContext(
        table_schema=_schema(col),
        reference_rows=[{col: v} for v in values],
        pipeline_run_id="run-shape-1",
        strict_freetext=True,
        num_rows=40,
    )


def test_copy_saturated_id_column_falls_back_to_shapes(caplog):
    engine = B1RagEngine()
    with caplog.at_level("WARNING"):
        engine.setup(_ParrotClient(_ID_VALUES), _ctx("carr_id", _ID_VALUES))
    pool = engine._free_text_pools["carr_id"]
    assert pool, "shape fallback must produce a pool instead of raising"
    observed = set(_ID_VALUES)
    assert all(v not in observed for v in pool), "fallback values must be novel"
    names = [
        m["name"]
        for m in (parse_milestone(r.getMessage()) for r in caplog.records)
        if m
    ]
    assert "freetext_pool_shape_fallback" in names


def test_copy_saturated_prose_column_still_raises_strict():
    engine = B1RagEngine()
    with pytest.raises(FreeTextEmptyYieldError):
        engine.setup(_ParrotClient(_PROSE_VALUES), _ctx("notes", _PROSE_VALUES))


class _FewNovelThenParrotClient(_ParrotClient):
    """First call yields 6 novel values, then parrots -> undersized pool."""

    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        self.call_count += 1
        if self.call_count == 1:
            return [{"values": [f"GEN{i:04d}Z" for i in range(6)]}]
        return [{"values": list(self._echoes[:8])} for _ in range(n)]


def test_undersized_pool_topped_up_with_shape_values(caplog):
    engine = B1RagEngine()
    with caplog.at_level("WARNING"):
        engine.setup(
            _FewNovelThenParrotClient(_ID_VALUES), _ctx("carr_id", _ID_VALUES)
        )
    pool = engine._free_text_pools["carr_id"]
    # target = min(num_rows=40, distinct=60, 512) = 40; the LLM delivered 6.
    assert len(pool) == 40
    names = [
        m["name"]
        for m in (parse_milestone(r.getMessage()) for r in caplog.records)
        if m
    ]
    assert "freetext_pool_shape_topup" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_b1_shape_fallback.py -q`
Expected: FAIL — first/third tests raise `FreeTextEmptyYieldError` / pool stays at 6.

- [ ] **Step 3: Implement**

In `engine.py` imports, extend the `text_shapes` import:

```python
from sdfb_core.engines.text_shapes import (
    build_relaxed_shapes,
    sample_identifier,
    sample_relaxed_identifier,
)
```

Add this method to `B1RagEngine` (after `_infer_free_text_pool`):

```python
def _shape_fallback_pool(
    self, prof: ColumnProfile, count: int, exclude: set[str]
) -> list[str]:
    """Verified-novel values from a relaxed per-position template, or [].

    B.2-parity route (freetext.py:_shape_fallback_pool) for copy-saturated
    or undersized LLM builds: mixed-length identifier-ish columns the
    strict detector rejects still template per length bucket, and every
    emitted value is rejected against the observed reference values (and
    `exclude`, and itself) — nothing here can memorize. Prose columns
    (whitespace) return [] and the caller keeps its existing raise/
    fallback path. Deterministic per (run_id, column).
    """
    shapes = build_relaxed_shapes([str(v) for v in prof.observed_values])
    if shapes is None or count <= 0:
        return []
    run_id = self._ctx.pipeline_run_id if self._ctx is not None else ""
    rng = random.Random(_mix_seed(None, f"{run_id}:shape:{prof.name}"))
    observed = {str(v) for v in prof.observed_values}
    out: list[str] = []
    seen: set[str] = set()
    # Bounded rejection sampling: dense keyspaces stop at the cap
    # instead of spinning (same 40x budget as B.2).
    for _ in range(count * 40):
        v = sample_relaxed_identifier(shapes, rng.randrange)
        if v in observed or v in exclude or v in seen:
            continue
        seen.add(v)
        out.append(v)
        if len(out) >= count:
            break
    return out
```

In `_infer_free_text_pool`, replace the `else:` branch body (currently `pool = y.pool` through the `freetext_pool_undersized` milestone) with:

```python
else:
    pool = y.pool
    if not pool and y.parsed > 0:
        # Copy-saturated: the model parsed values but every one was an
        # observed copy — deterministic on rebuild, so retrying or
        # failing the bundle buys nothing (the 2026-07-24 16:35 E2E
        # burned 2 full setup() retries exactly here). A relaxed
        # template can still generate verified-novel in-format values.
        shape_pool = self._shape_fallback_pool(prof, target, exclude=set())
        if shape_pool:
            log_milestone(
                "freetext_pool_shape_fallback",
                level=logging.WARNING,
                column=prof.name,
                pool_size=len(shape_pool),
                target=target,
                attempts=y.attempts,
                parsed=y.parsed,
                verbatim_copies=y.copies,
                prompt_echoes=y.prompt_echoes,
            )
            pool = shape_pool
    if not pool:
        diagnosis = (
            f"attempts={y.attempts}, requested_per_attempt="
            f"{per_call}, parsed={y.parsed}, "
            f"distinct={y.distinct}, verbatim_copies={y.copies}, "
            f"prompt_echoes={y.prompt_echoes}, novel=0"
        )
        if self._ctx is not None and self._ctx.strict_freetext:
            raise FreeTextEmptyYieldError(
                f"LLM calls for free-text column {prof.name!r} "
                f"yielded no usable values ({diagnosis})."
            )
        log_milestone(
            "freetext_llm_fallback",
            level=logging.WARNING,
            column=prof.name,
            error="EmptyYield",
            attempts=y.attempts,
            parsed=y.parsed,
            distinct=y.distinct,
            verbatim_copies=y.copies,
            prompt_echoes=y.prompt_echoes,
        )
    elif len(pool) < target:
        # Top up an undersized pool from the template before accepting
        # the shortfall — the 2026-07-24 16:35 run landed CHANGE_USERID
        # with 31 distinct values over 1000 rows (diversity collapse).
        top_up = self._shape_fallback_pool(
            prof, target - len(pool), exclude=set(pool)
        )
        if top_up:
            log_milestone(
                "freetext_pool_shape_topup",
                level=logging.WARNING,
                column=prof.name,
                added=len(top_up),
                pool_size=len(pool) + len(top_up),
                target=target,
                attempts=y.attempts,
            )
            pool = [*pool, *top_up]
        if len(pool) < target:
            log_milestone(
                "freetext_pool_undersized",
                level=logging.WARNING,
                column=prof.name,
                pool_size=len(pool),
                target=target,
                attempts=y.attempts,
                parsed=y.parsed,
                distinct=y.distinct,
                verbatim_copies=y.copies,
                prompt_echoes=y.prompt_echoes,
            )
```

(Note: the original comment block above the strict raise — "The calls succeeded … saturated key space" — stays, moved onto the `if not pool:` block.)

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_b1_shape_fallback.py packages/sdfb-tests/tests/unit/rag/ packages/sdfb-tests/tests/unit/engines/ -q`
Expected: PASS (existing `freetext_pool_undersized` tests must still pass — the milestone still fires when the template can't close the gap).

- [ ] **Step 5: Full suite + lint, then commit**

Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`

```bash
git add packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py packages/sdfb-tests/tests/unit/rag/test_b1_shape_fallback.py
git commit -m "fix(b1): relaxed-shape fallback + top-up instead of setup()-fatal empty yield

Ports B.2's copy-saturated shape fallback to B.1 and adds a shape top-up
for undersized pools. CHG_MESS_CARR_ID-class columns (mixed-length IDs the
strict detector rejects, LLM parrots exemplars) no longer raise
FreeTextEmptyYieldError out of DoFn.setup() — the 2026-07-24 16:35 E2E
paid 2 silent full-setup retries (+17 min) and landed 31-distinct pools.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FzPHDr8vfd4if8P7AyUuKj"
```

---

### Task 3: Parallel per-column pool ladders (embedder work stays sequential)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`_build_free_text_pools` ~line 338; constants block)
- Test: `packages/sdfb-tests/tests/unit/rag/test_pool_parallel_build.py` (create)

**Interfaces:**
- Consumes: `_infer_free_text_pool(prof, seed_examples, target)` (unchanged from Tasks 1–2).
- Produces: module constant `_POOL_BUILD_MAX_WORKERS = 4`; `_build_free_text_pools` behavior contract: all seed-example retrieval (embedder use) completes BEFORE any thread spawns; on a strict per-column failure, all other columns still finish before the exception re-raises (Task 4 relies on this to cache siblings).

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_pool_parallel_build.py`:

```python
"""Column-parallel free-text pool ladders (2026-07-25 perf fix).

The 2026-07-24 12:46 E2E built 3 columns' pools strictly sequentially
(717s + 505s + 330s). The ladders are independent per column and vLLM's
continuous batching absorbs concurrent requests, so they run in a bounded
thread pool. Seed-example retrieval keeps using the embedder SEQUENTIALLY
(HF fast tokenizers are not thread-safe — "Already borrowed").
"""

from __future__ import annotations

import threading
import time

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag.engine import B1RagEngine
from sdfb_core.engines.base import FreeTextEmptyYieldError, GenerationContext

_COLS = ["col_a", "col_b", "col_c"]


def _schema() -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.t"},
            "schema": [
                {"name": c, "type": "STRING", "mode": "REQUIRED"} for c in _COLS
            ],
        }
    )


def _rows() -> list[dict]:
    # 60 distinct multi-word values per column -> FREE_TEXT, no identifier
    # shape, no relaxed template (whitespace).
    return [
        {c: f"{c} reference prose value number {i}" for c in _COLS}
        for i in range(60)
    ]


def _ctx(**overrides) -> GenerationContext:
    defaults = dict(
        table_schema=_schema(),
        reference_rows=_rows(),
        pipeline_run_id="run-par-1",
        num_rows=40,
    )
    defaults.update(overrides)
    return GenerationContext(**defaults)


class _ConcurrencyProbeClient:
    """Prompt-keyed deterministic values; records max in-flight calls."""

    def __init__(self):
        self._lock = threading.Lock()
        self._inflight = 0
        self.max_inflight = 0
        self.call_count = 0

    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        with self._lock:
            self._inflight += 1
            self.call_count += 1
            self.max_inflight = max(self.max_inflight, self._inflight)
        time.sleep(0.05)  # long enough for the other ladders to enter
        col = next((c for c in _COLS if f"'{c}'" in prompt), "unknown")
        out = [
            {"values": [f"{col}-gen-{choice}-{i}" for i in range(32)]}
            for choice in range(n)
        ]
        with self._lock:
            self._inflight -= 1
        return out


def test_ladders_run_concurrently_and_pools_stay_per_column():
    client = _ConcurrencyProbeClient()
    engine = B1RagEngine()
    engine.setup(client, _ctx())
    assert client.max_inflight >= 2, "expected overlapping ladder calls"
    for c in _COLS:
        pool = engine._free_text_pools[c]
        assert pool and all(v.startswith(f"{c}-gen-") for v in pool), (
            "columns must not receive each other's values"
        )


class _OneColumnFailsClient(_ConcurrencyProbeClient):
    """col_b parrots (parsed>0, novel=0) -> strict raise; others succeed."""

    def generate_json(self, prompt, json_schema, **kw):
        if "'col_b'" in prompt:
            with self._lock:
                self.call_count += 1
            return [{"values": ["col_b reference prose value number 0"]}]
        return super().generate_json(prompt, json_schema, **kw)


def test_strict_failure_on_one_column_still_finishes_siblings():
    client = _OneColumnFailsClient()
    engine = B1RagEngine()
    with pytest.raises(FreeTextEmptyYieldError):
        engine.setup(client, _ctx(strict_freetext=True))
    prompts_called = client.call_count
    assert prompts_called > 0
    # Siblings must have completed their ladders (their calls happened) —
    # Task 4 turns those completed builds into cache entries.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_pool_parallel_build.py -q`
Expected: `test_ladders_run_concurrently...` FAILS (`max_inflight == 1`); the strict test may already pass — that's fine, it pins behavior Task 4 depends on.

- [ ] **Step 3: Implement**

Add to the constants block in `engine.py`:

```python
# Ladders for different columns are independent — run them on a bounded
# thread pool. vLLM continuous-batches concurrent requests on the server
# side; the client (openai/httpx) is thread-safe; embedder work is NOT
# (HF "Already borrowed") and therefore finishes before any thread spawns.
_POOL_BUILD_MAX_WORKERS = 4
```

Replace the `for prof in free_text_cols:` loop at the end of `_build_free_text_pools` with:

```python
        # Phase 1 — sequential: seed-example retrieval touches the embedder
        # (HF fast tokenizers are not thread-safe), so it fully completes
        # before any ladder thread spawns.
        jobs: list[tuple[ColumnProfile, list[str], int]] = []
        for prof in free_text_cols:
            seed_examples = self._column_seed_examples(
                prof, ctx, _DEFAULT_TOP_K, chunks_by_column.get(prof.name)
            )
            if not seed_examples:
                seed_examples = [
                    e[prof.name]
                    for e in exemplars
                    if e.get(prof.name) not in (None, "")
                ][:_DEFAULT_TOP_K] or list(prof.text_examples[:_DEFAULT_TOP_K])
            jobs.append((prof, seed_examples, self._pool_target(prof, ctx)))

        # Phase 2 — parallel: one bounded ladder per column. Collect EVERY
        # result before re-raising the first failure, so sibling columns'
        # completed builds are never discarded by one column's strict raise
        # (the 2026-07-24 16:35 E2E rebuilt all pools 3x for one column).
        if len(jobs) == 1:
            prof, seed_examples, pool_target = jobs[0]
            pools[prof.name] = self._infer_free_text_pool(
                prof, seed_examples, pool_target
            )
            return pools
        from concurrent.futures import ThreadPoolExecutor

        first_error: Exception | None = None
        with ThreadPoolExecutor(
            max_workers=min(_POOL_BUILD_MAX_WORKERS, len(jobs)),
            thread_name_prefix="sdfb-pool",
        ) as executor:
            futures = [
                (
                    prof,
                    executor.submit(
                        self._infer_free_text_pool, prof, seed_examples, tgt
                    ),
                )
                for prof, seed_examples, tgt in jobs
            ]
            for prof, future in futures:
                try:
                    pools[prof.name] = future.result()
                except Exception as e:  # noqa: BLE001 — re-raised below
                    if first_error is None:
                        first_error = e
        if first_error is not None:
            raise first_error
        return pools
```

(The method's existing `return pools` at the end is superseded by the returns above; remove the old loop entirely.)

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/ packages/sdfb-tests/tests/unit/engines/ -q`
Expected: PASS, including the two new tests.

- [ ] **Step 5: Full suite + lint, then commit**

Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`

```bash
git add packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py packages/sdfb-tests/tests/unit/rag/test_pool_parallel_build.py
git commit -m "perf(b1): run per-column pool ladders concurrently (bounded pool)

Seed-example retrieval (embedder; HF tokenizers not thread-safe) stays
sequential; the independent LLM ladders then run on a ThreadPoolExecutor
(<=4 workers) that vLLM continuous batching absorbs. All columns finish
before a strict failure re-raises, so completed sibling builds survive.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FzPHDr8vfd4if8P7AyUuKj"
```

---

### Task 4: Process-level pool cache across `setup()` retries

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (imports; module-level cache; `_build_free_text_pools`; `_infer_free_text_pool`)
- Test: `packages/sdfb-tests/tests/unit/rag/test_b1_pool_cache.py` (create)

**Interfaces:**
- Produces: `clear_free_text_pool_cache() -> None` (module-level, exported in `__all__`) — tests and maintenance only; the cache is deliberately process-lived (mirrors the vLLM `_SERVER_REFS` server-reuse precedent) and `teardown()` must NOT clear it.
- Cache key: `(ctx.reference_digest, ctx.model_uri, column_name, target)`; caching is disabled when `reference_digest` is empty (no stable identity).

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_b1_pool_cache.py`:

```python
"""Process-level free-text pool cache (2026-07-24 16:35 E2E fix).

A FreeTextEmptyYieldError out of DoFn.setup() makes Dataflow retry the
bundle: a FRESH DoFn/engine re-runs setup() in the same worker process.
Before this fix the retry rebuilt every column's pool from scratch
(~15-19 min each attempt). The cache — keyed (reference_digest, model_uri,
column, target) — hands completed pools to the retry, like the module-level
vLLM server reuse (_SERVER_REFS) already does for the server.
"""

from __future__ import annotations

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag import engine as b1_engine
from sdfb_core.engines.b1_rag.engine import (
    B1RagEngine,
    clear_free_text_pool_cache,
)
from sdfb_core.engines.base import FreeTextEmptyYieldError, GenerationContext
from sdfb_core.observability import parse_milestone

_COLS = ["col_a", "col_b"]


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_free_text_pool_cache()
    yield
    clear_free_text_pool_cache()


def _schema() -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.t"},
            "schema": [
                {"name": c, "type": "STRING", "mode": "REQUIRED"} for c in _COLS
            ],
        }
    )


def _rows() -> list[dict]:
    return [
        {c: f"{c} reference prose value number {i}" for c in _COLS}
        for i in range(60)
    ]


def _ctx(digest: str = "digest-1", **overrides) -> GenerationContext:
    defaults = dict(
        table_schema=_schema(),
        reference_rows=_rows(),
        reference_digest=digest,
        model_uri="gs://m/qwen3/v1",
        pipeline_run_id="run-cache-1",
        num_rows=40,
    )
    defaults.update(overrides)
    return GenerationContext(**defaults)


class _CountingClient:
    def __init__(self):
        self.call_count = 0
        self.prompts: list[str] = []

    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        self.call_count += 1
        self.prompts.append(prompt)
        col = next((c for c in _COLS if f"'{c}'" in prompt), "unknown")
        return [
            {"values": [f"{col}-gen-{choice}-{i}" for i in range(32)]}
            for choice in range(n)
        ]


def test_second_engine_same_digest_reuses_pools(caplog):
    c1 = _CountingClient()
    e1 = B1RagEngine()
    e1.setup(c1, _ctx())
    assert c1.call_count > 0
    c2 = _CountingClient()
    e2 = B1RagEngine()
    with caplog.at_level("INFO"):
        e2.setup(c2, _ctx())
    assert c2.call_count == 0, "cached pools must skip all LLM calls"
    assert e2._free_text_pools == e1._free_text_pools
    names = [
        m["name"]
        for m in (parse_milestone(r.getMessage()) for r in caplog.records)
        if m
    ]
    assert "freetext_pool_cache_hit" in names


def test_different_digest_rebuilds():
    e1 = B1RagEngine()
    e1.setup(_CountingClient(), _ctx("digest-1"))
    c2 = _CountingClient()
    e2 = B1RagEngine()
    e2.setup(c2, _ctx("digest-2"))
    assert c2.call_count > 0


def test_empty_digest_disables_cache():
    e1 = B1RagEngine()
    e1.setup(_CountingClient(), _ctx(""))
    c2 = _CountingClient()
    e2 = B1RagEngine()
    e2.setup(c2, _ctx(""))
    assert c2.call_count > 0


class _ColBParrotsClient(_CountingClient):
    def generate_json(self, prompt, json_schema, **kw):
        if "'col_b'" in prompt:
            self.call_count += 1
            self.prompts.append(prompt)
            return [{"values": ["col_b reference prose value number 0"]}]
        return super().generate_json(prompt, json_schema, **kw)


def test_retry_after_strict_failure_skips_completed_sibling():
    c1 = _ColBParrotsClient()
    e1 = B1RagEngine()
    with pytest.raises(FreeTextEmptyYieldError):
        e1.setup(c1, _ctx(strict_freetext=True))
    # Retry (fresh engine, same process): col_a must come from cache.
    c2 = _ColBParrotsClient()
    e2 = B1RagEngine()
    with pytest.raises(FreeTextEmptyYieldError):
        e2.setup(c2, _ctx(strict_freetext=True))
    assert all("'col_a'" not in p for p in c2.prompts), (
        "the retry must not rebuild the completed sibling pool"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_b1_pool_cache.py -q`
Expected: FAIL — `clear_free_text_pool_cache` does not exist.

- [ ] **Step 3: Implement**

In `engine.py`, add `import threading` to the stdlib imports. After the constants block add:

```python
# Process-level pool cache (2026-07-24 16:35 E2E): a strict failure in ONE
# column's ladder crashes DoFn.setup() and Dataflow retries the bundle with
# a FRESH engine in the SAME worker process — without this cache the retry
# rebuilt every sibling column's pool from scratch (~15-19 min/attempt).
# Keyed on (reference_digest, model_uri, column, target); disabled when the
# digest is empty. Deliberately process-lived (teardown() must not clear
# it) — the module-level vLLM server reuse (_SERVER_REFS) is the precedent.
_POOL_CACHE: dict[tuple[str, str, str, int], tuple[str, ...]] = {}
_POOL_CACHE_LOCK = threading.Lock()


def clear_free_text_pool_cache() -> None:
    """Drop all cached pools (tests / maintenance only)."""
    with _POOL_CACHE_LOCK:
        _POOL_CACHE.clear()
```

In `B1RagEngine`, add a key helper:

```python
def _pool_cache_key(
    self, ctx: GenerationContext, column: str, target: int
) -> tuple[str, str, str, int] | None:
    if not ctx.reference_digest:
        return None
    return (ctx.reference_digest, ctx.model_uri, column, target)
```

In `_build_free_text_pools`, right after each job tuple is computed in Phase 1 (inside the `for prof in free_text_cols:` loop, after `self._pool_target(...)` is known), consult the cache instead of appending a job:

```python
            pool_target = self._pool_target(prof, ctx)
            key = self._pool_cache_key(ctx, prof.name, pool_target)
            if key is not None:
                with _POOL_CACHE_LOCK:
                    cached = _POOL_CACHE.get(key)
                if cached is not None:
                    pools[prof.name] = list(cached)
                    log_milestone(
                        "freetext_pool_cache_hit",
                        column=prof.name,
                        pool_size=len(cached),
                    )
                    continue
            jobs.append((prof, seed_examples, pool_target))
```

(Adjust the Phase-1 loop from Task 3 accordingly: `jobs.append(...)` becomes the fall-through above. When every column hits the cache, `jobs` is empty and the method returns `pools` without spawning threads — ensure the `if len(jobs) == 1:` branch is preceded by `if not jobs: return pools`.)

At the very end of `_infer_free_text_pool`, replace the final `return` with:

```python
        final = list(seen.keys())[: max(target, len(prof.text_examples))]
        if final and self._ctx is not None:
            key = self._pool_cache_key(self._ctx, prof.name, target)
            if key is not None:
                with _POOL_CACHE_LOCK:
                    _POOL_CACHE[key] = tuple(final)
        return final
```

Extend `__all__`:

```python
__all__ = ["B1RagEngine", "clear_free_text_pool_cache"]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/ packages/sdfb-tests/tests/unit/engines/ -q`
Expected: PASS. If any pre-existing test now fails because two tests share a `reference_digest` and unexpectedly hit the cache, add the `_fresh_cache` autouse fixture pattern to that module (most existing tests use the default empty digest, which disables caching).

- [ ] **Step 5: Full suite + lint, then commit**

Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`

```bash
git add packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py packages/sdfb-tests/tests/unit/rag/test_b1_pool_cache.py
git commit -m "fix(b1): process-level pool cache survives DoFn.setup() bundle retries

Completed per-column pools cache on (reference_digest, model_uri, column,
target) and are handed to any re-entrant setup() in the same worker
process, so one column's strict failure no longer discards its siblings'
15-19 min of ladder work (2026-07-24 16:35 E2E: 3 full rebuilds).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FzPHDr8vfd4if8P7AyUuKj"
```

---

### Task 5: `DoFn.setup()` retry observability

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py`
- Test: `packages/sdfb-tests/tests/unit/dofns/test_setup_retry_observability.py` (create)

**Interfaces:**
- Produces: milestone `dofn_setup_retry` (fields: `engine`, `attempt`) at WARNING, emitted only when a prior `setup()` for the same `(engine, run_id)` failed in this process; Beam counter `generation/setup_retries`. Parallel clean instances (Beam's normal N bundle processors) emit nothing.
- Produces: module-level `_SETUP_FAILURES: dict[str, int]` + `_reset_setup_failures()` test hook in `generate.py`.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/dofns/test_setup_retry_observability.py`:

```python
"""dofn_setup_retry milestone (2026-07-24 16:35 E2E fix).

That run's setup() crashed twice and Dataflow silently retried — the job
landed PASSED with dlq_count=0 and ZERO trace of ~35 min of retry cost.
A retry after an in-process setup failure now logs a WARNING milestone
and bumps a Beam counter (committed only by the surviving bundle, which
is exactly the invisible case).
"""

from __future__ import annotations

import pytest
from sdfb_beam.dofns import generate as generate_mod
from sdfb_beam.dofns.generate import GenerateRecordsDoFn
from sdfb_beam.handlers.fake_client import FakeModelClient
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import register_engine
from sdfb_core.engines.base import GenerationContext, GenerationEngine
from sdfb_core.observability import parse_milestone


class BoomOnceEngine(GenerationEngine):
    """Fails setup() a class-controlled number of times, then succeeds."""

    name = "boom_once"
    fail_remaining = 0  # tests set this

    def setup(self, model_client, ctx):
        if BoomOnceEngine.fail_remaining > 0:
            BoomOnceEngine.fail_remaining -= 1
            raise RuntimeError("synthetic setup failure")

    def generate_batch(self, n, cfg):
        return iter(())

    def teardown(self):
        pass


register_engine("boom_once", BoomOnceEngine)


@pytest.fixture(autouse=True)
def _fresh_state():
    generate_mod._reset_setup_failures()
    BoomOnceEngine.fail_remaining = 0
    yield
    generate_mod._reset_setup_failures()


def _ctx() -> GenerationContext:
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.t"},
            "schema": [{"name": "c", "type": "STRING", "mode": "REQUIRED"}],
        }
    )
    return GenerationContext(table_schema=schema, pipeline_run_id="run-retry-1")


def _dofn() -> GenerateRecordsDoFn:
    return GenerateRecordsDoFn(
        engine_name="boom_once",
        model_client=FakeModelClient(reference_pool=[{"c": "x"}]),
        ctx=_ctx(),
    )


def _milestones(caplog) -> list[dict]:
    return [
        m for m in (parse_milestone(r.getMessage()) for r in caplog.records) if m
    ]


def test_retry_after_failure_emits_milestone(caplog):
    BoomOnceEngine.fail_remaining = 1
    with pytest.raises(RuntimeError):
        _dofn().setup()
    with caplog.at_level("WARNING"):
        _dofn().setup()  # fresh DoFn, same process — the Dataflow retry shape
    retries = [m for m in _milestones(caplog) if m["name"] == "dofn_setup_retry"]
    assert retries and retries[0]["attempt"] == "2"
    assert retries[0]["engine"] == "boom_once"


def test_parallel_clean_setups_emit_nothing(caplog):
    with caplog.at_level("WARNING"):
        _dofn().setup()
        _dofn().setup()  # second clean instance = normal Beam parallelism
    assert not [
        m for m in _milestones(caplog) if m["name"] == "dofn_setup_retry"
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/test_setup_retry_observability.py -q`
Expected: FAIL — `_reset_setup_failures` does not exist.

- [ ] **Step 3: Implement**

In `generate.py`, extend the imports:

```python
import logging
import threading
import time
```

After `EMBEDDER_LOCAL_DIR = ...` add:

```python
# Per-process ledger of failed setup() attempts, keyed "engine:run_id".
# Dataflow retries a failed bundle with a FRESH DoFn in the SAME process;
# only a setup() entered AFTER a recorded failure is a retry — Beam's
# normal N parallel bundle processors all enter cleanly and never match.
# (2026-07-24 16:35 E2E: two silent setup() crashes cost ~35 min with zero
# trace in validation_runs.)
_SETUP_FAILURES: dict[str, int] = {}
_SETUP_FAILURES_LOCK = threading.Lock()


def _reset_setup_failures() -> None:
    """Test hook — production state is deliberately process-lived."""
    with _SETUP_FAILURES_LOCK:
        _SETUP_FAILURES.clear()
```

In `GenerateRecordsDoFn.setup()`, wrap the existing body: after the `t0 = time.monotonic()` / `log_milestone("dofn_setup_start", ...)` lines insert:

```python
        failure_key = f"{self.engine_name}:{self.ctx.pipeline_run_id}"
        with _SETUP_FAILURES_LOCK:
            prior_failures = _SETUP_FAILURES.get(failure_key, 0)
        if prior_failures:
            log_milestone(
                "dofn_setup_retry",
                level=logging.WARNING,
                engine=self.engine_name,
                attempt=prior_failures + 1,
            )
            # Committed only when THIS (surviving) bundle commits — i.e.
            # exactly the silent-retry-then-PASS case worker logs alone
            # could not surface into job metrics.
            Metrics.counter("generation", "setup_retries").inc()
        try:
            self._setup_inner()
        except Exception:
            with _SETUP_FAILURES_LOCK:
                _SETUP_FAILURES[failure_key] = (
                    _SETUP_FAILURES.get(failure_key, 0) + 1
                )
            raise
        log_milestone(
            "dofn_setup_done",
            engine=self.engine_name,
            seconds=round(time.monotonic() - t0, 1),
        )
```

and move everything between the current `ctx = self.ctx` line and the current `log_milestone("dofn_setup_done", ...)` (exclusive) into a new private method:

```python
    def _setup_inner(self) -> None:
        # (body unchanged: embedder warm-pull; chunk-store attach; engine
        #  construction + engine.setup; _column_types/_column_max_lengths)
```

`_setup_inner` uses `self.ctx` directly (the existing code already writes back `self.ctx = ctx` after each rewrite, so no parameter is needed). The existing `dofn_setup_done` milestone moves out of `_setup_inner` into the wrapper as shown.

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/ -q`
Expected: PASS (including the existing `test_generate_client_lifecycle.py`).

- [ ] **Step 5: Full suite + lint, then commit**

Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`

```bash
git add packages/sdfb-beam/src/sdfb_beam/dofns/generate.py packages/sdfb-tests/tests/unit/dofns/test_setup_retry_observability.py
git commit -m "feat(observability): dofn_setup_retry milestone + setup_retries counter

A DoFn.setup() entered after an in-process setup failure for the same
(engine, run_id) logs a WARNING milestone with the attempt number and
bumps generation/setup_retries — making silently-retried setups visible
in worker logs AND Dataflow job metrics (the 2026-07-24 16:35 E2E hid
~35 min of retries behind a clean PASSED row).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FzPHDr8vfd4if8P7AyUuKj"
```

---

### Task 6: ADR 0018 — decision record + LMCache/turbovec compatibility notes

**Files:**
- Create: `docs/adr/0018-parallel-batched-freetext-pools.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0018-parallel-batched-freetext-pools.md`:

```markdown
# 0018 — Batched, parallel, cached free-text pool builds (B.1)

Date: 2026-07-25
Status: accepted

## Context

WS2 §4b.2 scaled B.1's free-text pool target from 32 to
`min(num_rows, distinct, 512)`, which multiplied sequential vLLM calls per
column by up to 32x (20-48 s/call on T4/float16). The 2026-07-24 12:46 E2E
spent 1552 s (49% of wall clock) building 3 pools; the 16:35 run tripled
its setup because one echo-saturated column (`CHG_MESS_CARR_ID`) raised
`FreeTextEmptyYieldError` out of `DoFn.setup()` twice, and Dataflow's
silent bundle retries rebuilt every sibling pool from scratch — invisible
in `validation_runs`.

## Decision

1. **Batched sampling**: each pool HTTP round trip requests
   `n=_POOL_PARALLEL_CHOICES=4` independent array-completions (vLLM
   parallel sampling, unseeded) and de-dupes across choices; the attempt
   budget scales down by the same factor. Requests for one column keep a
   byte-identical prompt across attempts (only sampling params vary).
2. **Column-parallel ladders**: independent per-column ladders run on a
   `ThreadPoolExecutor` (≤4 workers) — vLLM continuous batching absorbs
   the concurrency. Embedder work (seed-example retrieval) completes
   sequentially first: HF fast tokenizers are not thread-safe.
3. **Shape fallback parity + top-up**: B.1 adopts B.2's relaxed-template
   fallback for copy-saturated builds and additionally tops up undersized
   pools with verified-novel template values, so identifier-ish columns
   can no longer fail `setup()` or land 31-distinct pools.
4. **Process-level pool cache**: completed pools cache on
   `(reference_digest, model_uri, column, target)` and survive bundle
   retries within a worker process (precedent: `_SERVER_REFS` vLLM server
   reuse). All columns complete before a strict failure re-raises.
5. **Retry observability**: re-entrant `setup()` after an in-process
   failure emits `dofn_setup_retry` (WARNING) and increments the
   `generation/setup_retries` Beam counter.

## Consequences

- Worst-case pool build for 3 columns drops from ~26 min sequential to
  roughly the slowest single column's scaled ladder (~3-7 min expected on
  T4); a retried setup pays only the failed column.
- `n=4` with up to 4 concurrent ladders means up to ~16 in-flight
  sequences; vLLM queues what the T4's KV cache cannot admit — graceful
  degradation, no config change needed.
- New milestones (`freetext_pool_shape_topup`, `freetext_pool_cache_hit`,
  `dofn_setup_retry`) are additive; existing probe-mined names unchanged.

## Compatibility with planned explorations

- **LMCache** (KV-cache layer for vLLM): a server-side deployment change
  only — enable via vLLM connector flags/env in `docker/Dockerfile` and
  `VLLMModelClient`'s server spawn args. No engine change: the pool
  builder already keeps prompts prefix-stable per column, which is the
  property prefix/KV reuse (vLLM APC today, LMCache later) rewards. The
  `ModelClient` Protocol stays transport-agnostic.
- **`turbovec`** (TurboQuant ANN index, Rust/Python): the swap point is the
  `sdfb_core.rag.index.build_index()` / `ExactIPIndex` seam
  (`search(query, k)` / `release()`), which nothing outside
  `rag/index.py` bypasses. Caveats for a future adapter: turbovec is
  quantized/approximate — the M1 acceptance criterion is *deterministic
  exact* top-k at ≤10k vectors, so FAISS `IndexFlatIP` stays the default;
  gate any turbovec backend behind explicit config and revisit at the
  >50k-vector scale where ANN pays. Vectors must be float32 numpy 2-D
  (our seam's `list[list[float]]` converts losslessly).
```

- [ ] **Step 2: Update the ADR index if one exists**

Run: `grep -l "0017" docs/adr/README.md` — if it lists ADRs, append a `0018` line in the same format.

- [ ] **Step 3: Commit**````

```bash
git add docs/adr/0018-parallel-batched-freetext-pools.md docs/adr/README.md
git commit -m "docs(adr): 0018 — batched/parallel/cached B.1 pool builds + LMCache/turbovec compat

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FzPHDr8vfd4if8P7AyUuKj"
```

---

## Self-review notes

- **Spec coverage**: fix #1 (batching) → Tasks 1+3; fix #2 (retry resilience) → Tasks 2+3+4; fix #3 (observability) → Task 5; fix #4 (ID columns off the LLM's failure path) → Task 2; RAG-phase performance → chunk-store reuse already landed (29 s vs 283 s, verified in the 16:35 run) so no embed/index change is warranted — documented in ADR; LMCache/turbovec compat → Task 6.
- **Type consistency**: `_shape_fallback_pool(prof, count, exclude)` (Task 2) is called with `(prof, target, set())` and `(prof, target - len(pool), set(pool))`; `_pool_cache_key(ctx, column, target)` (Task 4) matches both call sites; `_infer_free_text_pool(prof, seed_examples, target)` signature never changes.
- **Known interaction**: Task 4's Step 3 edits the Phase-1 loop introduced by Task 3 — execute in order.
