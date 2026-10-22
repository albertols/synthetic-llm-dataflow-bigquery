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
"""The Composer model DAG (`composer/synthetic_model_launch.py`, ADR 0045),
read statically: Airflow is not a laptop dependency, so the file is parsed
with `ast` and never imported — the same trade-off as the single-table
DAG's tests. Its pure logic lives in `composer/sdfb_batches.py`
(test_sdfb_batches.py), loaded here from its file to run what the DAG's
`plan` task runs.

Design: docs/DESIGN.md §4 Relational generation; §11 Evaluation
(sdfb-evaluation)
(ADR 0041, 0045).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest
from sdfb_tests.model_fixtures import forest_model_text

_REPO = Path(__file__).resolve().parents[5]
_DAG = _REPO / "composer" / "synthetic_model_launch.py"
_SINGLE = _REPO / "composer" / "synthetic_beam_bigquery.py"
_MIRROR = _REPO / "composer" / "sdfb_batches.py"
_EVALUATION = _REPO / "composer" / "evaluation_framework.py"
_METADATA = _REPO / "docker" / "flex_template_metadata.json"
_MARKER = re.compile(r"\{\{[A-Z_]+\}\}")
_DATAFLOW_NAME = re.compile(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?")
# Copied from the single-table DAG, pinned AST-equal: the build-time markers,
# the house Variables, the evaluation's constants and its failure close.
_COPIED_CONSTANTS = (
    "bucket_path", "templates_path", "model_uri", "GPU_DEFAULT",
    "ENGINE_DEFAULT", "embedder_uri", "default_table_fqn",
    "validation_runs_table", "WRITE_DISPOSITION_DEFAULT", "_RAG_CHUNKS_TABLE",
    "_FREETEXT_POOLS_TABLE", "_SOURCE_STATS_TABLE", "project_version",
    "dag_version", "env_name", "project_id", "region", "subnetwork",
    "service_account", "network_tags", "app_domain", "app_name",
    "flex_template", "network_tag_experiments", "evaluation_job_name",
    "evaluation_job_id", "EVALUATION_LAUNCH_POKE_SECONDS",
    "EVALUATION_LAUNCH_TIMEOUT_SECONDS", "_QUALIFIED_TABLE_PARTS",
    "_validation_runs_parts", "evaluation_output_dataset_default",
    "EVALUATION_WORKER_DISK_GB", "EVALUATION_TRIGGER", "_REGISTRY_TABLE",
    "_DATASET_PATTERN", "_CLOSE_RUNNING_SQL", "_ENDED_NOT_DONE")
_COPIED_HELPERS = ("_callback_closes_row", "_launched_job_id", "_job_state")
# The evaluation's template parameters that do not depend on the target.
_SHARED_EVALUATION_PARAMETERS = ("sdfb_job", "scope", "trigger", "mode",
                                 "output_dataset", "disk_size_gb")
# What `plan` pushes beside its return value (the bodies), and how the DAG
# pulls one of them.
_PLAN_KEYS = {
    "base_run_id", "batch_count", "relationships_uri", "batches", "banner"
}
_PLAN_PULL = re.compile(r"task_ids=['\"]plan['\"], key=['\"](\w+)['\"]")
# What builds the batch jobs' Dataflow name, in file order.
_JOB_NAME_ASSIGNMENTS = ("app_domain", "app_name", "_version_slug", "_slug",
                         "_base_job_name", "job_name")


def _load_mirror():
  spec = importlib.util.spec_from_file_location("sdfb_batches", _MIRROR)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _tree(path: Path) -> ast.Module:
  return ast.parse(path.read_text(encoding="utf-8"))


def _name(func: ast.AST) -> str | None:
  if isinstance(func, ast.Name):
    return func.id
  if isinstance(func, ast.Attribute):
    return func.attr
  return None


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
  return [
      n for n in ast.walk(tree)
      if isinstance(n, ast.Call) and _name(n.func) == name
  ]


def _kw(call: ast.Call, keyword: str) -> ast.expr:
  values = [k.value for k in call.keywords if k.arg == keyword]
  assert len(values) == 1, keyword
  return values[0]


def _keywords(call: ast.Call) -> set[str | None]:
  return {k.arg for k in call.keywords}


def _assignments(tree: ast.Module) -> dict[str, ast.expr]:
  return {
      node.targets[0].id: node.value
      for node in tree.body
      if isinstance(node, ast.Assign) and len(node.targets) == 1 and
      isinstance(node.targets[0], ast.Name)
  }


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
  return {
      node.name: node
      for node in tree.body
      if isinstance(node, ast.FunctionDef)
  }


def _inner(function: ast.FunctionDef, name: str) -> list[str]:
  """Every call of `name`, and every nested def `name`, in `function`."""
  return [
      ast.unparse(n)
      for n in ast.walk(function)
      if (isinstance(n, ast.Call) and _name(n.func) == name) or
      (isinstance(n, ast.FunctionDef) and n.name == name)
  ]


def _params(tree: ast.Module) -> dict[str, ast.Call]:
  params = _assignments(tree)["default_dag_params"]
  assert isinstance(params, ast.Dict)
  return {
      k.value: v
      for k, v in zip(params.keys, params.values, strict=True)
      if isinstance(k, ast.Constant) and isinstance(v, ast.Call)
  }


def _entry(node: ast.AST, key: str) -> ast.expr:
  """The one value stored under `key` in any dict literal inside `node`."""
  values = [
      v for d in ast.walk(node) if isinstance(d, ast.Dict)
      for k, v in zip(d.keys, d.values, strict=True)
      if isinstance(k, ast.Constant) and k.value == key
  ]
  assert len(values) == 1, key
  return values[0]


def _task(tree: ast.Module, task_id: str) -> ast.AST:
  """The operator call, or the decorated function, of one task id."""
  for call in _calls(tree, "partial") + _calls(
      tree, "DataflowStartFlexTemplateOperator") + _calls(
          tree, "DataflowJobStatusSensor"):
    ids = [k.value for k in call.keywords if k.arg == "task_id"]
    if ids and isinstance(ids[0], ast.Constant) and ids[0].value == task_id:
      return call
  for function in _functions(tree).values():
    for decorator in function.decorator_list:
      if (isinstance(decorator, ast.Call) and
          _name(decorator.func) == "task" and any(
              isinstance(k.value, ast.Constant) and k.value.value == task_id
              for k in decorator.keywords
              if k.arg == "task_id")):
        return function
  raise AssertionError(f"no task {task_id}")


def _dag_call(tree: ast.Module) -> ast.Call:
  calls = [
      c for c in _calls(tree, "DAG")
      if isinstance(c.func, ast.Attribute) and _name(c.func.value) == "models"
  ]
  assert len(calls) == 1
  return calls[0]


def _default(param: ast.Call):
  """A Param's default as the trigger form shows it once the markers are
  substituted: a literal as written, else the first allowed value."""
  try:
    return ast.literal_eval(_kw(param, "default"))
  except ValueError:
    enum = [k.value for k in param.keywords if k.arg == "enum"]
    return ast.literal_eval(enum[0])[0] if enum else ""


DAG = _tree(_DAG)
SINGLE = _tree(_SINGLE)
BATCHES = _load_mirror()


def test_the_dag_needs_no_marker_the_import_does_not_already_substitute():
  text, single = (p.read_text(encoding="utf-8") for p in (_DAG, _SINGLE))
  assert set(_MARKER.findall(text)) <= set(_MARKER.findall(single))
  assert "{{SDFB_DDL_URI}}" not in text  # a model launch extracts live


def test_the_dag_is_valid_python_once_its_markers_are_substituted():
  values = {
      "{{PROJECT_VERSION}}": "0.7.0",
      "{{DAG_VERSION}}": "0.7.0_20261021",
      "{{ENV}}": "dev",
  }
  text = _DAG.read_text(encoding="utf-8")
  for marker in set(_MARKER.findall(text)):
    text = text.replace(marker, values.get(marker, "p.ds.t"))
  compile(text, str(_DAG), "exec")


def test_the_dag_runs_only_when_triggered():
  # Composer imports the file with relationships_uri empty: a scheduled
  # first run (the single-table DAG's `@once`) would fail in `plan`. The
  # manual pair is the standalone evaluation DAG's. The run slot stays one,
  # and one failed batch stops no other (no fail_stop).
  call, manual = _dag_call(DAG), _dag_call(_tree(_EVALUATION))
  assert ast.unparse(_kw(call, "schedule_interval")) == "None"
  assert "schedule" not in _keywords(call)
  for keyword in ("start_date", "schedule_interval", "catchup",
                  "max_active_runs"):
    mine, standalone = (ast.unparse(_kw(c, keyword)) for c in (call, manual))
    assert mine == standalone, keyword
  assert ast.literal_eval(_kw(call, "max_active_runs")) == 1
  assert "fail_stop" not in _keywords(call)


def test_the_planner_is_the_mirror_beside_it():
  imports = {
      alias.name for node in DAG.body if isinstance(node, ast.Import)
      for alias in node.names
  }
  assert "sdfb_batches" in imports
  assert not {"sdfb_core", "sdfb_beam"} & {i.split(".")[0] for i in imports}
  plan = ast.unparse(_task(DAG, "plan"))
  assert "sdfb_batches.planning_inputs" in plan
  assert "sdfb_batches.launch_plan" in plan


def test_plan_returns_the_bodies_and_pushes_the_rest():
  # Airflow maps only over a task's return value: the bodies are what `plan`
  # returns, a list (a `dict` annotation would turn multiple_outputs on), and
  # the other values go out under their own keys.
  plan = _task(DAG, "plan")
  decorator = plan.decorator_list[0]
  assert "multiple_outputs" not in _keywords(decorator)
  assert ast.unparse(plan.returns) == "list"
  returns = [n for n in ast.walk(plan) if isinstance(n, ast.Return)]
  assert len(returns) == 1
  assert ast.unparse(returns[0].value) == "plan['bodies']"
  loops = [
      n for n in ast.walk(plan)
      if isinstance(n, ast.For) and _calls(n, "xcom_push")
  ]
  assert len(loops) == 1
  pushed = set(ast.literal_eval(loops[0].iter))
  assert pushed == _PLAN_KEYS
  # every pull of a `plan` key — Jinja or the callback — names a pushed one
  pulled = set(_PLAN_PULL.findall(_DAG.read_text(encoding="utf-8")))
  assert pulled == {"base_run_id", "relationships_uri"}
  assert pulled <= pushed


def test_a_bad_model_fails_the_run_at_once():
  # A PlanError (the model, the config file, K) is deterministic: retrying
  # `plan` would only delay the failed run.
  decorator = _task(DAG, "plan").decorator_list[0]
  assert ast.literal_eval(_kw(decorator, "retries")) == 0


def test_the_plan_runs_on_what_the_dag_declares():
  # `plan` hands the mirror the DAG params and `_CONSTANTS`: a key the
  # mirror reads and the DAG lacks would fail every launch on Composer.
  params = {name: _default(call) for name, call in _params(DAG).items()}
  params["relationships_uri"] = "gs://b/rel/model.yaml"
  node = _assignments(DAG)["_CONSTANTS"]
  assert isinstance(node, ast.Dict)
  placeholders = {"network_tag_experiments": [], "labels": {"app": "sdfb"}}
  constants = {
      k.value: placeholders.get(k.value, "x")
      for k in node.keys
      if isinstance(k, ast.Constant)
  }
  uri, k = BATCHES.planning_inputs(params, None)
  plan = BATCHES.launch_plan(
      params,
      model_text=forest_model_text(enabled=12, disabled=1),
      max_tables_per_job=k,
      relationships_uri=uri,
      base_run_id="manual__x-abcd1234",
      constants=constants)
  assert (k, plan["batch_count"]) == (10, 2)
  # `plan` returns the bodies and pushes the rest: every key exists
  assert set(plan) == _PLAN_KEYS | {"bodies"}
  assert len(plan["bodies"]) == plan["batch_count"]
  json.dumps(plan)  # the task's XComs: plain JSON


@pytest.mark.parametrize("version",
                         ["0.7.0", "0.7.0rc1+local.3", "1.0.0-Beta_2"])
@pytest.mark.parametrize("model_uri", [
    "gs://b/synthetic/models/Qwen2.5/7B_Instruct/v1/",
    "gs://b/synthetic/models/gemma4/e4b-it/v2/", "config/local-model", ""
])
def test_the_batch_job_name_is_a_dataflow_name(version, model_uri):
  # sdfb_batches.batch_job_name truncates and suffixes, it does not
  # sanitise: the name the DAG hands it is lowercase [a-z0-9-] already.
  assert ast.unparse(
      _assignments(DAG)["_slug"]) == "sdfb_batches.model_slug(model_uri)"
  assert ast.unparse(_entry(_assignments(DAG)["_CONSTANTS"],
                            "job_name")) == "job_name"
  nodes = [
      n for n in DAG.body
      if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and
      n.targets[0].id in _JOB_NAME_ASSIGNMENTS
  ]
  assert [n.targets[0].id for n in nodes] == list(_JOB_NAME_ASSIGNMENTS)
  scope = {
      "project_version": version,
      "model_uri": model_uri,
      "sdfb_batches": BATCHES,
      "re": re
  }
  exec(  # pylint: disable=exec-used  # the DAG's own name-building lines; Airflow is not importable here
      compile(ast.Module(body=nodes, type_ignores=[]), str(_DAG), "exec"),
      scope)
  assert re.fullmatch(r"[a-z0-9-]+", scope["job_name"]), scope["job_name"]
  assert scope["job_name"].startswith("synthetic-sdfb-model-v")
  for index in (0, 7, 123):
    name = BATCHES.batch_job_name(scope["job_name"], index)
    assert _DATAFLOW_NAME.fullmatch(name), name


def test_batches_are_one_mapped_launch_capped_by_the_variable():
  start = _task(DAG, "start_batch")
  assert _name(start.func) == "partial"
  assert _name(start.func.value) == "DataflowStartFlexTemplateOperator"
  assert ast.literal_eval(_kw(start, "wait_until_finished")) is True
  cap = ast.unparse(_kw(start, "max_active_tis_per_dag"))
  assert cap == "MAX_PARALLEL_JOBS"
  expands = _calls(DAG, "expand")
  assert len(expands) == 1
  # The plain output of `plan`, never a custom key (`planned["bodies"]`):
  # Airflow refuses to map over one when it parses the file.
  body = _kw(expands[0], "body")
  assert isinstance(body, ast.Name), ast.unparse(body)
  sources = [
      ast.unparse(n.value)
      for n in ast.walk(DAG)
      if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and
      n.targets[0].id == body.id
  ]
  plan = _task(DAG, "plan")
  assert sources == [f"{plan.name}()"]
  assert ("Variable.get('SDFB_MAX_PARALLEL_JOBS', default_var='1')"
          in ast.unparse(_assignments(DAG)["MAX_PARALLEL_JOBS"]))


def test_a_failed_batch_job_is_not_launched_again():
  # A batch that ended in any state but DONE is not retried: a relaunch
  # replays the same run id, and a BLOCKER fails the job after its rows
  # loaded, so an append launch would land them again.
  start = _task(DAG, "start_batch")
  assert ast.literal_eval(_kw(start, "retries")) == 0


def test_the_evaluation_runs_after_every_batch_on_the_landed_jobs():
  collect = _task(DAG, "collect_landed")
  assert "TriggerRule.ALL_DONE" in ast.unparse(collect.decorator_list[0])
  assert "sdfb_batches.landed_job_ids" in ast.unparse(collect)
  # Its consumers read the return value (`…['job_ids']`): say so, rather
  # than let the `dict` annotation turn multiple_outputs on.
  assert ast.literal_eval(_kw(collect.decorator_list[0],
                              "multiple_outputs")) is False
  for function in _functions(DAG).values():
    tasks = [d for d in function.decorator_list if _calls(d, "task")]
    if tasks and function.returns and ast.unparse(function.returns) == "dict":
      assert "multiple_outputs" in _keywords(tasks[0]), function.name
  trigger = _task(DAG, "trigger_evaluation")
  # all_success: skipped with collect_landed (evaluation off, nothing landed)
  assert "trigger_rule" not in _keywords(trigger)
  text = ast.unparse(_kw(trigger, "body"))
  assert "'sdfb_job': 'evaluation'" in text
  assert "task_ids='plan', key='base_run_id'" in text
  assert "task_ids='collect_landed')['job_ids']" in text
  assert "'scope': 'manual'" in text
  # one target: the base run id — never a seed table or a table list
  assert "seed_table" not in text and "'tables'" not in text


def test_the_evaluation_parameters_are_template_parameters():
  names = {
      p["name"]
      for p in json.loads(_METADATA.read_text(encoding="utf-8"))["parameters"]
  }
  parameters = _entry(_task(DAG, "trigger_evaluation"), "parameters")
  assert isinstance(parameters, ast.Dict)
  keys = {k.value for k in parameters.keys if isinstance(k, ast.Constant)}
  # `generation_job_ids` joins the template with the evaluator's flag
  # (Task 11), which drops this exception.
  assert keys - {"generation_job_ids"} <= names, keys - names
  assert {"sdfb_job", "run_id", "generation_job_ids", "output_dataset"} <= keys


def test_the_evaluation_launch_is_the_generation_dags():
  ours, theirs = (_task(t, "trigger_evaluation") for t in (DAG, SINGLE))
  assert _keywords(ours) == _keywords(theirs)
  for keyword in _keywords(theirs) - {"body"}:
    mine, single = (ast.unparse(_kw(c, keyword)) for c in (ours, theirs))
    assert mine == single, keyword
  for key in ("containerSpecGcsPath", "jobName", "environment",
              *_SHARED_EVALUATION_PARAMETERS):
    mine, single = (ast.unparse(_entry(c, key)) for c in (ours, theirs))
    assert mine == single, key
  ours, theirs = (_task(t, "wait_for_evaluation_launch") for t in (DAG, SINGLE))
  assert ast.unparse(ours) == ast.unparse(theirs)


def test_a_failed_batch_fails_the_run_and_the_rest_are_evaluated():
  watcher = _task(DAG, "fail_if_a_batch_failed")
  assert "TriggerRule.ONE_FAILED" in ast.unparse(watcher.decorator_list[0])
  assert "AirflowFailException" in ast.unparse(watcher)
  chains = " ".join(
      ast.unparse(node)
      for node in ast.walk(DAG)
      if isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift))
  assert "start_batch >> landed" in chains
  assert "start_batch >> failed" in chains
  assert "trigger_evaluation >> wait_for_evaluation_launch" in chains


def test_the_close_statement_and_its_helpers_are_the_generation_dags():
  ours, theirs = _assignments(DAG), _assignments(SINGLE)
  for name in _COPIED_CONSTANTS:
    assert ast.unparse(ours[name]) == ast.unparse(theirs[name]), name
  ours_f, theirs_f = _functions(DAG), _functions(SINGLE)
  for name in _COPIED_HELPERS:
    assert ast.unparse(ours_f[name]) == ast.unparse(theirs_f[name]), name
  # The callback's BigQuery call and its parameter builder.
  for name in ("insert_job", "string"):
    mine = _inner(ours_f["_close_running_row"], name)
    single = _inner(theirs_f["_close_running_row"], name)
    assert mine and mine == single, name


def test_the_callback_finds_the_row_by_the_launch_base():
  callback = ast.unparse(_functions(DAG)["_close_running_row"])
  assert "task_ids='plan', key='base_run_id'" in callback
  assert "task_ids='trigger_evaluation'" in callback
  sensor = _task(DAG, "wait_for_evaluation_launch")
  assert ast.unparse(_kw(sensor, "on_failure_callback")) == "_close_running_row"


_DIFFERENT = {
    # a whole-model launch names ONE gs:// model file; the single DAG a folder
    "relationships_uri",
    # the generator's io.source_dataset here; the single DAG's names the
    # evaluation's sources
    "source_dataset",
}


def test_shared_params_keep_the_single_table_dags_defaults():
  ours, theirs = _params(DAG), _params(SINGLE)
  shared = (set(ours) & set(theirs)) - _DIFFERENT
  assert len(shared) > 30
  for name in shared:
    for keyword in ("default", "type", "enum"):
      mine = [
          ast.unparse(k.value) for k in ours[name].keywords if k.arg == keyword
      ]
      single = [
          ast.unparse(k.value)
          for k in theirs[name].keywords
          if k.arg == keyword
      ]
      assert mine == single, (name, keyword)


@pytest.mark.parametrize("name",
                         ["table_fqn", "pk_cols", "identity_cols", "tables"])
def test_what_the_model_names_is_no_dag_param(name):
  assert name not in _params(DAG)


def test_the_house_variables_and_one_of_its_own():
  gets = {
      ast.literal_eval(call.args[0])
      for call in _calls(DAG, "get")
      if isinstance(call.func, ast.Attribute) and
      _name(call.func.value) == "Variable"
  }
  assert gets == {
      "PROJECT_ID", "REGION", "DATAFLOW_SUBNET", "SA_DATAFLOW",
      "DATAFLOW_NETWORK_TAGS", "SDFB_MAX_PARALLEL_JOBS"
  }
