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
"""Static tests for the two Composer DAGs that launch an evaluation.

    composer/synthetic_beam_bigquery.py   the generation DAG: with
                                          `run_evaluation` it waits for its
                                          own job and launches the evaluation
                                          itself (Ruling R118)
    composer/evaluation_framework.py      the standalone DAG: one evaluation
                                          per run, for a job id, a run id or
                                          tables (Task 29)

Both launch from the repository's ONE template, with `sdfb_job=evaluation`.

Airflow is not a dependency here, so the DAG files are read with ``ast`` and
never imported; a pure function of a DAG is executed on its own. The files
live at the repository root (``composer/``); when the package is copied out
as a standalone unit they are absent and the whole module skips.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import types
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


# --------------------------------------------------------------------------- #
# The evaluation DAG
# --------------------------------------------------------------------------- #


def test_dag_id_and_manual_schedule():
  tree = _tree(EVALUATION_DAG)
  dag = _one(tree, "DAG")
  assert _value(tree, _kw(dag, "dag_id")) == "sdfb_evaluation_framework"
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


def _task(tree: ast.Module, variable: str) -> ast.Call:
  """The operator assigned to `variable` at DAG level."""
  (call,) = [
      n.value
      for n in ast.walk(tree)
      if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and
      [t.id for t in n.targets if isinstance(t, ast.Name)] == [variable] and
      [k for k in n.value.keywords if k.arg == "task_id"]
  ]
  return call


def test_wait_for_generation_is_a_deferrable_sensor_behind_a_short_circuit():
  tree = _tree(EVALUATION_DAG)
  sensor = _one(tree, "DataflowJobStatusSensor")
  assert ast.literal_eval(_kw(sensor,
                              "expected_statuses")) == {"JOB_STATE_DONE"}
  assert ast.literal_eval(_kw(sensor, "deferrable")) is True
  gate = _task(tree, "wait_gate")
  assert _name(gate.func) == "ShortCircuitOperator"
  callable_name = _kw(gate, "python_callable")
  assert isinstance(callable_name, ast.Name)
  assert "wait_for_generation" in ast.unparse(_function(tree, callable_name.id))
  edges = _edges(tree)
  assert ("wait_gate", "wait_for_generation_job") in edges
  assert ("wait_for_generation_job", "start_evaluation") in edges
  # The skipped branch (wait_for_generation false) still reaches the launch:
  # the task ahead of the gate is also directly upstream of it.
  assert ("fan_out", "wait_gate") in edges
  assert ("fan_out", "start_evaluation") in edges
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  assert (ast.literal_eval(_kw(
      start, "trigger_rule")) == "none_failed_min_one_success")


def _assigned(tree: ast.Module, name: str) -> ast.expr:
  (value,) = [
      n.value for n in tree.body if isinstance(n, ast.Assign) and
      [t.id for t in n.targets if isinstance(t, ast.Name)] == [name]
  ]
  return value


def test_launch_points_at_the_one_template_the_generation_dag_launches():
  """No evaluator template to build and no marker of its own: the path is
  the generation DAG's, typed the same way from {{PROJECT_VERSION}}."""
  tree, generation = _tree(EVALUATION_DAG), _tree(GENERATION_DAG)
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  spec = ast.unparse(_entry(start, "containerSpecGcsPath"))
  assert spec == ast.unparse(
      _entry(_launch(generation, "start_sdfb"), "containerSpecGcsPath"))
  assert "flex_template" in spec and "synthetic/" in spec
  for name in ("flex_template", "project_version", "templates_path"):
    assert ast.unparse(_assigned(tree, name)) == ast.unparse(
        _assigned(generation, name)), name
  assert ast.unparse(_assigned(
      tree, "flex_template")) == "f'sdfb-{project_version}-template.json'"
  assert ast.literal_eval(_assigned(
      tree, "project_version")) == "{{" + "PROJECT_VERSION" + "}}"


def test_the_import_workflow_needs_no_marker_it_does_not_already_substitute():
  markers = _markers(EVALUATION_DAG)
  assert markers == {
      "PROJECT_VERSION", "ENV", "GCS_DATAFLOW_STAGING", "GCS_DATAFLOW_TEMPLATES"
  }
  assert markers <= _GENERATION_MARKERS
  assert _variables(_tree(EVALUATION_DAG)) == _HOUSE_VARIABLES


