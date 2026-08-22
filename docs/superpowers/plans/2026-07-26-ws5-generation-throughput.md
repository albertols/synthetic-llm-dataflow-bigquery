# WS5 — Generation Throughput & RAG Seeding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Design: [`docs/designs/2026-07-26-ws5-generation-throughput.md`](../../designs/2026-07-26-ws5-generation-throughput.md).
> Evidence: `integration_tests/2026-07-26_06_54_25-14348390798392809440/` (1M rows, 68 min).
> Docs written under this plan follow the `visual-first-documentation` skill.

**Goal:** Make a cold 1M-row B.1 run finish in under 20 minutes by building free-text pools once per reference digest instead of once per worker process, and by removing per-call array re-materialization from the generation hot path.

**Architecture:** Free-text pools become a persisted artifact in BigQuery (`synthetic_rag.freetext_pools`), produced by a dedicated DAG branch exactly as `rag_chunks` already is, and read by `Generate.setup()` in ~2 s. `Generate` stops constructing a `ModelClient` and stops touching CUDA entirely. Independently, `ColumnSampler` hoists its derived NumPy arrays from per-call to per-construction.

**Tech Stack:** Apache Beam 2.74 (Python SDK), BigQuery, vLLM (L4/T4), NumPy, pytest.

## Global Constraints

- No Vertex AI, no Dataplex/Looker, no external LLM APIs, no HuggingFace Hub at runtime (CLAUDE.md hard constraints).
- `sdfb-core` must not import `apache_beam`, `google.cloud.*`, or `torch`. Protocols live in core; BigQuery implementations live in `sdfb-beam`.
- Engines are built in `DoFn.setup()`, never in `process()`.
- `WriteToBigQuery` uses `FILE_LOADS`, never `STREAMING_INSERTS`.
- Validation failures route to a tagged DLQ output with full error context — never silently dropped.
- Laptop test command: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q` (currently **619 passed**). Lint: `uv run --no-sync ruff check .`
- `COL_047` / `COL_048` is a known special case (binary characters) and is **excluded from all acceptance measurements** — per user instruction 2026-07-26.

## Sequencing note (read before starting)

Phase 1 is independently shippable and does not change generation semantics.
Ship and re-measure Phase 1+2 **before** starting Phase 3, so the three-arm
seed comparison in Phase 3 varies exactly one thing. Running everything in one
build confounds the experiment.

## File Structure

| File | Responsibility |
|---|---|
| `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` | DDL fallback (T1), batch sizing (T3), flag threading (T9), DAG branch wiring (T7) |
| `packages/sdfb-core/src/sdfb_core/engines/b1_rag/_fidelity.py` | hoisted sampler arrays (T2) |
| `packages/sdfb-core/src/sdfb_core/pools/store.py` *(new)* | `FreeTextPoolStore` Protocol + `InMemoryFreeTextPoolStore` (T4) |
| `packages/sdfb-core/src/sdfb_core/pools/record.py` *(new)* | `FreeTextPool` dataclass (T4) |
| `packages/sdfb-beam/src/sdfb_beam/pools/store.py` *(new)* | `BigQueryFreeTextPoolStore` (T5) |
| `config/bq_schema/synthetic_rag/freetext_pools.schema.json` *(new)* | BQ table schema (T5) |
| `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` | store-first pool resolution (T6), seed strategy (T9) |
| `packages/sdfb-beam/src/sdfb_beam/dofns/pools.py` *(new)* | `BuildFreeTextPoolsDoFn` (T7) |
| `packages/sdfb-core/src/sdfb_core/rag/retrieval.py` | `retrieve_kcenter_k` (T8) |
| `docs/adr/0020-freetext-pools-as-persisted-artifact.md` *(new)* | decision record (T10) |

---

## PHASE 1 — Independent wins (no architecture change)

### Task 1: `--ddl_uri` miss falls through to live extraction

TEST_1 (`2026-07-25_16_38_10`) died at template launch, before any worker
started, because a pinned DDL object did not exist.

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py:227-246`
- Test: `packages/sdfb-tests/tests/unit/cli/test_resolve_table_schema.py` (create)

**Interfaces:**
- Consumes: `load_ddl(ddl_uri) -> TableSchema`, `extract_table_schema(reference_table) -> TableSchema` (both already exist).
- Produces: `resolve_table_schema(ddl_uri: str, reference_table: str) -> TableSchema` — unchanged signature; new milestone `ddl_uri_miss_fallback`.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/cli/test_resolve_table_schema.py`:

```python
"""--ddl_uri precedence and the 404 fallback (TEST_1, 2026-07-25 16:38)."""

from __future__ import annotations

import json

import pytest

from sdfb_beam.cli import run_pipeline
from sdfb_core.contracts.schema import ColumnSpec, TableSchema


def _schema(name: str) -> TableSchema:
    return TableSchema(
        table="t",
        columns=[ColumnSpec(name=name, bq_type="STRING", mode="NULLABLE")],
    )


def test_explicit_ddl_uri_still_wins_when_it_resolves(monkeypatch):
    monkeypatch.setattr(run_pipeline, "load_ddl", lambda uri: _schema("from_pin"))
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live"),
        raising=False,
    )
    got = run_pipeline.resolve_table_schema("gs://b/ddl.json", "p.d.t")
    assert got.columns[0].name == "from_pin"


def test_missing_ddl_uri_falls_back_to_live_extraction(monkeypatch):
    """A 404 on the pin must NOT kill the launch — the source table is the
    authority and extract_table_schema already exists."""
    from google.api_core.exceptions import NotFound

    def _boom(uri):
        raise NotFound("No such object: synthetic/ddls/ddl_metadata_X.json")

    monkeypatch.setattr(run_pipeline, "load_ddl", _boom)
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live"),
        raising=False,
    )
    got = run_pipeline.resolve_table_schema("gs://b/missing.json", "p.d.t")
    assert got.columns[0].name == "from_live"


def test_corrupt_ddl_still_fails_loudly(monkeypatch):
    """Silently ignoring a MALFORMED pin is worse than the 404 — the operator
    asked for that exact schema and got something else."""

    def _bad(uri):
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(run_pipeline, "load_ddl", _bad)
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live"),
        raising=False,
    )
    with pytest.raises(json.JSONDecodeError):
        run_pipeline.resolve_table_schema("gs://b/corrupt.json", "p.d.t")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/test_resolve_table_schema.py -v`
Expected: `test_missing_ddl_uri_falls_back_to_live_extraction` FAILS with `NotFound` propagating out of `resolve_table_schema`.

- [ ] **Step 3: Implement**

In `run_pipeline.py`, add above `resolve_table_schema` (and move the
`extract_table_schema` import to module scope so tests can monkeypatch it):

```python
from sdfb_beam.ddl import extract_table_schema

