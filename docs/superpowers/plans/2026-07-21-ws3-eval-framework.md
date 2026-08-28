# WS3 — Evaluation Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the post-WriteLanding evaluation branch — a `sdfb_core.evaluation` package (stratified reservoir sampling + Tier 1 scipy/sklearn metrics + Tier 2 SDMetrics + Tier 3 opt-in stubs), a single-worker `EvaluationDoFn` writing one `synthetic_data_quality.validation_data_history` row per run, an always-BLOCKER memorization gate, and report-prompt/probe integration — so every fidelity/privacy claim becomes a number trending across runs.

**Architecture:** Pure metric/sampling/gate code lives in `packages/sdfb-core/src/sdfb_core/evaluation/` (no Beam, no GCP). Beam wiring (a `CombineFn` reservoir + the one-invocation `EvaluationDoFn`) lives in `packages/sdfb-beam/src/sdfb_beam/dofns/evaluation.py`, attached in `pipeline.py` as a sibling of the existing `validation_runs` branch (same `Create([None])` + `AsSingleton` collapse-to-one shape). The CLI grows two flags mirroring `--validation_runs_table`'s sink pattern. The eval row is ALWAYS written; job-failing happens in a downstream `_MemorizationGateDoFn` (mirror of `_BlockerGateDoFn`).

**Tech Stack:** scipy ≥1.11, scikit-learn ≥1.4, pandas ≥2.2, sdmetrics ≥0.28 (all new **base** deps of `sdfb-core` — audit resolved 2026-07-21: sdmetrics 0.28.x puts `torch` behind an optional `[torch]` extra, base install pulls only numpy/pandas/sklearn/scipy/copulas/tqdm/plotly, so the design §6 caveat is closed in favor of base); syntheval + evidently behind a new `sdfb-beam` `[eval-extra]` extra; apache-beam CombineGlobally/ParDo; google-cloud-bigquery (injectable client) for the previous-row lookup.

**Spec:** `docs/superpowers/specs/2026-07-20-e2e-remediation-rag-eval-evolution-design.md` §5 (5a/5b/5c/5d), which keeps most of `docs/designs/2026-07-07-evaluation-framework-design.md` (rewritten in place by Task 14).

## Global Constraints