def test_the_evaluator_version_marker_is_gone_from_the_repository():
  """Nothing substitutes it any more, and an unsubstituted marker would be
  the DAG's version. Decision records and release reports keep their
  history, as for every removed name (.github/drift-check.txt)."""
  marker = "{{" + "EVALUATOR_VERSION" + "}}"  # spelled apart: this file is scanned
  listed = subprocess.run(
      ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
      capture_output=True,
      check=False)
  if listed.returncode != 0:
    pytest.skip("not a git checkout: the tracked files cannot be listed")
  history = ("docs/adr/", "docs/releases/", ".github/drift-check.txt")
  holders = []
  for name in listed.stdout.decode().split("\0"):
    path = REPO_ROOT / name
    if not name or name.startswith(history) or not path.is_file():
      continue
    if marker.encode() in path.read_bytes():
      holders.append(name)
  assert not holders
  patterns = (REPO_ROOT / ".github" /
              "drift-check.txt").read_text(encoding="utf-8").splitlines()
  assert marker in patterns


def test_header_says_how_to_launch_from_an_evaluator_only_template():
  docstring = ast.get_docstring(_tree(EVALUATION_DAG)) or ""
  for said in ("flex_template", "sdfb-evaluation-", "build_flex_template.sh",
               "sdfb_job"):
    assert said in docstring, said


def test_evaluation_dag_is_valid_python_once_its_markers_are_substituted():
  text = _as_imported(EVALUATION_DAG, PROJECT_VERSION="latest")
  assert not re.search(r"\{\{[A-Z_]+\}\}", text)
  compile(text, str(EVALUATION_DAG), "exec")
  prefix = _pure(
      ast.parse(text),
      "app_name",
      "project_version",
      "_job_name_prefix",
      path=EVALUATION_DAG)["_job_name_prefix"]
  assert prefix == "sdfb-evaluation-vlatest"


def test_launch_parameters_are_metadata_names_from_params_or_constants():
  tree = _tree(EVALUATION_DAG)
  start = _one(tree, "DataflowStartFlexTemplateOperator")
  parameters = _entry(start, "parameters")
  assert isinstance(parameters, ast.Dict)
  keys = [k.value for k in parameters.keys if isinstance(k, ast.Constant)]
  assert len(keys) == len(parameters.keys), "no ** unpacking in parameters"
  names = {p["name"] for p in json.loads(METADATA.read_text())["parameters"]}
  assert set(keys) <= names
  # the launcher drops a template parameter named `job_id`
  assert "generation_job_id" in keys and "job_id" not in keys
  # ... and of the one template this DAG launches from by default
  assert set(keys) <= _main_metadata_names()
  assert not set(keys) & set(_NEVER_PASSED)
  declared = set(_params(tree))
  for key, value in zip(keys, parameters.values, strict=True):
    refs = re.findall(r"params\.(\w+)", ast.unparse(value))
    # every value is a DAG Param reference or a string constant (typed in
    # place, or once at module level)
    assert refs or isinstance(_value(tree, value), str), key
    assert set(refs) <= declared, (key, refs)
  assert _entry(parameters, "trigger") is not None
  assert "lower" in ast.unparse(_entry(parameters, "allow_contaminated"))


def test_launch_selects_the_evaluator_and_sizes_the_shared_images_disk():
  tree = _tree(EVALUATION_DAG)
  parameters = _entry(
      _one(tree, "DataflowStartFlexTemplateOperator"), "parameters")
  # the one image's entry runs generation unless told otherwise
  assert _value(tree, _entry(parameters, "sdfb_job")) == "evaluation"
  # the same number the generation DAG's chained evaluation passes
  disk = _value(tree, _entry(parameters, "disk_size_gb"))
  generation = _tree(GENERATION_DAG)
  assert disk == _value(
      generation,
      _entry(_launch(generation, "trigger_evaluation"), "disk_size_gb"))


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
  # the statement runs through the hook: an operator is never executed inside
  # a callable (recent Airflow versions warn about or refuse it)
  assert "BigQueryHook" in body and "BigQueryInsertJobOperator" not in body
  assert ".execute(" not in body
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
  insert = _one(callback, "insert_job")
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
# Several generation jobs: the DAG re-triggers itself once per id (R119)
# --------------------------------------------------------------------------- #
#
#   begin ─► fan_out ─┬─► wait_gate ─► wait_for_generation_job ─► start_evaluation
#                     └──────────────────────────────────────────────▲
#
#   generation_job_ids   generation_job_id   fan_out
#   ───────────────────  ──────────────────  ─────────────────────────────────
#   empty                empty or one id     passes: the run is the
#                                            single-target run it always was
#   one id or more       empty or one id     triggers one run of THIS DAG per
#                                            id (the list, then the single
#                                            id; blanks and repeats dropped),
#                                            then skips the rest of this run
#
# A child run carries `generation_job_id` and an empty list, so it takes the
# first row: the sensor, the launch and the failure callback never see a list.

