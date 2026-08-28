# E2E Hardening Cycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the engine defects found by the 2026-07-07 GCP dev E2E report (seed batch-replay, identity-column memorization, silent LLM fallback, gate blind spots), port + improve the e2e validation tooling into this repo, add a stable milestone-logging contract, and ship the run playbook + two follow-up design specs.

**Architecture:** A pure-Python milestone logger in `sdfb-core` becomes the contract between worker logs and the `e2e_gcp_probe.py` miner. Engine fixes land at the DoFn boundary (seed derivation, identity synthesis) and in the engines' fallback paths. The gate gains two uniqueness rules fed by a new dedup transform that diverts duplicates to the existing DLQ→`dlq_by_rule`→`build_run_summary` path.

**Tech Stack:** Python 3.12, uv, pytest, Apache Beam DirectRunner (mocked GPU/GCP per CLAUDE.md), pandas (scripts only).

**Spec:** `docs/superpowers/specs/2026-07-07-e2e-hardening-design.md`
**Assets (vendored base files):** `docs/superpowers/plans/assets/e2e/`

## Global Constraints

- Laptop-only: never import `vllm`, `torch`, `google.cloud.*` at module import time; GPU/GCP tests carry `@pytest.mark.gpu` / `@pytest.mark.gcp`.
- `sdfb-core` must NOT import `apache_beam` (import direction is strict: beam → core).
- Scripts in `scripts/` import GCP client libs lazily inside functions only.
- Verify after every task: `uv run pytest -m "not gpu and not gcp" -q` and `uv run ruff check .` both green.
- Commit after every task (small commits, message prefix `feat:`/`fix:`/`docs:`/`test:`).
- No Vertex AI / Dataplex / Looker / external LLM APIs anywhere.
- The milestone line format is an API: `SDFB_MILESTONE name=<name> key=value …` — never change wording without updating `scripts/e2e/e2e_gcp_probe.py` and the contract test together.

---

### Task 1: Milestone logger in sdfb-core (`observability.py`)

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/observability.py`
- Test: `packages/sdfb-tests/tests/unit/test_observability.py`

**Interfaces:**
- Produces: `MILESTONE_PREFIX: str = "SDFB_MILESTONE"`, `format_milestone(name: str, **fields) -> str`, `log_milestone(name: str, *, level: int = logging.INFO, **fields) -> str`, `parse_milestone(line: str) -> dict | None`. Later tasks (2, 5, 8) import these.

- [ ] **Step 1: Write the failing test**

```python
"""Contract tests for the SDFB_MILESTONE worker-log line format."""
import logging

from sdfb_core.observability import (
    MILESTONE_PREFIX,
    format_milestone,
    log_milestone,
    parse_milestone,
)


def test_format_is_single_stable_line():
    line = format_milestone("embedder_pull_done", seconds=1.6, files=5)
    assert line == "SDFB_MILESTONE name=embedder_pull_done files=5 seconds=1.6"
    assert "\n" not in line


def test_fields_are_sorted_for_determinism():
    a = format_milestone("x", b=2, a=1)
    b = format_milestone("x", a=1, b=2)
    assert a == b == "SDFB_MILESTONE name=x a=1 b=2"


def test_values_with_spaces_are_quoted():
    line = format_milestone("freetext_llm_fallback", error="RuntimeError: boom now")
    assert line == "SDFB_MILESTONE name=freetext_llm_fallback error='RuntimeError: boom now'"


def test_parse_roundtrip():
    line = format_milestone("vllm_ready", seconds=337.2, model="gemma4")
    parsed = parse_milestone(line)
    assert parsed == {"name": "vllm_ready", "model": "gemma4", "seconds": "337.2"}


def test_parse_rejects_non_milestone_lines():
    assert parse_milestone("INFO something else") is None


def test_log_milestone_emits_via_std_logging(caplog):
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        line = log_milestone("dofn_setup_start", engine="b1_rag")
    assert line.startswith(MILESTONE_PREFIX)
    assert any(line in r.message for r in caplog.records)


def test_warning_level_supported(caplog):
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        log_milestone("freetext_llm_fallback", level=logging.WARNING, column="c1")
    assert caplog.records[0].levelno == logging.WARNING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/sdfb-tests/tests/unit/test_observability.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdfb_core.observability'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Structured worker-log milestones — the log-mining contract.

One stable, greppable line per milestone:

    SDFB_MILESTONE name=<milestone> key=value key='quoted value' ...

``scripts/e2e/e2e_gcp_probe.py`` mines Dataflow worker logs for this prefix to
derive engine execution timings. The format is therefore an API: fields are
emitted in sorted order, values containing whitespace are single-quoted via
``shlex.quote``, and the line never contains a newline. Change nothing here
without updating the probe and the contract test together.

