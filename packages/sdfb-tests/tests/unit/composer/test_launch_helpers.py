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
"""The generation DAG's launch helpers in `composer/sdfb_batches.py`: the
env overlay (`composer/sdfb_env.yaml`) over `generator.yaml`, the DAG's
param defaults, and the launch bodies, which pass only the keys that differ
from `generator.yaml` (ADR 0043).

The helpers read the DAG folder as deployed (conftest.py's `deployed`:
`composer/` filled with the committed markers, `generator.yaml` beside it). An explicit launch is
compared with master's recorded launch (`dag_bodies_master.json`) after the
harness's own normalisation.

Design: docs/DESIGN.md §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from sdfb_beam.cli import run_pipeline as rp
from sdfb_core.config import model as config_model
from sdfb_core.config.defaults import defaults_digest, load_defaults
from sdfb_core.config.model import (ALL_KEYS, DEFAULT_CONFIG, FLAG_KEYS,
                                    SECTION_TYPES, value_of)
from sdfb_tests.config_parity import DELIBERATE_CHANGES

_REPO = Path(__file__).resolve().parents[5]
_COMPOSER = _REPO / "composer"
_OVERLAY = _COMPOSER / "sdfb_env.yaml"
_FIXTURES = (
    _REPO / "packages" / "sdfb-tests" / "tests" / "fixtures" / "config_parity")
_MARKERS = json.loads((_FIXTURES / "dag_markers.json").read_text("utf-8"))
_GOLDEN = json.loads((_FIXTURES / "dag_bodies_master.json").read_text("utf-8"))
_MASTER_DEFAULTS = json.loads(
    (_FIXTURES / "dag_param_defaults_master.json").read_text("utf-8"))
_MARKER = re.compile(r"\{\{([A-Z_]+)\}\}")
# The DAG files whose markers the overlay takes over (Ruling R2): master's
# generation and evaluation DAGs, and the model DAG at the plan's start.
_MARKED_FILES = (("edcfdcd", "composer/synthetic_beam_bigquery.py"),
                 ("edcfdcd", "composer/evaluation_framework.py"),
                 ("a7dd31f", "composer/synthetic_model_launch.py"))
# What generator.yaml leaves unset and a launch must name (the launcher's
# REQUIRED_FLAGS, run_id aside).
_REQUIRED = {"reference_table", "landing_table", "dlq_table", "model_uri"}
# A new identity key, not one of master's: the digest of the DAG's yaml.
_DIGEST_KEY = "launch.generator_defaults_digest"


def _module(name: str, path: Path):
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


M = _module("sdfb_batches", _COMPOSER / "sdfb_batches.py")
H = _module("check_dags", _REPO / "scripts" / "composer" / "check_dags.py")


@pytest.fixture(name="layers")
def _layers(deployed):
  return M.load_layers(deployed())


def _dag_run_id(scenario: str) -> str:
  return f"manual__{H.logical_date(scenario).isoformat()}"


def _params(layers, scenario: str) -> dict:
  """What the DAG resolves for a master scenario's trigger: its defaults,
  the trigger's values, and the salted run id."""
  return {
      **M.param_defaults(layers),
      **H.SCENARIOS[scenario]["run_conf"],
      "run_id":
          f"{_dag_run_id(scenario)}-0a1b2c3d",
  }


def _normalised(scenario: str, value):
  return H.normalise(
      value, run_id=_dag_run_id(scenario), when=H.logical_date(scenario))


def _config(parameters: dict):
  args, beam_args = rp.parse_args([f"--{k}={v}" for k, v in parameters.items()])
  assert beam_args == []
  return args.config_resolution.config


# -- the overlay -------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "prd",
        "append",
        "0.7.0_20261026T1200",
        "l4",
        "b1_rag",
        "",
        "gs://sdfb-bucket/synthetic/models/qwen3/4b-instruct-2507/v1/",
        # YAML 1.1's booleans, null and numbers: still text
        "on",
        "no",
        "true",
        "null",
        "~",
        "0.7",
        "1e3",
        "007"
    ])
def test_overlay_values_survive_yaml_after_substitution(deployed, value):
  layers = M.load_layers(deployed(**{name: value for name in _MARKERS}))
  leaves = [*layers.overlay["dag"].values(), *layers.overlay["infra"].values()]
  for section in layers.overlay["config"].values():
    leaves.extend(section.values())
  assert len(leaves) == len(_MARKERS)
  assert all(isinstance(leaf, str) and leaf == value for leaf in leaves), leaves


