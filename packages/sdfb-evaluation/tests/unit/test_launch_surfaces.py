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
"""Tests for the launch surfaces (Task 28): the CPU Dataflow image, the
flex template metadata and the build script.

Static checks only: the files are read and compared with each other and
with the package, nothing is built and nothing reaches the network.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from sdfb_evaluation.cli.main import public_run_flags
from sdfb_evaluation.version import EVALUATOR_VERSION

PACKAGE = Path(__file__).resolve().parents[2]
DOCKERFILE = PACKAGE / "docker" / "Dockerfile"
METADATA = PACKAGE / "deploy" / "flex_template_metadata.json"
BUILD_SCRIPT = PACKAGE / "deploy" / "build_flex_template.sh"


def _dockerfile() -> str:
  return DOCKERFILE.read_text(encoding="utf-8")


def _metadata() -> dict:
  return json.loads(METADATA.read_text(encoding="utf-8"))


def _env(name: str) -> str:
  match = re.search(rf'^ENV {name}="?([^"\s]+)"?\s*$', _dockerfile(), re.M)
  assert match, f"ENV {name} not found in the Dockerfile"
  return match.group(1)


def _locked(name: str) -> str:
  lock = tomllib.loads((PACKAGE / "uv.lock").read_text(encoding="utf-8"))
  versions = [p["version"] for p in lock["package"] if p["name"] == name]
  assert len(versions) == 1, f"{name} locked {len(versions)} times"
  return versions[0]


def test_metadata_mirrors_every_public_run_flag() -> None:
  names = [p["name"] for p in _metadata()["parameters"]]
  assert len(names) == len(set(names))
  flags = public_run_flags()
  assert flags, "no public flags: the parser changed shape"
  assert sorted(f"--{n}" for n in names) == flags


def test_metadata_parameters_are_well_formed() -> None:
  metadata = _metadata()
  assert metadata["name"] and metadata["description"]
  for parameter in metadata["parameters"]:
    assert parameter["label"] and parameter["helpText"], parameter["name"]
    assert re.fullmatch(r"[a-z][a-z0-9_]*", parameter["name"])
    # A flex template passes an unset optional parameter as an empty
    # string, which the CLI reads as "not given"; no parameter is required.
    assert parameter.get("isOptional") is True, parameter["name"]
    for pattern in parameter.get("regexes", []):
      re.compile(pattern)


def test_metadata_has_no_launch_only_parameters() -> None:
  names = {p["name"] for p in _metadata()["parameters"]}
  assert not names & {"experiments", "enable_data_sampling", "fixture_dir"}


def test_flex_entry_file_exists() -> None:
  entry = Path(_env("FLEX_TEMPLATE_PYTHON_PY_FILE"))
  assert entry.parts[:2] == ("/", "template")
  assert (PACKAGE / Path(*entry.parts[2:])).is_file()
  assert entry.name == "run_evaluation.py"


def test_dockerfile_copies_only_files_the_package_has() -> None:
  copies = re.findall(r"^COPY (?!--from)(.+) \./?(?:\S*)$", _dockerfile(), re.M)
  assert copies
  for line in copies:
    for source in line.split():
      prefix = "packages/sdfb-evaluation/"
      assert source.startswith(prefix), source
      assert (PACKAGE / source[len(prefix):]).exists(), source


def test_beam_sdk_tag_equals_locked_apache_beam() -> None:
  match = re.search(r"^FROM apache/beam_python(\d+\.\d+)_sdk:(\S+)$",
                    _dockerfile(), re.M)
  assert match
  assert match.group(2) == _locked("apache-beam")


def test_python_minor_matches_the_package() -> None:
  minor = (PACKAGE / ".python-version").read_text(encoding="utf-8").strip()
  requires = tomllib.loads((PACKAGE / "pyproject.toml").read_text(
      encoding="utf-8"))["project"]["requires-python"]
  assert minor in requires
  text = _dockerfile()
  base = re.search(r"^FROM apache/beam_python(\d+\.\d+)_sdk:", text, re.M)
  launcher = re.search(r"python(\d)(\d+)-template-launcher-base", text)
  assert base and launcher
  assert base.group(1) == minor
  assert f"{launcher.group(1)}.{launcher.group(2)}" == minor
  # the venv path in PYTHONPATH and the bridge carries the same minor
  assert f"python{minor}/site-packages" in _env("PYTHONPATH")


def test_bridge_and_pythonpath_use_the_template_layout() -> None:
  text = _dockerfile()
  pythonpath = _env("PYTHONPATH").split(":")
  assert "/template/.venv/lib/python3.11/site-packages" in pythonpath
  assert "/template/src" in pythonpath
  assert "site.addsitedir('/template/.venv/lib/python3.11/site-packages')" in (
      text)
  assert "zz_sdfb_bridge.pth" in text


def test_image_never_enables_data_sampling() -> None:
  for path in (DOCKERFILE, METADATA, BUILD_SCRIPT):
    assert "enable_data_sampling" not in path.read_text(encoding="utf-8")


def test_build_script_is_valid_shell_with_env_inputs_only() -> None:
  text = BUILD_SCRIPT.read_text(encoding="utf-8")
  assert "set -euo pipefail" in text
  for name in ("PROJECT_ID", "REGION", "REPOSITORY", "TEMPLATES_BUCKET"):
    assert f': "${{{name}:?' in text, name
  assert "gcloud builds submit" in text
  assert "gcloud dataflow flex-template build" in text
  assert "--sdk-language PYTHON" in text
  assert "--sdk_container_image" in text  # the header says how workers run
  assert "sdfb-evaluation-${VERSION}-template.json" in text
  assert "version.py" in text
  code = "\n".join(
      line for line in text.splitlines() if not line.lstrip().startswith("#"))
  assert "upload_graph" not in code  # the CLI owns launch flags
  result = subprocess.run(["bash", "-n", str(BUILD_SCRIPT)],
                          capture_output=True,
                          text=True,
                          check=False)
  assert result.returncode == 0, result.stderr


def test_version_is_readable_by_the_build_script_pattern() -> None:
  text = (PACKAGE / "src" / "sdfb_evaluation" /
          "version.py").read_text(encoding="utf-8")
  match = re.search(r'^EVALUATOR_VERSION = "([^"]+)"', text, re.M)
  assert match and match.group(1) == EVALUATOR_VERSION


@pytest.mark.parametrize("path", [DOCKERFILE, BUILD_SCRIPT])
def test_files_carry_the_licence_header(path: Path) -> None:
  assert "Licensed under the Apache License" in path.read_text(
      encoding="utf-8")[:900]
