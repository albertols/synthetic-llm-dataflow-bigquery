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
"""The one generation DAG (`composer/synthetic_beam_bigquery.py`, ADR 0045
D6): `route` takes the explicit branch (master's chain) or the `tables=all`
branch (one mapped launch per batch), and every default comes from
`generator.yaml` overlaid with `sdfb_env.yaml` (ADR 0043).

Airflow is not a laptop dependency, so the file is read with `ast`; what it
computes is `composer/sdfb_batches.py`'s, run here on a deployed folder
(conftest.py's `deployed`). The real-Airflow run of the same file is
`scripts/composer/check_dags.py`: with `SDFB_DAG_BODIES` naming its output
(all nine scenarios, on a folder of `composer/*.py`, `generator.yaml` and
`sdfb_env.yaml`), `test_the_harness_run_is_the_plans` checks every scenario
against the plan's expectations, and test_launch_helpers.py compares its
explicit launches with master's.

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import ast
import copy
import difflib
import importlib.util
import itertools
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest
from sdfb_tests.model_fixtures import forest_model_text

_REPO = Path(__file__).resolve().parents[5]
_COMPOSER = _REPO / "composer"
_DAG = _COMPOSER / "synthetic_beam_bigquery.py"
_METADATA = _REPO / "docker" / "flex_template_metadata.json"
_MARKER = re.compile(r"\{\{[A-Z_]+\}\}")
_DATAFLOW_NAME = re.compile(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?")
# The harness output this module checks when it is set (module docstring).
HARNESS_OUTPUT_ENV = "SDFB_DAG_BODIES"
# Master's explicit chain, in order.
_EXPLICIT_CHAIN = ("start_sdfb", "run_evaluation_gate", "wait_for_generation",
                   "trigger_evaluation", "wait_for_evaluation_launch")
# The `all` branch's tasks.
_ALL_TASKS = ("batch_bodies", "start_batch", "collect_landed",
              "trigger_evaluation_batches", "wait_for_batch_evaluation_launch",
              "fail_if_a_batch_failed")
# What `route` pushes; every pull of a `route` key names one of them.
_ROUTE_KEYS = {
    "body", "bodies", "base_run_id", "batch_count", "relationships_uri",
    "batches", "banner", "mode"
}
_ROUTE_PULL = re.compile(r"task_ids=['\"]route['\"], key=['\"](\w+)['\"]")


def _module(name: str, path: Path):
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


M = _module("sdfb_batches", _COMPOSER / "sdfb_batches.py")
HARNESS = _module("check_dags",
                  _REPO / "scripts" / "composer" / "check_dags.py")
MARKERS = json.loads(
    (_REPO / "packages" / "sdfb-tests" / "tests" / "fixtures" /
     "config_parity" / "dag_markers.json").read_text(encoding="utf-8"))
DAG = ast.parse(_DAG.read_text(encoding="utf-8"))


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


def _assignments() -> dict[str, ast.expr]:
  return {
      node.targets[0].id: node.value
      for node in DAG.body
      if isinstance(node, ast.Assign) and len(node.targets) == 1 and
      isinstance(node.targets[0], ast.Name)
  }


def _functions() -> dict[str, ast.FunctionDef]:
  return {
      node.name: node for node in DAG.body if isinstance(node, ast.FunctionDef)
  }


def _params() -> dict[str, ast.Call]:
  params = _assignments()["default_dag_params"]
  assert isinstance(params, ast.Dict)
  out = {}
  for key, value in zip(params.keys, params.values, strict=True):
    assert isinstance(key, ast.Constant) and isinstance(value, ast.Call)
    assert _name(value.func) == "Param", key.value
    out[key.value] = value
  return out


def _task_id(node: ast.AST) -> str | None:
  for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
    ids = [k.value for k in call.keywords if k.arg == "task_id"]
    if ids and isinstance(ids[0], ast.Constant):
      return ids[0].value
  return None


def _task(task_id: str) -> ast.AST:
  """The operator call, or the decorated function, of one task id."""
  for call in (_calls(DAG, "partial") +
               _calls(DAG, "DataflowStartFlexTemplateOperator") +
               _calls(DAG, "DataflowJobStatusSensor") +
               _calls(DAG, "ShortCircuitOperator")):
    ids = [k.value for k in call.keywords if k.arg == "task_id"]
    if ids and isinstance(ids[0], ast.Constant) and ids[0].value == task_id:
      return call
  for function in _functions().values():
    for decorator in function.decorator_list:
      if _task_id(decorator) == task_id:
        return function
  raise AssertionError(f"no task {task_id}")


def _decorator(task_id: str) -> ast.Call:
  function = _task(task_id)
  assert isinstance(function, ast.FunctionDef)
  (decorator,) = function.decorator_list
  assert isinstance(decorator, ast.Call)
  return decorator


def _edges() -> set[tuple[str, str]]:
  """Every `a >> b` of the file, by variable name."""
  edges = set()
  for stmt in ast.walk(DAG):
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.BinOp)):
      continue
    names: list[str] = []
    node: ast.expr = stmt.value
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift):
      assert isinstance(node.right, ast.Name)
      names.append(node.right.id)
      node = node.left
    assert isinstance(node, ast.Name)
    names.append(node.id)
    names.reverse()
    edges |= set(itertools.pairwise(names))
  return edges


def _variable_of(task_id: str) -> str:
  """The DAG-level variable a task (operator or decorated call) is bound to."""
  task = _task(task_id)
  for node in ast.walk(DAG):
    if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
            and isinstance(node.targets[0], ast.Name)):
      continue
    call = node.value
    target = node.targets[0]
    if call is task or (isinstance(call.func, ast.Attribute) and
                        call.func.value is task):
      return target.id
    if isinstance(task, ast.FunctionDef) and _name(call.func) == task.name:
      return target.id
  raise AssertionError(task_id)


# -- the brief's four --------------------------------------------------------
def test_dag_file_has_no_deploy_marker():
  # Workflow 3 seds sdfb_env.yaml only (ADR 0043): a marker here would reach
  # Composer unfilled.
  assert not _MARKER.findall(_DAG.read_text(encoding="utf-8"))


def test_model_dag_is_gone():
  assert not (_COMPOSER / "synthetic_model_launch.py").exists()
  dags = [
      p.name
      for p in _COMPOSER.glob("*.py")
      if "models.DAG" in p.read_text("utf-8")
  ]
  assert sorted(dags) == [
      "evaluation_framework.py", "synthetic_beam_bigquery.py"
  ]
  for path in _COMPOSER.iterdir():
    if path.is_file():
      assert "synthetic_model_launch" not in path.read_text("utf-8"), path


def test_param_defaults_come_from_the_layers(deployed):
  """No param writes its default: each is `DEFAULTS[<its name>]`, the
  helpers' `param_defaults`, and the DAG declares exactly those params."""
  assert ast.unparse(
      _assignments()["DEFAULTS"]) == ("sdfb_batches.param_defaults(LAYERS)")
  assert ast.unparse(_assignments()["LAYERS"]) == (
      "sdfb_batches.load_layers(os.path.dirname(os.path.abspath(__file__)))")
  params = _params()
  for name, call in params.items():
    default = _kw(call, "default")
    assert ast.unparse(default) == f"DEFAULTS[{name!r}]", name
  defaults = M.param_defaults(M.load_layers(deployed()))
  assert set(params) == set(defaults)
  assert set(M.GENERATOR_PARAMS) < set(params)


