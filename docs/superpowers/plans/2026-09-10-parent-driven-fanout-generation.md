# Parent-Driven Fan-out Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Children in a relational launch are generated from their parent's landed keys — fan-out from the source histogram, PK-completing cells drawn without replacement, inherited columns copied — so ratio, PK uniqueness and referential integrity hold by construction with no side-input cap and no join.

**Architecture:** A pure `sdfb_core.engines.fanout` module owns the per-key math (histogram, cell table, chunked expansion) and both engines expose `generate_for_keys`. The Beam composer feeds a driven child's Generate DoFn with key batches projected from the parent's `valid` PCollection (Reshuffle + BatchElements) instead of `Create(requests)`; the launcher measures the fan-out histogram and PK cells on the SOURCE child (cached in BigQuery), derives the child's row count, resolves edge roles (driving / implied / external) and wires it all through `PipelineConfig` / `GenerationContext.fanout`.

**Tech Stack:** Python 3.11, Pydantic v2, Apache Beam Python SDK (DirectRunner on the laptop), google-cloud-bigquery (driver-side only, mocked in tests), pytest.

**Spec:** `docs/designs/2026-09-10-parent-driven-fanout-generation.md` (the design; ADR 0036 is written in Task 12 from it).

## Global Constraints

- `sdfb-core` never imports `apache_beam`, `google.cloud.*`, `torch`; engines must run under pure pytest (CLAUDE.md).
- Import direction is strict: `sdfb-beam` → `sdfb-core`, never the reverse.
- Engines are built in `DoFn.setup()`, never in `process()` (the ADR 0030 side-input deferral is the one exception and stays as is).
- `WriteToBigQuery` uses `FILE_LOADS` only; any new BQ table write in the launcher uses a LOAD job (`load_table_from_json`), never streaming inserts.
- Every new milestone is a single line via `sdfb_core.observability.log_milestone` (ADR 0035 rev 2: no multi-line worker entries).
- Verify before every commit: `uv run --no-sync pytest -m "not gpu and not gcp" -q`, `uv run --no-sync ruff check .`, `uv run --no-sync mypy packages/sdfb-core/src` (0 errors is a CI gate).
- Laptop quirk: run pytest as `uv run --no-sync python3 -m pytest …`.
- Anonymized identifiers only in code, tests, docs and commit messages (A_TABLE / COL_XXX aliases).
- Branch: `ws12-fanout-generation` (already exists, holds the spec). Commit after every task with the trailer:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01MhsrQAqKaMsdwxN6aiF3tH`.
- Spec clarification applied by this plan: the CLI has ONE launch-wide `--num_rows`. It applies to root tables; a driven child's count is derived and the launch card says so (`relational_single_job rows_detail=`). There is no per-child flag to refuse.

---

## File structure

| File | Responsibility |
|---|---|
| `packages/sdfb-core/src/sdfb_core/engines/fanout.py` (new) | Pure math and expansion: `FanoutHistogram`, `CellTable`, `FanoutPlan`, `expand_keys` (chunked), `key_seed`. No Beam, no BQ. |
| `packages/sdfb-core/src/sdfb_core/seeding.py` | + `derive_key_seed(run_id, key)` — per-key rng seed. |
| `packages/sdfb-core/src/sdfb_core/contracts/relationships.py` | `FkEdge.drives`; `RelationshipRegistry.edge_roles()` / `driving_edge()`; card shows `DRIVES` / `implied`. |
| `packages/sdfb-core/src/sdfb_core/engines/base.py` | `GenerationContext.fanout`; `GenerationEngine.generate_for_keys`. |
| `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` | `_bind_fanout` in setup; `generate_for_keys`. |
| `packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py` | same for B.2. |
| `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` | `keys` request shape; per-key seeds; `batch_done keys=`. |
| `packages/sdfb-beam/src/sdfb_beam/pipeline.py` | `PipelineConfig.fanout`; `FkEdgeSpec.mode`; `_fanout_requests`; `build_pipeline(requests=)`; `build_relational_pipeline` routes fanout edges. |
| `packages/sdfb-beam/src/sdfb_beam/io/fanout_stats.py` (new) | Driver-side BQ measurement of the histogram + cells, and the `BigQueryFanoutStatsStore` cache. |
| `packages/sdfb-beam/src/sdfb_beam/cli/preflight.py` | Driven-child P4 (`max_k ≤ cells`), `fk_edge_role` milestones, ambiguity stop. |
| `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` | Edge roles → fan-out measurement → derived rows → config/spec wiring; `--driven_uniqueness_mode`; `rows_detail`. |
| `config/bq_schema/synthetic_data_quality/fk_fanout_stats.schema.json` (new) | Cache table schema. |
| `docs/adr/0036-parent-driven-fanout-generation.md` (new), `docs/RUN_PLAYBOOK.md`, `docs/DEPLOYMENT_PREREQUISITES.md`, `config/relationships/README.md`, `.github/prompts/end_to_end_validation_report_generation.prompt.md` | Decision + operator docs. |
| Tests | `tests/unit/engines/test_fanout.py` (new), `tests/unit/contracts/test_relationship_models.py` (+class), `tests/unit/engines/test_abc_contract.py` (+test), `tests/unit/engines/test_b1_rag.py` (+class), `tests/unit/engines/test_b2_generation_plan.py` (+test), `tests/unit/dofns/test_generate_keys_request.py` (new), `tests/unit/test_relational_pipeline.py` (+tests), `tests/unit/io/test_fanout_stats.py` (new), `tests/unit/cli/test_preflight_fanout.py` (new), `tests/unit/cli/test_fanout_launch_wiring.py` (new), `tests/unit/test_fanout_three_tables.py` (new). |

---

### Task 1: Pure fan-out math — `sdfb_core.engines.fanout`

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/engines/fanout.py`
- Modify: `packages/sdfb-core/src/sdfb_core/seeding.py` (append `derive_key_seed`)
- Test: `packages/sdfb-tests/tests/unit/engines/test_fanout.py`

**Interfaces:**
- Produces:
  - `derive_key_seed(run_id: str, key: tuple) -> int`
  - `class FanoutHistogram(parents_by_k: dict[int, int])` with `.mean -> float`, `.max_k -> int`, `.zero_share -> float`, `.sample(rng: random.Random) -> int`, `.to_payload() -> dict[str, int]`, `FanoutHistogram.from_payload(dict) -> FanoutHistogram`
  - `class CellTable(cols: tuple[str, ...], rows: list[tuple], counts: list[float])` with `.size -> int`, `.draw(k: int, rng, *, exact: bool) -> list[tuple]`, `.to_payload()`, `CellTable.from_payload(dict)`
  - `class FanoutPlan(driving_cols: tuple[str, ...], histogram: FanoutHistogram, cells: CellTable | None, exact_cells: bool)` with `FanoutPlan.from_payload(dict) -> FanoutPlan`, `.to_payload() -> dict`, `.columns -> frozenset[str]` (driving + cell cols)
  - `expand_keys(plan, keys: Sequence[tuple], run_id: str, chunk_rows: int) -> Iterator[list[tuple[tuple, tuple]]]` — yields chunks of `(key_tuple, cell_tuple)` pairs, at most `chunk_rows` per chunk, key boundaries may be split across chunks.
- Payload shape (what crosses Beam pickling / BQ JSON):
  ```python
  {"driving_cols": ["D_COL_001", "D_COL_024"], "histogram": {"0": 195, "1": 320, "2": 177},
   "cells": {"cols": ["C_COL_002", "D_COL_018"], "rows": [["C0", "K0"], ["C1", "K1"]], "counts": [40, 20]},
   "exact_cells": True}
  ```

- [ ] **Step 1: Write the failing tests**

```python
# packages/sdfb-tests/tests/unit/engines/test_fanout.py
"""Parent-driven fan-out math (design 2026-09-10, ADR 0036).

A child's rows are its parent's keys x the SOURCE fan-out histogram; the
PK-completing cells of one key are drawn WITHOUT replacement, so a PK
that contains an FK is unique by construction (the 2026-09-09 runs lost
87.9% then 56.5% of C_TABLE to random draws of exactly that PK).
"""

from __future__ import annotations

import random
from collections import Counter

import pytest
from sdfb_core.engines.fanout import (
    CellTable,
    FanoutHistogram,
    FanoutPlan,
    expand_keys,
)
from sdfb_core.seeding import derive_key_seed


class TestFanoutHistogram:
    def test_mean_and_zero_share_include_the_zero_bucket(self):
        h = FanoutHistogram({0: 50, 1: 30, 2: 20})
        assert h.mean == pytest.approx(0.7)
        assert h.zero_share == pytest.approx(0.5)
        assert h.max_k == 2

    def test_sampling_follows_the_histogram(self):
        h = FanoutHistogram({0: 50, 1: 30, 2: 20})
        rng = random.Random(1)
        draws = Counter(h.sample(rng) for _ in range(20_000))
        assert draws[0] / 20_000 == pytest.approx(0.5, abs=0.02)
        assert draws[2] / 20_000 == pytest.approx(0.2, abs=0.02)

    def test_payload_round_trip_uses_string_keys(self):
        h = FanoutHistogram({0: 5, 3: 1})
        assert h.to_payload() == {"0": 5, "3": 1}
        assert FanoutHistogram.from_payload({"0": 5, "3": 1}).parents_by_k == {0: 5, 3: 1}

    def test_empty_histogram_is_refused(self):
        with pytest.raises(ValueError, match="empty"):
            FanoutHistogram({})


class TestCellTable:
    _cells = CellTable(
        cols=("C2", "D18"),
        rows=[("C0", "K0"), ("C1", "K1"), ("C2", "K2"), ("C3", "K3")],
        counts=[40, 30, 20, 10],
    )

    def test_exact_draw_never_repeats_a_cell(self):
        rng = random.Random(3)
        for _ in range(200):
            drawn = self._cells.draw(4, rng, exact=True)
            assert len(set(drawn)) == 4

    def test_exact_draw_respects_weights_in_aggregate(self):
        rng = random.Random(4)
        first = Counter(self._cells.draw(1, rng, exact=True)[0] for _ in range(20_000))
        assert first[("C0", "K0")] / 20_000 == pytest.approx(0.4, abs=0.02)

    def test_exact_draw_beyond_the_table_raises(self):
        with pytest.raises(ValueError, match="4 cells"):
            self._cells.draw(5, random.Random(0), exact=True)

    def test_inexact_draw_may_repeat_and_never_raises(self):
        drawn = self._cells.draw(50, random.Random(0), exact=False)
        assert len(drawn) == 50

    def test_large_table_rejection_path_is_exact_too(self):
        rows = [(i,) for i in range(5_000)]
        table = CellTable(cols=("X",), rows=rows, counts=[1.0] * 5_000)
        drawn = table.draw(20, random.Random(9), exact=True)  # k << C: rejection path
        assert len(set(drawn)) == 20
        drawn = table.draw(4_000, random.Random(9), exact=True)  # k ~ C: permutation path
        assert len(set(drawn)) == 4_000


class TestExpandKeys:
    _plan = FanoutPlan(
        driving_cols=("PID",),
        histogram=FanoutHistogram({0: 1, 2: 1, 3: 1}),
        cells=CellTable(cols=("CAT",), rows=[("a",), ("b",), ("c",)], counts=[1, 1, 1]),
        exact_cells=True,
    )

    def test_same_key_same_children_under_the_same_run(self):
        keys = [(f"P{i}",) for i in range(50)]
        a = [c for chunk in expand_keys(self._plan, keys, "run-1", 1_000) for c in chunk]
        b = [c for chunk in expand_keys(self._plan, keys, "run-1", 1_000) for c in chunk]
        assert a == b
        c = [c for chunk in expand_keys(self._plan, keys, "run-2", 1_000) for c in chunk]
        assert a != c

    def test_cells_are_unique_within_a_key(self):
        keys = [(f"P{i}",) for i in range(200)]
        by_key: dict[tuple, list[tuple]] = {}
        for chunk in expand_keys(self._plan, keys, "run-1", 1_000):
            for key, cell in chunk:
                by_key.setdefault(key, []).append(cell)
        assert by_key  # some keys had children
        for cells in by_key.values():
            assert len(cells) == len(set(cells))
            assert len(cells) <= 3

    def test_chunks_never_exceed_chunk_rows_and_split_keys(self):
        plan = FanoutPlan(
            driving_cols=("PID",),
            histogram=FanoutHistogram({7: 1}),  # every key has 7 children
            cells=CellTable(cols=("CAT",), rows=[(i,) for i in range(10)], counts=[1] * 10),
            exact_cells=True,
        )
        chunks = list(expand_keys(plan, [("P1",), ("P2",)], "r", chunk_rows=5))
        assert [len(c) for c in chunks] == [5, 5, 4]
        cells_p1 = [cell for chunk in chunks for key, cell in chunk if key == ("P1",)]
        assert len(cells_p1) == 7 and len(set(cells_p1)) == 7  # unique across the split

    def test_no_cells_means_key_only_rows(self):
        plan = FanoutPlan(
            driving_cols=("PID",), histogram=FanoutHistogram({2: 1}), cells=None,
            exact_cells=False,
        )
        (chunk,) = list(expand_keys(plan, [("P1",)], "r", 100))
        assert chunk == [(("P1",), ()), (("P1",), ())]

    def test_payload_round_trip(self):
        payload = self._plan.to_payload()
        again = FanoutPlan.from_payload(payload)
        assert again.driving_cols == ("PID",)
        assert again.histogram.parents_by_k == {0: 1, 2: 1, 3: 1}
        assert again.cells is not None and again.cells.rows == [("a",), ("b",), ("c",)]
        assert again.columns == frozenset({"PID", "CAT"})


def test_key_seed_is_stable_and_key_sensitive():
    assert derive_key_seed("r", ("a", 1)) == derive_key_seed("r", ("a", 1))
    assert derive_key_seed("r", ("a", 1)) != derive_key_seed("r", ("a", 2))
    assert derive_key_seed("r", ("a", 1)) != derive_key_seed("s", ("a", 1))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_fanout.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'sdfb_core.engines.fanout'`.

