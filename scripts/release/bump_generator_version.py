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
"""Write a release's version into sdfb_core/version.py (ADR 0044).

The release Action runs this before its `[release-report]` commit — the
commit it tags — so a tagged checkout's `GENERATOR_VERSION_BASE` is its own
version. A build of that tag then reads `<version>-<branch>-<sha7>`.

    uv run --no-sync python3 scripts/release/bump_generator_version.py --version v0.7.0
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERSION_FILE = (
    Path(__file__).resolve().parents[2] / "packages" / "sdfb-core" / "src" /
    "sdfb_core" / "version.py")
_LINE = re.compile(r'^GENERATOR_VERSION_BASE = "[^"\n]*"$', re.MULTILINE)
_RELEASE = re.compile(r"v?(\d+\.\d+\.\d+)")


def bump(text: str, version: str) -> str:
  """`text` with its one GENERATOR_VERSION_BASE line set to `version`."""
  match = _RELEASE.fullmatch(version.strip())
  if match is None:
    raise ValueError(f"expected a release version vX.Y.Z, got {version!r}")
  if len(_LINE.findall(text)) != 1:
    raise ValueError("version.py must hold exactly one GENERATOR_VERSION_BASE "
                     "line")
  return _LINE.sub(f'GENERATOR_VERSION_BASE = "{match.group(1)}"', text)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--version", required=True, help="the tag, vX.Y.Z")
  parser.add_argument("--file", type=Path, default=VERSION_FILE)
  args = parser.parse_args(argv)
  path: Path = args.file
  path.write_text(
      bump(path.read_text(encoding="utf-8"), args.version), encoding="utf-8")
  return 0


if __name__ == "__main__":
  sys.exit(main())
