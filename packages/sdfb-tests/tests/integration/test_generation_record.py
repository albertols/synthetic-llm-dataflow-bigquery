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
"""DirectRunner: the summary row carries the generation record when the
launcher hands one over, and is master's 21-column row when it does not.

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam import pipeline as pipeline_mod
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import PipelineConfig, build_pipeline
from sdfb_core.engines.generation_plan import clear_generation_plan_log
from sdfb_core.record import LAUNCHER_COLUMNS, RECORD_COLUMNS, LaunchRecord
from sdfb_core.validation import Thresholds
from sdfb_core.validation.summary import RunSummary
from sdfb_tests.fakes import FakeModelClient
from sdfb_tests.fixtures import load_ddl, load_reference

_VERSION = "0.6.1-test-abcdef0"


@pytest.fixture(autouse=True)
def _fresh_plan_log():
  """The worker's plan once-guard is process-lived; a run here must not
    swallow the plan line a later test (the relationship-card seam test)
    asserts on."""
  clear_generation_plan_log()
  yield
  clear_generation_plan_log()


def _record(*,
            launcher_version: str = _VERSION,
            read_job_id: bool = False) -> LaunchRecord:
  columns = {c: f"value-{c}" for c in LAUNCHER_COLUMNS}
  columns["similarity"] = 0.5
  return LaunchRecord(
      columns=columns,
      launcher_version=launcher_version,
      read_job_id=read_job_id)


def _rows(prefix: Path) -> list[dict]:
  rows = []
  for f in sorted(prefix.parent.glob(prefix.name + "*")):
    rows.extend(json.loads(line) for line in f.read_text().splitlines() if line)
  return rows


def _run(tmp: Path, record: LaunchRecord | None) -> dict[str, list[dict]]:
  tmp.mkdir(parents=True, exist_ok=True)
  schema, reference = load_ddl("customers"), load_reference("customers")
  config = PipelineConfig(
      table_schema=schema,
      engine_name="b1_rag",
      model_client=FakeModelClient(reference_pool=reference),
      num_rows=20,
      batch_size=10,
      seed=7,
      run_id="rec-1",
      reference_table="p.src.customers",
      landing_table="p.land.customers",
      identity_columns=("customer_id",),
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.2),
      fail_on_blocker=False,
      record=record,
  )
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_pipeline(
        p,
        reference_rows=reference,
        config=config,
        landing_sink=WriteToJsonLines(str(tmp / "land"), num_shards=1),
        dlq_sink=WriteToJsonLines(str(tmp / "dlq"), num_shards=1),
        validation_runs_sink=WriteToJsonLines(str(tmp / "vr"), num_shards=1),
    )
  return {name: _rows(tmp / name) for name in ("land", "vr")}


def _sorted(rows):
  return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))


def test_no_record_is_masters_row(tmp_path):
  (row,) = _run(tmp_path, None)["vr"]
  assert set(row) == set(RunSummary.model_fields)


def test_a_record_adds_every_column_and_lands_the_same_rows(
    tmp_path, monkeypatch):
  monkeypatch.setattr(pipeline_mod, "generator_version", lambda: _VERSION)
  plain = _run(tmp_path / "plain", None)
  recorded = _run(tmp_path / "rec", _record())
  (row,) = recorded["vr"]
  assert set(row) == set(RunSummary.model_fields) | set(RECORD_COLUMNS)
  assert row["generator_version"] == _VERSION
  assert row["job_id"] is None  # the launcher said: not a Dataflow run
  assert row["launch_run_id"] == "value-launch_run_id"
  assert _sorted(recorded["land"]) == _sorted(plain["land"])
  (legacy,) = plain["vr"]
  assert {
      k: row[k] for k in legacy if k != "created_at"
  } == {
      k: legacy[k] for k in legacy if k != "created_at"
  }


def test_the_job_id_reaches_the_row_on_a_dataflow_run(tmp_path, monkeypatch):
  monkeypatch.setattr(pipeline_mod, "generator_version", lambda: _VERSION)
  monkeypatch.setattr(pipeline_mod, "read_job_id", lambda:
                      ("2026-10-21_01_02_03-123", "metadata:job_id"))
  (row,) = _run(tmp_path, _record(read_job_id=True))["vr"]
  assert row["job_id"] == "2026-10-21_01_02_03-123"


def test_setup_reads_the_job_id_once_per_instance(monkeypatch):
  """The metadata read is in setup(), never per element (ADR 0044)."""
  calls = []

  def read():
    calls.append(1)
    return "j-1", "metadata:job_id"

  monkeypatch.setattr(pipeline_mod, "generator_version", lambda: _VERSION)
  monkeypatch.setattr(pipeline_mod, "read_job_id", read)
  monkeypatch.setattr(pipeline_mod, "_build_validation_run_row",
                      lambda seed, **kw: {"run_id": "r"})
  dofn = pipeline_mod._BuildValidationRunDoFn(  # pylint: disable=protected-access
      record=_record(read_job_id=True),
      row_kwargs={})
  dofn.setup()
  rows = [
      next(dofn.process(None, valid_count=1, dlq_by_rule={})) for _ in range(3)
  ]
  assert calls == [1]
  assert all(r["job_id"] == "j-1" for r in rows)


def test_no_metadata_read_off_dataflow(monkeypatch):
  monkeypatch.setattr(pipeline_mod, "generator_version", lambda: _VERSION)
  monkeypatch.setattr(pipeline_mod, "read_job_id",
                      lambda: pytest.fail("read the metadata server"))
  dofn = pipeline_mod._BuildValidationRunDoFn(  # pylint: disable=protected-access
      record=_record(read_job_id=False),
      row_kwargs={})
  dofn.setup()


def test_a_version_mismatch_warns_once(tmp_path, monkeypatch, caplog):
  monkeypatch.setattr(pipeline_mod, "generator_version", lambda: _VERSION)
  with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
    (row,) = _run(tmp_path,
                  _record(launcher_version="0.6.1-other-1111111"))["vr"]
  assert row["generator_version"] == _VERSION  # the worker's, not the launcher's
  warnings = [
      r.getMessage()
      for r in caplog.records
      if "name=generator_version_mismatch" in r.getMessage()
  ]
  assert len(warnings) == 1
  assert "launcher=0.6.1-other-1111111" in warnings[0]
  assert f"worker={_VERSION}" in warnings[0]


def test_the_record_pickles():
  record = _record()
  assert pickle.loads(pickle.dumps(record)) == record
  dofn = pipeline_mod._BuildValidationRunDoFn(  # pylint: disable=protected-access
      record=record,
      row_kwargs={"run_id": "r"})
  assert pickle.loads(pickle.dumps(dofn))._record == record  # pylint: disable=protected-access