_PARAMS = {
    "generation_job_id": "",
    "generation_job_ids": [],
    "run_id": "",
    "tables": "",
    "landing_dataset": "",
    "reference_dataset": "",
    "relationships_uri": "",
    "mode": "sampled",
    "allow_contaminated": False,
    "output_dataset": "synthetic_data_quality",
    "trigger": "composer",
    "wait_for_generation": True,
    "machine_type": "e2-standard-8",
    "max_workers": 4,
}
_A, _B, _C = ("2026-09-14_01_00_00-111", "2026-09-14_02_00_00-222",
              "2026-09-14_03_00_00-333")


def _fan_out_scope() -> dict:
  return _pure(
      _tree(EVALUATION_DAG), "DAG_ID", "_fan_out_confs", "_fan_out_run_id",
      "_fan_out")


def _confs(**given) -> list[dict]:
  return _fan_out_scope()["_fan_out_confs"]({**_PARAMS, **given})


def test_the_list_param_is_an_array_of_strings_and_empty_by_default():
  params = _params(_tree(EVALUATION_DAG))
  assert set(_PARAMS) == set(params), "the fixture mirrors the DAG's params"
  listed = params["generation_job_ids"]
  assert ast.literal_eval(_kw(listed, "default")) == []
  assert ast.literal_eval(_kw(listed, "type")) == "array"
  assert ast.literal_eval(_kw(listed, "items")) == {"type": "string"}
  description = ast.literal_eval(_kw(listed, "description"))
  assert "own run" in description and "one Dataflow job per id" in description
  # the one-id param stays as it was
  single = params["generation_job_id"]
  assert ast.literal_eval(_kw(single, "default")) == ""
  assert ast.literal_eval(_kw(single, "type")) == "string"


@pytest.mark.parametrize("given", [
    {},
    {
        "generation_job_id": _A
    },
    {
        "generation_job_ids": None
    },
    {
        "generation_job_ids": ["", "   "]
    },
    {
        "generation_job_ids": ["", " "],
        "generation_job_id": _A
    },
    {
        "run_id": "thelook-0913-a1b2c3"
    },
    {
        "tables": "orders,order_items"
    },
])
def test_without_a_list_nothing_fans_out(given):
  assert _confs(**given) == []


@pytest.mark.parametrize("given,ids", [
    ({
        "generation_job_ids": [_A, _B, _C]
    }, [_A, _B, _C]),
    ({
        "generation_job_ids": [_C, _A]
    }, [_C, _A]),
    ({
        "generation_job_ids": [_A, "", _B, f"  {_A} ", "  ", _B, _C]
    }, [_A, _B, _C]),
    ({
        "generation_job_ids": [_A, _B],
        "generation_job_id": _C
    }, [_A, _B, _C]),
    ({
        "generation_job_ids": [_A, _B],
        "generation_job_id": _A
    }, [_A, _B]),
    ({
        "generation_job_ids": [_B],
        "generation_job_id": _A
    }, [_B, _A]),
    ({
        "generation_job_ids": [_A]
    }, [_A]),
    ({
        "generation_job_ids": [_A],
        "generation_job_id": f" {_A} "
    }, [_A]),
])
def test_one_conf_per_id_blanks_and_repeats_dropped_order_kept(given, ids):
  confs = _confs(**given)
  assert [conf["generation_job_id"] for conf in confs] == ids


