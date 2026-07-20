"""WS1: temporal conversion + novel-range sampling for b2_library.

The 2026-07-20 E2E run landed 8 high-cardinality temporal columns at
copy_ratio=1.0 because b2 resampled the observed value table verbatim.
These tests pin the replacement: epoch round-trips per value type and a
seeded uniform sampler that stays inside the observed [min, max].
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import numpy as np
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b2_library.fidelity import ColumnKind, profile_table
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


_SCHEMA = {
    "table_info": {"table_id": "demo.events"},
    "schema": [
        {"name": "event_id", "type": "INT64", "mode": "REQUIRED"},
        {"name": "ts", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "ts_low", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "d_str", "type": "STRING", "mode": "REQUIRED"},
        {"name": "code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "enum_col", "type": "STRING", "mode": "REQUIRED", "max_length": 8},
    ],
    "primary_keys": ["event_id"],
}


def _reference_rows(n: int = 100) -> list[dict]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        {
            "event_id": i,
            # 60 distinct tz-aware timestamps (> cap 20) → TEMPORAL
            "ts": base + timedelta(hours=i % 60),
            # 10 distinct timestamps (≤ cap) → CATEGORICAL (enum-in-disguise)
            "ts_low": base + timedelta(days=i % 10),
            # 30 distinct date strings, ratio 0.3 (< 0.9) → TEMPORAL via shape
            "d_str": f"2024-06-{(i % 30) + 1:02d}",
            # 30 distinct short non-date strings, ratio 0.3 → FREE_TEXT (new cap route)
            "code": f"br {i % 30:03d} x",
            # 5 distinct → CATEGORICAL as before
            "enum_col": ["A", "B", "C", "D", "E"][i % 5],
        }
        for i in range(n)
    ]


def test_classifier_caps_route_families_correctly():
    schema = TableSchema.model_validate(_SCHEMA)
    profiles = profile_table(schema, _reference_rows())
    assert profiles["ts"].kind is ColumnKind.TEMPORAL
    assert profiles["ts"].temporal_value_type == VT_DATETIME_UTC
    assert profiles["ts"].minimum is not None
    assert profiles["ts"].maximum > profiles["ts"].minimum
    assert profiles["ts_low"].kind is ColumnKind.CATEGORICAL
    assert profiles["d_str"].kind is ColumnKind.TEMPORAL
    assert profiles["d_str"].temporal_value_type == VT_STR
    assert profiles["d_str"].temporal_format == "%Y-%m-%d"
    assert profiles["code"].kind is ColumnKind.FREE_TEXT
    assert profiles["enum_col"].kind is ColumnKind.CATEGORICAL


def test_unparseable_high_card_temporal_demotes_to_categorical():
    # TIMESTAMP-typed column whose values are mixed strings the detector
    # rejects → stays CATEGORICAL (in-support) instead of crashing.
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.weird"},
            "schema": [{"name": "w", "type": "TIMESTAMP", "mode": "REQUIRED"}],
            "primary_keys": None,
        }
    )
    rows = [{"w": f"junk-{i}" if i % 2 else f"2024-01-{(i % 28) + 1:02d}"} for i in range(50)]
    profiles = profile_table(schema, rows)
    assert profiles["w"].kind is ColumnKind.CATEGORICAL