- **Branch**: `ws3-eval-framework`, created FROM `ws2-rag-phase-a` (stacked on PR #4). The WS4 plan (unexecuted) also stacks there and touches `run_pipeline.py` argparse — WS3 does not depend on WS4; whichever executes second rebases. Use superpowers:using-git-worktrees at execution start.
- **Import direction**: `sdfb_core.evaluation` must not import `apache_beam`, `google.cloud.*`, `vllm`, or `torch`. scipy/sklearn/pandas/sdmetrics are permitted base deps (spec kept Tier 1+2 as base; audit above).
- **Spec §5a**: `memorization.copy_ratio` severity is plain `BLOCKER` in every env — NO per-env severity dict, NO `resolve_severity` helper. Threshold `0.0`.
- **Spec §5b**: gate trips on `identical_match_rate > 0` OR `max(column_copy_ratio) >= 0.3`; per-column copy_ratio computed for columns with reference-sample distinct count > 100 (the probe's rule), stored in `raw_metrics_json`.
- **Spec §5c**: `column.cardinality_floor` is a MAJOR **recorded** metric (`landing_distinct / min(num_rows, source_distinct) ≥ 0.5` per FREE_TEXT column) — never job-failing.
- **Eval row is never dropped**: empty samples produce a `skipped_insufficient_sample` row with NULL metrics. `NULL`/not-evaluated is never a pass and never a trip.
- **Gate raising** happens in `_MemorizationGateDoFn` (downstream of the sink write), wired only `if config.fail_on_blocker` — fake-client smoke runs stay informational, exactly like `_BlockerGateDoFn` (`pipeline.py:273-274`).
- **Flag naming**: `--enable_evaluation` / `--validation_data_history_table` (underscores — every existing `run_pipeline.py` flag uses underscores; the design doc's `--enable-evaluation` spelling is corrected in Task 14's rewrite).
- **Silent no-op guard**: `--enable_evaluation` without `--validation_data_history_table` is `p.error(...)` (the WS2 final-review `--build_rag_layer` precedent, superseding the design's "empty skips the write").
- **Sinks**: `validation_data_history` is `FILE_LOADS + WRITE_APPEND + CREATE_NEVER`, matching `validation_runs` (`run_pipeline.py:365-372`). `raw_metrics_json` is written as a `json.dumps(...)` string into the JSON column.
- Test command: `uv run --no-sync python3 -m pytest <path> -q` (**always `--no-sync`**). Baseline `uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q` must stay green (515 at branch point). Lint `uv run --no-sync ruff check .` clean after every task.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Docs sweep (RUN_PLAYBOOK, DEPLOYMENT_PREREQUISITES, ROADMAP, skills, CLAUDE.md) is **WS5** — the only docs touched here are the design-doc rewrite (Task 14) and the report prompt (Task 13, spec §5d assigns it to WS3).

---

### Task 1: `sdfb_core/evaluation/profile.py` — stratification-column selection

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/evaluation/__init__.py`
- Create: `packages/sdfb-core/src/sdfb_core/evaluation/profile.py`
- Test: `packages/sdfb-tests/tests/unit/evaluation/__init__.py` (empty), `packages/sdfb-tests/tests/unit/evaluation/test_profile.py`

**Interfaces:**
- Consumes: `TableSchema` (`sdfb_core.contracts`) — `.columns: list[FieldSchema]`, each `FieldSchema` has `.name`, `.bq_type`, `.is_struct`, `.is_repeated`.
- Produces: `StratificationPlan(column: str | None, values: tuple[object, ...])` (frozen dataclass), `choose_stratification_column(table_schema, reference_rows, *, min_categories=2, max_categories=50) -> StratificationPlan`, `stratum_key(plan, row) -> str`. Used by Tasks 2 and 9.

- [ ] **Step 1: Write the failing tests**

`packages/sdfb-tests/tests/unit/evaluation/test_profile.py`:

```python
"""choose_stratification_column — first non-numeric column with 2..50 distinct wins."""

from sdfb_core.contracts import TableSchema
from sdfb_core.evaluation.profile import (
    StratificationPlan,
    choose_stratification_column,
    stratum_key,
)


def _schema(cols: list[tuple[str, str]]) -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t"},
            "columns": [{"name": n, "type": t} for n, t in cols],
        }
    )


def test_picks_first_qualifying_column_in_schema_order():
    schema = _schema([("amount", "FLOAT64"), ("tier", "STRING"), ("region", "STRING")])
    rows = [{"amount": i, "tier": f"t{i % 3}", "region": f"r{i % 4}"} for i in range(30)]
    plan = choose_stratification_column(schema, rows)
    assert plan.column == "tier"
    assert plan.values == ("t0", "t1", "t2")


def test_numeric_and_over_cardinality_columns_skipped():
    schema = _schema([("id", "INT64"), ("email", "STRING")])
    rows = [{"id": i, "email": f"u{i}@x.com"} for i in range(100)]  # 100 distinct > 50
    plan = choose_stratification_column(schema, rows)
    assert plan == StratificationPlan(column=None, values=())


def test_single_value_column_skipped():
    schema = _schema([("env", "STRING")])
    rows = [{"env": "prd"}] * 10  # 1 distinct < min_categories
    assert choose_stratification_column(schema, rows).column is None


def test_nulls_do_not_count_as_a_category():
    schema = _schema([("tier", "STRING")])
    rows = [{"tier": "a"}, {"tier": "b"}, {"tier": None}]
    plan = choose_stratification_column(schema, rows)
    assert plan.values == ("a", "b")


def test_stratum_key_fallback_and_column_mode():
    plan_none = StratificationPlan(column=None, values=())
    assert stratum_key(plan_none, {"x": 1}) == "__all__"
    plan = StratificationPlan(column="tier", values=("a",))
    assert stratum_key(plan, {"tier": "a"}) == "a"
    assert stratum_key(plan, {}) == "None"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_profile.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdfb_core.evaluation'`

- [ ] **Step 3: Implement**

`packages/sdfb-core/src/sdfb_core/evaluation/__init__.py`:

```python
"""Post-write fidelity/privacy evaluation (WS3).

Pure-Python: sampling plans, reservoir sampling, Tier 1-3 metrics, and the
memorization gate. Beam wiring lives in ``sdfb_beam.dofns.evaluation``.

REF: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from sdfb_core.evaluation.profile import (
    StratificationPlan,
    choose_stratification_column,
    stratum_key,
)

__all__ = [
    "StratificationPlan",
    "choose_stratification_column",
    "stratum_key",
]
```

`packages/sdfb-core/src/sdfb_core/evaluation/profile.py`:

```python
"""Stratification-column selection for the evaluation sample (WS3 §2).

Deliberately independent of the engine-local ColumnKind classifiers
(b1_rag/profile.py, b2_library/fidelity.py) — evaluation must not depend
on whichever engine happened to run.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdfb_core.contracts import TableSchema

_NUMERIC_BQ_TYPES = {"INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC"}


@dataclass(frozen=True)
class StratificationPlan:
    """None column ⇒ unstratified single "__all__" bucket."""

    column: str | None
    values: tuple[object, ...]


def choose_stratification_column(
    table_schema: TableSchema,
    reference_rows: list[dict],
    *,
    min_categories: int = 2,
    max_categories: int = 50,
) -> StratificationPlan:
    """First column (declared schema order) whose reference-sample distinct
    count falls in [min_categories, max_categories] and whose BQ type is not
    numeric. No qualifying column ⇒ the unstratified fallback plan — this
    always terminates with a concrete plan."""
    for field in table_schema.columns:
        if field.bq_type in _NUMERIC_BQ_TYPES or field.is_struct or field.is_repeated:
            continue
        distinct = {
            row.get(field.name)
            for row in reference_rows
            if row.get(field.name) is not None
        }
        if min_categories <= len(distinct) <= max_categories:
            return StratificationPlan(
                column=field.name, values=tuple(sorted(distinct, key=str))
            )
    return StratificationPlan(column=None, values=())


def stratum_key(plan: StratificationPlan, row: dict) -> str:
    return str(row.get(plan.column)) if plan.column else "__all__"
```

Also create empty `packages/sdfb-tests/tests/unit/evaluation/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_profile.py -q`
Expected: 5 passed

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/evaluation packages/sdfb-tests/tests/unit/evaluation
git commit -m "feat(eval): stratification plan — sdfb_core.evaluation.profile (WS3 §2)"
```

---

### Task 2: `sdfb_core/evaluation/sampling.py` — deterministic stratified reservoir

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/evaluation/sampling.py`
- Modify: `packages/sdfb-core/src/sdfb_core/evaluation/__init__.py` (extend exports)
- Test: `packages/sdfb-tests/tests/unit/evaluation/test_sampling.py`

**Interfaces:**
- Consumes: `row_digest(record: dict) -> str` (`sdfb_core.validation.uniqueness`), `StratificationPlan`/`stratum_key` (Task 1).
- Produces (used by Task 9's `StratifiedReservoirFn`): `_OVERALL_CAP = 50_000`, `per_stratum_cap(num_strata, overall_cap=_OVERALL_CAP) -> int`, `sort_key(run_id, stratum, row) -> int`, `ReservoirAccumulator(by_stratum: dict[str, list[tuple[int, str, dict]]])`, `add_row(acc, row, *, plan, run_id, cap) -> ReservoirAccumulator`, `merge_accumulators(accs, *, cap) -> ReservoirAccumulator`, `extract_sample(acc, *, cap, overall_cap=_OVERALL_CAP) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

`packages/sdfb-tests/tests/unit/evaluation/test_sampling.py`:

```python
"""Reservoir: deterministic under same run_id, bounded, merge-order-invariant."""

from sdfb_core.evaluation.profile import StratificationPlan
from sdfb_core.evaluation.sampling import (
    ReservoirAccumulator,
    add_row,
    extract_sample,
    merge_accumulators,
    per_stratum_cap,
    sort_key,
)

_PLAN = StratificationPlan(column="tier", values=("a", "b"))
_UNSTRAT = StratificationPlan(column=None, values=())


def _rows(n: int) -> list[dict]:
    return [{"tier": "a" if i % 2 else "b", "v": i} for i in range(n)]


def _sample(rows, run_id="r1", cap=10, plan=_PLAN):
    acc = ReservoirAccumulator()
    for row in rows:
        acc = add_row(acc, row, plan=plan, run_id=run_id, cap=cap)
    return extract_sample(acc, cap=cap, overall_cap=15)


def test_per_stratum_cap_floor_and_division():
    assert per_stratum_cap(1) == 50_000
    assert per_stratum_cap(10) == 5_000
    assert per_stratum_cap(100) == 1_000  # _MIN_STRATUM_CAP floor
    assert per_stratum_cap(0) == 50_000


def test_sort_key_depends_on_run_id_and_content():
    row = {"tier": "a", "v": 1}
    assert sort_key("r1", "a", row) == sort_key("r1", "a", row)
    assert sort_key("r1", "a", row) != sort_key("r2", "a", row)
    assert sort_key("r1", "a", row) != sort_key("r1", "a", {"tier": "a", "v": 2})


def test_determinism_same_run_id_same_rows():
    assert _sample(_rows(100)) == _sample(_rows(100))


def test_different_run_id_different_selection():
    assert _sample(_rows(100), run_id="r1") != _sample(_rows(100), run_id="r2")


def test_caps_enforced_per_stratum_and_overall():
    sample = _sample(_rows(200), cap=10)  # 2 strata × cap 10 = 20 > overall 15
    assert len(sample) == 15
    per = {"a": 0, "b": 0}
    for row in sample:
        per[row["tier"]] += 1
    assert per["a"] <= 10 and per["b"] <= 10


def test_merge_is_order_invariant_and_bounded():
    rows = _rows(120)
    acc1, acc2 = ReservoirAccumulator(), ReservoirAccumulator()
    for row in rows[:60]:
        acc1 = add_row(acc1, row, plan=_UNSTRAT, run_id="r1", cap=8)
    for row in rows[60:]:
        acc2 = add_row(acc2, row, plan=_UNSTRAT, run_id="r1", cap=8)
    ab = extract_sample(merge_accumulators([acc1, acc2], cap=8), cap=8)
    ba = extract_sample(merge_accumulators([acc2, acc1], cap=8), cap=8)
    assert ab == ba
    assert len(ab) == 8
    # And identical to a single-accumulator pass over all rows:
    assert ab == _sample(rows, cap=8, plan=_UNSTRAT)[:8]


def test_empty_accumulator_extracts_empty():
    assert extract_sample(ReservoirAccumulator(), cap=5) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_sampling.py -q`
Expected: FAIL with `ImportError` (module missing)

- [ ] **Step 3: Implement**

`packages/sdfb-core/src/sdfb_core/evaluation/sampling.py`:

```python
"""Deterministic stratified 'bottom-k reservoir' (WS3 §2).

Same run_id + same row content ⇒ same sample, every time. Buckets are
trimmed at 2×cap so accumulator memory stays O(num_strata × cap) — the
same commutative/associative-accumulator contract as MergeProfilesFn.
Selection order is (sort_key, row_digest): the digest tie-break keeps
entries totally ordered without ever comparing row dicts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from sdfb_core.validation.uniqueness import row_digest

from sdfb_core.evaluation.profile import StratificationPlan, stratum_key

_OVERALL_CAP = 50_000
_MIN_STRATUM_CAP = 1_000

# One reservoir entry: (sort_key, row_digest, row).
_Entry = tuple[int, str, dict]


def per_stratum_cap(num_strata: int, overall_cap: int = _OVERALL_CAP) -> int:
    return max(_MIN_STRATUM_CAP, overall_cap // max(num_strata, 1))


def sort_key(run_id: str, stratum: str, row: dict) -> int:
    """Deterministic bottom-k priority — smallest keys win."""
    digest = row_digest(row)
    h = hashlib.blake2b(f"{run_id}:{stratum}:{digest}".encode(), digest_size=8)
    return int.from_bytes(h.digest(), "big")


@dataclass
class ReservoirAccumulator:
    by_stratum: dict[str, list[_Entry]] = field(default_factory=dict)


def _trim(bucket: list[_Entry], cap: int) -> None:
    bucket.sort(key=lambda e: (e[0], e[1]))
    del bucket[cap:]


def add_row(
    acc: ReservoirAccumulator,
    row: dict,
    *,
    plan: StratificationPlan,
    run_id: str,
    cap: int,
) -> ReservoirAccumulator:
    stratum = stratum_key(plan, row)
    bucket = acc.by_stratum.setdefault(stratum, [])
    bucket.append((sort_key(run_id, stratum, row), row_digest(row), row))
    if len(bucket) >= 2 * cap:
        _trim(bucket, cap)
    return acc


def merge_accumulators(
    accs: Iterable[ReservoirAccumulator], *, cap: int
) -> ReservoirAccumulator:
    merged = ReservoirAccumulator()
    for acc in accs:
        for stratum, bucket in acc.by_stratum.items():
            target = merged.by_stratum.setdefault(stratum, [])
            target.extend(bucket)
            if len(target) >= 2 * cap:
                _trim(target, cap)
    return merged


def extract_sample(
    acc: ReservoirAccumulator, *, cap: int, overall_cap: int = _OVERALL_CAP
) -> list[dict]:
    """Trim every stratum to cap, then re-sort the union and trim globally —
    this is what guarantees the overall cap holds even when
    num_strata × cap overshoots it."""
    entries: list[_Entry] = []
    for bucket in acc.by_stratum.values():
        _trim(bucket, cap)
        entries.extend(bucket)
    entries.sort(key=lambda e: (e[0], e[1]))
    return [row for _, _, row in entries[:overall_cap]]
```

Extend `__init__.py` exports with `ReservoirAccumulator`, `add_row`, `extract_sample`, `merge_accumulators`, `per_stratum_cap`, `sort_key` (import from `sdfb_core.evaluation.sampling`, append to `__all__`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/ -q`
Expected: all passed (Task 1's 5 + these 7)

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add -A packages/sdfb-core/src/sdfb_core/evaluation packages/sdfb-tests/tests/unit/evaluation
git commit -m "feat(eval): deterministic stratified bottom-k reservoir (WS3 §2)"
```

---

### Task 3: deps bump + `metrics_t1.py` part 1 — marginals & drift

**Files:**
- Modify: `packages/sdfb-core/pyproject.toml` (add base deps)
- Modify: `packages/sdfb-beam/pyproject.toml` (add `[eval-extra]` optional group)
- Create: `packages/sdfb-core/src/sdfb_core/evaluation/metrics_t1.py`
- Test: `packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1.py`

**Interfaces:**
- Produces (used by Tasks 4, 5, 9): `numeric_and_categorical_columns(df) -> tuple[list[str], list[str]]`, `ks_statistic(real: pd.Series, synth: pd.Series) -> float | None`, `wasserstein(real, synth) -> float | None`, `tvd(real, synth) -> float | None`, `binned_frequencies(series, *, bins=10, edges=None) -> dict` (returns `{"kind": "numeric", "edges": [...], "freqs": [...]}` or `{"kind": "categorical", "freqs": {...}}`), `psi(curr: dict, prev: dict) -> float | None`, `jsd(curr: dict, prev: dict) -> float | None`.

- [ ] **Step 1: Add the dependencies**

In `packages/sdfb-core/pyproject.toml`, extend `[project] dependencies` with (keep existing entries):

```toml
    # WS3 evaluation — Tier 1 (scipy/sklearn) + Tier 2 (sdmetrics) are base
    # per the locked decision. Audit 2026-07-21: sdmetrics 0.28.x declares
    # torch ONLY under its optional [torch] extra, so the "no torch in
    # sdfb-core" rule holds with a base install.
    "scipy>=1.11.0",
    "scikit-learn>=1.4.0",
    "pandas>=2.2.0",
    "sdmetrics>=0.28.0",
```

In `packages/sdfb-beam/pyproject.toml`, add to `[project.optional-dependencies]` (after the existing `library` group):

```toml
# Tier-3 evaluation extras — SynthEval + Evidently. Off by default; the
# functions importing these are deferred-import (metrics_t3.py) so their
# absence never breaks a Tier 1/2 evaluation run.
eval-extra = [
    "syntheval>=1.5.0",
    "evidently>=0.4.0",
]
```

Run: `uv sync --group dev` (one-time real sync to pull the new deps), then verify `uv run --no-sync python3 -c "import scipy, sklearn, sdmetrics; import torch" 2>&1 | tail -1` reports `ModuleNotFoundError: No module named 'torch'` (proves the audit).

- [ ] **Step 2: Write the failing tests**

`packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1.py`:

```python
"""Tier-1 marginals & drift — small hand-built fixtures exercise every formula."""

import math

import pandas as pd
from sdfb_core.evaluation.metrics_t1 import (
    binned_frequencies,
    jsd,
    ks_statistic,
    numeric_and_categorical_columns,
    psi,
    tvd,
    wasserstein,
)


def test_column_split_by_dtype():
    df = pd.DataFrame({"a": [1, 2], "b": [0.5, 1.5], "c": ["x", "y"]})
    numeric, categorical = numeric_and_categorical_columns(df)
    assert numeric == ["a", "b"] and categorical == ["c"]


def test_ks_zero_for_identical_and_positive_for_shifted():
    s = pd.Series(range(100), dtype=float)
    assert ks_statistic(s, s) == 0.0
    assert ks_statistic(s, s + 50) > 0.4


def test_wasserstein_matches_shift():
    s = pd.Series(range(100), dtype=float)
    assert math.isclose(wasserstein(s, s + 10), 10.0, rel_tol=1e-9)


def test_empty_series_yield_none():
    empty = pd.Series([], dtype=float)
    s = pd.Series([1.0])
    assert ks_statistic(empty, s) is None
    assert wasserstein(s, empty) is None
    assert tvd(empty, s) is None


def test_tvd_bounds():
    r = pd.Series(["a", "a", "b", "b"])
    assert tvd(r, r) == 0.0
    assert tvd(r, pd.Series(["c", "c"])) == 1.0


def test_binned_frequencies_numeric_and_categorical():
    num = binned_frequencies(pd.Series(range(100), dtype=float), bins=5)
    assert num["kind"] == "numeric"
    assert len(num["edges"]) == 6 and len(num["freqs"]) == 5
    assert math.isclose(sum(num["freqs"]), 1.0, rel_tol=1e-9)
    cat = binned_frequencies(pd.Series(["x", "x", "y"]))
    assert cat["kind"] == "categorical"
    assert math.isclose(cat["freqs"]["x"], 2 / 3, rel_tol=1e-9)


def test_binned_frequencies_reuses_previous_edges():
    prev = binned_frequencies(pd.Series(range(100), dtype=float), bins=5)
    curr = binned_frequencies(pd.Series(range(50), dtype=float), edges=prev["edges"])
    assert curr["edges"] == prev["edges"]


def test_psi_jsd_zero_on_identical_positive_on_drift():
    a = binned_frequencies(pd.Series(range(100), dtype=float), bins=5)
    b = binned_frequencies(
        pd.Series([90.0] * 100), edges=a["edges"]
    )  # mass collapsed into one bin
    assert abs(psi(a, a)) < 1e-9
    assert psi(b, a) > 0.2
    assert abs(jsd(a, a)) < 1e-9
    assert jsd(b, a) > 0.1


def test_psi_none_on_misaligned_kinds_or_edges():
    num = binned_frequencies(pd.Series(range(100), dtype=float), bins=5)
    cat = binned_frequencies(pd.Series(["x", "y"]))
    assert psi(num, cat) is None
    other = binned_frequencies(pd.Series(range(200), dtype=float), bins=5)
    assert psi(num, other) is None  # different edges — not comparable
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Implement**

`packages/sdfb-core/src/sdfb_core/evaluation/metrics_t1.py`:

```python
"""Tier-1 evaluation metrics — scipy/scikit-learn/pandas only (WS3 §3).

Pure functions over sampled rows / Series / DataFrames. This module never
imports Beam or GCP; the Beam EvaluationDoFn is its only pipeline caller.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial.distance import jensenshannon

_EPS = 1e-6
_DEFAULT_BINS = 10


def numeric_and_categorical_columns(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    return numeric, [c for c in df.columns if c not in numeric]


def ks_statistic(real: pd.Series, synth: pd.Series) -> float | None:
    r, s = real.dropna(), synth.dropna()
    if r.empty or s.empty:
        return None
    return float(stats.ks_2samp(r, s).statistic)


def wasserstein(real: pd.Series, synth: pd.Series) -> float | None:
    r, s = real.dropna(), synth.dropna()
    if r.empty or s.empty:
        return None
    return float(stats.wasserstein_distance(r, s))


def tvd(real: pd.Series, synth: pd.Series) -> float | None:
    """Total variation distance over the union of observed categories, [0,1]."""
    r = real.dropna().astype(str).value_counts(normalize=True)
    s = synth.dropna().astype(str).value_counts(normalize=True)
    if r.empty or s.empty:
        return None
    cats = r.index.union(s.index)
    return float(
        0.5
        * (r.reindex(cats, fill_value=0.0) - s.reindex(cats, fill_value=0.0))
        .abs()
        .sum()
    )


def binned_frequencies(
    series: pd.Series, *, bins: int = _DEFAULT_BINS, edges: list[float] | None = None
) -> dict:
    """Sufficient statistics for PSI/JSD, persisted in raw_metrics_json so the
    NEXT run can diff without re-reading raw rows. Passing the previous run's
    ``edges`` forces bin alignment across runs (numeric only)."""
    s = series.dropna()
    is_numeric = pd.api.types.is_numeric_dtype(s)
    if is_numeric and (edges is not None or s.nunique() > bins):
        if edges is None:
            edges = [float(e) for e in np.histogram_bin_edges(s, bins=bins)]
        counts, _ = np.histogram(s, bins=np.asarray(edges, dtype=float))
        total = max(int(counts.sum()), 1)
        return {
            "kind": "numeric",
            "edges": [float(e) for e in edges],
            "freqs": [float(c) / total for c in counts],
        }
    freqs = s.astype(str).value_counts(normalize=True)
    return {
        "kind": "categorical",
        "freqs": {str(k): float(v) for k, v in freqs.items()},
    }


def _aligned(curr: dict, prev: dict) -> tuple[np.ndarray, np.ndarray] | None:
    if curr.get("kind") != prev.get("kind"):
        return None
    if curr.get("kind") == "numeric":
        if curr.get("edges") != prev.get("edges"):
            return None
        p, q = np.asarray(curr["freqs"]), np.asarray(prev["freqs"])
    else:
        cats = sorted(set(curr.get("freqs", {})) | set(prev.get("freqs", {})))
        if not cats:
            return None
        p = np.asarray([curr["freqs"].get(c, 0.0) for c in cats])
        q = np.asarray([prev["freqs"].get(c, 0.0) for c in cats])
    return p + _EPS, q + _EPS


def psi(curr: dict, prev: dict) -> float | None:
    """Population Stability Index, this run vs the previous run's stats."""
    aligned = _aligned(curr, prev)
    if aligned is None:
        return None
    p, q = aligned
    p, q = p / p.sum(), q / q.sum()
    return float(np.sum((p - q) * np.log(p / q)))


def jsd(curr: dict, prev: dict) -> float | None:
    """Jensen-Shannon divergence (natural log; bounded [0, ln 2])."""
    aligned = _aligned(curr, prev)
    if aligned is None:
        return None
    p, q = aligned
    return float(jensenshannon(p, q, base=math.e) ** 2)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/ -q`
Expected: all passed

- [ ] **Step 6: Full baseline + lint + commit**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q   # 515+ green
uv run --no-sync ruff check .
git add packages/sdfb-core/pyproject.toml packages/sdfb-beam/pyproject.toml uv.lock \
        packages/sdfb-core/src/sdfb_core/evaluation/metrics_t1.py \
        packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1.py
git commit -m "feat(eval): tier-1 marginals + drift metrics; scipy/sklearn/sdmetrics base deps (WS3 §3)"
```

---

### Task 4: `metrics_t1.py` part 2 — structure metrics (correlation + MI)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/evaluation/metrics_t1.py` (append)
- Test: `packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1_structure.py`

**Interfaces:**
- Produces (used by Task 9): `corr_diff_frobenius(real_df, synth_df, *, method="pearson") -> float | None`, `mi_matrix_diff(real_df, synth_df, *, bins=10) -> float | None`.

- [ ] **Step 1: Write the failing tests**

`packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1_structure.py`:

```python
"""Correlation/MI structure metrics — catch destroyed inter-column relationships."""

import numpy as np
import pandas as pd
from sdfb_core.evaluation.metrics_t1 import corr_diff_frobenius, mi_matrix_diff


def _correlated(n=200, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    return pd.DataFrame({"x": x, "y": 2 * x + rng.normal(scale=0.1, size=n)})


def _independent(n=200, seed=8) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"x": rng.normal(size=n), "y": rng.normal(size=n)})


def test_corr_diff_zero_for_same_frame_large_when_structure_destroyed():
    real = _correlated()
    assert corr_diff_frobenius(real, real) == 0.0
    assert corr_diff_frobenius(real, _independent()) > 1.0


def test_corr_diff_spearman_method_and_insufficient_columns():
    real = _correlated()
    assert corr_diff_frobenius(real, _independent(), method="spearman") > 1.0
    single = pd.DataFrame({"x": [1.0, 2.0]})
    assert corr_diff_frobenius(single, single) is None


def test_mi_matrix_diff_zero_same_positive_when_dependence_lost():
    real = _correlated(400)
    assert mi_matrix_diff(real, real) == 0.0
    assert mi_matrix_diff(real, _independent(400)) > 0.1


def test_mi_handles_categorical_columns():
    real = pd.DataFrame({"k": ["a", "a", "b", "b"] * 25, "v": [1, 1, 2, 2] * 25})
    synth = pd.DataFrame({"k": ["a", "b"] * 50, "v": [1, 2] * 50})
    assert mi_matrix_diff(real, synth) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1_structure.py -q`
Expected: FAIL with `ImportError: cannot import name 'corr_diff_frobenius'`

- [ ] **Step 3: Implement — append to `metrics_t1.py`**

```python
def corr_diff_frobenius(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, *, method: str = "pearson"
) -> float | None:
    """‖corr_real − corr_synth‖_F over the shared numeric columns."""
    numeric, _ = numeric_and_categorical_columns(real_df)
    cols = [c for c in numeric if c in synth_df.columns]
    if len(cols) < 2:
        return None
    r = real_df[cols].corr(method=method).fillna(0.0)
    s = synth_df[cols].corr(method=method).fillna(0.0)
    return float(np.linalg.norm(r.to_numpy() - s.to_numpy(), ord="fro"))


def _discretize(df: pd.DataFrame, *, bins: int = _DEFAULT_BINS) -> pd.DataFrame:
    """Quantile-bin high-cardinality numerics so a single mutual_info_score
    call covers every column pair (numeric↔numeric, mixed, cat↔cat)."""
    out: dict[str, pd.Series] = {}
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) and s.nunique() > bins:
            out[c] = pd.qcut(s, q=bins, labels=False, duplicates="drop").astype(str)
        else:
            out[c] = s.astype(str)
    return pd.DataFrame(out)


def mi_matrix_diff(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, *, bins: int = _DEFAULT_BINS
) -> float | None:
    """Frobenius diff of pairwise mutual-information matrices — catches
    nonlinear dependency loss that Pearson/Spearman miss."""
    from sklearn.metrics import mutual_info_score

    cols = [c for c in real_df.columns if c in synth_df.columns]
    if len(cols) < 2:
        return None
    r = _discretize(real_df[cols], bins=bins)
    s = _discretize(synth_df[cols], bins=bins)
    n = len(cols)
    rm, sm = np.zeros((n, n)), np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            rm[i, j] = rm[j, i] = mutual_info_score(r[cols[i]], r[cols[j]])
            sm[i, j] = sm[j, i] = mutual_info_score(s[cols[i]], s[cols[j]])
    return float(np.linalg.norm(rm - sm, ord="fro"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/ -q`
Expected: all passed

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/evaluation/metrics_t1.py \
        packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1_structure.py
git commit -m "feat(eval): correlation + mutual-information structure metrics (WS3 §3)"
```

---

### Task 5: `metrics_t1.py` part 3 — privacy metrics (DCR/NNDR, identical-match, §5b copy ratios, §5c cardinality floor)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/evaluation/metrics_t1.py` (append)
- Test: `packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1_privacy.py`

**Interfaces:**
- Consumes: `row_digest` (`sdfb_core.validation.uniqueness`).
- Produces (used by Tasks 7's gate input shape and 9): `dcr_nndr(real_df, synth_df) -> tuple[float | None, float | None]`, `identical_match_rate(real_rows: list[dict], synth_rows: list[dict]) -> float | None`, `column_copy_ratios(real_rows, synth_rows, *, min_source_distinct=100) -> dict[str, float]`, `cardinality_floor(real_rows, synth_rows, *, free_text_columns, num_rows) -> dict[str, float]`.

- [ ] **Step 1: Write the failing tests**

`packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1_privacy.py`:

```python
"""Privacy tier — the metrics that feed the §5a/§5b gate and the §5c floor."""

import numpy as np
import pandas as pd
from sdfb_core.evaluation.metrics_t1 import (
    cardinality_floor,
    column_copy_ratios,
    dcr_nndr,
    identical_match_rate,
)


def test_dcr_zero_when_synth_copies_real_larger_when_novel():
    rng = np.random.default_rng(3)
    real = pd.DataFrame({"a": rng.normal(size=50), "k": ["x", "y"] * 25})
    copied, _ = dcr_nndr(real, real.copy())
    novel, _ = dcr_nndr(real, pd.DataFrame({"a": rng.normal(loc=10, size=50), "k": ["z"] * 50}))
    assert copied == 0.0
    assert novel > copied


def test_nndr_near_zero_flags_reidentification():
    # One real record isolated far from the rest; synth sits on top of it.
    real = pd.DataFrame({"a": [0.0, 0.1, 0.2, 100.0]})
    synth = pd.DataFrame({"a": [100.0]})
    _, nndr = dcr_nndr(real, synth)
    assert nndr is not None and nndr < 0.05


def test_dcr_nndr_none_on_degenerate_input():
    assert dcr_nndr(pd.DataFrame({"a": [1.0]}), pd.DataFrame({"a": [1.0]})) == (None, None)
    assert dcr_nndr(pd.DataFrame({"a": [1.0, 2.0]}), pd.DataFrame({"a": []})) == (None, None)


def test_identical_match_rate_counts_exact_digest_hits():
    real = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    synth = [{"a": 1, "b": "x"}, {"a": 3, "b": "z"}]
    assert identical_match_rate(real, synth) == 0.5
    assert identical_match_rate(real, []) is None
    assert identical_match_rate([], synth) is None


def test_column_copy_ratios_only_high_cardinality_columns():
    n = 150
    real = [{"id": f"id-{i}", "tier": f"t{i % 3}"} for i in range(n)]
    # Half the synth ids are verbatim copies; 'tier' (3 distinct ≤ 100) is exempt.
    synth = [{"id": f"id-{i}" if i % 2 else f"new-{i}", "tier": "t0"} for i in range(n)]
    ratios = column_copy_ratios(real, synth)
    assert "tier" not in ratios
    assert abs(ratios["id"] - 0.5) < 0.01


def test_column_copy_ratio_full_copy_is_one():
    n = 150
    real = [{"id": f"id-{i}"} for i in range(n)]
    ratios = column_copy_ratios(real, list(real))
    assert ratios["id"] == 1.0


def test_cardinality_floor_flags_bounded_pool_collapse():
    real = [{"note": f"text-{i}"} for i in range(1000)]
    synth = [{"note": f"text-{i % 32}"} for i in range(500)]  # 32-value pool collapse
    floor = cardinality_floor(real, synth, free_text_columns=["note"], num_rows=500)
    assert abs(floor["note"] - 32 / 500) < 1e-9
    healthy = cardinality_floor(real, real[:500], free_text_columns=["note"], num_rows=500)
    assert healthy["note"] == 1.0
    assert cardinality_floor(real, synth, free_text_columns=[], num_rows=500) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1_privacy.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement — append to `metrics_t1.py`** (also add `from sdfb_core.validation.uniqueness import row_digest` to the module imports)

```python
def _gower_embed(
    real_df: pd.DataFrame, synth_df: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray] | None:
    """ONE concatenated feature matrix per side: min-max-normalized numerics
    (bounds fit on the real side) + one-hot categoricals scaled by 1/√2 (a
    category mismatch then contributes distance 1, like a full numeric span).
    Gower-STYLE approximation (design §3); never metric='precomputed' — a
    dense n×n distance matrix would blow the single-worker memory bound."""
    numeric, categorical = numeric_and_categorical_columns(real_df)
    numeric = [c for c in numeric if c in synth_df.columns]
    categorical = [c for c in categorical if c in synth_df.columns]
    if not numeric and not categorical:
        return None
    blocks_r: list[np.ndarray] = []
    blocks_s: list[np.ndarray] = []
    for c in numeric:
        r = pd.to_numeric(real_df[c], errors="coerce")
        s = pd.to_numeric(synth_df[c], errors="coerce")
        lo = float(r.min()) if r.notna().any() else 0.0
        hi = float(r.max()) if r.notna().any() else 0.0
        span = (hi - lo) or 1.0
        blocks_r.append(((r.fillna(lo) - lo) / span).clip(0.0, 1.0).to_numpy()[:, None])
        blocks_s.append(((s.fillna(lo) - lo) / span).clip(0.0, 1.0).to_numpy()[:, None])
    for c in categorical:
        cats = sorted(set(real_df[c].astype(str)) | set(synth_df[c].astype(str)))
        index = {v: i for i, v in enumerate(cats)}
        onehot_r = np.zeros((len(real_df), len(cats)))
        onehot_s = np.zeros((len(synth_df), len(cats)))
        for row_idx, v in enumerate(real_df[c].astype(str)):
            onehot_r[row_idx, index[v]] = 1.0
        for row_idx, v in enumerate(synth_df[c].astype(str)):
            onehot_s[row_idx, index[v]] = 1.0
        blocks_r.append(onehot_r / math.sqrt(2.0))
        blocks_s.append(onehot_s / math.sqrt(2.0))
    return np.hstack(blocks_r), np.hstack(blocks_s)


def dcr_nndr(
    real_df: pd.DataFrame, synth_df: pd.DataFrame
) -> tuple[float | None, float | None]:
    """(mean Distance to Closest Record, mean Nearest-Neighbor Distance
    Ratio) of the synthetic sample vs the real sample. Tree-based
    NearestNeighbors — O(n log n), never a dense pairwise matrix."""
    from sklearn.neighbors import NearestNeighbors

    if len(real_df) < 2 or len(synth_df) == 0:
        return None, None
    embedded = _gower_embed(real_df, synth_df)
    if embedded is None:
        return None, None
    real_matrix, synth_matrix = embedded
    nn = NearestNeighbors(n_neighbors=2).fit(real_matrix)
    dist, _ = nn.kneighbors(synth_matrix)
    d1, d2 = dist[:, 0], dist[:, 1]
    avg_dcr = float(np.mean(d1))
    nndr = float(np.mean(d1 / np.maximum(d2, 1e-12)))
    return avg_dcr, nndr


def identical_match_rate(
    real_rows: list[dict], synth_rows: list[dict]
) -> float | None:
    """Fraction of sampled synthetic rows whose full-row content digest
    exactly matches a sampled real row's — feeds the §5a gate."""
    if not real_rows or not synth_rows:
        return None
    real_digests = {row_digest(r) for r in real_rows}
    hits = sum(1 for r in synth_rows if row_digest(r) in real_digests)
    return hits / len(synth_rows)


def column_copy_ratios(
    real_rows: list[dict],
    synth_rows: list[dict],
    *,
    min_source_distinct: int = 100,
) -> dict[str, float]:
    """Spec §5b — per-column verbatim-copy ratio vs the reference sample, for
    columns whose reference distinct count exceeds min_source_distinct (the
    offline probe's exact rule; low-cardinality enums are legitimately
    verbatim). Ratio = fraction of non-null synthetic values present in the
    reference sample's value set."""
    if not real_rows or not synth_rows:
        return {}
    out: dict[str, float] = {}
    for col in real_rows[0]:
        real_values = {r.get(col) for r in real_rows if r.get(col) is not None}
        if len(real_values) <= min_source_distinct:
            continue
        synth_values = [r.get(col) for r in synth_rows if r.get(col) is not None]
        if not synth_values:
            continue
        out[col] = sum(1 for v in synth_values if v in real_values) / len(synth_values)
    return out


def cardinality_floor(
    real_rows: list[dict],
    synth_rows: list[dict],
    *,
    free_text_columns: list[str],
    num_rows: int,
) -> dict[str, float]:
    """Spec §5c — landing_distinct / min(num_rows, source_distinct) per
    FREE_TEXT column. MAJOR recorded metric; never job-failing. Values well
    below 1.0 expose bounded-pool collapse (the 2026-07-19 b1 residual)."""
    out: dict[str, float] = {}
    for col in free_text_columns:
        source_distinct = len({r.get(col) for r in real_rows if r.get(col) is not None})
        denominator = min(num_rows, source_distinct) if num_rows > 0 else source_distinct
        if denominator <= 0:
            continue
        landing_distinct = len(
            {r.get(col) for r in synth_rows if r.get(col) is not None}
        )
        out[col] = landing_distinct / denominator
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/ -q`
Expected: all passed

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/evaluation/metrics_t1.py \
        packages/sdfb-tests/tests/unit/evaluation/test_metrics_t1_privacy.py
git commit -m "feat(eval): privacy metrics — DCR/NNDR, identical-match, column copy ratios, cardinality floor (WS3 §3, §5b, §5c)"
```

---

### Task 6: `metrics_t2.py` (SDMetrics) + `metrics_t3.py` (opt-in stubs)

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/evaluation/metrics_t2.py`
- Create: `packages/sdfb-core/src/sdfb_core/evaluation/metrics_t3.py`
- Test: `packages/sdfb-tests/tests/unit/evaluation/test_metrics_t2.py`, `packages/sdfb-tests/tests/unit/evaluation/test_metrics_t3.py`

**Interfaces:**
- Consumes: `TableSchema` (Task 1's usage pattern).
- Produces (used by Task 9): `sdmetrics_metadata(table_schema, columns: list[str]) -> dict`, `coerce_for_sdmetrics(df, metadata) -> pd.DataFrame`, `sdmetrics_quality(real_df, synth_df, metadata) -> dict` (keys `score: float`, `properties: dict[str, float]`), `sdmetrics_diagnostic(real_df, synth_df, metadata) -> dict`. Tier 3: `syntheval_privacy(real_df, synth_df) -> dict`, `evidently_drift_report(real_df, synth_df, out_path: str) -> str` — both deferred-import, raising `ImportError` with the install hint when the extra is absent.

- [ ] **Step 1: Write the failing tests**

`packages/sdfb-tests/tests/unit/evaluation/test_metrics_t2.py`:

```python
"""Tier 2 — SDMetrics wrappers on a tiny fixture (real call, no network)."""

import pandas as pd
from sdfb_core.contracts import TableSchema
from sdfb_core.evaluation.metrics_t2 import (
    coerce_for_sdmetrics,
    sdmetrics_diagnostic,
    sdmetrics_metadata,
    sdmetrics_quality,
)

_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "p.d.t"},
        "columns": [
            {"name": "amount", "type": "FLOAT64"},
            {"name": "tier", "type": "STRING"},
            {"name": "created", "type": "TIMESTAMP"},
        ],
    }
)


def _df(seed_shift: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "amount": [float(i) + seed_shift for i in range(40)],
            "tier": ["a", "b"] * 20,
            "created": ["2026-01-01T00:00:00Z"] * 40,
        }
    )


