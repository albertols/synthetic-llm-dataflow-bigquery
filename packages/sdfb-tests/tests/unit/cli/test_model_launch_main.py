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
"""`--tables=all` in the launcher (ADR 0045): the parse-time rules, one job
per batch, the batch identity, the Flex / CLI split, and — on the
DirectRunner — the record every batch writes.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from apache_beam.options.pipeline_options import (GoogleCloudOptions,
                                                  SetupOptions)
from sdfb_beam import pipeline as pipeline_mod
from sdfb_beam.cli import run_pipeline as rp
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import PipelineConfig, TableSpec
from sdfb_core.record import SCHEMA_PATH
from sdfb_core.validation import Thresholds
from sdfb_tests import config_parity as cp
from sdfb_tests.fakes import FakeModelClient
from sdfb_tests.fixtures import load_ddl, load_reference
from sdfb_tests.launcher_logging import restoring_root_handlers

_ROOT = Path(__file__).resolve().parents[5]
_FLEX = "FLEX_TEMPLATE_PYTHON_PY_FILE"
_FOREST = """model: forest_test
tables:
  T_ACCOUNTS: {pk: [ACC_ID]}
  T_EVENTS_LOG: {}
  T_RATES: {source_table: other_ds.RATES_V2, num_rows: 7}
  T_LEGACY: {enabled: false}
