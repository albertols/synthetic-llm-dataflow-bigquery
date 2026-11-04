#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""DirectRunner tests for the §12 validation_runs subgraph + BLOCKER gate."""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=arguments-renamed,unused-argument

from __future__ import annotations

import json
import logging
from typing import ClassVar

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import (
    PipelineConfig,
    SkippedTable,
    TableSpec,
    _BlockerGateDoFn,
    build_pipeline,
    build_relational_pipeline,
)
from sdfb_core.codegen import derive_record_model
from sdfb_core.engines import register_engine
from sdfb_core.engines.base import GenerationEngine
from sdfb_core.record import LaunchRecord
from sdfb_core.validation import (
    STATUS_FAILED_BLOCKER,
    STATUS_PASSED,
    Thresholds,
    preflight_failed_summary,
)
from sdfb_tests.fakes import FakeModelClient  # also registers MinimalEngine as "minimal"


class _PartialFailureEngine(GenerationEngine):
  """Test-only engine: batches whose derived seed is in ``FAIL_SEEDS``
    raise (an uncaught engine crash, routed to the ``engine_failure`` DLQ
    tag by `GenerateRecordsDoFn`); every other batch emits ``n`` rows with
    a unique ``customer_id`` per row so full-row uniqueness never masks the
    valid count.

    With ``PipelineConfig.seed=0`` the derived per-batch seed equals the
    batch_id (``base_seed + batch_id``, see `GenerateRecordsDoFn.process`),
    so ``FAIL_SEEDS`` selects batches deterministically regardless of
    DirectRunner execution order.
    """

  name = "partial_failure"
  FAIL_SEEDS: ClassVar[set[int]] = {0, 1}

  def setup(self, model_client, ctx):
    self._ctx = ctx
    self._record_model = derive_record_model(ctx.table_schema)

  def generate_batch(self, n, cfg):
    if cfg.seed in self.FAIL_SEEDS:
      raise RuntimeError("synthetic engine failure")
    template = self._ctx.reference_rows[0]
    for i in range(n):
      row = dict(template)
      row["customer_id"] = cfg.seed * 1000 + i
      yield self._record_model.model_validate(row)

  def teardown(self):
    self._ctx = None
    self._record_model = None


register_engine("partial_failure", _PartialFailureEngine)


class _WriteResultLike:
  """Minimal stand-in for `apache_beam.io.gcp.bigquery.WriteResult` —
    only the attribute `build_pipeline`'s gate wiring probes for."""

  def __init__(self, destination_load_jobid_pairs):
    self.destination_load_jobid_pairs = destination_load_jobid_pairs


class _FakeBQFileLoadsSink(beam.PTransform):
  """Test double for `WriteToBigQuery(method=FILE_LOADS)`: writes the
    summary row through (identity `Map`, result discarded) and returns an
    object exposing `.destination_load_jobid_pairs` as a real PCollection —
    exactly the shape `build_pipeline` probes via
    ``getattr(write_result, "destination_load_jobid_pairs", None)`` to
    decide whether to wire the `AsIter` ordering edge in front of the
    BLOCKER gate (see pipeline.py ~line 232 / `_BlockerGateDoFn`).
    """

  def expand(self, pcoll):  # type: ignore[override]
    _ = pcoll | "FakeBQWrite" >> beam.Map(lambda row: row)
    load_jobs = pcoll.pipeline | "FakeLoadJobIds" >> beam.Create(
        [("proj.ds.validation_runs", "job-1")])
    return _WriteResultLike(load_jobs)


def _read_jsonl(directory):
  out = []
  for f in sorted(directory.glob("*.jsonl")):
    for line in f.read_text().splitlines():
      if line.strip():
        out.append(json.loads(line))
  return out


@pytest.mark.integration
def test_validation_run_row_written(tmp_path, customers_schema,
                                    customers_reference):
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
  with pytest.raises(Exception), beam.Pipeline(  # noqa: B017
      options=options) as p:
    _ = p | beam.Create([failed_row]) | beam.ParDo(_BlockerGateDoFn())