def test_metadata_maps_bq_types_to_sdtypes():
    md = sdmetrics_metadata(_SCHEMA, ["amount", "tier", "created"])
    assert md["columns"]["amount"] == {"sdtype": "numerical"}
    assert md["columns"]["tier"] == {"sdtype": "categorical"}
    assert md["columns"]["created"] == {"sdtype": "datetime"}


def test_coerce_parses_datetime_columns():
    md = sdmetrics_metadata(_SCHEMA, ["created"])
    out = coerce_for_sdmetrics(_df(0.0), md)
    assert pd.api.types.is_datetime64_any_dtype(out["created"])


def test_quality_score_bounded_and_higher_for_identical():
    md = sdmetrics_metadata(_SCHEMA, ["amount", "tier", "created"])
    real = coerce_for_sdmetrics(_df(0.0), md)
    same = sdmetrics_quality(real, real.copy(), md)
    shifted = sdmetrics_quality(real, coerce_for_sdmetrics(_df(100.0), md), md)
    assert 0.0 <= shifted["score"] <= same["score"] <= 1.0
    assert isinstance(same["properties"], dict) and same["properties"]


def test_diagnostic_returns_dict():
    md = sdmetrics_metadata(_SCHEMA, ["amount", "tier", "created"])
    real = coerce_for_sdmetrics(_df(0.0), md)
    out = sdmetrics_diagnostic(real, real.copy(), md)
    assert isinstance(out, dict) and "score" in out