- [ ] **Step 3: Implement**

Append to `packages/sdfb-core/src/sdfb_core/seeding.py`:

```python
def derive_key_seed(run_id: str, key: tuple) -> int:
    """Stable seed for one parent key's children (design 2026-09-10):
    the same parent yields the same children on a re-run of ``run_id``
    and on a retried bundle. ``repr`` keeps mixed-type tuples total."""
    digest = hashlib.blake2b(
        f"{run_id}\x1f{key!r}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") >> 1
```

Create `packages/sdfb-core/src/sdfb_core/engines/fanout.py`:

```python
"""Parent-driven fan-out (design 2026-09-10, ADR 0036).

A child table generated from its parent's landed keys: per key, how many
children (the SOURCE fan-out histogram, zero bucket included), which
PK-completing cells (drawn WITHOUT replacement from the source's joint
cell distribution), and which columns are inherited (copied from the
key tuple). This is the part both engines share; everything that is not
a key, a cell or an inherited column is the engine's own sampling.

Why without replacement: a PK that contains an FK is a per-parent key.
Drawing its remaining members at random inside a parent collides as
balls into bins (`1 - C/k (1 - e^(-k/C))`, the 2026-09-09 runs); the
source PK guarantees k <= C, so drawing k distinct cells reproduces it.

Pure Python; no Beam, no GCP.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import accumulate

from sdfb_core.seeding import derive_key_seed

__all__ = [
    "CellTable",
    "FanoutHistogram",
    "FanoutPlan",
    "expand_keys",
]

# Above this fraction of the table, sampling without replacement switches
# from rejection (expected O(k) draws) to a weighted permutation
# (Efraimidis & Spirakis 2006, O(C log k)).
_REJECTION_MAX_FILL = 0.5


@dataclass(frozen=True)
class FanoutHistogram:
    """``k -> number of parent tuples with k children`` in the source."""

    parents_by_k: dict[int, int]
    _ks: tuple[int, ...] = field(init=False, repr=False)
    _cum: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.parents_by_k or sum(self.parents_by_k.values()) <= 0:
            raise ValueError("fan-out histogram is empty")
        ks = tuple(sorted(int(k) for k in self.parents_by_k))
        total = float(sum(self.parents_by_k.values()))
        cum = tuple(accumulate(self.parents_by_k[k] / total for k in ks))
        object.__setattr__(self, "_ks", ks)
        object.__setattr__(self, "_cum", cum)

    @property
    def mean(self) -> float:
        total = sum(self.parents_by_k.values())
        return sum(k * n for k, n in self.parents_by_k.items()) / total

    @property
    def max_k(self) -> int:
        return max(self.parents_by_k)

    @property
    def zero_share(self) -> float:
        return self.parents_by_k.get(0, 0) / sum(self.parents_by_k.values())

    def sample(self, rng: random.Random) -> int:
        i = min(bisect_right(self._cum, rng.random()), len(self._ks) - 1)
        return self._ks[i]

    def to_payload(self) -> dict[str, int]:
        return {str(k): int(n) for k, n in sorted(self.parents_by_k.items())}

    @classmethod
    def from_payload(cls, payload: dict) -> FanoutHistogram:
        return cls({int(k): int(n) for k, n in payload.items()})


@dataclass(frozen=True)
class CellTable:
    """The joint values of the PK-completing categorical members, with
    their source row counts as weights."""

    cols: tuple[str, ...]
    rows: list[tuple]
    counts: list[float]

    def __post_init__(self) -> None:
        if len(self.rows) != len(self.counts) or not self.rows:
            raise ValueError("cell table needs one positive count per row")

    @property
    def size(self) -> int:
        return len(self.rows)

    def draw(self, k: int, rng: random.Random, *, exact: bool) -> list[tuple]:
        """``k`` cells. ``exact`` = without replacement (raises when
        ``k`` exceeds the table); otherwise weighted with replacement."""
        if k <= 0:
            return []
        if not exact:
            return rng.choices(self.rows, weights=self.counts, k=k)
        if k > self.size:
            raise ValueError(
                f"{k} children requested from {self.size} cells over "
                f"{list(self.cols)} — the declared PK is not a key"
            )
        if k <= self.size * _REJECTION_MAX_FILL:
            chosen: dict[int, None] = {}
            while len(chosen) < k:
                (idx,) = rng.choices(range(self.size), weights=self.counts, k=1)
                chosen.setdefault(idx, None)
            return [self.rows[i] for i in chosen]
        # Weighted permutation: key = u^(1/w), take the k largest.
        keyed = sorted(
            range(self.size),
            key=lambda i: -(rng.random() ** (1.0 / self.counts[i])),
        )
        return [self.rows[i] for i in keyed[:k]]

    def to_payload(self) -> dict:
        return {
            "cols": list(self.cols),
            "rows": [list(r) for r in self.rows],
            "counts": [float(c) for c in self.counts],
        }

    @classmethod
    def from_payload(cls, payload: dict) -> CellTable:
        return cls(
            cols=tuple(payload["cols"]),
            rows=[tuple(r) for r in payload["rows"]],
            counts=[float(c) for c in payload["counts"]],
        )


@dataclass(frozen=True)
class FanoutPlan:
    """One driven child's relational recipe."""

    driving_cols: tuple[str, ...]
    histogram: FanoutHistogram
    cells: CellTable | None
    # True when EVERY PK member outside the driving edge is a cell column
    # — then the cells alone must key the child and are drawn without
    # replacement. False when an unbounded member (pattern, numeric,
    # temporal) completes the PK and cells only need to follow weights.
    exact_cells: bool

    @property
    def columns(self) -> frozenset[str]:
        cols = set(self.driving_cols)
        if self.cells is not None:
            cols.update(self.cells.cols)
        return frozenset(cols)

    def to_payload(self) -> dict:
        return {
            "driving_cols": list(self.driving_cols),
            "histogram": self.histogram.to_payload(),
            "cells": self.cells.to_payload() if self.cells is not None else None,
            "exact_cells": bool(self.exact_cells),
        }

    @classmethod
    def from_payload(cls, payload: dict) -> FanoutPlan:
        cells = payload.get("cells")
        return cls(
            driving_cols=tuple(payload["driving_cols"]),
            histogram=FanoutHistogram.from_payload(payload["histogram"]),
            cells=CellTable.from_payload(cells) if cells else None,
            exact_cells=bool(payload.get("exact_cells", False)),
        )


def expand_keys(
    plan: FanoutPlan,
    keys: Sequence[tuple],
    run_id: str,
    chunk_rows: int,
) -> Iterator[list[tuple[tuple, tuple]]]:
    """``(key, cell)`` pairs for every child of every key, in chunks of at
    most ``chunk_rows`` — a hot parent never makes an oversized bundle,
    and a key's cells stay unique across the split because they are
    drawn once per key."""
    chunk_rows = max(1, int(chunk_rows))
    chunk: list[tuple[tuple, tuple]] = []
    for key in keys:
        rng = random.Random(derive_key_seed(run_id, tuple(key)))
        k = plan.histogram.sample(rng)
        if k <= 0:
            continue
        cells: list[tuple] = (
            plan.cells.draw(k, rng, exact=plan.exact_cells)
            if plan.cells is not None
            else [()] * k
        )
        for cell in cells:
            chunk.append((tuple(key), tuple(cell)))
            if len(chunk) >= chunk_rows:
                yield chunk
                chunk = []
    if chunk:
        yield chunk
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_fanout.py -q`
Expected: all pass. Then `uv run --no-sync ruff check packages/sdfb-core/src/sdfb_core/engines/fanout.py packages/sdfb-core/src/sdfb_core/seeding.py` and `uv run --no-sync mypy packages/sdfb-core/src` clean.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-core/src/sdfb_core/engines/fanout.py packages/sdfb-core/src/sdfb_core/seeding.py packages/sdfb-tests/tests/unit/engines/test_fanout.py
git commit -m "feat(fanout): pure fan-out math — histogram, cell table without replacement, chunked key expansion (ADR 0036)"
```

---

### Task 2: Edge roles in the relationship contract

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/contracts/relationships.py` (`FkEdge`, `RelationshipRegistry`, `_table_lines`)
- Test: `packages/sdfb-tests/tests/unit/contracts/test_relationship_models.py`

**Interfaces:**
- `FkEdge.drives: bool = False` (new YAML key `drives: true`).
- `RelationshipRegistry.edge_roles(table: str) -> dict[FkEdge, str]` — role per enforced edge: `"driving" | "implied" | "external"`; raises `RelationshipError` when several in-model enforced edges exist and none/many are marked `drives`, or when an edge is neither driving nor implied.
- `RelationshipRegistry.driving_edge(table: str) -> FkEdge | None` — the driving edge, `None` for a root / external-only table.
- Card: the driving edge line ends with `[enforced, DRIVES]`, an implied one with `[enforced, implied via <driving parent>]`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/sdfb-tests/tests/unit/contracts/test_relationship_models.py`:

```python
class TestEdgeRoles:
    """Design 2026-09-10 (ADR 0036): one DRIVING edge per child — the
    parent whose keys the child is generated from; every other enforced
    in-model edge must be IMPLIED (its columns are carried by the driving
    parent from that other parent), else the launch stops. Today the
    engine writes each edge's columns in turn and the last one wins."""

    _THREE = """
model: kw
tables:
  B_TABLE:
    pk: [D_COL_001]
  C_TABLE:
    pk: [D_COL_001, C_COL_002, D_COL_018]
    fk:
      - cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009]
        ref: B_TABLE
        ref_cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009]
  A_TABLE:
    pk: [D_COL_024, D_COL_025, C_COL_009, C_COL_045, A_COL_005]
    fk:
      - cols: [D_COL_024, D_COL_025, C_COL_009]
        ref: C_TABLE
        ref_cols: [D_COL_024, D_COL_025, C_COL_009]
        drives: true
      - cols: [D_COL_024, D_COL_025, C_COL_009]
        ref: B_TABLE
        ref_cols: [D_COL_024, D_COL_025, C_COL_009]
"""

    def _registry(self, text: str) -> RelationshipRegistry:
        return RelationshipRegistry.from_sources([("config/relationships/kw.yaml", text)])

    def test_single_edge_drives_by_itself(self):
        reg = self._registry(self._THREE)
        (edge,) = reg.enforced_edges("C_TABLE")
        assert reg.edge_roles("C_TABLE") == {edge: "driving"}
        assert reg.driving_edge("C_TABLE") == edge

    def test_marked_edge_drives_and_the_other_is_implied(self):
        reg = self._registry(self._THREE)
        to_c, to_b = reg.enforced_edges("A_TABLE")
        assert reg.edge_roles("A_TABLE") == {to_c: "driving", to_b: "implied"}

    def test_root_has_no_driving_edge(self):
        reg = self._registry(self._THREE)
        assert reg.driving_edge("B_TABLE") is None
        assert reg.edge_roles("B_TABLE") == {}

    def test_two_unmarked_edges_stop_with_the_edit(self):
        text = self._THREE.replace("        drives: true\n", "")
        with pytest.raises(RelationshipError, match=r"drives: true"):
            self._registry(text).edge_roles("A_TABLE")

    def test_an_edge_the_driving_parent_does_not_carry_stops(self):
        # C_TABLE's edge no longer carries the account tuple -> A_TABLE's
        # B_TABLE edge is not implied.
        text = self._THREE.replace(
            "      - cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009]\n"
            "        ref: B_TABLE\n"
            "        ref_cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009]\n",
            "      - cols: [D_COL_001]\n        ref: B_TABLE\n        ref_cols: [D_COL_001]\n",
        )
        with pytest.raises(RelationshipError, match=r"neither driving nor implied"):
            self._registry(text).edge_roles("A_TABLE")

    def test_external_edges_are_external(self):
        text = """
model: m
tables:
  T:
    pk: [ID]
    fk:
      - cols: [X]
        ref: ds.other
        ref_cols: [X]
"""
        reg = self._registry(text)
        (edge,) = reg.enforced_edges("T")
        assert reg.edge_roles("T") == {edge: "external"}
        assert reg.driving_edge("T") is None

    def test_card_names_the_roles(self):
        card = self._registry(self._THREE).card("A_TABLE")
        assert "[enforced, DRIVES]" in card
        assert "[enforced, implied via C_TABLE]" in card
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/contracts/test_relationship_models.py -q -k EdgeRoles`
Expected: `ValidationError … extra fields not permitted: drives` and `AttributeError: … edge_roles`.

- [ ] **Step 3: Implement**

In `packages/sdfb-core/src/sdfb_core/contracts/relationships.py`, add to `FkEdge` after `note: str = ""`:

```python
    # Design 2026-09-10 (ADR 0036): the edge whose parent keys this table
    # is generated FROM. Needed only when a child has several enforced
    # in-model edges; a lone edge drives by itself.
    drives: bool = False
