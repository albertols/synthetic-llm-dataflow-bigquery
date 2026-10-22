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
"""The Composer model DAG's planner (`composer/sdfb_batches.py`, ADR 0045)
plans exactly what sdfb-core plans — on every committed model and on
generated forests of up to 120 tables — and builds launch bodies a batch
launcher accepts.

The module is loaded from its file (the Composer DAGs folder is not a
package); the scheduler is never imported.

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest
from sdfb_beam.cli import run_pipeline as rp
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.launch.batches import plan_model_launch
from sdfb_tests.launch_surface import generator_flags
from sdfb_tests.model_fixtures import forest_model_text

_REPO = Path(__file__).resolve().parents[5]
_MIRROR = _REPO / "composer" / "sdfb_batches.py"
_SINGLE_DAG = _REPO / "composer" / "synthetic_beam_bigquery.py"
_METADATA = _REPO / "docker" / "flex_template_metadata.json"
_COMMITTED = sorted((_REPO / "config" / "relationships").glob("*.yaml"))


def _load():
  spec = importlib.util.spec_from_file_location("sdfb_batches", _MIRROR)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


M = _load()

_PARAMS = {
    "relationships_uri": "gs://b/rel/model.yaml",
    "config_uri": "",
    "source_dataset": "p.src",
    "landing_dataset": "p.land",
    "max_tables_per_job": "10",
    "prep_workers": "8",
    "num_rows": "1000",
    "engine": "b1_rag",
    "batch_size": "16",
    "similarity": "0.5",
    "vllm_dtype": "float16",
    "vllm_max_model_len": "8192",
    "client_type": "vllm",
    "gpu": "l4",
    "seed": "",
    "write_disposition": "append",
    "create_if_not_exists": "false",
    "build_rag_layer": "true",
    "rag_row_docs_limit": "1024",
    "build_pool_layer": "true",
    "sdk_containers": "multi",
    "initial_workers": "",
    "autoscaling": "auto",
    "launcher_machine_type": "e2-standard-8",
    "uniqueness_mode": "exact",
    "freetext_expansion": "identifiers",
    "prompt_constraints": "on",
    "prompt_debug": "off",
    "source_stats": "sample",
    "fk_parent_landing": "",
    "multi_table_mode": "single_job",
    "generate_fk_relationships": "true",
    "pool_seed_strategy": "centroid",
}
_CONSTANTS = {
    "bucket_path":
        "staging-bucket",
    "subnetwork":
        "regions/r/subnetworks/s",
    "service_account":
        "example-service-account",
    "region":
        "europe-west3",
    "network_tag_experiments": [],
    "labels": {
        "app": "sdfb",
        "env": "dev",
        "dag": "synthetic_sdfb_model_x"
    },
    "model_uri":
        "gs://b/synthetic/models/m/v1/",
    "embedder_uri":
        "",
    "dlq_table":
        "p.synthetic_data_quality.dlq",
    "validation_runs_table":
        "p.synthetic_data_quality.validation_runs",
    "env":
        "dev",
    "rag_chunks_table":
        "p.synthetic_rag.rag_chunks",
    "freetext_pools_table":
        "p.synthetic_rag.freetext_pools",
    "source_stats_table":
        "p.synthetic_rag.source_table_stats",
    "container_spec_gcs_path":
        "gs://templates/synthetic/sdfb-0.7.0-template.json",
    "job_name":
        "synthetic-sdfb-model-v0-7-0-m",
}


def _core(text: str, k: int) -> list[dict]:
  registry = RelationshipRegistry.from_sources([("m.yaml", text)])
  return [{
      "index": b.index,
      "label": b.label,
      "tables": list(b.tables),
      "oversized": b.oversized,
  } for b in plan_model_launch(registry, k).batches]


# -- parity with sdfb-core -------------------------------------------------
@pytest.mark.parametrize("path", _COMMITTED, ids=lambda p: p.stem)
@pytest.mark.parametrize("k", [1, 2, 3, 10])
def test_the_mirror_plans_every_committed_model_like_the_core(path, k):
  text = path.read_text(encoding="utf-8")
  assert M.plan_batches(text, k) == _core(text, k)


@pytest.mark.parametrize(
    "shape", [
        {},
        {
            "enabled": 100,
            "disabled": 20,
            "chains": (5, 12, 3)
        },
        {
            "enabled": 40,
            "disabled": 0,
            "chains": (30,),
            "reverse": True
        },
    ],
    ids=["spec_forest", "forest_with_chains", "one_big"])
@pytest.mark.parametrize("k", [1, 7, 10, 50, 200])
def test_the_mirror_plans_a_generated_forest_like_the_core(shape, k):
  text = forest_model_text(**shape)
  assert M.plan_batches(text, k) == _core(text, k)


_BRIDGED = """model: m
tables:
  T_A:
    pk: [A_ID]
  T_B:
    enabled: false
    pk: [B_ID]
    fk:
      - {cols: [A_ID], ref: T_A, ref_cols: [A_ID]}
  T_C:
    pk: [C_ID]
    fk:
      - {cols: [B_ID], ref: T_B, ref_cols: [B_ID]}
      - {cols: [X_ID], ref: other_ds.T_X, ref_cols: [X_ID]}
  T_D:
    pk: [D_ID]
    fk:
      - {cols: [A_ID, A_ID2], ref: T_A, ref_cols: [A_ID, A_ID2]}
      - {cols: [C_ID], ref: T_C, ref_cols: [C_ID], enforced: false}
  T_E:
    pk: [E_ID]
