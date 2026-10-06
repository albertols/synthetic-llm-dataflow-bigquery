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

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from apache_beam.options.pipeline_options import (
    PipelineOptions,
    WorkerOptions,
)

from sdfb_evaluation.cli.main import (
    _flag,
    _subparsers,
    parse_args,
    public_run_flags,
)
from sdfb_evaluation.version import EVALUATOR_VERSION

PACKAGE = Path(__file__).resolve().parents[2]
DOCKERFILE = PACKAGE / "docker" / "Dockerfile"
METADATA = PACKAGE / "deploy" / "flex_template_metadata.json"
BUILD_SCRIPT = PACKAGE / "deploy" / "build_flex_template.sh"
ENTRYPOINT = PACKAGE / "docker" / "entrypoint.sh"


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


# The template launcher passes these to the entry itself (the generator's
# template declares none of them): the runner is DataflowRunner, project and
# region are the launch's. A template parameter of the same name could
# contradict it, in an order-dependent way (Ruling R97).
LAUNCHER_SUPPLIED = frozenset({"--runner", "--project", "--region"})

# Not a flag of the CLI: the selector the shared image's entry
# (docker/flex_entry.py at the repository root) reads and removes before
# the CLI sees the arguments. A template built from this metadata on that
# image selects the evaluator with it (Ruling R118).
SELECTOR = "--sdfb_job"

# Not flags of the CLI either: Beam's own options, which `run` hands to Beam
# unchanged. They are declared so a launch may pass them. The worker boot
# disk is the one a launch on the shared image must pass: that image is
# multi-GB and Dataflow's default disk overflows while a worker unpacks it.
BEAM_PASSTHROUGH = frozenset({"--disk_size_gb"})


def test_metadata_mirrors_every_public_run_flag_plus_the_selector() -> None:
  names = [p["name"] for p in _metadata()["parameters"]]
  assert len(names) == len(set(names))
  flags = public_run_flags()
  assert flags, "no public flags: the parser changed shape"
  assert set(flags) >= LAUNCHER_SUPPLIED
  assert SELECTOR not in flags and not BEAM_PASSTHROUGH & set(flags)
  declared = (set(flags) - LAUNCHER_SUPPLIED) | {SELECTOR} | BEAM_PASSTHROUGH
  assert sorted(f"--{n}" for n in names) == sorted(declared)


def test_the_selector_is_optional_and_admits_the_two_jobs() -> None:
  (selector,) = [
      p for p in _metadata()["parameters"] if p["name"] == SELECTOR[2:]
  ]
  assert selector["isOptional"] is True
  (pattern,) = selector["regexes"]
  for value in ("evaluation", "generation", ""):
    assert re.fullmatch(pattern, value), value
  assert not re.fullmatch(pattern, "both")
  assert "evaluation" in selector["helpText"]


def test_the_selector_is_harmless_where_the_cli_is_the_entry() -> None:
  """On the standalone image `run_evaluation.py` is the entry and nothing
  removes the selector: it must stay a Beam argument Beam itself drops,
  never a usage error of this CLI."""
  args, extras = parse_args([
      "run", "--job_id", "J", "--project", "p", "--region", "r", "--runner",
      "DataflowRunner", f"{SELECTOR}=evaluation"
  ])
  assert args.job_id == "J"
  assert extras == [f"{SELECTOR}=evaluation"]
  assert "sdfb_job" not in PipelineOptions(extras).get_all_options()


def test_the_worker_disk_is_beams_own_option_and_never_empty() -> None:
  """`disk_size_gb` reaches Beam as it is given; Beam reads an integer, so
  the pattern refuses the empty string a template passes for "unset"."""
  args, extras = parse_args([
      "run", "--job_id", "J", "--project", "p", "--region", "r", "--runner",
      "DataflowRunner", "--disk_size_gb=200"
  ])
  assert not hasattr(args, "disk_size_gb")
  assert PipelineOptions(extras).view_as(WorkerOptions).disk_size_gb == 200
  pattern = _regex("disk_size_gb")
  assert re.fullmatch(pattern, "200") and re.fullmatch(pattern, "25")
  for refused in ("", "0", "200GB", "-1"):
    assert not re.fullmatch(pattern, refused), refused
  with pytest.raises(SystemExit) as raised:
    parse_args([
        "run", "--job_id", "J", "--project", "p", "--region", "r",
        "--disk_size_gb="
    ])
  assert raised.value.code == 2


def test_the_shared_template_declares_this_metadata_whole() -> None:
  """The repository's one template (docker/flex_template_metadata.json)
  carries every parameter here under the same name and pattern, so the
  same launch body works against either template."""
  shared = PACKAGE.parents[1] / "docker" / "flex_template_metadata.json"
  if not shared.is_file():  # the package stands alone as its own unit
    pytest.skip("the repository-root template metadata is not beside this "
                "package")
  template = {
      p["name"]: p
      for p in json.loads(shared.read_text(encoding="utf-8"))["parameters"]
  }
  for parameter in _metadata()["parameters"]:
    name = parameter["name"]
    assert name in template, name
    assert template[name].get("regexes") == parameter.get("regexes"), name
    assert template[name].get("isOptional") is True, name


