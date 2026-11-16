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
"""`generator_version`: `<base>-<branch>-<sha7>[-dirty]`, from git, else the
image, else unknown.
"""
from __future__ import annotations

import subprocess

import pytest
from sdfb_core import observability, version

BASE = version.GENERATOR_VERSION_BASE


@pytest.fixture(autouse=True)
def _fresh_cache():
  version.generator_version.cache_clear()
  yield
  version.generator_version.cache_clear()


def _fake_git(monkeypatch, answers: dict[str, str | None]) -> list[list[str]]:
  """`git <a> <b> …` answers `answers["<a> <b>"]`; None or absent = exit 128."""
  calls: list[list[str]] = []

  def run(cmd, **kwargs):
    calls.append(cmd)
    assert kwargs["timeout"] == 2.0
    assert kwargs["cwd"] == version.PACKAGE_DIR
    out = answers.get(" ".join(cmd[1:3]))
    return subprocess.CompletedProcess(
        cmd, 0 if out is not None else 128, stdout=out or "", stderr="")

  monkeypatch.setattr(version.subprocess, "run", run)
  return calls


_CLEAN = {
    "rev-parse --abbrev-ref": "master\n",
    "rev-parse --short=7": "edcfdcd\n",
    "status --porcelain": "",
}


def test_a_clean_checkout(monkeypatch):
  _fake_git(monkeypatch, _CLEAN)
  assert version.generator_version() == f"{BASE}-master-edcfdcd"


def test_a_dirty_checkout(monkeypatch):
  _fake_git(monkeypatch, {**_CLEAN, "status --porcelain": " M packages/x.py\n"})
  assert version.generator_version() == f"{BASE}-master-edcfdcd-dirty"


def test_a_branch_is_cleaned_and_cut_to_24_characters(monkeypatch):
  _fake_git(monkeypatch, {
      **_CLEAN, "rev-parse --abbrev-ref": "feat/config-record-model-launch\n"
  })
  assert version.generator_version() == (
      f"{BASE}-feat-config-record-model-edcfdcd")


def test_a_detached_head(monkeypatch):
  _fake_git(monkeypatch, {**_CLEAN, "rev-parse --abbrev-ref": "HEAD\n"})
  assert version.generator_version() == f"{BASE}-detached-edcfdcd"


def test_a_long_sha_is_cut_to_seven(monkeypatch):
  _fake_git(monkeypatch, {**_CLEAN, "rev-parse --short=7": "edcfdcd12\n"})
  assert version.generator_version() == f"{BASE}-master-edcfdcd"


def test_no_checkout_reads_the_image(monkeypatch):
  _fake_git(monkeypatch, {})
  monkeypatch.setenv(version.BUILD_COMMIT_ENV, "1a2b3c4d5e6f")
  monkeypatch.setenv(version.BUILD_BRANCH_ENV, "release/v0.7")
  assert version.generator_version() == f"{BASE}-release-v0.7-1a2b3c4"


def test_an_image_without_a_branch_says_unknown(monkeypatch):
  _fake_git(monkeypatch, {})
  monkeypatch.setenv(version.BUILD_COMMIT_ENV, "1a2b3c4")
  monkeypatch.delenv(version.BUILD_BRANCH_ENV, raising=False)
  assert version.generator_version() == f"{BASE}-unknown-1a2b3c4"


def test_nothing_known(monkeypatch):
  _fake_git(monkeypatch, {})
  monkeypatch.delenv(version.BUILD_COMMIT_ENV, raising=False)
  monkeypatch.delenv(version.BUILD_BRANCH_ENV, raising=False)
  assert version.generator_version() == f"{BASE}-unknown-unknown"


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("git"),
     subprocess.TimeoutExpired("git", 2.0)])
def test_git_missing_or_slow_falls_back_to_the_image(monkeypatch, failure):

  def run(cmd, **kwargs):
    raise failure

  monkeypatch.setattr(version.subprocess, "run", run)
  monkeypatch.setenv(version.BUILD_COMMIT_ENV, "abcdef0")
  monkeypatch.setenv(version.BUILD_BRANCH_ENV, "main")
  assert version.generator_version() == f"{BASE}-main-abcdef0"


def test_computed_once_per_process(monkeypatch):
  calls = _fake_git(monkeypatch, _CLEAN)
  first = version.generator_version()
  assert version.generator_version() == first
  assert len(calls) == 3  # branch, sha, status — once


@pytest.mark.parametrize("branch,expected", [
    ("master", "master"),
    ("HEAD", "detached"),
    ("feat/x y", "feat-x-y"),
    ("v0.7.0", "v0.7.0"),
    ("", "unknown"),
])
def test_clean_branch(branch, expected):
  assert version.clean_branch(branch) == expected


def test_the_base_line_keeps_the_shape_the_release_tools_grep():
  text = (version.PACKAGE_DIR / "version.py").read_text(encoding="utf-8")
  assert f'\nGENERATOR_VERSION_BASE = "{BASE}"\n' in text


def test_both_modules_name_the_same_image_variable():
  assert observability.BUILD_COMMIT_ENV == version.BUILD_COMMIT_ENV
