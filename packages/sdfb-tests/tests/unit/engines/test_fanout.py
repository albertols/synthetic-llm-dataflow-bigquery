"""Parent-driven fan-out math (design 2026-09-10, ADR 0036).

A child's rows are its parent's keys x the SOURCE fan-out histogram; the
PK-completing cells of one key are drawn WITHOUT replacement, so a PK
that contains an FK is unique by construction (the 2026-09-09 runs lost
87.9% then 56.5% of C_TABLE to random draws of exactly that PK).
"""

from __future__ import annotations

import random
import time
from collections import Counter

import pytest
from sdfb_core.engines.fanout import (
    CellTable,
    ConditionalEdge,
    FanoutHistogram,
    FanoutPlan,
    conditional_values,
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

    def test_zero_weight_row_is_rejected(self):
        with pytest.raises(ValueError, match="positive count"):
            CellTable(cols=("X",), rows=[(1,), (2,)], counts=[1, 0])

    def test_small_draw_on_a_big_table_stays_cheap(self):
        """ADR 0036 D3 claims O(k), not O(k*C). `random.choices` rebuilds
        the cumulative weights on EVERY call, so the rejection loop was
        O(k*C) — 746 us per key at C=10,000, i.e. ~20 minutes of pure draw
        for a 100M-key parent. The cumulative table is built once in
        `__post_init__` instead and each draw is a bisect."""
        table = CellTable(
            cols=("X",),
            rows=[(i,) for i in range(10_000)],
            counts=[1.0 + (i % 7) for i in range(10_000)],
        )
        rng = random.Random(0)
        started = time.perf_counter()
        for _ in range(1_000):
            assert len(table.draw(3, rng, exact=True)) == 3
        assert time.perf_counter() - started < 2.0


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


class TestConditionalValues:
    def test_conditional_values_are_without_replacement_until_wrap(self):
        cands = [("r1",), ("r2",), ("r3",)]
        out = conditional_values("run", ("t1",), "T,R", cands, 5)
        assert sorted(out[:3]) == sorted(cands)  # a permutation
        assert out[3:] == out[:2]  # then wraps in the same order

    def test_conditional_values_are_deterministic_per_key_and_edge(self):
        cands = [("a",), ("b",), ("c",), ("d",)]
        assert conditional_values("run", ("k",), "E", cands, 4) == conditional_values(
            "run", ("k",), "E", cands, 4
        )

    def test_conditional_values_without_candidates_are_empty_tuples(self):
        assert conditional_values("run", ("k",), "E", [], 3) == [(), (), ()]

    def test_conditional_values_nonpositive_k_is_empty(self):
        assert conditional_values("run", ("k",), "E", [("a",), ("b",)], 0) == []
        assert conditional_values("run", ("k",), "E", [("a",), ("b",)], -1) == []

    def test_conditional_values_does_not_mutate_the_caller_list(self):
        cands = [("a",), ("b",), ("c",)]
        before = list(cands)
        conditional_values("run", ("k",), "E", cands, 3)
        assert cands == before


class TestConditionalEdge:
    def test_payload_round_trip(self):
        edge = ConditionalEdge(id="T,R", cols=("R",), nullable=True)
        assert ConditionalEdge.from_payload(edge.to_payload()) == edge

    def test_to_payload_shape(self):
        edge = ConditionalEdge(id="T,R", cols=("T", "R"), nullable=False)
        assert edge.to_payload() == {"id": "T,R", "cols": ["T", "R"], "nullable": False}


class TestFanoutPlanConditional:
    def test_plan_payload_round_trips_conditional_edges(self):
        plan = FanoutPlan(
            driving_cols=("T", "L"),
            histogram=FanoutHistogram({1: 1}),
            cells=None,
            exact_cells=True,
            conditional=(ConditionalEdge(id="T,R", cols=("R",), nullable=True),),
        )
        assert FanoutPlan.from_payload(plan.to_payload()) == plan
        assert "R" in plan.columns

    def test_plan_payload_missing_conditional_key_defaults_to_empty(self):
        plan = FanoutPlan(
            driving_cols=("T", "L"),
            histogram=FanoutHistogram({1: 1}),
            cells=None,
            exact_cells=True,
            conditional=(ConditionalEdge(id="T,R", cols=("R",), nullable=True),),
        )
        assert FanoutPlan.from_payload({**plan.to_payload(), "conditional": None}).conditional == ()

    def test_plan_without_conditional_edges_round_trips_to_empty_tuple(self):
        plan = FanoutPlan(
            driving_cols=("PID",),
            histogram=FanoutHistogram({1: 1}),
            cells=None,
            exact_cells=False,
        )
        assert plan.conditional == ()
        assert FanoutPlan.from_payload(plan.to_payload()).conditional == ()
        assert plan.to_payload()["conditional"] == []