def test_metadata_leaves_launcher_supplied_flags_out() -> None:
  parameters = _metadata()["parameters"]
  names = {"--" + p["name"] for p in parameters}
  assert not names & LAUNCHER_SUPPLIED


def _run_choices() -> dict[str, tuple[str, ...]]:
  run = _subparsers()[1]["run"]
  actions = run._actions  # pylint: disable=protected-access  # no public list
  return {
      option[2:]: tuple(action.choices) for action in actions
      if action.choices and action.help is not argparse.SUPPRESS
      for option in action.option_strings
      if option.startswith("--") and option != "--help"
  }


def _regex(name: str) -> str:
  (parameter,) = [p for p in _metadata()["parameters"] if p["name"] == name]
  (pattern,) = parameter["regexes"]
  return pattern


@pytest.mark.parametrize("name", sorted(_run_choices()))
def test_regex_of_a_choices_flag_admits_exactly_the_choices(name: str) -> None:
  pattern = _regex(name)
  for choice in (*_run_choices()[name], ""):
    assert re.fullmatch(pattern, choice), (name, choice)
  assert not re.fullmatch(pattern, "no-such-choice")


def _boolean_flags() -> list[str]:
  run = _subparsers()[1]["run"]
  return sorted(action.option_strings[0][2:]
                for action in run._actions  # pylint: disable=protected-access  # argparse has no public list of options
                if action.type is _flag)


def test_boolean_flags_exist() -> None:
  assert "allow_contaminated" in _boolean_flags()


@pytest.mark.parametrize("name", _boolean_flags())
def test_boolean_regex_accepts_what_the_cli_parser_accepts(name: str) -> None:
  pattern = _regex(name)
  spellings = [
      "", "true", "false", "True", "False", "TRUE", "FALSE", "1", "0", "yes",
      "No", "ON", "off", "maybe", "2", "tru"
  ]
  for value in spellings:
    try:
      _flag(value)
      accepted = True
    except argparse.ArgumentTypeError:
      accepted = False
    assert bool(re.fullmatch(pattern, value)) == accepted, (name, value)


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
  copies = re.findall(r"^COPY (?!--from)(.+) \S+$", _dockerfile(), re.M)
  assert len(copies) >= 3
  for line in copies:
    for source in line.split():
      prefix = "packages/sdfb-evaluation/"
      assert source.startswith(prefix), source
      assert (PACKAGE / source[len(prefix):]).exists(), source


def test_entrypoint_is_the_dispatch_script() -> None:
  text = _dockerfile()
  entrypoint = re.search(r'^ENTRYPOINT \["([^"]+)"\]$', text, re.M)
  assert entrypoint
  copied = re.search(
      r"^COPY (packages/sdfb-evaluation/docker/entrypoint\.sh) (\S+)$", text,
      re.M)
  assert copied and copied.group(2) == entrypoint.group(1)
  assert (PACKAGE / "docker" / "entrypoint.sh").is_file()
  assert f"chmod +x {entrypoint.group(1)}" in text


def test_entrypoint_script_dispatches_by_the_fnapi_flags() -> None:
  script = ENTRYPOINT.read_text(encoding="utf-8")
  assert script.startswith("#!/bin/sh")
  assert "exec /opt/apache/beam/boot" in script
  assert "exec /opt/google/dataflow/python_template_launcher" in script
  assert "ADR 0009" in script
  result = subprocess.run(["sh", "-n", str(ENTRYPOINT)],
                          capture_output=True,
                          text=True,
                          check=False)
  assert result.returncode == 0, result.stderr


def _discriminators(path: Path) -> set[str]:
  return set(re.findall(r"--\w+=\*", path.read_text(encoding="utf-8")))


def test_entrypoint_discriminator_equals_the_generators() -> None:
  generator = PACKAGE.parents[1] / "docker" / "entrypoint.sh"
  if not generator.is_file():  # the package stands alone as its own unit
    pytest.skip("the repository-root entrypoint is not beside this package")
  assert _discriminators(ENTRYPOINT) == _discriminators(generator)
  assert len(_discriminators(ENTRYPOINT)) == 5


def test_dockerfile_bakes_the_worker_image_and_the_script_passes_it() -> None:
  text = _dockerfile()
  assert 'ARG SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG=""' in text
  assert ("ENV SDFB_EVAL_SDK_CONTAINER_IMAGE="
          "$SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG") in text
  # declared after the dependency layers, so it never invalidates them
  assert text.index("SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG") > text.rindex(
      "RUN uv sync")
  assert ("SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG=${IMAGE}"
          in BUILD_SCRIPT.read_text(encoding="utf-8"))


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
  minor = (PACKAGE / ".python-version").read_text(encoding="utf-8").strip()
  site = f"/template/.venv/lib/python{minor}/site-packages"
  assert site in pythonpath
  assert "/template/src" in pythonpath
  assert f"site.addsitedir('{site}')" in text
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


@pytest.mark.parametrize("path", [DOCKERFILE, BUILD_SCRIPT, ENTRYPOINT])
def test_files_carry_the_licence_header(path: Path) -> None:
  assert "Licensed under the Apache License" in path.read_text(
      encoding="utf-8")[:900]
