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
# The repository's ONE template: both DAGs launch from it.
MAIN_METADATA = REPO_ROOT / "docker" / "flex_template_metadata.json"

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


def _pure(tree: ast.Module, *names: str, path: Path = EVALUATION_DAG) -> dict:
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
  code = compile(ast.Module(body=body, type_ignores=[]), str(path), "exec")
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
  # no job id: the state was never read, so nothing is written, whatever
  # state is passed (a failed task pushes no return value to XCom)
  for state in (None, "JOB_STATE_RUNNING", *_NOT_DONE_TERMINAL):
    assert closes("", state) is False, state
    assert closes(None, state) is False, state


def test_the_job_id_comes_from_the_return_value_then_the_launch_config():
  scope = _pure(_tree(EVALUATION_DAG), "_launched_job_id")
  found = scope["_launched_job_id"]
  assert found({"id": "A"}, {"job_id": "B"}) == "A"  # the return value first
  assert found(None, {"job_id": "B"}) == "B"  # a failed task: only the config
  assert found({"id": ""}, {"job_id": "B"}) == "B"
  assert found({}, None) == "" and found(None, None) == ""
  assert found("not a dict", 7) == "" and found({"id": 5}, {"job_id": 6}) == ""


def test_the_callback_reads_the_job_state_before_it_inserts():
  tree = _tree(EVALUATION_DAG)
  callback = _function(tree, "_close_running_row")
  body = ast.unparse(callback)
  assert "xcom_pull(task_ids='start_evaluation')" in body
  # the fallback key the launch operator pushes before it can fail
  assert "key='dataflow_job_config'" in body
  # the decision's arguments are the pulled id and the state read for it
  (assign_id,) = [
      n for n in ast.walk(callback)
      if isinstance(n, ast.Assign) and n.targets[0].id == "job_id"
  ]
  assert "_launched_job_id(" in ast.unparse(assign_id.value)
  (assign_state,) = [
      n for n in ast.walk(callback)
      if isinstance(n, ast.Assign) and n.targets[0].id == "state"
  ]
  assert ast.unparse(assign_state.value) == "_job_state(job_id)"
  (no_id,) = [
      n for n in ast.walk(callback)
      if isinstance(n, ast.If) and ast.unparse(n.test) == "not job_id"
  ]
  assert isinstance(no_id.body[-1], ast.Return) and no_id.body[-1].value is None
  assert "logging.warning" in ast.unparse(no_id)
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
  (call,) = [
      n for n in ast.walk(decision.test) if isinstance(n, ast.Call) and
      ast.unparse(n.func) == "_callback_closes_row"
  ]
  assert [ast.unparse(a) for a in call.args] == ["job_id", "state"]
  insert = _one(callback, "BigQueryInsertJobOperator")
  # no id is checked, then the state read, then decided, before any write
  assert no_id.end_lineno < assign_state.lineno < decision.lineno
  assert decision.end_lineno < insert.lineno


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
# The generation DAG's opt-in evaluation, chained inside the DAG itself
# --------------------------------------------------------------------------- #
#
#   start_sdfb >> run_evaluation_gate >> wait_for_generation >> trigger_evaluation
#
# One import of composer/synthetic_beam_bigquery.py is all the deployment
# needs: the evaluation is launched by this DAG, from the SAME template as the
# generation, with `sdfb_job=evaluation` (Ruling R118). The gate is off by
# default, and then only `start_sdfb` runs, as before.

_JOB_ID_XCOM = "{{ ti.xcom_pull(task_ids='start_sdfb')['id'] }}"
_CHAIN = [
    "start_sdfb", "run_evaluation_gate", "wait_for_generation",
    "trigger_evaluation"
]
# What workflow 3 substitutes at import. It refuses a file in which any other
# {{UPPERCASE}} marker is left, so a new one would break the import.
_GENERATION_MARKERS = {
    "DAG_VERSION", "ENGINE", "ENV", "GCS_DATAFLOW_STAGING",
    "GCS_DATAFLOW_TEMPLATES", "GPU", "PROJECT_VERSION", "SDFB_DDL_URI",
    "SDFB_DEFAULT_TABLE_FQN", "SDFB_DLQ_TABLE", "SDFB_EMBEDDER_URI",
    "SDFB_FREETEXT_POOLS_TABLE", "SDFB_LANDING_TABLE", "SDFB_MODEL_URI",
    "SDFB_RAG_CHUNKS_TABLE", "SDFB_SOURCE_STATS_TABLE",
    "SDFB_VALIDATION_RUNS_TABLE", "WRITE_DISPOSITION"
}
_HOUSE_VARIABLES = {
    "PROJECT_ID", "REGION", "DATAFLOW_SUBNET", "SA_DATAFLOW",
    "DATAFLOW_NETWORK_TAGS"
}
_GPU_ONLY = ("accelerator", "nvidia", "reservation",
             "no_use_multiple_sdk_containers", "params.gpu",
             "params.client_type", "g2-standard", "n1-standard")


