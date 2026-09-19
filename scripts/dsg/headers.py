#!/usr/bin/env python
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
"""Apache-2.0 licence header on every source file, checked and inserted.

The Dataflow Solution Guides require one identical header on all source
files. This repository carries the same block under its own holder, and the
sync (`scripts/dsg/sync.py`) rewrites the copyright line with `retitle`, so a
file has the same line numbers in both trees (ADR 0040).

    uv run python scripts/dsg/headers.py          # check, exit 1 on findings
    uv run python scripts/dsg/headers.py --fix    # insert where missing

Only a missing header is inserted. A malformed header or an unexpected holder
is reported and left for a person to resolve.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

HOLDER = "The synthetic-llm-dataflow-bigquery Authors"
DSG_HOLDER = "Google LLC"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIRST = re.compile(r"^#  Copyright (\d{4}) (.+)$")
# Lines a tool reads only on line 1: a shebang, a Dockerfile parser directive.
_LEAD = re.compile(r"^(#!|# syntax=|# escape=)")
_DOCSTRING = ('"""', "'''", 'r"""', "r'''")
_SCAN_LINES = 20
_BODY = (
    "#",
    '#  Licensed under the Apache License, Version 2.0 (the "License");',
    "#  you may not use this file except in compliance with the License.",
    "#  You may obtain a copy of the License at",
    "#",
    "#      https://www.apache.org/licenses/LICENSE-2.0",
    "#",
    "#  Unless required by applicable law or agreed to in writing, software",
    '#  distributed under the License is distributed on an "AS IS" BASIS,',
    "#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or "
    "implied.",
    "#  See the License for the specific language governing permissions and",
    "#  limitations under the License.",
)
# `#`-comment source files. Everything else is out by construction: vendored
# bytes (dsg/pylintrc), generated files (uv.lock, requirements), formats with
# no comment syntax (.json, .python-version), ignore files and markdown.
_SUFFIXES = frozenset({".py", ".sh", ".tf", ".hcl", ".yaml", ".yml", ".toml"})
_NAMES = frozenset({"Dockerfile"})


def in_scope(rel: str) -> bool:
  """Whether the file at repo-relative `rel` must carry the header."""
  path = PurePosixPath(rel)
  return path.name in _NAMES or path.suffix in _SUFFIXES


def header_lines(holder: str, year: int) -> list[str]:
  return [f"#  Copyright {year} {holder}", *_BODY]


def _header_start(lines: Sequence[str]) -> int:
  return 1 if lines and _LEAD.match(lines[0]) else 0


def problem(text: str, *, holder: str = HOLDER) -> str | None:
  """What is wrong with the header of `text`, or None when it is correct."""
  lines = text.splitlines()
  start = _header_start(lines)
  first = _FIRST.match(lines[start]) if len(lines) > start else None
  if not first:
    if any(_FIRST.match(line) for line in lines[:_SCAN_LINES]):
      return "malformed header"
    return "missing header"
  if tuple(lines[start + 1:start + 1 + len(_BODY)]) != _BODY:
    return "malformed header"
  if first.group(2) != holder:
    return f"unexpected holder: {first.group(2)}"
  return None


def apply(text: str, rel: str, *, year: int) -> str:
  """`text` with the header inserted; unchanged unless it is missing."""
  if problem(text) != "missing header":
    return text
  lines = text.splitlines(keepends=True)
  lead = ""
  if lines and _LEAD.match(lines[0]):
    lead = lines.pop(0)
    if not lead.endswith("\n"):
      lead += "\n"
  rest = "".join(lines)
  header = "\n".join(header_lines(HOLDER, year)) + "\n"
  if not rest.strip():
    return lead + header
  # A module docstring sits directly under the header (the DSG layout).
  adjacent = rest.startswith("\n") or (rel.endswith(".py") and
                                       rest.startswith(_DOCSTRING))
  return lead + header + ("" if adjacent else "\n") + rest


def retitle(text: str, holder: str) -> str:
  """`text` with the copyright holder replaced; every other byte is kept."""
  lines = text.splitlines(keepends=True)
  start = _header_start([line.rstrip("\n") for line in lines[:1]])
  first = _FIRST.match(
      lines[start].rstrip("\n")) if len(lines) > start else None
  if not first:
    return text
  ending = "\n" if lines[start].endswith("\n") else ""
  lines[start] = f"#  Copyright {first.group(1)} {holder}{ending}"
  return "".join(lines)


def _list_files(root: Path) -> list[str]:
  """Tracked and untracked-unignored files at a git top level, else a walk."""
  try:
    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True).stdout.strip()
  except (OSError, subprocess.CalledProcessError):
    top = ""
  if top and Path(top).resolve() == root.resolve():
    listed = subprocess.run([
        "git", "-C",
        str(root), "ls-files", "-z", "--cached", "--others",
        "--exclude-standard"
    ],
                            capture_output=True,
                            text=True,
                            check=True).stdout
    return sorted(p for p in listed.split("\0") if p and (root / p).is_file())
  files = []
  for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d != ".git"]
    for name in filenames:
      files.append(Path(dirpath, name).relative_to(root).as_posix())
  return sorted(files)


def check(root: Path, *, holder: str = HOLDER) -> list[str]:
  """One `path: problem` line per in-scope file whose header is not right."""
  findings = []
  for rel in _list_files(root):
    if not in_scope(rel):
      continue
    found = problem((root / rel).read_text(encoding="utf-8"), holder=holder)
    if found:
      findings.append(f"{rel}: {found}")
  return findings


def fix(root: Path, *, year: int) -> list[str]:
  """Insert the header where it is missing; return the files rewritten."""
  changed = []
  for rel in _list_files(root):
    if not in_scope(rel):
      continue
    path = root / rel
    text = path.read_text(encoding="utf-8")
    fixed = apply(text, rel, year=year)
    if fixed != text:
      path.write_text(fixed, encoding="utf-8")
      changed.append(rel)
  return changed


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--root", type=Path, default=_REPO_ROOT)
  parser.add_argument(
      "--fix", action="store_true", help="insert the header where missing")
  args = parser.parse_args(argv)
  if args.fix:
    for rel in fix(args.root, year=datetime.date.today().year):
      print(f"fixed {rel}")
  findings = check(args.root)
  for finding in findings:
    print(finding)
  if findings:
    print(f"{len(findings)} file(s) need attention", file=sys.stderr)
  return 1 if findings else 0


if __name__ == "__main__":
  sys.exit(main())
