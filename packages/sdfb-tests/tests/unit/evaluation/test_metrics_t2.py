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