def test_explicit_task_ids_are_masters():
  for task_id in _EXPLICIT_CHAIN:
    _task(task_id)
  chain = [_variable_of(t) for t in _EXPLICIT_CHAIN]
  assert chain == list(_EXPLICIT_CHAIN)  # each bound to its own name
  edges = _edges()
  for edge in itertools.pairwise(_EXPLICIT_CHAIN):
    assert edge in edges, edge
  # No retries, no trigger rule, no callback but the second sensor's: the
  # environment decides, as on master.
  for task_id in _EXPLICIT_CHAIN:
    passed = _keywords(_task(task_id))
    assert not passed & {"retries", "trigger_rule", "default_args"}, task_id
  for task_id in _EXPLICIT_CHAIN[:-1]:
    assert "on_failure_callback" not in _keywords(_task(task_id)), task_id
  assert ast.unparse(
      _kw(_task("wait_for_evaluation_launch"),
          "on_failure_callback")) == "_close_running_row"


# -- what the DAG imports and reads ------------------------------------------
def test_the_dag_imports_only_airflow_the_stdlib_and_the_helpers():
  imported = {
      alias.name.split(".")[0]
      for node in DAG.body if isinstance(node, ast.Import)
      for alias in node.names
  } | {
      node.module.split(".")[0]
      for node in DAG.body
      if isinstance(node, ast.ImportFrom) and node.module
  }
  assert imported == {"__future__", "airflow", "os", "sdfb_batches"}


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
  variables = _assignments()["VARIABLES"]
  assert isinstance(variables, ast.Dict)
  assert {k.value for k in variables.keys if isinstance(k, ast.Constant)} == {
      "PROJECT_ID", "REGION", "DATAFLOW_SUBNET", "SA_DATAFLOW",
      "DATAFLOW_NETWORK_TAGS"
  }