"""


@pytest.mark.parametrize("k", [1, 2, 3, 10])
def test_the_mirror_plans_a_disabled_bridge_like_the_core(k):
  """A disabled table between two enabled ones, an edge to it, a dotted
    external ref, a composite FK and a documented edge (review of Task 8)."""
  assert M.plan_batches(_BRIDGED, k) == _core(_BRIDGED, k)


def test_enabled_reads_like_the_core():
  text = ("model: m\ntables:\n  T_A: {enabled: 'false'}\n  T_B: {enabled: 0}\n"
          "  T_C: {enabled: 'on'}\n  T_D: {}\n")
  assert M.plan_batches(text, 10) == _core(text, 10)
  assert M.plan_batches(text, 10)[0]["tables"] == ["T_C", "T_D"]


def test_what_the_core_refuses_the_mirror_refuses():
  with pytest.raises(M.PlanError, match="enabled"):
    M.plan_batches("model: m\ntables:\n  T_A: {enabled: maybe}\n", 10)
  with pytest.raises(M.PlanError, match="enables no table"):
    M.plan_batches("model: m\ntables:\n  T_A: {enabled: false}\n", 10)
  for k in (0, -1, "0", "ten", True):
    with pytest.raises(M.PlanError, match="max_tables_per_job"):
      M.plan_batches("model: m\ntables:\n  T_A: {}\n", k)


# -- planning inputs -------------------------------------------------------
def test_params_name_the_model_and_k():
  assert M.planning_inputs(_PARAMS, None) == ("gs://b/rel/model.yaml", 10)


def test_the_config_file_wins_like_the_launcher(tmp_path):
  text = ("relational: {relationships_uri: gs://b/rel/file.yaml}\n"
          "launch: {max_tables_per_job: 3}\n")
  assert M.planning_inputs(_PARAMS, text) == ("gs://b/rel/file.yaml", 3)
  config = tmp_path / "c.yaml"
  config.write_text(text, encoding="utf-8")
  args, _ = rp.parse_args([
      "--dlq_table=p.q.dlq", "--num_rows=10", "--run_id=r1",
      "--model_uri=gs://b/synthetic/models/m/v1/", "--tables=all",
      "--source_dataset=p.src", "--landing_dataset=p.land",
      "--relationships_uri=gs://b/rel/model.yaml", "--max_tables_per_job=10",
      f"--config_uri={config}"
  ])
  assert (args.relationships_uri,
          args.max_tables_per_job) == ("gs://b/rel/file.yaml", 3)


@pytest.mark.parametrize("text, message", [
    ("launch: {tables: explicit}\n", "launches every enabled table"),
    ("[1, 2]\n", "top level must be a mapping"),
])
def test_a_config_file_the_model_dag_cannot_launch_is_refused(text, message):
  with pytest.raises(M.PlanError, match=message):
    M.planning_inputs(_PARAMS, text)


@pytest.mark.parametrize(
    "uri",
    ["", "config/relationships", "gs://b/rel/", "config/relationships/m.yaml"])
def test_the_model_is_one_file_on_gcs(uri):
  with pytest.raises(M.PlanError, match="ONE model file on GCS"):
    M.planning_inputs({**_PARAMS, "relationships_uri": uri}, None)


# -- launch bodies ---------------------------------------------------------
def _template_names() -> set[str]:
  return {
      p["name"]
      for p in json.loads(_METADATA.read_text(encoding="utf-8"))["parameters"]
  }


def test_the_batch_parameters_are_template_parameters_and_generator_flags():
  params = M.batch_parameters(
      M.common_parameters(_PARAMS, constants=_CONSTANTS),
      base_run_id="manual__2026-10-21-abcd1234",
      index=1,
      count=3)
  assert set(params) <= _template_names()
  assert set(params) <= generator_flags()
  assert params["tables"] == "all"
  assert (params["batch_index"], params["batch_count"]) == ("1", "3")
  assert params["run_id"] == "manual__2026-10-21-abcd1234"
  assert all(isinstance(v, str) for v in params.values())
  for name in ("reference_table", "landing_table", "ddl_uri", "pk_cols",
               "identity_cols"):
    assert name not in params


def _single_dag_forwarded() -> set[str]:
  """Every parameter name the single-table DAG's generation launch
  forwards, conditional `**{...}` blocks included."""
  tree = ast.parse(_SINGLE_DAG.read_text(encoding="utf-8"))
  launches = [
      n for n in ast.walk(tree) if isinstance(n, ast.Call) and
      getattr(n.func, "id", "") == "DataflowStartFlexTemplateOperator" and any(
          k.arg == "task_id" and "start_" in ast.unparse(k.value)
          for k in n.keywords)
  ]
  assert len(launches) == 1
  names: set[str] = set()
  for node in ast.walk(launches[0]):
    if isinstance(node, ast.Dict):
      keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
      if "num_rows" in keys or "rag_chunks_table" in keys or (
          "freetext_pools_table"
          in keys) or "source_stats_table" in keys or ("ddl_uri" in keys):
        names.update(keys)
  return names


def test_a_batch_forwards_what_the_single_table_launch_forwards():
  params = M.batch_parameters(
      M.common_parameters(_PARAMS, constants=_CONSTANTS),
      base_run_id="b",
      index=0,
      count=1)
  missing = _single_dag_forwarded() - set(params) - {
      "reference_table", "landing_table", "ddl_uri", "pk_cols", "identity_cols"
  }
  assert not missing, missing


def test_the_stores_are_omitted_when_the_import_left_them_empty():
  constants = {
      **_CONSTANTS, "rag_chunks_table": "",
      "freetext_pools_table": "",
      "source_stats_table": ""
  }
  params = M.common_parameters(_PARAMS, constants=constants)
  for name in ("rag_chunks_table", "build_rag_layer", "freetext_pools_table",
               "build_pool_layer", "source_stats_table"):
    assert name not in params


@pytest.mark.parametrize("params, machine, accelerator", [
    ({}, "g2-standard-8", M.L4_ACCELERATOR),
    ({
        "gpu": "t4"
    }, "n1-standard-8", M.T4_ACCELERATOR),
    ({
        "client_type": "fake"
    }, "e2-standard-8", None),
])
def test_the_environment_follows_the_gpu_and_the_client(params, machine,
                                                        accelerator):
  env = M.launch_environment({**_PARAMS, **params}, constants=_CONSTANTS)
  assert env["machineType"] == machine
  accelerators = [
      e for e in env["additionalExperiments"]
      if e.startswith("worker_accelerator=")
  ]
  assert accelerators == ([accelerator] if accelerator else [])
  assert env["launcherMachineType"] == "e2-standard-8"
  single = M.launch_environment({
      **_PARAMS, "sdk_containers": "single"
  },
                                constants=_CONSTANTS)
  assert "no_use_multiple_sdk_containers" in single["additionalExperiments"]


def test_the_accelerators_are_the_single_table_dags():
  """The single-table DAG writes each accelerator inside a Jinja string
  split over adjacent literals; Python joins them into one constant, which
  must hold ours whole."""
  tree = ast.parse(_SINGLE_DAG.read_text(encoding="utf-8"))
  constants = [
      node.value
      for node in ast.walk(tree)
      if isinstance(node, ast.Constant) and isinstance(node.value, str)
  ]
  assert any(M.L4_ACCELERATOR in c for c in constants)
  assert any(M.T4_ACCELERATOR in c for c in constants)


def test_batch_job_names_are_distinct_dataflow_names():
  names = [
      M.batch_job_name("synthetic-sdfb-model-v" + "9" * 70, i)
      for i in range(12)
  ]
  assert len(set(names)) == 12
  for name in names:
    assert re.fullmatch(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?", name), name
  assert M.batch_job_name("synthetic-sdfb-model-v0-7-0-m",
                          3) == ("synthetic-sdfb-model-v0-7-0-m-b03")


@pytest.mark.parametrize("uri", [
    "gs://b/synthetic/models/qwen3/4b-instruct-2507/v1/",
    "gs://b/synthetic/models/gemma4/e4b-it/v2/", "config/local-model"
])
def test_the_model_slug_is_the_generation_dags(uri):
  tree = ast.parse(_SINGLE_DAG.read_text(encoding="utf-8"))
  functions = [
      n for n in tree.body
      if isinstance(n, ast.FunctionDef) and n.name == "_model_slug"
  ]
  assert len(functions) == 1
  scope: dict = {}
  exec(  # pylint: disable=exec-used  # one pure function of a file only Composer can import
      compile(
          ast.Module(body=functions, type_ignores=[]), str(_SINGLE_DAG),
          "exec"), scope)
  assert M.model_slug(uri) == scope["_model_slug"](uri)


def test_the_plan_holds_one_body_per_batch():
  text = forest_model_text(enabled=25, disabled=0)
  plan = M.launch_plan(
      _PARAMS,
      model_text=text,
      max_tables_per_job=10,
      relationships_uri="gs://b/rel/model.yaml",
      base_run_id="manual__x-abcd1234",
      constants=_CONSTANTS)
  assert plan["batch_count"] == 3 and len(plan["bodies"]) == 3
  names = [b["launchParameter"]["jobName"] for b in plan["bodies"]]
  assert names == [f"synthetic-sdfb-model-v0-7-0-m-b{i:02d}" for i in range(3)]
  third = plan["bodies"][2]["launchParameter"]
  assert third["parameters"]["batch_index"] == "2"
  assert third["containerSpecGcsPath"] == _CONSTANTS["container_spec_gcs_path"]
  assert "b02 |   5 tables" in plan["banner"]
  json.dumps(plan)  # an XCom value: plain JSON


def test_landed_job_ids_skip_what_did_not_land():
  assert M.landed_job_ids([{
      "id": "j0"
  }, None, {
      "id": ""
  }, {
      "id": "j2"
  }, "junk", {
      "id": "j0"
  }]) == ["j0", "j2"]
  assert M.landed_job_ids(None) == []


def test_the_module_needs_only_the_standard_library_and_yaml():
  tree = ast.parse(_MIRROR.read_text(encoding="utf-8"))
  imported = {
      alias.name.split(".")[0]
      for node in ast.walk(tree) if isinstance(node, ast.Import)
      for alias in node.names
  } | {
      node.module.split(".")[0]
      for node in ast.walk(tree)
      if isinstance(node, ast.ImportFrom) and node.module
  }
  assert imported <= {"__future__", "collections", "re", "typing", "yaml"}
  # A DAGs-folder file naming the scheduler and "dag" is parsed as a DAG.
  assert "airflow" not in _MIRROR.read_text(encoding="utf-8").lower()
