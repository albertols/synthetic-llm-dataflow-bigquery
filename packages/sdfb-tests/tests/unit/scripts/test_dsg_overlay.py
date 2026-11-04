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
"""Invariants of the `dsg/` overlay the Dataflow Solution Guides copy ships.

The copy carries no provenance file and no licence of its own (the guides
repository has one), so nothing in the overlay may depend on either, and the
values a provenance file used to supply are typed once and kept equal here.
The evaluator ships too: what the overlay types about it (its tables'
partitioning, its launch) is kept equal to the evaluator and the template.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).parents[5]
_DSG = _ROOT / "dsg"
_EVALUATOR = "packages/sdfb-evaluation/"
_spec = importlib.util.spec_from_file_location(
    "dsg_sync_overlay", _ROOT / "scripts" / "dsg" / "sync.py")
sync = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync
_spec.loader.exec_module(sync)


def _text(rel: str) -> str:
  return (_ROOT / rel).read_text(encoding="utf-8")


def _literal(rel: str, name: str):
  """A module-level literal of a Python file, read with `ast`, never imported
  (the evaluator is not installed in this environment)."""
  for node in ast.parse(_text(rel)).body:
    if isinstance(node, ast.AnnAssign):
      targets = [node.target]
    elif isinstance(node, ast.Assign):
      targets = node.targets
    else:
      continue
    if any(isinstance(t, ast.Name) and t.id == name for t in targets):
      return ast.literal_eval(node.value)
  raise AssertionError(f"{name} is not a module-level literal of {rel}")


def _launch_parameters(rel: str) -> dict[str, str]:
  """The `name=value` entries of a launch script's PARAMS array."""
  return dict(re.findall(r'^  "([a-z_]+)=([^"]*)"$', _text(rel), re.MULTILINE))


def test_nothing_in_the_overlay_depends_on_a_provenance_file():
  tracked = subprocess.run(
      ["git", "-C", str(_ROOT), "ls-files", "dsg"],
      capture_output=True,
      text=True,
      check=True).stdout.split()
  mentions = [rel for rel in tracked if ".sync-source" in _text(rel)]
  assert mentions == []


def test_the_guide_version_is_typed_the_same_everywhere():
  project = tomllib.loads(_text("pyproject.toml"))["project"]["version"]
  setup = re.search(r'^\s*version="([^"]+)",$', _text("dsg/pipeline/setup.py"),
                    re.MULTILINE)
  tag = re.search(r'^\s*docker_tag\s*=\s*"([^"]+)"$',
                  _text("dsg/terraform/main.tf"), re.MULTILINE)
  assert setup and tag
  assert setup.group(1) == tag.group(1) == project


def test_the_sdist_lists_only_the_generated_requirements():
  assert _text("dsg/pipeline/MANIFEST.in").split("\n") == [
      "include requirements.txt", "include requirements-dev.txt", ""
  ]


def test_the_licence_file_is_not_shipped():
  manifest = yaml.safe_load(_text("dsg/manifest.yaml"))
  assert "LICENSE" not in manifest["include"]


def test_the_copy_ships_the_evaluator_and_never_the_gui():
  tracked = subprocess.run(["git", "-C", str(_ROOT), "ls-files"],
                           capture_output=True,
                           text=True,
                           check=True).stdout.splitlines()
  manifest = sync.Manifest.load(_DSG / "manifest.yaml")
  # select_files also stops on a shipped test that loads an unshipped script
  shipped = sync.select_files(_ROOT, manifest, files=tracked)
  evaluator = {rel for rel in shipped if rel.startswith(_EVALUATOR)}
  for rel in (".python-version", "README.md", "pyproject.toml", "uv.lock",
              "src/sdfb_evaluation/catalogue/metrics.yaml",
              "src/sdfb_evaluation/schemas/views.sql",
              "src/sdfb_evaluation/cli/run_evaluation.py",
              "tests/fixtures/parity/goldens.json"):
    assert _EVALUATOR + rel in evaluator, rel
  assert {
      rel for rel in evaluator
      if rel.startswith(_EVALUATOR + "src/sdfb_evaluation/schemas/") and
      rel.endswith(".schema.json")
  }
  # its own image and template: the copy builds one image for both jobs
  assert not [
      rel for rel in evaluator
      if rel.startswith((_EVALUATOR + "docker/", _EVALUATOR + "deploy/"))
  ]
  # the GUI is TypeScript and stays in the source repository (ADR 0042)
  assert not [
      rel for rel in shipped
      if rel.startswith(("gui/", "scripts/gui/", "composer/"))
  ]