def _launch(tree: ast.Module, variable: str) -> ast.Call:
  """The flex-template launch assigned to `variable` at DAG level."""
  (call,) = [
      n.value
      for n in ast.walk(tree)
      if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and
      _name(n.value.func) == "DataflowStartFlexTemplateOperator" and
      [t.id for t in n.targets if isinstance(t, ast.Name)] == [variable]
  ]
  return call


def _value(tree: ast.Module, node: ast.expr):
  """`node` as a Python value; a name is read from its one module-level
  assignment (the DAG types a value once and uses it twice)."""
  if isinstance(node, ast.Name):
    (assigned,) = [
        n.value for n in tree.body if isinstance(n, ast.Assign) and
        [t.id for t in n.targets if isinstance(t, ast.Name)] == [node.id]
    ]
    node = assigned
  return ast.literal_eval(node)


def _variables(tree: ast.Module) -> set[str]:
  return {
      ast.literal_eval(c.args[0])
      for c in _calls(tree, "get")
      if isinstance(c.func, ast.Attribute) and
      isinstance(c.func.value, ast.Name) and c.func.value.id == "Variable"
  }


def _markers(path: Path) -> set[str]:
  return set(re.findall(r"\{\{([A-Z_]+)\}\}", path.read_text(encoding="utf-8")))


def _main_metadata_names() -> set[str]:
  return {
      p["name"] for p in json.loads(MAIN_METADATA.read_text(
          encoding="utf-8"))["parameters"]
  }


def test_generation_dag_opt_in_param_defaults_off():
  param = _params(_tree(GENERATION_DAG))["run_evaluation"]
  assert ast.literal_eval(_kw(param, "default")) is False
  assert ast.literal_eval(_kw(param, "type")) == "boolean"


def test_generation_dag_chain_is_four_tasks_of_this_dag():
  tree = _tree(GENERATION_DAG)
  assert _chains(tree) == [_CHAIN]
  task_ids = {}
  for node in ast.walk(tree):
    if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
      continue
    if [k for k in node.value.keywords if k.arg == "task_id"]:
      (target,) = node.targets
      assert isinstance(target, ast.Name)
      task_ids[target.id] = ast.unparse(_kw(node.value, "task_id"))
  assert task_ids == {
      "start_sdfb": "f'start_{app_name}'",
      "run_evaluation_gate": "'run_evaluation_gate'",
      "wait_for_generation": "'wait_for_generation'",
      "trigger_evaluation": "'trigger_evaluation'",
  }
  (app_name,) = [
      n.value for n in tree.body if isinstance(n, ast.Assign) and
      [t.id for t in n.targets if isinstance(t, ast.Name)] == ["app_name"]
  ]
  assert ast.literal_eval(app_name) == "sdfb"  # the XCom pulls 'start_sdfb'


def test_generation_dag_triggers_no_other_dag():
  """No second DAG import: nothing here imports a trigger operator or
  names the standalone evaluation DAG's id (its file is named once, in
  the header, as the path that closes a RUNNING row)."""
  tree = _tree(GENERATION_DAG)
  assert not _calls(tree, "TriggerDagRunOperator")
  dag_id = ast.literal_eval(_kw(_one(_tree(EVALUATION_DAG), "DAG"), "dag_id"))
  text = GENERATION_DAG.read_text(encoding="utf-8")
  for absent in ("TriggerDagRunOperator", "trigger_dagrun", dag_id):
    assert absent not in text, absent
  code = ast.unparse(ast.Module(body=tree.body[1:],
                                type_ignores=[]))  # without the header
  assert "evaluation_framework" not in code


def test_generation_dag_gate_is_the_opt_in_short_circuit():
  tree = _tree(GENERATION_DAG)
  gate = _one(tree, "ShortCircuitOperator")
  callable_name = _kw(gate, "python_callable")
  assert isinstance(callable_name, ast.Name)
  enabled = _pure(tree, callable_name.id, path=GENERATION_DAG)[callable_name.id]
  assert enabled({"run_evaluation": False}) is False
  assert enabled({"run_evaluation": True}) is True
  # the default: a skipped gate skips EVERYTHING downstream of it
  assert {k.arg for k in gate.keywords} == {"task_id", "python_callable"}
  sensor = _one(tree, "DataflowJobStatusSensor")
  trigger = _launch(tree, "trigger_evaluation")
  for downstream in (sensor, trigger):
    assert "trigger_rule" not in {k.arg for k in downstream.keywords}


