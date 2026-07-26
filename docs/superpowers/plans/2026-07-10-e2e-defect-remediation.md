# E2E Defect Remediation (2026-07-10 reports) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three root causes behind the 2026-07-09/10 live runs — (1) `VLLMModelClient.setup()` is never called so every LLM call fails and both engines silently emit memorized reference data, (2) `run_id`-derived seeds replay identical output across Airflow retriggers of the same logical date, (3) the BLOCKER gate is blind to PK duplicates, engine failures, and LLM fallback — and make the next M4 run verifiable.

**Architecture:** The `ModelClient` lifecycle becomes the DoFn's responsibility (duck-typed `setup()`/`teardown()`, fail-fast). A `strict_freetext` flag on `GenerationContext` converts silent exemplar fallback into a raised error for real-vLLM runs. `EnforceUniqueness` gains a third dedup stage for declared PK columns. The Composer DAG salts `run_id` per trigger and exposes `seed`/`pk_cols` params.

**Tech Stack:** Python 3.11, Apache Beam (mocked on laptop), pydantic, pytest. No GPU/GCP needed for any task except the final M4 validation matrix (docs-only here).

## Global Constraints

- No Vertex AI, no HuggingFace Hub at runtime, no external LLM APIs (CLAUDE.md hard constraints).
- Import direction is strict: `sdfb-beam` → `sdfb-core`, never the reverse. No `apache_beam` import inside `sdfb-core`.
- Laptop test command (memory quirk): `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q` — expect all green (324+ tests).
- Lint: `uv run ruff check .` must pass before each commit.
- Do not catch and silently drop errors; failures route to DLQ or crash loudly.
- Branch: `e2e-hardening` (already checked out).

## Root-cause evidence (verified in code, 2026-07-10)

| # | Root cause | Evidence |
|---|---|---|
| RC1 | **Nobody calls `VLLMModelClient.setup()`.** `GenerateRecordsDoFn.setup()` (`packages/sdfb-beam/src/sdfb_beam/dofns/generate.py:66-109`) builds the engine and calls `engine.setup(self.model_client, ctx)` but never `self.model_client.setup()`. `VLLMModelClient.generate_json()` (`vllm_client.py:273-278`) raises `RuntimeError` when `_client is None`. Both engines catch broad `Exception` → `freetext_llm_fallback` → exemplar pools = 100% memorization. Laptop tests never caught it: `FakeModelClient` needs no setup and `MLXModelClient` lazily self-initializes (`mlx_client.py:102-103`). | Every live run logged `SDFB_MILESTONE name=freetext_llm_fallback … error=RuntimeError`; no `model_pull_start`/`vllm_spawn`/`vllm_ready` milestone in any job. |
| RC2 | **Seed replay across retriggers.** `seed=None` → `derive_batch_seed(run_id, batch_id)` (`sdfb_core/seeding.py`), documented as "re-running the same run_id reproduces the exact output". Composer passes `"run_id": "{{ dag_run.run_id }}"` — retriggering the same logical date reuses `scheduled__2026-07-08T00:00:00+00:00`. Fallback pools are also deterministic (profiler exemplars, deterministic order). Same run_id + same reference sample + no LLM contribution ⇒ byte-identical output, regardless of model. | Three b1_rag runs (incl. gemma vs qwen) produced identical rows; all shared `run_id=scheduled__2026-07-08T00:00:00+00:00`. |
| RC3 | **Gate blind spots.** `PipelineConfig` has no `pk_columns`; `EnforceUniqueness` handles only `row.duplicate` + `identity.unique`; `identity_cols` defaulted to `""`; `engine_failure` is not in `BLOCKER_RULE_IDS`; nothing gates fallback. | All 5 runs PASSED with `observed_blocker_ratio=0.0` despite copy_ratio=1.0 on identifiers. |

---