```

`packages/sdfb-tests/tests/unit/evaluation/test_metrics_t3.py`:

```python
"""Tier 3 — deferred imports: clear error without the extra, skip-clean with it."""

import pandas as pd
import pytest
from sdfb_core.evaluation import metrics_t3


def _df() -> pd.DataFrame:
    return pd.DataFrame({"a": [1.0, 2.0, 3.0]})


def test_missing_extra_raises_actionable_importerror():
    if metrics_t3.tier3_available():
        pytest.skip("eval-extra installed — the ImportError path is untestable")
    with pytest.raises(ImportError, match="eval-extra"):
        metrics_t3.syntheval_privacy(_df(), _df())
    with pytest.raises(ImportError, match="eval-extra"):
        metrics_t3.evidently_drift_report(_df(), _df(), "/tmp/x.html")


def test_syntheval_runs_when_installed():
    pytest.importorskip("syntheval")
    out = metrics_t3.syntheval_privacy(_df(), _df())
    assert isinstance(out, dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_metrics_t2.py packages/sdfb-tests/tests/unit/evaluation/test_metrics_t3.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

`packages/sdfb-core/src/sdfb_core/evaluation/metrics_t2.py`:

```python
"""Tier-2 evaluation — SDMetrics QualityReport / DiagnosticReport (WS3 §3).

sdmetrics is a BASE dependency: the 2026-07-21 audit confirmed 0.28.x
declares torch only under its optional [torch] extra, so sdfb-core's
no-torch rule holds (design §6 caveat resolved).
"""

from __future__ import annotations

import pandas as pd

from sdfb_core.contracts import TableSchema

_NUMERIC = {"INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC"}
_TEMPORAL = {"DATE", "DATETIME", "TIMESTAMP", "TIME"}
_BOOLEAN = {"BOOL", "BOOLEAN"}


def sdmetrics_metadata(table_schema: TableSchema, columns: list[str]) -> dict:
    """Single-table SDMetrics metadata dict derived from the BQ schema."""
    wanted = set(columns)
    sdtypes: dict[str, dict] = {}
    for field in table_schema.columns:
        if field.name not in wanted:
            continue
        if field.bq_type in _NUMERIC:
            sdtypes[field.name] = {"sdtype": "numerical"}
        elif field.bq_type in _TEMPORAL:
            sdtypes[field.name] = {"sdtype": "datetime"}
        elif field.bq_type in _BOOLEAN:
            sdtypes[field.name] = {"sdtype": "boolean"}
        else:
            sdtypes[field.name] = {"sdtype": "categorical"}
    # Columns present in the sample but absent from the schema (should not
    # happen) default to categorical so the report never KeyErrors.
    for name in wanted - sdtypes.keys():
        sdtypes[name] = {"sdtype": "categorical"}
    return {"columns": sdtypes}


def coerce_for_sdmetrics(df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    """Parse datetime-sdtype columns to tz-naive datetimes — BQ rows arrive
    as ISO strings / date objects, which SDMetrics' datetime handling rejects."""
    out = df.copy()
    for col, spec in metadata["columns"].items():
        if spec.get("sdtype") == "datetime" and col in out.columns:
            parsed = pd.to_datetime(out[col], errors="coerce", utc=True)
            out[col] = parsed.dt.tz_localize(None)
    return out


def sdmetrics_quality(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, metadata: dict
) -> dict:
    from sdmetrics.reports.single_table import QualityReport

    report = QualityReport()
    report.generate(real_df, synth_df, metadata, verbose=False)
    properties = report.get_properties()
    return {
        "score": float(report.get_score()),
        "properties": {
            str(row["Property"]): float(row["Score"])
            for _, row in properties.iterrows()
        },
    }


def sdmetrics_diagnostic(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, metadata: dict
) -> dict:
    from sdmetrics.reports.single_table import DiagnosticReport

    report = DiagnosticReport()
    report.generate(real_df, synth_df, metadata, verbose=False)
    properties = report.get_properties()
    return {
        "score": float(report.get_score()),
        "properties": {
            str(row["Property"]): float(row["Score"])
            for _, row in properties.iterrows()
        },
    }
```

`packages/sdfb-core/src/sdfb_core/evaluation/metrics_t3.py`:

```python
"""Tier-3 evaluation — SynthEval + Evidently, behind sdfb-beam[eval-extra].

Deferred imports inside each function body: absence of the extra never
breaks a Tier 1/2 run. The EvaluationDoFn records tier3 as not_installed
rather than calling these; they exist for opt-in offline use and for the
future --eval_tier knob (design §7)."""

from __future__ import annotations

import pandas as pd

_HINT = (
    "Tier-3 evaluation needs the eval-extra extras: "
    "uv sync --group dev --package sdfb-beam --extra eval-extra"
)


def tier3_available() -> bool:
    try:
        import evidently  # noqa: F401
        import syntheval  # noqa: F401
    except ImportError:
        return False
    return True


def syntheval_privacy(real_df: pd.DataFrame, synth_df: pd.DataFrame) -> dict:
    """SynthEval's native exact-Gower DCR/NNDR — the slow second opinion."""
    try:
        from syntheval import SynthEval
    except ImportError as e:
        raise ImportError(_HINT) from e
    evaluator = SynthEval(real_df)
    results = evaluator.evaluate(synth_df, analysis_target_var=None, **{"dcr": {}, "nndr": {}})
    return {"syntheval": str(results)} if results is not None else {}


def evidently_drift_report(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, out_path: str
) -> str:
    """Durable HTML artifact (a FILE, never a dashboard). Returns out_path."""
    try:
        from evidently import Report
        from evidently.presets import DataDriftPreset
    except ImportError as e:
        raise ImportError(_HINT) from e
    report = Report(metrics=[DataDriftPreset()])
    snapshot = report.run(reference_data=real_df, current_data=synth_df)
    snapshot.save_html(out_path)
    return out_path
```

Extend `evaluation/__init__.py` `__all__` is NOT needed for t2/t3 (they are imported by module path); leave as is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/ -q`
Expected: all passed (t3's installed-path test skips on the laptop)

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/evaluation/metrics_t2.py \
        packages/sdfb-core/src/sdfb_core/evaluation/metrics_t3.py \
        packages/sdfb-tests/tests/unit/evaluation/test_metrics_t2.py \
        packages/sdfb-tests/tests/unit/evaluation/test_metrics_t3.py
git commit -m "feat(eval): tier-2 SDMetrics wrappers + tier-3 deferred-import stubs (WS3 §3)"
```

**Note for the implementer:** if `sdmetrics_quality`'s `get_properties()` shape differs in the pinned version (column names `Property`/`Score` are the documented 0.28 API), adapt the dict comprehension — the test asserts only `score` bounds and non-empty `properties`. Same for the SynthEval API surface: `syntheval_privacy` may need its call adapted to the installed version; its test only runs when the extra is present.

---

### Task 7: `gate.py` + thresholds.yml rules (§5a/§5b/§5c)

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/evaluation/gate.py`
- Modify: `config/thresholds.yml` (append two rules)
- Test: `packages/sdfb-tests/tests/unit/evaluation/test_gate.py`

**Interfaces:**
- Consumes: `Thresholds` (`sdfb_core.validation`, has `.rules: dict[str, dict]`).
- Produces (used by Tasks 9, 10): `MemorizationThresholdExceeded(RuntimeError)`, `evaluate_memorization_gate(*, identical_match_rate, column_copy_ratios, thresholds, rule_id="memorization.copy_ratio") -> dict` (outcome dict with keys `rule_id`, `evaluated`, `tripped`, `severity`, `threshold`, `column_copy_ratio_max`, `reasons`), `raise_if_blocker(outcome: dict, *, run_id: str) -> None`, `SEVERITY_BLOCKER = "BLOCKER"`, `_COLUMN_COPY_RATIO_MAX = 0.3`.

- [ ] **Step 1: Write the failing tests**

`packages/sdfb-tests/tests/unit/evaluation/test_gate.py`:

```python
"""§5a/§5b memorization gate — plain BLOCKER, compound trip, None never passes silently."""

import pytest
from sdfb_core.evaluation.gate import (
    MemorizationThresholdExceeded,
    evaluate_memorization_gate,
    raise_if_blocker,
)
from sdfb_core.validation import Thresholds

_THRESHOLDS = Thresholds(
    env="dev",
    blocker_failure_ratio=0.2,
    rules={
        "memorization.copy_ratio": {
            "dimension": "privacy",
            "severity": "BLOCKER",
            "threshold": 0.0,
        }
    },
)


def _gate(imr, ratios, thresholds=_THRESHOLDS):
    return evaluate_memorization_gate(
        identical_match_rate=imr, column_copy_ratios=ratios, thresholds=thresholds
    )


def test_clean_run_does_not_trip():
    out = _gate(0.0, {"id": 0.05})
    assert out["evaluated"] and not out["tripped"]
    raise_if_blocker(out, run_id="r1")  # no raise


def test_any_identical_match_trips():
    out = _gate(0.001, {})
    assert out["tripped"] and out["severity"] == "BLOCKER"
    with pytest.raises(MemorizationThresholdExceeded, match="r1"):
        raise_if_blocker(out, run_id="r1")


def test_column_copy_ratio_at_030_trips_even_when_rows_unique():
    # The 2026-07-20 failure mode: row-unique but column-verbatim.
    out = _gate(0.0, {"col_048": 1.0, "tier": 0.1})
    assert out["tripped"]
    assert out["column_copy_ratio_max"] == 1.0
    assert any("col_048" in r for r in out["reasons"])
    assert not _gate(0.0, {"col_048": 0.29})["tripped"]


def test_none_inputs_mean_not_evaluated_never_pass_never_trip():
    out = _gate(None, None)
    assert not out["evaluated"] and not out["tripped"]
    raise_if_blocker(out, run_id="r1")  # no raise


def test_severity_is_blocker_in_every_env_even_dev():
    for env in ("dev", "uat", "prd"):
        thresholds = _THRESHOLDS.model_copy(update={"env": env})
        assert _gate(0.5, {}, thresholds)["severity"] == "BLOCKER"


def test_missing_rule_defaults_to_blocker_zero():
    bare = Thresholds(env="dev", blocker_failure_ratio=0.2, rules={})
    out = _gate(0.1, {}, bare)
    assert out["tripped"] and out["severity"] == "BLOCKER" and out["threshold"] == 0.0


def test_non_blocker_severity_records_but_does_not_raise():
    major = Thresholds(
        env="dev",
        blocker_failure_ratio=0.2,
        rules={"memorization.copy_ratio": {"severity": "MAJOR", "threshold": 0.0}},
    )
    out = _gate(0.5, {}, major)
    assert out["tripped"] and out["severity"] == "MAJOR"
    raise_if_blocker(out, run_id="r1")  # MAJOR → metric only, no raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_gate.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

`packages/sdfb-core/src/sdfb_core/evaluation/gate.py`:

```python
"""Post-write memorization gate (WS3 §5a/§5b). Pure — no Beam imports.

Spec §5a: severity is plain BLOCKER in every env; the 2026-07-07 design's
per-env resolve_severity helper is deliberately gone. §5b: the gate trips
on identical_match_rate > threshold (0.0) OR max(column_copy_ratio) ≥ 0.3.
None inputs mean "not evaluated" — never an implicit pass, never a trip.
"""

from __future__ import annotations

from sdfb_core.validation import Thresholds

_RULE_ID = "memorization.copy_ratio"
_COLUMN_COPY_RATIO_MAX = 0.3
SEVERITY_BLOCKER = "BLOCKER"


class MemorizationThresholdExceeded(RuntimeError):  # noqa: N818 — mirrors BlockerThresholdExceeded
    """Fails the Dataflow job when the memorization gate trips at BLOCKER severity."""


def evaluate_memorization_gate(
    *,
    identical_match_rate: float | None,
    column_copy_ratios: dict[str, float] | None,
    thresholds: Thresholds | None,
    rule_id: str = _RULE_ID,
) -> dict:
    """Returns the outcome dict stored under raw_metrics_json.memorization_gate.

    Raising is a separate step (raise_if_blocker) so the eval row is ALWAYS
    written before the job is failed."""
    rules = (thresholds.rules if thresholds else {}) or {}
    rule = rules.get(rule_id, {})
    severity = str(rule.get("severity", SEVERITY_BLOCKER))
    threshold = float(rule.get("threshold", 0.0))
    max_column_ratio = (
        max(column_copy_ratios.values()) if column_copy_ratios else None
    )
    outcome = {
        "rule_id": rule_id,
        "severity": severity,
        "threshold": threshold,
        "column_copy_ratio_max": max_column_ratio,
        "evaluated": identical_match_rate is not None or max_column_ratio is not None,
        "tripped": False,
        "reasons": [],
    }
    if not outcome["evaluated"]:
        return outcome
    if identical_match_rate is not None and identical_match_rate > threshold:
        outcome["reasons"].append(
            f"identical_match_rate={identical_match_rate:.6f} > {threshold}"
        )
    if max_column_ratio is not None and max_column_ratio >= _COLUMN_COPY_RATIO_MAX:
        worst = max(column_copy_ratios, key=column_copy_ratios.get)
        outcome["reasons"].append(
            f"max(column_copy_ratio)={max_column_ratio:.4f} >= "
            f"{_COLUMN_COPY_RATIO_MAX} (column={worst})"
        )
    outcome["tripped"] = bool(outcome["reasons"])
    return outcome


def raise_if_blocker(outcome: dict, *, run_id: str) -> None:
    """MAJOR/other severities record only — same 'MAJOR → metric only'
    semantics as thresholds.yml's ladder."""
    if outcome.get("tripped") and outcome.get("severity") == SEVERITY_BLOCKER:
        raise MemorizationThresholdExceeded(
            f"run_id={run_id} memorization gate tripped: "
            + "; ".join(outcome.get("reasons", []))
        )
```

Append to `config/thresholds.yml` under `rules:` (after `regex.format`):

```yaml
  memorization.copy_ratio:
    dimension: privacy
    severity: BLOCKER  # spec §5a — BLOCKER in EVERY env, deliberately no per-env dict
    threshold: 0.0     # any exact sampled-row copy trips; column copy_ratio >= 0.3 also trips (§5b)

  column.cardinality_floor:
    dimension: fidelity
    severity: MAJOR    # recorded in validation_data_history; never job-failing (§5c)
    threshold: 0.5     # landing_distinct / min(num_rows, source_distinct) per FREE_TEXT column
```

Extend `evaluation/__init__.py` exports with `MemorizationThresholdExceeded`, `evaluate_memorization_gate`, `raise_if_blocker`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/ packages/sdfb-tests/tests/unit/validation/ -q`
Expected: all passed (validation suite proves the yml additions parse)

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/evaluation config/thresholds.yml \
        packages/sdfb-tests/tests/unit/evaluation/test_gate.py
git commit -m "feat(eval): always-BLOCKER memorization gate + cardinality-floor rule (WS3 §5a-§5c)"
```

---

### Task 8: `engine_version` on `GenerationEngine` + `_build_feature_flag_tags`

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py` (class attribute, next to `name` at ~line 202)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py:102` area, `packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py:48` area (explicit versions)
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (add `_build_feature_flag_tags` + `_engine_version` helpers after `_dlq_rule_weight`)
- Test: append to `packages/sdfb-tests/tests/unit/engines/test_abc_contract.py`; new `packages/sdfb-tests/tests/unit/test_feature_flag_tags.py`

**Interfaces:**
- Produces: `GenerationEngine.version: str = "0.1.0"` class attribute (b1_rag and b2_library both declare `version = "0.2.0"` — bumped for the WS1/WS2 logic changes); `_build_feature_flag_tags(config: PipelineConfig) -> list[str]` (sorted, deterministic); `_engine_version(engine_name: str) -> str`. Used by Task 10's branch wiring.

- [ ] **Step 1: Write the failing tests**

Append to `packages/sdfb-tests/tests/unit/engines/test_abc_contract.py`:

```python
def test_every_registered_engine_declares_a_version():
    import sdfb_core.engines  # noqa: F401 — populates the registry
    from sdfb_core.engines import ENGINE_REGISTRY

    for name, engine_class in ENGINE_REGISTRY.items():
        version = getattr(engine_class, "version", "")
        assert isinstance(version, str) and version, f"{name} missing version"
```

`packages/sdfb-tests/tests/unit/test_feature_flag_tags.py`:

```python
"""feature_flag_tags — sorted, deterministic, config-derived (design §4 notes)."""

from sdfb_beam.pipeline import PipelineConfig, _build_feature_flag_tags, _engine_version


def _config(**overrides) -> PipelineConfig:
    from sdfb_core.contracts import TableSchema

    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.d.t"}, "columns": [{"name": "a", "type": "STRING"}]}
    )
    defaults = dict(
        table_schema=schema, engine_name="b1_rag", model_client=object(), num_rows=10
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def test_tags_sorted_and_stable():
    config = _config(similarity=0.5, identity_columns=("id",), embedder_id="bge", embedder_version="v1")
    tags = _build_feature_flag_tags(config)
    assert tags == sorted(tags)
    assert _build_feature_flag_tags(config) == tags
    assert "engine:b1_rag" in tags
    assert "similarity:0.50" in tags
    assert "identity_columns:id" in tags
    assert "embedder:bge-v1" in tags


def test_optional_tags_absent_when_unset():
    tags = _build_feature_flag_tags(_config())
    assert not any(t.startswith(("identity_columns:", "embedder:", "rag_read_path:")) for t in tags)


def test_engine_version_lookup_and_unknown_fallback():
    assert _engine_version("b1_rag") == "0.2.0"
    assert _engine_version("nope") == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_abc_contract.py packages/sdfb-tests/tests/unit/test_feature_flag_tags.py -q`
Expected: FAIL (`version` missing / import errors)

- [ ] **Step 3: Implement**

In `engines/base.py`, directly under `name: str = ""` in `GenerationEngine`:

```python
    # Engine CODE version — bumped by hand when engine logic changes
    # materially. Distinct from validation_runs.model_uri (LLM weights).
    # Feeds validation_data_history.engine_version (WS3).
    version: str = "0.1.0"
```

In `b1_rag/engine.py` directly under `name = "b1_rag"`: `version = "0.2.0"  # WS2 Phase A: pool scaling + per-column retrieval + ChunkStore read path`
In `b2_library/engine.py` directly under its `name` attribute: `version = "0.2.0"  # WS1: temporal jitter, cardinality caps, free-text rerouting, lazy ignition`

In `pipeline.py`, after `_dlq_rule_weight`:

```python
def _build_feature_flag_tags(config: PipelineConfig) -> list[str]:
    """Sorted, human-diffable run-configuration tags for
    validation_data_history.feature_flag_tags — two identical configs
    produce byte-identical arrays (queryable via IN UNNEST)."""
    tags = [
        f"engine:{config.engine_name}",
        f"similarity:{config.similarity:.2f}",
        f"strict_freetext:{str(config.strict_freetext).lower()}",
    ]
    if config.identity_columns:
        tags.append("identity_columns:" + ",".join(config.identity_columns))
    if config.embedder_id:
        tags.append(f"embedder:{config.embedder_id}-{config.embedder_version}")
    if config.rag_chunks_table:
        tags.append("rag_read_path:on")
    return sorted(tags)


def _engine_version(engine_name: str) -> str:
    from sdfb_core.engines import ENGINE_REGISTRY

    return getattr(ENGINE_REGISTRY.get(engine_name), "version", "unknown")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/engines/test_abc_contract.py packages/sdfb-tests/tests/unit/test_feature_flag_tags.py -q`
Expected: all passed

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-core/src/sdfb_core/engines packages/sdfb-beam/src/sdfb_beam/pipeline.py packages/sdfb-tests
git commit -m "feat(eval): GenerationEngine.version + feature_flag_tags builder (WS3 §4)"
```

---

### Task 9: `sdfb_beam/dofns/evaluation.py` — `StratifiedReservoirFn` + `EvaluationDoFn`

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/dofns/evaluation.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/__init__.py` (export both classes)
- Test: `packages/sdfb-tests/tests/unit/dofns/test_evaluation.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7; `log_milestone(name, **fields)` (`sdfb_core.observability`); `Thresholds`.
- Produces (used by Task 10): `StratifiedReservoirFn(plan, run_id, cap, overall_cap=50_000)` (a `beam.CombineFn`); `EvaluationDoFn(*, table_schema, run_id, execution_id, engine, engine_version, feature_flag_tags, thresholds, free_text_columns, num_rows, landing_table, history_table="", validation_runs_table="", bq_client_factory=None)` — `process(_seed, real_sample, synth_sample)` yields exactly ONE dict whose keys match the Task 12 schema file: `execution_id, execution_timestamp, run_id, engine, engine_version, feature_flag_tags, sample_rows_real, sample_rows_synthetic, fidelity_overall_score, avg_dcr, nndr, identical_match_rate, max_psi, corr_diff_frobenius, tstr_f1_delta, raw_metrics_json`.

- [ ] **Step 1: Write the failing tests**

`packages/sdfb-tests/tests/unit/dofns/test_evaluation.py`:

```python
"""EvaluationDoFn — one row always; metrics populated; skipped row on empty sides."""

import json
from unittest.mock import MagicMock

from sdfb_core.contracts import TableSchema
from sdfb_core.evaluation.profile import StratificationPlan
from sdfb_core.validation import Thresholds

from sdfb_beam.dofns.evaluation import EvaluationDoFn, StratifiedReservoirFn

_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "p.d.t"},
        "columns": [
            {"name": "amount", "type": "FLOAT64"},
            {"name": "tier", "type": "STRING"},
        ],
    }
)
_THRESHOLDS = Thresholds(
    env="dev",
    blocker_failure_ratio=0.2,
    rules={"memorization.copy_ratio": {"severity": "BLOCKER", "threshold": 0.0}},
)