@pytest.mark.integration
def test_blocker_gate_waits_on_bq_write_result(tmp_path, customers_schema,
                                               customers_reference):
  """A WriteResult-shaped `validation_runs_sink` (the real FILE_LOADS
    shape) wires the AsIter ordering edge and the run still completes
    cleanly when no blocker trips — proving the wiring is valid Beam graph,
    not just that the PDone-shaped fallback (`WriteToJsonLines`) works."""
  landing, dlq = (tmp_path / d for d in ("landing", "dlq"))
  for d in (landing, dlq):
    d.mkdir()

  config = PipelineConfig(
      table_schema=customers_schema,
      engine_name="minimal",
      model_client=FakeModelClient(reference_pool=customers_reference),
      num_rows=20,
      batch_size=5,
      seed=42,
      run_id="vr-write-result-pass",
      reference_table="proj.ds.src",
      landing_table="proj.ds.landing",
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
  )

  options = PipelineOptions(["--runner=DirectRunner"])
  with beam.Pipeline(options=options) as p:
    result = build_pipeline(
        p,
        reference_rows=customers_reference,
        config=config,
        landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
        dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
        validation_runs_sink=_FakeBQFileLoadsSink(),
    )
  assert result["run_id"] == "vr-write-result-pass"


@pytest.mark.integration
def test_blocker_gate_still_raises_with_write_result_sink(
    tmp_path, customers_schema, customers_reference):
  """Regression guard for the AsIter wiring itself: a WriteResult-shaped
    sink must not swallow or delay the gate's raise — the job still fails
    on a blocker breach (2026-07-20 b2 E2E: the gate must fail the JOB)."""
  landing, dlq = (tmp_path / d for d in ("landing", "dlq"))
  for d in (landing, dlq):
    d.mkdir()

  config = PipelineConfig(
      table_schema=customers_schema,
      engine_name="partial_failure",
      model_client=FakeModelClient(reference_pool=customers_reference),
      num_rows=64,
      batch_size=16,
      seed=0,
      run_id="vr-write-result-blocker",
      reference_table="proj.ds.src",
      landing_table="proj.ds.landing",
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
  )

  options = PipelineOptions(["--runner=DirectRunner"])
  # DirectRunner wraps the DoFn raise, so match broadly (B017).
  with pytest.raises(Exception), beam.Pipeline(  # noqa: B017
      options=options) as p:
    build_pipeline(
        p,
        reference_rows=customers_reference,
        config=config,
        landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
        dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
        validation_runs_sink=_FakeBQFileLoadsSink(),
    )


