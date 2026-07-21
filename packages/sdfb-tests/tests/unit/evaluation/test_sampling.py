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
    sample = _sample(_rows(200), cap=10)  # 2 strata x cap 10 = 20 > overall 15
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