def _dofn(**overrides) -> EvaluationDoFn:
    kwargs = dict(
        table_schema=_SCHEMA,
        run_id="r1",
        execution_id="r1-abc",
        engine="b1_rag",
        engine_version="0.2.0",
        feature_flag_tags=["engine:b1_rag"],
        thresholds=_THRESHOLDS,
        free_text_columns=[],
        num_rows=50,
        landing_table="p.d.landing",
    )
    kwargs.update(overrides)
    return EvaluationDoFn(**kwargs)


def _rows(n, shift=0.0):
    return [{"amount": float(i) + shift, "tier": f"t{i % 3}"} for i in range(n)]


def test_yields_exactly_one_row_with_metrics():
    rows = list(_dofn().process(None, _rows(60), _rows(60, shift=1000.0)))
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "r1" and row["execution_id"] == "r1-abc"
    assert row["sample_rows_real"] == 60 and row["sample_rows_synthetic"] == 60
    assert row["identical_match_rate"] == 0.0
    assert row["avg_dcr"] is not None and row["nndr"] is not None
    assert row["corr_diff_frobenius"] is None  # single numeric column
    assert row["tstr_f1_delta"] is None  # reserved, always NULL
    raw = json.loads(row["raw_metrics_json"])
    assert raw["status"] == "evaluated"
    assert "distributions" in raw and "amount" in raw["distributions"]
    assert raw["memorization_gate"]["evaluated"]
    assert row["max_psi"] is None  # no history table configured