This module is pure stdlib (``logging``/``shlex``/``re``) — sdfb-core must
stay Beam-free. Beam metric counterparts live in ``sdfb_beam``.
"""

from __future__ import annotations

import logging
import re
import shlex

MILESTONE_PREFIX = "SDFB_MILESTONE"

# name= then sorted key=value tokens; values may be shlex-quoted.
_MILESTONE_RE = re.compile(
    rf"{MILESTONE_PREFIX} name=(?P<name>[a-z0-9_]+)(?P<fields>.*)$"
)

_logger = logging.getLogger("sdfb.milestone")


def format_milestone(name: str, **fields) -> str:
    parts = [f"{MILESTONE_PREFIX} name={name}"]
    for key in sorted(fields):
        value = str(fields[key]).replace("\n", " ")
        parts.append(f"{key}={shlex.quote(value)}")
    return " ".join(parts)


def log_milestone(name: str, *, level: int = logging.INFO, **fields) -> str:
    line = format_milestone(name, **fields)
    _logger.log(level, line)
    return line


def parse_milestone(line: str) -> dict | None:
    m = _MILESTONE_RE.search(line)
    if not m:
        return None
    out = {"name": m.group("name")}
    for token in shlex.split(m.group("fields")):
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/sdfb-tests/tests/unit/test_observability.py -q`
Expected: 7 passed

- [ ] **Step 5: Ruff + full suite + commit**

```bash
uv run ruff check . && uv run pytest -m "not gpu and not gcp" -q
git add packages/sdfb-core/src/sdfb_core/observability.py packages/sdfb-tests/tests/unit/test_observability.py
git commit -m "feat: SDFB_MILESTONE structured log contract in sdfb-core"
```

---

### Task 2: Instrument the Beam worker path with milestones

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py`
- Test: extend the existing DoFn test module under `packages/sdfb-tests/tests/unit/dofns/` (read it first; reuse its ctx/engine fixtures)

**Interfaces:**
- Consumes: `log_milestone` from Task 1.
- Produces: milestone names used by Task 8's probe: `dofn_setup_start`, `embedder_pull_start`, `embedder_pull_done`, `dofn_setup_done`, `batch_start`, `batch_done`, `model_pull_start`, `model_pull_done`, `vllm_spawn`, `vllm_ready`.

- [ ] **Step 1: Read the existing DoFn tests** — `ls packages/sdfb-tests/tests/unit/dofns/ && sed -n 1,60p` the generate-DoFn test file to learn the fixture that builds a `GenerationContext` + fake client. Reuse it below (import it; do not duplicate).

- [ ] **Step 2: Write the failing test** (append to the existing generate-DoFn test module; adapt fixture names to what Step 1 found)

```python
def test_setup_and_process_emit_milestones(caplog, <existing_ctx_fixture>, <existing_client_fixture>):
    import logging
    dofn = GenerateRecordsDoFn(
        engine_name="b2_library",  # or the engine the existing tests use
        model_client=<existing_client_fixture>,
        ctx=<existing_ctx_fixture>,
    )
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        dofn.setup()
        list(dofn.process({"n": 4, "batch_id": 0}))
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=dofn_setup_start" in text
    assert "SDFB_MILESTONE name=dofn_setup_done" in text
    assert "SDFB_MILESTONE name=batch_start" in text and "batch_id=0" in text
    assert "SDFB_MILESTONE name=batch_done" in text and "rows=4" in text
```

- [ ] **Step 3: Run to verify it fails** — `uv run pytest packages/sdfb-tests/tests/unit/dofns/ -q -k milestone` → FAIL (no milestones logged).

- [ ] **Step 4: Instrument `generate.py`** — exact edits:

Add import at top: `from sdfb_core.observability import log_milestone` and `import time`.

In `setup()` (currently lines 61–78), wrap:

```python
    def setup(self):
        t0 = time.monotonic()
        log_milestone("dofn_setup_start", engine=self.engine_name)
        ctx = self.ctx
        if ctx.embedder_uri.startswith("gs://"):
            from sdfb_beam.gcs import localize_gcs_prefix

            log_milestone("embedder_pull_start", uri=ctx.embedder_uri)
            t_pull = time.monotonic()
            local_dir = localize_gcs_prefix(ctx.embedder_uri, EMBEDDER_LOCAL_DIR)
            log_milestone(
                "embedder_pull_done",
                seconds=round(time.monotonic() - t_pull, 1),
            )
            ctx = ctx.model_copy(update={"embedder_uri": local_dir})
            self.ctx = ctx  # cache so a re-entrant setup() skips the pull

        engine_class = get_engine(self.engine_name)
        self._engine = engine_class()
        self._engine.setup(self.model_client, ctx)
        log_milestone(
            "dofn_setup_done",
            engine=self.engine_name,
            seconds=round(time.monotonic() - t0, 1),
        )
```

In `process()`, after computing `cfg` and before the `try`, add `log_milestone("batch_start", batch_id=batch_id, n=n)` and `t0 = time.monotonic(); count = 0`; inside the loop increment `count`; after the loop (still inside `try`) add:

```python
            log_milestone(
                "batch_done",
                batch_id=batch_id,
                rows=count,
                seconds=round(time.monotonic() - t0, 1),
            )
            self._batch_seconds.update(int((time.monotonic() - t0) * 1000))
```

In `__init__`, alongside the existing counters, add:

```python
        self._batch_seconds = Metrics.distribution("generation", "batch_msec")
```

- [ ] **Step 5: Instrument `vllm_client.py`** — in `setup()` (line ~136): `log_milestone("model_pull_start", uri=...)` / `log_milestone("model_pull_done", seconds=...)` around `self._pull_weights()`; `log_milestone("vllm_spawn")` before `self._spawn_server()`; `log_milestone("vllm_ready", seconds=<total setup seconds>)` after `self._wait_until_ready()`. Same `time.monotonic()` pattern as Step 4. Import stays top-level-safe (`sdfb_core.observability` is stdlib-only).

- [ ] **Step 6: Run tests + commit**

```bash
uv run pytest -m "not gpu and not gcp" -q && uv run ruff check .
git add -A packages/
git commit -m "feat: milestone instrumentation for DoFn setup/batches and vLLM client"
```

---

### Task 3: Derived per-batch seed (kills batch replay)

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/seeding.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py:83`
- Test: `packages/sdfb-tests/tests/unit/test_seeding.py` + extend the generate-DoFn test module

**Interfaces:**
- Produces: `derive_batch_seed(run_id: str, batch_id: int) -> int` (also consumed by Task 4).

- [ ] **Step 1: Write the failing test** (`packages/sdfb-tests/tests/unit/test_seeding.py`)

```python
from sdfb_core.seeding import derive_batch_seed


def test_deterministic_per_run_and_batch():
    assert derive_batch_seed("run-a", 0) == derive_batch_seed("run-a", 0)


def test_batches_differ():
    seeds = {derive_batch_seed("run-a", i) for i in range(100)}
    assert len(seeds) == 100


def test_runs_differ():
    assert derive_batch_seed("run-a", 0) != derive_batch_seed("run-b", 0)


def test_fits_in_signed_64bit_and_nonnegative():
    s = derive_batch_seed("x" * 500, 10**9)
    assert 0 <= s < 2**63
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest packages/sdfb-tests/tests/unit/test_seeding.py -q` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement `sdfb_core/seeding.py`**

```python
"""Per-batch seed derivation for the no-``--seed`` case.

