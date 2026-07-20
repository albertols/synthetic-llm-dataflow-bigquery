"""WS1: temporal conversion + novel-range sampling for b2_library.

The 2026-07-20 E2E run landed 8 high-cardinality temporal columns at
copy_ratio=1.0 because b2 resampled the observed value table verbatim.
These tests pin the replacement: epoch round-trips per value type and a
seeded uniform sampler that stays inside the observed [min, max].
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import numpy as np
from sdfb_core.engines.b2_library.temporal import (
    VT_DATE,
    VT_DATETIME,
    VT_DATETIME_UTC,
    VT_STR,
    VT_TIME,
    classify_temporal_values,
    from_epoch,
    sample_temporal,
    to_epoch,
)


def test_classify_date_strings():
    values = [f"2024-03-{d:02d}" for d in range(1, 25)]
    assert classify_temporal_values(values) == (VT_STR, "%Y-%m-%d")


def test_classify_native_types():
    aware = [datetime(2024, 1, 1, 12, 0, tzinfo=UTC), datetime(2024, 6, 1, tzinfo=UTC)]
    naive = [datetime(2024, 1, 1, 12, 0), datetime(2024, 6, 1)]
    dates = [date(2024, 1, 1), date(2024, 6, 1)]
    times = [time(9, 30), time(17, 45)]
    assert classify_temporal_values(aware) == (VT_DATETIME_UTC, None)
    assert classify_temporal_values(naive) == (VT_DATETIME, None)
    assert classify_temporal_values(dates) == (VT_DATE, None)
    assert classify_temporal_values(times) == (VT_TIME, None)


def test_classify_rejects_non_temporal_and_mixed():
    assert classify_temporal_values(["hello", "world"]) is None
    assert classify_temporal_values(["2024-01-01", "not a date"]) is None
    assert classify_temporal_values([datetime(2024, 1, 1), date(2024, 1, 2)]) is None
    assert classify_temporal_values([]) is None


def test_epoch_round_trip_every_value_type():
    cases = [
        (datetime(2024, 3, 5, 6, 7, 8, tzinfo=UTC), VT_DATETIME_UTC, None),
        (datetime(2024, 3, 5, 6, 7, 8), VT_DATETIME, None),
        (date(2024, 3, 5), VT_DATE, None),
        (time(6, 7, 8), VT_TIME, None),
        ("2024-03-05", VT_STR, "%Y-%m-%d"),
        ("2024-03-05 06:07:08", VT_STR, "%Y-%m-%d %H:%M:%S"),
    ]
    for value, vt, fmt in cases:
        assert from_epoch(to_epoch(value, vt, fmt), vt, fmt) == value


def test_sample_temporal_in_range_novel_and_seeded():
    lo = to_epoch("2024-01-01", VT_STR, "%Y-%m-%d")
    hi = to_epoch("2024-12-01", VT_STR, "%Y-%m-%d")
    a = sample_temporal(lo, hi, VT_STR, "%Y-%m-%d", 200, np.random.default_rng(7))
    b = sample_temporal(lo, hi, VT_STR, "%Y-%m-%d", 200, np.random.default_rng(7))
    assert a == b  # seeded determinism
    parsed = [datetime.strptime(v, "%Y-%m-%d") for v in a]
    assert all(datetime(2024, 1, 1) <= p <= datetime(2024, 12, 1) for p in parsed)
    assert len(set(a)) > 20  # novel spread, not a handful of repeats


def test_sample_temporal_degenerate_bounds():
    assert sample_temporal(None, None, VT_STR, "%Y-%m-%d", 3, np.random.default_rng(0)) == [None] * 3
    lo = to_epoch("2024-05-05", VT_STR, "%Y-%m-%d")
    assert sample_temporal(lo, lo, VT_STR, "%Y-%m-%d", 2, np.random.default_rng(0)) == ["2024-05-05"] * 2