@pytest.mark.parametrize("version", ["0.7.0", "0.7.0_20261026T1200"])
def test_the_dag_identity_comes_from_the_overlay(deployed, version):
  assert ast.unparse(_assignments()["dag_id"]) == "CONSTANTS['dag_id']"
  assert ast.unparse(_assignments()["CONSTANTS"]) == (
      "sdfb_batches.dag_constants(LAYERS, VARIABLES)")
  assert ast.unparse(_kw(_calls(DAG, "DAG")[0], "dag_id")) == "dag_id"
  layers = M.load_layers(deployed(DAG_VERSION=version))
  constants = M.dag_constants(layers, HARNESS.AIRFLOW_VARIABLES)
  assert constants["dag_id"] == f"synthetic_sdfb_{version}"
  assert constants["labels"]["dag"] == constants["dag_id"]


def test_route_reads_gcs_only_for_a_config_file_or_a_whole_model_launch():
  """Review Focus 4: an explicit launch without config_uri reads nothing
  from GCS (the launcher reads the relationship model itself)."""
  route = _task("route")
  reads = [ast.unparse(c) for c in _calls(route, "_read_gcs_text")]
  assert reads == [
      "_read_gcs_text(config_uri)", "_read_gcs_text(relationships_uri)"
  ]
  text = ast.unparse(route)
  assert ("config_text = _read_gcs_text(config_uri) if config_uri else None"
          in text)
  # The model is read on the `all` branch only.
  (branch,) = [
      n for n in ast.walk(route)
      if isinstance(n, ast.If) and ast.unparse(n.test) == "mode == 'explicit'"
  ]
  assert not _calls(
      ast.Module(body=branch.body, type_ignores=[]), "_read_gcs_text")
  # Only `route` reads GCS, by `download` (bytes; no local file).
  readers = [
      f.name for f in _functions().values() if _calls(f, "_read_gcs_text")
  ]
  assert readers == ["route"]
  assert _calls(_functions()["_read_gcs_text"], "download")