Without this, ``seed=None`` reaches the engines' ``_mix_seed(None) → 0`` path
and every batch replays an identical draw (the 97.6 %-duplicate defect from
the 2026-07 E2E report). Deriving from ``(run_id, batch_id)`` keeps runs
reproducible — re-running the same run_id reproduces the exact output — while
guaranteeing no two batches share an RNG stream.
"""

from __future__ import annotations

import hashlib


def derive_batch_seed(run_id: str, batch_id: int) -> int:
    """Stable, collision-resistant seed from (run_id, batch_id), in [0, 2^63)."""
    digest = hashlib.blake2b(
        f"{run_id}\x1f{batch_id}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") >> 1
```

- [ ] **Step 4: Wire into `generate.py`** — replace line 83:

```python
        seed = None if self.base_seed is None else self.base_seed + batch_id
```

with:

```python
        if self.base_seed is None:
            # No explicit seed: derive one so batches never replay each other
            # while the run stays reproducible per run_id (E2E report §2).
            seed = derive_batch_seed(self.ctx.pipeline_run_id, batch_id)
        else:
            seed = self.base_seed + batch_id
```

and add `from sdfb_core.seeding import derive_batch_seed` to the imports.

- [ ] **Step 5: Add a DoFn-level regression test** (generate-DoFn test module): call `process({"n": 4, "batch_id": 0})` and `process({"n": 4, "batch_id": 1})` on a DoFn built with `seed=None` and a real engine (b2_library with the fake client, as the existing tests do); assert the two batches' record lists are NOT identical.

- [ ] **Step 6: Run + commit**

```bash
uv run pytest -m "not gpu and not gcp" -q && uv run ruff check .
git add -A packages/
git commit -m "fix: derive per-batch seed from (run_id, batch_id) when --seed absent"
```

---

### Task 4: Identity columns synthesized per-row-unique

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/engines/identity.py`
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py` (GenerationContext, ~line 74)
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` (process)
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (PipelineConfig + ctx construction)
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (new `--identity-cols` flag)
- Test: `packages/sdfb-tests/tests/unit/engines/test_identity.py`

**Interfaces:**
- Consumes: `derive_batch_seed` (Task 3).
- Produces: `GenerationContext.identity_columns: list[str]`, `synthesize_identity_value(bq_type: str, run_id: str, batch_id: int, row_index: int, column: str) -> str | int`, `apply_identity_columns(record: dict, *, identity_columns: list[str], column_types: dict[str, str], run_id: str, batch_id: int, row_index: int) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
import uuid

from sdfb_core.engines.identity import (
    apply_identity_columns,
    synthesize_identity_value,
)


def test_string_identity_is_valid_uuid_and_deterministic():
    v1 = synthesize_identity_value("STRING", "run-a", 0, 0, "customer_id")
    v2 = synthesize_identity_value("STRING", "run-a", 0, 0, "customer_id")
    assert v1 == v2
    uuid.UUID(v1)  # raises if not UUID-shaped


def test_rows_and_batches_and_columns_are_unique():
    vals = {
        synthesize_identity_value("STRING", "run-a", b, r, c)
        for b in range(3)
        for r in range(50)
        for c in ("id_a", "id_b")
    }
    assert len(vals) == 3 * 50 * 2


def test_integer_identity_is_int():
    v = synthesize_identity_value("INTEGER", "run-a", 1, 2, "seq_id")
    assert isinstance(v, int) and v >= 0


def test_apply_overwrites_only_identity_columns():
    record = {"customer_id": "LEAKED-REAL-VALUE", "amount": 42}
    out = apply_identity_columns(
        record,
        identity_columns=["customer_id"],
        column_types={"customer_id": "STRING", "amount": "INTEGER"},
        run_id="run-a",
        batch_id=0,
        row_index=0,
    )
    assert out["amount"] == 42
    assert out["customer_id"] != "LEAKED-REAL-VALUE"
    uuid.UUID(out["customer_id"])
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest packages/sdfb-tests/tests/unit/engines/test_identity.py -q` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement `engines/identity.py`**

```python
"""Identity-column synthesis — never emit reference values for row identifiers.