def test_a_conf_is_the_runs_params_with_its_one_id_and_no_list():
  given = {
      "generation_job_ids": [_A, _B],
      "generation_job_id": _C,
      "mode": "exact",
      "wait_for_generation": True,
      "max_workers": 8,
      "trigger": "chained",
  }
  run = {**_PARAMS, **given}
  confs = _fan_out_scope()["_fan_out_confs"](run)
  assert len(confs) == 3
  for conf, job_id in zip(confs, [_A, _B, _C], strict=True):
    assert conf["generation_job_id"] == job_id
    assert conf["generation_job_ids"] == []  # a child never fans out again
    others = set(run) - {"generation_job_id", "generation_job_ids"}
    assert {
        name: conf[name] for name in others
    } == {
        name: run[name] for name in others
    }
    assert set(conf) == set(run)
    json.dumps(conf)  # what a trigger's conf must be
  # the run's own params are not touched
  assert run["generation_job_ids"] == [_A, _B]
  assert run["generation_job_id"] == _C
  # ... and a child's conf, fed back, proceeds on the single-target path
  for conf in confs:
    assert _fan_out_scope()["_fan_out_confs"](conf) == []


def test_child_run_ids_are_deterministic_unique_and_name_job_and_date():
  run_id = _fan_out_scope()["_fan_out_run_id"]
  first = run_id(_A, "20261006T101500")
  assert first == run_id(_A, "20261006T101500")
  assert _A in first and "20261006T101500" in first
  assert len(
      {first,
       run_id(_B, "20261006T101500"),
       run_id(_A, "20261007T101500")}) == 3
  # Airflow's default pattern for a run id it accepts
  assert re.fullmatch(r"[A-Za-z0-9_.~:+-]+", first)


def test_fan_out_is_the_first_task_and_skips_everything_when_it_fans_out():
  tree = _tree(EVALUATION_DAG)
  fan_out = _task(tree, "fan_out")
  assert _name(fan_out.func) == "ShortCircuitOperator"
  assert ast.literal_eval(_kw(fan_out, "task_id")) == "fan_out"
  callable_name = _kw(fan_out, "python_callable")
  assert isinstance(callable_name, ast.Name) and callable_name.id == "_fan_out"
  # the default: returning False skips EVERY downstream task, whatever its
  # trigger rule (the launch's own would otherwise let it run)
  assert {k.arg for k in fan_out.keywords} == {"task_id", "python_callable"}
  edges = _edges(tree)
  assert {b for a, b in edges if a == "begin"} == {"fan_out"}
  assert {a for a, b in edges if b == "fan_out"} == {"begin"}
  assert {b for a, b in edges if a == "fan_out"
         } == {"wait_gate", "start_evaluation"}


class _AlreadyExistsError(Exception):
  """Stands in for airflow.exceptions.DagRunAlreadyExists."""


def _stub_airflow(monkeypatch,
                  triggered: list,
                  existing: frozenset = frozenset()):
  """A recording `trigger_dag` under Airflow's module names: the callable
  imports it only when it has runs to trigger."""

  def trigger_dag(**kwargs):
    if kwargs["run_id"] in existing:
      raise _AlreadyExistsError(kwargs["run_id"])
    triggered.append(kwargs)

  exceptions = types.ModuleType("airflow.exceptions")
  exceptions.DagRunAlreadyExists = _AlreadyExistsError  # type: ignore[attr-defined]
  trigger = types.ModuleType("airflow.api.common.trigger_dag")
  trigger.trigger_dag = trigger_dag  # type: ignore[attr-defined]
  for name, module in (("airflow", types.ModuleType("airflow")),
                       ("airflow.exceptions", exceptions),
                       ("airflow.api", types.ModuleType("airflow.api")),
                       ("airflow.api.common",
                        types.ModuleType("airflow.api.common")),
                       ("airflow.api.common.trigger_dag", trigger)):
    monkeypatch.setitem(sys.modules, name, module)


def test_with_no_list_the_callable_passes_and_never_reaches_for_airflow(
    monkeypatch):
  for name in ("airflow", "airflow.exceptions", "airflow.api",
               "airflow.api.common", "airflow.api.common.trigger_dag"):
    monkeypatch.setitem(sys.modules, name, None)  # any import would raise
  fan_out = _fan_out_scope()["_fan_out"]
  assert fan_out({
      **_PARAMS, "generation_job_id": _A
  },
                 ts_nodash="20261006T101500") is True
  assert fan_out(dict(_PARAMS), ts_nodash="20261006T101500") is True


