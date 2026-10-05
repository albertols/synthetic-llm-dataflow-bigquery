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
"""Tests for the `sdfb-eval` command line (Task 27) that need no real
evaluation pipeline: the flag surface and its defaults, usage errors,
planning (`plan`), the driver's registry events around a stand-in
pipeline (`run`, the flex entry), the exit-code gate, `schemas` and
`catalogue`. The real DirectRunner run is in `test_run.py`.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from apache_beam.options.pipeline_options import (
    DebugOptions,
    GoogleCloudOptions,
    PipelineOptions,
    SetupOptions,
    StandardOptions,
    WorkerOptions,
)
from unit.context.plan_fakes import (
    BASE,
    DS,
    JOB_ID,
    MODEL_YAML,
    PROJECT,
    QDS,
    SRC,
    TABLES,
    thelook_launch,
    thelook_models,
)

from sdfb_evaluation.beam import pipeline as beam_pipeline
from sdfb_evaluation.cli import driver, gate, run_evaluation
from sdfb_evaluation.cli.main import main, parse_args, public_run_flags
from sdfb_evaluation.context.bq import BqApiError
from sdfb_evaluation.context.gcp import JobNotFoundError
from sdfb_evaluation.context.plan import (
    Knobs,
    PlanError,
    PrepareStatement,
    build_plan,
)
from sdfb_evaluation.schemas import CLUSTERING, TABLES as EVAL_TABLES

from .conftest import catalogue
from .helpers import (
    NOW,
    REGISTRY,
    RecordingBq,
    check_row,
    make_env,
    tiny_pipeline,
)

REGION = "europe-west1"
TARGET = ["--project", PROJECT, "--region", REGION, "--job_id", JOB_ID]
LABEL_KEY_URI = "projects/demo-project/secrets/sdfb-eval-label/versions/3"


@pytest.fixture(name="bq")
def fixture_bq() -> RecordingBq:
  return RecordingBq()


@pytest.fixture(name="resolved")
def fixture_resolved(monkeypatch, bq):
  """The launch and models the CLI resolves, without the Dataflow and
  Logging APIs: the invented thelook launch of `bq`'s rows. Returns the
  calls `resolve_launch` received."""
  calls: list[dict[str, Any]] = []

  def resolve(**kwargs: Any):
    calls.append(kwargs)
    return thelook_launch(bq)

  monkeypatch.setattr(driver, "resolve_launch", resolve)
  monkeypatch.setattr(driver, "load_models", lambda uri: thelook_models())
  return calls


@pytest.fixture(name="stub")
def fixture_stub(monkeypatch):
  """`build_evaluation_pipeline` replaced by an empty pipeline: for runs
  whose pipeline never executes (the real graph is `test_run.py`'s)."""
  build = tiny_pipeline(write=False)
  monkeypatch.setattr(driver, "build_evaluation_pipeline", build)
  return build


def _usage_error(argv: list[str], capsys) -> str:
  with pytest.raises(SystemExit) as info:
    parse_args(argv)
  assert info.value.code == 2
  return capsys.readouterr().err


# --------------------------------------------------------------------------
# the flag surface
# --------------------------------------------------------------------------
def test_runner_defaults():
  assert driver.runner_defaults("DirectRunner") == ("sampled", "bq_client")
  assert driver.runner_defaults("DataflowRunner") == ("exact", "bq")
  args, beam_args = parse_args(["run", *TARGET])
  assert (args.runner, args.mode, args.sink) == ("DirectRunner", "sampled",
                                                 "bq_client")
  assert beam_args == []
  args, beam_args = parse_args([
      "run", *TARGET, "--runner", "DataflowRunner", "--temp_location",
      "gs://demo-bucket/tmp"
  ])
  assert (args.mode, args.sink) == ("exact", "bq")
  assert beam_args == ["--temp_location", "gs://demo-bucket/tmp"]
  args, _ = parse_args([
      "run", *TARGET, "--mode", "exact", "--sink", "local_json",
      "--output_local", "/tmp/sdfb-eval-out"
  ])
  assert (args.mode, args.sink) == ("exact", "local_json")  # explicit wins


def test_run_flags_default_to_the_brief():
  args, _ = parse_args(["run", *TARGET])
  assert (args.sample_rows, args.privacy_sample_rows,
          args.detection_sample_rows) == (200_000, 50_000, 50_000)
  assert (args.pair_max_columns, args.topk_profile,
          args.row_flags_top_k) == (20, 1000, 100)
  assert args.row_flags_source_keys == "hashed"
  assert args.max_bytes_billed == 1_099_511_627_776
  assert args.max_shuffle_gb == 500.0
  assert args.output_dataset == "synthetic_data_quality"
  assert args.temp_dataset is None  # the planner's <project>.<output_dataset>
  assert (args.scope, args.allow_contaminated) == ("auto", False)
  assert (args.fail_on, args.trigger) == ("none", "cli")
  assert args.label_key_uri is None and args.thresholds_uri is None
  knobs = driver.knobs_from_args(args, "eval-20260914T080000Z-00000001")
  assert knobs == Knobs(evaluation_id="eval-20260914T080000Z-00000001")
  flags = public_run_flags()
  assert {
      "--project", "--region", "--job_id", "--run_id", "--relationships_uri",
      "--tables", "--reference_dataset", "--landing_dataset", "--mode",
      "--scope", "--allow_contaminated", "--sample_rows",
      "--privacy_sample_rows", "--detection_sample_rows", "--pair_max_columns",
      "--topk_profile", "--row_flags_top_k", "--row_flags_source_keys",
      "--max_bytes_billed", "--max_shuffle_gb", "--output_dataset",
      "--temp_dataset", "--sink", "--output_local", "--thresholds_uri",
      "--fail_on", "--trigger", "--label_key_uri", "--runner"
  } == set(flags)
  assert "--fixture_dir" not in flags  # hidden
  assert "--evaluation_id" not in flags  # minted, never passed (R88c)


@pytest.mark.parametrize(
    "extra, needle",
    [
        (["--row_flags_source_keys", "raw"], "row_flags_source_keys"),  # R88a
        (["--output_local", "/tmp/out[1]"], "glob"),
        (["--output_local", "/tmp/out*"], "glob"),
        (["--evaluation_id", "eval-mine"], "evaluation_id"),  # R88c
        (["--experiments=enable_data_sampling"], "enable_data_sampling"),
        (["--experiments", "use_runner_v2,enable_data_sampling"
         ], "enable_data_sampling"),
        (["--experiment=enable_data_sampling=true"], "enable_data_sampling"),
        (["--runner", "DataflowRunner", "--sink", "bq_client"
         ], "DataflowRunner"),
        (["--sink", "local_json"], "output_local"),
        (["--sink", "bq", "--output_local", "/tmp/out"], "output_local"),
        (["--label_key_uri", "relative/key.bin"], "label_key_uri"),
        (["--run_id", "thelook-0913-a1b2c3"], "one of"),
        (["--mode", "approximate"], "mode"),
        (["--runner", " "], "runner"),
        (["--fixture_dir", "/tmp/fixture"], "fixture_dir"),
        (["--fail_on", "error"], "fail_on"),
        (["--sample_rows", "0"], "sample_rows"),
    ])
def test_run_usage_errors_exit_2(extra, needle, capsys):
  assert needle in _usage_error(["run", *TARGET, *extra], capsys)


def test_a_run_needs_exactly_one_target(capsys):
  err = _usage_error(["run", "--project", PROJECT], capsys)
  assert "--job_id" in err and "--run_id" in err and "--tables" in err
  err = _usage_error(["run", "--project", PROJECT, "--job_id", JOB_ID], capsys)
  assert "--region" in err  # a job id is only visible in its region
  err = _usage_error(["run", "--job_id", JOB_ID, "--region", REGION], capsys)
  assert "--project" in err


def test_other_commands_take_no_beam_arguments(capsys):
  for argv in (["plan", *TARGET, "--temp_location", "gs://demo-bucket/tmp"], [
      "report", "--project", PROJECT, "--evaluation_id", "eval-x", "--streaming"
  ], ["catalogue", "--runner", "DataflowRunner"]):
    assert "unrecognized arguments" in _usage_error(argv, capsys)
  err = _usage_error(["report", "--project", PROJECT], capsys)
  assert "--evaluation_id" in err
  err = _usage_error(
      ["compare", "--project", PROJECT, "--evaluation_ids", "eval-a"], capsys)
  assert "two" in err


def test_manual_target_maps_to_the_resolver_input():
  args, _ = parse_args([
      "run", "--project", PROJECT, "--tables", "users,orders",
      "--landing_dataset", "thelook_synthetic", "--reference_dataset",
      "bigquery-public-data.thelook_ecommerce", "--relationships_uri",
      "gs://demo-bucket/synthetic/relationships/", "--scope", "manual"
  ])
  job_id, manual = driver.launch_request(args)
  assert job_id is None
  assert manual == {
      "tables_in_order": [f"{DS}.users", f"{DS}.orders"],
      "reference_table": f"{SRC}.users",
      "relationships_uri": "gs://demo-bucket/synthetic/relationships/",
      "params": {
          "relationships_uri": "gs://demo-bucket/synthetic/relationships/"
      },
  }
  args, _ = parse_args(["run", *TARGET])
  assert driver.launch_request(args) == (JOB_ID, None)


# --------------------------------------------------------------------------
# pipeline options (R88e, the Task 26 review note)
# --------------------------------------------------------------------------
def _big_plan(bq):
  """The thelook plan with 9 M rows a side: side inputs past the 512 MB
  cache floor."""
  plan = build_plan(
      launch=thelook_launch(bq),
      models=thelook_models(),
      bq=bq,
      knobs=Knobs(temp_dataset=QDS),
      mode="exact",
      trigger="cli",
      runner="DataflowRunner",
      now=NOW)
  return dataclasses.replace(
      plan,
      tables=tuple(
          dataclasses.replace(
              t, rows_source=9_000_000, rows_synthetic=9_000_000)
          for t in plan.tables))


def test_options_carry_the_plan_sized_cache_and_keep_upload_graph(bq):
  plan = _big_plan(bq)
  wanted = beam_pipeline.pipeline_options_defaults(
      "DataflowRunner", plan)["max_cache_memory_usage_mb"]
  assert wanted > beam_pipeline.MIN_CACHE_MB
  args, beam_args = parse_args([
      "run", *TARGET, "--runner", "DataflowRunner", "--temp_location",
      "gs://demo-bucket/tmp", "--experiments=use_runner_v2", "--experiments",
      "shuffle_mode=service", "--save_main_session"
  ])
  options = driver.pipeline_options(args, beam_args, plan,
                                    "eval-20260914T080000Z-00000001")
  assert options.view_as(WorkerOptions).max_cache_memory_usage_mb == wanted
  assert options.view_as(SetupOptions).save_main_session is False
  experiments = options.view_as(DebugOptions).experiments
  assert experiments == [
      "use_runner_v2", "shuffle_mode=service", "upload_graph"
  ]
  assert options.view_as(StandardOptions).runner == "DataflowRunner"
  cloud = options.view_as(GoogleCloudOptions)
  assert (cloud.project, cloud.region) == (PROJECT, REGION)
  assert cloud.temp_location == "gs://demo-bucket/tmp"
  assert cloud.job_name == "sdfb-eval-20260914t080000z-00000001"
  # a larger cache the operator asked for is kept; a smaller one is raised
  args, beam_args = parse_args([
      "run", *TARGET, "--runner", "DataflowRunner",
      f"--max_cache_memory_usage_mb={wanted + 1000}", "--job_name", "mine"
  ])
  options = driver.pipeline_options(args, beam_args, plan, "eval-x")
  assert options.view_as(
      WorkerOptions).max_cache_memory_usage_mb == wanted + 1000
  assert options.view_as(GoogleCloudOptions).job_name == "mine"
  args, beam_args = parse_args(["run", *TARGET, "--experiments=use_fastavro"])
  options = driver.pipeline_options(args, beam_args, plan, "eval-x")
  assert options.view_as(DebugOptions).experiments == ["use_fastavro"]
  assert options.view_as(WorkerOptions).max_cache_memory_usage_mb == wanted
  with pytest.raises(ValueError, match="enable_data_sampling"):
    driver.pipeline_options(args, ["--experiments=enable_data_sampling"], plan,
                            "eval-x")


def test_run_passes_the_prepared_plan_to_options_and_pipeline(
    bq, resolved, monkeypatch, capsys):
  seen: dict[str, Any] = {}
  real_prepare, real_defaults = (driver.prepare_evaluation,
                                 driver.pipeline_options_defaults)

  def prepare(plan, client):
    seen["prepared"] = real_prepare(plan, client)
    return seen["prepared"]

  def defaults(runner, plan=None):
    seen["defaults_plan"] = plan
    return real_defaults(runner, plan)

  build = tiny_pipeline()
  monkeypatch.setattr(driver, "prepare_evaluation", prepare)
  monkeypatch.setattr(driver, "pipeline_options_defaults", defaults)
  monkeypatch.setattr(driver, "build_evaluation_pipeline", build)
  executed: list[tuple[Any, bool]] = []

  def execute(pipeline, wait):
    executed.append((pipeline, wait))
    return driver.execute_pipeline(pipeline, wait)

  code = main(["run", *TARGET, "--label_key_uri", LABEL_KEY_URI],
              make_env(bq, execute=execute))
  assert code == 0
  assert resolved[0]["job_id"] == JOB_ID
  assert (resolved[0]["project"], resolved[0]["region"]) == (PROJECT, REGION)
  assert len(build.built) == 1
  built_plan, kwargs = build.built[0]
  assert built_plan is seen["prepared"] is seen["defaults_plan"]
  assert built_plan.label_key_uri == LABEL_KEY_URI
  assert kwargs["stats_query"] == bq.query
  assert len(executed) == 1
  pipeline, wait = executed[0]
  assert wait is True
  cache = pipeline.options.view_as(WorkerOptions).max_cache_memory_usage_mb
  assert cache == real_defaults("DirectRunner",
                                built_plan)["max_cache_memory_usage_mb"]
  assert pipeline.options.view_as(SetupOptions).save_main_session is False
  capsys.readouterr()


# --------------------------------------------------------------------------
# run: the registry events around the pipeline
# --------------------------------------------------------------------------
def test_a_fresh_evaluation_id_per_attempt(bq, resolved, monkeypatch, capsys):
  monkeypatch.setattr(driver, "build_evaluation_pipeline", tiny_pipeline())
  env = make_env(bq, execute=driver.execute_pipeline)
  assert main(["run", *TARGET], env) == 0
  assert main(["run", *TARGET], env) == 0
  ids = [row["evaluation_id"] for row in bq.registry_rows()]
  first, second = ids[0], ids[-1]
  assert first != second  # the same launch, the same second: two attempts
  for evaluation_id in (first, second):
    assert driver.EVALUATION_ID_RE.fullmatch(evaluation_id)
    assert evaluation_id.startswith("eval-20260914T080000Z-")
  assert ids == [first, first, second, second]  # RUNNING, FINAL each
  # the temp tables an attempt creates carry its own id
  created = [sql for sql, _ in bq.executed if sql.startswith("CREATE")]
  assert any(first in sql for sql in created)
  assert any(second in sql for sql in created)
  assert len(resolved) == 2
  out = capsys.readouterr().out
  assert first in out and second in out
  assert re.fullmatch(r"eval-\d{8}T\d{6}Z-[0-9a-f]{8}",
                      driver.mint_evaluation_id(NOW, "0a1b2c3d"))
  assert driver.mint_evaluation_id(driver.Env().now(),
                                   driver.Env().token()) != (
                                       driver.mint_evaluation_id(
                                           driver.Env().now(),
                                           driver.Env().token()))


def test_run_writes_running_prepares_runs_then_loads_final_last(
    bq, resolved, monkeypatch, capsys):
  del resolved
  monkeypatch.setattr(driver, "build_evaluation_pipeline",
                      tiny_pipeline(counts={
                          "total": 3,
                          "pass": 2,
                          "warn": 1
                      }))
  code = main(["run", *TARGET], make_env(bq, execute=driver.execute_pipeline))
  assert code == 0
  running, final = bq.registry_rows()
  for row in (running, final):
    check_row(REGISTRY, row, ordered=row is running)
  assert (running["event"], running["status"]) == ("RUNNING", "RUNNING")
  assert (final["event"], final["status"]) == ("FINAL", "SUCCEEDED")
  assert running["evaluation_id"] == final["evaluation_id"]
  assert final["recorded_at"] > running["recorded_at"]
  assert running["trigger"] == "cli" and running["runner"] == "DirectRunner"
  assert running["mode"] == "sampled"  # the DirectRunner default
  assert running["evaluation_params"]["label_key_mode"] == "ephemeral"
  assert running["generation_job_id"] == JOB_ID
  # RUNNING is loaded before any prepare DDL; the registry's FINAL last
  kinds = [kind for kind, _ in bq.events]
  first_load = kinds.index("load")
  assert all(kind != "execute" for kind in kinds[:first_load])
  assert "execute" in kinds[first_load:]
  assert kinds[-1] == "load" and kinds.count("load") == 2
  assert all(fqn == f"{QDS}.{REGISTRY}" for fqn, _ in bq.loads)
  out = capsys.readouterr().out
  assert final["evaluation_id"] in out and "SUCCEEDED" in out


def test_a_driver_exception_writes_failed_then_reraises(bq, resolved, stub,
                                                        capsys):
  del resolved, stub

  def explode(pipeline, wait):
    del pipeline, wait
    raise RuntimeError("worker pool exhausted in europe-west1")

  with pytest.raises(RuntimeError, match="worker pool exhausted"):
    main(["run", *TARGET, "--label_key_uri", LABEL_KEY_URI],
         make_env(bq, execute=explode))
  running, failed = bq.registry_rows()
  check_row(REGISTRY, running)
  check_row(REGISTRY, failed)
  assert (running["event"], running["status"]) == ("RUNNING", "RUNNING")
  assert (failed["event"], failed["status"]) == ("FINAL", "FAILED")
  assert failed["status_reason"] == (
      "RuntimeError: worker pool exhausted in europe-west1")
  assert failed["evaluation_id"] == running["evaluation_id"]
  assert failed["recorded_at"] > running["recorded_at"]
  assert failed["finished_at"] == failed["recorded_at"]
  assert failed["metrics_total"] is None and failed["overall_score"] is None
  assert [t["name"] for t in failed["tables"]
         ] == ["products", "users", "orders", "order_items"]
  captured = capsys.readouterr()
  assert LABEL_KEY_URI not in captured.out + captured.err


def test_a_transient_prepare_error_writes_failed_then_propagates(
    bq, resolved, stub, monkeypatch):
  """R90(g): whatever `prepare_evaluation` raises sits inside the
  driver's FAILED-then-re-raise path — one FAILED row, then the error."""
  del resolved
  # sampled mode with a small --sample_rows plans sample tables; their
  # CTAS is no source pin, so its failure is never degraded
  bq.execute_failures["FARM_FINGERPRINT"] = BqApiError(
      "DDL: 503 backend error", status=503)
  with pytest.raises(BqApiError, match="503 backend error"):
    main(["run", *TARGET, "--sample_rows", "50"], make_env(bq))
  running, failed = bq.registry_rows()
  assert running["status"] == "RUNNING"
  assert (failed["event"], failed["status"]) == ("FINAL", "FAILED")
  assert failed["status_reason"].startswith("BqApiError: DDL: 503")
  check_row(REGISTRY, failed)
  assert stub.built == []  # the pipeline was never built
  # the same for an error on the source pin itself (a transient one
  # raises there instead of degrading the pin)
  bq.execute_failures.clear()
  bq.loads.clear()

  def unavailable(plan, client):
    del plan, client
    raise BqApiError("DDL: 503 the source pin could not be created", status=503)

  monkeypatch.setattr(driver, "prepare_evaluation", unavailable)
  with pytest.raises(BqApiError, match="source pin"):
    main(["run", *TARGET], make_env(bq))
  assert [r["status"] for r in bq.registry_rows()] == ["RUNNING", "FAILED"]
  assert stub.built == []


def test_a_pipeline_that_ends_without_a_final_row_is_failed(bq, resolved, stub):
  del resolved, stub
  with pytest.raises(RuntimeError, match="FINAL"):
    main(["run", *TARGET], make_env(bq))  # built, never run: no FINAL row
  assert [r["status"] for r in bq.registry_rows()] == ["RUNNING", "FAILED"]


def test_a_failed_row_that_cannot_be_written_never_hides_the_error(
    bq, resolved, stub, capsys):
  del resolved, stub

  def explode(pipeline, wait):
    del pipeline, wait
    bq.load_failures[REGISTRY] = PermissionError("403 tables.updateData")
    raise RuntimeError("the pipeline failed first")

  with pytest.raises(RuntimeError, match="the pipeline failed first"):
    main(["run", *TARGET], make_env(bq, execute=explode))
  assert [r["status"] for r in bq.registry_rows()] == ["RUNNING"]
  err = capsys.readouterr().err
  assert "FAILED registry row could not be written" in err
  assert "403 tables.updateData" in err


def test_a_planning_failure_writes_a_schema_valid_failed_row(
    bq, monkeypatch, capsys):

  def missing(**kwargs: Any):
    job_id, region = kwargs["job_id"], kwargs["region"]
    raise JobNotFoundError(
        f"Dataflow job {job_id} was not found in region {region}")

  monkeypatch.setattr(driver, "resolve_launch", missing)
  with pytest.raises(JobNotFoundError):
    main(["run", *TARGET, "--mode", "exact", "--trigger", "agent"],
         make_env(bq))
  (row,) = bq.registry_rows()
  check_row(REGISTRY, row)
  assert (row["event"], row["status"]) == ("FINAL", "FAILED")
  assert row["status_reason"].startswith("JobNotFoundError: Dataflow job")
  assert driver.EVALUATION_ID_RE.fullmatch(row["evaluation_id"])
  assert row["evaluated_at"] == "2026-09-14T08:00:00.000000Z"
  assert row["recorded_at"] > row["evaluated_at"]
  assert row["finished_at"] == row["recorded_at"]
  assert (row["mode"], row["trigger"], row["runner"]) == ("exact", "agent",
                                                          "DirectRunner")
  assert row["generation_job_id"] == JOB_ID
  assert row["generation_region"] == REGION
  assert row["tables"] == [] and row["run_ids"] == []
  assert row["params_source"] is None and row["bq_bytes_processed"] is None
  assert row["evaluation_params"]["sample_rows"] == 200_000
  assert row["evaluation_params"]["label_key_mode"] == "ephemeral"
  assert row["catalogue_version"] == catalogue().version
  assert len(row["evaluation_key"]) == 32
  assert bq.executed == []  # nothing was prepared
  capsys.readouterr()


def test_a_plan_error_keeps_what_planning_created(bq, monkeypatch, capsys):
  launch = thelook_launch(bq)
  monkeypatch.setattr(driver, "resolve_launch", lambda **kwargs: launch)
  monkeypatch.setattr(driver, "load_models", lambda uri: thelook_models())
  created = "CREATE SNAPSHOT TABLE `demo-project.synthetic_data_quality.s`"

  def refuse(**kwargs: Any):
    del kwargs
    raise PlanError("orders: the as_of_diff start snapshot already exists",
                    (PrepareStatement(created, {}),))

  monkeypatch.setattr(driver, "build_plan", refuse)
  with pytest.raises(PlanError):
    main(["run", *TARGET], make_env(bq))
  (row,) = bq.registry_rows()
  check_row(REGISTRY, row)
  assert row["status"] == "FAILED"
  assert row["status_reason"].startswith("PlanError: orders")
  assert f"planning created {created}" in row["warnings"]
  # the launch did resolve: the row records it, under the planner's key
  assert row["run_ids"] == list(launch.run_ids)
  assert row["params_source"] == "jobs_labels+logs"
  assert row["relationship_model"] == "thelook"
  plan = build_plan(
      launch=launch,
      models=thelook_models(),
      bq=bq,
      knobs=Knobs(temp_dataset=QDS),
      mode="sampled",
      trigger="cli",
      runner="DirectRunner",
      now=NOW)
  assert row["evaluation_key"] == plan.evaluation_key
  capsys.readouterr()


def test_label_key_uri_is_operator_mode_and_never_printed(
    bq, resolved, monkeypatch, capsys):
  del resolved
  build = tiny_pipeline()
  monkeypatch.setattr(driver, "build_evaluation_pipeline", build)
  code = main(["run", *TARGET, "--label_key_uri", LABEL_KEY_URI],
              make_env(bq, execute=driver.execute_pipeline))
  assert code == 0
  assert len(build.built) == 1
  assert build.built[0][0].label_key_uri == LABEL_KEY_URI
  rows = bq.registry_rows()
  assert [r["evaluation_params"]["label_key_mode"] for r in rows
         ] == ["operator", "operator"]
  assert LABEL_KEY_URI not in json.dumps(rows)
  captured = capsys.readouterr()
  assert LABEL_KEY_URI not in captured.out + captured.err
  assert "label key: operator" in captured.out


def test_local_json_keeps_every_event_under_the_evaluation_directory(
    bq, resolved, monkeypatch, tmp_path, capsys):
  del resolved
  monkeypatch.setattr(driver, "build_evaluation_pipeline", tiny_pipeline())
  out = tmp_path / "out"
  env = make_env(bq, execute=driver.execute_pipeline)
  argv = ["run", *TARGET, "--sink", "local_json", "--output_local", str(out)]
  assert main(argv, env) == 0
  assert bq.loads == []  # nothing goes to BigQuery
  (directory,) = list(out.iterdir())
  assert driver.EVALUATION_ID_RE.fullmatch(directory.name)  # <DIR>/<id>
  lines = [
      json.loads(line)
      for path in sorted((directory / REGISTRY).glob("*.jsonl"))
      for line in path.read_text().splitlines()
  ]
  assert sorted(r["status"] for r in lines) == ["RUNNING", "SUCCEEDED"]
  for row in lines:
    check_row(REGISTRY, row, ordered=False)
    assert row["evaluation_id"] == directory.name
  assert str(directory) in capsys.readouterr().out

  def explode(pipeline, wait):
    raise RuntimeError("boom")

  env.execute = explode
  with pytest.raises(RuntimeError, match="boom"):
    main(argv, env)
  failed_dir = next(d for d in out.iterdir() if d != directory)
  statuses = sorted(
      json.loads(line)["status"]
      for path in (failed_dir / REGISTRY).glob("*.jsonl")
      for line in path.read_text().splitlines())
  assert statuses == ["FAILED", "RUNNING"]
  capsys.readouterr()


# --------------------------------------------------------------------------
# the exit-code gate (R88g)
# --------------------------------------------------------------------------
def test_fail_on_exit_codes(bq, resolved, monkeypatch, capsys):
  del resolved
  counts = {"total": 4, "pass": 2, "warn": 1, "fail": 1}
  assert gate.exit_code("none", counts) == 0
  assert gate.exit_code("warn", counts) == 1
  assert gate.exit_code("fail", counts) == 1
  only_warn = {"total": 3, "pass": 2, "warn": 1, "fail": 0}
  assert gate.exit_code("fail", only_warn) == 0
  assert gate.exit_code("warn", only_warn) == 1
  assert gate.exit_code("warn", {"total": 2, "pass": 2}) == 0
  with pytest.raises(ValueError, match="fail_on"):
    gate.exit_code("error", counts)
  env = make_env(bq, execute=driver.execute_pipeline)
  monkeypatch.setattr(driver, "build_evaluation_pipeline",
                      tiny_pipeline(counts=only_warn))
  assert main(["run", *TARGET, "--fail_on", "fail"], env) == 0
  assert main(["run", *TARGET, "--fail_on", "warn"], env) == 1
  assert "gate (--fail_on warn): TRIPPED" in capsys.readouterr().out
  # a run that FAILED never returns the gate's code: it raises
  env.execute = lambda pipeline, wait: None  # never run: no FINAL row
  with pytest.raises(RuntimeError):
    main(["run", *TARGET, "--fail_on", "warn"], env)
  capsys.readouterr()


def _metric_row(metric_id: str, value: float, status: str, **extra: Any):
  metric = catalogue().get(metric_id)
  return {
      "metric_id": metric_id,
      "table_name": "orders",
      "column_name": "amount",
      "column_name_2": None,
      "edge": None,
      "column_kind": "numeric",
      "value": value,
      "source_value": None,
      "synthetic_value": None,
      "baseline_value": None,
      "noise_floor": None,
      "ci_low": None,
      "ci_high": None,
      "n_source": 5000,
      "n_synthetic": 5000,
      "method": "exact",
      "sample_rate": None,
      "status": status,
      "threshold_warn": metric.warn,
      "threshold_fail": metric.fail,
      "detail": None,
      **extra,
  }


def test_a_final_row_reading_failed_exits_3_whatever_fail_on_says(
    bq, resolved, monkeypatch, capsys):
  """R90(g): the pipeline can write FINAL = FAILED and end normally (no
  launch table evaluated). `run` reads the row back: exit 3, no second
  registry row, and `--fail_on none` does not mask it."""
  del resolved
  none_evaluated = {"total": 4, "not_evaluated": 4}
  assert gate.final_exit_code({"status": "FAILED"}, "none", none_evaluated) == 3
  assert gate.final_exit_code({"status": "FAILED"}, "warn", none_evaluated) == 3
  assert gate.final_exit_code({"status": "FAILED"},
                              "none",
                              none_evaluated,
                              gated=False) == 3
  failing = {"total": 2, "pass": 1, "fail": 1}
  for status in ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PARTIAL", "SKIPPED"):
    assert gate.final_exit_code({"status": status}, "none", failing) == 0
    assert gate.final_exit_code({"status": status}, "fail", failing) == 1
    assert gate.final_exit_code({"status": status},
                                "fail",
                                failing,
                                gated=False) == 0
  assert (gate.EXIT_TRIPPED, gate.EXIT_FAILED) == (1, 3)
  monkeypatch.setattr(driver, "build_evaluation_pipeline",
                      tiny_pipeline(counts=none_evaluated, status="FAILED"))
  env = make_env(bq, execute=driver.execute_pipeline)
  assert main(["run", *TARGET, "--fail_on", "none"], env) == 3
  # RUNNING, then the pipeline's own FINAL: the driver appends nothing
  assert [(r["event"], r["status"]) for r in bq.registry_rows()
         ] == [("RUNNING", "RUNNING"), ("FINAL", "FAILED")]
  out = capsys.readouterr().out
  assert "the evaluation finished FAILED: exit 3" in out
  assert main(["run", *TARGET, "--fail_on", "warn"], env) == 3
  assert "TRIPPED" not in capsys.readouterr().out  # 3 is not the gate
  # the template entry applies no gate, and still reports the failure
  assert run_evaluation.main(TARGET, env) == 3
  assert len(bq.registry_rows()) == 6
  capsys.readouterr()


def test_thresholds_uri_regrades_the_gate_only(tmp_path):
  ks = catalogue().get("column.ks")
  rows = [
      _metric_row("column.ks", ks.warn / 2, "pass"),
      _metric_row("column.ks", ks.fail * 2, "fail", column_name="cost"),
      _metric_row("table.overall_score", 0.9, "info", column_name=None),
  ]
  assert gate.gate_counts(rows, {}) == {
      "total": 2,
      "pass": 1,
      "warn": 0,
      "fail": 1,
      "info": 0,
      "not_evaluated": 0
  }
  path = tmp_path / "gate.yaml"
  path.write_text(
      "thresholds:\n"
      f"  column.ks: {{warn: {ks.warn / 4}, fail: {ks.fail * 4}}}\n")
  overrides = gate.load_thresholds(str(path))
  assert overrides == {"column.ks": (ks.warn / 4, ks.fail * 4)}
  strict = gate.gate_counts(rows, overrides)
  assert (strict["pass"], strict["warn"], strict["fail"]) == (0, 2, 0)
  assert rows[0]["status"] == "pass"  # the stored rows are never rewritten
  with pytest.raises(ValueError, match="unknown status"):
    gate.gate_counts([{**rows[0], "status": "maybe"}], {})
  path.write_text("thresholds:\n  column.nope: {warn: 1, fail: 2}\n")
  with pytest.raises(ValueError, match=r"column\.nope"):
    gate.load_thresholds(str(path))
  path.write_text("thresholds:\n  column.ks: {warn: lots}\n")
  with pytest.raises(ValueError, match=r"column\.ks"):
    gate.load_thresholds(str(path))
  path.write_text("column.ks: 0.1\n")
  with pytest.raises(ValueError, match="thresholds"):
    gate.load_thresholds(str(path))


def test_thresholds_uri_moves_the_gate_of_a_run_not_its_rows(
    bq, resolved, monkeypatch, tmp_path, capsys):
  del resolved
  ks = catalogue().get("column.ks")
  between = (ks.warn + ks.fail) / 2  # a WARN by the catalogue
  counts = {"total": 1, "pass": 0, "warn": 1, "fail": 0}
  monkeypatch.setattr(driver, "build_evaluation_pipeline",
                      tiny_pipeline(counts=counts, ks=[between]))
  env = make_env(bq, execute=driver.execute_pipeline)
  strict = tmp_path / "strict.yaml"
  strict.write_text(f"thresholds:\n  column.ks: {{fail: {ks.warn}}}\n")
  argv = ["run", *TARGET, "--fail_on", "fail"]
  assert main(argv, env) == 0  # the catalogue: one WARN, no FAIL
  capsys.readouterr()
  assert main([*argv, "--thresholds_uri", str(strict)], env) == 1
  out = capsys.readouterr().out
  assert "TRIPPED — 1 fail, 0 warn under --thresholds_uri" in out
  stored = [
      row for fqn, rows in bq.loads if fqn.endswith(".evaluation_metrics")
      for row in rows
  ]
  assert [r["status"] for r in stored] == ["warn", "warn"]  # both runs
  assert all(r["threshold_fail"] == ks.fail for r in stored)
  assert [
      r["metrics_fail"] for r in bq.registry_rows() if r["event"] == "FINAL"
  ] == [0, 0]
  # a file that cannot be used is a usage error, before anything starts
  loads = len(bq.loads)
  strict.write_text("thresholds:\n  column.nope: {fail: 1}\n")
  err = _usage_error([*argv, "--thresholds_uri", str(strict)], capsys)
  assert "column.nope" in err
  err = _usage_error(
      [*argv, "--thresholds_uri",
       str(tmp_path / "missing.yaml")], capsys)
  assert "--thresholds_uri" in err
  assert len(bq.loads) == loads


# --------------------------------------------------------------------------
# the flex-template entry (R88f)
# --------------------------------------------------------------------------
def test_flex_entry_does_not_wait_and_never_applies_fail_on(
    bq, resolved, stub, capsys):
  del resolved, stub
  waits: list[bool] = []

  def execute(pipeline, wait):
    del pipeline
    waits.append(wait)
    return "2026-09-14_01_00_00-777"

  argv = [
      *TARGET, "--runner", "DataflowRunner", "--temp_location",
      "gs://demo-bucket/tmp", "--fail_on", "fail", "--trigger", "composer"
  ]
  # Beam validates Dataflow options against Cloud Storage when a Pipeline
  # is made: here the pipeline is only a holder of its options
  made: list[PipelineOptions] = []

  def make_pipeline(options):
    made.append(options)
    return SimpleNamespace(options=options)

  env = make_env(bq, execute=execute, make_pipeline=make_pipeline)
  assert run_evaluation.main(argv, env) == 0
  assert waits == [False]
  assert len(made) == 1
  options = made[0]
  assert options.view_as(StandardOptions).runner == "DataflowRunner"
  assert options.view_as(DebugOptions).experiments == ["upload_graph"]
  assert options.view_as(
      GoogleCloudOptions).temp_location == "gs://demo-bucket/tmp"
  (running,) = bq.registry_rows()  # FINAL is the pipeline's, on Dataflow
  check_row(REGISTRY, running)
  assert (running["status"], running["trigger"]) == ("RUNNING", "composer")
  assert (running["runner"], running["mode"]) == ("DataflowRunner", "exact")
  out = capsys.readouterr().out
  assert "2026-09-14_01_00_00-777" in out
  assert "--fail_on is not applied" in out
  # the same arguments through `sdfb-eval run` wait for the job, then
  # read its FINAL row back (here the job wrote none)
  waits.clear()
  bq.canned.append((REGISTRY, [running]))
  with pytest.raises(LookupError, match="FINAL"):
    main(["run", *argv], env)
  assert waits == [True]
  capsys.readouterr()


def test_flex_entry_on_a_local_runner_still_loads_its_outputs(
    bq, resolved, monkeypatch, capsys):
  del resolved
  monkeypatch.setattr(driver, "build_evaluation_pipeline",
                      tiny_pipeline(counts={
                          "total": 1,
                          "fail": 1
                      }))
  waits: list[bool] = []

  def execute(pipeline, wait):
    waits.append(wait)
    return driver.execute_pipeline(pipeline, wait)

  code = run_evaluation.main([*TARGET, "--fail_on", "fail"],
                             make_env(bq, execute=execute))
  assert code == 0  # never the gate's code
  assert waits == [True]  # bq_client: the driver loads after the pipeline
  assert [r["status"] for r in bq.registry_rows()] == ["RUNNING", "SUCCEEDED"]
  assert "--fail_on is not applied here" in capsys.readouterr().out


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------
def _copy_launch(bq):
  launch = thelook_launch(bq, write_disposition="append")
  copies = tuple(dataclasses.replace(w, job_type="COPY") for w in launch.writes)
  return dataclasses.replace(launch, writes=copies)


def test_plan_prints_tables_scopes_methods_bytes_and_shuffle(
    bq, resolved, capsys):
  del resolved
  assert main(["plan", *TARGET, "--dry_run"], make_env(bq)) == 0
  out = capsys.readouterr().out
  assert "eval-20260914T080000Z-00000001" in out
  for landing in TABLES:
    assert landing in out
  assert f"{SRC}.orders" in out
  assert "scope table / ok" in out
  assert "BigQuery dry-run bytes" in out and "predicted shuffle" in out
  assert re.search(r"email\s+STRING\s+identifier", out)
  assert re.search(r"status\s+STRING\s+categorical\s+census exact", out)
  assert re.search(r"num_of_item\s+INT(64|EGER)\s+numeric", out)
  assert "orders(user_id) -> users(id)" in out
  assert "read-only parent" in out  # products
  # a plan writes nothing: no registry row, no prepare DDL (R5)
  assert bq.loads == [] and bq.executed == []
  assert main(["plan", *TARGET, "--format", "json"], make_env(bq)) == 0
  document = json.loads(capsys.readouterr().out)
  assert document["mode"] == "sampled" and document["runner"] == "DirectRunner"
  assert document["bq_bytes_estimate"] > 0
  assert document["predicted_shuffle_gb"] >= 0
  orders = next(t for t in document["tables"] if t["name"] == "orders")
  assert orders["scope"] == {
      "mode": "table",
      "status": "ok",
      "reason": None,
      "read_table": f"{DS}.orders"
  }
  methods = {c["name"]: c for c in orders["columns"]}
  assert methods["status"]["kind"] == "categorical"
  assert methods["status"]["census"] == "exact"
  assert methods["order_id"]["is_key"] is True
  assert len(document["prepare_sql"]) == 3  # the source pins a run creates
  assert document["planning_ddl"] == []


def test_plan_dry_run_still_creates_as_of_diff_start_snapshots(
    bq, monkeypatch, capsys):
  monkeypatch.setattr(driver, "resolve_launch",
                      lambda **kwargs: _copy_launch(bq))
  monkeypatch.setattr(driver, "load_models", lambda uri: thelook_models())
  assert main(["plan", *TARGET, "--dry_run", "--format", "json"],
              make_env(bq)) == 0
  document = json.loads(capsys.readouterr().out)
  executed = [sql for sql, _ in bq.executed]
  assert len(executed) == 3 and all(
      sql.startswith("CREATE SNAPSHOT TABLE") and "_start_" in sql
      for sql in executed)  # R57: the one thing planning creates
  assert document["planning_ddl"] == executed
  assert bq.loads == []
  scopes = {t["name"]: t["scope"] for t in document["tables"]}
  assert scopes["orders"]["mode"] == "as_of_diff"
  assert scopes["orders"]["status"] == "ok"
  assert main(["plan", *TARGET, "--dry_run"], make_env(bq)) == 0
  text = capsys.readouterr().out
  assert "planning already created" in text and "--no_planning_snapshots" in text


def test_no_planning_snapshots_creates_nothing_and_marks_scopes_unplanned(
    bq, monkeypatch, capsys):
  monkeypatch.setattr(driver, "resolve_launch",
                      lambda **kwargs: _copy_launch(bq))
  monkeypatch.setattr(driver, "load_models", lambda uri: thelook_models())
  argv = ["plan", *TARGET, "--dry_run", "--no_planning_snapshots"]
  assert main([*argv, "--format", "json"], make_env(bq)) == 0
  document = json.loads(capsys.readouterr().out)
  assert bq.executed == [] and bq.loads == []
  assert document["planning_ddl"] == []
  for table in document["tables"]:
    if table["role"] == "external":
      continue
    assert table["scope"]["status"] == "unknown", table["name"]
    assert "--no_planning_snapshots" in table["scope"]["reason"]
    assert table["evaluated"] is False
    assert "--no_planning_snapshots" in table["skip_reason"]
  assert main(argv, make_env(bq)) == 0
  text = capsys.readouterr().out
  assert text.count("UNPLANNED") == 3
  assert "nothing can be evaluated" in text


def test_plan_of_a_manual_target_resolves_and_plans(bq, tmp_path, capsys):
  model = tmp_path / "thelook.yaml"
  model.write_text(MODEL_YAML)
  argv = [
      "plan", "--project", PROJECT, "--tables", "users,orders,order_items",
      "--landing_dataset", "thelook_synthetic", "--reference_dataset", SRC,
      "--relationships_uri",
      str(model), "--scope", "manual", "--format", "json"
  ]
  assert main(argv, make_env(bq)) == 0
  document = json.loads(capsys.readouterr().out)
  assert document["launch"]["params_source"] == "manual"
  assert document["launch"]["generation_job_id"] is None
  tables = {t["name"]: t for t in document["tables"]}
  assert list(tables) == ["products", "users", "orders", "order_items"]
  assert tables["orders"]["source_table"] == f"{SRC}.orders"
  assert tables["orders"]["scope"]["mode"] == "manual"
  assert tables["orders"]["evaluated"] is True
  assert tables["order_items"]["edges"] == [
      "order_items(order_id) -> orders(order_id)",
      "order_items(product_id) -> synthetic_data.products(id)"
  ]
  # no panel: a manual launch carries no reference_rows_limit
  assert tables["users"]["panel"] is None


def test_a_run_id_target_reads_validation_runs(bq, capsys):
  bq.canned.append(("validation_runs", [{
      "run_id": f"{BASE}-{i:02d}-{name}",
      "landing_table": f"{DS}.{name}",
      "reference_table": f"{SRC}.{name}",
      "reference_digest": None,
      "valid_count": 120 * 2**i,
      "num_rows_requested": 120 * 2**i,
      "status": "PASSED",
      "created_at": "2026-09-13T13:45:00Z",
  } for i, name in enumerate(("users", "orders", "order_items"))]))
  argv = [
      "plan", "--project", PROJECT, "--run_id", BASE, "--scope", "manual",
      "--format", "json"
  ]
  assert main(argv, make_env(bq)) == 0
  document = json.loads(capsys.readouterr().out)
  sql, params = next((s, p) for s, p in bq.queries if "validation_runs" in s)
  assert f"`{QDS}.validation_runs`" in sql and params == {"base": BASE}
  assert document["launch"]["base_run_id"] == BASE
  assert document["launch"]["run_ids"] == [
      f"{BASE}-00-users", f"{BASE}-01-orders", f"{BASE}-02-order_items"
  ]
  tables = {t["name"]: t for t in document["tables"]}
  assert tables["order_items"]["rows_expected"] == 480
  assert tables["order_items"]["source_table"] == f"{SRC}.order_items"
  bq.canned.clear()
  bq.canned.append(("validation_runs", []))
  with pytest.raises(ValueError, match="no landing table"):
    main(argv, make_env(bq))
  capsys.readouterr()


def test_request_key_is_the_planners_key(bq):
  launch = thelook_launch(bq)
  knobs = Knobs(temp_dataset=QDS, sample_rows=1234)
  plan = build_plan(
      launch=launch,
      models=thelook_models(),
      bq=bq,
      knobs=knobs,
      mode="sampled",
      trigger="cli",
      runner="DirectRunner",
      now=NOW)
  assert driver.launch_key(launch, "sampled", knobs) == plan.evaluation_key
  assert driver.launch_key(launch, "exact", knobs) != plan.evaluation_key


# --------------------------------------------------------------------------
# schemas, catalogue
# --------------------------------------------------------------------------
def test_schemas_prints_the_commands_and_touches_nothing(bq, capsys):
  assert main(["schemas", "--project", PROJECT], make_env(bq)) == 0
  out = capsys.readouterr().out
  assert out.count("bq mk --table") == 4
  assert out.count("CREATE OR REPLACE VIEW") == 2
  for table in EVAL_TABLES:
    assert f"{PROJECT}:synthetic_data_quality.{table}" in out
  assert bq.executed == [] and bq.queries == []
  assert main(["schemas", "--project", PROJECT, "--dataset", "eval_dev"],
              make_env(bq)) == 0
  assert f"{PROJECT}:eval_dev.evaluation_metrics" in capsys.readouterr().out
  err = _usage_error(
      ["schemas", "--project", PROJECT, "--dataset", "d`; DROP TABLE x"],
      capsys)
  assert "do not name a BigQuery dataset" in err


def test_schemas_apply_issues_the_ddl(bq, capsys):
  assert main(["schemas", "--project", PROJECT, "--apply"], make_env(bq)) == 0
  executed = [sql for sql, _ in bq.executed]
  tables, views = executed[:4], executed[4:]
  assert [
      re.match(r"CREATE TABLE IF NOT EXISTS `([^`]+)`", sql).group(1)
      for sql in tables
  ] == [f"{QDS}.{name}" for name in EVAL_TABLES]
  assert [
      re.match(r"CREATE OR REPLACE VIEW `([^`]+)`", sql).group(1)
      for sql in views
  ] == [f"{QDS}.evaluation_latest", f"{QDS}.evaluation_latest_per_job"]
  history, metrics, _, flags = tables
  assert "PARTITION BY DATE(`recorded_at`)" in history
  assert "CLUSTER BY `relationship_model`, `engine`, `evaluation_id`" in history
  assert "`evaluation_id` STRING NOT NULL" in history
  assert "`run_ids` ARRAY<STRING>" in history
  assert re.search(
      r"`tables` ARRAY<STRUCT<`name` STRING[^>]*`table_score` FLOAT64>>",
      history)
  assert "`generation_params` JSON" in history
  assert "`check` STRING NOT NULL" in flags
  assert "PARTITION BY TIMESTAMP_TRUNC(`evaluated_at`, MONTH)" in metrics
  assert "CLUSTER BY " + ", ".join(
      f"`{c}`" for c in CLUSTERING["evaluation_metrics"]) in metrics
  assert "partition_expiration_days=180" in flags
  assert "partition_expiration_days" not in metrics
  assert 'OPTIONS(description="' in metrics
  out = capsys.readouterr().out
  assert out.count("created or kept") == 4 and out.count("replaced") == 2
  assert bq.loads == []


def test_catalogue_formats(capsys):
  assert main(["catalogue", "--format", "json"]) == 0
  out = capsys.readouterr().out
  assert out == catalogue().to_json()
  assert main(["catalogue", "--format", "md"]) == 0
  text = capsys.readouterr().out
  packaged = catalogue()
  assert f"catalogue {packaged.version}" in text
  for metric in packaged.metrics:
    assert f"`{metric.id}`" in text
  assert text.count("\n| `") == len(packaged.metrics)


def test_catalogue_out_writes_a_file(tmp_path: Path, capsys):
  path = tmp_path / "catalogue.json"
  assert main(["catalogue", "--format", "json", "--out", str(path)]) == 0
  assert path.read_text() == catalogue().to_json()
  assert str(path) in capsys.readouterr().out