The 2026-07 E2E report found identity columns copied verbatim from the source
table (copy_ratio = 1.0 — a re-identification leak). Identity columns are
therefore synthesized per row from ``(run_id, batch_id, row_index, column)``,
bypassing reference pools, empirical sampling, and the LLM path entirely.
Deterministic per run_id so reruns reproduce.
"""

from __future__ import annotations

import hashlib
import uuid


def _digest(run_id: str, batch_id: int, row_index: int, column: str) -> bytes:
    key = f"{run_id}\x1f{batch_id}\x1f{row_index}\x1f{column}".encode()
    return hashlib.blake2b(key, digest_size=16).digest()


def synthesize_identity_value(
    bq_type: str, run_id: str, batch_id: int, row_index: int, column: str
) -> str | int:
    raw = _digest(run_id, batch_id, row_index, column)
    if bq_type in {"INTEGER", "INT64"}:
        return int.from_bytes(raw[:8], "big") >> 1
    # UUIDv4-shaped so downstream format checks (regex.format) keep passing.
    return str(uuid.UUID(bytes=raw, version=4))


def apply_identity_columns(
    record: dict,
    *,
    identity_columns: list[str],
    column_types: dict[str, str],
    run_id: str,
    batch_id: int,
    row_index: int,
) -> dict:
    for column in identity_columns:
        if column in record:
            record[column] = synthesize_identity_value(
                column_types.get(column, "STRING"),
                run_id,
                batch_id,
                row_index,
                column,
            )
    return record
```

- [ ] **Step 4: Add `identity_columns` to `GenerationContext`** in `engines/base.py` (after `reference_digest`, ~line 86):

```python
    # Columns that must be per-row-unique and NEVER sampled from reference
    # data (PK / UUID / account-number style). See engines/identity.py.
    identity_columns: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Apply in `GenerateRecordsDoFn.process`** — replace the yield loop body:

```python
            for row_index, record in enumerate(
                self._engine.generate_batch(n, cfg)  # type: ignore[union-attr]
            ):
                self._yielded.inc()
                count += 1
                row = record.model_dump(mode="python")
                if self.ctx.identity_columns:
                    row = apply_identity_columns(
                        row,
                        identity_columns=self.ctx.identity_columns,
                        column_types=self._column_types,
                        run_id=self.ctx.pipeline_run_id,
                        batch_id=batch_id,
                        row_index=row_index,
                    )
                yield row
```

In `setup()`, after the engine is built, derive `self._column_types` once from `self.ctx.table_schema` — read `engines/base.py` / `sdfb_core.contracts` first to find the TableSchema shape, then build `{field_name: bq_type}` accordingly (it is the same structure the codegen emits from `_ddl.json`; handle both attribute and dict access). Import `apply_identity_columns` from `sdfb_core.engines.identity`.

- [ ] **Step 6: Plumb through pipeline + CLI** — `PipelineConfig` gains `identity_columns: tuple[str, ...] = ()` (dataclass field near `seed`, pipeline.py:59); the `GenerationContext` construction in `build_pipeline` (~line 93, where `pipeline_run_id=config.run_id` is set) gains `identity_columns=list(config.identity_columns)`. `cli/run_pipeline.py` gains `--identity-cols` (comma-separated string → tuple) with help text "per-row-unique columns synthesized fresh each row (PK/UUID); never sampled from reference data".

- [ ] **Step 7: Run + commit**

```bash
uv run pytest -m "not gpu and not gcp" -q && uv run ruff check .
git add -A packages/
git commit -m "fix: synthesize identity columns per row; never sample from reference (privacy)"
```

---

### Task 5: Fallback taxonomy — loud generation fallback, fatal init, GPU guard

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/freetext.py:144-145`
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`_infer_free_text_pool` except path)
- Modify: `packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py` (`setup()`)
- Modify: `config/models.yml` (documentation fields)
- Test: `packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py`, `packages/sdfb-tests/tests/unit/handlers/` (extend vllm client tests)

**Interfaces:**
- Consumes: `log_milestone` (Task 1).
- Produces: milestone `freetext_llm_fallback` (mined by Task 8); `ModelGpuIncompatibleError` and `_assert_dtype_supported(torch_dtype: str, capability: tuple[int, int]) -> None` in `vllm_client.py`.

- [ ] **Step 1: Write the failing fallback test**

```python
import logging

from sdfb_core.engines.b2_library.freetext import FreeTextSampler  # adjust to the real class name after reading the file


class _BoomClient:
    def generate_json(self, *a, **k):
        raise RuntimeError("boom")


def test_b2_fallback_emits_warning_milestone(caplog, <profile_fixture>):
    sampler = <construct sampler exactly as existing b2 freetext tests do, with _BoomClient()>
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        pool = <call the pool-generating method that hits _generate_pool>
    assert pool  # exemplar fallback still returns values
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=freetext_llm_fallback" in text
    assert "error=RuntimeError" in text
```

First read `freetext.py` and the existing b2 freetext tests to fill the construction details; mirror an equivalent test for B.1 `_infer_free_text_pool` using its existing test fixtures.

- [ ] **Step 2: Run to verify failure**, then **Step 3: implement** — in `freetext.py` `_generate_pool` replace:

```python
        except Exception:
            return exemplars
```

with:

```python
        except Exception as e:
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

(add `import logging` + `from sdfb_core.observability import log_milestone`). Apply the same pattern to the `except` inside B.1's `_infer_free_text_pool` (which currently sets `pool=[]` silently) — same milestone name, `column=` from the profile being inferred.

- [ ] **Step 4: GPU/dtype guard in `vllm_client.py`** — add near the top of the module:

```python
class ModelGpuIncompatibleError(RuntimeError):
    """The pulled model's dtype cannot run on this worker's GPU.

    Raised BEFORE the vLLM server spawn so the Dataflow job fails fast with an
    actionable message instead of stalling for ~24 min and letting the engines
    silently fall back to copying reference exemplars (E2E report §4.2)."""


_MIN_BF16_CAPABILITY = (8, 0)


def _assert_dtype_supported(torch_dtype: str, capability: tuple[int, int]) -> None:
    if torch_dtype == "bfloat16" and capability < _MIN_BF16_CAPABILITY:
        raise ModelGpuIncompatibleError(
            f"model dtype bfloat16 needs GPU compute capability >= 8.0 "
            f"(Ampere/L4+); this worker reports {capability[0]}.{capability[1]} "
            "(e.g. T4/Turing). Run with gpu=l4, or point SDFB_MODEL_URI at a "
            "T4-safe fp16 model (see config/models.yml: qwen3_4b_instruct_2507). "
            "Do NOT force --dtype=half for Gemma: it silently emits empty output."
        )
```

In `setup()`, after `self._pull_weights()` and before `self._spawn_server()`:

```python
        # Fail fast on T4+bf16 instead of letting vLLM stall and the engines
        # fall back to memorizing reference data.
        try:
            import torch  # GPU-only path; absent on the laptop
        except ImportError:
            torch = None
        if torch is not None and torch.cuda.is_available():
            import json as _json
            from pathlib import Path as _Path

            cfg_path = _Path(self._local_model_dir) / "config.json"  # use the real attr name for /local-ssd/model
            if cfg_path.exists():
                dtype = _json.loads(cfg_path.read_text()).get("torch_dtype", "")
                _assert_dtype_supported(dtype, torch.cuda.get_device_capability())
```

(Read `_pull_weights` first to get the real local-dir attribute name.) Unit-test `_assert_dtype_supported` directly — `("bfloat16", (7, 5))` raises with "qwen3_4b_instruct_2507" in the message; `("bfloat16", (8, 9))` and `("float16", (7, 5))` pass. No torch needed.

- [ ] **Step 5: `config/models.yml`** — add `min_compute_capability: "8.0"  # bf16; T4 (7.5) cannot run this — see RUN_PLAYBOOK.md` to both gemma4 entries.

- [ ] **Step 6: Run + commit** — `git commit -m "fix: loud freetext LLM fallback + fatal bf16-on-Turing guard in vLLM client"`

---

### Task 6: Uniqueness gate — `row.duplicate` + `identity.unique` rules

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/validation/uniqueness.py`
- Create: `packages/sdfb-beam/src/sdfb_beam/dofns/uniqueness.py`
- Modify: `config/thresholds.yml`
- Modify: `packages/sdfb-core/src/sdfb_core/validation/summary.py` (`BLOCKER_RULE_IDS`)
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (wire between `batch_validated.main` and `WriteLanding`, add tag to `FlattenDLQ`)
- Test: `packages/sdfb-tests/tests/unit/validation/test_uniqueness.py`, `packages/sdfb-tests/tests/unit/dofns/test_uniqueness_transform.py`

**Interfaces:**
- Produces: `row_digest(record: dict) -> str` (pure); `EnforceUniqueness(identity_columns: list[str])` a `beam.PTransform` returning a result with `.unique` (main) and `.duplicates` (DLQ-envelope dicts with `rule_id` in `{"row.duplicate", "identity.unique"}`, `error_type="uniqueness"`, `stage="pre_write"`).

- [ ] **Step 1: Failing pure test** (`test_uniqueness.py`)

```python
from sdfb_core.validation.uniqueness import row_digest


def test_digest_stable_across_key_order():
    assert row_digest({"a": 1, "b": "x"}) == row_digest({"b": "x", "a": 1})


def test_digest_differs_on_value_change():
    assert row_digest({"a": 1}) != row_digest({"a": 2})


def test_non_json_types_hash_via_str():
    from datetime import date
    assert row_digest({"d": date(2026, 1, 1)}) == row_digest({"d": date(2026, 1, 1)})
```

- [ ] **Step 2: Implement** `sdfb_core/validation/uniqueness.py`:

```python
"""Row/identity digests feeding the uniqueness gate rules."""

from __future__ import annotations

import hashlib
import json


def row_digest(record: dict) -> str:
    canon = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.blake2b(canon.encode(), digest_size=16).hexdigest()
```

- [ ] **Step 3: Failing transform test** (`test_uniqueness_transform.py`) — DirectRunner `TestPipeline`: create 4 dict records where two are identical and two share an identity value; apply `EnforceUniqueness(identity_columns=["id"])`; `assert_that` `.unique` has 2 elements and `.duplicates` has 2 envelopes whose `rule_id`s are `{"row.duplicate", "identity.unique"}`. Follow the existing tagged-output test style in `tests/unit/dofns/`.

- [ ] **Step 4: Implement** `sdfb_beam/dofns/uniqueness.py`:

```python
"""Uniqueness enforcement — duplicates divert to the DLQ instead of landing.