@pytest.mark.integration
def test_engine_failure_envelopes_weighted_by_lost_batch_size(
    tmp_path, customers_schema, customers_reference):
  """Regression: a crashed batch loses `n` rows, not one DLQ envelope.

    2 failed batches of 16 (`engine_failure`, batch_size=16) + 2 successful
    batches of 16 valid rows = 64 rows requested. Weighted correctly this is
    blocker_count=32 / total=64 = observed 0.5, which trips the dev
    threshold of 0.20. Counting envelopes 1-for-1 (the pre-fix behavior)
    would score observed=2/64≈0.03 and wrongly PASS.
    """
  landing, dlq, vr = (tmp_path / d for d in ("landing", "dlq", "vr"))
  for d in (landing, dlq, vr):
    d.mkdir()

  config = PipelineConfig(
      table_schema=customers_schema,
      engine_name="partial_failure",
      model_client=FakeModelClient(reference_pool=customers_reference),
      num_rows=64,
      batch_size=16,
      seed=0,
      run_id="vr-weighted-engine-failure",
      reference_table="proj.ds.src",
      landing_table="proj.ds.landing",
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
      # Assert on the summary arithmetic in isolation from the gate raise
      # (covered separately by test_blocker_gate_fails_pipeline).
      fail_on_blocker=False,
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
  assert row["valid_count"] == 32
  assert row["dlq_by_rule"] and json.loads(row["dlq_by_rule"]) == {
      "engine_failure": 32
  }
  assert row["blocker_count"] == 32
  assert row["dlq_count"] == 32
  assert row["observed_blocker_ratio"] == pytest.approx(0.5)
  assert row["status"] == STATUS_FAILED_BLOCKER


def _failing_config(schema, reference, **overrides):
  return PipelineConfig(
      table_schema=schema,
      engine_name="partial_failure",
      model_client=FakeModelClient(reference_pool=reference),
      num_rows=64,
      batch_size=16,
      seed=0,
      run_id="vr-verdict-only",
      reference_table="proj.ds.src",
      landing_table="proj.ds.landing",
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
      **overrides,
  )


def _run_single(tmp_path, config, reference):
  landing, dlq, vr = (tmp_path / d for d in ("landing", "dlq", "vr"))
  for d in (landing, dlq, vr):
    d.mkdir()
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_pipeline(
        p,
        reference_rows=reference,
        config=config,
        landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
        dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
        validation_runs_sink=WriteToJsonLines(str(vr / "v"), num_shards=1),
    )
  return vr


@pytest.mark.integration
def test_the_gate_records_instead_of_raising_when_told(caplog):
  failed_row = {
      "status": "FAILED_BLOCKER",
      "run_id": "r1-b00-00-T_A",
      "blocker_count": 5,
      "observed_blocker_ratio": 0.5,
      "blocker_failure_ratio": 0.05,
      "env": "dev",
      "landing_table": "p.land.T_A",
  }
  with caplog.at_level(logging.ERROR), beam.Pipeline(
      options=PipelineOptions(["--runner=DirectRunner"])) as p:
    _ = p | beam.Create([failed_row]) | beam.ParDo(
        _BlockerGateDoFn(fail_job=False))
  line = next(r.message
              for r in caplog.records
              if "name=blocker_gate_recorded" in r.message)
  assert "table=T_A" in line and "run_id=r1-b00-00-T_A" in line
  assert "blocker_count=5" in line


@pytest.mark.integration
def test_a_whole_model_batch_lands_a_failed_blocker_row_and_the_pipeline_completes(
    tmp_path, customers_schema, customers_reference, caplog):
  config = _failing_config(
      customers_schema,
      customers_reference,
      fail_on_blocker=True,
      blocker_verdict_only=True)
  with caplog.at_level(logging.ERROR):
    vr = _run_single(tmp_path, config, customers_reference)
  row = _read_jsonl(vr)[0]
  assert row["status"] == STATUS_FAILED_BLOCKER
  assert row["status_reason"].startswith("BLOCKER failures")
  assert any("name=blocker_gate_recorded" in r.message for r in caplog.records)


@pytest.mark.integration
def test_an_explicit_launch_still_fails_the_job_on_a_blocker(
    tmp_path, customers_schema, customers_reference):
  config = _failing_config(
      customers_schema, customers_reference, fail_on_blocker=True)
  # DirectRunner wraps the DoFn raise, so match broadly.
  with pytest.raises(  # noqa: B017
      Exception):
    _run_single(tmp_path, config, customers_reference)


@pytest.mark.integration
def test_the_reason_column_is_dropped_when_the_table_lacks_it(
    tmp_path, customers_schema, customers_reference):
  config = PipelineConfig(
      table_schema=customers_schema,
      engine_name="minimal",
      model_client=FakeModelClient(reference_pool=customers_reference),
      num_rows=20,
      batch_size=5,
      seed=42,
      run_id="vr-no-reason",
      reference_table="proj.ds.src",
      landing_table="proj.ds.landing",
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
      write_status_reason=False,
  )
  vr = _run_single(tmp_path, config, customers_reference)
  assert "status_reason" not in _read_jsonl(vr)[0]


@pytest.mark.integration
def test_a_skipped_table_row_is_written_by_the_job_with_the_record(
    tmp_path, customers_schema, customers_reference):
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
      run_id="vr-spec-a",
      reference_table="p.src.T_A",
      landing_table="p.land.T_A",
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
  )
  spec_for_t_a = TableSpec(
      config=config,
      reference_rows=customers_reference,
      landing_sink=WriteToJsonLines(str(landing / "l"), num_shards=1),
      dlq_sink=WriteToJsonLines(str(dlq / "d"), num_shards=1),
      validation_runs_sink=WriteToJsonLines(str(vr / "a"), num_shards=1),
  )
  row = preflight_failed_summary(
      run_id="r1-b00-01-T_B",
      reference_table="p.src.T_B",
      landing_table="p.land.T_B",
      engine="b1_rag",
      model_uri="gs://b/m/",
      env="dev",
      num_rows_requested=10,
      reason="[preflight P1] no rows").to_bq_row()
  record = LaunchRecord(
      columns={
          "launch_run_id": "r1",
          "job_name": None
      },
      launcher_version="v",
      read_job_id=False)
  skipped = SkippedTable(
      landing_table="p.land.T_B",
      row=row,
      validation_runs_sink=WriteToJsonLines(str(vr / "s"), num_shards=1))
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_relational_pipeline(
        p, [spec_for_t_a], skipped=[skipped], record=record)
  rows = {r["landing_table"]: r for r in _read_jsonl(vr)}
  assert rows["p.land.T_B"]["status"] == "FAILED_PREFLIGHT"
  assert rows["p.land.T_B"]["status_reason"] == "[preflight P1] no rows"
  assert rows["p.land.T_B"]["launch_run_id"] == "r1"
  assert rows["p.land.T_A"]["status"] == "PASSED"