```

Add to `RelationshipRegistry` after `enforced_edges`:

```python
    def edge_roles(self, table: str) -> dict[FkEdge, str]:
        """Role of every enforced edge (ADR 0036): ``driving`` (the
        parent whose keys this child is generated from), ``implied``
        (its columns are a subset of the driving edge's, and the driving
        parent carries them from that parent through its own enforced
        edge, transitively) or ``external``. Anything else is a launch
        stop: today the engine writes each edge's columns in turn and
        the last edge silently wins."""
        edges = self.enforced_edges(table)
        roles: dict[FkEdge, str] = {e: "external" for e in edges if e.external}
        internal = [e for e in edges if not e.external]
        if not internal:
            return roles
        driving: FkEdge
        if len(internal) == 1:
            driving = internal[0]
        else:
            marked = [e for e in internal if e.drives]
            if len(marked) != 1:
                names = ", ".join(f"({','.join(e.cols)})->{e.ref}" for e in internal)
                raise RelationshipError(
                    f"{_name(table)}: {len(internal)} enforced edges [{names}] "
                    f"and {len(marked)} marked `drives: true` — mark exactly "
                    f"one edge `drives: true` (the parent whose keys this "
                    f"table is generated from)"
                )
            driving = marked[0]
        roles[driving] = "driving"
        for edge in internal:
            if edge is driving:
                continue
            if self._implied(edge, driving):
                roles[edge] = "implied"
                continue
            raise RelationshipError(
                f"{_name(table)}: edge ({','.join(edge.cols)})->{edge.ref} is "
                f"neither driving nor implied by the driving edge "
                f"({','.join(driving.cols)})->{driving.ref}: widen "
                f"{driving.ref}'s edge to {edge.ref} to carry "
                f"[{','.join(edge.cols)}], or mark this edge `drives: true`"
            )
        return roles

    def driving_edge(self, table: str) -> FkEdge | None:
        return next(
            (e for e, r in self.edge_roles(table).items() if r == "driving"), None
        )

    def _implied(self, edge: FkEdge, driving: FkEdge) -> bool:
        """``edge`` is satisfied by construction when its columns ride on
        the driving edge AND the driving parent obtains them from
        ``edge.ref`` (transitively over enforced edges)."""
        if not set(edge.cols) <= set(driving.cols):
            return False
        # The parent-side names of edge.cols on the driving parent.
        pos = {c: i for i, c in enumerate(driving.cols)}
        parent_cols = {driving.ref_cols[pos[c]] for c in edge.cols}
        return self._carries(driving.ref, edge.ref, parent_cols, seen=set())

    def _carries(self, table: str, target: str, cols: set[str], seen: set[str]) -> bool:
        if table == target:
            return True
        if table in seen:
            return False
        seen.add(table)
        for up in self.enforced_edges(table):
            if up.external or not cols <= set(up.cols):
                continue
            pos = {c: i for i, c in enumerate(up.cols)}
            upstream = {up.ref_cols[pos[c]] for c in cols}
            if self._carries(up.ref, target, upstream, seen):
                return True
        return False
```

In `_table_lines`, where an enforced edge's suffix is rendered (the string `[enforced]`), compute the role once per table (`roles = {}` guarded with `try/except RelationshipError` so a mis-declared model still renders its card) and render `[enforced, DRIVES]` for `"driving"`, `[enforced, implied via {driving.ref}]` for `"implied"`, `[enforced]` otherwise. Find the exact line with `grep -n '\[enforced\]' packages/sdfb-core/src/sdfb_core/contracts/relationships.py`.

Also add `drives` to `config/relationships/README.md`'s field table (one row: `drives` — `true` on the ONE enforced edge a multi-parent child is generated from; ADR 0036).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/contracts/ packages/sdfb-tests/tests/unit/cli/test_launch_scenarios.py -q`
Expected: pass. ruff + mypy core clean.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-core/src/sdfb_core/contracts/relationships.py packages/sdfb-tests/tests/unit/contracts/test_relationship_models.py config/relationships/README.md
git commit -m "feat(relationships): drives flag, edge roles (driving/implied/external) with loud stops (ADR 0036)"
```

---

### Task 3: `GenerationContext.fanout` and `GenerationEngine.generate_for_keys`

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py`
- Test: `packages/sdfb-tests/tests/unit/engines/test_abc_contract.py`

**Interfaces:**
- `GenerationContext.fanout: dict | None = None` — the `FanoutPlan.to_payload()` dict (Task 1).
- `GenerationEngine.generate_for_keys(self, keys: Sequence[tuple], cfg: GenerationConfig) -> Iterator[GeneratedRecord]` — NOT abstract; the base raises `NotImplementedError`; both real engines override (Tasks 4–5). `cfg.batch_size` is the chunk size for `expand_keys`.

- [ ] **Step 1: Write the failing test**

Append to `packages/sdfb-tests/tests/unit/engines/test_abc_contract.py`:

```python
def test_both_engines_implement_generate_for_keys():
    """ADR 0036: a driven child is generated from parent keys; both
    engines must own the entry point (the base refuses)."""
    from sdfb_core.engines import GenerationEngine, get_engine
    from sdfb_core.engines.b1_rag import B1RagEngine
    from sdfb_core.engines.b2_library import B2LibraryEngine

    assert B1RagEngine.generate_for_keys is not GenerationEngine.generate_for_keys
    assert B2LibraryEngine.generate_for_keys is not GenerationEngine.generate_for_keys
    assert get_engine("b1_rag") is B1RagEngine


def test_context_carries_the_fanout_payload():
    from sdfb_core.engines import GenerationContext
    from sdfb_core.contracts import TableSchema

    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.d.t"},
         "schema": [{"name": "ID", "type": "STRING", "mode": "REQUIRED"}]}
    )
    ctx = GenerationContext(
        table_schema=schema, reference_rows=[{"ID": "a"}],
        reference_digest="d", pipeline_run_id="r",
        fanout={"driving_cols": ["ID"], "histogram": {"1": 1}, "cells": None,
                "exact_cells": False},
    )
    assert ctx.fanout is not None and ctx.fanout["driving_cols"] == ["ID"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_abc_contract.py -q`
Expected: `AttributeError: type object 'GenerationEngine' has no attribute 'generate_for_keys'` and a pydantic `extra fields` error for `fanout`.

- [ ] **Step 3: Implement**

In `GenerationContext` (after `relationship_card: str = ""`):

```python
    # Design 2026-09-10 (ADR 0036): a DRIVEN child's recipe — the
    # `FanoutPlan.to_payload()` dict (driving edge columns, the SOURCE
    # fan-out histogram, the PK-completing cell table). None = this table
    # generates from `--num_rows` batch requests as before.
    fanout: dict | None = None
```

In `GenerationEngine` after `generate_batch`:

```python
    def generate_for_keys(
        self,
        keys: Sequence[tuple],
        cfg: GenerationConfig,
    ) -> Iterator[GeneratedRecord]:
        """Yield the children of ``keys`` (design 2026-09-10, ADR 0036):
        per key, the fan-out and the PK-completing cells come from
        ``ctx.fanout`` (`sdfb_core.engines.fanout.expand_keys`), inherited
        columns are copied from the key tuple, and every other column is
        this engine's own sampling. ``cfg.batch_size`` bounds the rows
        sampled at once (chunked emission). Engines that cannot be driven
        keep this default."""
        raise NotImplementedError(f"{type(self).__name__} cannot generate from parent keys")
```

Add `from collections.abc import Iterator, Sequence` to the imports if `Sequence` is not already imported.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_abc_contract.py -q`
Expected: the `fanout` test passes; `test_both_engines_implement_generate_for_keys` still FAILS (engines come in Tasks 4–5). That is expected; leave it red until Task 5 and do not commit a skip.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-core/src/sdfb_core/engines/base.py packages/sdfb-tests/tests/unit/engines/test_abc_contract.py
git commit -m "feat(engines): GenerationContext.fanout + generate_for_keys entry point (ADR 0036)"
```

---

### Task 4: B.1 `generate_for_keys`

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (setup binding after `_bind_fk_key_pools`; new method next to `generate_batch`)
- Test: `packages/sdfb-tests/tests/unit/engines/test_b1_rag.py`

**Interfaces:**
- Consumes: `FanoutPlan.from_payload`, `expand_keys` (Task 1); `ctx.fanout` (Task 3).
- Produces: `B1RagEngine.generate_for_keys(keys, cfg)`; `self._fanout: FanoutPlan | None`.

- [ ] **Step 1: Write the failing test**