def test_with_a_list_the_callable_triggers_this_dag_once_per_id_and_stops(
    monkeypatch):
  triggered: list = []
  _stub_airflow(monkeypatch, triggered)
  scope = _fan_out_scope()
  params = {**_PARAMS, "generation_job_ids": [_A, _B], "mode": "exact"}
  context = {"ts_nodash": "20261006T101500", "task_instance": object()}
  assert scope["_fan_out"](params, **context) is False
  assert len(triggered) == 2
  tree = _tree(EVALUATION_DAG)
  own_id = _value(tree, _kw(_one(tree, "DAG"), "dag_id"))
  expected = scope["_fan_out_confs"](params)
  for kwargs, conf, job_id in zip(triggered, expected, [_A, _B], strict=True):
    assert kwargs["dag_id"] == own_id == scope["DAG_ID"]
    assert kwargs["conf"] == conf and conf["generation_job_id"] == job_id
    assert kwargs["run_id"] == scope["_fan_out_run_id"](job_id,
                                                        "20261006T101500")
    # two runs started in one second must not collide on the logical date
    assert kwargs["replace_microseconds"] is False
    assert set(kwargs) == {"dag_id", "run_id", "conf", "replace_microseconds"}
  assert len({kwargs["run_id"] for kwargs in triggered}) == 2


def test_the_callable_executes_no_operator_and_imports_airflows_function():
  body = ast.unparse(_function(_tree(EVALUATION_DAG), "_fan_out"))
  assert "TriggerDagRunOperator" not in body and ".execute(" not in body
  assert "from airflow.api.common.trigger_dag import trigger_dag" in body


@pytest.mark.parametrize("name,value", [("run_id", "r-1"),
                                        ("tables", "ds.a,ds.b")])
def test_a_list_with_another_target_fails_fast_with_one_message(
    monkeypatch, name, value):
  triggered: list = []
  _stub_airflow(monkeypatch, triggered)
  scope = _fan_out_scope()
  params = {**_PARAMS, "generation_job_ids": [_A, _B], name: value}
  for call in (lambda: scope["_fan_out_confs"]
               (params), lambda: scope["_fan_out"]
               (params, ts_nodash="20261006T101500")):
    with pytest.raises(ValueError) as info:
      call()
    message = str(info.value)
    assert "generation_job_ids" in message and name in message
  assert not triggered  # not one child run was started


def test_the_job_id_alone_or_blank_list_with_another_target_is_not_refused():
  scope = _fan_out_scope()
  assert scope["_fan_out_confs"]({
      **_PARAMS, "generation_job_ids": ["", " "],
      "run_id": "r-1"
  }) == []
  assert [
      c["generation_job_id"] for c in scope["_fan_out_confs"]({
          **_PARAMS, "generation_job_ids": [_A],
          "generation_job_id": _B
      })
  ] == [_A, _B]


def test_a_cleared_fan_out_does_not_evaluate_a_job_twice(monkeypatch):
  """The run ids are deterministic so that a re-run of the task finds the
  runs it already started: Airflow refuses a run id that exists, and the
  callable goes on to the ids that are left."""
  scope = _fan_out_scope()
  already = scope["_fan_out_run_id"](_A, "20261006T101500")
  triggered: list = []
  _stub_airflow(monkeypatch, triggered, existing=frozenset({already}))
  params = {**_PARAMS, "generation_job_ids": [_A, _B]}
  assert scope["_fan_out"](params, ts_nodash="20261006T101500") is False
  assert [kwargs["conf"]["generation_job_id"] for kwargs in triggered] == [_B]


def test_with_no_list_the_graph_is_the_one_before_the_fan_out():
  """`fan_out` stands where `begin` stood: with it folded back into
  `begin`, the edges are the four the DAG had."""
  folded = {("begin" if a == "fan_out" else a, "begin" if b == "fan_out" else b)
            for a, b in _edges(_tree(EVALUATION_DAG))}
  folded.discard(("begin", "begin"))
  assert folded == {
      ("begin", "wait_gate"),
      ("wait_gate", "wait_for_generation_job"),
      ("wait_for_generation_job", "start_evaluation"),
      ("begin", "start_evaluation"),
  }


