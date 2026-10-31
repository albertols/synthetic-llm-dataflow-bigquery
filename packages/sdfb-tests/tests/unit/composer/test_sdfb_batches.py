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
"""The Composer DAG's planner (`composer/sdfb_batches.py`, ADR 0045)
plans exactly what sdfb-core plans — on every committed model and on
generated forests of up to 120 tables — and builds launch bodies a batch
launcher accepts.

The module is loaded from its file (the Composer DAGs folder is not a
package); the scheduler is never imported.

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest
from sdfb_beam.cli import run_pipeline as rp
from sdfb_core.contracts.relationships import (RelationshipError,
                                               RelationshipRegistry)
from sdfb_core.launch.batches import plan_model_launch
from sdfb_tests.launch_surface import generator_flags
from sdfb_tests.model_fixtures import forest_model_text

_REPO = Path(__file__).resolve().parents[5]
_MIRROR = _REPO / "composer" / "sdfb_batches.py"
_MASTER_BODIES = (
    _REPO / "packages" / "sdfb-tests" / "tests" / "fixtures" / "config_parity" /
    "dag_bodies_master.json")
# Master's generation DAG, in master's history (the v0.6.1 report commit).
_MASTER_COMMIT = "edcfdcd"
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


@pytest.mark.parametrize("tables", ["explicit", "all"])
def test_the_keys_a_mode_does_not_pass_are_the_ones_the_launcher_warns_about(
    tables, monkeypatch):
  """The mirror leaves out exactly the keys `warn_unused_launch_keys` would
    flag as launch_option_unused in that mode (ADR 0045, Ruling 10): one
    more and the DAG drops a key the launcher reads, one fewer and every
    launch of that mode logs a WARNING."""
  warned = []
  monkeypatch.setattr(rp, "log_milestone",
                      lambda name, **fields: warned.append(fields["key"]))
  every_key = {
      "reference_table": "p.src.A_TABLE",
      "landing_table": "p.land.A_TABLE",
      "ddl_uri": "gs://b/ddl.json",
      "source_dataset": "p.src",
      "landing_dataset": "p.land",
  }
  rp.warn_unused_launch_keys(argparse.Namespace(tables=tables, **every_key))
  # The mirror's private sets are what is pinned.
  under_all = M._UNREAD_UNDER_ALL  # pylint: disable=protected-access
  under_explicit = M._UNREAD_UNDER_EXPLICIT  # pylint: disable=protected-access
  unread = under_all if tables == "all" else under_explicit
  assert {key.removeprefix("io.") for key in warned} == set(unread)
  assert all(key.startswith("io.") for key in warned)


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


_REFUSED_AT_LOAD = {
    # A bare ref the model does not declare (a typo): no launcher would load
    # the model, so no batch may be launched to find that out.
    "unknown_ref": ("model: m\ntables:\n  T_A: {pk: [A_ID]}\n  T_B:\n"
                    "    fk:\n      - {cols: [A_ID], ref: GHOST, "
                    "ref_cols: [A_ID]}\n"),
    # The same on a disabled table: the core reads every table's refs.
    "unknown_ref_on_a_disabled_table":
        ("model: m\ntables:\n  T_A: {pk: [A_ID]}\n  T_B:\n    enabled: false\n"
         "    fk:\n      - {cols: [A_ID], ref: T_GHOST, ref_cols: [A_ID]}\n"),
    "self_ref":
        ("model: m\ntables:\n  T_A: {pk: [A_ID]}\n  T_B:\n"
         "    fk:\n      - {cols: [P_ID], ref: T_B, ref_cols: [B_ID]}\n"),
    # A dotted ref whose table part is the table itself is one too.
    "dotted_self_ref": ("model: m\ntables:\n  T_A:\n    fk:\n"
                        "      - {cols: [P_ID], ref: other_ds.T_A, "
                        "ref_cols: [A_ID]}\n"),
}


@pytest.mark.parametrize(
    "text", list(_REFUSED_AT_LOAD.values()), ids=list(_REFUSED_AT_LOAD))
def test_a_ref_the_core_refuses_at_load_the_mirror_refuses_in_its_words(text):
  """Review M1: the launcher's registry load refuses these refs, so every
    batch would pay a Flex launch only to stop there. The planner refuses
    them first, with the core's message (less its file-name prefix)."""
  with pytest.raises(RelationshipError) as core:
    RelationshipRegistry.from_sources([("m.yaml", text)])
  with pytest.raises(M.PlanError) as mirror:
    M.plan_batches(text, 10)
  assert str(core.value) == f"m.yaml: {mirror.value}"


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