Full-row duplicates and repeated identity values are the two block-replay /
memorization signatures the 2026-07 E2E report found unguarded. Keying by
digest + GroupByKey keeps memory flat regardless of run size; the first
occurrence lands, the rest become DLQ rows whose ``rule_id`` feeds
``build_run_summary`` → the BLOCKER gate.
"""

from __future__ import annotations

import apache_beam as beam

from sdfb_core.validation.uniqueness import row_digest

RULE_ROW_DUPLICATE = "row.duplicate"
RULE_IDENTITY_UNIQUE = "identity.unique"


def _envelope(record: dict, rule_id: str) -> dict:
    return {
        "raw_request": record,
        "error_type": "uniqueness",
        "error_detail": f"{rule_id}: duplicate of an earlier record in this run",
        "rule_id": rule_id,
        "stage": "pre_write",
    }


class _FirstWins(beam.DoFn):
    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id

    def process(self, kv):
        _key, records = kv
        it = iter(records)
        yield next(it)
        for dup in it:
            yield beam.pvalue.TaggedOutput("duplicates", _envelope(dup, self.rule_id))


class EnforceUniqueness(beam.PTransform):
    def __init__(self, identity_columns: list[str] | None = None) -> None:
        super().__init__()
        self.identity_columns = list(identity_columns or [])

    def expand(self, records):
        by_row = (
            records
            | "KeyByRowDigest" >> beam.Map(lambda r: (row_digest(r), r))
            | "GroupByRowDigest" >> beam.GroupByKey()
            | "FirstRowWins"
            >> beam.ParDo(_FirstWins(RULE_ROW_DUPLICATE)).with_outputs(
                "duplicates", main="unique"
            )
        )
        row_unique = by_row.unique
        dup_streams = [by_row.duplicates]
        if self.identity_columns:
            cols = self.identity_columns
            by_id = (
                row_unique
                | "KeyByIdentity"
                >> beam.Map(lambda r, c=cols: (tuple(str(r.get(x)) for x in c), r))
                | "GroupByIdentity" >> beam.GroupByKey()
                | "FirstIdentityWins"
                >> beam.ParDo(_FirstWins(RULE_IDENTITY_UNIQUE)).with_outputs(
                    "duplicates", main="unique"
                )
            )
            row_unique = by_id.unique
            dup_streams.append(by_id.duplicates)
        duplicates = dup_streams | "FlattenDuplicates" >> beam.Flatten()
        return {"unique": row_unique, "duplicates": duplicates}
```

(If dict-return from `expand` fights the existing pipeline style, mirror how other tagged transforms in this repo return results — adapt, keep the two-collection contract.)

- [ ] **Step 5: Wire into `pipeline.py`** — between `batch_validated.main` and `WriteLanding` (line ~142):

```python
    uniq = batch_validated.main | "EnforceUniqueness" >> EnforceUniqueness(
        identity_columns=list(config.identity_columns)
    )
    _ = uniq["unique"] | "WriteLanding" >> landing_sink
```

and extend the DLQ flatten (line ~147) to include `uniq["duplicates"]`. Update the `result` dict's `"valid"` entry to `uniq["unique"]`.

- [ ] **Step 6: Register the rules** — `config/thresholds.yml` under `rules:`:

```yaml
  row.duplicate:
    dimension: uniqueness
    severity: BLOCKER
    threshold: 0     # duplicates divert to DLQ; ratio gated by blocker_failure_ratio

  identity.unique:
    dimension: uniqueness
    severity: BLOCKER
    threshold: 0
```

In `summary.py`, find `BLOCKER_RULE_IDS` (imported constant — locate its definition with `grep -rn "BLOCKER_RULE_IDS" packages/`) and add both ids.

- [ ] **Step 7: Run + commit** — full suite + ruff; `git commit -m "feat: row.duplicate + identity.unique BLOCKER gate rules with DLQ diversion"`

---

### Task 7: Vendor the e2e scripts + prompt into the repo

**Files:**
- Create: `scripts/e2e/e2e_validation_analysis.py`, `scripts/e2e/e2e_gcp_probe.py`, `scripts/e2e/e2e_bundle_export.py` (copied from `docs/superpowers/plans/assets/e2e/`)
- Create: `.github/prompts/end_to_end_validation_report_generation.prompt.md` (copied from assets)
- Test: `packages/sdfb-tests/tests/unit/scripts/test_e2e_validation_analysis.py`

- [ ] **Step 1: Copy the assets verbatim**

```bash
cp docs/superpowers/plans/assets/e2e/e2e_validation_analysis.py scripts/e2e/e2e_validation_analysis.py
cp docs/superpowers/plans/assets/e2e/e2e_gcp_probe.py scripts/e2e/e2e_gcp_probe.py
cp docs/superpowers/plans/assets/e2e/e2e_bundle_export.py scripts/e2e/e2e_bundle_export.py
mkdir -p .github/prompts
cp docs/superpowers/plans/assets/e2e/end_to_end_validation_report_generation.prompt.md .github/prompts/
```

- [ ] **Step 2: Write smoke tests for the analysis script** — load it the way `tests/unit/cli/test_deployment_prerequisites.py` loads `scripts/deployment_prerequisites.py` (read that file first and reuse its importlib pattern). Test with an in-test fixture:

```python
def test_analysis_end_to_end(tmp_path, analysis_module):
    schema = [{"name": "id", "type": "STRING"}, {"name": "amt", "type": "INTEGER"}]
    (tmp_path / "s.json").write_text(json.dumps(schema))
    csv = "id,amt\r\n" + "\r\n".join(f"row-{i % 4},{i}" for i in range(16))
    (tmp_path / "e.csv").write_text(csv)
    out = tmp_path / "m.json"
    rc = analysis_module.main_with_args([  # see Step 3
        "--csv", f"eng={tmp_path/'e.csv'}", "--schema", str(tmp_path/'s.json'),
        "--identity-cols", "id", "--out", str(out),
    ])
    assert rc == 0
    m = json.loads(out.read_text())
    eng = m["engines"]["eng"]
    assert eng["row_count"] == 16
    assert eng["identity_columns"]["id"]["distinct"] == 4
    assert eng["columns"]["amt"]["int_type_conformance"] == 1.0
```

- [ ] **Step 3: Minimal testability refactor** — in all three scripts, change `def main() -> int:` to `def main(argv: list[str] | None = None) -> int:` and `ap.parse_args()` → `ap.parse_args(argv)` (expose as `main_with_args = main` alias if the test reads better). No other behavior change.

- [ ] **Step 4: Ruff will flag style in the vendored scripts** — fix only what `uv run ruff check scripts/` reports; do not restructure.

- [ ] **Step 5: Run + commit** — `git commit -m "feat: vendor e2e validation/probe/bundle scripts + report prompt from M4"`

---

### Task 8: Probe improvements — milestone contract, failure regexes, run-id filter, engine labels

**Files:**
- Modify: `scripts/e2e/e2e_gcp_probe.py`
- Test: `packages/sdfb-tests/tests/unit/scripts/test_e2e_gcp_probe.py`

**Interfaces:**
- Consumes: `MILESTONE_PREFIX` / `parse_milestone` semantics from Task 1 (the probe re-implements parsing standalone — scripts must not import project packages — but the contract test imports BOTH and asserts agreement).

- [ ] **Step 1: Failing contract + parsing tests**

```python
def test_probe_recognizes_sdfb_milestones(probe_module):
    from sdfb_core.observability import format_milestone
    line = format_milestone("vllm_ready", seconds=12.5)
    assert probe_module._SDFB_MILESTONE_RE.search(line)
    name = probe_module._SDFB_MILESTONE_RE.search(line).group("name")
    assert name == "vllm_ready"


def test_vllm_error_regexes(probe_module):
    labels = dict(probe_module._DEFAULT_MILESTONES)
    rx = labels["vllm_error"]
    import re
    for text in (
        "ValueError: Bfloat16 is only supported on GPUs with compute capability of at least 8.0",
        "out of resource: shared memory, Required: 98304, Hardware limit: 65536",
        "head size 512 is not supported by FlashInfer",
        "torch.cuda.OutOfMemoryError: CUDA out of memory",
    ):
        assert re.search(rx, text), text
```

- [ ] **Step 2: Implement in the probe** — add to `e2e_gcp_probe.py`:

```python
# First-class contract with sdfb_core.observability.log_milestone: any line
# "SDFB_MILESTONE name=<x> ..." is captured generically, one timestamp per
# distinct name. Legacy wording regexes below remain as fallback for jobs
# that predate the instrumented image.
_SDFB_MILESTONE_RE = re.compile(r"SDFB_MILESTONE name=(?P<name>[a-z0-9_]+)")
```

Extend `_DEFAULT_MILESTONES` with:

```python
    ("vllm_error", r"Bfloat16 is only supported|out of resource: shared memory|head size \d+ is not supported|CUDA out of memory"),
```

In `_worker_log_milestones`, inside the entry loop before the legacy regex pass:

```python
            sm2 = _SDFB_MILESTONE_RE.search(text)
            if sm2:
                found.setdefault(f"sdfb.{sm2.group('name')}", ts)
```

- [ ] **Step 3: run-id filter + engine labels** — in `bq_quality`, when `run_ids` is non-empty, replace the query with a parameterized `WHERE run_id IN UNNEST(@run_ids) ORDER BY 1 DESC LIMIT 50` (use `bigquery.ArrayQueryParameter("run_ids", "STRING", run_ids)`); keep the bare `LIMIT 50` as the no-run-id fallback. Add `--engine-label` (repeatable, `label=job_id`); store on each `dataflow_job` result as `"engine_label"` when its job_id matches. Test both with a fake client object exposing a `query()` returning canned rows (follow the fake-object style used in `tests/unit/cli/test_deployment_prerequisites.py`).

- [ ] **Step 4: Run + commit** — `git commit -m "feat: probe mines SDFB_MILESTONE contract, vllm failure lines, run-id filter, engine labels"`

---

### Task 9: Analysis improvements — batch-size annotation, entropy, sequential identity check

**Files:**
- Modify: `scripts/e2e/e2e_validation_analysis.py`
- Test: extend `packages/sdfb-tests/tests/unit/scripts/test_e2e_validation_analysis.py`

- [ ] **Step 1: Failing tests**

```python
def test_run_lengths_flag_batch_size(analysis_module, tmp_path):
    # 32 rows of the same id in blocks of 8 → with --batch-size 8, flagged.
    ...build a CSV whose id column repeats each value 8 times consecutively...
    m = ...run main() with ["--batch-size", "8", ...]...
    rl = m["engines"]["eng"]["identity_columns"]["id"]["run_lengths"]
    assert rl["equals_batch_size"] is True


def test_entropy_reported(analysis_module, tmp_path):
    m = ...run on a CSV with a constant column and a diverse column...
    cols = m["engines"]["eng"]["columns"]
    assert cols["const_col"]["normalized_entropy"] == 0.0
    assert cols["diverse_col"]["normalized_entropy"] > 0.9
```

- [ ] **Step 2: Implement** — add `--batch-size` (int, default 0 = unknown); in `_run_lengths` accept `batch_size: int = 0` and add `"equals_batch_size": batch_size > 0 and runs_sorted[0][1] == batch_size`. In `_column_report` add:

```python
        import math
        if len(non_empty) and distinct > 1:
            probs = (vc / len(non_empty)).tolist()
            h = -sum(p * math.log2(p) for p in probs if p > 0)
            entry["normalized_entropy"] = round(h / math.log2(distinct), 6)
        else:
            entry["normalized_entropy"] = 0.0
```

(move `import math` to top-level imports). Thread `batch_size` from `main()` through `_analyze_one` → `_key_report`/`_identity_report` → `_run_lengths`.

- [ ] **Step 3: Run + commit** — `git commit -m "feat: analysis gains batch-size-aware run flags + normalized entropy"`

---

### Task 10: Bundle-export improvements — recursive leak scan, more identifiers

**Files:**
- Modify: `scripts/e2e/e2e_bundle_export.py`
- Test: `packages/sdfb-tests/tests/unit/scripts/test_e2e_bundle_export.py`

- [ ] **Step 1: Failing tests** — build minimal `gcp`/`offline` metric dicts in-test (project, source/landing FQNs, one dataflow job with `job_id`, `name`, and `environment.worker_image = "europe-docker.pkg.dev/proj/repo/img:tag"`), plus a tiny report md mentioning the project id; run `main()` against tmp_path; assert: exit code 0, `oss/report.md` contains no real project id, `mapping.json` maps the worker image, and a leak planted in a SUBDIRECTORY of `oss/` is caught (call `_leak_scan` directly for that case).

- [ ] **Step 2: Implement** — in `_leak_scan`, replace `oss_dir.iterdir()` with `(f for f in oss_dir.rglob("*") if f.is_file())`. In `_collect_identifiers`, after the dataflow-jobs loop, add:

```python
    for job in gcp.get("dataflow") or []:
        img = (job.get("environment") or {}).get("worker_image")
        if img:
            m.add_identifier(img, "WORKER_IMAGE")
```

- [ ] **Step 3: Run + commit** — `git commit -m "feat: bundle export scans recursively and redacts worker-image URIs"`

---

### Task 11: models.yml Qwen entry + composer docstring

**Files:**
- Modify: `config/models.yml`
- Modify: `composer/synthetic_beam_bigquery.py` (the `gpu` Param description only)

- [ ] **Step 1: Add the registry entry** under `models:`:

```yaml
  qwen3_4b_instruct_2507:
    # T4-safe profile — the ONLY registry entry that runs on Turing (SM 7.5).
    # Gemma cannot run on T4 in any form (bf16 needs SM>=8.0 and fp16 Gemma
    # silently emits empty output; attention head sizes exceed Turing's 64KB
    # shared memory — vLLM #38918/#38887). Use this to exercise the REAL vLLM
    # path cheaply on gpu=t4; keep Gemma-on-L4 as the fidelity profile.
    description: Qwen3 4B Instruct (2507). T4-compatible fp16; guided-JSON clean.
    gcs_uri: "gs://{bucket}/synthetic/models/qwen3/4b-instruct-2507/v1/"
    quantization: null            # FP16 (fp16-safe family, unlike Gemma)
    expected_vram_gb: 9
    min_compute_capability: "7.5"
    license: apache-2.0
    source: huggingface           # pulled once on the M4, never at runtime
    download_hint: |
      hf download Qwen/Qwen3-4B-Instruct-2507 --local-dir qwen3-4b-instruct-2507
```

- [ ] **Step 2: Update the `gpu` Param description** in the composer file (lines 120–130): replace the "pair t4 with a small Turing-compatible model" sentence with: `"t4 = n1-standard-8 + NVIDIA T4 (16GB): plumbing profile. Gemma CANNOT run on T4 (bf16/SM7.5 + shared-memory limits — the vLLM client now fails fast, see handlers/vllm_client.py ModelGpuIncompatibleError); pair t4 with the qwen3_4b_instruct_2507 registry model via SDFB_MODEL_URI."` Keep the default `"t4"` unchanged.

- [ ] **Step 3: Commit** — `git commit -m "docs: register T4-safe Qwen3-4B model; document t4/Gemma incompatibility"`

---

### Task 12: `docs/RUN_PLAYBOOK.md`

**Files:**
- Create: `docs/RUN_PLAYBOOK.md`

- [ ] **Step 1: Write the doc** with these sections (full prose, no stubs):
  1. **GPU verdict** — Gemma-on-T4 impossibility (three blockers + citations: HF gemma-3-4b-it discussion #33, vllm#40290, vllm#38918, vllm#38887; quantization does not help — #38918 was already AWQ); L4 availability europe-west3-a/b; europe-west4 fallback; the fail-fast guard (`ModelGpuIncompatibleError`).
  2. **Run matrix** — a table of four runs with exact Airflow params (`engine`, `client_type`, `gpu`, `num_rows`, `batch_size`, `similarity`, `seed`) and expected insight: (R1) b1_rag L4+Gemma fidelity; (R2) b2_library L4+Gemma fidelity; (R3) b2_library similarity sweep 0.0/0.5/0.9 to quantify `_blend_pools` reference mass; (R4) either engine T4+Qwen3-4B plumbing (`SDFB_MODEL_URI` → qwen3 gcs_uri). Plus a reproducibility check: same run_id twice with no seed ⇒ identical output.
  3. **Dataflow options** — `worker_accelerator=type:nvidia-l4;count:1;install-nvidia-driver`, g2-standard-4 vs -8 trade-off (1 L4 either way; -8 for headroom), `disk_size_gb>=50` (GPU image size), Runner v2, zone pinning europe-west3-a/b, max_workers guidance for 1000-row runs (1–2).
  4. **L4 capacity strategy (europe-west3 g2/L4 stockout)** — ladder from weakest to strongest: (a) on-demand + zone spread a/b (default; retries on stockout); (b) the `automatically_use_created_reservation` experiment already in the DAG (`composer/synthetic_beam_bigquery.py:182`) — ANY-reservation affinity, inert until a matching reservation exists, harmless otherwise; (c) **strongest: a Compute Engine reservation for the exact shape (`g2-standard-8`, 1× `nvidia-l4`, europe-west3-a or -b) + that experiment** — the reservation holds the capacity between runs and the experiment makes Dataflow consume it. Document the `gcloud compute reservations create` one-liner (`--machine-type=g2-standard-8 --accelerator=count=1,type=nvidia-l4 --zone=europe-west3-b --vm-count=1`), the prerequisite platform-team allowlist for GPU-targeted Dataflow reservations noted in the DAG comment, the cost caveat (a reservation bills while idle — create before a run campaign, delete after), and that `SPECIFIC_RESERVATION` targeting is NOT used because the experiment channel only supports ANY-affinity.
  4. **After every run** — the three-command report recipe (analysis → probe → bundle export) copied from the prompt file, with `--batch-size` and `--identity-cols` now included.
- [ ] **Step 2: Link it** — add a RUN_PLAYBOOK bullet to the "When in doubt" list in `CLAUDE.md` and a pointer in `docs/DEPLOYMENT_PREREQUISITES.md`'s intro.
- [ ] **Step 3: Commit** — `git commit -m "docs: RUN_PLAYBOOK — GPU verdict, run matrix, Dataflow options, report recipe"`

---

### Task 13: ACTION_4 design spec — RAG layer

**Files:**
- Create: `docs/designs/2026-07-07-rag-layer-design.md` (tracked dir — `docs/superpowers/` is gitignored and would not reach the M4)

- [ ] **Step 1: Write the spec** covering, with concrete DDL and dataflow diagrams in text form:
  - **Goal:** persist embeddings once, reuse everywhere (synthetic generator, GenAI apps, KG/ontology, chatbots); stop re-embedding per run.
  - **Storage:** dataset `synthetic_rag`; table `rag_chunks` with DDL: `chunk_id STRING (blake2 of source_fqn+row_digest+chunk_index)`, `source_fqn STRING`, `source_pk JSON` (PK column→value map for joining back), `row_digest STRING`, `reference_digest STRING`, `chunk_index INT64`, `chunk_kind STRING (row_doc|free_text_col)`, `chunk_text STRING`, `embedder_id STRING`, `embedder_version STRING`, `embedding ARRAY<FLOAT64>`, `metadata JSON`, `created_at TIMESTAMP`; `CREATE VECTOR INDEX … ON rag_chunks(embedding) OPTIONS(index_type='IVF', distance_type='COSINE')`; retrieval via `VECTOR_SEARCH` for downstream apps.
  - **Population:** a Beam stage/job (`--build-rag-layer`) reading the reference sample → chunker (row-as-document serialization for tabular rows; per-free-text-column chunks) → `RunInference` with the existing embedder ModelHandler → `WriteToBigQuery` FILE_LOADS. Idempotent per `reference_digest`: skip when rows for the digest already exist.
  - **Generation-time path:** B.1 reads `chunk_text`+`embedding` for its reference_digest from BQ and builds FAISS locally (no re-embed); fallback to today's embed-on-worker when the table has no rows for the digest.
  - **Chunking/retrieval standards:** exact-kNN FlatIP locally; cosine + normalized embeddings; top-k documented; embedder pinned by `(embedder_id, embedder_version)` so mixed-version vectors never co-mingle in one index.
  - **Constraints:** no Vertex; embedder self-hosted; single-table M1 semantics; source linkage always possible via `source_pk`/`row_digest`.
  - **Out of scope:** cross-table graphs, BQ remote models, external vector DBs.
- [ ] **Step 2: Commit** — `git commit -m "docs: RAG layer design spec (BQ rag_chunks + VECTOR_SEARCH, idempotent embed job)"`

---

### Task 14: ACTION_5 design spec — evaluation framework

**Files:**
- Create: `docs/designs/2026-07-07-evaluation-framework-design.md`

- [ ] **Step 1: Write the spec** covering:
  - **Toggle:** `--enable-evaluation` pipeline arg (+ DAG param), default off; independent of the Mode-A gate.
  - **Execution:** post-`WriteLanding` branch → deterministic stratified sample (seeded by run_id; cap ~50k rows/side) of source + landing → `beam.CombineGlobally`-collected sample → ONE evaluation DoFn on a single worker → `validation_data_history` BQ row.
  - **Metric tiers:** T1 always-on scipy/sklearn: KS + Wasserstein (numeric marginals), TVD (categorical), PSI + JSD (drift between engine runs), Pearson/Spearman correlation-matrix diff, mutual-information-matrix diff, DCR (Gower-ish mixed distance), NNDR, identical-match rate; T2 SDMetrics QualityReport + DiagnosticReport (incl. NewRowSynthesis); T3 optional `[eval-extra]`: SynthEval (native Gower DCR/NNDR), Evidently (drift history HTML artifact to GCS). TSTR (train-on-synthetic-test-on-real, LightGBM F1/RMSE delta): documented, deferred, non-priority.
  - **History table DDL:** `execution_id STRING`, `execution_timestamp TIMESTAMP`, `run_id STRING`, `engine STRING`, `engine_version STRING`, `feature_flag_tags ARRAY<STRING>`, `sample_rows_real INT64`, `sample_rows_synthetic INT64`, `fidelity_overall_score FLOAT64`, `avg_dcr FLOAT64`, `nndr FLOAT64`, `identical_match_rate FLOAT64`, `max_psi FLOAT64`, `corr_diff_frobenius FLOAT64`, `tstr_f1_delta FLOAT64 (NULLABLE)`, `raw_metrics_json JSON`.
  - **Gate integration:** memorization guard rule `memorization.copy_ratio` (MAJOR in dev, BLOCKER in prd) computed here from the sampled copy-ratio; regression tracking = compare against the previous row per (table, engine).
  - **Packaging:** metrics code in `sdfb-core` (pure, laptop-testable with fixture DataFrames); the Beam branch in `sdfb-beam`; heavy deps in the existing optional-extras pattern.
- [ ] **Step 2: Commit** — `git commit -m "docs: evaluation framework design spec (tiered metrics, validation_data_history)"`

---

### Task 15: Prompt-file refresh + cross-links + final verification

**Files:**
- Modify: `.github/prompts/end_to_end_validation_report_generation.prompt.md`
- Modify: `docs/ROADMAP.md`, `CLAUDE.md` (links only)

- [ ] **Step 1: Refresh the prompt file** (it was vendored verbatim in Task 7):
  - Step 1 reading list: fix the workflow filename to `.github/workflows/3_import_dag.yaml` (this repo's name); add `sdfb_core/observability.py` and `docs/RUN_PLAYBOOK.md`.
  - Step 2 command: add `--batch-size <BATCH_SIZE>`.
  - Step 3: note milestones now arrive as `sdfb.*` names via the `SDFB_MILESTONE` contract (legacy regexes only for pre-contract jobs); add `--engine-label` and `--run-id` to the example command.
  - Step 4 diagnosis list: replace the `_mix_seed(None)→0` bullet with "seed replay is fixed by `derive_batch_seed`; if run-lengths still equal batch size, check `sdfb.batch_start` seeds in worker logs"; add "check `sdfb.freetext_llm_fallback` count — any occurrence means the LLM contributed nothing for that column"; add "a `vllm_error`/`ModelGpuIncompatibleError` line means the job should have died — if it PASSED instead, the guard regressed".
  - Add a one-line pointer to the future ACTION_5 suite: "when `--enable-evaluation` lands, pull `validation_data_history` instead of hand-computing fidelity."
- [ ] **Step 2: Cross-links** — ROADMAP: add this cycle under M1/M2 notes with the two design-spec links; CLAUDE.md "When in doubt": add `docs/RUN_PLAYBOOK.md` (how to run) and `.github/prompts/end_to_end_validation_report_generation.prompt.md` (how to validate a run).
- [ ] **Step 3: Final verification**

```bash
uv run pytest -m "not gpu and not gcp" -q     # expect: all green (218 + ~30 new)
uv run ruff check .                            # expect: no findings
git log --oneline master..HEAD 2>/dev/null || git log --oneline -12
```

- [ ] **Step 4: Commit** — `git commit -m "docs: refresh e2e report prompt for milestone contract; cross-link playbook + specs"`