def test_generator_yaml_is_one_tracked_file_and_ships_twice():
  packaged = "packages/sdfb-core/src/sdfb_core/config/generator.yaml"
  modes = dict(
      line.split("\t")[::-1]
      for line in subprocess.run([
          "git", "-C",
          str(_ROOT), "ls-files", "-s", "config/generator.yaml", packaged
      ],
                                 capture_output=True,
                                 text=True,
                                 check=True).stdout.splitlines())
  # one tracked copy: config/generator.yaml is a link to the packaged file
  assert modes["config/generator.yaml"].startswith("120000 ")
  assert modes[packaged].startswith("100644 ")
  tracked = subprocess.run(["git", "-C", str(_ROOT), "ls-files"],
                           capture_output=True,
                           text=True,
                           check=True).stdout.splitlines()
  shipped = sync.select_files(
      _ROOT, sync.Manifest.load(_DSG / "manifest.yaml"), files=tracked)
  # the guide gets both paths; staging copies through the link
  assert {"config/generator.yaml", packaged} <= set(shipped)


def test_terraform_partitions_the_evaluation_tables_as_the_evaluator_does():
  schemas = _EVALUATOR + "src/sdfb_evaluation/schemas/__init__.py"
  partitioning = _literal(schemas, "PARTITIONING")
  clustering = _literal(schemas, "CLUSTERING")
  rows = re.findall(
      r'^\s*(\w+)\s*=\s*\{\s*partition\s*=\s*"(\w+)",\s*type\s*=\s*"(\w+)",'
      r"\s*expiration_days\s*=\s*(null|\d+),\s*clustering\s*=\s*\[([^\]]*)\]"
      r"\s*\}\s*$", _text("dsg/terraform/evaluation.tf"), re.MULTILINE)
  layout = {}
  for table, field, kind, days, columns in rows:
    expiration = None if days == "null" else int(days)
    layout[table] = ((field, kind, expiration),
                     tuple(re.findall(r'"(\w+)"', columns)))
  assert layout == {
      table: (partitioning[table], clustering[table])
      for table in _literal(schemas, "TABLES")
  }


def test_the_evaluation_launch_passes_template_parameters_only():
  params = _launch_parameters("dsg/pipeline/scripts/06_run_evaluation.sh")
  template = {
      p["name"]: p for p in json.loads(
          _text("docker/flex_template_metadata.json"))["parameters"]
  }
  assert set(params) <= set(template)
  for name, value in params.items():
    if "$" not in value:
      for pattern in template[name].get("regexes", []):
        assert re.fullmatch(pattern, value), (name, value)
  assert params["sdfb_job"] == "evaluation"
  # the generation DAG's chained launch: the launched table read whole, the
  # job id as the launch's identity, never a second target
  assert params["scope"] == "manual" and params["seed_table"] == "order_items"
  assert {"generation_job_id", "validation_runs_table",
          "disk_size_gb"} <= set(params)
  assert not {"run_id", "tables", "job_id"} & set(params)
  generation = _launch_parameters("dsg/pipeline/scripts/04_run_dataflow.sh")
  # the evaluator rebuilds the generation's reference sample: same size
  assert params["reference_rows_limit"] == generation["reference_rows_limit"]
  assert params["relationships_uri"] == generation["relationships_uri"]
  seed = params["seed_table"]
  assert generation["landing_table"].endswith(f".{seed}")