def test_route_resolves_the_branch_and_fails_a_plan_error_once():
  decorator = _decorator("route")
  assert ast.unparse(decorator.func) == "task.branch"
  assert "retries" not in _keywords(decorator)  # a GCS read may be retried
  route = _task("route")
  text = ast.unparse(route)
  # The file is parsed once; every helper takes the parsed mapping.
  assert "config = sdfb_batches.parse_config(config_text)" in text
  assert "sdfb_batches.resolve_tables_mode(params, config)" in text
  assert "sdfb_batches.planning_inputs(params, config)" in text
  branches = {
      ast.literal_eval(n.value)
      for n in ast.walk(route)
      if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "branch"
  }
  assert branches == {"start_sdfb", "batch_bodies"}
  (handler,) = [
      h for n in ast.walk(route) if isinstance(n, ast.Try) for h in n.handlers
  ]
  assert ast.unparse(handler.type) == "sdfb_batches.PlanError"
  assert "AirflowFailException" in ast.unparse(handler)


def test_route_puts_the_files_hardware_over_the_params_before_any_body():
  """Ruling R16: a config file's serving.gpu, sdk_containers and
  launcher_machine_type reach the machine, the accelerator, the experiments
  and the launcher VM the DAG builds, on both branches."""
  route = _task("route")
  (overlay,) = [
      n for n in ast.walk(route) if isinstance(n, ast.Assign) and ast.unparse(n)
      == "params = sdfb_batches.with_file_hardware(params, config)"
  ]
  builders = [
      c for name in ("explicit_body", "launch_plan", "planning_inputs",
                     "whole_model_params") for c in _calls(route, name)
  ]
  assert len(builders) == 4
  for call in builders:
    assert call.lineno > overlay.lineno, ast.unparse(call)


def test_a_files_hardware_values_are_the_params_allowed_ones():
  """Rulings R16, R16b: a file's value is checked against what the DAG's
  own Param allows (its enum), a machine type against non-empty text."""
  params = _params()
  for key, allowed in M.FILE_HARDWARE_KEYS.items():
    schema = {k.arg for k in params[key].keywords}
    if allowed is None:
      assert ast.literal_eval(_kw(params[key], "minLength")) == 1, key
      assert "enum" not in schema, key
    else:
      assert set(ast.literal_eval(_kw(params[key],
                                      "enum"))) == set(allowed), key


class _NotFoundError(Exception):
  """Stands in for google.api_core.exceptions.NotFound."""


def _read_gcs_text(monkeypatch, download):
  """The DAG's `_read_gcs_text`, run on a fake `GCSHook` whose `download`
  is `download`, and a fake `google.api_core.exceptions.NotFound`."""
  hooks = types.ModuleType("airflow.providers.google.cloud.hooks.gcs")
  hooks.GCSHook = type(
      "GCSHook",
      (),
      {  # type: ignore[attr-defined]
          "download": lambda self, **kwargs: download(**kwargs)
      })
  exceptions = types.ModuleType("google.api_core.exceptions")
  exceptions.NotFound = _NotFoundError  # type: ignore[attr-defined]
  parts = ["airflow", "providers", "google", "cloud", "hooks"]
  for depth in range(1, len(parts) + 1):
    name = ".".join(parts[:depth])
    monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
  monkeypatch.setitem(sys.modules, hooks.__name__, hooks)
  monkeypatch.setitem(sys.modules, "google.api_core.exceptions", exceptions)
  function = _functions()["_read_gcs_text"]
  scope: dict = {"sdfb_batches": M}
  exec(  # pylint: disable=exec-used  # one function of a file only Airflow imports
      compile(ast.Module(body=[function], type_ignores=[]), str(_DAG), "exec"),
      scope)
  return scope["_read_gcs_text"]


