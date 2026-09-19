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
"""One Python version for launcher, workers, container, CI and tooling.

`.python-version` is the reference. Beam pickles code on the launcher and
unpickles it on the workers, so a second version anywhere is a defect, not a
style issue. The sync gate and this repository's own tree use the same check.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[5]
_SCRIPT = _ROOT / "scripts" / "dsg" / "sync.py"
_spec = importlib.util.spec_from_file_location("dsg_sync_pyver", _SCRIPT)
sync = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync
_spec.loader.exec_module(sync)

_PINNED_SUFFIXES = (".py", ".sh", ".yaml", ".yml", ".toml", ".tf")
_PINNED_NAMES = ("Dockerfile", ".python-version")


def _tree(**overrides):
  files = {
      ".python-version": "3.11\n",
      "docker/Dockerfile":
          'ARG BEAM_SDK_IMAGE="apache/beam_python3.11_sdk:2.74.0"\n'
          'ARG LAUNCHER_IMAGE="gcr.io/x/python311-template-launcher-base"\n'
          'ENV PYTHONPATH="/workspace/.venv/lib/python3.11/site-packages"\n',
      "cloudbuild_stage_models.yaml": "steps:\n  - name: python:3.11-slim\n",
      "pyproject.toml": 'requires-python = ">=3.11,<3.12"\n'
                        'target-version = "py311"\npython_version = "3.11"\n',
      "setup.py": 'setup(\n    python_requires=">=3.11,<3.12",\n)\n',
  }
  files.update(overrides)
  return files


def test_one_version_everywhere_is_no_drift():
  assert sync.python_version_drift(_tree()) == []


def test_each_kind_of_pin_is_compared_with_the_reference():
  drift = sync.python_version_drift(
      _tree(
          **{
              "docker/Dockerfile":
                  'ARG A="apache/beam_python3.12_sdk:2.74.0"\n'
                  'ARG B="gcr.io/x/python313-template-launcher-base"\n'
                  'ENV P="/workspace/.venv/lib/python3.10/site-packages"\n',
              "cloudbuild_stage_models.yaml":
                  "steps:\n  - name: python:3.12-slim\n",
              "pyproject.toml":
                  'requires-python = ">=3.11,<3.13"\n'
                  'target-version = "py312"\npython_version = "3.12"\n',
          }))
  assert drift == [
      "cloudbuild_stage_models.yaml: container image is Python 3.12, "
      ".python-version says 3.11",
      "docker/Dockerfile: Beam SDK image is Python 3.12, "
      ".python-version says 3.11",
      "docker/Dockerfile: template launcher image is Python 3.13, "
      ".python-version says 3.11",
      "docker/Dockerfile: site-packages path is Python 3.10, "
      ".python-version says 3.11",
      "pyproject.toml: ruff target-version is Python 3.12, "
      ".python-version says 3.11",
      "pyproject.toml: mypy python_version is Python 3.12, "
      ".python-version says 3.11",
      "pyproject.toml: supported range is >=3.11,<3.13, "
      ".python-version says exactly 3.11",
  ]


def test_a_missing_or_unreadable_reference_is_itself_drift():
  files = _tree()
  del files[".python-version"]
  assert sync.python_version_drift(files) == [".python-version is missing"]
  assert sync.python_version_drift(_tree(**{".python-version": "3\n"})) == [
      ".python-version: expected MAJOR.MINOR, found '3'"
  ]


def test_this_repository_pins_one_python_version():
  tracked = subprocess.run(["git", "-C", str(_ROOT), "ls-files"],
                           capture_output=True,
                           text=True,
                           check=True).stdout.split("\n")
  files = {
      rel: (_ROOT / rel).read_text(encoding="utf-8")
      for rel in tracked
      if rel and (_ROOT / rel).is_file() and
      (rel.endswith(_PINNED_SUFFIXES) or Path(rel).name in _PINNED_NAMES)
  }
  # The reference is the one the guide ships: there is no second copy.
  assert [rel for rel in files if rel.endswith(".python-version")
         ] == [".python-version"]
  assert sync.python_version_drift(files) == []