def test_verbatim_copy_sets_identical_match_and_gate_tripped():
    rows = list(_dofn().process(None, _rows(60), _rows(60)))
    row = rows[0]
    assert row["identical_match_rate"] == 1.0
    raw = json.loads(row["raw_metrics_json"])
    assert raw["memorization_gate"]["tripped"]


def test_empty_sample_writes_skipped_row_never_silence():
    rows = list(_dofn().process(None, [], _rows(10)))
    row = rows[0]
    assert row["sample_rows_real"] == 0
    assert row["fidelity_overall_score"] is None
    raw = json.loads(row["raw_metrics_json"])
    assert raw["status"] == "skipped_insufficient_sample"
    assert not raw["memorization_gate"]["evaluated"]


def test_previous_row_lookup_feeds_max_psi():
    # First run's distributions become the fake "previous" raw_metrics_json.
    first = list(_dofn().process(None, _rows(60), _rows(60, shift=1.0)))[0]
    fake_client = MagicMock()
    fake_client.query.return_value.result.return_value = iter(
        [{"raw_metrics_json": first["raw_metrics_json"]}]
    )
    dofn = _dofn(
        history_table="p.q.validation_data_history",
        validation_runs_table="p.q.validation_runs",
        bq_client_factory=lambda: fake_client,
    )
    row = list(dofn.process(None, _rows(60), _rows(60, shift=500.0)))[0]
    assert row["max_psi"] is not None and row["max_psi"] > 0.0
    sql = fake_client.query.call_args[0][0]
    assert "validation_data_history" in sql and "USING (run_id)" in sql


def test_lookup_failure_degrades_to_null_max_psi():
    fake_client = MagicMock()
    fake_client.query.side_effect = RuntimeError("no table")
    dofn = _dofn(
        history_table="p.q.validation_data_history",
        validation_runs_table="p.q.validation_runs",
        bq_client_factory=lambda: fake_client,
    )
    row = list(dofn.process(None, _rows(30), _rows(30, shift=9.0)))[0]
    assert row["max_psi"] is None


def test_reservoir_combinefn_bounds_output():
    fn = StratifiedReservoirFn(StratificationPlan(column=None, values=()), "r1", cap=5, overall_cap=5)
    acc = fn.create_accumulator()
    for row in _rows(40):
        acc = fn.add_input(acc, row)
    merged = fn.merge_accumulators([acc, fn.create_accumulator()])
    assert len(fn.extract_output(merged)) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/test_evaluation.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement**

`packages/sdfb-beam/src/sdfb_beam/dofns/evaluation.py`:

```python
"""Post-WriteLanding evaluation branch (WS3): stratified reservoir CombineFn
+ the single-worker EvaluationDoFn.

This module is the ONLY pipeline importer of the Tier 1/2 metric libraries
(matching how PanderaValidateBatchDoFn is the only pandera importer).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

import apache_beam as beam
import pandas as pd
from sdfb_core.contracts import TableSchema
from sdfb_core.evaluation import metrics_t1, metrics_t2
from sdfb_core.evaluation.gate import evaluate_memorization_gate
from sdfb_core.evaluation.profile import StratificationPlan
from sdfb_core.evaluation.sampling import (
    ReservoirAccumulator,
    add_row,
    extract_sample,
    merge_accumulators,
)
from sdfb_core.observability import log_milestone
from sdfb_core.validation import Thresholds

logger = logging.getLogger(__name__)


class StratifiedReservoirFn(beam.CombineFn):
    """Thin CombineFn over sdfb_core.evaluation.sampling's pure functions.
    One selection code path governs the real AND synthetic sides."""

    def __init__(
        self,
        plan: StratificationPlan,
        run_id: str,
        cap: int,
        overall_cap: int = 50_000,
    ):
        self._plan = plan
        self._run_id = run_id
        self._cap = cap
        self._overall_cap = overall_cap

    def create_accumulator(self) -> ReservoirAccumulator:
        return ReservoirAccumulator()

    def add_input(self, acc: ReservoirAccumulator, row: dict) -> ReservoirAccumulator:
        return add_row(acc, row, plan=self._plan, run_id=self._run_id, cap=self._cap)

    def merge_accumulators(self, accs) -> ReservoirAccumulator:
        return merge_accumulators(accs, cap=self._cap)

    def extract_output(self, acc: ReservoirAccumulator) -> list[dict]:
        return extract_sample(acc, cap=self._cap, overall_cap=self._overall_cap)


class EvaluationDoFn(beam.DoFn):
    """ONE invocation per run (Create([None]) seed + AsSingleton side inputs —
    the _build_validation_run_row shape). ALWAYS yields exactly one
    validation_data_history row; empty samples yield the skipped row."""

    def __init__(
        self,
        *,
        table_schema: TableSchema,
        run_id: str,
        execution_id: str,
        engine: str,
        engine_version: str,
        feature_flag_tags: list[str],
        thresholds: Thresholds | None,
        free_text_columns: list[str],
        num_rows: int,
        landing_table: str,
        history_table: str = "",
        validation_runs_table: str = "",
        bq_client_factory: Callable | None = None,
    ):
        self._table_schema = table_schema
        self._run_id = run_id
        self._execution_id = execution_id
        self._engine = engine
        self._engine_version = engine_version
        self._feature_flag_tags = list(feature_flag_tags)
        self._thresholds = thresholds
        self._free_text_columns = list(free_text_columns)
        self._num_rows = num_rows
        self._landing_table = landing_table
        self._history_table = history_table
        self._validation_runs_table = validation_runs_table
        self._bq_client_factory = bq_client_factory

    def process(self, _seed, real_sample: list[dict], synth_sample: list[dict]):
        row: dict = {
            "execution_id": self._execution_id,
            "execution_timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self._run_id,
            "engine": self._engine,
            "engine_version": self._engine_version,
            "feature_flag_tags": list(self._feature_flag_tags),
            "sample_rows_real": len(real_sample),
            "sample_rows_synthetic": len(synth_sample),
            "fidelity_overall_score": None,
            "avg_dcr": None,
            "nndr": None,
            "identical_match_rate": None,
            "max_psi": None,
            "corr_diff_frobenius": None,
            "tstr_f1_delta": None,  # reserved for TSTR (design §3) — always NULL
        }
        if not real_sample or not synth_sample:
            reason = "empty_real_sample" if not real_sample else "empty_synthetic_sample"
            logger.warning("evaluation skipped: %s (run_id=%s)", reason, self._run_id)
            gate = evaluate_memorization_gate(
                identical_match_rate=None,
                column_copy_ratios=None,
                thresholds=self._thresholds,
            )
            row["raw_metrics_json"] = json.dumps(
                {
                    "status": "skipped_insufficient_sample",
                    "reason": reason,
                    "memorization_gate": gate,
                }
            )
            yield row
            return

        real_df = pd.DataFrame(real_sample)
        synth_df = pd.DataFrame(synth_sample)
        raw: dict = {"status": "evaluated", "columns": {}, "tier3": "not_installed"}

        numeric, _ = metrics_t1.numeric_and_categorical_columns(real_df)
        distributions: dict[str, dict] = {}
        for col in real_df.columns:
            if col not in synth_df.columns:
                continue
            entry: dict = {}
            if col in numeric:
                entry["ks"] = metrics_t1.ks_statistic(real_df[col], synth_df[col])
                entry["wasserstein"] = metrics_t1.wasserstein(
                    real_df[col], synth_df[col]
                )
            else:
                entry["tvd"] = metrics_t1.tvd(real_df[col], synth_df[col])
            raw["columns"][col] = entry
            distributions[col] = metrics_t1.binned_frequencies(synth_df[col])
        # Load-bearing: the NEXT run's PSI/JSD diffs against these (§4 notes).
        raw["distributions"] = distributions

        row["corr_diff_frobenius"] = metrics_t1.corr_diff_frobenius(real_df, synth_df)
        raw["corr_diff_frobenius_spearman"] = metrics_t1.corr_diff_frobenius(
            real_df, synth_df, method="spearman"
        )
        raw["mi_matrix_diff_frobenius"] = metrics_t1.mi_matrix_diff(real_df, synth_df)
        row["avg_dcr"], row["nndr"] = metrics_t1.dcr_nndr(real_df, synth_df)
        row["identical_match_rate"] = metrics_t1.identical_match_rate(
            real_sample, synth_sample
        )
        column_ratios = metrics_t1.column_copy_ratios(real_sample, synth_sample)
        raw["column_copy_ratios"] = column_ratios
        raw["cardinality_floor"] = metrics_t1.cardinality_floor(
            real_sample,
            synth_sample,
            free_text_columns=self._free_text_columns,
            num_rows=self._num_rows,
        )

        try:
            metadata = metrics_t2.sdmetrics_metadata(
                self._table_schema, list(real_df.columns)
            )
            real_coerced = metrics_t2.coerce_for_sdmetrics(real_df, metadata)
            synth_coerced = metrics_t2.coerce_for_sdmetrics(synth_df, metadata)
            quality = metrics_t2.sdmetrics_quality(real_coerced, synth_coerced, metadata)
            row["fidelity_overall_score"] = quality["score"]
            quality["diagnostic"] = metrics_t2.sdmetrics_diagnostic(
                real_coerced, synth_coerced, metadata
            )
            raw["sdmetrics"] = quality
        except Exception as e:  # a Tier-2 library error must never sink the row
            logger.warning("tier-2 sdmetrics failed: %s", e)
            raw["sdmetrics"] = {"error": f"{type(e).__name__}: {e}"}

        previous = self._fetch_previous_distributions()
        if previous:
            psis: dict[str, float] = {}
            jsds: dict[str, float] = {}
            for col, prev_stats in previous.items():
                if col not in synth_df.columns:
                    continue
                edges = (
                    prev_stats.get("edges")
                    if prev_stats.get("kind") == "numeric"
                    else None
                )
                curr = metrics_t1.binned_frequencies(synth_df[col], edges=edges)
                psi_value = metrics_t1.psi(curr, prev_stats)
                jsd_value = metrics_t1.jsd(curr, prev_stats)
                if psi_value is not None:
                    psis[col] = psi_value
                if jsd_value is not None:
                    jsds[col] = jsd_value
            raw["psi"], raw["jsd"] = psis, jsds
            row["max_psi"] = max(psis.values()) if psis else None

        gate = evaluate_memorization_gate(
            identical_match_rate=row["identical_match_rate"],
            column_copy_ratios=column_ratios,
            thresholds=self._thresholds,
        )
        raw["memorization_gate"] = gate
        log_milestone(
            "evaluation_row_built",
            run_id=self._run_id,
            gate_tripped=gate["tripped"],
            sample_real=len(real_sample),
            sample_synth=len(synth_sample),
        )
        row["raw_metrics_json"] = json.dumps(raw, default=str)
        yield row

    def _fetch_previous_distributions(self) -> dict | None:
        """The ONE extra BQ read this design introduces: previous row per
        (landing_table, engine) via the validation_runs join — LIMIT 1, once
        per run. Any failure degrades to NULL max_psi, never a crash."""
        if not (
            self._history_table
            and self._validation_runs_table
            and self._landing_table
        ):
            return None
        try:
            from google.cloud import bigquery

            client = (
                self._bq_client_factory()
                if self._bq_client_factory
                else bigquery.Client()
            )
            sql = (
                f"SELECT h.raw_metrics_json FROM `{self._history_table}` h "
                f"JOIN `{self._validation_runs_table}` r USING (run_id) "
                "WHERE r.landing_table = @landing_table AND h.engine = @engine "
                "ORDER BY h.execution_timestamp DESC LIMIT 1"
            )
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "landing_table", "STRING", self._landing_table
                    ),
                    bigquery.ScalarQueryParameter("engine", "STRING", self._engine),
                ]
            )
            rows = list(client.query(sql, job_config=job_config).result())
            if not rows:
                return None
            payload = rows[0]["raw_metrics_json"]
            data = json.loads(payload) if isinstance(payload, str) else dict(payload)
            return data.get("distributions") or None
        except Exception as e:
            logger.warning(
                "previous-row lookup failed (%s); max_psi stays NULL", e
            )
            return None
```

In `dofns/__init__.py`, add `EvaluationDoFn` and `StratifiedReservoirFn` to the imports and `__all__` (mirror the existing entries).

**Note:** the fake `bigquery.Client` in tests is a `MagicMock`, so the real `bigquery.QueryJobConfig` import still executes — fine on the laptop (`google-cloud-bigquery` is a base `sdfb-beam` dep; no network happens).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/dofns/test_evaluation.py -q`
Expected: 6 passed

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-beam/src/sdfb_beam/dofns packages/sdfb-tests/tests/unit/dofns/test_evaluation.py
git commit -m "feat(eval): StratifiedReservoirFn + single-worker EvaluationDoFn (WS3 §2)"
```

---