def test_a_read_that_fails_the_same_way_every_time_is_a_plan_error(monkeypatch):
  """Review M2: a missing object and bytes that are not UTF-8 are
  PlanErrors (route fails once); a transient GCS error is raised as it is,
  for the environment's retries."""
  read = _read_gcs_text(monkeypatch, lambda **kw: b"launch:\n  tables: all\n")
  assert read("gs://b/c.yaml") == "launch:\n  tables: all\n"

  def missing(**_):
    raise _NotFoundError("404 No such object: b/c.yaml")

  with pytest.raises(M.PlanError, match="no such object"):
    _read_gcs_text(monkeypatch, missing)("gs://b/c.yaml")
  with pytest.raises(M.PlanError, match="not UTF-8"):
    _read_gcs_text(monkeypatch, lambda **kw: b"\xff\xfe\x00")("gs://b/c.yaml")
  with pytest.raises(M.PlanError, match="from GCS"):
    _read_gcs_text(monkeypatch, missing)("config/relationships/m.yaml")

  def transient(**_):
    raise ConnectionError("503 backend unavailable")

  with pytest.raises(ConnectionError):
    _read_gcs_text(monkeypatch, transient)("gs://b/c.yaml")


def test_route_pushes_every_key_a_task_pulls():
  route = ast.unparse(_task("route"))
  assert "ti.xcom_push(key='mode', value=mode)" in route
  for key in _ROUTE_KEYS - {"body", "mode"}:
    assert f"'{key}'" in route, key
  assert "'body':" in route
  pulled = set(_ROUTE_PULL.findall(_DAG.read_text(encoding="utf-8")))
  assert pulled <= _ROUTE_KEYS
  assert {"mode", "base_run_id", "bodies", "relationships_uri"} <= pulled


def test_the_launches_take_their_bodies_from_xcom():
  assert ast.unparse(_kw(_task("start_sdfb"),
                         "body")) == ("XComArg(routed.operator, key='body')")
  assert ast.unparse(
      _kw(_task("trigger_evaluation"),
          "body")) == ("XComArg(run_evaluation_gate, key='evaluation_body')")
  assert ast.unparse(_kw(_task("trigger_evaluation_batches"),
                         "body")) == "landed"
  assert _variable_of("collect_landed") == "landed"
  assert _variable_of("route") == "routed"
  # Both evaluations take the branch route resolved, never the param.
  for function in ("_run_evaluation_gate", "collect_landed"):
    (call,) = _calls(_functions()[function], "evaluation_body")
    assert ast.unparse(
        _kw(call,
            "mode")) == ("ti.xcom_pull(task_ids='route', key='mode')"), function
  gate = _task("run_evaluation_gate")
  assert ast.unparse(_kw(gate, "python_callable")) == "_run_evaluation_gate"
  assert _keywords(gate) == {"task_id", "python_callable"}


def test_batches_are_one_mapped_launch_capped_by_the_variable():
  start = _task("start_batch")
  assert _name(start.func) == "partial"
  assert _name(start.func.value) == "DataflowStartFlexTemplateOperator"
  assert ast.literal_eval(_kw(start, "wait_until_finished")) is True
  # No retries: a relaunch replays the same base run id, and after a BLOCKER
  # an append launch would land the rows twice.
  assert ast.literal_eval(_kw(start, "retries")) == 0
  assert ast.unparse(_kw(start,
                         "max_active_tis_per_dag")) == ("MAX_PARALLEL_JOBS")
  assert ("Variable.get('SDFB_MAX_PARALLEL_JOBS', default_var='1')"
          in ast.unparse(_assignments()["MAX_PARALLEL_JOBS"]))
  (expand,) = _calls(DAG, "expand")
  # A task's plain return value: Airflow refuses to map over a custom key.
  assert ast.unparse(_kw(expand, "body")) == "planned"
  assert _variable_of("batch_bodies") == "planned"
  bodies = ast.unparse(_task("batch_bodies"))
  assert "xcom_pull(task_ids='route', key='bodies')" in bodies
  assert ast.unparse(_task("batch_bodies").returns) == "list"
  assert ("routed", "planned") in _edges()