Append to `packages/sdfb-tests/tests/unit/engines/test_b1_rag.py` (reuse the file's existing stub client if one exists; otherwise this one):

```python
class TestGenerateForKeys:
    """ADR 0036: a driven child from parent keys — inherited columns
    copied, PK cells unique per key, the rest sampled as usual."""

    _SCHEMA = TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.src.child_t"},
            "schema": [
                {"name": "PID", "type": "STRING", "mode": "REQUIRED"},
                {"name": "REGION", "type": "STRING", "mode": "REQUIRED"},
                {"name": "CAT", "type": "STRING", "mode": "REQUIRED"},
                {"name": "AMT", "type": "INT64", "mode": "REQUIRED"},
            ],
        }
    )

    @staticmethod
    def _rows():
        return [
            {"PID": f"P{i:04d}", "REGION": "ES" if i % 2 else "PT",
             "CAT": "abc"[i % 3], "AMT": i * 3}
            for i in range(60)
        ]

    def _ctx(self):
        return GenerationContext(
            table_schema=self._SCHEMA,
            reference_rows=self._rows(),
            reference_digest="fanout-digest",
            pipeline_run_id="fanout-run",
            pk_columns=["PID", "CAT"],
            fanout={
                "driving_cols": ["PID", "REGION"],
                "histogram": {"0": 1, "2": 2, "3": 1},
                "cells": {"cols": ["CAT"], "rows": [["a"], ["b"], ["c"]],
                          "counts": [3, 2, 1]},
                "exact_cells": True,
            },
        )

    class _Client:
        def generate_json(self, *, prompt, n=1, **kw):
            return [{"values": [f"gen-{i}" for i in range(32)]}]

    def test_children_carry_their_parent_and_unique_cells(self):
        engine = B1RagEngine(embedder=HashingEmbedder())
        engine.setup(self._Client(), self._ctx())
        keys = [(f"K{i}", "ES" if i % 2 else "PT") for i in range(40)]
        cfg = GenerationConfig(seed=1, batch_size=1_000)
        rows = [r.model_dump() for r in engine.generate_for_keys(keys, cfg)]
        assert rows
        for r in rows:
            assert (r["PID"], r["REGION"]) in keys          # inherited verbatim
            assert isinstance(r["AMT"], int)                # the rest is sampled
        per_key: dict = {}
        for r in rows:
            per_key.setdefault(r["PID"], []).append(r["CAT"])
        for cats in per_key.values():
            assert len(cats) == len(set(cats)) and len(cats) <= 3
        # Fan-out histogram: only 0, 2, 3 children per key.
        assert {len(v) for v in per_key.values()} <= {2, 3}

    def test_same_keys_same_children(self):
        engine = B1RagEngine(embedder=HashingEmbedder())
        engine.setup(self._Client(), self._ctx())
        keys = [(f"K{i}", "ES") for i in range(20)]
        cfg = GenerationConfig(seed=1, batch_size=7)  # chunked at 7 rows
        a = [(r.PID, r.CAT) for r in engine.generate_for_keys(keys, cfg)]
        b = [(r.PID, r.CAT) for r in engine.generate_for_keys(keys, cfg)]
        assert a == b

    def test_without_a_plan_it_refuses(self):
        engine = B1RagEngine(embedder=HashingEmbedder())
        ctx = self._ctx().model_copy(update={"fanout": None})
        engine.setup(self._Client(), ctx)
        with pytest.raises(RuntimeError, match="fanout"):
            list(engine.generate_for_keys([("K1", "ES")], GenerationConfig(seed=1)))
```

Add the imports the file lacks: `from sdfb_core.engines import GenerationConfig, GenerationContext`, `from sdfb_core.engines.b1_rag import B1RagEngine, HashingEmbedder`, `from sdfb_core.contracts import TableSchema`, `import pytest`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b1_rag.py -q -k GenerateForKeys`
Expected: `NotImplementedError: B1RagEngine cannot generate from parent keys`.

- [ ] **Step 3: Implement**

In `B1RagEngine.__init__` add `self._fanout: FanoutPlan | None = None`. In `setup`, right after `self._bind_fk_key_pools(ctx)`, add `self._bind_fanout(ctx)`. Add the methods:

```python
    def _bind_fanout(self, ctx: GenerationContext) -> None:
        """A driven child's recipe (ADR 0036): the driving-edge columns
        (FK + inherited) leave the per-column samplers exactly as FK
        columns do; the cell columns stay sampled and are overridden."""
        payload = getattr(ctx, "fanout", None)
        if not payload:
            self._fanout = None
            return
        self._fanout = FanoutPlan.from_payload(payload)
        assert self._profiles is not None
        for name in self._fanout.driving_cols:
            self._fk_columns.add(name)
            base = self._profiles.get(name)
            if base is not None:
                self._profiles[name] = ColumnProfile(
                    name=base.name, bq_type=base.bq_type,
                    kind=ColumnKind.CATEGORICAL, nullable=base.nullable,
                    null_fraction=0.0, categories={}, observed_values=(),
                )
        log_milestone(
            "fanout_bound",
            driving_cols=",".join(self._fanout.driving_cols),
            cells=self._fanout.cells.size if self._fanout.cells else 0,
            exact_cells=self._fanout.exact_cells,
            mean_fanout=round(self._fanout.histogram.mean, 3),
        )

    def generate_for_keys(
        self, keys: Sequence[tuple], cfg: GenerationConfig
    ) -> Iterator[GeneratedRecord]:
        if not self._ready or self._record_model is None or self._samplers is None:
            raise RuntimeError("B1RagEngine.generate_for_keys called before setup()")
        if self._fanout is None:
            raise RuntimeError("B1RagEngine.generate_for_keys: ctx.fanout is not set")
        assert self._ctx is not None
        plan = self._fanout
        similarity = float(cfg.similarity)
        chunk_rows = max(1, int(cfg.batch_size or 1000))
        for chunk in expand_keys(plan, keys, self._ctx.pipeline_run_id, chunk_rows):
            n = len(chunk)
            columns = self._sample_columns(n, cfg, similarity)
            columns.update(self._sample_free_text(n, cfg, similarity))
            for j, name in enumerate(plan.driving_cols):
                columns[name] = [key[j] for key, _ in chunk]
            if plan.cells is not None:
                for j, name in enumerate(plan.cells.cols):
                    columns[name] = [cell[j] for _, cell in chunk]
            for i in range(n):
                raw = {name: columns[name][i] for name in self._column_order}
                try:
                    yield self._record_model.model_validate(raw)
                except Exception:
                    continue
```

Imports: `from sdfb_core.engines.fanout import FanoutPlan, expand_keys`; `Sequence` from `collections.abc`. `_sample_columns` already skips names in `self._fk_columns`; a `cfg.seed` of the same value across chunks is fine because the key-level draws are seeded per key and the "rest" samplers are re-seeded per chunk from `cfg.seed` (the DoFn varies `cfg.seed` per batch in Task 6).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b1_rag.py packages/sdfb-tests/tests/unit/engines/test_abc_contract.py -q`
Expected: B.1 tests pass; the ABC contract test still fails on B.2 (next task).

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py packages/sdfb-tests/tests/unit/engines/test_b1_rag.py
git commit -m "feat(b1): generate_for_keys — driven children from parent keys (ADR 0036)"
```

---

### Task 5: B.2 `generate_for_keys`

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py`
- Test: `packages/sdfb-tests/tests/unit/engines/test_b2_generation_plan.py`

**Interfaces:** same as Task 4 for `B2LibraryEngine`.

- [ ] **Step 1: Write the failing test**

Append to `packages/sdfb-tests/tests/unit/engines/test_b2_generation_plan.py`:

```python
def test_b2_generate_for_keys_matches_the_b1_contract():
    """ADR 0036: both engines are driven the same way."""
    from sdfb_core.contracts import TableSchema
    from sdfb_core.engines import GenerationConfig, GenerationContext, get_engine

    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.src.child_t"},
         "schema": [
             {"name": "PID", "type": "STRING", "mode": "REQUIRED"},
             {"name": "CAT", "type": "STRING", "mode": "REQUIRED"},
             {"name": "AMT", "type": "INT64", "mode": "REQUIRED"},
         ]}
    )
    rows = [{"PID": f"P{i:04d}", "CAT": "abc"[i % 3], "AMT": i} for i in range(60)]
    ctx = GenerationContext(
        table_schema=schema, reference_rows=rows, reference_digest="b2-fanout",
        pipeline_run_id="b2-run", pk_columns=["PID", "CAT"],
        fanout={"driving_cols": ["PID"], "histogram": {"1": 1, "3": 1},
                "cells": {"cols": ["CAT"], "rows": [["a"], ["b"], ["c"]], "counts": [1, 1, 1]},
                "exact_cells": True},
    )

    class _Client:
        def generate_json(self, *, prompt, n=1, **kw):
            return [{"values": []}]

    engine = get_engine("b2_library")(use_sdgx=False)
    engine.setup(_Client(), ctx)
    keys = [(f"K{i}",) for i in range(30)]
    out = [r.model_dump() for r in engine.generate_for_keys(keys, GenerationConfig(seed=2, batch_size=8))]
    assert out and all((r["PID"],) in keys for r in out)
    per_key: dict = {}
    for r in out:
        per_key.setdefault(r["PID"], []).append(r["CAT"])
    assert all(len(v) == len(set(v)) for v in per_key.values())
    assert {len(v) for v in per_key.values()} <= {1, 3}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_b2_generation_plan.py -q -k generate_for_keys`
Expected: `NotImplementedError`.

- [ ] **Step 3: Implement**

In `B2LibraryEngine.setup`, after the `bind_fk_key_pools` block (the loop that overrides `self._profiles[fk_name]`), add:

```python
        payload = getattr(ctx, "fanout", None)
        self._fanout = FanoutPlan.from_payload(payload) if payload else None
        if self._fanout is not None:
            for name in self._fanout.driving_cols:
                base = self._profiles.get(name)
                if base is not None:
                    self._profiles[name] = ColumnProfile(
                        name=base.name, bq_type=base.bq_type,
                        kind=ColumnKind.CATEGORICAL, nullable=base.nullable,
                        null_fraction=0.0, categories={}, observed_values=(),
                    )
            log_milestone(
                "fanout_bound",
                driving_cols=",".join(self._fanout.driving_cols),
                cells=self._fanout.cells.size if self._fanout.cells else 0,
                exact_cells=self._fanout.exact_cells,
                mean_fanout=round(self._fanout.histogram.mean, 3),
            )
```

(`self._fanout: FanoutPlan | None = None` in `__init__`.) Note the B.2 profile override must happen BEFORE the backend fit if the fit reads `self._profiles` — place it where the FK override already sits, which is before the fit. Add the method after `generate_batch`:

```python
    def generate_for_keys(
        self, keys: Sequence[tuple], cfg: GenerationConfig
    ) -> Iterator[GeneratedRecord]:
        if not self._fitted or self._backend is None or self._record_model is None:
            raise RuntimeError("B2LibraryEngine.generate_for_keys called before setup()")
        if self._fanout is None:
            raise RuntimeError("B2LibraryEngine.generate_for_keys: ctx.fanout is not set")
        assert self._ctx is not None
        plan = self._fanout
        rng = np.random.default_rng(cfg.seed)
        temperature = _similarity_to_sampling_temperature(cfg.similarity)
        col_order = [c.name for c in self._ctx.table_schema.columns]
        chunk_rows = max(1, int(cfg.batch_size or 1000))
        for chunk in expand_keys(plan, keys, self._ctx.pipeline_run_id, chunk_rows):
            n = len(chunk)
            columns = self._backend.sample_columns(n, rng, temperature=temperature)
            for name in self._free_text_cols:
                columns[name] = self._freetext_hook.sample(self._profiles[name], n, cfg, rng)
            for j, name in enumerate(plan.driving_cols):
                columns[name] = [key[j] for key, _ in chunk]
            if plan.cells is not None:
                for j, name in enumerate(plan.cells.cols):
                    columns[name] = [cell[j] for _, cell in chunk]
            for i in range(n):
                row = self._assemble_row(columns, col_order, i)
                try:
                    yield self._record_model.model_validate(row)
                except Exception:
                    continue
```

`_assemble_row` calls `enforce_value(profile, raw)` on every column; the driving columns' profile is the empty CATEGORICAL placeholder above — confirm `enforce_value` returns the raw value unchanged for a CATEGORICAL profile with empty `categories` (read `sdfb_core/engines/b2_library/fidelity.py::enforce_value`; if it snaps to the nearest category, skip enforcement for names in `plan.columns` by passing the raw value through in `_assemble_row` via an extra `passthrough: frozenset[str]` parameter).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/ -q`
Expected: all pass, including `test_both_engines_implement_generate_for_keys`.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py packages/sdfb-tests/tests/unit/engines/test_b2_generation_plan.py
git commit -m "feat(b2): generate_for_keys parity with B.1 (ADR 0036)"
```

---

### Task 6: `GenerateRecordsDoFn` accepts a `keys` request

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` (`_process_with_scope`)
- Test: `packages/sdfb-tests/tests/unit/dofns/test_generate_keys_request.py` (new)

**Interfaces:**
- Request shape: `{"batch_id": int, "keys": list[tuple]}` (alongside `{"batch_id": int, "n": int}`). Output rows are identical in shape to today's.
- Milestones: `batch_start batch_id= keys=` and `batch_done batch_id= keys= rows= seconds=` for key batches.

- [ ] **Step 1: Write the failing test**

```python
# packages/sdfb-tests/tests/unit/dofns/test_generate_keys_request.py
"""ADR 0036: the Generate DoFn drives the engine from parent keys."""

from __future__ import annotations

import logging

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.testing.util import assert_that
from sdfb_beam.dofns.generate import GenerateRecordsDoFn
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext
from sdfb_tests.fakes import FakeModelClient

_SCHEMA = TableSchema.model_validate(
    {"table_info": {"table_id": "p.src.child_t"},
     "schema": [
         {"name": "PID", "type": "STRING", "mode": "REQUIRED"},
         {"name": "CAT", "type": "STRING", "mode": "REQUIRED"},
         {"name": "AMT", "type": "INT64", "mode": "REQUIRED"},
     ]}
)
_ROWS = [{"PID": f"P{i:04d}", "CAT": "abc"[i % 3], "AMT": i} for i in range(60)]


def _ctx() -> GenerationContext:
    return GenerationContext(
        table_schema=_SCHEMA, reference_rows=_ROWS, reference_digest="dofn-fanout",
        pipeline_run_id="dofn-run", pk_columns=["PID", "CAT"],
        fanout={"driving_cols": ["PID"], "histogram": {"2": 1},
                "cells": {"cols": ["CAT"], "rows": [["a"], ["b"], ["c"]], "counts": [1, 1, 1]},
                "exact_cells": True},
    )


def test_keys_request_yields_two_children_per_key(caplog):
    dofn = GenerateRecordsDoFn(
        engine_name="b1_rag", model_client=FakeModelClient(reference_pool=_ROWS), ctx=_ctx(),
    )
    request = {"batch_id": 0, "keys": [("K1",), ("K2",), ("K3",)]}

    def _check(rows):
        rows = list(rows)
        assert len(rows) == 6, rows
        assert {r["PID"] for r in rows} == {"K1", "K2", "K3"}
        for pid in ("K1", "K2", "K3"):
            cats = [r["CAT"] for r in rows if r["PID"] == pid]
            assert len(set(cats)) == 2

    with caplog.at_level(logging.INFO, logger="sdfb.milestone"), beam.Pipeline(
        options=PipelineOptions(["--runner=DirectRunner"])
    ) as p:
        out = p | beam.Create([request]) | beam.ParDo(dofn).with_outputs("failed", main="main")
        assert_that(out.main, _check)
    assert "name=batch_done" in caplog.text and "keys=3" in caplog.text
```

If `sdfb_tests.fakes.FakeModelClient` has a different constructor, copy the one used in `tests/unit/test_relational_pipeline.py` (`FakeModelClient(reference_pool=...)`) — that test is the reference.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/test_generate_keys_request.py -q`
Expected: `KeyError: 'n'`.

- [ ] **Step 3: Implement**

In `_process_with_scope`, replace `n = int(request["n"])` and the following seed/cfg/log/generate block so that both shapes are handled. Concretely:

```python
        keys = request.get("keys")
        n = len(keys) if keys is not None else int(request["n"])
        batch_id = int(request["batch_id"])
        ... (seed / pool_seed derivation unchanged) ...
        cfg = GenerationConfig(seed=seed, batch_size=n if keys is None else self._chunk_rows(), ...)
        if keys is not None:
            log_milestone("batch_start", batch_id=batch_id, keys=n)
            records = self._engine.generate_for_keys([tuple(k) for k in keys], cfg)
        else:
            log_milestone("batch_start", batch_id=batch_id, n=n)
            records = self._engine.generate_batch(n, cfg)
```

and iterate `records` where `self._engine.generate_batch(n, cfg)` was iterated. In `batch_done`, add `keys=n` when `keys is not None`. Add:

```python
    def _chunk_rows(self) -> int:
        """Rows the engine samples at once for a key batch — the run's
        batch size, threaded through ctx (design 2026-09-10 §5)."""
        return max(1, int(getattr(self.ctx, "num_rows", 0) and 0) or 1000)
```

Simpler and explicit: add `chunk_rows: int = 1000` to `GenerateRecordsDoFn.__init__` and use it (`_generate_pardo` in Task 7 passes `config.batch_size`). Delete `_chunk_rows` if you take this route (you should).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/ packages/sdfb-tests/tests/unit/test_pipeline.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-beam/src/sdfb_beam/dofns/generate.py packages/sdfb-tests/tests/unit/dofns/test_generate_keys_request.py
git commit -m "feat(dofn): keys request shape drives generate_for_keys (ADR 0036)"
```

---

### Task 7: Composer — key batches from the parent, no side input for driven edges

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (`PipelineConfig`, `FkEdgeSpec`, `build_pipeline`, `_generate_pardo`, `build_relational_pipeline`, new `_fanout_requests`)
- Test: `packages/sdfb-tests/tests/unit/test_relational_pipeline.py`

**Interfaces:**
- `PipelineConfig.fanout: dict | None = None` (payload → ctx).
- `FkEdgeSpec.mode: str = "side_input"` (`"side_input" | "fanout" | "implied"`), `FkEdgeSpec.keys_per_batch: int = 100`.
- `build_pipeline(..., requests: Any = None)` — when given, a PCollection of request dicts replaces `Create(request_specs)`.
- `_fanout_requests(parent_valid, edge: FkEdgeSpec, prefix: str) -> PCollection[dict]`.
- `_generate_pardo(config, ctx, fk_side)` passes `chunk_rows=config.batch_size`.

- [ ] **Step 1: Write the failing test**

Append to `packages/sdfb-tests/tests/unit/test_relational_pipeline.py`:

```python
def test_driven_child_is_generated_from_parent_keys_without_a_side_input(
    tmp_path, customers_schema, customers_reference
):
    """ADR 0036: every child FK value is a landed parent key, the PK is
    unique by construction, and no fk side input exists in the graph."""
    parent_cfg = PipelineConfig(
        table_schema=customers_schema, engine_name="b1_rag",
        model_client=FakeModelClient(reference_pool=customers_reference),
        num_rows=50, batch_size=25, run_id="fan-parent",
        landing_table="p.land.customers", log_table_prefix="customers",
        identity_columns=("customer_id",),
    )
    child_schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.src.orders_fan"},
         "schema": [
             {"name": "CUST_ID", "type": "INT64", "mode": "REQUIRED"},
             {"name": "LINE", "type": "STRING", "mode": "REQUIRED"},
             {"name": "AMOUNT", "type": "INT64", "mode": "REQUIRED"},
         ]}
    )
    child_ref = [{"CUST_ID": 900000 + i, "LINE": "xyz"[i % 3], "AMOUNT": i * 3} for i in range(40)]
    child_cfg = PipelineConfig(
        table_schema=child_schema, engine_name="b1_rag",
        model_client=FakeModelClient(reference_pool=child_ref),
        num_rows=100,  # derived expectation; not a request count
        batch_size=20, run_id="fan-child", landing_table="p.land.orders_fan",
        log_table_prefix="orders_fan", pk_columns=("CUST_ID", "LINE"),
        uniqueness_mode="streaming",
        fanout={"driving_cols": ["CUST_ID"], "histogram": {"0": 1, "1": 2, "3": 1},
                "cells": {"cols": ["LINE"], "rows": [["x"], ["y"], ["z"]], "counts": [1, 1, 1]},
                "exact_cells": True},
    )
    specs = [
        TableSpec(config=parent_cfg, reference_rows=customers_reference,
                  landing_sink=WriteToJsonLines(str(tmp_path / "parent")),
                  dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_parent"))),
        TableSpec(config=child_cfg, reference_rows=child_ref,
                  landing_sink=WriteToJsonLines(str(tmp_path / "child")),
                  dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_child")),
                  parent_edges=(FkEdgeSpec(child_cols=("CUST_ID",), ref_cols=("customer_id",),
                                           parent_landing="p.land.customers",
                                           parent_pk=("customer_id",), mode="fanout",
                                           keys_per_batch=10),)),
    ]
    with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
        results = build_relational_pipeline(p, specs)
    labels = {t.full_label for t in p.transforms_stack[0].parts} if hasattr(p, "transforms_stack") else set()
    parent_rows = _read_jsonl(tmp_path / "parent")
    child_rows = _read_jsonl(tmp_path / "child")
    parent_keys = {r["customer_id"] for r in parent_rows}
    assert child_rows and {r["CUST_ID"] for r in child_rows} <= parent_keys
    assert len({(r["CUST_ID"], r["LINE"]) for r in child_rows}) == len(child_rows)  # PK unique
    per_key = {}
    for r in child_rows:
        per_key.setdefault(r["CUST_ID"], 0)
        per_key[r["CUST_ID"]] += 1
    assert set(per_key.values()) <= {1, 3}
    assert "orders_fan/edge0/FkSample" not in "".join(labels)  # no side-input sample
```

Replace the `labels` line with a direct check if `transforms_stack` is awkward: collect labels via `p.visit(...)` with a `PipelineVisitor` that records `transform_node.full_label`, and assert no label contains `FkSample` or `FkPools` under the child's prefix but one contains `orders_fan/FanoutKeys`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/test_relational_pipeline.py -q -k driven_child`
Expected: `TypeError: FkEdgeSpec.__init__() got an unexpected keyword argument 'mode'`.

- [ ] **Step 3: Implement**

In `pipeline.py`:

1. `PipelineConfig`: add `fanout: dict | None = None` (comment: ADR 0036 driven-child recipe; threaded to `GenerationContext.fanout`). In `build_pipeline`'s `GenerationContext(...)` add `fanout=config.fanout`.
2. `FkEdgeSpec`: add
   ```python
       # ADR 0036: "side_input" (ADR 0030 key sample), "fanout" (this edge DRIVES
       # the child — its parent's keys are the child's generation input) or
       # "implied" (satisfied by construction through the driving edge; no DAG
       # edge at all).
       mode: str = "side_input"
       keys_per_batch: int = 100
   ```
3. `build_pipeline(... , requests: Any = None)`: build `request_specs` and `Create` only when `requests is None`; otherwise `requests = requests`. Pass `chunk_rows=config.batch_size` into `GenerateRecordsDoFn` via `_generate_pardo`.
4. Add:
   ```python
   def _fanout_requests(parent_valid, edge: FkEdgeSpec, prefix: str):
       """The parent's landed key tuples as key-batch requests for a
       DRIVEN child (ADR 0036): project, Distinct only when the tuple lacks
       the parent PK, Reshuffle off the parent's write path, batch."""
       keys = parent_valid | f"{prefix}FanoutKeys" >> beam.Map(
           lambda r, rc=edge.ref_cols: tuple(r[c] for c in rc)
       ) | f"{prefix}FanoutDropNull" >> beam.Filter(lambda t: all(v is not None for v in t))
       if not set(edge.parent_pk) <= set(edge.ref_cols):
           keys = keys | f"{prefix}FanoutDistinct" >> beam.Distinct()
       batches = (
           keys
           | f"{prefix}FanoutReshuffle" >> beam.Reshuffle()
           | f"{prefix}FanoutBatch" >> beam.BatchElements(
               min_batch_size=edge.keys_per_batch, max_batch_size=edge.keys_per_batch
           )
       )
       return batches | f"{prefix}FanoutRequests" >> beam.Map(
           lambda ks: {"batch_id": abs(hash(ks[0])) % (1 << 31), "keys": list(ks)}
       )
   ```
   `hash()` of a tuple of str/int is process-salted for str — replace with `int.from_bytes(hashlib.blake2b(repr(ks[0]).encode(), digest_size=4).digest(), "big")` (import hashlib) so batch ids are stable.
5. In `build_relational_pipeline`: for each spec, split `spec.parent_edges` by mode. `fanout` edges (at most one; assert) → `requests = _fanout_requests(parent_valid, edge, prefix)`, `side = None`. `side_input` edges → today's `_edge_key_pools` path. `implied` edges → nothing. Pass `requests=requests` to `build_pipeline`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/test_relational_pipeline.py packages/sdfb-tests/tests/unit/test_pipeline.py packages/sdfb-tests/tests/unit/test_pipeline_uniqueness_wiring.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-beam/src/sdfb_beam/pipeline.py packages/sdfb-tests/tests/unit/test_relational_pipeline.py
git commit -m "feat(pipeline): driven children generate from parent key batches; fanout/implied edge modes (ADR 0036)"
```

---

### Task 8: Driver-side fan-out measurement and its BigQuery cache

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/io/fanout_stats.py`
- Create: `config/bq_schema/synthetic_data_quality/fk_fanout_stats.schema.json`
- Test: `packages/sdfb-tests/tests/unit/io/test_fanout_stats.py`

**Interfaces:**
- `measure_fanout(*, source_child: str, child_cols: tuple[str, ...], source_parent: str, ref_cols: tuple[str, ...], cell_cols: tuple[str, ...], client) -> dict` — returns `{"histogram": {"0": n0, ...}, "cells": {"cols": [...], "rows": [[...]], "counts": [...]} | None, "parents": int, "children": int}`; `client` is a BigQuery client whose `.query(sql).result()` yields row-like mappings (injectable fake in tests).
- `class BigQueryFanoutStatsStore(table_fqn: str, *, client=None)` with `get(source_child, child_cols, model_sha) -> dict | None` and `put(source_child, child_cols, model_sha, payload) -> None` (LOAD job, never streaming).
- `fanout_payload(measured: dict, driving_cols: tuple[str, ...], exact_cells: bool) -> dict` — the `FanoutPlan` payload (Task 1 shape).
- Milestone `fk_fanout_measured edge= parents= children= mean= p50= p95= max= zero_share= source=measured|cache`.

- [ ] **Step 1: Write the failing tests**

```python
# packages/sdfb-tests/tests/unit/io/test_fanout_stats.py
"""ADR 0036: the SOURCE fan-out histogram and PK cells, measured once,
cached by (source child, edge cols, model sha)."""

from __future__ import annotations

from sdfb_beam.io.fanout_stats import (
    BigQueryFanoutStatsStore,
    fanout_payload,
    measure_fanout,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class _Client:
    """Answers the three queries by shape of SQL."""

    def __init__(self):
        self.sql: list[str] = []
        self.loaded: list[list[dict]] = []

    def query(self, sql, job_config=None):
        self.sql.append(sql)
        if "AS k" in sql:
            return _Result([{"k": 1, "parents": 30}, {"k": 2, "parents": 20}])
        if "COUNT(DISTINCT" in sql or "APPROX_COUNT_DISTINCT" in sql:
            return _Result([{"parents": 100}])
        if "AS n FROM" in sql and "GROUP BY" in sql:  # cells
            return _Result([{"C2": "C0", "D18": "K0", "n": 40}, {"C2": "C1", "D18": "K1", "n": 20}])
        if "SELECT payload" in sql:
            return _Result([])
        raise AssertionError(sql)

    def load_table_from_json(self, rows, table, job_config=None):
        self.loaded.append(rows)
        return _Result([])


def test_measure_fanout_builds_histogram_with_zero_bucket_and_cells():
    client = _Client()
    out = measure_fanout(
        source_child="p.src.C_TABLE", child_cols=("D_COL_001",),
        source_parent="p.src.B_TABLE", ref_cols=("D_COL_001",),
        cell_cols=("C2", "D18"), client=client,
    )
    # 100 parents, 50 with children (30 x1 + 20 x2) -> 50 in the zero bucket
    assert out["histogram"] == {"0": 50, "1": 30, "2": 20}
    assert out["parents"] == 100 and out["children"] == 70
    assert out["cells"] == {"cols": ["C2", "D18"], "rows": [["C0", "K0"], ["C1", "K1"]],
                            "counts": [40.0, 20.0]}


def test_measure_fanout_without_cell_columns():
    out = measure_fanout(
        source_child="p.src.C", child_cols=("K",), source_parent="p.src.P",
        ref_cols=("K",), cell_cols=(), client=_Client(),
    )
    assert out["cells"] is None


def test_store_round_trip_uses_a_load_job():
    client = _Client()
    store = BigQueryFanoutStatsStore("p.synthetic_data_quality.fk_fanout_stats", client=client)
    assert store.get("p.src.C", ("K",), "abc") is None
    store.put("p.src.C", ("K",), "abc", {"histogram": {"0": 1}, "cells": None, "parents": 1, "children": 0})
    (rows,) = client.loaded
    assert rows[0]["source_table"] == "p.src.C" and rows[0]["model_sha"] == "abc"
    assert '"histogram"' in rows[0]["payload"]


def test_fanout_payload_shape():
    payload = fanout_payload(
        {"histogram": {"0": 1, "2": 1}, "cells": None, "parents": 2, "children": 2},
        driving_cols=("K", "INH"), exact_cells=False,
    )
    assert payload == {"driving_cols": ["K", "INH"], "histogram": {"0": 1, "2": 1},
                       "cells": None, "exact_cells": False}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/io/test_fanout_stats.py -q`
Expected: `ModuleNotFoundError: sdfb_beam.io.fanout_stats`.

- [ ] **Step 3: Implement**

`config/bq_schema/synthetic_data_quality/fk_fanout_stats.schema.json`:

```json
[
  {"name": "source_table", "type": "STRING", "mode": "REQUIRED"},
  {"name": "edge_cols", "type": "STRING", "mode": "REQUIRED"},
  {"name": "model_sha", "type": "STRING", "mode": "REQUIRED"},
  {"name": "measured_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
  {"name": "payload", "type": "STRING", "mode": "REQUIRED"}
]
```

`packages/sdfb-beam/src/sdfb_beam/io/fanout_stats.py`:

```python
"""SOURCE fan-out statistics for a driven child (design 2026-09-10, ADR 0036).

Per driving edge, measured driver-side on the SOURCE child table: the
histogram of children per parent tuple (zero bucket from the source
parent's distinct tuple count) and the joint PK-completing cells. One
scan of the FK + cell columns; cached in ``synthetic_data_quality
.fk_fanout_stats`` by (source child, edge cols, model sha) so a
re-launch pays nothing. A 5k-row sample cannot measure this: it almost
never holds two rows of one parent (design §10).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sdfb_core.observability import log_milestone

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _cols(cols: tuple[str, ...]) -> str:
    for c in cols:
        if not _IDENTIFIER.match(c):
            raise ValueError(f"not a BigQuery column name: {c!r}")
    return ", ".join(f"`{c}`" for c in cols)


def _rows(job) -> list[dict]:
    return [dict(r) if not isinstance(r, dict) else r for r in job.result()]


def measure_fanout(
    *,
    source_child: str,
    child_cols: tuple[str, ...],
    source_parent: str,
    ref_cols: tuple[str, ...],
    cell_cols: tuple[str, ...],
    client: Any,
) -> dict:
    """``{"histogram", "cells", "parents", "children"}`` from three
    GROUP BY queries; ``cells`` is None without cell columns."""
    child_sql = (
        f"SELECT n AS k, COUNT(*) AS parents FROM (SELECT {_cols(child_cols)}, "
        f"COUNT(*) AS n FROM `{source_child}` GROUP BY {_cols(child_cols)}) GROUP BY n"
    )
    histogram = {int(r["k"]): int(r["parents"]) for r in _rows(client.query(child_sql))}
    parent_sql = (
        f"SELECT COUNT(DISTINCT CONCAT({', '.join(f'CAST(`{c}` AS STRING), \"\\x1f\"' for c in ref_cols)}"
        f")) AS parents FROM `{source_parent}` WHERE "
        + " AND ".join(f"`{c}` IS NOT NULL" for c in ref_cols)
    )
    (parent_row,) = _rows(client.query(parent_sql))
    parents = int(parent_row["parents"])
    with_children = sum(histogram.values())
    histogram[0] = max(0, parents - with_children) + histogram.get(0, 0)
    children = sum(k * n for k, n in histogram.items())
    cells = None
    if cell_cols:
        cell_sql = (
            f"SELECT {_cols(cell_cols)}, COUNT(*) AS n FROM `{source_child}` "
            f"GROUP BY {_cols(cell_cols)}"
        )
        rows = _rows(client.query(cell_sql))
        cells = {
            "cols": list(cell_cols),
            "rows": [[r[c] for c in cell_cols] for r in rows],
            "counts": [float(r["n"]) for r in rows],
        }
    return {
        "histogram": {str(k): n for k, n in sorted(histogram.items())},
        "cells": cells,
        "parents": parents,
        "children": children,
    }


def fanout_payload(measured: dict, driving_cols: tuple[str, ...], exact_cells: bool) -> dict:
    return {
        "driving_cols": list(driving_cols),
        "histogram": dict(measured["histogram"]),
        "cells": measured.get("cells"),
        "exact_cells": bool(exact_cells),
    }


def log_fanout_measured(edge: str, measured: dict, *, source: str) -> None:
    hist = {int(k): n for k, n in measured["histogram"].items()}
    total = sum(hist.values()) or 1
    order = sorted(hist)
    cum = 0
    p50 = p95 = order[-1]
    for k in order:
        cum += hist[k]
        if cum / total >= 0.5 and p50 == order[-1]:
            p50 = k
        if cum / total >= 0.95:
            p95 = k
            break
    log_milestone(
        "fk_fanout_measured",
        edge=edge,
        parents=measured["parents"],
        children=measured["children"],
        mean=round(measured["children"] / max(1, measured["parents"]), 4),
        p50=p50,
        p95=p95,
        max=order[-1],
        zero_share=round(hist.get(0, 0) / total, 4),
        source=source,
    )


class BigQueryFanoutStatsStore:
    """Cache of measured fan-out payloads in ``fk_fanout_stats``."""

    def __init__(self, table_fqn: str, *, client: Any = None) -> None:
        self.table_fqn = table_fqn
        self._client = client

    def __getstate__(self) -> dict:
        state = self.__dict__.copy()
        state["_client"] = None
        return state

    def _bq(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client()
        return self._client

    def get(self, source_table: str, edge_cols: tuple[str, ...], model_sha: str) -> dict | None:
        from google.cloud import bigquery  # driver-side only

        sql = (
            f"SELECT payload FROM `{self.table_fqn}` WHERE source_table = @t "
            f"AND edge_cols = @e AND model_sha = @s ORDER BY measured_at DESC LIMIT 1"
        )
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("t", "STRING", source_table),
            bigquery.ScalarQueryParameter("e", "STRING", ",".join(edge_cols)),
            bigquery.ScalarQueryParameter("s", "STRING", model_sha),
        ])
        rows = _rows(self._bq().query(sql, job_config=job_config))
        return json.loads(rows[0]["payload"]) if rows else None

    def put(self, source_table: str, edge_cols: tuple[str, ...], model_sha: str, payload: dict) -> None:
        from google.cloud import bigquery

        row = {
            "source_table": source_table,
            "edge_cols": ",".join(edge_cols),
            "model_sha": model_sha,
            "measured_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(payload, separators=(",", ":"), default=str),
        }
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
        self._bq().load_table_from_json([row], self.table_fqn, job_config=job_config).result()


__all__ = ["BigQueryFanoutStatsStore", "fanout_payload", "log_fanout_measured", "measure_fanout"]
```

In the unit test the fake client has no `google.cloud` — `get`/`put` import `bigquery` lazily; the test environment has `google-cloud-bigquery` installed (the stats store tests rely on it), so the import succeeds and `QueryJobConfig` is constructible offline. If not, guard with `try: from google.cloud import bigquery except ImportError: job_config = None`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/io/test_fanout_stats.py -q`; `uv run --no-sync ruff check packages/sdfb-beam/src/sdfb_beam/io/fanout_stats.py`.
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-beam/src/sdfb_beam/io/fanout_stats.py config/bq_schema/synthetic_data_quality/fk_fanout_stats.schema.json packages/sdfb-tests/tests/unit/io/test_fanout_stats.py
git commit -m "feat(io): source fan-out histogram + PK cells measurement with a BigQuery cache (ADR 0036)"
```

---

### Task 9: Preflight for driven children

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/preflight.py` (`preflight` signature, new `_check_driven_pk`, roles milestones)
- Test: `packages/sdfb-tests/tests/unit/cli/test_preflight_fanout.py` (new)

**Interfaces:**
- `preflight(..., fanout: dict | None = None, edge_roles: Mapping[FkEdge, str] | None = None)`.
  - With `edge_roles` given: one `fk_edge_role edge= role=` milestone per edge.
  - With `fanout` given (the Task 8 payload): the ADR 0035 random-draw check is skipped for this table; instead `_check_driven_pk(table_schema, effective_pk, fanout, profiles)` stops with `[preflight P4]` when every PK member outside the driving edge is a cell column and `max_k > cells`.
- `PreflightResult.derived_rows: int | None` — `round(parent_rows × mean)` when `fk_parent_rows` names the driving parent; else None.
- `pk_cell_columns(effective_pk, driving_cols, profiles) -> tuple[tuple[str, ...], bool]` — `(cell cols, exact)`: the PK members outside the driving edge that are CATEGORICAL/CONSTANT per profile; `exact` is True when those are ALL the remaining members.

- [ ] **Step 1: Write the failing tests**

```python
# packages/sdfb-tests/tests/unit/cli/test_preflight_fanout.py
"""ADR 0036 preflight: driven children are checked per key, not by the
random-draw model; edge roles are logged; the row count derives."""

from __future__ import annotations

import logging

import pytest
from sdfb_beam.cli.preflight import pk_cell_columns, preflight
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.engines.b1_rag.profile import profile_columns

_MODEL = """
model: m
tables:
  parent:
    pk: [PID]
  child:
    pk: [PID, C2, D18]
    fk:
      - cols: [PID]
        ref: parent
        ref_cols: [PID]
"""
_REG = RelationshipRegistry.from_sources([("config/relationships/m.yaml", _MODEL)])


def _schema(extra: str | None = None) -> TableSchema:
    cols = [
        {"name": "PID", "type": "STRING", "mode": "REQUIRED"},
        {"name": "C2", "type": "STRING", "mode": "REQUIRED"},
        {"name": "D18", "type": "STRING", "mode": "REQUIRED"},
        {"name": "AMT", "type": "INT64", "mode": "REQUIRED"},
    ]
    if extra:
        cols.append({"name": "SEQ", "type": extra, "mode": "REQUIRED"})
    return TableSchema.model_validate({"table_info": {"table_id": "p.d.child"}, "schema": cols})


def _rows(n: int = 480) -> list[dict]:
    return [{"PID": f"E2F3{i:020X}", "C2": f"C{i % 4}", "D18": f"K{i % 3}", "AMT": i, "SEQ": i}
            for i in range(n)]


def _fanout(max_k: int) -> dict:
    return {"driving_cols": ["PID"], "histogram": {"0": 10, str(max_k): 5},
            "cells": {"cols": ["C2", "D18"], "rows": [[f"C{i % 4}", f"K{i % 3}"] for i in range(12)],
                      "counts": [1.0] * 12},
            "exact_cells": True}


def test_pk_cell_columns_are_the_sampled_members_outside_the_edge():
    profiles = profile_columns(_schema("INT64"), _rows())
    assert pk_cell_columns(("PID", "C2", "D18"), ("PID",), profiles) == (("C2", "D18"), True)
    assert pk_cell_columns(("PID", "C2", "D18", "SEQ"), ("PID",), profiles) == (("C2", "D18"), False)


def test_fanout_within_cells_passes_and_derives_rows(caplog):
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        result = preflight(
            _schema(), (), (), _rows(), relations=_REG.relations("child"),
            num_rows=10_000_000, fk_parent_rows={"parent": 10_000_000},
            blocker_failure_ratio=0.2, fanout=_fanout(12), edge_roles=_REG.edge_roles("child"),
        )
    # mean k = (10*0 + 5*12)/15 = 4 -> 40M derived rows
    assert result.derived_rows == 40_000_000
    assert "name=fk_edge_role" in caplog.text and "role=driving" in caplog.text
    assert "name=pk_capacity_tight" not in caplog.text  # the random-draw model is off


def test_fanout_beyond_cells_stops():
    with pytest.raises(SystemExit, match=r"preflight P4.*13 children.*12 cells"):
        preflight(
            _schema(), (), (), _rows(), relations=_REG.relations("child"),
            num_rows=1_000, fk_parent_rows={"parent": 1_000},
            blocker_failure_ratio=0.2, fanout=_fanout(13),
        )


def test_an_unbounded_member_makes_the_check_inexact_and_passes():
    model = _MODEL.replace("pk: [PID, C2, D18]", "pk: [PID, C2, D18, SEQ]")
    reg = RelationshipRegistry.from_sources([("config/relationships/m.yaml", model)])
    preflight(
        _schema("INT64"), (), (), _rows(), relations=reg.relations("child"),
        num_rows=1_000, fk_parent_rows={"parent": 1_000},
        blocker_failure_ratio=0.2, fanout=_fanout(500),
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/test_preflight_fanout.py -q`
Expected: `ImportError: cannot import name 'pk_cell_columns'`.

- [ ] **Step 3: Implement**

In `preflight.py`:

```python
def pk_cell_columns(
    effective_pk: tuple[str, ...],
    driving_cols: tuple[str, ...],
    profiles: Mapping[str, ColumnProfile],
) -> tuple[tuple[str, ...], bool]:
    """``(cell columns, exact)`` for a driven child (ADR 0036): the PK
    members outside the driving edge that the engine re-emits from a
    domain (CATEGORICAL / CONSTANT); ``exact`` when they are ALL the
    remaining members, so the cells alone must key the child."""
    rest = tuple(c for c in effective_pk if c not in set(driving_cols))
    cells = tuple(
        c for c in rest
        if (p := profiles.get(c)) is not None
        and p.kind in (ColumnKind.CATEGORICAL, ColumnKind.CONSTANT)
    )
    return cells, len(cells) == len(rest)


def _check_driven_pk(
    table_schema: TableSchema,
    effective_pk: tuple[str, ...],
    fanout: Mapping,
    profiles: Mapping[str, ColumnProfile],
) -> None:
    """P4 for a DRIVEN child: the largest source fan-out must fit in the
    PK-completing cells, else the declared PK is not a key in the source."""
    driving = tuple(fanout.get("driving_cols") or ())
    cells, exact = pk_cell_columns(effective_pk, driving, profiles)
    if not exact:
        return
    table = fanout.get("cells") or {}
    n_cells = len(table.get("rows") or ())
    max_k = max(int(k) for k in (fanout.get("histogram") or {"0": 0}))
    if max_k > n_cells:
        raise SystemExit(
            f"[preflight P4] {table_schema.fqn}: the driving edge "
            f"({','.join(driving)}) fans out to {max_k} children per parent "
            f"in the source, but the PK-completing members {list(cells)} "
            f"cover only {n_cells} cells — the declared PK "
            f"{list(effective_pk)} is not a key of the source. Fix the `pk:` "
            f"in the relationship model."
        )
```

`preflight()` gains `fanout: Mapping | None = None, edge_roles: Mapping[FkEdge, str] | None = None`; `PreflightResult` gains `derived_rows: int | None = None`. In the body, after P5:

```python
    if edge_roles:
        for edge, role in edge_roles.items():
            log_milestone("fk_edge_role", table=fqn,
                          edge=f"({','.join(edge.cols)})->{edge.ref}", role=role)
    derived_rows: int | None = None
    if fanout is not None:
        if num_rows > 0 and effective_pk:
            _check_driven_pk(table_schema, tuple(effective_pk), fanout, profiles)
        hist = {int(k): int(n) for k, n in (fanout.get("histogram") or {}).items()}
        total = sum(hist.values())
        driving_ref = next((e.ref for e, r in (edge_roles or {}).items() if r == "driving"), None)
        parent_rows = (fk_parent_rows or {}).get(driving_ref) if driving_ref else None
        if total and parent_rows:
            derived_rows = round(parent_rows * sum(k * n for k, n in hist.items()) / total)
    elif num_rows > 0 and effective_pk:
        fk_key_sample_caps = _check_pk_capacity(...)  # the existing ADR 0035 call, unchanged
```

and return `derived_rows=derived_rows` in the `PreflightResult`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/ -q`
Expected: pass (the ADR 0035 tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-beam/src/sdfb_beam/cli/preflight.py packages/sdfb-tests/tests/unit/cli/test_preflight_fanout.py
git commit -m "feat(preflight): driven-child per-key PK check, edge roles, derived rows (ADR 0036)"
```

---

### Task 10: Launcher wiring — roles, measurement, derived rows, config

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (`build_parser` flags; `_load_reference_and_preflight`; `in_set_parent_edges`; `_prepare_table_spec`; `_run_relational_job` card)
- Test: `packages/sdfb-tests/tests/unit/cli/test_fanout_launch_wiring.py` (new)

**Interfaces:**
- New flags: `--fk_fanout_stats_table` (default `""`; FQN of `synthetic_data_quality.fk_fanout_stats`; empty = measure without caching), `--driven_uniqueness_mode` (default `streaming`, choices `UNIQUENESS_MODES`).
- `resolve_fanout(registry, landing_table, source_table, *, in_set_names, num_rows_by_ref: Mapping[str, int], reference_rows, table_schema, stats_store, bq_client) -> tuple[dict | None, dict[FkEdge, str]]` — `(fanout payload or None, edge roles)`; measures (or reads the cache) for the driving edge when its parent is in-set; source parent FQN derived with `derive_source_fqn(parent_landing, source_table)` (exists).
- `in_set_parent_edges(registry, landing_table, *, in_set_names, key_sample_caps, edge_roles, keys_per_batch)` — emits `FkEdgeSpec(mode="fanout"|"implied"|"side_input")`.
- `PipelineConfig.num_rows` for a driven child = `pf.derived_rows`; `batch_size = resolve_batch_size(args.batch_size, derived)`; `uniqueness_mode = args.driven_uniqueness_mode if not identity cols else "exact"`; `fanout=payload`.
- `relational_single_job rows_detail=<name>:<rows>,...` on the launch card.

- [ ] **Step 1: Write the failing tests**

```python
# packages/sdfb-tests/tests/unit/cli/test_fanout_launch_wiring.py
"""ADR 0036 launcher: roles -> measurement -> derived rows -> config."""

from __future__ import annotations

from sdfb_beam.cli.run_pipeline import (
    in_set_parent_edges,
    resolve_driven_uniqueness_mode,
    resolve_fanout,
)
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relationships import RelationshipRegistry

_MODEL = """
model: kw
tables:
  B_TABLE:
    pk: [D_COL_001]
  C_TABLE:
    pk: [D_COL_001, C_COL_002]
    fk:
      - cols: [D_COL_001, D_COL_024]
        ref: B_TABLE
        ref_cols: [D_COL_001, D_COL_024]
  A_TABLE:
    pk: [D_COL_024, X]
    fk:
      - cols: [D_COL_024]
        ref: C_TABLE
        ref_cols: [D_COL_024]
        drives: true
      - cols: [D_COL_024]
        ref: B_TABLE
        ref_cols: [D_COL_024]
"""
_REG = RelationshipRegistry.from_sources([("config/relationships/kw.yaml", _MODEL)])
_SCHEMA = TableSchema.model_validate(
    {"table_info": {"table_id": "p.src.C_TABLE"},
     "schema": [{"name": n, "type": "STRING", "mode": "REQUIRED"}
                for n in ("D_COL_001", "D_COL_024", "C_COL_002")]}
)
_ROWS = [{"D_COL_001": f"K{i}", "D_COL_024": "A", "C_COL_002": "xy"[i % 2]} for i in range(40)]


class _Store:
    def __init__(self):
        self.saved = {}

    def get(self, t, cols, sha):
        return self.saved.get((t, cols, sha))

    def put(self, t, cols, sha, payload):
        self.saved[(t, cols, sha)] = payload


def _measure(**kw):
    return {"histogram": {"0": 1, "2": 1}, "cells": {"cols": ["C_COL_002"], "rows": [["x"], ["y"]],
            "counts": [1.0, 1.0]}, "parents": 2, "children": 2}


def test_resolve_fanout_measures_the_driving_edge_and_caches(monkeypatch):
    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "measure_fanout", _measure)
    store = _Store()
    payload, roles = resolve_fanout(
        _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=_ROWS, table_schema=_SCHEMA, stats_store=store, bq_client=object(),
    )
    assert payload["driving_cols"] == ["D_COL_001", "D_COL_024"]
    assert payload["exact_cells"] is True and payload["cells"]["cols"] == ["C_COL_002"]
    assert list(roles.values()) == ["driving"]
    assert store.saved  # cached under the model sha
    # second call hits the cache: measure_fanout must not run
    monkeypatch.setattr(rp, "measure_fanout", lambda **kw: (_ for _ in ()).throw(AssertionError("measured twice")))
    again, _ = resolve_fanout(
        _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=_ROWS, table_schema=_SCHEMA, stats_store=store, bq_client=object(),
    )
    assert again == payload


def test_root_has_no_fanout():
    payload, roles = resolve_fanout(
        _REG, "proj.synthetic_data.B_TABLE", "proj.src.B_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=[], table_schema=_SCHEMA, stats_store=None, bq_client=None,
    )
    assert payload is None and roles == {}


def test_edges_get_their_modes():
    roles = _REG.edge_roles("A_TABLE")
    edges = in_set_parent_edges(
        _REG, "proj.synthetic_data.A_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"}, key_sample_caps={},
        edge_roles=roles, keys_per_batch=250,
    )
    assert [(e.parent_landing.rsplit(".", 1)[-1], e.mode) for e in edges] == [
        ("C_TABLE", "fanout"), ("B_TABLE", "implied")]
    assert edges[0].keys_per_batch == 250


def test_driven_uniqueness_mode():
    assert resolve_driven_uniqueness_mode("streaming", driven=True, identity_cols=()) == "streaming"
    assert resolve_driven_uniqueness_mode("streaming", driven=True, identity_cols=("X",)) == "exact"
    assert resolve_driven_uniqueness_mode("streaming", driven=False, identity_cols=()) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/test_fanout_launch_wiring.py -q`
Expected: `ImportError: cannot import name 'resolve_fanout'`.

- [ ] **Step 3: Implement**

In `run_pipeline.py`:

1. Flags in the parser next to `--uniqueness_mode`:
   ```python
   p.add_argument("--fk_fanout_stats_table", default="",
                  help="FQN of synthetic_data_quality.fk_fanout_stats (ADR 0036 cache of "
                       "the SOURCE fan-out histogram + PK cells per driving edge). "
                       "Empty = measure every launch, never cache.")
   p.add_argument("--driven_uniqueness_mode", default="streaming", choices=list(UNIQUENESS_MODES),
                  help="Uniqueness mode for a DRIVEN child without identity columns "
                       "(ADR 0036): its PK is unique by construction, so `streaming` "
                       "measures duplicates without the landing-path barrier.")
   ```
2. Imports: `from sdfb_beam.io.fanout_stats import BigQueryFanoutStatsStore, fanout_payload, log_fanout_measured, measure_fanout`; `from sdfb_beam.cli.preflight import pk_cell_columns`; `from sdfb_core.engines.b1_rag.profile import profile_columns`.
3. Functions:
   ```python
   def resolve_driven_uniqueness_mode(flag: str, *, driven: bool, identity_cols: tuple) -> str | None:
       """None = not driven (keep --uniqueness_mode); identity columns keep `exact`."""
       if not driven:
           return None
       return "exact" if identity_cols else flag


   def resolve_fanout(
       registry, landing_table: str, source_table: str, *, in_set_names: set[str],
       reference_rows: list[dict], table_schema, stats_store, bq_client,
   ) -> tuple[dict | None, dict]:
       """(FanoutPlan payload or None, edge roles) for one table (ADR 0036)."""
       roles = registry.edge_roles(landing_table)  # RelationshipError propagates -> prep failure
       driving = next((e for e, r in roles.items() if r == "driving"), None)
       if driving is None or driving.ref.rsplit(".", 1)[-1] not in in_set_names:
           return None, roles
       relations = registry.relations(landing_table)
       pk = tuple(relations.pk) if relations else ()
       profiles = profile_columns(table_schema, reference_rows) if reference_rows else {}
       cell_cols, exact = pk_cell_columns(pk, tuple(driving.cols), profiles)
       source_parent = derive_source_fqn(
           parent_landing_fqn(driving.ref, derive_fk_parent_landing(landing_table)), source_table
       )
       sha = registry.sha12()
       measured = stats_store.get(source_table, tuple(driving.cols), sha) if stats_store else None
       source = "cache"
       if measured is None:
           measured = measure_fanout(
               source_child=source_table, child_cols=tuple(driving.cols),
               source_parent=source_parent, ref_cols=tuple(driving.ref_cols),
               cell_cols=cell_cols, client=bq_client,
           )
           source = "measured"
           if stats_store:
               stats_store.put(source_table, tuple(driving.cols), sha, measured)
       log_fanout_measured(f"({','.join(driving.cols)})->{driving.ref}", measured, source=source)
       return fanout_payload(measured, tuple(driving.cols), exact), roles
   ```
   Check the exact name/signature of the existing source-FQN helper (`grep -n "def derive_source_fqn" packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py`) and use it as it is.
4. `in_set_parent_edges(..., edge_roles: Mapping | None = None, keys_per_batch: int = 100)`: `mode = {"driving": "fanout", "implied": "implied"}.get(edge_roles.get(fk, ""), "side_input")`, `keys_per_batch=keys_per_batch`.
5. `_load_reference_and_preflight`: before `preflight(...)`, when `in_set_landing` is non-empty and `parse_bool_flag(args.generate_fk_relationships)`:
   ```python
   stats_store = BigQueryFanoutStatsStore(args.fk_fanout_stats_table) if args.fk_fanout_stats_table else None
   fanout, edge_roles = resolve_fanout(registry, args.landing_table, args.reference_table,
                                       in_set_names=in_set_names, reference_rows=reference_rows,
                                       table_schema=table_schema, stats_store=stats_store, bq_client=None)
   ```
   (`bq_client=None` → `measure_fanout` uses `bigquery.Client()` lazily: add `if client is None: from google.cloud import bigquery; client = bigquery.Client()` at the top of `measure_fanout`.) Pass `fanout=fanout, edge_roles=edge_roles` to `preflight`; return them alongside the existing tuple (extend the return to `(reference_rows, pf, fk_pools, fk_key_pools, source_distinct, fanout, edge_roles)` and update the two callers).
6. `_prepare_table_spec`: `driven = fanout is not None`; `num_rows = pf.derived_rows if driven and pf.derived_rows else args.num_rows`; `batch_size = resolve_batch_size(args.batch_size, num_rows)`; `mode = resolve_driven_uniqueness_mode(args.driven_uniqueness_mode, driven=driven, identity_cols=pf.identity_cols) or args.uniqueness_mode`; `PipelineConfig(..., num_rows=num_rows, batch_size=batch_size, uniqueness_mode=mode, fanout=fanout, ...)`; `in_set_parent_edges(..., edge_roles=edge_roles, keys_per_batch=max(1, round(batch_size / max(mean_fanout, 1e-6))))` where `mean_fanout` is computed from `fanout["histogram"]` (helper `_mean_k(hist)`), 100 when not driven.
7. `_run_relational_job`: after the specs are prepared, add `rows_detail=",".join(f"{name}:{s.config.num_rows}" for s in specs)` to the `relational_single_job` milestone. Because the derived count of a grandchild depends on its parent's derived count, prepare specs in plan order and pass `num_rows_by_landing` forward: `fk_parent_rows` (already built per table in `_load_reference_and_preflight`) must use the PARENT'S resolved count, not `args.num_rows` — keep a dict on `args` (`table_args._rows_by_landing = {...}` filled after each spec) and read `fk_parent_rows[fk.ref] = rows_by_landing.get(parent_landing, args.num_rows)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/ -q`; then the full suite, ruff, mypy core.
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py packages/sdfb-tests/tests/unit/cli/test_fanout_launch_wiring.py
git commit -m "feat(launcher): edge roles, source fan-out measurement, derived child rows, driven uniqueness mode (ADR 0036)"
```

---

### Task 11: Three-table DirectRunner acceptance test

**Files:**
- Test: `packages/sdfb-tests/tests/unit/test_fanout_three_tables.py` (new)

**Interfaces:** consumes Tasks 1–7 only (no BQ): payloads are hand-built exactly as the launcher would produce them.

- [ ] **Step 1: Write the test**

```python
# packages/sdfb-tests/tests/unit/test_fanout_three_tables.py
"""ADR 0036 acceptance on the laptop: B -> C -> A with C driven by B and
A driven by C (B implied). Every FK tuple exists in its parent, every PK
is unique, sizes follow the histograms, and A's implied edge holds."""

from __future__ import annotations

import json
from pathlib import Path

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import FkEdgeSpec, PipelineConfig, TableSpec, build_relational_pipeline
from sdfb_core.contracts import TableSchema
from sdfb_tests.fakes import FakeModelClient


def _schema(table: str, cols: list[tuple[str, str]]) -> TableSchema:
    return TableSchema.model_validate(
        {"table_info": {"table_id": f"p.src.{table}"},
         "schema": [{"name": n, "type": t, "mode": "REQUIRED"} for n, t in cols]}
    )


def _read(prefix: Path) -> list[dict]:
    rows = []
    for f in sorted(prefix.parent.glob(prefix.name + "*")):
        rows.extend(json.loads(line) for line in f.read_text().splitlines() if line)
    return rows


def test_three_tables_by_construction(tmp_path):
    b_schema = _schema("B_TABLE", [("D_COL_001", "STRING"), ("D_COL_024", "STRING"), ("AMT", "INT64")])
    b_ref = [{"D_COL_001": f"B{i:05d}", "D_COL_024": "AB"[i % 2], "AMT": i} for i in range(60)]
    c_schema = _schema("C_TABLE", [("D_COL_001", "STRING"), ("D_COL_024", "STRING"),
                                   ("C_COL_002", "STRING"), ("V", "INT64")])
    c_ref = [{"D_COL_001": f"X{i}", "D_COL_024": "A", "C_COL_002": "pqr"[i % 3], "V": i} for i in range(60)]
    a_schema = _schema("A_TABLE", [("D_COL_024", "STRING"), ("A_COL_005", "STRING"), ("W", "INT64")])
    a_ref = [{"D_COL_024": "Z", "A_COL_005": f"S{i:06d}", "W": i} for i in range(60)]

    def cfg(schema, ref, name, **kw):
        return PipelineConfig(table_schema=schema, engine_name="b1_rag",
                              model_client=FakeModelClient(reference_pool=ref),
                              run_id=f"fan-{name}", landing_table=f"p.land.{name}",
                              log_table_prefix=name, batch_size=20, **kw)

    b_cfg = cfg(b_schema, b_ref, "B_TABLE", num_rows=80, pk_columns=("D_COL_001",),
                identity_columns=("D_COL_001",))
    c_cfg = cfg(c_schema, c_ref, "C_TABLE", num_rows=160, pk_columns=("D_COL_001", "C_COL_002"),
                uniqueness_mode="streaming",
                fanout={"driving_cols": ["D_COL_001", "D_COL_024"], "histogram": {"0": 1, "2": 2, "3": 1},
                        "cells": {"cols": ["C_COL_002"], "rows": [["p"], ["q"], ["r"]], "counts": [3, 2, 1]},
                        "exact_cells": True})
    # A is keyed by (D_COL_024, A_COL_005); A_COL_005 is unbounded -> inexact cells (none).
    a_cfg = cfg(a_schema, a_ref, "A_TABLE", num_rows=100, pk_columns=("D_COL_024", "A_COL_005"),
                identity_columns=("A_COL_005",), uniqueness_mode="exact",
                fanout={"driving_cols": ["D_COL_024"], "histogram": {"1": 1, "2": 1},
                        "cells": None, "exact_cells": False})
    sinks = lambda name: dict(landing_sink=WriteToJsonLines(str(tmp_path / name)),  # noqa: E731
                              dlq_sink=WriteToJsonLines(str(tmp_path / f"dlq_{name}")))
    specs = [
        TableSpec(config=b_cfg, reference_rows=b_ref, **sinks("B_TABLE")),
        TableSpec(config=c_cfg, reference_rows=c_ref, **sinks("C_TABLE"),
                  parent_edges=(FkEdgeSpec(child_cols=("D_COL_001", "D_COL_024"),
                                           ref_cols=("D_COL_001", "D_COL_024"),
                                           parent_landing="p.land.B_TABLE", parent_pk=("D_COL_001",),
                                           mode="fanout", keys_per_batch=10),)),
        TableSpec(config=a_cfg, reference_rows=a_ref, **sinks("A_TABLE"),
                  parent_edges=(FkEdgeSpec(child_cols=("D_COL_024",), ref_cols=("D_COL_024",),
                                           parent_landing="p.land.C_TABLE",
                                           parent_pk=("D_COL_001", "C_COL_002"),
                                           mode="fanout", keys_per_batch=10),
                                FkEdgeSpec(child_cols=("D_COL_024",), ref_cols=("D_COL_024",),
                                           parent_landing="p.land.B_TABLE", parent_pk=("D_COL_001",),
                                           mode="implied"))),
    ]
    with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
        build_relational_pipeline(p, specs)

    b = _read(tmp_path / "B_TABLE"); c = _read(tmp_path / "C_TABLE"); a = _read(tmp_path / "A_TABLE")
    assert b and c and a
    b_keys = {(r["D_COL_001"], r["D_COL_024"]) for r in b}
    assert {(r["D_COL_001"], r["D_COL_024"]) for r in c} <= b_keys            # C -> B by construction
    assert len({(r["D_COL_001"], r["C_COL_002"]) for r in c}) == len(c)        # C PK unique
    per_b = {}
    for r in c:
        per_b[r["D_COL_001"]] = per_b.get(r["D_COL_001"], 0) + 1
    assert set(per_b.values()) <= {2, 3}                                        # the histogram
    c_tuples = {r["D_COL_024"] for r in c}
    assert {r["D_COL_024"] for r in a} <= c_tuples                              # A -> C (driving)
    assert {r["D_COL_024"] for r in a} <= {r["D_COL_024"] for r in b}          # A -> B (implied) holds
    assert not _read(tmp_path / "dlq_C_TABLE")                                 # nothing diverted
```

- [ ] **Step 2: Run it**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/test_fanout_three_tables.py -q`
Expected: pass. If the A_TABLE identity synthesis overwrites `A_COL_005` in a way that breaks the assertion set, that is fine — the test does not assert on `A_COL_005`. If `dlq_C_TABLE` is non-empty, read the envelopes' `rule_id` — a `schema.types` failure means a driving column's placeholder profile leaked into `enforce_value`; fix in Task 4/5, not by relaxing the test.

- [ ] **Step 3: Commit**

```bash
git add packages/sdfb-tests/tests/unit/test_fanout_three_tables.py
git commit -m "test(fanout): three-table DirectRunner acceptance — FK, PK and ratio by construction (ADR 0036)"
```

---

### Task 12: ADR 0036 and operator documentation

**Files:**
- Create: `docs/adr/0036-parent-driven-fanout-generation.md`
- Modify: `docs/adr/README.md` (index line), `docs/RUN_PLAYBOOK.md` (milestone rows + §6 recipe), `docs/DEPLOYMENT_PREREQUISITES.md` (the `fk_fanout_stats` table row + `bq mk` line), `config/relationships/README.md` (`drives`, widened edges, the implied rule), `.github/prompts/end_to_end_validation_report_generation.prompt.md` (read `fk_fanout_measured`, `fk_edge_role`, `rows_detail`; PK duplicates on a driven child are a regression), `docs/designs/2026-09-10-parent-driven-fanout-generation.md` (status → `ACCEPTED (laptop) — ADR 0036`).

- [ ] **Step 1: Write ADR 0036** following the ADR 0035 layout (Status / Design / Evidence / Amends / Keeps; Context; Decision D1–D6; Alternatives; Consequences; Acceptance). Decisions, one paragraph each:
  - D1 driven children generate from parent keys (`generate_for_keys`, key batches from the parent's `valid` PCollection, no side input).
  - D2 fan-out histogram and PK cells measured on the SOURCE, cached in `fk_fanout_stats` by model sha.
  - D3 PK-completing cells drawn without replacement per key; `k > cells` is a preflight stop.
  - D4 widened edges carry inherited columns; one driving edge, others implied or a stop (`edge_roles`).
  - D5 `--num_rows` applies to roots; children derive; `rows_detail` on the launch card.
  - D6 driven children default to `streaming` uniqueness (measured, no barrier) unless identity columns are declared; the in-DAG FK gate is skipped for driven edges; the playbook orphan query is the independent check.
  - Alternatives: co-partitioned join (coverage, not a key), hybrid, sample-based fan-out.
  - Consequences: the ADR 0031 side-input path and ADR 0035 sizing remain for roots and external parents; new BQ table; the corp model edit (widen C_TABLE's edge, `drives: true` on A_TABLE's C_TABLE edge).
  - Acceptance: the unit and DirectRunner tests of Tasks 1–11 checked; the M4 three-table launch unchecked with the §7 reading of the design doc.

- [ ] **Step 2: Playbook and prerequisites**

RUN_PLAYBOOK relational milestone rows to add:

```
| `fk_edge_role edge= role=driving|implied|external` (launcher) | which edge a child is generated FROM, which are satisfied by construction (ADR 0036) |
| `fk_fanout_measured edge= parents= children= mean= p50= p95= max= zero_share= source=measured|cache` (launcher) | the SOURCE ratio the driven child reproduces; `cache` = read from fk_fanout_stats |
| `relational_single_job … rows_detail=` (launcher) | derived row count per table — roots take --num_rows, children derive |
| `fanout_bound driving_cols= cells= exact_cells= mean_fanout=` (worker) | the engine bound the child's recipe; `exact_cells=True` = PK unique by construction |
| `batch_done keys= rows=` (worker) | a key batch: parents in, children out |
```

DEPLOYMENT_PREREQUISITES: a row for `fk_fanout_stats` (`config/bq_schema/synthetic_data_quality/fk_fanout_stats.schema.json`, no partition) and the `bq mk --schema … project:synthetic_data_quality.fk_fanout_stats` line next to the existing ones; the `--fk_fanout_stats_table` flag in the run matrix.

- [ ] **Step 3: Verify docs render**

Run: `grep -n "0036" docs/adr/README.md docs/RUN_PLAYBOOK.md` — both hit. `uv run --no-sync ruff check .` clean (no code change here, but the gate runs before every commit).

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0036-parent-driven-fanout-generation.md docs/adr/README.md docs/RUN_PLAYBOOK.md docs/DEPLOYMENT_PREREQUISITES.md config/relationships/README.md .github/prompts/end_to_end_validation_report_generation.prompt.md docs/designs/2026-09-10-parent-driven-fanout-generation.md
git commit -m "docs(adr): ADR 0036 parent-driven fan-out generation; playbook, prerequisites, model README, report prompt"
```

---

### Task 13: Final verification and PR

- [ ] **Step 1: Full gates**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q
uv run --no-sync ruff check .
uv run --no-sync mypy packages/sdfb-core/src
```
Expected: all green, 0 mypy errors in core. Record the passing test count for the PR body.

- [ ] **Step 2: Push and open the PR** (base `ws11-pk-fk-capacity` until PR #17 merges, then retarget `master`):

```bash
git push -u origin ws12-fanout-generation
gh pr create --base ws11-pk-fk-capacity --title "feat(ws12): parent-driven fan-out generation — children from parent keys (ADR 0036)" --body-file <body>
```
The body: the design summary, the operator note (corp model edit: widen C_TABLE's edge, `drives: true` on A_TABLE's C_TABLE edge; create `fk_fanout_stats`; pass `--fk_fanout_stats_table`), the test plan with the M4 three-table launch unchecked, and the trailer `🤖 Generated with [Claude Code](https://claude.com/claude-code)` + the session link.

---

## Self-review

**Spec coverage.** §2 mechanism → Tasks 1, 4, 5, 7. §3 contract (widened edges, `drives`, implied rule, stops) → Task 2 (+ README in Task 12). §4 statistics (source histogram with zero bucket, source cells with sample fallback, cache by sha, `fk_fanout_measured`, derived rows, per-key P4) → Tasks 8, 9, 10; the sample fallback for cells is `pk_cell_columns` + the reference-row profiles in Task 10 when `measure_fanout` returns no cells (cells come from the source query; the sample decides WHICH columns are cells). §5 DAG (project, Distinct when needed, Reshuffle, batching, second request shape, chunked emission, streaming uniqueness, FK gate skipped) → Tasks 6, 7, 10; chunked emission is `expand_keys(chunk_rows=cfg.batch_size)`. §6 engine API (ABC, shared helper, O(k) draw, per-key seed) → Tasks 1, 3, 4, 5. §7 milestones → Tasks 4/5 (`fanout_bound`), 6 (`batch_done keys=`), 8 (`fk_fanout_measured`), 9 (`fk_edge_role`), 10 (`rows_detail`); `relational_fk_edge mode=fanout` from the spec is NOT emitted — the `fk_edge_role` launcher line and the worker's `fanout_bound` carry the same fact; noted as a deliberate simplification in ADR 0036. §8 acceptance → Tasks 1, 2, 9, 10, 11. §9 rollout → Task 12–13 plus the M4 launch (out of laptop scope). §11 scale → streaming uniqueness (Task 10), O(k) draws and chunking (Task 1), narrow key shuffle (Task 7), cache (Task 8).

**Placeholders.** None: every step has its code. Two spots leave a judgment to the implementer with the decision rule stated (Task 5 `enforce_value` passthrough; Task 6 `chunk_rows` constructor parameter, the recommended route).

**Type consistency.** `FanoutPlan.to_payload()` shape (Task 1) = `GenerationContext.fanout` (Task 3) = `PipelineConfig.fanout` (Task 7) = `fanout_payload()` output (Task 8) = the `fanout=` argument of `preflight` (Task 9) and `resolve_fanout` (Task 10). `FkEdgeSpec.mode` values `"side_input" | "fanout" | "implied"` (Task 7) are produced by `in_set_parent_edges` (Task 10) from `edge_roles` values `"driving" | "implied" | "external"` (Task 2). `generate_for_keys(keys: Sequence[tuple], cfg)` is identical in Tasks 3, 4, 5 and called so in Task 6. `derive_key_seed(run_id, key)` (Task 1) is used only inside `expand_keys`.
