"""EvaluationDoFn — one row always; metrics populated; skipped row on empty sides."""

import json
from unittest.mock import MagicMock

from sdfb_beam.dofns.evaluation import EvaluationDoFn, StratifiedReservoirFn
from sdfb_core.contracts import TableSchema
from sdfb_core.evaluation.profile import StratificationPlan
from sdfb_core.validation import Thresholds

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
    first = next(iter(_dofn().process(None, _rows(60), _rows(60, shift=1.0))))
    fake_client = MagicMock()
    fake_client.query.return_value.result.return_value = iter(
        [{"raw_metrics_json": first["raw_metrics_json"]}]
    )
    dofn = _dofn(
        history_table="p.q.validation_data_history",
        validation_runs_table="p.q.validation_runs",
        bq_client_factory=lambda: fake_client,
    )
    row = next(iter(dofn.process(None, _rows(60), _rows(60, shift=500.0))))
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
    row = next(iter(dofn.process(None, _rows(30), _rows(30, shift=9.0))))
    assert row["max_psi"] is None


def test_reservoir_combinefn_bounds_output():
    fn = StratifiedReservoirFn(StratificationPlan(column=None, values=()), "r1", cap=5, overall_cap=5)
    acc = fn.create_accumulator()
    for row in _rows(40):
        acc = fn.add_input(acc, row)
    merged = fn.merge_accumulators([acc, fn.create_accumulator()])
    assert len(fn.extract_output(merged)) == 5


def test_nan_metrics_sanitized_to_null_in_row_and_valid_json(monkeypatch):
    """Controller amendment: sdmetrics can emit float NaN properties (e.g.
    Column Pair Trends on a constant column). json.dumps would serialize
    NaN as the bare token `NaN`, which is invalid JSON for BigQuery's JSON
    column type — sanitize NaN/Inf -> None before dumping."""
    from sdfb_beam.dofns import evaluation as evaluation_module

    def _fake_quality(real_df, synth_df, metadata):
        return {"score": float("nan"), "properties": {"p": float("nan")}}

    monkeypatch.setattr(
        evaluation_module.metrics_t2, "sdmetrics_quality", _fake_quality
    )
    monkeypatch.setattr(
        evaluation_module.metrics_t2,
        "sdmetrics_diagnostic",
        lambda *a, **k: {"score": 1.0, "properties": {}},
    )

    rows = list(_dofn().process(None, _rows(60), _rows(60, shift=1000.0)))
    row = rows[0]

    assert "NaN" not in row["raw_metrics_json"]
    parsed = json.loads(row["raw_metrics_json"])  # must not raise
    assert parsed["sdmetrics"]["score"] is None
    assert parsed["sdmetrics"]["properties"]["p"] is None
    assert row["fidelity_overall_score"] is None