### Task 10: `pipeline.py` wiring — evaluation branch + `_MemorizationGateDoFn`

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (PipelineConfig fields; `build_pipeline` param + branch after the rag block, before the `result` dict at ~line 232; new `_MemorizationGateDoFn` after `_BlockerGateDoFn`; add `import json` to the module imports)
- Test: `packages/sdfb-tests/tests/unit/test_pipeline_evaluation.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 7, 8, 9 outputs.
- Produces: `PipelineConfig` gains `enable_evaluation: bool = False`, `execution_id: str = ""`, `validation_runs_table: str = ""`, `validation_data_history_table: str = ""`; `build_pipeline(..., validation_data_history_sink: beam.PTransform | None = None)`; `result["validation_data_history"]` PCollection. Used by Task 11's CLI.

- [ ] **Step 1: Write the failing tests**

`packages/sdfb-tests/tests/unit/test_pipeline_evaluation.py`:

```python
"""Evaluation branch wiring — DirectRunner end-to-end with a fake engine client."""

import json

import apache_beam as beam
import pytest
from apache_beam.testing.test_pipeline import TestPipeline
from apache_beam.testing.util import assert_that
from sdfb_core.validation import Thresholds

from sdfb_beam.pipeline import PipelineConfig, build_pipeline

# Reuse whatever fixture builds a working DirectRunner config in
# tests/unit/test_pipeline.py — import its helpers rather than duplicating.
from tests.unit.test_pipeline import _make_config, _memory_sinks  # noqa: F401  (adapt to actual names)


def _eval_config(**overrides) -> PipelineConfig:
    config = _make_config()  # existing helper: schema + fake client + defaults
    return __import__("dataclasses").replace(
        config,
        enable_evaluation=True,
        execution_id="exec-1",
        validation_runs_table="",
        validation_data_history_table="",
        **overrides,
    )


class _CollectSink(beam.PTransform):
    def __init__(self):
        self.result = None

    def expand(self, pcoll):
        return pcoll | beam.Map(lambda row: row)


def test_eval_branch_absent_without_sink_or_flag():
    with TestPipeline() as p:
        result = build_pipeline(
            p,
            reference_rows=_reference_rows(),
            config=_eval_config(enable_evaluation=False),
            **_memory_sinks(),
            validation_data_history_sink=_CollectSink(),
        )
        assert "validation_data_history" not in result
    with TestPipeline() as p:
        result = build_pipeline(
            p,
            reference_rows=_reference_rows(),
            config=_eval_config(),
            **_memory_sinks(),
            validation_data_history_sink=None,
        )
        assert "validation_data_history" not in result


def test_eval_branch_emits_one_row_with_execution_id():
    def _check(rows):
        assert len(rows) == 1
        row = rows[0]
        assert row["execution_id"] == "exec-1"
        assert json.loads(row["raw_metrics_json"])["memorization_gate"] is not None

    with TestPipeline() as p:
        result = build_pipeline(
            p,
            reference_rows=_reference_rows(),
            config=_eval_config(),
            **_memory_sinks(),
            validation_data_history_sink=_CollectSink(),
        )
        assert_that(
            result["validation_data_history"] | beam.combiners.ToList(), _check
        )
```

**Implementer note:** open `packages/sdfb-tests/tests/unit/test_pipeline.py` FIRST and reuse its actual fixture/helper names for config, reference rows, and in-memory sinks (the names `_make_config`, `_memory_sinks`, `_reference_rows` above are stand-ins for whatever that file provides — the assertions and flow are the requirement). A gate-trip test belongs here too if the existing fake client can be driven to emit verbatim reference copies: assert `MemorizationThresholdExceeded` surfaces from `p.run()` when `fail_on_blocker=True` and the synthetic rows duplicate reference rows; skip it if the fake engine cannot produce verbatim copies deterministically — the gate itself is fully covered by Task 7/9 tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/test_pipeline_evaluation.py -q`
Expected: FAIL (`build_pipeline` has no `validation_data_history_sink` parameter)

- [ ] **Step 3: Implement**

`PipelineConfig` — append after `embedder_version: str = ""`:

```python
    # WS3 — post-WriteLanding evaluation branch. enable_evaluation and the
    # sink are independent (both required for the branch); execution_id is
    # the append-only natural key (a run_id may be re-evaluated).
    enable_evaluation: bool = False
    execution_id: str = ""
    validation_runs_table: str = ""
    validation_data_history_table: str = ""
```

`build_pipeline` signature — after `rag_chunks_sink: beam.PTransform | None = None,` add `validation_data_history_sink: beam.PTransform | None = None,`.

Module imports — add:

```python
import json

from sdfb_core.evaluation.gate import raise_if_blocker
from sdfb_core.evaluation.profile import choose_stratification_column
from sdfb_core.evaluation.sampling import per_stratum_cap

from sdfb_beam.dofns import EvaluationDoFn, StratifiedReservoirFn
```

Insert the branch AFTER the `validation_runs` block (after `result["validation_run"] = summary_rows`, ~line 275) and before the final `return result` — symmetric with that block, and `result` already exists there:

```python
    # WS3 — post-WriteLanding evaluation branch. Sibling of the
    # validation_runs block: same Create([None]) + AsSingleton collapse.
    if config.enable_evaluation and validation_data_history_sink is not None:
        eval_thresholds = config.thresholds or Thresholds(
            env="dev", blocker_failure_ratio=1.0
        )
        plan = choose_stratification_column(config.table_schema, reference_rows)
        cap = per_stratum_cap(max(len(plan.values), 1))
        real_sample = (
            p
            | "EvalReferenceRows" >> beam.Create(reference_rows)
            | "EvalSampleReference"
            >> beam.CombineGlobally(StratifiedReservoirFn(plan, config.run_id, cap))
        )
        synth_sample = uniq["unique"] | "EvalSampleSynthetic" >> beam.CombineGlobally(
            StratifiedReservoirFn(plan, config.run_id, cap)
        )
        eval_rows = (
            p
            | "EvalSeed" >> beam.Create([None])
            | "Evaluate"
            >> beam.ParDo(
                EvaluationDoFn(
                    table_schema=config.table_schema,
                    run_id=config.run_id,
                    execution_id=config.execution_id or config.run_id,
                    engine=config.engine_name,
                    engine_version=_engine_version(config.engine_name),
                    feature_flag_tags=_build_feature_flag_tags(config),
                    thresholds=eval_thresholds,
                    free_text_columns=_rag_free_text_columns(
                        config.table_schema, reference_rows
                    ),
                    num_rows=config.num_rows,
                    landing_table=config.landing_table,
                    history_table=config.validation_data_history_table,
                    validation_runs_table=config.validation_runs_table,
                ),
                real_sample=beam.pvalue.AsSingleton(real_sample),
                synth_sample=beam.pvalue.AsSingleton(synth_sample),
            )
        )
        _ = eval_rows | "WriteValidationDataHistory" >> validation_data_history_sink
        if config.fail_on_blocker:
            _ = eval_rows | "MemorizationGate" >> beam.ParDo(_MemorizationGateDoFn())
        result["validation_data_history"] = eval_rows
```

After `_BlockerGateDoFn`:

```python
class _MemorizationGateDoFn(beam.DoFn):
    """Fails the job when the eval row's memorization gate tripped at BLOCKER
    severity (§5a). Downstream of the sink write, so the row always lands."""

    def process(self, row: dict):
        raw = json.loads(row.get("raw_metrics_json") or "{}")
        raise_if_blocker(
            raw.get("memorization_gate") or {}, run_id=str(row.get("run_id"))
        )
        yield row
```

- [ ] **Step 4: Run tests to verify they pass, then the full baseline**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/test_pipeline_evaluation.py packages/sdfb-tests/tests/unit/test_pipeline.py -q
uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q
```
Expected: all green

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-beam/src/sdfb_beam/pipeline.py packages/sdfb-tests/tests/unit/test_pipeline_evaluation.py
git commit -m "feat(eval): pipeline evaluation branch + memorization gate DoFn (WS3 §2, §5a)"
```

---

### Task 11: CLI — `--enable_evaluation` + `--validation_data_history_table`

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (argparse after `--validation_runs_table` at ~line 108; guard next to the `--build_rag_layer` guard at ~line 131; sink + config in `main` — sink block next to `validation_runs_sink` at ~line 365, config fields at ~line 348, `build_pipeline` call at ~line 375; add `import uuid`)
- Test: append to `packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py`

**Interfaces:**
- Consumes: Task 10's `PipelineConfig` fields + `build_pipeline` parameter.
- Produces: argparse args `enable_evaluation` (store_true), `validation_data_history_table` (default `""`); `execution_id = f"{args.run_id}-{uuid.uuid4().hex[:12]}"`.

