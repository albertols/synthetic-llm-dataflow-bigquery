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
"""The generator's version: `<base>-<branch>-<sha7>[-dirty]` (ADR 0044).

`GENERATOR_VERSION_BASE` names the release the code belongs to; the release
Action rewrites it in the commit it tags. The branch and the commit come from
the first source that answers:

    1. git, when this package sits in a checkout (laptop, M4, DirectRunner);
    2. SDFB_BUILD_COMMIT / SDFB_BUILD_BRANCH, baked into the image;
    3. unknown.

Computed once per process. The pyprojects stay at 0.1.0: bumping them would
change uv.lock on every release (the corp `uv sync --frozen` path).

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import functools
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

GENERATOR_VERSION_BASE = "0.6.1"

BUILD_COMMIT_ENV = "SDFB_BUILD_COMMIT"
BUILD_BRANCH_ENV = "SDFB_BUILD_BRANCH"
UNKNOWN = "unknown"
PACKAGE_DIR = Path(__file__).resolve().parent
_GIT_TIMEOUT_S = 2.0
_BRANCH_MAX = 24
_SHA_LEN = 7
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def clean_branch(branch: str) -> str:
  """A branch name fit for a version string: `HEAD` is `detached`."""
  branch = branch.strip()
  if branch == "HEAD":
    return "detached"
  return _UNSAFE.sub("-", branch)[:_BRANCH_MAX] or UNKNOWN


def _git(*args: str) -> str | None:
  try:
    done = subprocess.run(["git", *args],
                          cwd=PACKAGE_DIR,
                          capture_output=True,
                          text=True,
                          timeout=_GIT_TIMEOUT_S,
                          check=False)
  except (OSError, subprocess.SubprocessError):
    return None
  return done.stdout.strip() if done.returncode == 0 else None


def from_git() -> tuple[str, str, bool] | None:
  """(branch, sha7, dirty) of the checkout this package sits in, or None."""
  branch = _git("rev-parse", "--abbrev-ref", "HEAD")
  sha = _git("rev-parse", f"--short={_SHA_LEN}", "HEAD")
  if not branch or not sha:
    return None
  status = _git("status", "--porcelain", "--untracked-files=no")
  return clean_branch(branch), sha[:_SHA_LEN], bool(status)


def from_env(
    environ: Mapping[str, str] | None = None) -> tuple[str, str, bool] | None:
  """(branch, sha7, False) the image was built from, or None."""
  env = os.environ if environ is None else environ
  commit = env.get(BUILD_COMMIT_ENV, "").strip()
  if not commit or commit == UNKNOWN:
    return None
  branch = env.get(BUILD_BRANCH_ENV, "").strip()
  return (clean_branch(branch) if branch and branch != UNKNOWN else UNKNOWN,
          commit[:_SHA_LEN], False)


def format_version(base: str, branch: str, sha: str, dirty: bool) -> str:
  return f"{base}-{branch}-{sha}" + ("-dirty" if dirty else "")


@functools.lru_cache(maxsize=1)
def generator_version() -> str:
  """This process's generator version, computed once."""
  branch, sha, dirty = from_git() or from_env() or (UNKNOWN, UNKNOWN, False)
  return format_version(GENERATOR_VERSION_BASE, branch, sha, dirty)