def test_the_skipped_branch_never_evaluates():
  """collect_landed is all_done (it must run after a failed batch), so it
  skips itself when route planned no batch: an explicit launch, or a route
  that failed."""
  decorator = _decorator("collect_landed")
  assert "TriggerRule.ALL_DONE" in ast.unparse(decorator)
  assert ast.literal_eval(_kw(decorator, "multiple_outputs")) is False
  collect = _task("collect_landed")
  first = next(n for n in collect.body if isinstance(n, ast.If))
  assert ast.unparse(first.test) == "not base_run_id"
  assert "AirflowSkipException" in ast.unparse(first.body[0])
  assert "sdfb_batches.landed_job_ids" in ast.unparse(collect)
  for task_id in ("trigger_evaluation_batches",
                  "wait_for_batch_evaluation_launch"):
    assert "trigger_rule" not in _keywords(_task(task_id)), task_id
  assert ("start_batch", "landed") in _edges()
  assert ("trigger_evaluation_batches",
          "wait_for_batch_evaluation_launch") in _edges()


def test_a_failed_batch_fails_the_run_and_the_rest_are_evaluated():
  watcher = _task("fail_if_a_batch_failed")
  assert "TriggerRule.ONE_FAILED" in ast.unparse(watcher.decorator_list[0])
  assert "AirflowFailException" in ast.unparse(watcher)
  assert ("start_batch", "failed") in _edges()
  assert _variable_of("fail_if_a_batch_failed") == "failed"


def _masked(function: ast.FunctionDef) -> list[str]:
  body = function.body[1:] if ast.get_docstring(function) else function.body
  module = ast.Module(body=copy.deepcopy(body), type_ignores=[])
  for node in ast.walk(module):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
      node.value = "…"
  return ast.unparse(module).splitlines()


def test_the_batch_callback_is_the_explicit_ones_with_its_own_target():
  """The two callbacks are the same statements, string literals masked, but
  for where the target and the evaluation job come from."""
  functions = _functions()
  ours = _masked(functions["_close_batch_running_row"])
  theirs = _masked(functions["_close_running_row"])
  changed = [
      line[1:].strip()
      for line in difflib.unified_diff(theirs, ours, lineterm="", n=0)
      if line.startswith(("-", "+")) and not line.startswith(("---", "+++"))
  ]
  # The job id lines read alike once masked: the task ids differ.
  assert changed == [
      "target = _launched_job_id(ti.xcom_pull(task_ids='…'), None)",
      "target = str(ti.xcom_pull(task_ids='…', key='…') or '…')",
  ]
  batch = ast.unparse(functions["_close_batch_running_row"])
  assert "xcom_pull(task_ids='route', key='base_run_id')" in batch
  assert "xcom_pull(task_ids='trigger_evaluation_batches')" in batch
  sensor = _task("wait_for_batch_evaluation_launch")
  assert ast.unparse(_kw(sensor,
                         "on_failure_callback")) == ("_close_batch_running_row")
  explicit = _task("wait_for_evaluation_launch")
  for keyword in _keywords(explicit) - {
      "task_id", "job_id", "on_failure_callback"
  }:
    assert ast.unparse(_kw(sensor,
                           keyword)) == ast.unparse(_kw(explicit,
                                                        keyword)), keyword
  assert ast.literal_eval(_assignments()["batch_evaluation_job_id"]) == (
      "{{ ti.xcom_pull(task_ids='trigger_evaluation_batches')['id'] }}")


