"""Evaluation branch wiring — DirectRunner end-to-end with a fake engine
client (WS3 §2/§5a wired into `build_pipeline`).

Fixture/helper conventions mirrored from `test_pipeline.py` (local
`_config(schema, **overrides)` building a `PipelineConfig` with the
`minimal` engine + `FakeModelClient`) and `test_validation_runs.py`
(`WriteToJsonLines` + `tmp_path` sinks, `_read_jsonl` readback, the
`with pytest.raises(...), beam.Pipeline(...) as p:` gate-trip shape) —
neither file exposes a `_make_config`/`_memory_sinks`/`_reference_rows`
helper by those names, so this file defines its own small equivalents
rather than importing private names from either.
"""

from __future__ import annotations

import json

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import PipelineConfig, build_pipeline
from sdfb_core.contracts import TableSchema
from sdfb_core.evaluation.gate import MemorizationThresholdExceeded
from sdfb_tests import fakes  # noqa: F401  registers "minimal" engine
from sdfb_tests.fakes import FakeModelClient


def _config(schema, **overrides):
    defaults = dict(
        table_schema=schema,
        engine_name="minimal",
        model_client=FakeModelClient(reference_pool=[{}]),
        num_rows=5,
        batch_size=5,
        run_id="test-eval-branch",
        # The generic (non-gate-trip) tests below don't care whether the
        # memorization gate trips on echoed reference rows — only the
        # dedicated gate-trip test asserts on that. Keep the job from
        # failing on an incidental trip elsewhere.
        fail_on_blocker=False,
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def _read_jsonl(directory):
    out = []
    for f in sorted(directory.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def test_eval_branch_absent_without_flag(tmp_path, customers_schema, customers_reference):
    """enable_evaluation=False ⇒ no branch even with a sink supplied."""
    landing, dlq, vdh = (tmp_path / d for d in ("landing", "dlq", "vdh"))
    for d in (landing, dlq, vdh):
        d.mkdir()
    config = _config(
        customers_schema,
        model_client=FakeModelClient(reference_pool=customers_reference),
        enable_evaluation=False,
        execution_id="exec-1",
    )
    options = PipelineOptions(["--runner=DirectRunner"])
    with beam.Pipeline(options=options) as p:
        result = build_pipeline(
            p,
            reference_rows=customers_reference,
            config=config,
            landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
            dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
            validation_data_history_sink=WriteToJsonLines(str(vdh / "v"), num_shards=1),
        )
    assert "validation_data_history" not in result
    assert not _read_jsonl(vdh)


def test_eval_branch_absent_without_sink(tmp_path, customers_schema, customers_reference):
    """enable_evaluation=True but sink=None ⇒ no branch (both are required)."""
    landing, dlq = (tmp_path / d for d in ("landing", "dlq"))
    for d in (landing, dlq):
        d.mkdir()
    config = _config(
        customers_schema,
        model_client=FakeModelClient(reference_pool=customers_reference),
        enable_evaluation=True,
        execution_id="exec-1",
    )
    options = PipelineOptions(["--runner=DirectRunner"])
    with beam.Pipeline(options=options) as p:
        result = build_pipeline(
            p,
            reference_rows=customers_reference,
            config=config,
            landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
            dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
            validation_data_history_sink=None,
        )
    assert "validation_data_history" not in result


def test_eval_branch_emits_one_row_with_execution_id(
    tmp_path, customers_schema, customers_reference
):
    """enable_evaluation=True + a sink ⇒ exactly one validation_data_history
    row, carrying execution_id and a populated memorization_gate."""
    landing, dlq, vdh = (tmp_path / d for d in ("landing", "dlq", "vdh"))
    for d in (landing, dlq, vdh):
        d.mkdir()
    config = _config(
        customers_schema,
        model_client=FakeModelClient(reference_pool=customers_reference),
        num_rows=20,
        batch_size=5,
        enable_evaluation=True,
        execution_id="exec-1",
    )
    options = PipelineOptions(["--runner=DirectRunner"])
    with beam.Pipeline(options=options) as p:
        result = build_pipeline(
            p,
            reference_rows=customers_reference,
            config=config,
            landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
            dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
            validation_data_history_sink=WriteToJsonLines(str(vdh / "v"), num_shards=1),
        )
    assert "validation_data_history" in result

    rows = _read_jsonl(vdh)
    assert len(rows) == 1
    row = rows[0]
    assert row["execution_id"] == "exec-1"
    assert row["run_id"] == "test-eval-branch"
    assert json.loads(row["raw_metrics_json"])["memorization_gate"] is not None


# ---------------------------------------------------------------------------
# Gate-trip: FakeModelClient's "echo" mode returns pool rows verbatim, and
# MinimalEngine passes them straight through model_validate with no identity
# synthesis (identity_columns=()) — so with a schema of plain INT64/STRING
# columns (no TIMESTAMP/NUMERIC coercion to worry about) the landed rows are
# byte-identical to the eval branch's own reference sample. That reliably
# trips the always-BLOCKER memorization gate (§5a), so this covers the
# fail_on_blocker=True → MemorizationThresholdExceeded path end-to-end,
# on top of the gate's own unit coverage in Tasks 7/9.
# ---------------------------------------------------------------------------

_GATE_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "p.d.gate_trip"},
        "columns": [
            {"name": "id", "type": "INT64"},
            {"name": "label", "type": "STRING"},
        ],
    }
)
_GATE_POOL = [{"id": i, "label": f"label-{i}"} for i in range(8)]


def test_gate_trips_and_fails_the_job_on_verbatim_copies(tmp_path):
    landing, dlq, vdh = (tmp_path / d for d in ("landing", "dlq", "vdh"))
    for d in (landing, dlq, vdh):
        d.mkdir()
    config = PipelineConfig(
        table_schema=_GATE_SCHEMA,
        engine_name="minimal",
        model_client=FakeModelClient(reference_pool=_GATE_POOL),
        num_rows=16,
        batch_size=16,
        seed=7,
        run_id="gate-trip-test",
        enable_evaluation=True,
        execution_id="gate-trip-exec",
        fail_on_blocker=True,
    )
    options = PipelineOptions(["--runner=DirectRunner"])
    with pytest.raises(Exception) as exc_info, beam.Pipeline(options=options) as p:
        build_pipeline(
            p,
            reference_rows=_GATE_POOL,
            config=config,
            landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
            dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
            validation_data_history_sink=WriteToJsonLines(str(vdh / "v"), num_shards=1),
        )

    cause = exc_info.value
    while cause.__cause__ is not None:
        cause = cause.__cause__
    assert isinstance(cause, MemorizationThresholdExceeded) or "memorization" in str(
        exc_info.value
    ).lower()
    assert "identical_match_rate" in str(cause)