def test_the_batch_parameters_are_template_parameters_and_generator_flags(
    deployed):
  params = M.batch_parameters(
      M.common_parameters(_PARAMS, layers=M.load_layers(deployed())),
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


def _master_forwarded() -> set[str]:
  """Every parameter name master's generation launch forwarded, read from
  its recorded launches (`dag_bodies_master.json`; its DDL marker was empty,
  so ddl_uri is added: master forwarded it when the marker was set)."""
  golden = json.loads(_MASTER_BODIES.read_text(encoding="utf-8"))
  names = {"ddl_uri"}
  for scenario in golden.values():
    for body in scenario["bodies"]:
      names.update(body["launchParameter"]["parameters"])
  return names


def test_a_batch_forwards_what_the_single_table_launch_forwards(deployed):
  """A batch passes only what differs from generator.yaml (ADR 0043): every
  key master's single-table launch forwarded is passed, or the batch's value
  for it is generator.yaml's, which the launcher holds without the flag."""
  layers = M.load_layers(deployed())
  params = M.batch_parameters(
      M.common_parameters(_PARAMS, layers=layers),
      base_run_id="b",
      index=0,
      count=1)
  missing = _master_forwarded() - set(params) - {
      "reference_table", "landing_table", "ddl_uri", "pk_cols", "identity_cols"
  }
  defaults = {
      k: v for values in layers.defaults.values() for k, v in values.items()
  }
  values = {
      **defaults,
      **{
          k: v for values in layers.overlay["config"].values()
          for k, v in values.items()
      },
      **_PARAMS
  }
  # config_uri (newer than master) is no generator.yaml key: it is passed
  # when set.
  assert _PARAMS["config_uri"] == "" and "config_uri" not in params
  for name in missing:
    assert M.cli_text(values[name]) == M.cli_text(defaults[name]), name
  assert len(missing) > 15  # most of them hold the default
  assert M.common_parameters({
      **_PARAMS, "config_uri": "gs://b/c.yaml"
  },
                             layers=layers)["config_uri"] == "gs://b/c.yaml"


def test_the_stores_are_omitted_when_the_import_left_them_empty(deployed):
  layers = M.load_layers(
      deployed(
          SDFB_RAG_CHUNKS_TABLE="",
          SDFB_FREETEXT_POOLS_TABLE="",
          SDFB_SOURCE_STATS_TABLE=""))
  params = M.common_parameters(_PARAMS, layers=layers)
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


def test_the_accelerators_are_masters():
  """Master's generation launches carried these experiments (its recorded
  l4 and t4 launches)."""
  golden = json.loads(_MASTER_BODIES.read_text(encoding="utf-8"))
  for scenario, accelerator in (("explicit_l4", M.L4_ACCELERATOR),
                                ("explicit_t4", M.T4_ACCELERATOR)):
    (body,) = golden[scenario]["bodies"]
    experiments = body["launchParameter"]["environment"][
        "additionalExperiments"]
    assert accelerator in experiments, scenario


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


@pytest.mark.parametrize(
    "base", [
        "r1", "example-acceptance-whole-model-launch-2026-10-22-forest-r01",
        "x" * 58, "scheduled__2026-10-22T00:00:00+00:00-" + "y" * 40
    ],
    ids=["short", "59_chars", "58_chars", "long"])
@pytest.mark.parametrize("index", [0, 1, 11, 100])
def test_a_cli_batch_job_is_named_by_the_model_dags_rule(base, index):
  """Review M2: the launcher names a CLI batch job as the Composer DAG's
    tables=all branch names its batch jobs — the name before `-bNN`
    shortened, the suffix whole."""
  name = rp.batch_job_name(base, index)
  assert name == M.batch_job_name(rp.sanitize_job_name("sdfb", base), index)
  assert len(name) <= 63 and name.endswith(f"-b{index:02d}")


@pytest.mark.parametrize("uri", [
    "gs://b/synthetic/models/qwen3/4b-instruct-2507/v1/",
    "gs://b/synthetic/models/gemma4/e4b-it/v2/", "config/local-model"
])
def test_the_model_slug_is_masters(uri):
  """Master's generation DAG's `_model_slug`, read from master's history."""
  text = subprocess.run(
      ["git", "show", f"{_MASTER_COMMIT}:composer/synthetic_beam_bigquery.py"],
      cwd=_REPO,
      check=True,
      capture_output=True,
      text=True).stdout
  tree = ast.parse(text)
  functions = [
      n for n in tree.body
      if isinstance(n, ast.FunctionDef) and n.name == "_model_slug"
  ]
  assert len(functions) == 1
  scope: dict = {}
  exec(  # pylint: disable=exec-used  # one pure function of master's DAG file
      compile(
          ast.Module(body=functions, type_ignores=[]), "master_dag", "exec"),
      scope)
  assert M.model_slug(uri) == scope["_model_slug"](uri)


def test_the_plan_holds_one_body_per_batch(deployed):
  text = forest_model_text(enabled=25, disabled=0)
  plan = M.launch_plan(
      _PARAMS,
      model_text=text,
      max_tables_per_job=10,
      relationships_uri="gs://b/rel/model.yaml",
      base_run_id="manual__x-abcd1234",
      constants=_CONSTANTS,
      layers=M.load_layers(deployed()))
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
  assert imported <= {
      "collections", "dataclasses", "hashlib", "os", "re", "typing", "yaml"
  }
  # A DAGs-folder file naming the scheduler and "dag" is parsed as a DAG.
  assert "airflow" not in _MIRROR.read_text(encoding="utf-8").lower()


# -- the launch's table verdicts (ADR 0045) -----------------------------------
def _row(**over):
  return {
      "landing_table": "p.land.T_A",
      "run_id": "r1-b00-00-T_A",
      "status": "PASSED",
      "created_at": "2026-10-31T10:00:00+00:00",
      "status_reason": None,
      **over
  }


_BATCHES = [{
    "index": 0,
    "label": "b00",
    "tables": ["T_A", "T_B"],
    "oversized": False
}, {
    "index": 1,
    "label": "b01",
    "tables": ["T_C"],
    "oversized": False
}]
_PLANNED = ["p.land.T_A", "p.land.T_B", "p.land.T_C"]


def test_table_verdicts_names_passed_failed_missing_and_unplanned():
  m = _load()
  rows = [
      _row(),
      _row(
          landing_table="p.land.T_B",
          run_id="r1-b00-01-T_B",
          status="FAILED_BLOCKER",
          status_reason=("BLOCKER failures 9/10 = 0.9000 exceeds gate 0.0500 "
                         "(env=prd, run_id=r1-b00-01-T_B)")),
      _row(landing_table="p.land.T_Z", run_id="r1-b01-00-T_Z")
  ]
  out = m.table_verdicts(_PLANNED, rows)
  assert out["verdicts"]["p.land.T_B"]["status"] == "FAILED_BLOCKER"
  assert out["verdicts"]["p.land.T_A"] == {
      "status": "PASSED",
      "reason": None,
      "run_id": "r1-b00-00-T_A"
  }
  assert out["missing"] == ["p.land.T_C"] and out["unplanned"] == ["p.land.T_Z"]
  json.dumps(out)


def test_a_colon_qualified_landing_table_matches_its_plan():
  m = _load()
  out = m.table_verdicts(["p.land.T_A"], [_row(landing_table="p:land.T_A")])
  assert out["verdicts"]["p.land.T_A"]["status"] == "PASSED"
  assert out["missing"] == [] and out["unplanned"] == []


def test_the_newest_row_of_a_table_wins():
  m = _load()
  rows = [
      _row(
          status="FAILED_BLOCKER",
          created_at="2026-10-31T10:00:00+00:00",
          run_id="r1-b00-00-T_A"),
      _row(
          status="PASSED",
          created_at="2026-10-31T11:00:00+00:00",
          run_id="r1-b00-00-T_A")
  ]
  assert m.table_verdicts(["p.land.T_A"],
                          rows)["verdicts"]["p.land.T_A"]["status"] == "PASSED"
  assert m.table_verdicts(
      ["p.land.T_A"],
      rows[::-1])["verdicts"]["p.land.T_A"]["status"] == "PASSED"


def test_failed_tables_follow_the_client():
  m = _load()
  out = m.table_verdicts(_PLANNED, [
      _row(status="FAILED_BLOCKER"),
      _row(landing_table="p.land.T_B", status="FAILED_PREFLIGHT")
  ])
  assert m.failed_tables(out, fail_on_blocker=True) == _PLANNED
  assert m.failed_tables(
      out, fail_on_blocker=False) == ["p.land.T_B", "p.land.T_C"]


def test_an_unknown_status_fails_rather_than_passing_silently():
  m = _load()
  out = m.table_verdicts(["p.land.T_A"], [_row(status="SOMETHING_NEW")])
  assert m.failed_tables(out, fail_on_blocker=False) == ["p.land.T_A"]


def test_the_banner_is_one_line_per_table():
  m = _load()
  out = m.table_verdicts(_PLANNED, [
      _row(status="PASSED"),
      _row(
          landing_table="p.land.T_B",
          status="FAILED_PREFLIGHT",
          status_reason="[preflight P5] p.land.T_B: the declared\nPK is not a key"
      )
  ])
  banner = m.verdict_banner(
      out,
      batches=_BATCHES,
      landing_dataset="p.land",
      fail_on_blocker=True,
      reason_column=True)
  lines = banner.splitlines()
  assert lines[
      0] == "TABLE VERDICTS | 3 planned · 1 passed · 1 failed · 1 no row"
  assert lines[1] == ("  p.land.T_B | FAILED_PREFLIGHT | [preflight P5] "
                      "p.land.T_B: the declared PK is not a key")
  assert lines[2] == (
      "  p.land.T_C | NO ROW | batch b01 did not reach DONE, or the record is off"
  )
  assert len(lines) == 3


def test_missing_tables_are_named_by_their_batch():
  m = _load()
  out = m.table_verdicts(_PLANNED,
                         [_row(landing_table="p.land.T_C", run_id="r1-b01-00")])
  lines = m.verdict_banner(
      out,
      batches=_BATCHES,
      landing_dataset="p.land",
      fail_on_blocker=True,
      reason_column=True).splitlines()
  assert lines == [
      "TABLE VERDICTS | 3 planned · 1 passed · 0 failed · 2 no row",
      "  p.land.T_A | NO ROW | batch b00 did not reach DONE, or the record is off",
      "  p.land.T_B | NO ROW | batch b00 did not reach DONE, or the record is off",
  ]


def test_a_table_in_no_batch_and_an_unplanned_row_are_named():
  m = _load()
  out = m.table_verdicts(["p.land.T_Q"], [_row(landing_table="p.land.T_Z")])
  lines = m.verdict_banner(
      out,
      batches=_BATCHES,
      landing_dataset="p.land",
      fail_on_blocker=True,
      reason_column=True).splitlines()
  assert lines == [
      "TABLE VERDICTS | 1 planned · 0 passed · 0 failed · 1 no row",
      "  p.land.T_Q | NO ROW | batch ? did not reach DONE, or the record is off",
      "  p.land.T_Z | UNPLANNED | a row of a table the plan does not name",
  ]


def test_the_banner_marks_a_fake_clients_blocker_informational_and_a_missing_reason_column(
):
  m = _load()
  out = m.table_verdicts(_PLANNED[:2], [
      _row(status="FAILED_BLOCKER"),
      _row(landing_table="p.land.T_B", status="FAILED_PREFLIGHT")
  ])
  lines = m.verdict_banner(
      out,
      batches=_BATCHES,
      landing_dataset="p.land",
      fail_on_blocker=False,
      reason_column=False).splitlines()
  assert lines == [
      "TABLE VERDICTS | 2 planned · 0 passed · 1 failed · 0 no row",
      "  p.land.T_A | FAILED_BLOCKER (informational: client_type=fake) | "
      "no reason recorded (the table has no status_reason column)",
      "  p.land.T_B | FAILED_PREFLIGHT | "
      "no reason recorded (the table has no status_reason column)",
  ]
  with_column = m.verdict_banner(
      out,
      batches=_BATCHES,
      landing_dataset="p.land",
      fail_on_blocker=True,
      reason_column=True).splitlines()
  assert with_column[0] == (
      "TABLE VERDICTS | 2 planned · 0 passed · 2 failed · 0 no row")
  assert with_column[1] == (
      "  p.land.T_A | FAILED_BLOCKER | no reason recorded")


def test_verdicts_query_names_the_launch_two_ways_and_refuses_a_bad_table():
  m = _load()
  sql = m.verdicts_query(
      "sdfb-proj:synthetic_data_quality.validation_runs", with_reason=True)
  assert "FROM `sdfb-proj.synthetic_data_quality.validation_runs`" in sql
  assert ("launch_run_id = @base OR STARTS_WITH(run_id, CONCAT(@base, '-b'))"
          in sql)
  assert sql.startswith(
      "SELECT landing_table, run_id, status, created_at, status_reason")
  assert "status_reason" not in m.verdicts_query("p.d.t", with_reason=False)
  with pytest.raises(ValueError):
    m.verdicts_query("p.d.t; DROP TABLE x", with_reason=True)
  with pytest.raises(ValueError):
    m.verdicts_query("p.d.t`", with_reason=True)


def test_only_the_missing_reason_column_error_triggers_the_retry():
  m = _load()
  assert m.is_missing_reason_column(
      "400 Unrecognized name: status_reason at [1:35]")
  assert not m.is_missing_reason_column("403 Access Denied: Table p:d.t")


def test_parallel_cap_reads_the_variable_then_the_overlaid_yaml(deployed):
  m = _load()
  folder = deployed()
  yaml_only = m.load_layers(folder)
  assert m.parallel_cap(yaml_only, {}) == 1  # generator.yaml's 1
  overlay = Path(folder) / "sdfb_env.yaml"
  overlay.write_text(
      overlay.read_text(encoding="utf-8") +
      '  launch:\n    max_parallel_jobs: "2"\n',
      encoding="utf-8")  # under config:
  layers = m.load_layers(folder)
  assert m.parallel_cap(layers, {}) == 2
  assert m.parallel_cap(layers, {"SDFB_MAX_PARALLEL_JOBS": "3"}) == 3  # wins
  assert m.parallel_cap(layers, {"SDFB_MAX_PARALLEL_JOBS": ""}) == 2  # unset
  assert m.parallel_cap(layers, {"SDFB_MAX_PARALLEL_JOBS": "  "}) == 2  # blank
  assert m.parallel_cap(layers, {"SDFB_MAX_PARALLEL_JOBS": " 3 "}) == 3
  assert m.parallel_cap(layers, {"SDFB_MAX_PARALLEL_JOBS": "0"}) == 1  # floor
  with pytest.raises(ValueError, match="SDFB_MAX_PARALLEL_JOBS"):
    m.parallel_cap(layers, {"SDFB_MAX_PARALLEL_JOBS": "two"})
  overlay.write_text(
      overlay.read_text(encoding="utf-8").replace('max_parallel_jobs: "2"',
                                                  'max_parallel_jobs: "two"'),
      encoding="utf-8")
  with pytest.raises(ValueError, match=r"launch\.max_parallel_jobs"):
    m.parallel_cap(m.load_layers(folder), {})


def test_verdict_inputs_read_the_overlay_then_the_file(deployed):
  m = _load()
  layers = m.load_layers(deployed())
  params = {**m.param_defaults(layers), "client_type": "fake"}
  assert m.verdict_inputs(params, {}, layers) == {
      "validation_runs_table":
          "sdfb-proj.synthetic_data_quality.validation_runs",
      "landing_dataset":
          "sdfb-proj.synthetic_data",
      "client_type":
          "fake"
  }
  # A config file's io.validation_runs_table wins (ADR 0043), empty too.
  for value in ("p.q.runs", ""):
    file = {"io": {"validation_runs_table": value}}
    assert m.verdict_inputs(params, file,
                            layers)["validation_runs_table"] == value
  # A file that leaves it unset, or null, keeps the overlay's.
  for file in ({
      "io": {
          "dlq_table": "p.q.dlq"
      }
  }, {
      "io": {
          "validation_runs_table": None
      }
  }, {
      "io": None
  }):
    assert m.verdict_inputs(params, file, layers)["validation_runs_table"] == (
        "sdfb-proj.synthetic_data_quality.validation_runs")
  # A trigger's landing_dataset is kept.
  assert m.verdict_inputs({
      **params, "landing_dataset": "p.land"
  }, {}, layers)["landing_dataset"] == "p.land"
  with pytest.raises(m.PlanError, match="io"):
    m.verdict_inputs(params, {"io": "p.q.runs"}, layers)


def test_verdict_inputs_follow_a_files_landing_dataset(deployed):
  """The launcher lands the tables in a config file's io.landing_dataset
  (ADR 0043), so the verdicts look there too."""
  m = _load()
  layers = m.load_layers(deployed())
  params = m.param_defaults(layers)
  file = {"io": {"landing_dataset": "sdfb-proj.other_land"}}
  assert m.verdict_inputs(params, file,
                          layers)["landing_dataset"] == "sdfb-proj.other_land"
  # A trigger's value, too, gives way to the file's.
  assert m.verdict_inputs({
      **params, "landing_dataset": "p.land"
  }, file, layers)["landing_dataset"] == "sdfb-proj.other_land"
  # Without the key: whole_model_params' value.
  assert m.verdict_inputs(params, {"io": {
      "dlq_table": "p.q.dlq"
  }}, layers)["landing_dataset"] == "sdfb-proj.synthetic_data"
  # A null is unset, as the launcher reads it (ADR 0043): the params' value.
  assert m.verdict_inputs(params, {"io": {
      "landing_dataset": None
  }}, layers)["landing_dataset"] == "sdfb-proj.synthetic_data"
  for bad in ("", "  ", 7, ["p.d"]):
    with pytest.raises(m.PlanError, match=r"io\.landing_dataset"):
      m.verdict_inputs(params, {"io": {"landing_dataset": bad}}, layers)