# -- note 5: never an empty gpu, sdk_containers or launcher machine ---------
def test_no_launch_forwards_an_empty_gpu_topology_or_launcher_machine(deployed):
  """'' for gpu or sdk_containers is refused by the launcher's argparse, and
  '' for launcherMachineType overrides Dataflow's own. The trigger form and
  the template's regexes refuse '' for all three; a launch passes gpu and
  sdk_containers only where they differ from generator.yaml."""
  params = _params()
  for name in ("gpu", "sdk_containers"):
    enum = ast.literal_eval(_kw(params[name], "enum"))
    assert "" not in enum and enum, name
  assert ast.literal_eval(_kw(params["launcher_machine_type"],
                              "minLength")) == 1
  metadata = {
      p["name"]: p
      for p in json.loads(_METADATA.read_text(encoding="utf-8"))["parameters"]
  }
  for name in ("gpu", "sdk_containers", "launcher_machine_type"):
    assert not any(re.fullmatch(r, "") for r in metadata[name]["regexes"]), name
  layers = M.load_layers(deployed())
  defaults = {**M.param_defaults(layers), "run_id": "manual__x-0a1b2c3d"}
  body = M.explicit_body(
      defaults, layers, job_name="j",
      variables=HARNESS.AIRFLOW_VARIABLES)["launchParameter"]
  plan = M.launch_plan(
      M.whole_model_params({
          **defaults, "tables": "all"
      }, layers),
      model_text=forest_model_text(enabled=12, disabled=1),
      max_tables_per_job=10,
      relationships_uri="gs://b/rel/model.yaml",
      base_run_id="manual__x-abcd1234",
      constants=M.dag_constants(layers, HARNESS.AIRFLOW_VARIABLES),
      layers=layers)
  launches = [body, *(b["launchParameter"] for b in plan["bodies"])]
  assert len(launches) == 3
  for launch in launches:
    assert not {"gpu", "sdk_containers"} & set(launch["parameters"])
    assert launch["environment"]["launcherMachineType"] == "e2-standard-8"
    assert "launcher_machine_type" not in launch["parameters"]


@pytest.mark.parametrize("version",
                         ["0.7.0", "0.7.0rc1+local.3", "1.0.0-Beta_2"])
@pytest.mark.parametrize("model_uri", [
    "gs://b/synthetic/models/Qwen2.5/7B_Instruct/v1/",
    "gs://b/synthetic/models/gemma4/e4b-it/v2/", "config/local-model", ""
])
def test_job_names_are_dataflow_names(deployed, version, model_uri):
  layers = M.load_layers(
      deployed(PROJECT_VERSION=version, SDFB_MODEL_URI=model_uri))
  constants = M.dag_constants(layers, HARNESS.AIRFLOW_VARIABLES)
  name = constants["job_name"]
  assert _DATAFLOW_NAME.fullmatch(name), name
  assert name.startswith("synthetic-sdfb-v")
  for index in (0, 7, 123):
    batch = M.batch_job_name(name, index)
    assert _DATAFLOW_NAME.fullmatch(batch), batch


def test_the_all_branch_runs_on_what_the_dag_declares(deployed):
  # `route` hands the helpers the DAG params: a key they read and the DAG
  # lacks would fail every launch on Composer.
  layers = M.load_layers(deployed())
  params = {
      **M.param_defaults(layers), "tables": "all",
      "relationships_uri": "gs://b/rel/model.yaml"
  }
  uri, k = M.planning_inputs(params, None)
  plan = M.launch_plan(
      M.whole_model_params(params, layers),
      model_text=forest_model_text(enabled=12, disabled=1),
      max_tables_per_job=k,
      relationships_uri=uri,
      base_run_id="manual__x-abcd1234",
      constants=M.dag_constants(layers, HARNESS.AIRFLOW_VARIABLES),
      layers=layers)
  assert (k, plan["batch_count"]) == (10, 2)
  assert set(plan) == (_ROUTE_KEYS - {"body", "mode"}) | {"bodies"}
  json.dumps(plan)  # XComs: plain JSON
  first = plan["bodies"][0]["launchParameter"]["parameters"]
  # The datasets the launcher requires under tables=all, from the overlay.
  assert first["source_dataset"] == "sdfb-proj.src_ds"
  assert first["landing_dataset"] == "sdfb-proj.synthetic_data"