def test_the_overlay_names_every_marker_the_dags_used():
  used: set[str] = set()
  for commit, path in _MARKED_FILES:
    text = subprocess.run(["git", "show", f"{commit}:{path}"],
                          cwd=_REPO,
                          check=True,
                          capture_output=True,
                          text=True).stdout
    used |= set(_MARKER.findall(text))
  overlay = _OVERLAY.read_text(encoding="utf-8")
  named = _MARKER.findall(overlay)
  assert len(named) == len(set(named)), "a marker named twice"
  assert set(named) == used
  # The harness fills the same names (Task 7's committed values).
  assert set(named) == set(_MARKERS)
  # One per line, quoted.
  for line in overlay.splitlines():
    if _MARKER.search(line):
      assert re.fullmatch(r'  +\w+: "\{\{[A-Z_]+\}\}"', line), line


def test_the_overlay_sets_only_generator_keys(layers):
  for section, values in layers.overlay["config"].items():
    assert set(values) <= set(layers.defaults[section]), section


def test_an_overlay_key_generator_yaml_lacks_is_refused(deployed):
  folder = Path(deployed())
  text = (folder / "sdfb_env.yaml").read_text(encoding="utf-8")
  (folder / "sdfb_env.yaml").write_text(
      text.replace("    gpu:", "    gpus:"), encoding="utf-8")
  with pytest.raises(ValueError, match=r"serving\.gpus"):
    M.load_layers(str(folder))


def test_digest_matches_sdfb_core(layers):
  assert layers.digest == defaults_digest()
  assert re.fullmatch(r"[0-9a-f]{12}", layers.digest)
  assert layers.defaults == load_defaults()


# -- cli_text is sdfb-core's text of a yaml value ----------------------------
_ROUND_TRIP_BASE = [
    "--reference_table=p.src.t", "--landing_table=p.land.t",
    "--dlq_table=p.q.dlq", "--run_id=r1",
    "--model_uri=gs://b/synthetic/models/m/v1/",
    "--rag_chunks_table=p.r.chunks", "--freetext_pools_table=p.r.pools"
]


@pytest.mark.parametrize(
    "dotted",
    [f"{s}.{k}" for s, values in load_defaults().items() for k in values])
def test_cli_text_is_sdfb_cores_text_of_every_yaml_value(dotted):
  section, key = dotted.split(".", 1)
  value = load_defaults()[section][key]
  text = M.cli_text(value)
  if value is None:
    # Unset: the DAG never passes it (its text equals the default's).
    assert text == ""
    return
  if key in FLAG_KEYS:
    # The launcher reads the text back as the default itself.
    args, _ = rp.parse_args([*_ROUND_TRIP_BASE, f"--{key}={text}"])
    config = args.config_resolution.config
    assert value_of(config, dotted) == value_of(DEFAULT_CONFIG, dotted)
    assert args.config_resolution.provenance[dotted] == "flag"
  elif isinstance(value, list):
    # A list constant: the model's own csv text of it.
    assert text == TypeAdapter(config_model.CsvText).validate_python(value)
  else:
    # A scalar constant: the model reads the text back as the default.
    section_model = SECTION_TYPES[section].model_validate({key: text})
    assert getattr(section_model,
                   key) == getattr(getattr(DEFAULT_CONFIG, section), key)


def test_cli_text_renders_like_the_models_coercers():
  assert M.cli_text(None) == ""
  bools = TypeAdapter(config_model.CliBoolFalse)
  assert M.cli_text(True) == bools.validate_python(True) == "true"
  assert M.cli_text(False) == bools.validate_python(False) == "false"
  numbers = TypeAdapter(config_model.NumericText)
  assert M.cli_text(8192) == numbers.validate_python(8192) == "8192"
  csv = TypeAdapter(config_model.CsvText)
  assert M.cli_text([0.05, 0.5]) == csv.validate_python([0.05, 0.5])
  assert M.cli_text(0.5) == "0.5" and M.cli_text("l4") == "l4"


# -- param defaults ----------------------------------------------------------
def test_param_defaults_are_the_yaml_overlaid(layers):
  defaults = M.param_defaults(layers)
  overlaid = {
      section: {
          **values,
          **layers.overlay["config"].get(section, {})
      } for section, values in layers.defaults.items()
  }
  generator_params = 0
  for name, default in defaults.items():
    key = "reference_table" if name == "table_fqn" else name
    sections = [s for s, values in overlaid.items() if key in values]
    if sections:
      generator_params += 1
      assert default == M.cli_text(overlaid[sections[0]][key]), name
  assert generator_params >= 35
  assert defaults["table_fqn"] == _MARKERS["SDFB_DEFAULT_TABLE_FQN"]
  assert defaults["gpu"] == "l4" and defaults["engine"] == "b1_rag"
  assert defaults["vllm_dtype"] == ""  # null: derived from gpu
  assert defaults["tables"] == "explicit" and defaults["config_uri"] == ""


