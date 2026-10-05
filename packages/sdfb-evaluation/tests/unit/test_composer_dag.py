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
"""Static tests for the Composer DAG (Task 29) and its opt-in trigger.

Airflow is not a dependency here, so the DAG files are read with ``ast`` and
never imported. The files live at the repository root (``composer/``); when
the package is copied out as a standalone unit they are absent and the whole
module skips.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGE.parents[1]
EVALUATION_DAG = REPO_ROOT / "composer" / "evaluation_framework.py"
GENERATION_DAG = REPO_ROOT / "composer" / "synthetic_beam_bigquery.py"
METADATA = PACKAGE / "deploy" / "flex_template_metadata.json"

if not (EVALUATION_DAG.parent.is_dir() and GENERATION_DAG.is_file()):
  pytest.skip(
      "composer/ is not part of this copy of the package",
      allow_module_level=True)

_NEVER_PASSED = ("runner", "project", "region", "sdk_container_image",
                 "experiments", "fail_on")


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


def _one(tree: ast.AST, name: str) -> ast.Call:
  (call,) = _calls(tree, name)
  return call


def _kw(call: ast.Call, keyword: str) -> ast.expr:
  (value,) = [k.value for k in call.keywords if k.arg == keyword]
  return value


def _dict_entries(node: ast.AST):
  for sub in ast.walk(node):
    if isinstance(sub, ast.Dict):
      for key, value in zip(sub.keys, sub.values, strict=True):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
          yield key.value, value


def _entry(node: ast.AST, key: str) -> ast.expr:
  (value,) = [v for k, v in _dict_entries(node) if k == key]
  return value


def _params(tree: ast.Module) -> dict[str, ast.Call]:
  return {
      k: v
      for k, v in _dict_entries(tree)
      if isinstance(v, ast.Call) and _name(v.func) == "Param"
  }


def _chains(tree: ast.Module) -> list[list[str]]:
  """Every ``a >> b >> c`` statement as its ordered list of names."""
  chains = []
  for stmt in ast.walk(tree):
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.BinOp) and
            isinstance(stmt.value.op, ast.RShift)):
      continue
    names: list[str] = []
    node: ast.expr = stmt.value
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift):
      assert isinstance(node.right, ast.Name)
      names.append(node.right.id)
      node = node.left
    assert isinstance(node, ast.Name)
    names.append(node.id)
    chains.append(names[::-1])
  return chains


def _edges(tree: ast.Module) -> set[tuple[str, str]]:
  return {(c[i], c[i + 1]) for c in _chains(tree) for i in range(len(c) - 1)}


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
  (fn,) = [
      n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name
  ]
  return fn


def _strings(node: ast.AST) -> list[str]:
  return [
      n.value
      for n in ast.walk(node)
      if isinstance(n, ast.Constant) and isinstance(n.value, str)
  ]


def _flat(text: str) -> str:
  return " ".join(text.split())


# --------------------------------------------------------------------------- #
# The evaluation DAG
# --------------------------------------------------------------------------- #


def test_dag_id_and_manual_schedule():
  dag = _one(_tree(EVALUATION_DAG), "DAG")
  assert ast.literal_eval(_kw(dag, "dag_id")) == "sdfb_evaluation_framework"
  assert ast.literal_eval(_kw(dag, "schedule_interval")) is None


def test_params_cover_the_brief():
  params = _params(_tree(EVALUATION_DAG))
  assert {
      "generation_job_id", "run_id", "relationships_uri", "tables", "mode",
      "wait_for_generation", "machine_type", "max_workers"
  } <= set(params)
  wait = params["wait_for_generation"]
  assert ast.literal_eval(_kw(wait, "default")) is False
  assert ast.literal_eval(_kw(wait, "type")) == "boolean"
  trigger = params["trigger"]
  assert ast.literal_eval(_kw(trigger, "default")) == "composer"
  assert ast.literal_eval(_kw(trigger, "enum")) == ["composer", "chained"]


def test_wait_for_generation_is_a_deferrable_sensor_behind_a_short_circuit():
  tree = _tree(EVALUATION_DAG)
  sensor = _one(tree, "DataflowJobStatusSensor")
  assert ast.literal_eval(_kw(sensor,
                              "expected_statuses")) == {"JOB_STATE_DONE"}
  assert ast.literal_eval(_kw(sensor, "deferrable")) is True
  gate = _one(tree, "ShortCircuitOperator")
  callable_name = _kw(gate, "python_callable")
  assert isinstance(callable_name, ast.Name)
  assert "wait_for_generation" in ast.unparse(_function(tree, callable_name.id))
  edges = _edges(tree)
  assert ("wait_gate", "wait_for_generation_job") in edges
  assert ("wait_for_generation_job", "start_evaluation") in edges
  # The skipped branch (wait_for_generation false) still reaches the launch.
  assert ("begin", "start_evaluation") in edges
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  assert (ast.literal_eval(_kw(
      start, "trigger_rule")) == "none_failed_min_one_success")


def test_launch_points_at_the_evaluator_template():
  tree = _tree(EVALUATION_DAG)
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  spec = ast.unparse(_entry(start, "containerSpecGcsPath"))
  assert "templates_path" in spec and "synthetic/" in spec
  (template,) = [
      n.value for n in tree.body if isinstance(n, ast.Assign) and
      [t.id for t in n.targets if isinstance(t, ast.Name)] == ["flex_template"]
  ]
  assert ast.unparse(template).startswith("f'sdfb-evaluation-{")
  assert ast.unparse(template).endswith("-template.json'")
  assert "flex_template" in spec


def test_launch_parameters_are_metadata_names_from_params_or_constants():
  tree = _tree(EVALUATION_DAG)
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  parameters = _entry(start, "parameters")
  assert isinstance(parameters, ast.Dict)
  keys = [k.value for k in parameters.keys if isinstance(k, ast.Constant)]
  assert len(keys) == len(parameters.keys), "no ** unpacking in parameters"
  names = {p["name"] for p in json.loads(METADATA.read_text())["parameters"]}
  assert set(keys) <= names
  assert not set(keys) & set(_NEVER_PASSED)
  declared = set(_params(tree))
  for key, value in zip(keys, parameters.values, strict=True):
    text = ast.unparse(value)
    refs = re.findall(r"params\.(\w+)", text)
    # every value is a DAG Param reference or a plain string constant
    assert refs or isinstance(value, ast.Constant), key
    assert set(refs) <= declared, (key, refs)
  assert _entry(parameters, "trigger") is not None
  assert "lower" in ast.unparse(_entry(parameters, "allow_contaminated"))


def test_nothing_the_launcher_owns_appears_anywhere_in_the_launch():
  tree = _tree(EVALUATION_DAG)
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  launch = _entry(start, "launchParameter")
  everywhere = " ".join(_strings(launch))
  for forbidden in ("sdk_container_image", "fail_on"):
    assert forbidden not in everywhere
  # no `experiments` template parameter; no data-sampling experiment anywhere
  parameters = _entry(start, "parameters")
  assert "experiments" not in {
      k.value for k in parameters.keys if isinstance(k, ast.Constant)
  }
  additional = _entry(_entry(start, "environment"), "additionalExperiments")
  assert isinstance(additional, ast.List)
  assert not [s for s in _strings(additional) if "enable_data_sampling" in s]
  assert "sdk_container_image" not in ast.unparse(start)
  assert "fail_on" not in ast.unparse(start)


def test_machine_type_and_max_workers_go_to_the_environment():
  tree = _tree(EVALUATION_DAG)
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  environment = _entry(start, "environment")
  assert "params.machine_type" in ast.unparse(
      _entry(environment, "machineType"))
  assert "params.max_workers" in ast.unparse(_entry(environment, "maxWorkers"))
  parameters = _entry(start, "parameters")
  assert not {"machine_type", "max_workers"} & {
      k.value for k in parameters.keys if isinstance(k, ast.Constant)
  }


def test_airflow_variables_are_the_house_ones():
  tree = _tree(EVALUATION_DAG)
  variables = {
      ast.literal_eval(c.args[0])
      for c in _calls(tree, "get")
      if isinstance(c.func, ast.Attribute) and
      isinstance(c.func.value, ast.Name) and c.func.value.id == "Variable"
  }
  assert {"PROJECT_ID", "REGION", "SA_DATAFLOW", "DATAFLOW_SUBNET"} <= variables


def _failure_sql(tree: ast.Module) -> str:
  (sql,) = [
      n for n in ast.walk(tree) if isinstance(n, ast.Constant) and
      isinstance(n.value, str) and "INSERT INTO" in n.value
  ]
  return _flat(sql.value)


def test_failure_callback_is_wired_on_the_launch():
  tree = _tree(EVALUATION_DAG)
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  callback = _kw(start, "on_failure_callback")
  assert isinstance(callback, ast.Name)
  body = ast.unparse(_function(tree, callback.id))
  assert "BigQueryInsertJobOperator" in body
  # the Param-derived dataset is validated before it is part of SQL text
  assert "fullmatch" in body
  # the job only completes inside Airflow's view when the launch waits
  assert ast.literal_eval(_kw(start, "wait_until_finished")) is True


def test_failure_sql_is_one_insert_select_that_closes_a_running_row():
  tree = _tree(EVALUATION_DAG)
  sql = _failure_sql(tree)
  assert sql.startswith("INSERT INTO `{registry}`")
  assert sql.count("INSERT") == 1
  assert "evaluation_data_history" not in sql  # the table is a placeholder
  assert "'FINAL' AS event" in sql
  assert "'FAILED' AS status" in sql
  assert "@reason AS status_reason" in sql
  assert "AS recorded_at" in sql and "AS finished_at" in sql
  assert "AS evaluation_job_id" in sql
  assert "FROM `{registry}` AS running" in sql
  assert "running.event = 'RUNNING'" in sql
  assert "running.trigger = @trigger" in sql
  assert "running.generation_job_id = @target" in sql
  assert "running.base_run_id = @target" in sql
  assert "running.recorded_at >= @run_start" in sql
  assert re.search(
      r"NOT EXISTS \( SELECT 1 FROM `\{registry\}` AS closed "
      r"WHERE closed\.evaluation_id = running\.evaluation_id "
      r"AND closed\.event = 'FINAL'\)", sql)
  # only @-parameters carry values: no other interpolation placeholder
  assert set(re.findall(r"\{(\w+)\}", sql)) == {"registry"}


def test_failure_sql_keeps_one_final_row_per_evaluation():
  sql = _failure_sql(_tree(EVALUATION_DAG))
  assert ("QUALIFY ROW_NUMBER() OVER ( PARTITION BY running.evaluation_id "
          "ORDER BY running.recorded_at DESC) = 1") in sql


def test_tables_only_launch_logs_a_warning_instead_of_closing_silently():
  body = ast.unparse(_function(_tree(EVALUATION_DAG), "_close_running_row"))
  assert "logging.warning" in body


def test_failure_callback_binds_every_parameter_it_uses():
  tree = _tree(EVALUATION_DAG)
  sql = _failure_sql(tree)
  used = set(re.findall(r"@(\w+)", sql))
  assert used == {"reason", "job_id", "trigger", "target", "run_start"}
  assert used <= set(_strings(_function(tree, "_close_running_row")))


# The final review's I4: the callback closes a row only when the job cannot.
_NOT_DONE_TERMINAL = {
    "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_UPDATED",
    "JOB_STATE_DRAINED"
}
_MAY_STILL_WRITE = ("JOB_STATE_RUNNING", "JOB_STATE_PENDING",
                    "JOB_STATE_QUEUED", "JOB_STATE_CANCELLING",
                    "JOB_STATE_DRAINING", "JOB_STATE_STOPPED",
                    "JOB_STATE_UNKNOWN", "JOB_STATE_DONE")


def _pure(tree: ast.Module, *names: str) -> dict:
  """The named module-level constants and functions of the DAG, executed
  on their own: they import nothing, so a pure decision can be called
  without Airflow (the file itself is never imported)."""
  body = [
      n for n in tree.body
      if (isinstance(n, ast.FunctionDef) and n.name in names) or
      (isinstance(n, ast.Assign) and any(
          isinstance(t, ast.Name) and t.id in names for t in n.targets))
  ]
  assert len(body) == len(names), names
  scope: dict = {}
  code = compile(
      ast.Module(body=body, type_ignores=[]), str(EVALUATION_DAG), "exec")
  exec(code, scope)  # pylint: disable=exec-used  # two pure definitions of a file Airflow alone can import
  return scope


def test_the_callback_closes_a_row_only_when_the_job_cannot():
  """A task can fail on the Airflow side while its Dataflow job runs on:
  a FAILED row written then is followed by the job's own FINAL — two
  terminal rows, and in one ordering FAILED is the latest for a run that
  succeeded. FAILED is written only for a job in a terminal state other
  than done, or when the launch pushed no job id (no job runs)."""
  scope = _pure(
      _tree(EVALUATION_DAG), "_ENDED_NOT_DONE", "_callback_closes_row")
  assert set(scope["_ENDED_NOT_DONE"]) == _NOT_DONE_TERMINAL
  closes = scope["_callback_closes_row"]
  for state in _NOT_DONE_TERMINAL:
    assert closes("2026-09-14_01_00_00-777", state) is True, state
  for state in (*_MAY_STILL_WRITE, None):  # None: the state was not read
    assert closes("2026-09-14_01_00_00-777", state) is False, state
  # no job id in XCom: the launch itself failed, as before
  for state in (None, "JOB_STATE_RUNNING"):
    assert closes("", state) is True


def test_the_callback_reads_the_job_state_before_it_inserts():
  tree = _tree(EVALUATION_DAG)
  callback = _function(tree, "_close_running_row")
  body = ast.unparse(callback)
  assert "xcom_pull(task_ids='start_evaluation')" in body
  (decision,) = [
      n for n in ast.walk(callback)
      if isinstance(n, ast.If) and "_callback_closes_row" in ast.unparse(n.test)
  ]
  assert isinstance(decision.test, ast.UnaryOp)  # `if not closes: return`
  assert isinstance(decision.test.op, ast.Not)
  assert isinstance(decision.body[-1], ast.Return)
  assert decision.body[-1].value is None  # nothing is written
  assert "logging." in ast.unparse(decision) and "still" in ast.unparse(
      decision)
  assert "_job_state(job_id)" in body
  insert = _one(callback, "BigQueryInsertJobOperator")
  assert decision.end_lineno < insert.lineno  # the state is read first


def test_the_job_state_comes_from_the_dataflow_hook_and_never_raises():
  reader = _function(_tree(EVALUATION_DAG), "_job_state")
  body = ast.unparse(reader)
  assert "from airflow.providers.google.cloud.hooks.dataflow import DataflowHook" in body
  get_job = _one(reader, "get_job")
  assert {k.arg for k in get_job.keywords
         } == {"job_id", "project_id", "location"}
  assert not get_job.args  # the hook's project fallback takes keywords only
  assert "currentState" in _strings(reader)
  # an unreadable state is "unknown" (None), which closes nothing
  (guard,) = [n for n in ast.walk(reader) if isinstance(n, ast.Try)]
  (handler,) = guard.handlers
  assert isinstance(handler.body[-1], ast.Return)
  assert ast.literal_eval(handler.body[-1].value) is None


# --------------------------------------------------------------------------- #
# The generation DAG's opt-in trigger
# --------------------------------------------------------------------------- #


def test_generation_dag_opt_in_param_defaults_off():
  param = _params(_tree(GENERATION_DAG))["run_evaluation"]
  assert ast.literal_eval(_kw(param, "default")) is False
  assert ast.literal_eval(_kw(param, "type")) == "boolean"


def test_generation_dag_trigger_sits_behind_the_short_circuit():
  tree = _tree(GENERATION_DAG)
  gate = _one(tree, "ShortCircuitOperator")
  assert "run_evaluation" in ast.unparse(
      _function(tree,
                _kw(gate, "python_callable").id))  # type: ignore[attr-defined]
  trigger = _one(tree, "TriggerDagRunOperator")
  assert (ast.literal_eval(_kw(
      trigger, "trigger_dag_id")) == "sdfb_evaluation_framework")
  conf = _kw(trigger, "conf")
  assert isinstance(conf, ast.Dict)
  entries = {
      k.value: v
      for k, v in zip(conf.keys, conf.values, strict=True)
      if isinstance(k, ast.Constant)
  }
  assert set(entries) == {"generation_job_id", "wait_for_generation", "trigger"}
  assert ast.literal_eval(entries["generation_job_id"]) == (
      "{{ ti.xcom_pull(task_ids='start_sdfb')['id'] }}")
  assert ast.literal_eval(entries["wait_for_generation"]) is True
  assert ast.literal_eval(entries["trigger"]) == "chained"
  assert _chains(tree) == [[
      "start_sdfb", "run_evaluation_gate", "trigger_evaluation"
  ]]


def test_generation_dag_default_path_is_unchanged():
  tree = _tree(GENERATION_DAG)
  # Still one launch operator, named as before, still not waiting, and
  # nothing is upstream of it: the new tasks only hang off its end.
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  assert ast.unparse(_kw(start, "task_id")) == "f'start_{app_name}'"
  assert ast.literal_eval(_kw(start, "wait_until_finished")) is False
  assert ast.literal_eval(_kw(start, "do_xcom_push")) is True
  assert {b for _, b in _edges(tree)
         } == {"run_evaluation_gate", "trigger_evaluation"}
  assert "evaluation" not in ast.unparse(_entry(start, "launchParameter"))
