"""PK capacity under random draws + FK key-sample cap sizing (ADR 0035).

The 2026-09-09 three-table launch (job …-16364509521974163594): C_TABLE's
PK is (FK to B_TABLE, two small categoricals). With B_TABLE enabled the
FK member collapsed to the 100k side-input cap, the tuple capacity fell
to ~1.2M, and 8 789 594 of 10M rows diverted as pk.duplicate — 3h16m
after launch. These are the numbers preflight must do at second zero.
"""

from __future__ import annotations

import math

import pytest
from sdfb_core.engines.pk_capacity import (
    FK_KEY_SAMPLE_CEILING,
    FK_KEY_SAMPLE_FLOOR,
    FK_KEY_SAMPLE_MARGIN,
    expected_duplicate_share,
    fk_key_sample_cap,
    max_rows_under_share,
)


class TestExpectedDuplicateShare:
    """N uniform draws into K slots land K(1 - e^-N/K) distinct tuples;
    the rest are duplicates the uniqueness barrier diverts."""

    def test_reproduces_the_2026_09_09_collapse(self):
        # 10M draws into ~1.21M tuples -> 87.9% measured pk.duplicate.
        share = expected_duplicate_share(10_000_000, 1_210_000)
        assert share == pytest.approx(0.879, abs=0.002)

    def test_capacity_equal_to_rows_still_loses_a_third(self):
        # K == N is NOT enough: 1 - (1 - e^-1) = 36.8% duplicates.
        assert expected_duplicate_share(1_000, 1_000) == pytest.approx(
            1 - (1 - math.exp(-1)), abs=1e-9
        )

    def test_margin_of_ten_keeps_duplicates_under_five_percent(self):
        share = expected_duplicate_share(1_000_000, 10_000_000)
        assert 0.04 < share < 0.05

    def test_unbounded_capacity_means_no_duplicates(self):
        assert expected_duplicate_share(1_000_000, None) == 0.0

    def test_zero_rows_means_no_duplicates(self):
        assert expected_duplicate_share(0, 100) == 0.0

    def test_share_is_monotone_in_rows(self):
        shares = [expected_duplicate_share(n, 1_000) for n in (10, 100, 1_000, 10_000)]
        assert shares == sorted(shares)


class TestFkKeySampleCap:
    """How many parent key tuples a child whose PK contains the FK must
    see: enough that (keys x other PK members) covers MARGIN x num_rows,
    clamped to the side-input floor (today's flat cap) and ceiling."""

    def test_constants_bracket_the_historic_cap(self):
        assert FK_KEY_SAMPLE_FLOOR == 100_000
        assert FK_KEY_SAMPLE_CEILING > FK_KEY_SAMPLE_FLOOR
        assert FK_KEY_SAMPLE_MARGIN >= 10

    def test_c_table_shape_at_one_million_rows(self):
        # other members contribute x12; 10 x 1M / 12 = 833 334 keys.
        assert fk_key_sample_cap(1_000_000, 12) == math.ceil(
            FK_KEY_SAMPLE_MARGIN * 1_000_000 / 12
        )

    def test_c_table_shape_at_ten_million_rows_hits_the_ceiling(self):
        assert fk_key_sample_cap(10_000_000, 12) == FK_KEY_SAMPLE_CEILING

    def test_small_runs_keep_the_floor(self):
        assert fk_key_sample_cap(1_000, 12) == FK_KEY_SAMPLE_FLOOR

    def test_unbounded_siblings_keep_the_floor(self):
        # A temporal/numeric PK member already covers the tuple.
        assert fk_key_sample_cap(10_000_000, None) == FK_KEY_SAMPLE_FLOOR

    def test_fk_alone_as_the_pk_needs_margin_times_rows(self):
        assert fk_key_sample_cap(50_000, 1) == FK_KEY_SAMPLE_MARGIN * 50_000


class TestMaxRowsUnderShare:
    def test_inverse_of_expected_share(self):
        capacity = 12_000_000
        rows = max_rows_under_share(capacity, 0.2)
        assert expected_duplicate_share(rows, capacity) <= 0.2
        assert expected_duplicate_share(rows + rows // 100, capacity) > 0.2

    def test_gate_of_one_never_limits(self):
        assert max_rows_under_share(1_000, 1.0) is None

    def test_unbounded_capacity_never_limits(self):
        assert max_rows_under_share(None, 0.2) is None