"""
_ARGV = [
    "--dlq_table=p.q.dlq", "--num_rows=10", "--run_id=r1",
    "--model_uri=gs://b/synthetic/models/m/v1/", "--tables=all",
    "--source_dataset=p.src", "--landing_dataset=p.land"
]


@pytest.fixture(name="model_uri")
def fixture_model_uri(tmp_path):
  path = tmp_path / "forest.yaml"
  path.write_text(_FOREST, encoding="utf-8")
  return str(path)


@pytest.fixture(name="jobs")
def fixture_jobs(monkeypatch):
  """Every job `main` would start, as (plan or None, its namespace)."""
  seen: list = []
  monkeypatch.setattr(
      rp,
      "_run_relational_job",
      lambda plan, a, beam_argv, registry=None: (seen.append((plan, a)) or 0))
  monkeypatch.setattr(
      rp,
      "_run_one_table",
      lambda a, beam_argv, registry=None: (seen.append((None, a)) or 0))
  monkeypatch.delenv(_FLEX, raising=False)
  return seen


def _main(model_uri: str, *extra: str) -> int:
  # main() points the root logger at stderr (basicConfig(force=True)):
  # its log is read from capsys, and its handler is removed afterwards.
  with restoring_root_handlers():
    return rp.main([*_ARGV, f"--relationships_uri={model_uri}", *extra])


def _milestones(text: str, name: str) -> list[list[str]]:
  """Each `name` milestone line of a captured log, as its tokens: the level
    reads `[ERROR]`, each field `key=value`."""
  return [
      line.split()
      for line in text.splitlines()
      if f"name={name}" in line.split()
  ]


# -- parse-time rules ------------------------------------------------------
@pytest.mark.parametrize("drop, add, message", [
    ("--source_dataset=p.src", (), "--tables=all needs io.source_dataset"),
    ("--landing_dataset=p.land", (), "--tables=all needs io.landing_dataset"),
    (None, ("--source_dataset=src",), "io.source_dataset must be "
     "project.dataset"),
    (None, ("--pk_cols=ACC_ID",), "relational.pk_cols is not allowed with "
     "--tables=all"),
    (None, ("--identity_cols=COL_001",), "relational.identity_cols is not "
     "allowed with --tables=all"),
    (None, ("--batch_index=0",), "--batch_index and --batch_count go "
     "together"),
    (None, ("--batch_index=2", "--batch_count=2"), "--batch_index must be 0 "
     "to 1"),
    (None, ("--batch_index=-1", "--batch_count=2"), "--batch_index must be 0 "
     "to 1 (--batch_count=2), got -1"),
    (None, ("--batch_index=0", "--batch_count=0"), "--batch_count must be "
     ">= 1, got 0"),
])
def test_the_rules_refuse_a_malformed_model_launch(model_uri, capsys, drop, add,
                                                   message):
  argv = [a for a in _ARGV if a != drop]
  with pytest.raises(SystemExit) as exc:
    rp.parse_args([*argv, f"--relationships_uri={model_uri}", *add])
  assert exc.value.code == 2 and message in capsys.readouterr().err


def test_a_directory_is_not_one_model_file(tmp_path, capsys):
  with pytest.raises(SystemExit):
    rp.parse_args([*_ARGV, f"--relationships_uri={tmp_path}"])
  assert "to name ONE model file" in capsys.readouterr().err


def test_the_batch_flags_need_tables_all(capsys):
  with pytest.raises(SystemExit):
    rp.parse_args([*cp.ARGV_BASE, "--batch_index=0", "--batch_count=1"])
  assert "need --tables=all" in capsys.readouterr().err


def test_a_model_launch_needs_no_reference_or_landing_table(model_uri):
  args, _ = rp.parse_args([*_ARGV, f"--relationships_uri={model_uri}"])
  assert args.reference_table is None and args.landing_table is None


# -- batches ---------------------------------------------------------------
def test_a_one_batch_model_is_one_job(model_uri, jobs):
  assert _main(model_uri) == 0
  assert len(jobs) == 1
  plan, args = jobs[0]
  runs = {r.landing_table: r for r in plan.runs}
  assert list(runs) == [
      "p.land.T_ACCOUNTS", "p.land.T_EVENTS_LOG", "p.land.T_RATES"
  ]
  assert runs["p.land.T_ACCOUNTS"].source_table == "p.src.T_ACCOUNTS"
  assert runs["p.land.T_RATES"].source_table == "p.other_ds.RATES_V2"
  assert runs["p.land.T_RATES"].num_rows == 7
  assert [r.run_id for r in plan.runs] == [
      "r1-b00-00-T_ACCOUNTS", "r1-b00-01-T_EVENTS_LOG", "r1-b00-02-T_RATES"
  ]
  assert args.run_id == "r1-b00" and args.ddl_uri == ""
  assert args._record_batch == {  # pylint: disable=protected-access
      "index": 0,
      "count": 1,
      "tables": ["T_ACCOUNTS", "T_EVENTS_LOG", "T_RATES"]
  }
  assert args._source_dataset == "p.src"  # pylint: disable=protected-access


def test_two_batches_run_in_sequence_on_the_cli(model_uri, jobs, capsys):
  assert _main(model_uri, "--max_tables_per_job=2") == 0
  assert len(jobs) == 2
  plan, first = jobs[0]
  none, second = jobs[1]
  assert [r.landing_table for r in plan.runs
         ] == ["p.land.T_ACCOUNTS", "p.land.T_EVENTS_LOG"]
  assert first.run_id == "r1-b00"
  assert none is None and second.run_id == "r1-b01-00-T_RATES"
  assert second.reference_table == "p.other_ds.RATES_V2"
  assert second.num_rows == 7
  assert second._record_batch["index"] == 1  # pylint: disable=protected-access
  # Each batch's identity names its own tables, never another batch's.
  first_tables = first._record_batch["tables"]  # pylint: disable=protected-access
  second_tables = second._record_batch["tables"]  # pylint: disable=protected-access
  assert first_tables == ["T_ACCOUNTS", "T_EVENTS_LOG"]
  assert second_tables == ["T_RATES"]
  assert "model_batches_in_sequence" in capsys.readouterr().err


def test_a_batch_index_runs_only_that_batch(model_uri, jobs):
  assert _main(model_uri, "--max_tables_per_job=2", "--batch_index=1",
               "--batch_count=2") == 0
  assert len(jobs) == 1
  none, args = jobs[0]
  assert none is None and args.run_id == "r1-b01-00-T_RATES"
  assert args._record_batch == {  # pylint: disable=protected-access
      "index": 1,
      "count": 2,
      "tables": ["T_RATES"]
  }


def test_a_batch_count_that_disagrees_stops_the_launch(model_uri, jobs,
                                                       monkeypatch, capsys):
  reads: list[str] = []
  # The first BigQuery read a batch makes is its validation_runs schema.
  monkeypatch.setattr(rp, "read_table_columns", lambda fqn:
                      (reads.append(fqn) or frozenset()))
  with pytest.raises(SystemExit, match=r"now plans 2 batch\(es\)") as exc:
    _main(model_uri, "--max_tables_per_job=2", "--batch_index=0",
          "--batch_count=3", "--validation_runs_table=p.q.validation_runs")
  assert "planned as batch 0 of 3" in str(exc.value)
  lines = _milestones(capsys.readouterr().err, "model_batch_count_mismatch")
  assert len(lines) == 1
  assert "[ERROR]" in lines[0]
  assert "planned=3" in lines[0] and "replanned=2" in lines[0]
  # Stopped before any BigQuery read: no table job, no schema read.
  assert not jobs
  assert not reads


def test_flex_refuses_more_than_one_batch_without_an_index(
    model_uri, jobs, monkeypatch):
  monkeypatch.setenv(_FLEX, "/opt/sdfb/flex_entry.py")
  with pytest.raises(SystemExit) as exc:
    _main(model_uri, "--max_tables_per_job=2")
  text = str(exc.value)
  assert "composer/synthetic_beam_bigquery.py" in text
  assert "tables=all" in text
  assert "scripts/launch/run_model.py" in text
  assert "launch.max_tables_per_job to 3" in text
  assert not jobs


def test_flex_runs_a_one_batch_model(model_uri, jobs, monkeypatch):
  monkeypatch.setenv(_FLEX, "/opt/sdfb/flex_entry.py")
  assert _main(model_uri) == 0
  assert len(jobs) == 1


def test_a_failed_batch_does_not_stop_the_next_on_the_cli(
    model_uri, monkeypatch):
  ran = []

  def fail(plan, a, beam_argv, registry=None):
    del plan, a, beam_argv, registry
    raise SystemExit("[preflight P4] T_ACCOUNTS: cannot cover num_rows")

  monkeypatch.setattr(rp, "_run_relational_job", fail)
  monkeypatch.setattr(
      rp,
      "_run_one_table",
      lambda a, beam_argv, registry=None: (ran.append(a.landing_table) or 0))
  monkeypatch.delenv(_FLEX, raising=False)
  with pytest.raises(SystemExit, match="1 of 2 batches failed") as exc:
    _main(model_uri, "--max_tables_per_job=2")
  assert "b00: SystemExit: [preflight P4]" in str(exc.value)
  assert ran == ["p.land.T_RATES"]


def test_a_batch_that_returns_non_zero_does_not_stop_the_next_on_the_cli(
    model_uri, monkeypatch, capsys):
  ran = []
  # b00 (T_ACCOUNTS, T_EVENTS_LOG) is the single-job run and returns 1;
  # b01 (T_RATES) is a one-table run and returns 0.
  monkeypatch.setattr(
      rp, "_run_relational_job", lambda plan, a, beam_argv, registry=None: 1)
  monkeypatch.setattr(
      rp,
      "_run_one_table",
      lambda a, beam_argv, registry=None: (ran.append(a.landing_table) or 0))
  monkeypatch.delenv(_FLEX, raising=False)
  with pytest.raises(SystemExit, match="1 of 2 batches failed") as exc:
    _main(model_uri, "--max_tables_per_job=2")
  assert "- b00: rc=1" in str(exc.value)
  assert ran == ["p.land.T_RATES"]
  lines = _milestones(capsys.readouterr().err, "model_batch_failed")
  assert len(lines) == 1
  assert "[ERROR]" in lines[0]
  assert "batch=b00" in lines[0] and "rc=1" in lines[0]


def test_an_oversized_batch_is_announced_and_runs_whole(tmp_path, jobs, capsys):
  path = tmp_path / "chain.yaml"
  path.write_text(
      "model: chain\ntables:\n  T_A: {pk: [K0]}\n  T_B:\n    pk: [K1]\n"
      "    fk:\n      - {cols: [K0], ref: T_A, ref_cols: [K0]}\n  T_C:\n"
      "    pk: [K2]\n    fk:\n      - {cols: [K1], ref: T_B, ref_cols: [K1]}\n",
      encoding="utf-8")
  assert _main(str(path), "--max_tables_per_job=2") == 0
  assert len(jobs) == 1
  plan, _ = jobs[0]
  assert [r.landing_table.rsplit(".", 1)[-1] for r in plan.runs
         ] == ["T_A", "T_B", "T_C"]
  text = capsys.readouterr().err
  assert "name=model_batch_oversized" in text
  assert "name=model_launch_plan" in text


class _JobNamedError(Exception):
  """Stops a job once its Dataflow options are set; carries its name."""


@pytest.fixture(name="job_name_of")
def fixture_job_name_of(monkeypatch):
  """`main`'s Dataflow job name for these flags: the job stops as soon as
    `configure_pipeline_options` has named it."""
  configure = rp.configure_pipeline_options

  def named(options, *a, **k):
    configure(options, *a, **k)
    raise _JobNamedError(options.view_as(GoogleCloudOptions).job_name)

  monkeypatch.setattr(rp, "configure_pipeline_options", named)
  monkeypatch.delenv(_FLEX, raising=False)

  def job_name_of(argv: list[str]) -> str:
    with restoring_root_handlers(), pytest.raises(_JobNamedError) as stop:
      rp.main([*argv, "--runner=DataflowRunner"])
    return str(stop.value)

  return job_name_of


_LONG_BASE = "example-acceptance-whole-model-launch-2026-10-22-forest-r01"


def test_parallel_batches_of_a_long_base_get_distinct_job_names(
    model_uri, job_name_of):
  """Review M2: `run_model.py` launches b00 (two tables, one job) and b01
    (one table) side by side; cut at 63 characters, both were named
    `sdfb-<the first 58 characters of the base>` and the second create
    failed. The `-bNN` suffix stays whole."""
  assert len(_LONG_BASE) == 59
  names = [
      job_name_of([
          *_ARGV, f"--relationships_uri={model_uri}", "--max_tables_per_job=2",
          f"--run_id={_LONG_BASE}", f"--batch_index={index}", "--batch_count=2"
      ]) for index in (0, 1)
  ]
  assert names == [f"sdfb-{_LONG_BASE[:54]}-b00", f"sdfb-{_LONG_BASE[:54]}-b01"]
  assert all(len(name) <= 63 for name in names)


def test_a_short_base_batch_job_is_named_by_its_batch(model_uri, job_name_of):
  """b00 (two tables, one job) keeps the name it had; b01's one table runs
    as `r1-b01-00-T_RATES` and its job takes the batch's name,
    `sdfb-<base>-bNN` (ADR 0045 D4), as the Composer DAG names it."""
  names = [
      job_name_of([
          *_ARGV, f"--relationships_uri={model_uri}", "--max_tables_per_job=2",
          f"--batch_index={index}", "--batch_count=2"
      ]) for index in (0, 1)
  ]
  assert names == ["sdfb-r1-b00", "sdfb-r1-b01"]


@pytest.mark.parametrize(
    "run_id, name", [("r1", "sdfb-r1"), ("a" * 58, "sdfb-" + "a" * 58),
                     (_LONG_BASE + "-b01", "sdfb-" + _LONG_BASE[:58])],
    ids=["short", "exactly_63", "long"])
def test_a_launch_that_is_no_batch_keeps_its_job_name(job_name_of, run_id,
                                                      name):
  """Behaviour parity: a job of no whole-model batch is named as before,
    `sdfb-<run id>` cut at 63 characters — a run id that ends like a batch
    suffix included."""
  assert job_name_of([*cp.ARGV_BASE, f"--run_id={run_id}"]) == name


# -- the record each batch writes (DirectRunner) ---------------------------
_ALL_COLUMNS = frozenset(
    f["name"] for f in json.loads((_ROOT / SCHEMA_PATH).read_text()))
# Every namespace the `direct` fixture's spec was built from, in order.
SEEN_ARGS: list = []
# A landing table that ENDS WITH this value fails its preflight in `direct`.
_FAIL_TABLE = "SDFB_TEST_FAIL_TABLE"


@pytest.fixture(name="direct")
def fixture_direct(tmp_path, monkeypatch):
  """`main` on the DirectRunner: each table's spec is built by hand from
    its namespace (`_prepare_table_spec` reads BigQuery), the rest is real —
    A2's record included. Mirrors `test_generation_record.py`'s
    `fixture_launcher`, except that the spec takes each table's own
    `args.num_rows` (the model's num_rows reaches it there). A table whose
    name ends with `$SDFB_TEST_FAIL_TABLE` stops at preflight."""
  SEEN_ARGS.clear()

  def spec(args, client, **kwargs):
    del client, kwargs
    SEEN_ARGS.append(args)
    if args.landing_table.endswith(os.environ.get(_FAIL_TABLE, "<none>")):
      raise SystemExit(
          f"[preflight P1] {args.landing_table}: the source has no rows")
    reference = load_reference("customers")
    config = PipelineConfig(
        table_schema=load_ddl("customers"),
        engine_name="b1_rag",
        model_client=FakeModelClient(reference_pool=reference),
        num_rows=args.num_rows,
        batch_size=10,
        seed=7,
        run_id=args.run_id,
        reference_table=args.reference_table,
        landing_table=args.landing_table,
        identity_columns=("customer_id",),
        thresholds=Thresholds(env="dev", blocker_failure_ratio=0.2),
        fail_on_blocker=False,
        blocker_verdict_only=getattr(args, "_blocker_verdict_only", False),
        write_status_reason=getattr(args, "_write_status_reason", True))
    stem = args.landing_table.rsplit(".", 1)[-1]
    return TableSpec(
        config=config,
        reference_rows=reference,
        landing_sink=WriteToJsonLines(
            str(tmp_path / f"land_{stem}"), num_shards=1),
        dlq_sink=WriteToJsonLines(str(tmp_path / f"dlq_{stem}"), num_shards=1),
        validation_runs_sink=WriteToJsonLines(
            str(tmp_path / f"vr_{stem}"), num_shards=1))

  configure = rp.configure_pipeline_options

  def configure_without_main_session(options, *a, **k):
    configure(options, *a, **k)
    # pytest's __main__ is not picklable; the test needs no main session.
    options.view_as(SetupOptions).save_main_session = False

  monkeypatch.setattr(rp, "read_table_columns", lambda fqn: _ALL_COLUMNS)
  monkeypatch.setattr(rp, "_prepare_table_spec", spec)

  # A skipped table's row goes where its spec's row would have gone.
  def validation_runs_sink(args):
    stem = args.landing_table.rsplit(".", 1)[-1]
    return WriteToJsonLines(str(tmp_path / f"vr_{stem}"), num_shards=1)

  monkeypatch.setattr(rp, "validation_runs_sink_for", validation_runs_sink)
  monkeypatch.setattr(rp, "status_reason_supported", lambda fqn: True)
  monkeypatch.setattr(rp, "configure_pipeline_options",
                      configure_without_main_session)
  # Both reports have their own tests and read nothing this test checks.
  monkeypatch.setattr(rp, "report_model_adjustments", lambda *a, **k: ())
  monkeypatch.setattr(rp, "report_row_projection", lambda *a, **k: ())
  monkeypatch.setattr(rp, "generator_version", lambda: "0.6.1-test-abcdef0")
  monkeypatch.setattr(pipeline_mod, "generator_version",
                      lambda: "0.6.1-test-abcdef0")
  monkeypatch.delenv(_FLEX, raising=False)
  return tmp_path


def _only_row(out: Path, stem: str) -> dict:
  """The one validation_runs row of table `stem`. A job's tables naming one
    validation_runs table share its first table's sink (ADR 0045 D9), so a
    row is read from whichever `vr_*` file holds it, by its landing table."""
  rows = [
      row for path in sorted(out.glob("vr_*"))
      for row in map(json.loads,
                     path.read_text().splitlines())
      if row["landing_table"].rsplit(".", 1)[-1] == stem
  ]
  assert len(rows) == 1, rows
  return rows[0]


def test_every_batch_records_its_tables_sources_rows_and_one_digest(
    model_uri, direct):
  """AC-B-3, and ADR 0045 D2/D4: each batch's rows carry the resolved source,
    the table's rows, the launch base and the batch; every batch shares one
    config digest."""
  assert _main(model_uri, "--max_tables_per_job=2", "--client_type=fake",
               "--runner=DirectRunner",
               "--validation_runs_table=p.q.validation_runs") == 0
  rows = {
      stem: _only_row(direct, stem)
      for stem in ("T_ACCOUNTS", "T_EVENTS_LOG", "T_RATES")
  }
  assert rows["T_RATES"]["reference_table"] == "p.other_ds.RATES_V2"
  assert rows["T_RATES"]["num_rows_requested"] == 7
  assert rows["T_ACCOUNTS"]["reference_table"] == "p.src.T_ACCOUNTS"
  assert rows["T_ACCOUNTS"]["num_rows_requested"] == 10
  assert {r["launch_run_id"] for r in rows.values()} == {"r1"}
  assert rows["T_RATES"]["run_id"] == "r1-b01-00-T_RATES"
  assert len({r["config_digest"] for r in rows.values()}) == 1
  batches = {
      stem: json.loads(row["config"])["batch"] for stem, row in rows.items()
  }
  assert batches["T_ACCOUNTS"] == {
      "index": 0,
      "count": 2,
      "tables": ["T_ACCOUNTS", "T_EVENTS_LOG"]
  }
  assert batches["T_RATES"] == {"index": 1, "count": 2, "tables": ["T_RATES"]}
  snapshot = json.loads(rows["T_RATES"]["config"])
  assert snapshot["values"]["launch"]["tables"] == "all"
  assert "batch_index" not in snapshot["values"]["launch"]
  assert snapshot["relationships"]["tables"]["T_RATES"]["num_rows"] == 7
  # b01's record declares b01's tables only, never b00's.
  assert set(snapshot["relationships"]["tables"]) == {"T_RATES"}


# -- a component that fails preflight (whole-model batch) ------------------
def test_a_table_that_fails_preflight_is_skipped_and_the_rest_land(
    model_uri, direct, monkeypatch, capsys):
  monkeypatch.setenv(_FAIL_TABLE, "T_EVENTS_LOG")
  # Skipped tables: exit 1, the message names them.
  with pytest.raises(SystemExit) as exc:
    _main(model_uri, "--max_tables_per_job=2", "--client_type=fake",
          "--runner=DirectRunner",
          "--validation_runs_table=p.q.validation_runs")
  assert "1 table(s) skipped at preflight; the others ran" in str(exc.value)
  assert "b00: T_EVENTS_LOG" in str(exc.value)
  err = capsys.readouterr().err
  assert _only_row(direct, "T_ACCOUNTS")["status"] == "PASSED"
  skipped = _only_row(direct, "T_EVENTS_LOG")
  assert skipped["status"] == "FAILED_PREFLIGHT"
  assert skipped["status_reason"] == (
      "[preflight P1] p.land.T_EVENTS_LOG: the source has no rows")
  assert (skipped["launch_run_id"],
          skipped["run_id"]) == ("r1", "r1-b00-01-T_EVENTS_LOG")
  assert skipped["valid_count"] == 0
  assert json.loads(skipped["config"])["batch"]["index"] == 0
  [skip] = _milestones(err, "table_skipped")
  assert "table=T_EVENTS_LOG" in skip and "[ERROR]" in skip
  # A value with spaces is quoted in a milestone line.
  assert ("reason='[preflight P1] p.land.T_EVENTS_LOG: the source has no rows'"
          in " ".join(skip))
  [partial] = _milestones(err, "model_batch_partial")
  assert "skipped=1" in partial and "skipped_tables=T_EVENTS_LOG" in partial


def test_a_batch_launched_by_its_index_exits_zero_after_a_skip(
    model_uri, direct, monkeypatch, capsys):
  """The Composer DAG and run_model.py launch each batch by its index: a
    partial skip is not a failed launch, the rows and milestones say it."""
  monkeypatch.setenv(_FAIL_TABLE, "T_EVENTS_LOG")
  assert _main(model_uri, "--max_tables_per_job=2", "--batch_index=0",
               "--batch_count=2", "--client_type=fake", "--runner=DirectRunner",
               "--validation_runs_table=p.q.validation_runs") == 0
  assert _only_row(direct, "T_EVENTS_LOG")["status"] == "FAILED_PREFLIGHT"
  assert _only_row(direct, "T_ACCOUNTS")["status"] == "PASSED"
  [partial] = _milestones(capsys.readouterr().err, "model_batch_partial")
  assert "skipped_tables=T_EVENTS_LOG" in partial


def test_a_one_batch_plan_without_an_index_exits_one_after_a_skip(
    model_uri, direct, monkeypatch):
  monkeypatch.setenv(_FAIL_TABLE, "T_EVENTS_LOG")
  with pytest.raises(SystemExit) as exc:
    _main(model_uri, "--client_type=fake", "--runner=DirectRunner",
          "--validation_runs_table=p.q.validation_runs")
  assert "1 table(s) skipped at preflight; the others ran" in str(exc.value)
  assert "- b00: T_EVENTS_LOG" in str(exc.value)
  assert _only_row(direct, "T_EVENTS_LOG")["status"] == "FAILED_PREFLIGHT"


def test_a_flex_one_batch_launch_exits_zero_after_a_skip(
    model_uri, direct, monkeypatch, capsys):
  monkeypatch.setenv(_FAIL_TABLE, "T_EVENTS_LOG")
  monkeypatch.setenv(rp._FLEX_ENTRY_ENV, "x")  # pylint: disable=protected-access
  assert _main(model_uri, "--client_type=fake", "--runner=DirectRunner",
               "--validation_runs_table=p.q.validation_runs") == 0
  assert _only_row(direct, "T_EVENTS_LOG")["status"] == "FAILED_PREFLIGHT"
  assert _milestones(capsys.readouterr().err, "model_batch_partial")


def test_a_component_fails_together_and_its_child_names_the_parent(
    tmp_path, direct, monkeypatch):
  model = tmp_path / "chain.yaml"
  model.write_text(
      "model: chain\ntables:\n  T_P: {pk: [ID]}\n"
      "  T_C: {fk: [{cols: [P_ID], ref: T_P, ref_cols: [ID]}]}\n  T_X: {}\n",
      encoding="utf-8")
  monkeypatch.setenv(_FAIL_TABLE, "T_P")
  with pytest.raises(SystemExit, match="2 table\\(s\\) skipped at preflight"):
    _main(
        str(model), "--client_type=fake", "--runner=DirectRunner",
        "--validation_runs_table=p.q.validation_runs")
  assert _only_row(direct, "T_X")["status"] == "PASSED"
  assert _only_row(direct, "T_P")["status"] == "FAILED_PREFLIGHT"
  child = _only_row(direct, "T_C")
  assert child["status"] == "FAILED_PREFLIGHT"
  assert child["status_reason"].startswith(
      "component member T_P failed preflight: [preflight P1]")
  # The child is never prepped.
  assert [
      a.landing_table for a in SEEN_ARGS if a.landing_table.endswith("T_C")
  ] == []


def test_a_parent_already_prepped_is_skipped_with_its_failed_child(
    tmp_path, direct, monkeypatch):
  model = tmp_path / "chain.yaml"
  model.write_text(
      "model: chain\ntables:\n  T_P: {pk: [ID]}\n"
      "  T_C: {fk: [{cols: [P_ID], ref: T_P, ref_cols: [ID]}]}\n  T_X: {}\n",
      encoding="utf-8")
  monkeypatch.setenv(_FAIL_TABLE, "T_C")
  with pytest.raises(SystemExit, match="2 table\\(s\\) skipped at preflight"):
    _main(
        str(model), "--client_type=fake", "--runner=DirectRunner",
        "--validation_runs_table=p.q.validation_runs")
  parent = _only_row(direct, "T_P")
  assert parent["status"] == "FAILED_PREFLIGHT"
  assert parent["status_reason"].startswith(
      "component member T_C failed preflight: [preflight P1]")
  assert not list(direct.glob("land_T_P*"))  # prepped, never generated
  assert _only_row(direct, "T_X")["status"] == "PASSED"


def test_an_error_in_a_tables_prep_skips_its_component_too(
    tmp_path, direct, monkeypatch):
  model = tmp_path / "chain.yaml"
  model.write_text(
      "model: chain\ntables:\n  T_P: {pk: [ID]}\n"
      "  T_C: {fk: [{cols: [P_ID], ref: T_P, ref_cols: [ID]}]}\n  T_X: {}\n",
      encoding="utf-8")
  fake_spec = rp._prepare_table_spec  # pylint: disable=protected-access

  def spec(args, client, **kwargs):
    if args.landing_table.endswith("T_P"):
      raise RuntimeError("Bad int64 value: COL_VALUE_001")
    return fake_spec(args, client, **kwargs)

  monkeypatch.setattr(rp, "_prepare_table_spec", spec)
  with pytest.raises(SystemExit, match="2 table\\(s\\) skipped"):
    _main(
        str(model), "--client_type=fake", "--runner=DirectRunner",
        "--validation_runs_table=p.q.validation_runs")
  # Not a preflight stop: the row names the type, never the error's text,
  # which may quote a source cell.
  why = ("RuntimeError during driver-side preflight (traceback in the "
         "launcher log)")
  row = _only_row(direct, "T_P")
  assert (row["status"], row["status_reason"]) == ("FAILED_PREFLIGHT", why)
  child = _only_row(direct, "T_C")
  assert child[
      "status_reason"] == f"component member T_P failed preflight: {why}"
  assert _only_row(direct, "T_X")["status"] == "PASSED"


def test_a_batch_whose_every_table_fails_preflight_launches_nothing(
    tmp_path, direct, monkeypatch):
  model = tmp_path / "one.yaml"
  model.write_text(
      "model: one\ntables:\n  T_A_EMPTY: {}\n  T_B_EMPTY: {}\n",
      encoding="utf-8")
  monkeypatch.setenv(_FAIL_TABLE, "_EMPTY")  # both
  with pytest.raises(SystemExit) as exc:
    _main(str(model), "--client_type=fake", "--runner=DirectRunner")
  assert "2 of 2 planned tables failed driver-side preflight" in str(exc.value)
  assert "nothing was launched" in str(exc.value)
  assert not list(direct.glob("vr_*"))


def test_an_explicit_launch_keeps_collect_then_fail(direct, monkeypatch):
  monkeypatch.setenv(_FAIL_TABLE, "T_B")
  with restoring_root_handlers(), pytest.raises(
      SystemExit, match="failed driver-side preflight") as exc:
    rp.main([
        "--reference_table=p.src.T_A", "--landing_table=p.land.T_A,p.land.T_B",
        "--dlq_table=p.q.dlq", "--num_rows=10", "--run_id=r1",
        "--model_uri=gs://b/synthetic/models/m/v1/", "--client_type=fake",
        "--runner=DirectRunner", "--validation_runs_table=p.q.validation_runs"
    ])
  assert "1 of 2 planned tables" in str(exc.value)
  assert "nothing was launched" in str(exc.value)
  assert not list(direct.glob("vr_*"))
  assert not any(getattr(a, "_blocker_verdict_only", False) for a in SEEN_ARGS)


@pytest.mark.usefixtures("direct")
def test_every_table_of_a_whole_model_batch_records_verdicts_only(model_uri):
  assert _main(model_uri, "--client_type=fake", "--runner=DirectRunner") == 0
  assert SEEN_ARGS
  assert all(getattr(a, "_blocker_verdict_only", False) for a in SEEN_ARGS)


def _status_reason_warnings(caplog) -> list:
  return [
      r for r in caplog.records
      if "name=status_reason_unsupported" in r.getMessage().split()
  ]


@pytest.mark.parametrize("columns, reason", [
    (None, "schema_unreadable"),
    (_ALL_COLUMNS - {"status_reason"}, "missing_column"),
])
def test_status_reason_is_written_only_where_the_column_exists(
    monkeypatch, caplog, columns, reason):

  def read(fqn):
    if columns is None:
      raise RuntimeError(f"cannot read {fqn}")
    return columns

  monkeypatch.setattr(rp, "read_table_columns", read)
  assert rp.status_reason_supported("p.q.validation_runs") is False
  [record] = _status_reason_warnings(caplog)
  assert record.levelno == logging.WARNING
  assert f"reason={reason}" in record.getMessage().split()
  monkeypatch.setattr(rp, "read_table_columns", lambda fqn: _ALL_COLUMNS)
  assert rp.status_reason_supported("p.q.validation_runs") is True


def test_no_validation_runs_table_writes_no_row_and_warns_nothing(
    monkeypatch, caplog):
  reads: list[str] = []
  monkeypatch.setattr(rp, "read_table_columns", lambda fqn:
                      (reads.append(fqn) or _ALL_COLUMNS))
  assert rp.status_reason_supported("") is False
  assert not _status_reason_warnings(caplog) and not reads
