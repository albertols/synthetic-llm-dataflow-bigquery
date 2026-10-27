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

import importlib.util
import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[5]
_SCRIPT = _REPO / "scripts" / "composer" / "check_dags.py"
_FIXTURES = _REPO / "packages" / "sdfb-tests" / "tests" / "fixtures" / "config_parity"
_GOLDEN = _FIXTURES / "dag_bodies_master.json"
_MARKERS = _FIXTURES / "dag_markers.json"

_PLAN_SCENARIOS = [
    "explicit_l4", "explicit_t4", "explicit_fk_group", "explicit_eval_off",
    "explicit_param_file_says_all", "all_one_batch", "all_two_batches",
    "all_batch_fails", "all_plan_fails"
]
_EXPLICIT_TASKS = {
    "start_sdfb", "run_evaluation_gate", "wait_for_generation",
    "trigger_evaluation", "wait_for_evaluation_launch"
}


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