- [ ] **Step 1: Write the failing tests** (append; reuse the file's existing `parse_args` import and test style)

```python
def test_enable_evaluation_flags_default_off():
    args, _ = parse_args(_minimal_args())  # reuse the file's existing minimal-args helper
    assert args.enable_evaluation is False
    assert args.validation_data_history_table == ""


def test_enable_evaluation_requires_history_table():
    with pytest.raises(SystemExit):
        parse_args(_minimal_args() + ["--enable_evaluation"])


def test_enable_evaluation_with_table_parses():
    args, _ = parse_args(
        _minimal_args()
        + ["--enable_evaluation", "--validation_data_history_table", "p.q.validation_data_history"]
    )
    assert args.enable_evaluation is True
    assert args.validation_data_history_table == "p.q.validation_data_history"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py -q`
Expected: new tests FAIL (`AttributeError: enable_evaluation` / no SystemExit)

- [ ] **Step 3: Implement**

In `parse_args` after the `--validation_runs_table` argument:

```python
    p.add_argument("--enable_evaluation", action="store_true",
                   help="Post-WriteLanding fidelity/privacy evaluation branch "
                        "(WS3): one validation_data_history row per run + the "
                        "always-BLOCKER memorization gate.")
    p.add_argument("--validation_data_history_table", default="",
                   help="BQ table for the evaluation row "
                        "(project.dataset.validation_data_history). Required "
                        "with --enable_evaluation.")
```

Next to the `--build_rag_layer` guard:

```python
    if args.enable_evaluation and not args.validation_data_history_table:
        p.error("--enable_evaluation requires --validation_data_history_table")
```

In `main()` — sink block after `validation_runs_sink`:

```python
    validation_data_history_sink = None
    if args.enable_evaluation:
        validation_data_history_sink = WriteToBigQuery(
            table=args.validation_data_history_table,
            method=WriteToBigQuery.Method.FILE_LOADS,
            write_disposition=BigQueryDisposition.WRITE_APPEND,
            create_disposition=BigQueryDisposition.CREATE_NEVER,
        )
        log_milestone(
            "evaluation_enabled",
            history_table=args.validation_data_history_table,
        )
```

In the `PipelineConfig(...)` construction, append:

```python
        enable_evaluation=args.enable_evaluation,
        execution_id=f"{args.run_id}-{uuid.uuid4().hex[:12]}",
        validation_runs_table=args.validation_runs_table,
        validation_data_history_table=args.validation_data_history_table,
```

Add `validation_data_history_sink=validation_data_history_sink,` to the `build_pipeline(...)` call and `import uuid` to the module imports.

- [ ] **Step 4: Run tests + full baseline**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py -q
uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q
```
Expected: all green

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py
git commit -m "feat(eval): --enable_evaluation + --validation_data_history_table CLI wiring (WS3 §2)"
```

---

### Task 12: `validation_data_history.schema.json` + row/schema drift guard

**Files:**
- Create: `config/bq_schema/synthetic_data_quality/validation_data_history.schema.json`
- Test: `packages/sdfb-tests/tests/unit/evaluation/test_history_schema.py`

**Interfaces:**
- Consumes: `EvaluationDoFn` row keys (Task 9).
- Produces: the provisioning contract for `bq mk` (WS5 threads it into `DEPLOYMENT_PREREQUISITES.md`; not here).

- [ ] **Step 1: Write the failing test**

`packages/sdfb-tests/tests/unit/evaluation/test_history_schema.py`:

```python
"""Drift guard: schema file fields == EvaluationDoFn row keys, exactly."""

import json
from pathlib import Path

from sdfb_core.contracts import TableSchema
from sdfb_core.validation import Thresholds

from sdfb_beam.dofns.evaluation import EvaluationDoFn

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4]
    / "config/bq_schema/synthetic_data_quality/validation_data_history.schema.json"
)


def test_schema_file_matches_dofn_row_keys():
    fields = json.loads(_SCHEMA_PATH.read_text())
    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.d.t"}, "columns": [{"name": "a", "type": "STRING"}]}
    )
    dofn = EvaluationDoFn(
        table_schema=schema, run_id="r", execution_id="e", engine="b1_rag",
        engine_version="0.2.0", feature_flag_tags=[], thresholds=None,
        free_text_columns=[], num_rows=1, landing_table="p.d.l",
    )
    row = next(iter(dofn.process(None, [], [])))
    assert {f["name"] for f in fields} == set(row.keys())
    required = {f["name"] for f in fields if f.get("mode") == "REQUIRED"}
    assert {"execution_id", "execution_timestamp", "run_id", "engine", "engine_version"} <= required
    by_name = {f["name"]: f for f in fields}
    assert by_name["feature_flag_tags"]["mode"] == "REPEATED"
    assert by_name["raw_metrics_json"]["type"] == "JSON"
```

**Note:** `parents[4]` must resolve to the repo root from `packages/sdfb-tests/tests/unit/evaluation/` — verify against how sibling tests locate `config/` (e.g. the thresholds tests) and reuse their convention if one exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_history_schema.py -q`
Expected: FAIL (`FileNotFoundError`)

- [ ] **Step 3: Create the schema file** (same JSON-array format as the sibling `validation_runs.schema.json`)

`config/bq_schema/synthetic_data_quality/validation_data_history.schema.json`:

```json
[
  {"name": "execution_id", "type": "STRING", "mode": "REQUIRED",
   "description": "Natural key for this evaluation execution — a run_id may be re-evaluated, appending a new row (append-only)."},
  {"name": "execution_timestamp", "type": "TIMESTAMP", "mode": "REQUIRED",
   "description": "Row write time (UTC); DAY partition key."},
  {"name": "run_id", "type": "STRING", "mode": "REQUIRED",
   "description": "Pipeline run id; joins to validation_runs.run_id and dlq.run_id."},
  {"name": "engine", "type": "STRING", "mode": "REQUIRED",
   "description": "b1_rag | b2_library — matches validation_runs.engine."},
  {"name": "engine_version", "type": "STRING", "mode": "REQUIRED",
   "description": "GenerationEngine.version — engine LOGIC evolution, distinct from validation_runs.model_uri (weights)."},
  {"name": "feature_flag_tags", "type": "STRING", "mode": "REPEATED",
   "description": "Sorted run-configuration tags, e.g. engine:b1_rag, similarity:0.50. Queryable via IN UNNEST."},
  {"name": "sample_rows_real", "type": "INTEGER", "mode": "NULLABLE",
   "description": "Rows in the sampled real side after the stratified cap (0 = reference sample unavailable)."},
  {"name": "sample_rows_synthetic", "type": "INTEGER", "mode": "NULLABLE",
   "description": "Rows in the sampled synthetic side after the stratified cap (0 = every generated row rejected pre-write)."},
  {"name": "fidelity_overall_score", "type": "FLOAT", "mode": "NULLABLE",
   "description": "SDMetrics QualityReport score, 0-1. NULL when either sample is empty or Tier 2 errored."},
  {"name": "avg_dcr", "type": "FLOAT", "mode": "NULLABLE",
   "description": "Mean Distance to Closest Record (Tier 1, Gower-style). Lower = higher memorization risk."},
  {"name": "nndr", "type": "FLOAT", "mode": "NULLABLE",
   "description": "Mean Nearest-Neighbor Distance Ratio. Near 0 = re-identification risk; near 1 = safe."},
  {"name": "identical_match_rate", "type": "FLOAT", "mode": "NULLABLE",
   "description": "Fraction of sampled synthetic rows with an exact row_digest match in the sampled real rows. Feeds memorization.copy_ratio (BLOCKER, threshold 0.0)."},
  {"name": "max_psi", "type": "FLOAT", "mode": "NULLABLE",
   "description": "Max PSI across columns vs the previous validation_data_history row for the same (landing_table, engine). NULL on the first run."},
  {"name": "corr_diff_frobenius", "type": "FLOAT", "mode": "NULLABLE",
   "description": "Frobenius norm of (corr_real - corr_synth), Pearson. Spearman + MI diffs live in raw_metrics_json."},
  {"name": "tstr_f1_delta", "type": "FLOAT", "mode": "NULLABLE",
   "description": "Reserved for future TSTR. Always NULL until implemented."},
  {"name": "raw_metrics_json", "type": "JSON", "mode": "NULLABLE",
   "description": "Full nested payload: per-column Tier-1 stats, SDMetrics sub-scores, column_copy_ratios (gate §5b), cardinality_floor (§5c), binned distributions (next run's PSI/JSD input), memorization_gate outcome, sampling metadata."}
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/evaluation/test_history_schema.py -q`
Expected: 1 passed

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-sync ruff check .
git add config/bq_schema/synthetic_data_quality/validation_data_history.schema.json \
        packages/sdfb-tests/tests/unit/evaluation/test_history_schema.py
git commit -m "feat(eval): validation_data_history schema file + row drift guard (WS3 §4)"
```

---

### Task 13: probe + report-prompt integration (§5d)

**Files:**
- Modify: `scripts/e2e/e2e_gcp_probe.py` (extend `bq_quality` table tuple at ~line 315; new `derive_run_ids` helper before `main`; auto-derive call in `main` at ~line 760; ensure `import re` present)
- Modify: `.github/prompts/end_to_end_validation_report_generation.prompt.md` (Inputs table ~L41; Step 3 ~L151; Step 4 note ~L250; Step 5 scorecard ~L257; consistency rules ~L340)
- Test: append to `packages/sdfb-tests/tests/unit/scripts/test_e2e_gcp_probe.py`

**Interfaces:**
- Consumes: probe's existing `_quote`, `_split_fqn`, `bq_quality`, `main`; `sanitize_job_name`'s slug rule (`run_pipeline.py:205`: `re.sub(r"[^a-z0-9]+", "-", run_id.lower()).strip("-")`).
- Produces: `derive_run_ids(client, quality_dataset: str, dataflow_results: list[dict]) -> list[str]`; `bq_quality` additionally returns a `validation_data_history` key.

- [ ] **Step 1: Write the failing tests** (append, reusing the file's existing fake-client pattern)

```python
def test_derive_run_ids_matches_job_name_slug():
    from scripts.e2e_gcp_probe import derive_run_ids

    client = MagicMock()
    client.query.return_value.result.return_value = iter(
        [
            {"run_id": "manual__2026-07-19T07:42:28+00:00-5608"},
            {"run_id": "unrelated-run"},
        ]
    )
    dataflow_results = [{"job_id": "j1", "name": "sdfb-manual-2026-07-19t07-42-28-00-00-5608"}]
    assert derive_run_ids(client, "p.synthetic_data_quality", dataflow_results) == [
        "manual__2026-07-19T07:42:28+00:00-5608"
    ]


def test_derive_run_ids_empty_without_inputs():
    from scripts.e2e_gcp_probe import derive_run_ids

    assert derive_run_ids(MagicMock(), "", [{"name": "x"}]) == []
    assert derive_run_ids(MagicMock(), "p.d", []) == []


def test_bq_quality_fetches_history_table():
    from scripts.e2e_gcp_probe import bq_quality

    client = MagicMock()
    client.query.return_value.result.return_value = iter([])
    out = bq_quality(client, "p.synthetic_data_quality", ["r1"])
    assert "validation_data_history" in out
    queried = " ".join(str(c[0][0]) for c in client.query.call_args_list)
    assert "validation_data_history" in queried
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_e2e_gcp_probe.py -q`
Expected: new tests FAIL (`ImportError: cannot import name 'derive_run_ids'` / missing key)

- [ ] **Step 3: Implement the probe changes**

In `bq_quality`, change the loop line to:

```python
    for table in ("validation_runs", "dlq", "validation_data_history"):
```

Before `main`, add (and `import re` at the top if absent):

```python
def derive_run_ids(
    client, quality_dataset: str, dataflow_results: list[dict]
) -> list[str]:
    """WS3 §5d — when --run-id is absent, derive it from validation_runs:
    Dataflow job names are sanitize_job_name slugs of run_id
    (run_pipeline.py), so match recent run_id slugs against the job names.
    Retires the recurring unscoped-DLQ gap (2026-07-19/20 reports)."""
    job_names = [r.get("name") or "" for r in dataflow_results if r.get("name")]
    if not (quality_dataset and job_names):
        return []
    proj, ds, _ = _split_fqn(quality_dataset + ".x")
    try:
        rows = client.query(
            f"SELECT run_id FROM {_quote(f'{proj}.{ds}.validation_runs')} "
            "ORDER BY created_at DESC LIMIT 200"
        ).result()
    except Exception:
        return []
    derived: set[str] = set()
    for row in rows:
        run_id = str(row["run_id"])
        slug = re.sub(r"[^a-z0-9]+", "-", run_id.lower()).strip("-")
        if slug and any(slug in name for name in job_names):
            derived.add(run_id)
    return sorted(derived)
```

In `main`, between `_annotate_engine_labels(...)` and the `report` dict, and switch the report's `quality` call to use `run_ids`:

```python
    run_ids = args.run_ids
    if not run_ids and args.quality_dataset:
        run_ids = derive_run_ids(client, args.quality_dataset, dataflow_results)
        if run_ids:
            print(f"derived run_ids from validation_runs: {run_ids}")
```

…and in the `report` dict: `"quality": bq_quality(client, args.quality_dataset, run_ids),`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/scripts/test_e2e_gcp_probe.py -q`
Expected: all passed

- [ ] **Step 5: Edit the report prompt** (read the file first; apply these content changes at the anchors)

1. **Inputs table** (~L41, after the `RUN_IDS` row): add row — `| HISTORY_FQN | \`<project>.synthetic_data_quality.validation_data_history\` — per-run evaluation rows (present for runs launched with --enable_evaluation) |`. Mark `RUN_IDS` as optional: append to its description "*(optional since WS3 — the probe auto-derives run_ids from validation_runs when omitted)*".
2. **Step 3** (~L151): after the probe invocation bullet, add: "The probe auto-derives `run_id` from the `validation_runs` rows whose sanitized slug matches each Dataflow job name when `--run-id` is not passed, and fetches the matching `validation_data_history` rows into the `quality.validation_data_history` key — never analyze DLQ/validation rows unscoped by run_id."
3. **Step 4** (~L250): replace the forward-looking `--enable-evaluation` note with the instruction: "Pull fidelity/privacy numbers from the run's `validation_data_history` row when present (`quality.validation_data_history` in the probe JSON); hand-compute them only for runs that predate `--enable_evaluation`."
4. **Step 5 scorecard** (~L257): add columns `fidelity_overall_score`, `avg_dcr`, `nndr`, `identical_match_rate`, `max_psi` — "sourced from the eval row; `n/a` for pre-WS3 runs".
5. **Consistency rules** (~L340): amend the numbers-provenance rule to "numbers come from the analyzer/probe JSON **or the validation_data_history row** — never from memory".

- [ ] **Step 6: Lint + commit**

```bash
uv run --no-sync ruff check .
git add scripts/e2e/e2e_gcp_probe.py .github/prompts/end_to_end_validation_report_generation.prompt.md \
        packages/sdfb-tests/tests/unit/scripts/test_e2e_gcp_probe.py
git commit -m "feat(eval): probe run_id auto-derive + history fetch + report-prompt integration (WS3 §5d)"
```

---

### Task 14: design-doc rewrite + final verification

**Files:**
- Modify: `docs/designs/2026-07-07-evaluation-framework-design.md` (in-place rewrite, spec §5 mandate)

**Interfaces:** none (docs).

- [ ] **Step 1: Rewrite the design doc** — keep its structure; apply this delta list exactly:

1. Header: `Status: implemented (WS3, 2026-07-21 — branch ws3-eval-framework)`; add an "Implemented by" pointer to `docs/superpowers/plans/2026-07-21-ws3-eval-framework.md` and spec §5.
2. §2 Toggle: flag spellings become `--enable_evaluation` / `--validation_data_history_table` (repo argparse convention); document the `p.error` guard — "`--enable_evaluation` without the table is a launcher error (WS2's silent-no-op precedent), superseding this design's original empty-skips-write contract".
3. §2: `PipelineConfig` gained `enable_evaluation`, `execution_id`, `validation_runs_table`, `validation_data_history_table`; `execution_id` is generated at the CLI as `{run_id}-{uuid4hex[:12]}`.
4. §5 Gate integration: REPLACE the per-env severity YAML block and the whole `resolve_severity` passage with the implemented §5a shape (plain `severity: BLOCKER`, threshold 0.0, `config/thresholds.yml` verbatim from Task 7); document the §5b compound trip condition (`identical_match_rate > 0` OR `max(column_copy_ratio) >= 0.3`, columns with reference distinct > 100) and `evaluate_memorization_gate`'s actual signature (outcome dict + separate `raise_if_blocker`); note the gate DoFn is downstream of the sink write and honors `fail_on_blocker` (fake-client runs informational). Also REWRITE the §5 "Complements — does not replace" paragraph's claim that severity is env-conditional — it now reads: severity is fixed BLOCKER everywhere; the sample-based false-negative caveat is accepted because any *observed* exact copy is disqualifying in every env (two E2E cycles proved leniency ships leaks).
5. §5: add the `column.cardinality_floor` MAJOR rule (§5c) — formula, FREE_TEXT column source (the shared b1 profile classification via `_rag_free_text_columns`), recorded-not-gating.
6. §3 Tier 1 table: add rows for `column_copy_ratios` (§5b) and `cardinality_floor` (§5c); note MI is computed on quantile-discretized columns via `mutual_info_score` (implementation choice).
7. §6 Packaging: record the audit outcome (sdmetrics 0.28.x — torch only under its `[torch]` extra → base dep confirmed); pin list as implemented (`scipy>=1.11.0, scikit-learn>=1.4.0, pandas>=2.2.0, sdmetrics>=0.28.0`, `eval-extra = syntheval>=1.5.0, evidently>=0.4.0`); note Tier 3 is NOT invoked by `EvaluationDoFn` (functions + extra only; the `--eval_tier` knob stays future work).
8. §4 DDL: unchanged; add the pointer to `config/bq_schema/synthetic_data_quality/validation_data_history.schema.json` and the Task 12 drift-guard test.
9. §2 sampling: document the implemented tie-break (`(sort_key, row_digest)` entries, 2×cap trim amortization) and `extract_sample`'s explicit `cap` parameter.

- [ ] **Step 2: Full verification**

```bash
uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q   # 515 + ~40 new, all green
uv run --no-sync ruff check .
```

- [ ] **Step 3: Commit**

```bash
git add docs/designs/2026-07-07-evaluation-framework-design.md
git commit -m "docs(eval): evaluation-framework design rewritten to implemented state (WS3 §5)"
```

---

## Deferred to M4/GCP (not in this plan)

- `bq mk` provisioning of `validation_data_history` (command in design §4; prereq-script conditional check is WS5).
- E2E run with `--enable_evaluation` on the b1/b2 matrix; every subsequent report cites the eval row (spec §8).
- WS5 docs sweep: `DEPLOYMENT_PREREQUISITES.md`, `RUN_PLAYBOOK.md`, `ROADMAP.md`, flex-template metadata/DAG/tiers threading of the two new flags (flag threading follows the WS4 deploy-chain pattern when WS4 executes).