def test_the_per_job_path_never_sees_the_list():
  """Sensor, launch, gate and failure callback are what they were for one
  id: their arguments are the same set, and none of them names the list."""
  tree = _tree(EVALUATION_DAG)
  arguments = {
      "begin": {"task_id"},
      "wait_gate": {
          "task_id", "python_callable", "ignore_downstream_trigger_rules"
      },
      "wait_for_generation_job": {
          "task_id", "job_id", "expected_statuses", "project_id", "location",
          "deferrable"
      },
      "start_evaluation": {
          "task_id", "project_id", "location", "body", "wait_until_finished",
          "deferrable", "do_xcom_push", "trigger_rule", "on_failure_callback"
      },
  }
  for variable, keywords in arguments.items():
    task = _task(tree, variable)
    assert {k.arg for k in task.keywords} == keywords, variable
    assert "generation_job_ids" not in ast.unparse(task), variable
  assert ast.literal_eval(
      _kw(_task(tree, "wait_for_generation_job"),
          "job_id")) == "{{ params.generation_job_id }}"
  for function in ("_wait_for_generation", "_close_running_row",
                   "_callback_closes_row", "_launched_job_id", "_job_state"):
    assert "generation_job_ids" not in ast.unparse(_function(tree, function))
  wait = _pure(tree, "_wait_for_generation")["_wait_for_generation"]
  assert wait({"wait_for_generation": True, "generation_job_id": _A}) is True
  assert wait({"wait_for_generation": True, "generation_job_id": ""}) is False
  assert wait({"wait_for_generation": False, "generation_job_id": _A}) is False


def test_runs_are_sequential_and_the_header_says_which_knob_changes_that():
  tree = _tree(EVALUATION_DAG)
  assert ast.literal_eval(_kw(_one(tree, "DAG"), "max_active_runs")) == 1
  docstring = ast.get_docstring(tree) or ""
  for said in ("generation_job_ids", "max_active_runs", "one after another",
               "has not been parsed or run by Airflow"):
    assert said in docstring, said


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
  evaluation = _tree(EVALUATION_DAG)
  dag_id = _value(evaluation, _kw(_one(evaluation, "DAG"), "dag_id"))
  assert dag_id
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
  # `deferrable` is a constructor argument only newer Google providers have:
  # passing it would take the whole DAG down on an older one. Not deferrable
  # is the default, so it is not passed at all.
  assert "deferrable" not in {k.arg for k in sensor.keywords}
  assert _value(tree, _kw(sensor, "poke_interval")) >= 60
  hours = _value(tree, _kw(sensor, "timeout")) / 3600
  assert 12 <= hours <= 48  # long enough for a generation run, and finite
  assert _value(tree, _kw(sensor, "job_id")) == _JOB_ID_XCOM
  start = _launch(tree, "start_sdfb")
  for keyword in ("project_id", "location"):
    assert ast.unparse(_kw(sensor, keyword)) == ast.unparse(_kw(start, keyword))


