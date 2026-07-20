"""WS1: temporal conversion + novel-range sampling for b2_library.

The 2026-07-20 E2E run landed 8 high-cardinality temporal columns at
copy_ratio=1.0 because b2 resampled the observed value table verbatim.
These tests pin the replacement: epoch round-trips per value type and a
seeded uniform sampler that stays inside the observed [min, max].
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
from sdfb_beam.handlers.fake_client import FakeModelClient
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationConfig, GenerationContext
from sdfb_core.engines.b2_library import B2LibraryEngine
from sdfb_core.engines.b2_library.backends import (
    EmpiricalBackend,
    SdgxBackend,
    _samplable_profiles,
)
from sdfb_core.engines.b2_library.fidelity import (
    ColumnKind,
    ColumnProfile,
    _representative,
    enforce_value,
    profile_table,
)
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


def test_empirical_backend_samples_novel_in_range_temporal():
    schema = TableSchema.model_validate(_SCHEMA)
    rows = _reference_rows()
    profiles = profile_table(schema, rows)
    backend = EmpiricalBackend()
    backend.fit(rows, profiles)
    out = backend.sample_columns(500, np.random.default_rng(11))

    observed_ts = {r["ts"] for r in rows}
    sampled_ts = [v for v in out["ts"] if v is not None]
    assert all(isinstance(v, datetime) and v.tzinfo is not None for v in sampled_ts)
    lo, hi = min(observed_ts), max(observed_ts)
    assert all(lo <= v <= hi for v in sampled_ts)
    # Novel-range jitter, not the verbatim observed table (60 distinct
    # observed instants in a 59-hour continuous range → collisions ≈ 0).
    copy_ratio = sum(v in observed_ts for v in sampled_ts) / len(sampled_ts)
    assert copy_ratio < 0.3

    sampled_d = [v for v in out["d_str"] if v is not None]
    assert all(datetime.strptime(v, "%Y-%m-%d") for v in sampled_d)
    # 30 observed days inside a 29-day span is a saturated keyspace, so
    # membership is unavoidable — the defect was FREQUENCY copying; assert
    # the draw is range-uniform, not the empirical table (distinct spread).
    assert len(set(sampled_d)) >= 20

    # Below-cap timestamp column stays empirical (in observed support).
    observed_low = {r["ts_low"] for r in rows}
    assert set(v for v in out["ts_low"] if v is not None) <= observed_low


def test_sdgx_reference_frame_excludes_temporal_columns():
    schema = TableSchema.model_validate(_SCHEMA)
    rows = _reference_rows()
    profiles = profile_table(schema, rows)
    backend = SdgxBackend()
    backend._profiles = _samplable_profiles(profiles)
    frame = backend._reference_frame(rows, pd)
    assert "ts" not in frame.columns and "d_str" not in frame.columns
    assert "enum_col" in frame.columns and "event_id" in frame.columns


def test_sdgx_backend_temporal_branch_injects_nulls():
    schema = TableSchema.model_validate(_SCHEMA)
    rows = _reference_rows()
    profiles = profile_table(schema, rows)
    # Nullable temporal with a 30% observed null rate, hand-tuned.
    profiles = dict(profiles)
    p = profiles["ts"]
    profiles["ts"] = ColumnProfile(
        name=p.name, bq_type=p.bq_type, kind=p.kind, nullable=True,
        null_fraction=0.3, minimum=p.minimum, maximum=p.maximum,
        temporal_value_type=p.temporal_value_type, temporal_format=p.temporal_format,
    )
    backend = SdgxBackend()
    backend._profiles = _samplable_profiles(profiles)

    class _StubSynth:
        def sample(self, n):
            return pd.DataFrame({"event_id": [1] * n})

    backend._synthesizer = _StubSynth()
    out = backend.sample_columns(400, np.random.default_rng(5))
    null_rate = sum(v is None for v in out["ts"]) / 400
    assert 0.2 < null_rate < 0.4  # nulls reinjected, not dropped
    assert any(v is not None for v in out["ts"])  # and real values sampled


# ---------------------------------------------------------------------------
# Fidelity enforcement + end-to-end integration tests
# ---------------------------------------------------------------------------


def test_enforce_value_passes_temporal_through_and_representative_renders():
    schema = TableSchema.model_validate(_SCHEMA)
    profiles = profile_table(schema, _reference_rows())
    p = profiles["d_str"]
    assert enforce_value(p, "2024-06-15") == "2024-06-15"
    rep = _representative(p)
    assert datetime.strptime(rep, "%Y-%m-%d")  # renders minimum, parseable


def test_engine_end_to_end_yields_novel_temporal_rows():
    rows = _reference_rows()
    ctx = GenerationContext(
        table_schema=TableSchema.model_validate(_SCHEMA),
        reference_rows=rows,
        reference_digest="temporal-digest",
        pipeline_run_id="ws1-temporal-run",
    )
    engine = B2LibraryEngine(use_sdgx=False)
    engine.setup(FakeModelClient(responses=rows), ctx)
    records = list(engine.generate_batch(50, GenerationConfig(seed=3, batch_size=50)))
    assert len(records) == 50  # no rows dropped by record-model validation

    dumped = [r.model_dump(mode="python") for r in records]
    observed_ts = {r["ts"] for r in rows}
    sampled_ts = [d["ts"] for d in dumped]
    copy_ratio = sum(v in observed_ts for v in sampled_ts) / len(sampled_ts)
    assert copy_ratio < 0.3  # the 2026-07-20 defect was 1.0
    assert all(datetime.strptime(d["d_str"], "%Y-%m-%d") for d in dumped)
    assert all(isinstance(d["code"], str) and d["code"] for d in dumped)  # hook ran
