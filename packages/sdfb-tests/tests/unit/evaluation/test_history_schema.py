"""Drift guard: schema file fields == EvaluationDoFn row keys, exactly."""

import json
from pathlib import Path

from sdfb_beam.dofns.evaluation import EvaluationDoFn
from sdfb_core.contracts import TableSchema

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[5]
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