def test_generation_dag_waits_in_reschedule_mode_and_needs_no_triggerer():
  tree = _tree(GENERATION_DAG)
  sensor = _one(tree, "DataflowJobStatusSensor")
  assert ast.literal_eval(_kw(sensor,
                              "expected_statuses")) == {"JOB_STATE_DONE"}
  assert ast.literal_eval(_kw(sensor, "mode")) == "reschedule"
  # stated, so a deployment-wide `default_deferrable` cannot turn it on
  assert ast.literal_eval(_kw(sensor, "deferrable")) is False
  assert _value(tree, _kw(sensor, "poke_interval")) >= 60
  hours = _value(tree, _kw(sensor, "timeout")) / 3600
  assert 12 <= hours <= 48  # long enough for a generation run, and finite
  assert _value(tree, _kw(sensor, "job_id")) == _JOB_ID_XCOM
  start = _launch(tree, "start_sdfb")
  for keyword in ("project_id", "location"):
    assert ast.unparse(_kw(sensor, keyword)) == ast.unparse(_kw(start, keyword))


def test_trigger_evaluation_launches_from_the_generation_template():
  tree = _tree(GENERATION_DAG)
  start, trigger = _launch(tree, "start_sdfb"), _launch(tree,
                                                        "trigger_evaluation")
  assert ast.unparse(_entry(trigger, "containerSpecGcsPath")) == ast.unparse(
      _entry(start, "containerSpecGcsPath"))
  for keyword in ("project_id", "location"):
    assert ast.unparse(_kw(trigger,
                           keyword)) == ast.unparse(_kw(start, keyword))
  # as the generation launch: submitted, not waited for. The evaluation job
  # writes its own FINAL row.
  assert ast.literal_eval(_kw(trigger, "wait_until_finished")) is False
  assert ast.literal_eval(_kw(trigger, "do_xcom_push")) is True
  assert "deferrable" not in {k.arg for k in trigger.keywords}
  # its own job name, not the generation job's
  assert ast.unparse(_entry(trigger, "jobName")) != ast.unparse(
      _entry(start, "jobName"))
  assert "evaluation" in ast.unparse(_entry(trigger, "jobName"))


def test_trigger_evaluation_parameters_select_the_evaluator_for_this_job():
  tree = _tree(GENERATION_DAG)
  parameters = _entry(_launch(tree, "trigger_evaluation"), "parameters")
  assert isinstance(parameters, ast.Dict)
  keys = [k.value for k in parameters.keys if isinstance(k, ast.Constant)]
  assert len(keys) == len(parameters.keys), "no ** unpacking in parameters"
  values = dict(zip(keys, parameters.values, strict=True))
  assert set(values) == {
      "sdfb_job", "job_id", "trigger", "mode", "output_dataset", "disk_size_gb"
  }
  assert _value(tree, values["sdfb_job"]) == "evaluation"
  assert _value(tree, values["job_id"]) == _JOB_ID_XCOM
  assert _value(tree, values["trigger"]) == "chained"
  assert _value(tree, values["mode"]) == "{{ params.evaluation_mode }}"
  assert _value(
      tree,
      values["output_dataset"]) == "{{ params.evaluation_output_dataset }}"
  # the same image as generation: the same boot disk, and for the reason
  # run_pipeline pins it (the evaluator pins none)
  assert int(_value(tree, values["disk_size_gb"])) >= 100
  # every key is a parameter of the ONE template, and of the evaluator's own
  assert set(values) <= _main_metadata_names()
  assert set(values) <= {
      p["name"] for p in json.loads(METADATA.read_text())["parameters"]
  }
  assert not set(values) & set(_NEVER_PASSED)
  declared = set(_params(tree))
  for key, value in values.items():
    refs = re.findall(r"params\.(\w+)", str(_value(tree, value)))
    assert set(refs) <= declared, (key, refs)


def test_trigger_evaluation_is_a_cpu_job_in_the_generation_jobs_network():
  tree = _tree(GENERATION_DAG)
  start, trigger = _launch(tree, "start_sdfb"), _launch(tree,
                                                        "trigger_evaluation")
  ours, theirs = _entry(trigger, "environment"), _entry(start, "environment")
  for key in ("tempLocation", "stagingLocation", "subnetwork",
              "ipConfiguration", "serviceAccountEmail", "workerRegion",
              "additionalUserLabels"):
    assert ast.unparse(_entry(ours, key)) == ast.unparse(_entry(theirs,
                                                                key)), key
  experiments = _entry(ours, "additionalExperiments")
  assert isinstance(experiments, ast.List)
  assert [e.value for e in experiments.elts if isinstance(e, ast.Constant)
         ] == ["use_runner_v2", "enable_secure_boot"]
  (starred,) = [e for e in experiments.elts if isinstance(e, ast.Starred)]
  assert ast.unparse(starred.value) == "network_tag_experiments"
  assert ast.unparse(starred.value) in ast.unparse(
      _entry(theirs, "additionalExperiments"))
  launch = ast.unparse(_entry(trigger, "launchParameter"))
  for gpu_only in (*_GPU_ONLY, "enable_data_sampling", "sdk_container_image",
                   "fail_on"):
    assert gpu_only not in launch, gpu_only
  assert ast.literal_eval(_entry(
      ours, "machineType")) == "{{ params.evaluation_machine_type }}"
  assert ast.literal_eval(_entry(
      ours, "maxWorkers")) == "{{ params.evaluation_max_workers }}"