@pytest.mark.parametrize("scenario", ["explicit_l4", "explicit_t4"])
def test_param_defaults_are_masters_but_the_deliberate_changes(
    deployed, scenario):
  """Every param master's DAG declared keeps master's default, except
  vllm_dtype (null now, derived from gpu: DELIBERATE_CHANGES)."""
  layers = M.load_layers(deployed(**H.SCENARIOS[scenario]["markers"]))
  ours, master = M.param_defaults(layers), _MASTER_DEFAULTS[scenario]
  assert set(master) <= set(ours)
  differing = {name for name in master if ours[name] != master[name]}
  assert differing == {"vllm_dtype"}
  assert "serving.vllm_dtype" in DELIBERATE_CHANGES


# -- what a launch passes ----------------------------------------------------
def _expected_defaults_only(layers) -> set[str]:
  overlay_keys = {
      key for section, values in layers.overlay["config"].items()
      for key, value in values.items()
      if M.cli_text(value) != M.cli_text(layers.defaults[section][key])
  }
  return _REQUIRED | overlay_keys | {"run_id", "generator_defaults_digest"}


def test_launch_passes_only_keys_that_differ(layers):
  params = {**M.param_defaults(layers), "run_id": "manual__x-0a1b2c3d"}
  passed = M.launch_overrides(params, layers)
  assert set(passed) == _expected_defaults_only(layers)
  # The committed markers: engine b1_rag, gpu l4, append, no DDL pin all
  # equal generator.yaml, so they are not passed.
  assert set(passed) == {
      "reference_table", "landing_table", "dlq_table", "model_uri", "run_id",
      "generator_defaults_digest", "validation_runs_table", "rag_chunks_table",
      "freetext_pools_table", "source_stats_table", "embedder_uri", "env"
  }
  assert passed["generator_defaults_digest"] == layers.digest
  assert passed["reference_table"] == _MARKERS["SDFB_DEFAULT_TABLE_FQN"]
  assert passed["landing_table"] == _MARKERS["SDFB_LANDING_TABLE"]
  assert passed["run_id"] == "manual__x-0a1b2c3d"
  assert all(isinstance(v, str) for v in passed.values())


def test_an_edited_param_is_passed(deployed, layers):
  base = {**M.param_defaults(layers), "run_id": "r"}
  passed = M.launch_overrides(
      {
          **base, "num_rows": "2000",
          "similarity": "0.7",
          "vllm_dtype": "float16",
          "table_fqn": "p.src.Y_TABLE",
          "run_evaluation": False,
          "config_uri": "gs://b/c.yaml"
      }, layers)
  assert {
      k: passed[k] for k in ("num_rows", "similarity", "vllm_dtype")
  } == {
      "num_rows": "2000",
      "similarity": "0.7",
      "vllm_dtype": "float16"
  }
  assert passed["reference_table"] == "p.src.Y_TABLE"
  assert passed["config_uri"] == "gs://b/c.yaml"
  assert "run_evaluation" not in passed and "table_fqn" not in passed
  # A trigger value equal to generator.yaml is not passed even where the
  # overlay set another: the launcher's default is that value.
  t4 = M.load_layers(deployed(GPU="t4"))
  assert M.launch_overrides({
      **M.param_defaults(t4), "run_id": "r"
  }, t4)["gpu"] == "t4"
  assert "gpu" not in M.launch_overrides(
      {
          **M.param_defaults(t4), "run_id": "r",
          "gpu": "l4"
      }, t4)
  # An empty value where generator.yaml has one is passed as empty.
  assert M.launch_overrides({
      **base, "vllm_max_model_len": ""
  }, layers)["vllm_max_model_len"] == ""


def test_each_mode_leaves_out_what_it_does_not_read(deployed):
  layers = M.load_layers(deployed(SDFB_DDL_URI="gs://b/ddl.json"))
  params = {
      **M.param_defaults(layers), "run_id": "r",
      "source_dataset": "p.src",
      "landing_dataset": "p.land"
  }
  explicit = M.explicit_body({
      **params, "tables": "all"
  },
                             layers,
                             job_name="j",
                             variables=H.AIRFLOW_VARIABLES)
  passed = explicit["launchParameter"]["parameters"]
  assert passed["ddl_uri"] == "gs://b/ddl.json" and "tables" not in passed
  assert "source_dataset" not in passed and "landing_dataset" not in passed
  batch = M.common_parameters(params, layers=layers)
  assert batch["tables"] == "all"
  assert (batch["source_dataset"], batch["landing_dataset"]) == ("p.src",
                                                                 "p.land")
  for name in ("reference_table", "landing_table", "ddl_uri", "run_id"):
    assert name not in batch


