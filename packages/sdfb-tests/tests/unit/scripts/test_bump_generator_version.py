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
"""`scripts/release/bump_generator_version.py`: the release Action writes the
tag's version into sdfb_core/version.py in the commit it tags (ADR 0044).

Loaded via importlib, as test_make_release_notes.py loads its script.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).parents[5]
_SCRIPT = _ROOT / "scripts" / "release" / "bump_generator_version.py"
_spec = importlib.util.spec_from_file_location("bump_generator_version",
                                               _SCRIPT)
assert _spec and _spec.loader
bump_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_mod)

_TEXT = '"""doc"""\nGENERATOR_VERSION_BASE = "0.6.1"\nOTHER = 1\n'


@pytest.mark.parametrize("given", ["v0.7.0", "0.7.0", " v0.7.0\n"])
def test_the_line_takes_the_tags_version(given):
  assert bump_mod.bump(
      _TEXT,
      given) == ('"""doc"""\nGENERATOR_VERSION_BASE = "0.7.0"\nOTHER = 1\n')


@pytest.mark.parametrize("given", ["v0.7", "latest", "v0.7.0-rc1", ""])
def test_a_non_release_version_is_refused(given):
  with pytest.raises(ValueError, match=r"vX\.Y\.Z"):
    bump_mod.bump(_TEXT, given)


def test_a_file_without_exactly_one_base_line_is_refused():
  with pytest.raises(ValueError, match="exactly one"):
    bump_mod.bump("X = 1\n", "v1.0.0")
  with pytest.raises(ValueError, match="exactly one"):
    bump_mod.bump(_TEXT + 'GENERATOR_VERSION_BASE = "0.1.0"\n', "v1.0.0")


def test_main_rewrites_the_file(tmp_path):
  target = tmp_path / "version.py"
  target.write_text(_TEXT)
  assert bump_mod.main(["--version", "v1.2.3", "--file", str(target)]) == 0
  assert 'GENERATOR_VERSION_BASE = "1.2.3"' in target.read_text()


def test_the_real_file_is_the_default_target():
  assert bump_mod.VERSION_FILE == (
      _ROOT / "packages" / "sdfb-core" / "src" / "sdfb_core" / "version.py")
  bump_mod.bump(bump_mod.VERSION_FILE.read_text(encoding="utf-8"), "v9.9.9")


def test_the_release_action_stamps_and_commits_the_version():
  workflow = yaml.safe_load(
      (_ROOT / ".github" / "workflows" /
       "release_tag_report.yaml").read_text(encoding="utf-8"))
  steps = workflow["jobs"]["tag-and-report"]["steps"]
  names = [s.get("name", "") for s in steps]
  stamp = names.index("Stamp the generator version")
  assert stamp < names.index("Tag, commit report, push")
  assert "bump_generator_version.py" in steps[stamp]["run"]
  assert "steps.ver.outputs.version" in steps[stamp]["run"]
  commit = steps[names.index("Tag, commit report, push")]["run"]
  assert "packages/sdfb-core/src/sdfb_core/version.py" in commit
