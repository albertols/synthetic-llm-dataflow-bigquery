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
"""The Composer DAGs folder as deploy workflow 3 lays it out: `composer/`
with its markers filled (the committed `dag_markers.json`, by the
real-Composer harness's own `fill_markers`) and `generator.yaml` copied
beside it byte for byte (Ruling R14: no symlink in `composer/`).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from sdfb_core.config.defaults import GENERATOR_YAML

_REPO = Path(__file__).resolve().parents[5]
_COMPOSER = _REPO / "composer"
MARKERS: dict[str, str] = json.loads(
    (_REPO / "packages" / "sdfb-tests" / "tests" / "fixtures" /
     "config_parity" / "dag_markers.json").read_text(encoding="utf-8"))


def load_file_module(name: str, path: Path):
  """A module loaded from its file: the DAGs folder and scripts/ are no
  packages."""
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


HARNESS = load_file_module("check_dags",
                           _REPO / "scripts" / "composer" / "check_dags.py")


def deploy_folder(target: Path, markers: dict[str, str] | None = None) -> Path:
  """`target` filled as the deployed DAGs folder; `markers` override the
  committed values."""
  HARNESS.fill_markers(_COMPOSER, target, {**MARKERS, **(markers or {})})
  shutil.copyfile(GENERATOR_YAML, target / "generator.yaml")
  return target


@pytest.fixture(name="deployed")
def _deployed(tmp_path) -> Callable[..., str]:
  """A factory: `deployed(**markers)` is a fresh deployed folder's path."""
  count = iter(range(1_000))

  def make(**markers: str) -> str:
    return str(deploy_folder(tmp_path / f"dags{next(count)}", markers))

  return make