@pytest.mark.parametrize("scenario", H.MASTER_SCENARIOS)
def test_explicit_launch_resolves_like_master(deployed, scenario):
  layers = M.load_layers(deployed(**H.SCENARIOS[scenario]["markers"]))
  (master,) = _GOLDEN[scenario]["bodies"]
  master = master["launchParameter"]
  body = _normalised(
      scenario,
      M.explicit_body(
          _params(layers, scenario),
          layers,
          job_name=M.dag_constants(layers, H.AIRFLOW_VARIABLES)["job_name"],
          variables=H.AIRFLOW_VARIABLES))["launchParameter"]
  assert body["environment"] == master["environment"]
  assert body["containerSpecGcsPath"] == master["containerSpecGcsPath"]
  assert body["jobName"] == master["jobName"]
  assert body["parameters"]["run_id"] == master["parameters"]["run_id"]
  theirs, ours = _config(master["parameters"]), _config(body["parameters"])
  compared = [
      k for k in ALL_KEYS if k not in DELIBERATE_CHANGES and k != _DIGEST_KEY
  ]
  differing = {
      k: (value_of(theirs, k), value_of(ours, k))
      for k in compared
      if value_of(theirs, k) != value_of(ours, k)
  }
  assert not differing
  assert value_of(ours, _DIGEST_KEY) == layers.digest
  # The deliberate keys hold the overlay's GPU; a T4 launch keeps master's
  # float16 through the derivation.
  gpu = H.SCENARIOS[scenario]["markers"].get("GPU", _MARKERS["GPU"])
  assert value_of(ours, "serving.gpu") == gpu
  if gpu == "t4":
    assert value_of(ours, "serving.vllm_dtype") == value_of(
        theirs, "serving.vllm_dtype") == "float16"
  # Fewer keys than master's body: only what differs from generator.yaml.
  assert len(body["parameters"]) < len(master["parameters"])


@pytest.mark.parametrize(
    "scenario",
    [s for s in H.MASTER_SCENARIOS if _GOLDEN[s]["evaluation_body"]])
def test_explicit_evaluation_body_is_masters(deployed, scenario):
  layers = M.load_layers(deployed(**H.SCENARIOS[scenario]["markers"]))
  when = H.logical_date(scenario)
  body = M.evaluation_body(
      _params(layers, scenario),
      layers,
      generation_job_ids=["fakejob-start_sdfb"],
      variables=H.AIRFLOW_VARIABLES,
      ts_nodash=when.strftime("%Y%m%dT%H%M%S"))
  assert _normalised(scenario, body) == _GOLDEN[scenario]["evaluation_body"]


def test_the_evaluation_of_an_all_launch_names_the_landed_jobs(layers):
  params = {
      **M.param_defaults(layers), "tables": "all",
      "run_id": "manual__x-0a1b2c3d",
      "relationships_uri": "gs://b/rel/model.yaml"
  }
  body = M.evaluation_body(
      params,
      layers,
      generation_job_ids=["j0", "j2"],
      variables=H.AIRFLOW_VARIABLES,
      ts_nodash="20261027T000000")["launchParameter"]
  assert body["parameters"] == {
      "sdfb_job": "evaluation",
      "run_id": "manual__x-0a1b2c3d",
      "generation_job_ids": "j0,j2",
      "relationships_uri": "gs://b/rel/model.yaml",
      "scope": "manual",
      "trigger": "chained",
      "mode": "",
      "output_dataset": "synthetic_data_quality",
      "disk_size_gb": "200",
  }
  assert body["jobName"] == "sdfb-evaluation-v0-7-0-20261027t000000"
  with pytest.raises(ValueError, match="one generation job"):
    M.evaluation_body({
        **params, "tables": "explicit"
    },
                      layers,
                      generation_job_ids=["j0", "j2"],
                      variables=H.AIRFLOW_VARIABLES,
                      ts_nodash="20261027T000000")


# -- the branch --------------------------------------------------------------
def test_the_file_switches_to_all():
  explicit, everything = {"tables": "explicit"}, {"tables": "all"}
  assert M.resolve_tables_mode(explicit, None) == "explicit"
  assert M.resolve_tables_mode(everything, None) == "all"
  assert M.resolve_tables_mode(explicit, "launch:\n  tables: all\n") == "all"
  assert M.resolve_tables_mode(everything,
                               "launch:\n  tables: explicit\n") == "explicit"
  # A file that leaves the key out: the param decides.
  assert M.resolve_tables_mode(everything, "io:\n  ddl_uri: ''\n") == "all"
  assert M.resolve_tables_mode(explicit, "") == "explicit"
  with pytest.raises(M.PlanError, match=r"launch\.tables"):
    M.resolve_tables_mode(explicit, "launch:\n  tables: some\n")
  with pytest.raises(M.PlanError, match="tables"):
    M.resolve_tables_mode({"tables": "both"}, None)