def test_generation_dag_gains_four_evaluation_params_and_nothing_else():
  params = _params(_tree(GENERATION_DAG))
  about_evaluation = {n for n in params if "evaluation" in n}
  assert about_evaluation == {
      "run_evaluation", "evaluation_mode", "evaluation_machine_type",
      "evaluation_max_workers", "evaluation_output_dataset"
  }

  def default(name: str):
    return ast.literal_eval(_kw(params[name], "default"))

  assert default("evaluation_mode") == ""  # the evaluator's own default
  assert ast.literal_eval(_kw(params["evaluation_mode"],
                              "enum")) == ["", "exact", "sampled"]
  assert default("evaluation_machine_type") == "e2-standard-8"
  assert default("evaluation_max_workers") == 4
  assert ast.literal_eval(_kw(params["evaluation_max_workers"],
                              "type")) == "integer"
  assert default("evaluation_output_dataset") == "synthetic_data_quality"


def test_generation_dag_needs_no_new_marker_and_no_new_variable():
  assert _markers(GENERATION_DAG) == _GENERATION_MARKERS
  assert _variables(_tree(GENERATION_DAG)) == _HOUSE_VARIABLES


def _as_imported(path: Path, **values: str) -> str:
  """The DAG file as workflow 3 leaves it: every {{MARKER}} replaced (the
  ones not named get a placeholder)."""
  return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: values.get(m.group(1), "x"),
                path.read_text(encoding="utf-8"))


def test_generation_dag_is_valid_python_once_its_markers_are_substituted():
  text = _as_imported(GENERATION_DAG, PROJECT_VERSION="0.5.3")
  assert not re.search(r"\{\{[A-Z_]+\}\}", text)  # the workflow's own check
  compile(text, str(GENERATION_DAG), "exec")


@pytest.mark.parametrize("version,expected", [
    ("latest", "sdfb-evaluation-vlatest"),
    ("0.5.3", "sdfb-evaluation-v0-5-3"),
    ("Feature-Branch-With-A-Long-Name-0a1b2c3",
     "sdfb-evaluation-vfeature-branch-with-a"),
])
def test_evaluation_job_name_is_a_dataflow_job_name(version, expected):
  """Lowercase letters, digits and hyphens, and short enough that the
  run's timestamp (16 characters with its hyphen) and the suffix the
  operator may append (9) still fit Dataflow's 63."""
  tree = ast.parse(_as_imported(GENERATION_DAG, PROJECT_VERSION=version))
  name = _pure(
      tree,
      "app_name",
      "project_version",
      "_evaluation_job_base",
      "evaluation_job_name",
      path=GENERATION_DAG)["evaluation_job_name"]
  assert name == expected
  assert re.fullmatch(r"[a-z][-a-z0-9]*[a-z0-9]", name)
  assert len(name) + 16 + 9 <= 63
  job_name = _entry(_launch(tree, "trigger_evaluation"), "jobName")
  assert ast.unparse(job_name) == (
      "f'{evaluation_job_name}-{{{{ ts_nodash | lower }}}}'")


def test_generation_dag_default_path_is_unchanged():
  """With `run_evaluation` False the gate skips the rest, so the default
  path is `start_sdfb` alone. Its call is pinned whole, against the commit
  before this chain, in the generator's own surface test
  (packages/sdfb-tests/tests/unit/docker/test_launch_surfaces.py); here:
  nothing is upstream of it and nothing of the evaluation is inside it."""
  tree = _tree(GENERATION_DAG)
  start = _launch(tree, "start_sdfb")
  assert ast.unparse(_kw(start, "task_id")) == "f'start_{app_name}'"
  assert ast.literal_eval(_kw(start, "wait_until_finished")) is False
  assert ast.literal_eval(_kw(start, "do_xcom_push")) is True
  assert {b for _, b in _edges(tree)} == set(_CHAIN[1:])
  launch = ast.unparse(_entry(start, "launchParameter"))
  assert "evaluation" not in launch and "sdfb_job" not in launch
