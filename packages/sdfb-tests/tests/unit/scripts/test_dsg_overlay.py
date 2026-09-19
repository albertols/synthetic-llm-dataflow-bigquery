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
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).parents[5]
_DSG = _ROOT / "dsg"


def _text(rel: str) -> str:
  return (_ROOT / rel).read_text(encoding="utf-8")


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