### Task 1: ModelClient lifecycle in `GenerateRecordsDoFn` (RC1 — the root fix)

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py`
- Test: `packages/sdfb-tests/tests/unit/dofns/test_generate_client_lifecycle.py` (new)

**Interfaces:**
- Consumes: duck-typed optional `setup()` / `teardown()` on the model client (present on `VLLMModelClient` and `MLXModelClient`; absent on `FakeModelClient`).
- Produces: `GenerateRecordsDoFn.setup()` guarantees the client is ready before `engine.setup()` runs (B.1 calls `generate_json` inside engine setup). New milestones `model_client_setup_start` / `model_client_setup_done`. Setup failures propagate — the worker crashes instead of degrading.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/dofns/test_generate_client_lifecycle.py`:

```python
"""GenerateRecordsDoFn must drive the ModelClient lifecycle.

E2E defect (2026-07-10 reports, all 5 live runs): nothing ever called
`VLLMModelClient.setup()`, so the first `generate_json` raised RuntimeError
inside the engines' broad except → `freetext_llm_fallback` → 100%
memorized reference data. The DoFn owns the worker lifecycle, so it must
call the client's optional `setup()` BEFORE `engine.setup()` (B.1 calls the
LLM during engine setup) and `teardown()` after the engine's teardown.
`FakeModelClient` has no lifecycle methods and must keep working unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

from sdfb_beam.dofns import generate as generate_mod
from sdfb_beam.dofns.generate import GenerateRecordsDoFn


class _Record:
    def __init__(self, i: int) -> None:
        self._i = i

    def model_dump(self, mode="python"):
        return {"i": self._i}


class _StubEngine:
    """Records lifecycle order on the class-level `events` list."""

    events: list[str] = []

    def setup(self, model_client, ctx):
        _StubEngine.events.append("engine_setup")
        # B.1 calls the LLM inside setup() — the client MUST be ready here.
        model_client.generate_json(prompt="p", json_schema={})

    def generate_batch(self, n, cfg):
        for i in range(n):
            yield _Record(i)

    def teardown(self):
        _StubEngine.events.append("engine_teardown")


class _SpyClient:
    """Lifecycle-bearing client (the VLLMModelClient shape)."""

    def __init__(self) -> None:
        self.ready = False

    def setup(self):
        _StubEngine.events.append("client_setup")
        self.ready = True

    def teardown(self):
        _StubEngine.events.append("client_teardown")
        self.ready = False

    def generate_json(self, prompt, json_schema, **kw):
        if not self.ready:
            raise RuntimeError("generate_json before setup()")
        return [{}]


class _NoLifecycleClient:
    """FakeModelClient shape — no setup()/teardown()."""

    def generate_json(self, prompt, json_schema, **kw):
        return [{}]


def _ctx():
    # Structural stand-in; the DoFn only touches these attributes on the
    # non-gs:// path. Keeps the test free of TableSchema construction.
    return SimpleNamespace(
        embedder_uri="",
        table_schema=SimpleNamespace(columns=[]),
        identity_columns=[],
        pipeline_run_id="lifecycle-test",
    )


def _dofn(client, monkeypatch):
    monkeypatch.setattr(generate_mod, "get_engine", lambda name: _StubEngine)
    return GenerateRecordsDoFn(
        engine_name="stub", model_client=client, ctx=_ctx(), seed=7
    )


def test_client_setup_runs_before_engine_setup(monkeypatch):
    _StubEngine.events = []
    dofn = _dofn(_SpyClient(), monkeypatch)
    dofn.setup()  # must NOT raise "generate_json before setup()"
    assert _StubEngine.events[:2] == ["client_setup", "engine_setup"]


def test_client_teardown_runs_after_engine_teardown(monkeypatch):
    _StubEngine.events = []
    dofn = _dofn(_SpyClient(), monkeypatch)
    dofn.setup()
    dofn.teardown()
    assert _StubEngine.events == [
        "client_setup",
        "engine_setup",
        "engine_teardown",
        "client_teardown",
    ]


def test_client_without_lifecycle_still_works(monkeypatch):
    _StubEngine.events = []
    dofn = _dofn(_NoLifecycleClient(), monkeypatch)
    dofn.setup()
    rows = list(dofn.process({"n": 2, "batch_id": 0}))
    dofn.teardown()
    assert len(rows) == 2
    assert _StubEngine.events == ["engine_setup", "engine_teardown"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/test_generate_client_lifecycle.py -v`