# -- the real-Airflow run ----------------------------------------------------
def _harness_output() -> dict:
  path = os.environ.get(HARNESS_OUTPUT_ENV)
  if not path:
    pytest.skip(f"set {HARNESS_OUTPUT_ENV} to a check_dags.py output of all "
                f"nine scenarios")
  return json.loads(Path(path).read_text(encoding="utf-8"))


def _states(result: dict, prefix: str) -> dict[str, str]:
  return {
      k: v for k, v in result["task_states"].items() if k.startswith(prefix)
  }


def test_the_harness_run_is_the_plans():
  """Task 9 Step 4, scenario by scenario, on the harness's output."""
  output = _harness_output()
  assert set(output) == set(HARNESS.SCENARIOS)
  golden = json.loads(
      (_REPO / "packages" / "sdfb-tests" / "tests" / "fixtures" /
       "config_parity" / "dag_bodies_master.json").read_text("utf-8"))
  for name in HARNESS.MASTER_SCENARIOS:
    result = output[name]
    # master's task states for master's tasks; the rest is the skipped
    # branch, and route ran
    assert {
        t: result["task_states"][t] for t in golden[name]["task_states"]
    } == golden[name]["task_states"], name
    assert result["task_states"]["route"] == "success", name
    for task_id in _ALL_TASKS:
      assert result["task_states"][task_id] == "skipped", (name, task_id)
    assert result["gcs_reads"] == [], name  # Review Focus 4
    assert len(result["bodies"]) == 1, name
    assert (result["evaluation_body"]
            is None) == (golden[name]["evaluation_body"] is None), name
  # Review Focus 2: the file's `all` wins over the trigger's `explicit`.
  says_all = output["explicit_param_file_says_all"]
  assert says_all["task_states"]["start_sdfb"] == "skipped"
  assert says_all["task_states"]["start_batch[0]"] == "success"
  assert says_all["task_states"]["trigger_evaluation_batches"] == "success"
  assert says_all["gcs_reads"] == [
      "gs://sdfb-bucket/synthetic/config/launch_all.yaml",
      "gs://sdfb-bucket/synthetic/relationships/model.yaml"
  ]
  for body in says_all["bodies"]:
    assert body["launchParameter"]["parameters"]["tables"] == "all"
  two = output["all_two_batches"]
  names = [b["launchParameter"]["jobName"] for b in two["bodies"]]
  assert len(names) == 2
  assert [n[-4:] for n in names] == ["-b00", "-b01"]
  evaluation = two["evaluation_body"]["launchParameter"]["parameters"]
  assert evaluation["generation_job_ids"] == (
      "fakejob-start_batch-0,fakejob-start_batch-1")
  assert evaluation["run_id"] == "<DAG_RUN_ID>-<SALT8>"
  fails = output["all_batch_fails"]
  assert fails["task_states"]["fail_if_a_batch_failed"] == "failed"
  assert fails["task_states"]["start_batch[1]"] == "failed"
  assert fails["evaluation_body"]["launchParameter"]["parameters"][
      "generation_job_ids"] == "fakejob-start_batch-0"
  plan_fails = output["all_plan_fails"]
  assert plan_fails["task_states"]["route"] == "failed"
  assert plan_fails["bodies"] == [] and plan_fails["evaluation_body"] is None
  assert not [s for s in plan_fails["task_states"].values() if s == "success"]
  for name in ("all_one_batch", "all_two_batches", "all_batch_fails",
               "explicit_param_file_says_all"):
    result = output[name]
    assert _states(result, "start_sdfb") == {"start_sdfb": "skipped"}, name
    for body in result["bodies"]:
      parameters = body["launchParameter"]["parameters"]
      assert parameters["source_dataset"] == "sdfb-proj.src_ds", name
      assert parameters["landing_dataset"] == "sdfb-proj.synthetic_data", name
  # The marker values the harness filled reached the bodies as text.
  assert MARKERS["DAG_VERSION"] in json.dumps(output)