def test_the_new_tasks_pass_only_arguments_the_working_launch_or_airflow_has():
  """No new task may use a constructor argument start_sdfb does not have.

  The sensor's own (job_id, expected_statuses) are the provider sensor's
  long-standing arguments; mode, poke_interval and timeout are the base
  sensor's; task_id and python_callable are Airflow's.
  """
  tree = _tree(GENERATION_DAG)

  def names(call):
    return {k.arg for k in call.keywords}

  assert names(_one(tree,
                    "ShortCircuitOperator")) == {"task_id", "python_callable"}
  assert names(_one(tree, "DataflowJobStatusSensor")) == {
      "task_id", "job_id", "expected_statuses", "project_id", "location",
      "mode", "poke_interval", "timeout"
  }
  assert names(_launch(tree, "trigger_evaluation")) == names(
      _launch(tree, "start_sdfb"))


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
      "sdfb_job", "generation_job_id", "seed_table", "relationships_uri",
      "landing_dataset", "reference_dataset", "scope", "reference_rows_limit",
      "validation_runs_table", "trigger", "mode", "output_dataset",
      "disk_size_gb"
  }
  # a hand-named target has no write disposition: without this every table
  # would be planned as "not evaluated"
  assert _value(tree, values["scope"]) == "manual"
  assert _value(tree, values["sdfb_job"]) == "evaluation"
  # the tables come from the launched table and the model; the job id is the
  # launch's identity (the evaluator reads only the Dataflow job for it, R134),
  # the one the sensor waits on
  assert "job_id" not in values
  assert _value(tree, values["generation_job_id"]) == _JOB_ID_XCOM
  sensor = _one(tree, "DataflowJobStatusSensor")
  assert _value(tree,
                values["generation_job_id"]) == _value(tree,
                                                       _kw(sensor, "job_id"))
  seed = _value(tree, values["seed_table"])
  assert "params.table_fqn" in seed and "rsplit('.', 1)[-1]" in seed
  uri = _value(tree, values["relationships_uri"])
  assert "params.relationships_uri" in uri
  # an isolated generation is evaluated as one table: no model
  assert "params.generate_fk_relationships == 'true'" in uri
  assert uri.endswith("else '' }}")
  assert "SDFB_LANDING_TABLE" in _value(tree, values["landing_dataset"])
  source = _value(tree, values["reference_dataset"])
  assert "params.source_dataset" in source and "params.table_fqn" in source
  assert "source_dataset" in _params(tree)
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


def test_both_launches_pass_the_same_validation_runs_table():
  tree = _tree(GENERATION_DAG)
  generation = _entry(_launch(tree, "start_sdfb"), "parameters")
  evaluation = _entry(_launch(tree, "trigger_evaluation"), "parameters")
  given = _value(tree, _entry(generation, "validation_runs_table"))
  assert given
  assert _value(tree, _entry(evaluation, "validation_runs_table")) == given


def test_both_launches_pass_the_same_reference_rows_limit():
  tree = _tree(GENERATION_DAG)
  generation = _entry(_launch(tree, "start_sdfb"), "parameters")
  evaluation = _entry(_launch(tree, "trigger_evaluation"), "parameters")
  given = _value(tree, _entry(generation, "reference_rows_limit"))
  assert given.isdigit() and int(given) > 0
  assert _value(tree, _entry(evaluation, "reference_rows_limit")) == given


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
  # the quality dataset of the DAG's own validation-runs table, read in
  # Python: no new marker (the executed test below)
  assert _kw(params["evaluation_output_dataset"],
             "default") is not None and isinstance(
                 _kw(params["evaluation_output_dataset"], "default"), ast.Name)


@pytest.mark.parametrize("table,dataset", [
    ("proj.quality_ds.validation_runs", "quality_ds"),
    ("proj.synthetic_data_quality.validation_runs", "synthetic_data_quality"),
    ("quality_ds.validation_runs", "synthetic_data_quality"),
    ("{{SDFB_VALIDATION_RUNS_TABLE}}", "synthetic_data_quality"),
    ("", "synthetic_data_quality"),
])
def test_the_evaluation_dataset_defaults_to_the_validation_runs_dataset(
    table, dataset):
  """The default of `evaluation_output_dataset` is the dataset part of the
  DAG's `{{SDFB_VALIDATION_RUNS_TABLE}}` value (`project.dataset.table`),
  and `synthetic_data_quality` when the value has not three parts."""
  tree = _tree(GENERATION_DAG)
  default = _kw(_params(tree)["evaluation_output_dataset"], "default")
  assert isinstance(default, ast.Name)
  names = {
      "validation_runs_table", "_QUALIFIED_TABLE_PARTS",
      "_validation_runs_parts", default.id
  }
  body = [
      n for n in tree.body if isinstance(n, ast.Assign) and any(
          isinstance(t, ast.Name) and t.id in names for t in n.targets)
  ]
  scope: dict = {}
  for node in body:  # the table marker's own assignment, as the import sets it
    if node.targets[0].id == "validation_runs_table":
      scope["validation_runs_table"] = table
      continue
    exec(  # pylint: disable=exec-used  # a pure module-level assignment of the DAG file
        compile(
            ast.Module(body=[node], type_ignores=[]), str(GENERATION_DAG),
            "exec"), scope)
  assert scope[default.id] == dataset


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