Expected: `test_client_setup_runs_before_engine_setup` FAILS — `_StubEngine.setup` raises `RuntimeError: generate_json before setup()` (nothing calls `client.setup()`). The `_NoLifecycleClient` test passes already.

- [ ] **Step 3: Implement the lifecycle calls**

In `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py`, inside `setup()`, insert **after** the embedder warm-pull block (after line 87's `self.ctx = ctx`) and **before** `engine_class = get_engine(self.engine_name)`:

```python
        # The vLLM client owns a server subprocess that must be started ONCE
        # per worker — engines only see the narrow ModelClient Protocol
        # (generate_json), so the lifecycle is the DoFn's job. Duck-typed:
        # FakeModelClient has no lifecycle; MLX self-initializes lazily.
        # Failures propagate — a worker that cannot start its LLM must crash
        # the job, not degrade into copying reference exemplars (E2E
        # 2026-07-10: every live run fell back because nobody called setup()).
        client_setup = getattr(self.model_client, "setup", None)
        if callable(client_setup):
            log_milestone(
                "model_client_setup_start",
                client=type(self.model_client).__name__,
            )
            t_client = time.monotonic()
            client_setup()
            log_milestone(
                "model_client_setup_done",
                seconds=round(time.monotonic() - t_client, 1),
            )
```

Replace `teardown()` (lines 169-174) with:

```python
    def teardown(self):
        try:
            if self._engine is not None:
                try:
                    self._engine.teardown()
                finally:
                    self._engine = None
        finally:
            client_teardown = getattr(self.model_client, "teardown", None)
            if callable(client_teardown):
                client_teardown()
```

- [ ] **Step 4: Run the new tests and the full suite**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/test_generate_client_lifecycle.py -v`
Expected: 3 PASS.
Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`
Expected: all green, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-beam/src/sdfb_beam/dofns/generate.py packages/sdfb-tests/tests/unit/dofns/test_generate_client_lifecycle.py
git commit -m "fix: GenerateRecordsDoFn drives ModelClient setup/teardown lifecycle

Root cause of 100% freetext memorization in all 2026-07-09/10 live runs:
VLLMModelClient.setup() was never called, so every generate_json raised
RuntimeError and both engines silently fell back to reference exemplars."
```

---

### Task 2: Strict free-text mode — no silent fallback on real-LLM runs (RC1 hardening, RC3)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py` (GenerationContext, ~line 97)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py:305-315`
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/freetext.py` (FreeTextHook `__init__` + `_generate_pool`)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py:85`
- Modify: `packages/sdfb-core/src/sdfb_core/validation/summary.py:23-31`
- Modify: `config/thresholds.yml`
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (PipelineConfig + ctx wiring)
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (~line 245 `PipelineConfig(...)`)
- Test: `packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py` (extend)

**Interfaces:**
- Consumes: `GenerationContext` (frozen pydantic model), `FreeTextHook(client)` constructor, `BLOCKER_RULE_IDS` frozenset.
- Produces: `GenerationContext.strict_freetext: bool = False`; `FreeTextHook(client, strict=...)`; `PipelineConfig.strict_freetext: bool = False`; CLI sets it to `True` when `client_type == "vllm"`. `"engine_failure"` becomes a BLOCKER rule id.

- [ ] **Step 1: Write the failing tests**

Append to `packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py` (reuses that file's `_BoomClient`, `wide_ctx`, `free_text_ctx` fixtures):

```python
# ---------------------------------------------------------------------------
# strict_freetext=True — real-vLLM runs must fail loudly, never fall back.
# ---------------------------------------------------------------------------


def test_b2_strict_reraises(wide_ctx):
    profiles = profile_table(wide_ctx.table_schema, wide_ctx.reference_rows)
    hook = FreeTextHook(_BoomClient(), strict=True)
    rng = np.random.default_rng(7)
    with pytest.raises(RuntimeError, match="boom"):
        hook.sample(profiles["summary"], 5, GenerationConfig(seed=7), rng)


def test_b1_strict_reraises(free_text_ctx):
    strict_ctx = free_text_ctx.model_copy(update={"strict_freetext": True})
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with pytest.raises(RuntimeError, match="boom"):
        engine.setup(_BoomClient(), strict_ctx)
```

Also add a summary test to the existing summary test module (`packages/sdfb-tests/tests/unit/validation/` — locate the file testing `build_run_summary`; if named differently, add to it):

```python
def test_engine_failure_counts_as_blocker():
    from sdfb_core.validation.summary import BLOCKER_RULE_IDS

    assert "engine_failure" in BLOCKER_RULE_IDS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py -v`
Expected: `test_b2_strict_reraises` FAILS with `TypeError: __init__() got an unexpected keyword argument 'strict'`; `test_b1_strict_reraises` FAILS (`strict_freetext` unknown field → pydantic `ValidationError`, or no re-raise). Summary test FAILS on the missing rule id.

- [ ] **Step 3: Implement**

`packages/sdfb-core/src/sdfb_core/engines/base.py` — add to `GenerationContext` after `identity_columns` (line 97):

```python
    # When True (real-LLM runs), a failed free-text LLM call re-raises
    # instead of silently falling back to reference exemplars. The 2026-07-10
    # E2E runs shipped 100% memorized identifiers because the fallback was
    # only a WARNING. Fake/mock clients keep the lenient default.
    strict_freetext: bool = False
```

`packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` — in `_infer_free_text_pool`'s except (line 305):

```python
        except Exception as e:
            if self._ctx is not None and self._ctx.strict_freetext:
                raise
            # Per-call generation failure: exemplar fallback is allowed, but
            # NEVER silently — a run where the LLM contributed nothing must be
            # visible in worker logs (E2E report §4.2: 100 % memorization).
            log_milestone(
                "freetext_llm_fallback",
                level=logging.WARNING,
                column=prof.name,
                error=type(e).__name__,
            )
            pool = []
```

`packages/sdfb-core/src/sdfb_core/engines/b2_library/freetext.py` — `FreeTextHook.__init__` gains `strict`:

```python
    def __init__(
        self,
        client: ModelClient,
        *,
        pool_size: int = _DEFAULT_POOL_SIZE,
        strict: bool = False,
    ) -> None:
```

(keep existing params; add `self._strict = strict`), and in `_generate_pool`'s except (line 147):

```python
        except Exception as e:
            if self._strict:
                raise
            # Per-call generation failure: exemplar fallback is allowed, but
            # NEVER silently — a run where the LLM contributed nothing must be
            # visible in worker logs (E2E report §4.2: 100 % memorization).
            log_milestone(
                "freetext_llm_fallback",
                level=logging.WARNING,
                column=profile.name,
                error=type(e).__name__,
            )
            return exemplars
```

`packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py:85`:

```python
        self._freetext_hook = FreeTextHook(
            model_client, strict=ctx.strict_freetext
        )
```

`packages/sdfb-core/src/sdfb_core/validation/summary.py:23`:

```python
BLOCKER_RULE_IDS = frozenset(
    {
        "schema.types",
        "null.required",
        "pk.duplicate",
        "row.duplicate",
        "identity.unique",
        # An engine crash (incl. strict_freetext re-raise reaching the DoFn)
        # loses the whole batch — that must count toward the gate, not PASS
        # with fewer rows.
        "engine_failure",
    }
)
```

`config/thresholds.yml` — add under `rules:`:

```yaml
  engine_failure:
    dimension: reliability
    severity: BLOCKER
    threshold: 0     # any engine-internal crash counts toward the gate
```

`packages/sdfb-beam/src/sdfb_beam/pipeline.py` — add to `PipelineConfig` after `identity_columns` (line 63):

```python
    # Real-LLM runs re-raise on free-text LLM failure instead of silently
    # copying exemplars. Set from client_type at the CLI boundary.
    strict_freetext: bool = False
```

and thread it into the ctx (line 102 block):

```python
    ctx = GenerationContext(
        table_schema=config.table_schema,
        reference_rows=reference_rows,
        reference_digest=digest,
        pipeline_run_id=config.run_id,
        model_uri=config.model_uri,
        embedder_uri=config.embedder_uri,
        identity_columns=list(config.identity_columns),
        strict_freetext=config.strict_freetext,
    )
```

`packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` — in the `PipelineConfig(...)` construction (~line 245), add:

```python
        strict_freetext=args.client_type == "vllm",
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py packages/sdfb-tests/tests/unit/validation -v`
Expected: new tests PASS; existing lenient-fallback tests still PASS (default `strict_freetext=False`).
Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-core config/thresholds.yml packages/sdfb-beam packages/sdfb-tests
git commit -m "feat: strict_freetext fails loudly on LLM errors for vllm runs; engine_failure gates"
```

---

### Task 3: `pk.duplicate` enforcement (RC3)

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/uniqueness.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (PipelineConfig + wiring, lines 63/92-99/157-159)
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (new `--pk_cols` arg + config)
- Modify: `composer/synthetic_beam_bigquery.py` (new `pk_cols` Param + parameters passthrough)
- Test: `packages/sdfb-tests/tests/unit/dofns/test_uniqueness.py` (extend — locate the existing `EnforceUniqueness` test module; if named differently, extend that one)

**Interfaces:**
- Consumes: `EnforceUniqueness(identity_columns=...)`, `_FirstWins`, `_envelope`, `RULE_*` constants.
- Produces: `RULE_PK_DUPLICATE = "pk.duplicate"`; `EnforceUniqueness(identity_columns=None, pk_columns=None)`; `PipelineConfig.pk_columns: tuple[str, ...] = ()`; CLI `--pk_cols` (comma-separated, default `""`); DAG param `pk_cols`.

- [ ] **Step 1: Write the failing test**

Add to the `EnforceUniqueness` test module (uses the same `TestPipeline`/`assert_that` style as its existing tests):

```python
def test_pk_duplicates_divert_to_dlq():
    """Rows sharing a declared PK tuple: first wins, rest → DLQ with
    rule_id=pk.duplicate. The 2026-07-09 run landed 722/1000 PK duplicates
    with a PASSED gate because no stage ever emitted this rule."""
    import apache_beam as beam
    from apache_beam.testing.test_pipeline import TestPipeline
    from apache_beam.testing.util import assert_that, equal_to

    from sdfb_beam.dofns.uniqueness import EnforceUniqueness

    rows = [
        {"pk_a": "K1", "pk_b": 1, "val": "x"},
        {"pk_a": "K1", "pk_b": 1, "val": "y"},  # same PK, different row
        {"pk_a": "K2", "pk_b": 2, "val": "z"},
    ]
    with TestPipeline() as p:
        out = (
            p
            | beam.Create(rows)
            | EnforceUniqueness(pk_columns=["pk_a", "pk_b"])
        )
        assert_that(
            out["unique"] | beam.Map(lambda r: (r["pk_a"], r["pk_b"])),
            equal_to([("K1", 1), ("K2", 2)]),
            label="unique_pks",
        )
        assert_that(
            out["duplicates"] | beam.Map(lambda d: d["rule_id"]),
            equal_to(["pk.duplicate"]),
            label="dlq_rule",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/ -k pk_duplicates -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'pk_columns'`.

- [ ] **Step 3: Implement the PK stage**

`packages/sdfb-beam/src/sdfb_beam/dofns/uniqueness.py` — add the constant after line 22:

```python
RULE_PK_DUPLICATE = "pk.duplicate"
```

Change `EnforceUniqueness.__init__` and `expand`:

```python
    def __init__(
        self,
        identity_columns: list[str] | None = None,
        pk_columns: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.identity_columns = list(identity_columns or [])
        self.pk_columns = list(pk_columns or [])
```

In `expand`, after the row-digest stage (`row_unique = by_row.unique`, line 84) and **before** the identity stage, insert:

```python
        if self.pk_columns:
            pk_cols = self.pk_columns
            by_pk = (
                row_unique
                | "KeyByPk"
                >> beam.Map(lambda r, c=pk_cols: (tuple(str(r.get(x)) for x in c), r))
                | "GroupByPk" >> beam.GroupByKey()
                | "FirstPkWins"
                >> beam.ParDo(_FirstWins(RULE_PK_DUPLICATE)).with_outputs(
                    "duplicates", main="unique"
                )
            )
            row_unique = by_pk.unique
            dup_streams.append(by_pk.duplicates)
```

(Stage order: row → pk → identity. Identity values are synthesized fresh per row, so PK dedup must run on the final rows regardless; keeping identity last preserves the existing digest-exclusion contract documented in the class docstring.)

`packages/sdfb-beam/src/sdfb_beam/pipeline.py`:

Add to `PipelineConfig` after `identity_columns`:

```python
    # Declared primary-key columns; duplicate PK tuples divert to the DLQ as
    # rule_id=pk.duplicate (BLOCKER). Empty = PK not declared (rule idle).
    pk_columns: tuple[str, ...] = ()
```

Extend the column-name validation block (lines 92-99) to cover both:

```python
    for label, cols in (
        ("identity_columns", config.identity_columns),
        ("pk_columns", config.pk_columns),
    ):
        if cols:
            valid_columns = {c.name for c in config.table_schema.columns}
            unknown = [c for c in cols if c not in valid_columns]
            if unknown:
                raise ValueError(
                    f"{label} not found on {config.table_schema.fqn}: "
                    f"{unknown}. Valid columns: {sorted(valid_columns)}"
                )
```

And the wiring (line 157):

```python
    uniq = batch_validated.main | "EnforceUniqueness" >> EnforceUniqueness(
        identity_columns=list(config.identity_columns),
        pk_columns=list(config.pk_columns),
    )
```

`packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` — next to `--identity_cols` (line 77):

```python
    p.add_argument("--pk_cols", default="",
                   help="Comma-separated declared primary-key columns; "
                        "duplicate PK tuples divert to the DLQ "
                        "(rule_id=pk.duplicate, BLOCKER). Empty disables.")
```

and in `PipelineConfig(...)`:

```python
        pk_columns=tuple(
            c.strip() for c in args.pk_cols.split(",") if c.strip()
        ),
```

`composer/synthetic_beam_bigquery.py` — add next to the `identity_cols` Param (line 138):

```python
    "pk_cols": Param(
        default="",
        type="string",
        description="Comma-separated declared primary-key columns. Duplicate "
                    "PK tuples divert to the DLQ (rule_id=pk.duplicate, "
                    "BLOCKER). Empty = PK undeclared, rule idle.",
    ),
```

and in the Flex-Template `parameters` dict (line ~255):

```python
                    "pk_cols": "{{ params.pk_cols }}",
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/ -v`
Expected: new test PASS, existing uniqueness tests PASS.
Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-beam packages/sdfb-tests composer/synthetic_beam_bigquery.py
git commit -m "feat: enforce pk.duplicate — PK dedup stage, PipelineConfig.pk_columns, --pk_cols, DAG param"
```

---

### Task 4: Per-trigger `run_id` salting + explicit `--seed` passthrough (RC2)

**Files:**
- Modify: `composer/synthetic_beam_bigquery.py` (run_id parameter + new `seed` Param)
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (new `--seed` arg)
- Test: none for the DAG template string (no Airflow on the laptop); CLI arg covered by full-suite smoke.

**Interfaces:**
- Produces: Dataflow `run_id` = `{{ dag_run.run_id }}-<8-hex uuid>` (unique per trigger, so `derive_batch_seed` never replays and `validation_runs` rows are uniquely attributable); optional `seed` DAG param → `--seed` CLI arg for deliberate reproduction.

- [ ] **Step 1: Salt run_id in the DAG**

In `composer/synthetic_beam_bigquery.py`, change the Flex-Template parameter (line ~253):

```python
                    # Salted per trigger: retriggering the same logical date
                    # reuses dag_run.run_id, and seed=None derives batch seeds
                    # from run_id — identical run_id replayed identical data
                    # (E2E 2026-07-10). The uuid suffix also makes each
                    # validation_runs row uniquely attributable to one job.
                    "run_id": "{{ dag_run.run_id }}-{{ macros.uuid.uuid4().hex[:8] }}",
```

- [ ] **Step 2: Add the seed Param + passthrough**

Add next to the other Params:

```python
    "seed": Param(
        default="",
        type="string",
        description="Explicit base RNG seed (integer) for deliberate "
                    "reproduction of a run. Empty (default) derives a fresh "
                    "seed per (run_id, batch) — recommended.",
    ),
```

and in the `parameters` dict:

```python
                    "seed": "{{ params.seed }}",
```

- [ ] **Step 3: Add `--seed` to the CLI**

In `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py`, next to `--similarity` (line 75):

```python
    p.add_argument("--seed", default="",
                   help="Explicit base RNG seed (int). Empty = derive per "
                        "(run_id, batch_id) — never replays across runs "
                        "because run_id is salted per trigger.")
```

and in `PipelineConfig(...)`:

```python
        seed=int(args.seed) if str(args.seed).strip() else None,
```

- [ ] **Step 4: Verify**

Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`
Expected: all green (the CLI module is imported by existing tests; a syntax/arg error would surface).
Also sanity-parse the DAG file: `uv run --no-sync python3 -c "import ast; ast.parse(open('composer/synthetic_beam_bigquery.py').read())"`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add composer/synthetic_beam_bigquery.py packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py
git commit -m "fix: salt run_id per trigger (no cross-run seed replay); expose explicit seed param"
```

---

### Task 5: B.1 setup phase milestones (embed vs FAISS vs pools)

The reports attribute a 22-minute stall to "FAISS build", but `reference_rows_limit=10000` means only ≤10k rows were embedded — the cost split between embedding, index build, and LLM pool calls is currently invisible. Instrument before optimizing.

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (setup, lines 113-132)
- Test: `packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py` (extend — the file already asserts milestones via caplog)

**Interfaces:**
- Produces: milestones `b1_embed_done` (`rows=`, `seconds=`), `b1_index_built` (`seconds=`), `b1_pools_built` (`seconds=`, `freetext_cols=`).

- [ ] **Step 1: Write the failing test**

Append to `test_freetext_fallback.py`:

```python
def test_b1_setup_emits_phase_milestones(caplog, free_text_ctx):
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        engine.setup(_BoomClient(), free_text_ctx)
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=b1_embed_done" in text
    assert "rows=12" in text
    assert "SDFB_MILESTONE name=b1_index_built" in text
    assert "SDFB_MILESTONE name=b1_pools_built" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py -k phase_milestones -v`
Expected: FAIL — milestones absent.

- [ ] **Step 3: Implement**

In `b1_rag/engine.py` add `import time` to the imports, then rewrite the embed/index/pool block in `setup()` (lines 123-132):

```python
        if ctx.reference_rows:
            t_embed = time.monotonic()
            texts = serialize_rows(ctx.reference_rows, self._column_order)
            self._ref_vectors = self._embedder.embed(texts)
            log_milestone(
                "b1_embed_done",
                rows=len(texts),
                seconds=round(time.monotonic() - t_embed, 1),
            )
            t_index = time.monotonic()
            self._index = build_index(self._ref_vectors, self._embedder.dim)
            log_milestone(
                "b1_index_built",
                seconds=round(time.monotonic() - t_index, 1),
            )
        else:
            self._ref_vectors = []
            self._index = None

        # 4. infer free-text pools ONCE (the only O(1) LLM use in setup).
        t_pools = time.monotonic()
        self._free_text_pools = self._build_free_text_pools(ctx)
        log_milestone(
            "b1_pools_built",
            seconds=round(time.monotonic() - t_pools, 1),
            freetext_cols=len(self._free_text_pools),
        )
```

(`log_milestone` is already imported in this module.)

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py
git commit -m "feat: B.1 setup phase milestones — embed/index/pool timings to localize the 22-min stall"
```

---

### Task 6: Report-prompt improvements (question 1 from the E2E review)

**Files:**
- Modify: `.github/prompts/end_to_end_validation_report_generation.prompt.md`

No test cycle — documentation/prompt change only.

- [ ] **Step 1: Add the missing extraction requirements**

Add a subsection to the prompt's log-mining instructions:

```markdown
### LLM-lifecycle forensics (mandatory)

For every run under test, extract and report:

1. **The `error=` field of every `freetext_llm_fallback` milestone** — the
   exception class name is logged (`error=RuntimeError`, `error=TimeoutError`,
   …). Never report a fallback without its error type; the 2026-07-10 cycle
   burned three runs because the truncated milestone hid
   `error=RuntimeError` (= client setup never ran).
2. **Presence/absence of the client lifecycle milestones**, in order:
   `model_client_setup_start` → `model_pull_start` → `model_pull_done` →
   `vllm_spawn` → `vllm_ready` → `model_client_setup_done`. Absence of
   `vllm_ready` means NO LLM inference happened — do not infer LLM activity
   from TotalGpuTime (GPU seconds accrue while allocated, even idle).
3. **B.1 phase timings** from `b1_embed_done` (with `rows=`),
   `b1_index_built`, `b1_pools_built` — attribute setup wall-time to the
   correct phase instead of "FAISS build".
4. **Effective launch parameters from the Dataflow job's `parameters` dict**
   (job describe), not from DAG defaults — include `reference_rows_limit`,
   `pk_cols`, `identity_cols`, `seed`, and the salted `run_id`.
```

- [ ] **Step 2: Commit**

```bash
git add .github/prompts/end_to_end_validation_report_generation.prompt.md
git commit -m "docs: report prompt must extract fallback error=, vLLM lifecycle milestones, B.1 phase timings"
```

---

### Task 7: M4 revalidation matrix (docs — executed on the M4, not the laptop)

**Files:**
- Modify: `docs/RUN_PLAYBOOK.md` (run matrix section)

- [ ] **Step 1: Replace the pending run matrix with the post-fix matrix**

```markdown
## Post-remediation run matrix (branch e2e-hardening, after Tasks 1-5)

Common params: `num_rows=1000`, `identity_cols=ID_COL`,
`pk_cols=<PK_COL>,<PK_COL_2>`, `seed=""` (derived), landing table truncated
between runs (or fresh run_id verified in validation_runs).

| Run | Engine | Model | GPU | Expect |
|---|---|---|---|---|
| R1' | b1_rag | qwen3-4b (`vllm_dtype=float16`) | t4 | `vllm_ready` present; NO `freetext_llm_fallback`; job FAILS if vLLM can't start (strict) |
| R2' | b1_rag | qwen3-4b | t4 (repeat of R1') | output DIFFERS from R1' (salted run_id → new seeds) |
| R3' | b2_library | qwen3-4b (`vllm_dtype=float16`) | t4 | same as R1' plus pk.duplicate rule live |
| R4' | b1_rag | gemma4-e4b-it | l4 | bf16 path; guard must NOT fire on L4 |

Pass criteria per run:
1. `model_client_setup_start/done`, `model_pull_*`, `vllm_spawn`, `vllm_ready` all present in worker logs.
2. Zero `freetext_llm_fallback` milestones (a vLLM run that falls back now crashes instead).
3. `copy_ratio < 0.3` on every non-constant STRING column with `source_distinct > 100` (probe step 3).
4. `validation_runs.run_id` unique per Dataflow job (salted suffix visible).
5. `pk.duplicate` present in `dlq_by_rule` if and only if PK collisions occurred; PASSED requires ~0.
6. `b1_embed_done rows=` equals `reference_rows_limit` (10000), not the full source count.
```

- [ ] **Step 2: Commit**

```bash
git add docs/RUN_PLAYBOOK.md
git commit -m "docs: post-remediation M4 run matrix R1'-R4' with strict pass criteria"
```

---

## Self-review notes

- **Spec coverage:** RC1 → Tasks 1-2; RC2 → Task 4; RC3 → Tasks 2-3; observability gaps → Tasks 5-6; live verification → Task 7. Report backlog items deferred deliberately: memorization `copy_ratio` as an in-pipeline gate (M2+ evaluation framework, per `docs/designs/2026-07-07-evaluation-framework-design.md`), FAISS/embed optimization (needs Task 5 evidence first), skipping the embedder pull for b2 (INFO-level perf, 1.6 s).
- **Type consistency:** `strict_freetext` name is identical across `GenerationContext`, `PipelineConfig`, and CLI wiring; `pk_columns` (config) vs `--pk_cols`/`pk_cols` (CLI/DAG) mirrors the existing `identity_columns`/`identity_cols` convention.
- **Test-file names for Tasks 2/3:** the exact summary/uniqueness test module names must be located at execution time (`grep -rl "build_run_summary\|EnforceUniqueness" packages/sdfb-tests/tests`); extend the existing module rather than creating a parallel one.