# A pinned --ddl_uri that does not EXIST is an operational miss (new source
# table, DDL not yet exported) and must degrade to live extraction: TEST_1
# (2026-07-25 16:38) died at template launch on a 404 with a perfectly good
# source table available. A pin that exists but is CORRUPT is a different
# failure — the operator asked for that exact schema — and still raises.
_MISSING_DDL_MARKERS = ("notfound", "no such object", "404", "filenotfound")


def _is_missing_ddl(exc: BaseException) -> bool:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, FileNotFoundError):
            return True
        blob = f"{type(cur).__name__} {cur}".lower()
        if any(m in blob for m in _MISSING_DDL_MARKERS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False
```

Replace the body of `resolve_table_schema`:

```python
def resolve_table_schema(ddl_uri: str, reference_table: str) -> TableSchema:
    """WS4 §6b precedence: explicit ``--ddl_uri`` (pin/air-gap) > live
    INFORMATION_SCHEMA extraction from the source table. A MISSING pin
    falls through (WS5 T1); a corrupt one still raises."""
    if ddl_uri:
        logger.info("Loading DDL from %s", ddl_uri)
        try:
            schema = load_ddl(ddl_uri)
        except Exception as exc:
            if not _is_missing_ddl(exc):
                raise
            logger.warning(
                "DDL pin %s not found; live-extracting from %s",
                ddl_uri,
                reference_table,
            )
            log_milestone(
                "ddl_uri_miss_fallback",
                uri=ddl_uri,
                table=reference_table,
                error=type(exc).__name__,
            )
        else:
            log_milestone("ddl_loaded_from_uri", uri=ddl_uri)
            return schema
    else:
        logger.info("No --ddl_uri; live-extracting schema from %s", reference_table)

    schema = extract_table_schema(reference_table)
    log_milestone(
        "ddl_live_extracted",
        table=reference_table,
        columns=len(schema.columns),
    )
    return schema
```

Delete the now-duplicated function-local `from sdfb_beam.ddl import extract_table_schema`.

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/ -v`
Expected: all PASS.

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py packages/sdfb-tests/tests/unit/cli/test_resolve_table_schema.py
git commit -m "fix(cli): missing --ddl_uri falls back to live extraction (TEST_1)"
```

**Operator note (docs only, no code):** target-table auto-create already works
via `create_if_not_exists=true` (`derive_bq_load_schema` + `CREATE_IF_NEEDED`,
confined to the landing sink). TEST_1 passed `false`. Record this in
`docs/RUN_PLAYBOOK.md` in Task 10; do **not** flip the default.

---

### Task 2: Hoist `ColumnSampler`'s derived arrays out of the hot path

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/_fidelity.py:64-66,168-190`
- Test: `packages/sdfb-tests/tests/unit/engines/test_sampler_hoisting.py` (create)

**Interfaces:**
- Produces: `ColumnSampler._numeric_obs_array(np)` and `ColumnSampler._temporal_obs_array(np)`, both returning a cached `np.ndarray`. `sample_numpy` signature is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/engines/test_sampler_hoisting.py`:

```python
"""Derived sampler arrays are built once per sampler, not once per call.

2026-07-26 1M E2E: 42 INT64 columns x ~10k observed values were
re-materialized on EVERY generate_batch() call (62,500 elements at
batch_size=16) — ~3,900 CPU-seconds of pure waste. The profile is frozen
for the sampler's lifetime, so its derived arrays must be too.
"""

from __future__ import annotations

import numpy as np

from sdfb_core.engines.b1_rag._fidelity import ColumnSampler
from sdfb_core.engines.b1_rag.profile import ColumnKind, ColumnProfile


def _numeric_profile() -> ColumnProfile:
    return ColumnProfile(
        name="amount",
        bq_type="INT64",
        kind=ColumnKind.NUMERIC,
        nullable=False,
        null_fraction=0.0,
        numeric_min=0.0,
        numeric_max=999.0,
        observed_values=tuple(range(1000)),
    )


def _temporal_profile() -> ColumnProfile:
    return ColumnProfile(
        name="booked_at",
        bq_type="STRING",
        kind=ColumnKind.TEMPORAL,
        nullable=False,
        null_fraction=0.0,
        temporal_format="%Y-%m-%d",
        numeric_min=1_600_000_000.0,
        numeric_max=1_700_000_000.0,
        observed_values=tuple(f"2021-01-{d:02d}" for d in range(1, 29)),
    )


def test_numeric_observed_array_built_once_across_calls():
    sampler = ColumnSampler(_numeric_profile())
    rng = np.random.default_rng(0)
    sampler.sample_numpy(rng, 4, 0.5)
    first = sampler._numeric_array
    assert first is not None
    sampler.sample_numpy(rng, 4, 0.5)
    assert sampler._numeric_array is first, "numeric array re-materialized"


def test_temporal_observed_array_built_once_across_calls():
    """The float LIST was already memoized, but np.asarray() ran per call —
    a 10k-element conversion x 62,500 elements x every temporal column."""
    sampler = ColumnSampler(_temporal_profile())
    rng = np.random.default_rng(0)
    sampler.sample_numpy(rng, 4, 0.5)
    first = sampler._temporal_array
    assert first is not None
    sampler.sample_numpy(rng, 4, 0.5)
    assert sampler._temporal_array is first, "temporal array re-materialized"


def test_hoisting_preserves_sampled_values_exactly():
    """Same seed, same draws — hoisting is a pure performance change."""
    prof = _numeric_profile()
    rng_a = np.random.default_rng(1234)
    rng_b = np.random.default_rng(1234)
    a = ColumnSampler(prof).sample_numpy(rng_a, 32, 0.7)
    b = ColumnSampler(prof).sample_numpy(rng_b, 32, 0.7)
    assert a == b
    assert all(0 <= v <= 999 for v in a)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_sampler_hoisting.py -v`
Expected: the first two FAIL with `AttributeError: 'ColumnSampler' object has no attribute '_numeric_array'`.

- [ ] **Step 3: Implement**

In `_fidelity.py`, replace `__init__`:

```python
    def __init__(self, profile: ColumnProfile) -> None:
        self.profile = profile
        # Derived views of the (frozen) profile. Built on first use and kept
        # for the sampler's lifetime: the 2026-07-26 1M E2E rebuilt these on
        # EVERY generate_batch() call. `_temporal_floats` was already
        # memoized, but the np.asarray() around it was not.
        self._temporal_floats: list[float] | None = None
        self._numeric_array = None  # np.ndarray | None
        self._temporal_array = None  # np.ndarray | None
```

Add both accessors next to `_temporal_obs_floats`:

```python
    def _numeric_obs_array(self, np):
        """Observed NUMERIC values as a float64 array (built once)."""
        if self._numeric_array is None:
            self._numeric_array = np.asarray(
                [float(x) for x in self.profile.observed_values if _is_number(x)],
                dtype="float64",
            )
        return self._numeric_array

    def _temporal_obs_array(self, np):
        """Observed TEMPORAL values as a float64 array (built once)."""
        if self._temporal_array is None:
            self._temporal_array = np.asarray(
                self._temporal_obs_floats(), dtype="float64"
            )
        return self._temporal_array
```

Then use them. In `_numeric_numpy`, replace the `obs = np.asarray([...])` line with:

```python
        obs = self._numeric_obs_array(np)
```

In `_temporal_numpy`, replace `obs = np.asarray(self._temporal_obs_floats(), dtype="float64")` with:

```python
        obs = self._temporal_obs_array(np)
```

Leave `_categorical_numpy` alone: `categories` is capped at 50 (`_FREE_TEXT_MAX_CATEGORIES`) / 20 (`_TEMPORAL_MAX_CATEGORIES`, `_NUMERIC_MAX_CATEGORIES`) in `profile.py`, so its per-call rebuild is bounded and measured in single-digit CPU-seconds across the whole job. Hoisting it would be churn without benefit.

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/ packages/sdfb-tests/tests/unit/rag/ -q`
Expected: all PASS (existing fidelity tests must be unaffected — this is a pure performance change).

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/engines/b1_rag/_fidelity.py packages/sdfb-tests/tests/unit/engines/test_sampler_hoisting.py
git commit -m "perf(b1): hoist observed-value arrays out of the sampling hot path"
```

---

### Task 3: Scale `batch_size` with `num_rows`

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py:102`
- Test: `packages/sdfb-tests/tests/unit/cli/test_batch_sizing.py` (create)

**Interfaces:**
- Produces: `resolve_batch_size(requested: int, num_rows: int) -> int`.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/cli/test_batch_sizing.py`:

```python
"""batch_size=16 over 1M rows = 62,500 elements (2026-07-26 E2E)."""

from __future__ import annotations

from sdfb_beam.cli.run_pipeline import DEFAULT_BATCH_SIZE, resolve_batch_size


def test_small_runs_keep_todays_batch_size():
    assert resolve_batch_size(DEFAULT_BATCH_SIZE, 1_000) == DEFAULT_BATCH_SIZE


def test_million_row_run_scales_up():
    got = resolve_batch_size(DEFAULT_BATCH_SIZE, 1_000_000)
    assert got >= 1_000
    assert 1_000_000 / got <= 2_000, "still too many elements"


def test_explicit_override_is_respected():
    assert resolve_batch_size(64, 1_000_000) == 64


def test_never_below_the_floor():
    assert resolve_batch_size(DEFAULT_BATCH_SIZE, 1) == DEFAULT_BATCH_SIZE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/test_batch_sizing.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_batch_size'`.

- [ ] **Step 3: Implement**

In `run_pipeline.py`, add near the other module constants:

```python
# batch_size is rows-per-element. At the historic fixed default of 16, a 1M-row
# run produced 62,500 elements and paid per-element Python overhead 62,500
# times over (2026-07-26 E2E). Target ~1,000 elements; never go below the
# historic default so small runs and their goldens are untouched.
DEFAULT_BATCH_SIZE = 16
_TARGET_ELEMENTS = 1_000


def resolve_batch_size(requested: int, num_rows: int) -> int:
    """Explicit non-default --batch_size always wins; otherwise scale."""
    if requested != DEFAULT_BATCH_SIZE:
        return requested
    if num_rows <= 0:
        return DEFAULT_BATCH_SIZE
    return max(DEFAULT_BATCH_SIZE, num_rows // _TARGET_ELEMENTS)
```

Change the argument default to reference the constant:

```python
    p.add_argument("--batch_size", type=int, default=DEFAULT_BATCH_SIZE)
```

At the call site (currently `batch_size=args.batch_size`, ~line 414):

```python
        batch_size=resolve_batch_size(args.batch_size, args.num_rows),
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/ -q`
Expected: PASS.

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py packages/sdfb-tests/tests/unit/cli/test_batch_sizing.py
git commit -m "perf(dag): scale batch_size with num_rows (62,500 -> ~1,000 elements at 1M)"
```

**Checkpoint:** ship Phase 1 and re-run the 1M E2E before starting Phase 3.
Record the new `dofn_setup_done` total and wall clock — Phase 2's targets are
measured against this, not against the 68-minute baseline.

---

## PHASE 2 — Pools become a persisted artifact

### Task 4: `FreeTextPool` record + `FreeTextPoolStore` Protocol (core)

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/pools/__init__.py`
- Create: `packages/sdfb-core/src/sdfb_core/pools/record.py`
- Create: `packages/sdfb-core/src/sdfb_core/pools/store.py`
- Test: `packages/sdfb-tests/tests/unit/pools/test_pool_store.py` (create)

**Interfaces:**
- Produces:
  - `FreeTextPool(reference_digest: str, model_uri: str, column: str, target: int, values: tuple[str, ...], stagnated: bool, attempts: int)`
  - `FreeTextPoolStore.fetch(reference_digest, model_uri) -> list[FreeTextPool]`
  - `FreeTextPoolStore.exists(reference_digest, model_uri) -> bool`
  - `InMemoryFreeTextPoolStore(pools=())` with `.add(pools)`

Deliberately a **sibling** of `ChunkStore`, not a `chunk_kind`: pools key on
`model_uri` (which LLM produced them), chunks key on
`embedder_id`/`embedder_version` (which vector space). One table cannot answer
both questions honestly.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/pools/test_pool_store.py`:

```python
"""The free-text pool store seam (WS5 T4)."""

from __future__ import annotations

from sdfb_core.pools.record import FreeTextPool
from sdfb_core.pools.store import FreeTextPoolStore, InMemoryFreeTextPoolStore


def _pool(column: str, digest: str = "d1", model: str = "m1", **kw) -> FreeTextPool:
    return FreeTextPool(
        reference_digest=digest,
        model_uri=model,
        column=column,
        target=kw.pop("target", 8),
        values=kw.pop("values", ("a", "b")),
        stagnated=kw.pop("stagnated", False),
        attempts=kw.pop("attempts", 1),
    )


def test_in_memory_store_satisfies_the_protocol():
    assert isinstance(InMemoryFreeTextPoolStore(), FreeTextPoolStore)


def test_fetch_filters_by_digest_and_model():
    store = InMemoryFreeTextPoolStore(
        [_pool("a"), _pool("b", digest="other"), _pool("c", model="other")]
    )
    got = store.fetch("d1", "m1")
    assert [p.column for p in got] == ["a"]


def test_exists_is_false_for_unknown_digest():
    store = InMemoryFreeTextPoolStore([_pool("a")])
    assert store.exists("d1", "m1") is True
    assert store.exists("nope", "m1") is False


def test_stagnation_is_recorded_not_rediscovered():
    """A pool that stagnated at 8 values is stored AS stagnated, with the
    attempt count that proved it — so the next worker never re-runs the
    ladder to learn the same thing (2026-07-26 E2E: 36x per column)."""
    store = InMemoryFreeTextPoolStore(
        [_pool("k", stagnated=True, attempts=12, values=tuple("abcdefgh"))]
    )
    got = store.fetch("d1", "m1")[0]
    assert got.stagnated is True
    assert got.attempts == 12
    assert len(got.values) == 8
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/pools/ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdfb_core.pools'`.

- [ ] **Step 3: Implement**

`packages/sdfb-core/src/sdfb_core/pools/__init__.py`:

```python
"""Persisted free-text pools (WS5). Protocol here; BigQuery impl in sdfb-beam."""
```

`packages/sdfb-core/src/sdfb_core/pools/record.py`:

```python
"""One column's generated free-text pool, as persisted (WS5 §2)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FreeTextPool:
    """A pool is identified by (reference_digest, model_uri, column, target).

    `model_uri` — NOT an embedder identity — because the values were produced
    by that LLM; a different model yields a different pool for the same rows.
    `stagnated`/`attempts` record what the ladder LEARNED, so a later worker
    never re-derives it: the 2026-07-26 1M run spent 68,805 s of LLM service
    time re-discovering the same three pools 36 times.
    """

    reference_digest: str
    model_uri: str
    column: str
    target: int
    values: tuple[str, ...]
    stagnated: bool = False
    attempts: int = 0
```

`packages/sdfb-core/src/sdfb_core/pools/store.py`:

```python
"""The `FreeTextPoolStore` seam (WS5 §2.1).

Sibling of `ChunkStore`, not a `chunk_kind` of it: pools key on `model_uri`
(which LLM produced the values), chunks key on `embedder_id`/`embedder_version`
(which vector space). One fetch signature cannot serve both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

    from sdfb_core.pools.record import FreeTextPool


@runtime_checkable
class FreeTextPoolStore(Protocol):
    """Read surface over `synthetic_rag.freetext_pools`."""

    def fetch(self, reference_digest: str, model_uri: str) -> list[FreeTextPool]:
        """Every persisted pool for this digest + LLM."""
        ...

    def exists(self, reference_digest: str, model_uri: str) -> bool:
        """True when ANY pool exists (the build stage's idempotency check)."""
        ...


class InMemoryFreeTextPoolStore:
    """List-backed store for tests and laptop DirectRunner runs."""

    def __init__(self, pools: Iterable[FreeTextPool] = ()) -> None:
        self._pools: list[FreeTextPool] = list(pools)

    def add(self, pools: Iterable[FreeTextPool]) -> None:
        self._pools.extend(pools)

    def fetch(self, reference_digest: str, model_uri: str) -> list[FreeTextPool]:
        return [
            p
            for p in self._pools
            if p.reference_digest == reference_digest and p.model_uri == model_uri
        ]

    def exists(self, reference_digest: str, model_uri: str) -> bool:
        return any(
            p.reference_digest == reference_digest and p.model_uri == model_uri
            for p in self._pools
        )


__all__ = ["FreeTextPoolStore", "InMemoryFreeTextPoolStore"]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/pools/ -v`
Expected: PASS.

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/pools packages/sdfb-tests/tests/unit/pools
git commit -m "feat(pools): FreeTextPool record + FreeTextPoolStore Protocol (WS5 T4)"
```

---

### Task 5: `BigQueryFreeTextPoolStore` + table schema (beam)

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/pools/__init__.py`
- Create: `packages/sdfb-beam/src/sdfb_beam/pools/store.py`
- Create: `config/bq_schema/synthetic_rag/freetext_pools.schema.json`
- Test: `packages/sdfb-tests/tests/unit/pools/test_bq_pool_store.py` (create)

**Interfaces:**
- Consumes: `FreeTextPool`, `FreeTextPoolStore` from Task 4.
- Produces: `BigQueryFreeTextPoolStore(table_fqn: str, client=None)`; `pool_to_row(pool) -> dict`; `row_to_pool(row) -> FreeTextPool`.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/pools/test_bq_pool_store.py`:

```python
"""Row <-> FreeTextPool mapping and the BQ-backed store (WS5 T5)."""

from __future__ import annotations

from sdfb_beam.pools.store import BigQueryFreeTextPoolStore, pool_to_row, row_to_pool
from sdfb_core.pools.record import FreeTextPool


class _FakeBQ:
    """Stands in for google.cloud.bigquery.Client — no GCP in unit tests."""

    def __init__(self, rows):
        self._rows = rows
        self.queries: list[str] = []

    def query(self, sql, job_config=None):  # noqa: ARG002
        self.queries.append(sql)
        return self._rows


def _pool() -> FreeTextPool:
    return FreeTextPool(
        reference_digest="d1",
        model_uri="gs://b/m",
        column="notes",
        target=64,
        values=("alpha", "beta"),
        stagnated=True,
        attempts=9,
    )


def test_row_round_trip_preserves_every_field():
    assert row_to_pool(pool_to_row(_pool())) == _pool()


def test_values_persist_as_a_repeated_field_not_a_blob():
    row = pool_to_row(_pool())
    assert row["values"] == ["alpha", "beta"]


def test_fetch_maps_rows_and_filters_by_digest_and_model():
    store = BigQueryFreeTextPoolStore(
        "p.synthetic_rag.freetext_pools", client=_FakeBQ([pool_to_row(_pool())])
    )
    got = store.fetch("d1", "gs://b/m")
    assert got == [_pool()]


def test_exists_is_false_on_empty_result():
    store = BigQueryFreeTextPoolStore(
        "p.synthetic_rag.freetext_pools", client=_FakeBQ([])
    )
    assert store.exists("d1", "gs://b/m") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/pools/test_bq_pool_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdfb_beam.pools'`.

- [ ] **Step 3: Implement**

`config/bq_schema/synthetic_rag/freetext_pools.schema.json`:

```json
{
  "fields": [
    {"name": "reference_digest", "type": "STRING", "mode": "REQUIRED",
     "description": "Canonical provenance digest of the reference sample the pool was inferred from."},
    {"name": "model_uri", "type": "STRING", "mode": "REQUIRED",
     "description": "LLM weights URI that produced these values; a different model is a different pool."},
    {"name": "column", "type": "STRING", "mode": "REQUIRED",
     "description": "Free-text column the pool belongs to."},
    {"name": "target", "type": "INT64", "mode": "REQUIRED",
     "description": "Requested pool size: min(num_rows, column_distinct, _FREE_TEXT_POOL_MAX)."},
    {"name": "values", "type": "STRING", "mode": "REPEATED",
     "description": "Deduped generated values, insertion-ordered."},
    {"name": "stagnated", "type": "BOOL", "mode": "REQUIRED",
     "description": "True when the ladder stopped yielding novel values before reaching target — recorded so later workers never re-derive it."},
    {"name": "attempts", "type": "INT64", "mode": "REQUIRED",
     "description": "Ladder attempts spent producing this pool."},
    {"name": "built_at", "type": "TIMESTAMP", "mode": "NULLABLE",
     "description": "Build wall-clock time (observability only; never a fetch key)."}
  ]
}
```

`packages/sdfb-beam/src/sdfb_beam/pools/__init__.py`:

```python
"""BigQuery-backed free-text pool persistence (WS5)."""
```

`packages/sdfb-beam/src/sdfb_beam/pools/store.py`:

```python
"""BigQuery implementation of `FreeTextPoolStore` (WS5 T5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sdfb_core.pools.record import FreeTextPool

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


def pool_to_row(pool: FreeTextPool) -> dict[str, Any]:
    return {
        "reference_digest": pool.reference_digest,
        "model_uri": pool.model_uri,
        "column": pool.column,
        "target": pool.target,
        "values": list(pool.values),
        "stagnated": pool.stagnated,
        "attempts": pool.attempts,
    }


def row_to_pool(row: Any) -> FreeTextPool:
    get = row.get if hasattr(row, "get") else row.__getitem__
    return FreeTextPool(
        reference_digest=get("reference_digest"),
        model_uri=get("model_uri"),
        column=get("column"),
        target=int(get("target")),
        values=tuple(get("values") or ()),
        stagnated=bool(get("stagnated")),
        attempts=int(get("attempts")),
    )


class BigQueryFreeTextPoolStore:
    """Read surface over `synthetic_rag.freetext_pools`.

    Exactly one query per worker setup — the whole point of WS5 is that the
    generation stage reads pools instead of inferring them.
    """

    def __init__(self, table_fqn: str, client: Any = None) -> None:
        self._table_fqn = table_fqn
        self._client = client

    def _bq(self) -> Any:
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client()
        return self._client

    def fetch(self, reference_digest: str, model_uri: str) -> list[FreeTextPool]:
        sql = (
            f"SELECT reference_digest, model_uri, column, target, values, "  # noqa: S608
            f"stagnated, attempts FROM `{self._table_fqn}` "
            "WHERE reference_digest = @digest AND model_uri = @model"
        )
        rows = self._run(sql, reference_digest, model_uri)
        return [row_to_pool(r) for r in rows]

    def exists(self, reference_digest: str, model_uri: str) -> bool:
        return bool(self.fetch(reference_digest, model_uri))

    def _run(self, sql: str, digest: str, model: str) -> list[Any]:
        client = self._bq()
        try:
            from google.cloud import bigquery

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("digest", "STRING", digest),
                    bigquery.ScalarQueryParameter("model", "STRING", model),
                ]
            )
        except ImportError:  # unit tests inject a fake client
            job_config = None
        return list(client.query(sql, job_config=job_config))


__all__ = ["BigQueryFreeTextPoolStore", "pool_to_row", "row_to_pool"]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/pools/ -v`
Expected: PASS.

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-beam/src/sdfb_beam/pools packages/sdfb-tests/tests/unit/pools/test_bq_pool_store.py
git commit -m "feat(pools): BigQueryFreeTextPoolStore + freetext_pools schema (WS5 T5)"
```

---

### Task 6: Engine resolves pools store-first

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`_build_free_text_pools`, ~line 400-430 and the cache write ~line 645-665)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py` (add `pool_store` to `GenerationContext`)
- Test: `packages/sdfb-tests/tests/unit/rag/test_pool_store_read.py` (create)

**Interfaces:**
- Consumes: `FreeTextPoolStore`, `FreeTextPool` (Task 4).
- Produces: `GenerationContext.pool_store: object | None`; milestones `freetext_pool_store_hit`, `freetext_pool_store_miss`.

Resolution order becomes **store → process cache → build**. The store is
authoritative and cross-process; the existing `_POOL_CACHE` stays as the
intra-process tier.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/rag/test_pool_store_read.py`:

```python
"""Pools come from the store when present — no LLM call (WS5 T6)."""

from __future__ import annotations

import pytest

from sdfb_core.engines.b1_rag.engine import B1RagEngine
from sdfb_core.pools.record import FreeTextPool
from sdfb_core.pools.store import InMemoryFreeTextPoolStore


class _ExplodingClient:
    """Any LLM call here is a bug: the pool was already persisted."""

    def complete(self, *a, **kw):  # noqa: ANN002, ANN003, ARG002
        raise AssertionError("LLM called despite a populated pool store")

    def setup(self) -> None:
        raise AssertionError("vLLM ignited despite a populated pool store")


def test_populated_store_short_circuits_the_ladder(b1_context_with_freetext):
    """b1_context_with_freetext: a GenerationContext whose schema has one
    FREE_TEXT column 'notes' and reference rows to profile from."""
    ctx = b1_context_with_freetext
    ctx = ctx.model_copy(
        update={
            "pool_store": InMemoryFreeTextPoolStore(
                [
                    FreeTextPool(
                        reference_digest=ctx.reference_digest,
                        model_uri=ctx.model_uri,
                        column="notes",
                        target=8,
                        values=("n1", "n2", "n3"),
                        stagnated=True,
                        attempts=11,
                    )
                ]
            )
        }
    )
    engine = B1RagEngine()
    engine.setup(_ExplodingClient(), ctx)
    rows = list(engine.generate_batch(5, engine_default_config()))
    assert rows
    assert {r.notes for r in rows} <= {"n1", "n2", "n3"}


def test_store_miss_falls_through_to_building(b1_context_with_freetext, fake_client):
    ctx = b1_context_with_freetext.model_copy(
        update={"pool_store": InMemoryFreeTextPoolStore([])}
    )
    engine = B1RagEngine()
    engine.setup(fake_client, ctx)  # must NOT raise — builds as before
    assert engine._free_text_pools["notes"]
```

> The two fixtures (`b1_context_with_freetext`, `fake_client`) and the
> `engine_default_config()` helper already exist in
> `packages/sdfb-tests/tests/unit/rag/conftest.py` — reuse them; if the names
> differ in your checkout, adopt the local ones rather than adding duplicates.

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_pool_store_read.py -v`
Expected: FAIL — `GenerationContext` has no `pool_store` field (pydantic rejects the `model_copy` update).

- [ ] **Step 3: Implement**

In `base.py`, add to `GenerationContext` beside the other RAG fields:

```python
    # --- persisted free-text pools (WS5 §2) -----------------------------
    # A `FreeTextPoolStore` (Protocol in sdfb_core.pools.store), attached
    # worker-side by the DoFn. When it already holds this digest+model's
    # pools, setup() reads them and never ignites vLLM: the 2026-07-26 1M
    # run rebuilt the same 3 pools 36 times for 68,805 s of LLM service
    # time. Typed `object` so sdfb-core stays import-light.
    pool_store: object | None = None
```

In `engine.py` `_build_free_text_pools`, before the per-column loop:

```python
        # Store tier (WS5): cross-PROCESS and authoritative. `_POOL_CACHE`
        # below stays as the intra-process tier — it still saves a rebuild
        # across setup() retries within one worker.
        stored: dict[str, FreeTextPool] = {}
        store = getattr(ctx, "pool_store", None)
        if store is not None and ctx.reference_digest:
            try:
                stored = {
                    p.column: p
                    for p in store.fetch(ctx.reference_digest, ctx.model_uri)
                }
            except Exception as exc:  # a store outage must not fail the run
                log_milestone("freetext_pool_store_error", error=type(exc).__name__)
```

Then, at the top of the per-column loop (before the `_POOL_CACHE` lookup):

```python
            hit = stored.get(prof.name)
            if hit is not None and hit.values:
                pools[prof.name] = list(hit.values)
                log_milestone(
                    "freetext_pool_store_hit",
                    column=prof.name,
                    pool_size=len(hit.values),
                    stagnated=hit.stagnated,
                    attempts=hit.attempts,
                )
                continue
            if store is not None:
                log_milestone("freetext_pool_store_miss", column=prof.name)
```

Add the import at the top of `engine.py`:

```python
from sdfb_core.pools.record import FreeTextPool
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/ -q`
Expected: PASS, including every existing pool test (a `None` store must behave exactly as today).

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/engines packages/sdfb-tests/tests/unit/rag/test_pool_store_read.py
git commit -m "feat(b1): resolve free-text pools store-first, then process cache (WS5 T6)"
```

---

### Task 7: `BuildFreeTextPools` branch; `Generate` drops the `ModelClient`

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/dofns/pools.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (DAG assembly + `--build_pool_layer` flag, mirroring `--build_rag_layer`)
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` (attach the store; skip `ModelClient` when pools are readable)
- Test: `packages/sdfb-tests/tests/unit/pools/test_pool_branch.py` (create)

**Interfaces:**
- Consumes: `BigQueryFreeTextPoolStore` (T5), `GenerationContext.pool_store` (T6).
- Produces: `BuildFreeTextPoolsDoFn(engine_factory, ctx_provider)` yielding `dict` rows shaped by `pool_to_row`; DAG flag `--build_pool_layer`.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/pools/test_pool_branch.py`:

```python
"""The pool-build branch writes rows and Generate stops needing an LLM."""

from __future__ import annotations

import apache_beam as beam
from apache_beam.testing.test_pipeline import TestPipeline
from apache_beam.testing.util import assert_that, equal_to

from sdfb_beam.dofns.pools import BuildFreeTextPoolsDoFn
from sdfb_beam.pools.store import pool_to_row
from sdfb_core.pools.record import FreeTextPool


def test_branch_emits_one_row_per_column(b1_context_with_freetext, fake_client):
    ctx = b1_context_with_freetext
    with TestPipeline() as p:
        rows = (
            p
            | beam.Create([None])
            | beam.ParDo(BuildFreeTextPoolsDoFn(lambda: fake_client, lambda: ctx))
        )
        assert_that(
            rows | beam.Map(lambda r: r["column"]), equal_to(["notes"])
        )


def test_emitted_rows_round_trip_through_the_store_mapping():
    pool = FreeTextPool(
        reference_digest="d",
        model_uri="m",
        column="notes",
        target=4,
        values=("a",),
        stagnated=False,
        attempts=2,
    )
    row = pool_to_row(pool)
    assert set(row) == {
        "reference_digest", "model_uri", "column",
        "target", "values", "stagnated", "attempts",
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/pools/test_pool_branch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdfb_beam.dofns.pools'`.

- [ ] **Step 3: Implement**

`packages/sdfb-beam/src/sdfb_beam/dofns/pools.py`:

```python
"""Build free-text pools ONCE, in their own branch (WS5 §2).

Mirrors the rag_chunks population branch: an independent stage that runs
concurrently with Generate and writes an artifact keyed on the reference
digest. Without it, every autoscaled worker process rebuilt all pools —
36 rebuilds and 68,805 s of LLM service time on the 2026-07-26 1M run.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import apache_beam as beam

from sdfb_beam.pools.store import pool_to_row
from sdfb_core.observability import log_milestone
from sdfb_core.pools.record import FreeTextPool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator


class BuildFreeTextPoolsDoFn(beam.DoFn):
    """Runs the pool ladder once and emits one BQ row per free-text column."""

    def __init__(self, client_factory, ctx_provider) -> None:
        self._client_factory = client_factory
        self._ctx_provider = ctx_provider
        self._engine: Any = None
        self._ctx: Any = None

    def setup(self) -> None:
        from sdfb_core.engines.b1_rag.engine import B1RagEngine

        self._ctx = self._ctx_provider()
        self._engine = B1RagEngine()
        t0 = time.monotonic()
        self._engine.setup(self._client_factory(), self._ctx)
        log_milestone(
            "pool_branch_setup_done", seconds=round(time.monotonic() - t0, 1)
        )

    def process(self, _element) -> Iterator[dict]:
        pools = self._engine._free_text_pools or {}
        for column, values in pools.items():
            yield pool_to_row(
                FreeTextPool(
                    reference_digest=self._ctx.reference_digest,
                    model_uri=self._ctx.model_uri,
                    column=column,
                    target=len(values),
                    values=tuple(values),
                    stagnated=bool(
                        getattr(self._engine, "_pool_stagnated", {}).get(column, False)
                    ),
                    attempts=int(
                        getattr(self._engine, "_pool_attempts", {}).get(column, 0)
                    ),
                )
            )

    def teardown(self) -> None:
        if self._engine is not None:
            self._engine.teardown()
```

In `generate.py` `_setup_inner`, attach the store and make the client lazy:

```python
        # WS5: when the pool store can answer for this digest, Generate needs
        # no LLM at all — generate_batch() is pure NumPy plus pool draws. This
        # deletes the cold vLLM spawn AND removes the second party in the
        # embedder-vs-vLLM CUDA OOM (2026-07-26 1M run).
        ctx = ctx.model_copy(update={"pool_store": self._pool_store})
        client = None if self._pools_readable(ctx) else self.model_client
        self._engine.setup(client, ctx)
```

Add the predicate to the DoFn:

```python
    def _pools_readable(self, ctx) -> bool:
        store = getattr(ctx, "pool_store", None)
        if store is None or not ctx.reference_digest:
            return False
        try:
            return store.exists(ctx.reference_digest, ctx.model_uri)
        except Exception:
            return False
```

In `run_pipeline.py`, add the flag beside `--build_rag_layer` and wire the
branch to `WriteToBigQuery(..., method=FILE_LOADS, create_disposition=CREATE_IF_NEEDED,
write_disposition=WRITE_APPEND)` against `synthetic_rag.freetext_pools`, using
`config/bq_schema/synthetic_rag/freetext_pools.schema.json` exactly as the rag_chunks branch uses its schema file.

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/pools/ packages/sdfb-tests/tests/unit/dofns/ -q`
Expected: PASS.

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-beam packages/sdfb-tests/tests/unit/pools/test_pool_branch.py
git commit -m "feat(pools): BuildFreeTextPools branch; Generate skips the ModelClient (WS5 T7)"
```

---

## PHASE 3 — The seeding experiment

### Task 8: `retrieve_kcenter_k` — seeds that span the modes

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/rag/retrieval.py`
- Test: `packages/sdfb-tests/tests/unit/rag/test_kcenter_retrieval.py` (create)

**Interfaces:**
- Produces: `retrieve_kcenter_k(vectors: list[list[float]], items: Sequence, k: int, start: int | None = None) -> list`.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/rag/test_kcenter_retrieval.py`:

```python
"""Greedy k-center seeds span the modes; centroid top-k does not (WS5 T8)."""

from __future__ import annotations

from sdfb_core.rag.index import build_index
from sdfb_core.rag.retrieval import retrieve_centroid_top_k, retrieve_kcenter_k


def _three_modes():
    """One dense mode plus two far, sparse ones — the realistic shape."""
    dense = [[1.0, 0.0, 0.0] for _ in range(20)]
    far_a = [[0.0, 1.0, 0.0] for _ in range(3)]
    far_b = [[0.0, 0.0, 1.0] for _ in range(2)]
    vectors = dense + far_a + far_b
    items = (
        [f"dense{i}" for i in range(20)]
        + [f"a{i}" for i in range(3)]
        + [f"b{i}" for i in range(2)]
    )
    return vectors, items


def test_centroid_topk_stays_in_the_densest_mode():
    vectors, items = _three_modes()
    index = build_index(vectors, 3)
    try:
        got = retrieve_centroid_top_k(index, vectors, items, 4)
    finally:
        index.release()
    assert all(g.startswith("dense") for g in got)


def test_kcenter_reaches_every_mode():
    vectors, items = _three_modes()
    got = retrieve_kcenter_k(vectors, items, 4)
    assert any(g.startswith("dense") for g in got)
    assert any(g.startswith("a") for g in got)
    assert any(g.startswith("b") for g in got)


def test_kcenter_is_deterministic():
    vectors, items = _three_modes()
    assert retrieve_kcenter_k(vectors, items, 5) == retrieve_kcenter_k(vectors, items, 5)


def test_kcenter_start_rotates_the_seed_set():
    """kcenter_rotate re-seeds per ladder attempt; a different start must
    give a different (still mode-spanning) set."""
    vectors, items = _three_modes()
    assert retrieve_kcenter_k(vectors, items, 4, start=0) != retrieve_kcenter_k(
        vectors, items, 4, start=21
    )


def test_kcenter_returns_everything_when_k_exceeds_the_data():
    vectors, items = _three_modes()
    assert len(retrieve_kcenter_k(vectors, items, 999)) == len(items)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_kcenter_retrieval.py -v`
Expected: FAIL with `ImportError: cannot import name 'retrieve_kcenter_k'`.

- [ ] **Step 3: Implement**

Append to `retrieval.py` (pure Python — `sdfb-core` keeps no numpy hard-dep):

```python
def _sq_dist(a: Sequence[float], b: Sequence[float]) -> float:
    return sum((x - y) * (x - y) for x, y in zip(a, b, strict=True))


def retrieve_kcenter_k(
    vectors: list[list[float]],
    items: Sequence,
    k: int,
    start: int | None = None,
) -> list:
    """Greedy k-center: each pick is the item FARTHEST from everything picked.

    Centroid top-k lands entirely in the densest region by construction, so
    rare modes are never shown to the LLM and it is asked for novel values
    while looking at the most average ones. k-center trades typicality for
    coverage. `start` re-seeds the walk (the `kcenter_rotate` arm);
    default starts at the medoid so the dominant mode is still represented.
    """
    items = list(items)
    if not items or k <= 0:
        return []
    if len(items) <= k:
        return items

    if start is None:
        c = centroid(vectors)
        start = min(range(len(vectors)), key=lambda i: _sq_dist(vectors[i], c))
    start %= len(vectors)

    chosen = [start]
    best = [_sq_dist(v, vectors[start]) for v in vectors]
    for _ in range(k - 1):
        nxt = max(range(len(vectors)), key=lambda i: (best[i], -i))
        chosen.append(nxt)
        for i, v in enumerate(vectors):
            d = _sq_dist(v, vectors[nxt])
            if d < best[i]:
                best[i] = d
    return [items[i] for i in chosen]
```

Add `"retrieve_kcenter_k"` to `__all__`.

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/ -q`
Expected: PASS.

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/rag/retrieval.py packages/sdfb-tests/tests/unit/rag/test_kcenter_retrieval.py
git commit -m "feat(rag): greedy k-center exemplar selection (WS5 T8)"
```

---

### Task 9: `--pool_seed_strategy` flag (three arms, one build)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py` (`GenerationContext.pool_seed_strategy`)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`_column_seed_examples`)
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (arg + validation + threading)
- Test: `packages/sdfb-tests/tests/unit/rag/test_pool_seed_strategy.py` (create)

**Interfaces:**
- Consumes: `retrieve_kcenter_k` (T8), `retrieve_column_exemplars` (existing).
- Produces: `POOL_SEED_STRATEGIES = ("centroid", "kcenter", "kcenter_rotate")`; `GenerationContext.pool_seed_strategy: str = "centroid"`.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/rag/test_pool_seed_strategy.py`:

```python
"""--pool_seed_strategy selects the seed set; the default is unchanged."""

from __future__ import annotations

import pytest

from sdfb_beam.cli.run_pipeline import POOL_SEED_STRATEGIES, validate_seed_strategy


def test_default_is_the_control_arm():
    from sdfb_core.engines.base import GenerationContext

    from sdfb_core.contracts.schema import ColumnSpec, TableSchema

    ctx = GenerationContext(
        table_schema=TableSchema(
            table="t", columns=[ColumnSpec(name="c", bq_type="STRING", mode="NULLABLE")]
        )
    )
    assert ctx.pool_seed_strategy == "centroid"


def test_every_arm_is_accepted():
    for arm in POOL_SEED_STRATEGIES:
        assert validate_seed_strategy(arm) == arm


def test_unknown_arm_fails_loudly_at_launch():
    """A typo must not silently degrade to the control arm — that would
    corrupt the three-arm comparison this flag exists for."""
    with pytest.raises(ValueError, match="pool_seed_strategy"):
        validate_seed_strategy("kcentre")


def test_rotate_varies_seeds_across_attempts(monkeypatch):
    from sdfb_core.engines.b1_rag import engine as eng

    seen: list[int | None] = []

    def _spy(vectors, items, k, start=None):
        seen.append(start)
        return list(items)[:k]

    monkeypatch.setattr(eng, "retrieve_kcenter_k", _spy)
    eng.seed_examples_for_attempt(
        vectors=[[1.0], [2.0], [3.0]],
        values=["a", "b", "c"],
        k=2,
        strategy="kcenter_rotate",
        attempt=2,
    )
    eng.seed_examples_for_attempt(
        vectors=[[1.0], [2.0], [3.0]],
        values=["a", "b", "c"],
        k=2,
        strategy="kcenter_rotate",
        attempt=3,
    )
    assert seen[0] != seen[1], "rotate must re-seed per attempt"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_pool_seed_strategy.py -v`
Expected: FAIL with `ImportError: cannot import name 'POOL_SEED_STRATEGIES'`.

- [ ] **Step 3: Implement**

In `base.py`, add to `GenerationContext`:

```python
    # --- pool seeding experiment (WS5 §3) -------------------------------
    # "centroid" (control, today's behavior) | "kcenter" | "kcenter_rotate".
    # Retrieval runs 3x per setup and only picks 8 prompt seeds, so this is
    # the cheapest lever on novel-yield-per-call there is.
    pool_seed_strategy: str = "centroid"
```

In `engine.py`, add the selector and use it in `_column_seed_examples`:

```python
def seed_examples_for_attempt(
    vectors: list[list[float]],
    values: list[str],
    k: int,
    strategy: str,
    attempt: int,
) -> list[str]:
    """Pick the k prompt seeds for one ladder attempt.

    `kcenter_rotate` walks the start index so successive attempts show the
    LLM different regions. That breaks vLLM prefix caching by design — a
    cost that mattered when pools rebuilt 36x per job (ADR 0018) and is
    close to free now that a pool is built once per digest (WS5 §2).
    """
    if strategy == "centroid" or not vectors:
        index = build_index(vectors, len(vectors[0])) if vectors else None
        if index is None:
            return values[:k]
        try:
            return retrieve_centroid_top_k(index, vectors, values, k)
        finally:
            index.release()
    start = None
    if strategy == "kcenter_rotate" and values:
        start = (attempt * max(k, 1)) % len(values)
    return retrieve_kcenter_k(vectors, values, k, start=start)
```

In `run_pipeline.py`:

```python
POOL_SEED_STRATEGIES = ("centroid", "kcenter", "kcenter_rotate")


def validate_seed_strategy(value: str) -> str:
    if value not in POOL_SEED_STRATEGIES:
        raise ValueError(
            f"--pool_seed_strategy must be one of {POOL_SEED_STRATEGIES}, got {value!r}"
        )
    return value
```

```python
    p.add_argument("--pool_seed_strategy", default="centroid")
```

and thread `pool_seed_strategy=validate_seed_strategy(args.pool_seed_strategy)`
into the `GenerationContext` construction. Add the parameter to
`flex_template_metadata.json` and the DAG, mirroring `--build_rag_layer`.

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/ packages/sdfb-tests/tests/unit/cli/ -q`
Expected: PASS.

- [ ] **Step 5: Full suite + lint, then commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add packages/sdfb-core packages/sdfb-beam packages/sdfb-tests/tests/unit/rag/test_pool_seed_strategy.py
git commit -m "feat(b1): --pool_seed_strategy centroid|kcenter|kcenter_rotate (WS5 T9)"
```

---

## PHASE 4 — Record

### Task 10: ADR 0020, doc refresh, run playbook

**Files:**
- Create: `docs/adr/0020-freetext-pools-as-persisted-artifact.md`
- Modify: `docs/adr/README.md` (index row)
- Modify: `docs/designs/2026-07-26-ws5-generation-throughput.md` (status → ACCEPTED; measured-vs-target table)
- Modify: `scripts/doc/make_ws5_figures.py` (`MEASURED` block → post-WS5 run)
- Modify: `docs/RUN_PLAYBOOK.md` (`create_if_not_exists=true`; the three-arm run matrix)

Follow the `visual-first-documentation` skill: update `MEASURED` and regenerate
— do **not** hand-edit numbers into the prose.

- [ ] **Step 1: Write ADR 0020**

Cover: context (the 1M measurements, `_POOL_CACHE` being process-scoped);
decision (pools as a BQ artifact keyed on `(reference_digest, model_uri,
column, target)`, sibling table not a `chunk_kind`, `Generate` without a
`ModelClient`, stagnation recorded); consequences (19.1 GPU-hours removed,
`WRITE_APPEND` duplicate semantics now value-stable, pools invalidate with the
digest or the model); and the ADR 0018 amendment — the stable-prefix
constraint was set when pools rebuilt 36 times and is now nearly free to
relax, which is what `kcenter_rotate` tests.

- [ ] **Step 2: Regenerate figures against the post-WS5 run**

```bash
# update the MEASURED block to the new job id first
uv run --no-sync python3 scripts/doc/make_ws5_figures.py
```

Add an evolution figure (`ws5-wallclock-evolution.png`) plotting wall clock and
pool-build seconds across the 2026-07-26 baseline and each WS5 run, estimates
hatched — this is the progress record the design doc's §6 table is checked
against.

- [ ] **Step 3: Commit**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
git add docs scripts/doc/make_ws5_figures.py
git commit -m "docs(adr): 0020 — free-text pools as a persisted artifact (WS5)"
```

---

## Acceptance (measured, COL_047 excluded)

| Phase | 1M baseline (2026-07-26) | Target |
|---|---:|---:|
| Pool build, LLM service time | 42 698 s over 72 rebuilds | ≤ 1 200 s, 1 build |
| Cold vLLM spawns | 6 × ~230 s | 1 |
| `dofn_setup_done`, total | 28 982 s | < 300 s |
| Generate re-materialization | ~3 900 CPU-s | < 5 CPU-s |
| Wall clock, cold | 4 050 s (68 min) | < 1 200 s (20 min) |
| Wall clock, warm | n/a | < 300 s (5 min) |

Then three runs off **one build**, differing only in `--pool_seed_strategy`,
reported as novel-yield per LLM call, final pool size per column, and ladder
attempts to target.

## Self-Review

**Spec coverage:** design §2.1 → T4/T5; §2.2 → T7; §2.3 → T4/T6; §3 → T8/T9;
§4.1 → T7 (structural, `Generate` never touches CUDA); §4.2 → T2; §4.3 → T3;
§5 → T1 (+ operator note); §6 → T10. Covered.

**Deliberately deferred:** the TEST_2 caveats — `Generate.setup()` has no
happens-before edge on `WriteRagChunks`, so the first worker embeds anyway,
and the row-doc embed covers 1024 of 10 000 reference rows. Neither is on the
critical path once pools stop being built in `setup()`; both belong to a WS6
follow-up against the geometry roadmap's P3/P7.

**Known risk:** T7 changes `Generate.setup()` to accept `client=None`. If any
engine path still assumes a non-`None` client when the store is populated, it
will surface as an `AttributeError` in the T7 DirectRunner test rather than in
production — that is the point of testing the branch before the E2E.
