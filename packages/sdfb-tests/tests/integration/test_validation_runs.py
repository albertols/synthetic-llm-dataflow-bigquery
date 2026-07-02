"""DirectRunner tests for the §12 validation_runs subgraph + BLOCKER gate."""

from __future__ import annotations

import json

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import PipelineConfig, _BlockerGateDoFn, build_pipeline
from sdfb_core.validation import STATUS_PASSED, Thresholds
from sdfb_tests import fakes  # noqa: F401 — registers MinimalEngine under "minimal"
from sdfb_tests.fakes import FakeModelClient


def _read_jsonl(directory):
    out = []
    for f in sorted(directory.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


@pytest.mark.integration
def test_validation_run_row_written(tmp_path, customers_schema, customers_reference):
    """A single validation_runs row lands with status PASSED on a clean run."""
    landing, dlq, vr = (tmp_path / d for d in ("landing", "dlq", "vr"))
    for d in (landing, dlq, vr):
        d.mkdir()

    config = PipelineConfig(
        table_schema=customers_schema,
        engine_name="minimal",
        model_client=FakeModelClient(reference_pool=customers_reference),
        num_rows=20,
        batch_size=5,
        seed=42,
        run_id="vr-test",
        reference_table="proj.ds.src",
        landing_table="proj.ds.landing",
        thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
    )

    options = PipelineOptions(["--runner=DirectRunner"])
    with beam.Pipeline(options=options) as p:
        build_pipeline(
            p,
            reference_rows=customers_reference,
            config=config,
            landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
            dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
            validation_runs_sink=WriteToJsonLines(str(vr / "v"), num_shards=1),
        )

    rows = _read_jsonl(vr)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "vr-test"
    assert row["status"] == STATUS_PASSED
    assert row["valid_count"] > 0
    assert row["engine"] == "minimal"
    assert row["reference_table"] == "proj.ds.src"
    assert row["env"] == "dev"
    assert len(row["reference_digest"]) == 64
    assert isinstance(json.loads(row["dlq_by_rule"]), dict)


@pytest.mark.integration
def test_blocker_gate_fails_pipeline():
    """The gate DoFn raises (failing the job) on a FAILED_BLOCKER summary row."""
    failed_row = {
        "status": "FAILED_BLOCKER",
        "run_id": "x",
        "blocker_count": 5,
        "observed_blocker_ratio": 0.5,
        "blocker_failure_ratio": 0.05,
        "env": "dev",
    }
    options = PipelineOptions(["--runner=DirectRunner"])
    # DirectRunner wraps the DoFn raise, so match broadly (B017).
    with pytest.raises(Exception), beam.Pipeline(options=options) as p:  # noqa: B017
        _ = p | beam.Create([failed_row]) | beam.ParDo(_BlockerGateDoFn())
