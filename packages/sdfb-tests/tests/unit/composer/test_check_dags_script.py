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
"""The real-Airflow DAG harness (`scripts/composer/check_dags.py`), read
without Airflow: its scenario list is the plan's, and master's recorded
launch bodies (`dag_bodies_master.json`) have the shape it writes.

The harness itself runs in a separate Python 3.11 venv with Airflow 2.10.5
(recipe in its docstring); the module imports Airflow inside functions only,
so it loads here from its file.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[5]
_SCRIPT = _REPO / "scripts" / "composer" / "check_dags.py"
_FIXTURES = _REPO / "packages" / "sdfb-tests" / "tests" / "fixtures" / "config_parity"
_GOLDEN = _FIXTURES / "dag_bodies_master.json"
_MARKERS = _FIXTURES / "dag_markers.json"
_DEFAULTS = _FIXTURES / "dag_param_defaults_master.json"
# Generation-body parameters fed by a DAG param of another name (master's
# `start_sdfb`); every other parameter is named after its param, or is a
# deploy constant with no param at all.
_BODY_KEY_PARAM = {"reference_table": "table_fqn"}

_PLAN_SCENARIOS = [
    "explicit_l4", "explicit_t4", "explicit_fk_group", "explicit_eval_off",
    "explicit_param_file_says_all", "all_one_batch", "all_two_batches",
    "all_batch_fails", "all_plan_fails"
]
_EXPLICIT_TASKS = {
    "start_sdfb", "run_evaluation_gate", "wait_for_generation",
    "trigger_evaluation", "wait_for_evaluation_launch"
}

_WHEN = dt.datetime(2026, 3, 4, 0, 5, tzinfo=dt.UTC)
_RUN_ID = "manual__2026-03-04T00:05:00+00:00"


def _load():
  spec = importlib.util.spec_from_file_location("check_dags", _SCRIPT)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


M = _load()


def test_scenarios_are_the_plans_list():
  assert list(M.SCENARIOS) == _PLAN_SCENARIOS
  assert list(M.MASTER_SCENARIOS) == _PLAN_SCENARIOS[:4]
  # Only explicit_t4 changes a deploy marker (the GPU default, master's only
  # way to make t4 the default); the explicit scenarios read nothing from GCS.
  assert {
      n: s["markers"] for n, s in M.SCENARIOS.items() if s["markers"]
  } == {
      "explicit_t4": {
          "GPU": "t4"
      }
  }
  for name in M.MASTER_SCENARIOS:
    assert M.SCENARIOS[name]["gcs_objects"] == {}
    assert "tables" not in M.SCENARIOS[name]["run_conf"]
  assert M.SCENARIOS["explicit_param_file_says_all"]["run_conf"][
      "tables"] == "explicit"
  assert M.SCENARIOS["all_batch_fails"]["fail_job_suffixes"] == ("-b01",)


def test_output_schema_keys():
  assert M.OUTPUT_KEYS == ("task_states", "bodies", "evaluation_body",
                           "gcs_reads")
  golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
  assert sorted(golden) == sorted(M.MASTER_SCENARIOS)
  for name, result in golden.items():
    assert tuple(sorted(result)) == tuple(sorted(M.OUTPUT_KEYS)), name
    assert set(result["task_states"]) == _EXPLICIT_TASKS, name
    assert len(result["bodies"]) == 1, name
    assert result["gcs_reads"] == [], name
    run_id = result["bodies"][0]["launchParameter"]["parameters"]["run_id"]
    assert run_id == "<DAG_RUN_ID>-<SALT8>", name
  assert golden["explicit_eval_off"]["evaluation_body"] is None
  evaluation = golden["explicit_l4"]["evaluation_body"]["launchParameter"]
  assert evaluation["jobName"].endswith("-<TS_NODASH>")
  # Normalised: no date or salt of the run that recorded it is left.
  text = _GOLDEN.read_text(encoding="utf-8")
  assert not re.search(r"manual__|\d{8}T\d{6}", text)


def test_marker_values_are_deployable():
  """Workflow 3 accepts these values; the golden holds no unfilled one."""
  markers = json.loads(_MARKERS.read_text(encoding="utf-8"))
  assert not M.MARKER.search(_GOLDEN.read_text(encoding="utf-8"))
  assert all(isinstance(v, str) for v in markers.values())
  assert markers["WRITE_DISPOSITION"] in {"append", "overwrite"}
  assert markers["GPU"] == "l4"


def test_untriggered_params_are_masters_defaults():
  """Critical 1 of the Task 7 review: one scenario's trigger values leaked
  into the next through the DAG's shared Param objects. Every generation
  parameter whose param the scenario does not set is the default master's
  freshly loaded DAG holds."""
  golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
  defaults = json.loads(_DEFAULTS.read_text(encoding="utf-8"))
  assert sorted(defaults) == sorted(golden)
  checked = 0
  for name, result in golden.items():
    run_conf = M.SCENARIOS[name]["run_conf"]
    parameters = result["bodies"][0]["launchParameter"]["parameters"]
    for key, value in parameters.items():
      param = _BODY_KEY_PARAM.get(key, key)
      if param in defaults[name] and param not in run_conf:
        assert value == str(defaults[name][param]), (name, key)
        checked += 1
  assert checked > 100
  by_name = {
      n: r["bodies"][0]["launchParameter"]["parameters"]
      for n, r in golden.items()
  }
  assert by_name["explicit_fk_group"]["similarity"] == "0.5"
  assert by_name["explicit_eval_off"]["relationships_uri"] == (
      "config/relationships")
  assert by_name["explicit_eval_off"]["num_rows"] == "1000"
  assert defaults["explicit_t4"]["gpu"] == "t4"
  assert defaults["explicit_l4"]["gpu"] == "l4"


def test_normalise_replaces_run_and_time_values():
  ts_nodash = "20260304T000500"
  value = {
      "run_id": f"{_RUN_ID}-0a1b2c3d",
      "names": [f"eval-{ts_nodash.lower()}", f"x-{ts_nodash}"],
      "when": ["2026-03-04T00:05:00+00:00", "day 2026-03-04"],
      "kept": 8,
      "other": "manual__2026-03-05T00:00:00+00:00",
  }
  assert M.normalise(
      value, run_id=_RUN_ID, when=_WHEN) == {
          "run_id": "<DAG_RUN_ID>-<SALT8>",
          "names": ["eval-<TS_NODASH>", "x-<TS_NODASH>"],
          "when": ["<TS>", "day <DS>"],
          "kept": 8,
          "other": "manual__2026-03-05T00:00:00+00:00",
      }


def test_fill_markers_fills_the_tree_and_names_unfilled_markers(tmp_path):
  source = tmp_path / "src"
  (source / "sub").mkdir(parents=True)
  (source / "__pycache__").mkdir()
  (source / "dag.py").write_text('X = "{{ENV}}"  # {{ params.x }}\n')
  (source / "sub" / "env.yaml").write_text("gpu: '{{GPU}}'\n")
  (source / "notes.txt").write_text("{{ENV}} stays\n")
  (source / "__pycache__" / "dag.cpython-311.pyc").write_bytes(b"\0")
  target = tmp_path / "out"
  M.fill_markers(source, target, {"ENV": "prd", "GPU": "t4"})
  assert (target / "dag.py").read_text() == 'X = "prd"  # {{ params.x }}\n'
  assert (target / "sub" / "env.yaml").read_text() == "gpu: 't4'\n"
  assert (target / "notes.txt").read_text() == "{{ENV}} stays\n"
  assert not (target / "__pycache__").exists()
  with pytest.raises(M.HarnessError, match=r"unfilled deploy markers.*GPU"):
    M.fill_markers(source, tmp_path / "out2", {"ENV": "prd"})


def test_recorder_records_and_refuses_reads_it_cannot_serve():
  explicit = M.Recorder(M.SCENARIOS["explicit_l4"])
  with pytest.raises(RuntimeError, match="reads nothing from GCS"):
    explicit.download("sdfb-bucket", "a.yaml")
  with pytest.raises(RuntimeError, match="get_conn"):
    explicit.get_conn()
  assert explicit.gcs_reads == ["gs://sdfb-bucket/a.yaml", M.GET_CONN_READ]
  assert M.forbidden_gcs_reads(M.SCENARIOS["explicit_l4"],
                               explicit.gcs_reads) == explicit.gcs_reads
  batch = M.Recorder(M.SCENARIOS["all_one_batch"])
  uri = "gs://sdfb-bucket/synthetic/relationships/model.yaml"
  assert batch.download("sdfb-bucket",
                        uri.split("/", 3)[3]).startswith(b"model:")
  with pytest.raises(RuntimeError, match="no such object"):
    batch.download("sdfb-bucket", "missing.yaml")
  assert M.forbidden_gcs_reads(M.SCENARIOS["all_one_batch"],
                               [uri, M.GET_CONN_READ]) == [M.GET_CONN_READ]


def test_split_refuses_two_evaluation_launches():
  recorder = M.Recorder(M.SCENARIOS["all_two_batches"])

  def body(job):
    return {"launchParameter": {"parameters": {"sdfb_job": job}}}

  recorder.launches = [("start_batch", 1, body("generation")),
                       ("start_batch", 0, body("generation")),
                       ("trigger_evaluation", -1, body("evaluation"))]
  bodies, evaluation = recorder.split()
  assert len(bodies) == 2 and evaluation == body("evaluation")
  recorder.launches.append(("trigger_evaluation_2", -1, body("evaluation")))
  with pytest.raises(M.HarnessError, match="2 evaluation launches"):
    recorder.split()
