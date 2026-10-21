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
"""The launcher's half of the generation record (ADR 0044): one schema check
per launch, one record per Dataflow job, and today's row when the table
cannot take the record.

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import apache_beam as beam
import pytest
import yaml
from apache_beam.options.pipeline_options import PipelineOptions, SetupOptions
from sdfb_beam import pipeline as pipeline_mod
from sdfb_beam.cli import run_pipeline as rp
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import PipelineConfig, TableSpec
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.record import RECORD_COLUMNS, SCHEMA_PATH, config_digest
from sdfb_core.validation import Thresholds
from sdfb_core.validation.summary import RunSummary
from sdfb_tests.config_parity import ARGV_BASE
from sdfb_tests.fakes import FakeModelClient
from sdfb_tests.fixtures import load_ddl, load_reference

_ROOT = Path(__file__).resolve().parents[5]
_ALL = frozenset(
    f["name"] for f in json.loads((_ROOT / SCHEMA_PATH).read_text()))
_LEGACY = frozenset(RunSummary.model_fields)
_VERSION = "0.6.1-test-abcdef0"
_VR = "p.q.validation_runs"


def _spec(tmp: Path, args) -> TableSpec:
  schema, reference = load_ddl("customers"), load_reference("customers")
  config = PipelineConfig(
      table_schema=schema,
      engine_name="b1_rag",
      model_client=FakeModelClient(reference_pool=reference),
      num_rows=20,
      batch_size=10,
      seed=7,
      run_id=args.run_id,
      reference_table=args.reference_table,
      landing_table=args.landing_table,
      identity_columns=("customer_id",),
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.2),
      fail_on_blocker=False)
  stem = args.landing_table.rsplit(".", 1)[-1]
  return TableSpec(
      config=config,
      reference_rows=reference,
      landing_sink=WriteToJsonLines(str(tmp / f"land_{stem}"), num_shards=1),
      dlq_sink=WriteToJsonLines(str(tmp / f"dlq_{stem}"), num_shards=1),
      validation_runs_sink=WriteToJsonLines(
          str(tmp / f"vr_{stem}"), num_shards=1))


def _rows(tmp: Path, name: str) -> list[dict]:
  rows = []
  for f in sorted(tmp.glob(name + "*")):
    rows.extend(json.loads(line) for line in f.read_text().splitlines() if line)
  return rows


def _only_row(tmp: Path, name: str) -> dict:
  rows = _rows(tmp, name)
  assert len(rows) == 1, rows
  return rows[0]


@pytest.fixture(name="launcher")
def fixture_launcher(tmp_path, monkeypatch):
  """`main` end to end on the DirectRunner: the table spec is built by hand
    (`_prepare_table_spec` reads BigQuery), everything after it is real.
    `live["columns"]` is what the table's schema read returns (a set, or an
    exception to raise); `live["out"]` is where the sinks write."""
  live: dict[str, object] = {"columns": _ALL, "out": tmp_path}

  def columns(fqn):
    assert fqn == _VR
    value = live["columns"]
    if isinstance(value, Exception):
      raise value  # pylint: disable=raising-non-exception  # isinstance-checked: pylint infers every value live holds
    return value

  configure = rp.configure_pipeline_options

  def configure_without_main_session(options, *a, **k):
    configure(options, *a, **k)
    # pytest's __main__ is not picklable; the test needs no main session.
    options.view_as(SetupOptions).save_main_session = False

  monkeypatch.setattr(rp, "read_table_columns", columns)
  monkeypatch.setattr(rp, "_prepare_table_spec",
                      lambda args, client, **kw: _spec(live["out"], args))
  monkeypatch.setattr(rp, "configure_pipeline_options",
                      configure_without_main_session)
  # Both reports have their own tests and read nothing this test checks.
  monkeypatch.setattr(rp, "report_model_adjustments", lambda *a, **k: ())
  monkeypatch.setattr(rp, "report_row_projection", lambda *a, **k: ())
  monkeypatch.setattr(rp, "generator_version", lambda: _VERSION)
  monkeypatch.setattr(pipeline_mod, "generator_version", lambda: _VERSION)
  return live


def _main(launcher, out: Path, *extra: str) -> int:
  out.mkdir(parents=True, exist_ok=True)
  launcher["out"] = out
  cfg = out / "launch.yaml"
  cfg.write_text("generation: {similarity: 0.7}\n")
  before = list(logging.root.handlers)
  try:
    return rp.main([
        *ARGV_BASE, "--client_type=fake", "--relationships_uri=",
        f"--validation_runs_table={_VR}", f"--config_uri={cfg}",
        "--runner=DirectRunner", *extra
    ])
  finally:
    # main() points the root logger at this test's captured stderr, which
    # pytest closes after the test; Beam's job server logs once more at exit.
    for handler in logging.root.handlers[:]:
      if handler not in before:
        logging.root.removeHandler(handler)


def test_a_launch_writes_every_record_column_and_its_values_replay(
    tmp_path, launcher):
  assert _main(launcher, tmp_path) == 0
  row = _only_row(tmp_path, "vr_t")
  assert set(row) == _ALL
  assert set(RECORD_COLUMNS) <= set(row)
  assert row["job_id"] is None and row["job_name"] is None  # DirectRunner
  assert row["launch_run_id"] == "r1"
  assert row["generator_version"] == _VERSION
  assert row["similarity"] == 0.7 and row["seed"] == "derived"
  assert row["retrieval_method"] == "centroid"
  assert row["relationships_uri"] == "" and row["relationships_digest"] is None
  assert datetime.fromisoformat(row["launched_at"]).tzinfo is not None
  snapshot = json.loads(row["config"])
  assert snapshot["provenance"]["generation.similarity"] == "file"
  assert snapshot["launcher"] == {"version": _VERSION}
  assert snapshot["beam"]["runner"] == "DirectRunner"
  assert snapshot["batch"] is None
  assert snapshot["derived"]["cache_keys"]["freetext_pools"] == (
      "gs://b/synthetic/models/m/v1/")
  replay = tmp_path / "replay.yaml"
  replay.write_text(yaml.safe_dump(snapshot["values"]))
  args, _ = rp.parse_args(["--run_id=r1", f"--config_uri={replay}"])
  assert config_digest(rp.config_of(args)) == row["config_digest"]
  assert args.similarity == 0.7


def test_a_table_without_the_columns_gets_todays_row(tmp_path, launcher,
                                                     capsys):
  launcher["columns"] = _LEGACY
  assert _main(launcher, tmp_path) == 0
  row = _only_row(tmp_path, "vr_t")
  assert set(row) == _LEGACY
  # main() reconfigures logging onto stderr, so read the launcher's log there.
  text = capsys.readouterr().err
  assert "generation_record_degraded" in text
  assert f"bq update --schema {SCHEMA_PATH} p:q.validation_runs" in text


def test_the_landed_rows_do_not_depend_on_the_record(tmp_path, launcher):
  assert _main(launcher, tmp_path / "rec") == 0
  launcher["columns"] = _LEGACY
  assert _main(launcher, tmp_path / "legacy") == 0

  def landed(tmp):
    return sorted(
        _rows(tmp, "land_t"), key=lambda r: json.dumps(r, sort_keys=True))

  assert landed(tmp_path / "rec") == landed(tmp_path / "legacy")


def test_an_unreadable_schema_degrades_to_the_legacy_row(
    tmp_path, launcher, capsys):
  launcher["columns"] = PermissionError("403 tables.get denied")
  assert _main(launcher, tmp_path) == 0
  row = _only_row(tmp_path, "vr_t")
  assert set(row) == _LEGACY
  # main() reconfigures logging onto stderr, so read the launcher's log there.
  text = capsys.readouterr().err
  assert "generation_record_degraded" in text
  assert "PermissionError" in text


def test_no_validation_runs_table_reads_no_schema(monkeypatch, caplog):
  monkeypatch.setattr(rp, "read_table_columns",
                      lambda fqn: pytest.fail("read the schema"))
  args, _ = rp.parse_args(ARGV_BASE)
  plan = rp.plan_launch(["p.land.t"], "p.src.t", True, RelationshipRegistry(),
                        "r1")
  with caplog.at_level(logging.WARNING):
    assert rp.prepare_record_base(args, RelationshipRegistry(), plan) is None


def test_the_record_carries_the_keys_main_stored(monkeypatch):
  # Task 7 review: `derived.cache_keys` is the launch's own keys (main's
  # `args._cache_keys`), never a recomputation.
  monkeypatch.setattr(rp, "read_table_columns", lambda fqn: _ALL)
  args, _ = rp.parse_args([*ARGV_BASE, f"--validation_runs_table={_VR}"])
  stored = {cache: f"stored-{cache}" for cache in rp.CACHES}
  args._cache_keys = stored  # pylint: disable=protected-access  # main's channel
  plan = rp.plan_launch(["p.land.t"], "p.src.t", True, RelationshipRegistry(),
                        "r1")
  base = rp.prepare_record_base(args, RelationshipRegistry(), plan)
  assert base is not None
  assert base.derived["cache_keys"] == stored


def test_every_table_of_a_single_job_launch_carries_one_record(
    tmp_path, launcher, monkeypatch):
  seen: list[TableSpec] = []

  def capture(p, specs):
    seen.extend(specs)
    _ = p | beam.Create([0])  # a pipeline needs one transform to run
    return {}

  monkeypatch.setattr(rp, "build_relational_pipeline", capture)
  rc = _main(launcher, tmp_path,
             "--landing_table=p.land.A_TABLE,p.land.B_TABLE",
             "--generate_fk_relationships=false")
  assert rc == 0
  assert [s.config.landing_table for s in seen
         ] == ["p.land.A_TABLE", "p.land.B_TABLE"]
  records = [s.config.record for s in seen]
  assert all(r is not None for r in records)
  assert len({r.columns["config_digest"] for r in records}) == 1
  assert {r.columns["launch_run_id"] for r in records} == {"r1"}


def test_finish_record_reads_the_jobs_beam_options():
  options = PipelineOptions([
      "--runner=DataflowRunner", "--job_name=synthetic-sdfb-v0-6-1-m",
      "--machine_type=g2-standard-8", "--max_num_workers=4",
      "--experiments=use_runner_v2",
      "--experiments=worker_accelerator=type:nvidia-l4;count:1;install-nvidia-driver",
      "--experiments=use_network_tags=tag-a"
  ])
  beam_ = rp.beam_block(options, "DataflowRunner")
  assert beam_["job_name"] == "synthetic-sdfb-v0-6-1-m"
  assert beam_["machine_type"] == "g2-standard-8"
  assert beam_["max_num_workers"] == 4
  assert beam_["accelerator"] == (
      "type:nvidia-l4;count:1;install-nvidia-driver")
  assert not [e for e in beam_["experiments"] if "network_tags" in e]
