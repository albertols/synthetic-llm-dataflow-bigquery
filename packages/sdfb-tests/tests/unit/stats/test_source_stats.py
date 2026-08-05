"""source_table_stats pure profiler (Task 4)."""

import json
from pathlib import Path

from sdfb_core.contracts.relational import parse_relational_contract
from sdfb_core.contracts.schema import TableSchema
from sdfb_core.stats.source_stats import (
    PROFILER_VERSION,
    profile_source_table,
    stats_rows,
)

_SCHEMA = {
    "table_info": {"table_id": "demo_project.demo_dataset.t"},
    "schema": [
        {"name": "ZEROS", "type": "INT64", "mode": "REQUIRED"},
        {"name": "NOTES", "type": "STRING", "mode": "NULLABLE"},
        {"name": "STATUS", "type": "STRING", "mode": "REQUIRED"},
        {"name": "LOADED", "type": "DATE", "mode": "REQUIRED"},
        {"name": "PK_COL", "type": "STRING", "mode": "REQUIRED"},
        {"name": "AMOUNT", "type": "FLOAT64", "mode": "NULLABLE"},
    ],
}

_CONTRACT = parse_relational_contract(
    '{"sdfb": 1, "pk": ["PK_COL"], '
    '"fk": [{"cols": ["STATUS"], "ref": "d.parent", "ref_cols": ["S"]}], '
    '"identity": ["PK_COL"]}'
)


def _rows(n: int = 100) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "ZEROS": 0,
                "NOTES": f"REF-{i:05d} SETTLED" if i < 10 else "",
                "STATUS": "OPEN" if i % 2 else "CLOSED",
                "LOADED": "2026-01-15",
                "PK_COL": f"PK{i:06d}",
                "AMOUNT": None if i < 50 else float(i),
            }
        )
    return rows


def _table_schema() -> TableSchema:
    return TableSchema.model_validate(_SCHEMA)


def test_per_column_stats_shape():
    stats = profile_source_table(_table_schema(), _rows(), contract=_CONTRACT)
    assert set(stats) == {"ZEROS", "NOTES", "STATUS", "LOADED", "PK_COL", "AMOUNT"}
    z = stats["ZEROS"]
    assert z["zero_fraction"] == 1.0
    assert z["is_constant"] is True
    assert z["distinct"] == 1
    n = stats["NOTES"]
    assert n["empty_fraction"] == 0.9
    assert n["distinct"] == 10  # empties excluded from distinct
    assert n["shape_mix"], "freetext-ish column must carry a shape mix"
    assert n["mean_len"] > 0
    a = stats["AMOUNT"]
    assert a["null_fraction"] == 0.5
    assert a["min"] == 50.0 and a["max"] == 99.0
    assert stats["PK_COL"]["is_pk"] is True
    assert stats["PK_COL"]["identity_col"] is True
    assert stats["STATUS"]["is_fk"] is True
    assert stats["STATUS"]["distinct"] == 2


def test_temporal_day_granularity_flag():
    stats = profile_source_table(_table_schema(), _rows())
    assert stats["LOADED"]["temporal_day_granularity"] is True


def test_generation_plan_labels_merged():
    stats = profile_source_table(
        _table_schema(), _rows(), generation_plan={"NOTES": "freetext_llm_pool"}
    )
    assert stats["NOTES"]["generation_plan"] == "freetext_llm_pool"
    assert stats["ZEROS"]["generation_plan"] == ""


def test_stats_rows_flatten():
    stats = profile_source_table(_table_schema(), _rows(), contract=_CONTRACT)
    rows = stats_rows("p.d.t", "digest123", "run-1", stats)
    assert len(rows) == len(stats)
    by_col = {r["column"]: r for r in rows}
    assert by_col["PK_COL"]["is_pk"] is True
    assert by_col["NOTES"]["empty_fraction"] == 0.9
    payload = json.loads(by_col["NOTES"]["stats"])
    assert payload["distinct"] == 10
    assert all(r["reference_digest"] == "digest123" for r in rows)
    assert all("computed_at" in r for r in rows)


def test_stats_rows_carry_provenance_columns():
    """sample_rows/stats_tier/profiler_version are headline BQ columns:
    distinct_ratio is not comparable across runs without sample_rows, and
    the versioned skip key needs profiler_version broken out (ADR 0022)."""
    stats = profile_source_table(_table_schema(), _rows())
    rows = stats_rows("p.d.t", "digest123", "run-1", stats)
    by_col = {r["column"]: r for r in rows}
    notes = by_col["NOTES"]
    assert notes["sample_rows"] == 100
    assert notes["stats_tier"] == "sample"
    assert notes["profiler_version"] == PROFILER_VERSION
    payload = json.loads(notes["stats"])
    assert payload["stats_tier"] == "sample"
    assert payload["profiler_version"] == PROFILER_VERSION


def test_schema_file_matches_stats_rows_columns():
    """The committed BQ schema and stats_rows must never drift — a missing
    column fails the write_rows load job mid-launch (step 11 rationale)."""
    schema_path = (
        Path(__file__).resolve().parents[5]
        / "config/bq_schema/synthetic_rag/source_table_stats.schema.json"
    )
    schema_cols = {f["name"] for f in json.loads(schema_path.read_text())}
    stats = profile_source_table(_table_schema(), _rows())
    row = stats_rows("p.d.t", "d", "r", stats)[0]
    assert set(row) == schema_cols
