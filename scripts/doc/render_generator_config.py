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
"""Check or fix the comments of generator.yaml around its values (ADR 0043).

    uv run python scripts/doc/render_generator_config.py --check [FILE]
    uv run python scripts/doc/render_generator_config.py --fix [FILE]

The value lines of the file are the generator's defaults. Both modes render
the catalogue's comments around the file's OWN values: --check exits 1 naming
the first key whose entry is stale, --fix rewrites the comments and never
changes a value. FILE defaults to the packaged
packages/sdfb-core/src/sdfb_core/config/generator.yaml (config/generator.yaml
links to it).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sdfb_core.config.defaults import load_defaults
from sdfb_core.config.model import IDENTITY_KEYS, SECTION_TYPES
from sdfb_core.config.render import first_stale_key, render_reference

_TARGET = (
    Path(__file__).resolve().parents[2] /
    "packages/sdfb-core/src/sdfb_core/config/generator.yaml")
# Lines 2-14 of this file are the licence header (line 1 is the shebang).
_LICENCE = "\n".join(
    Path(__file__).read_text(encoding="utf-8").splitlines()[1:14]) + "\n\n"


def _key_problem(values: dict[str, dict[str, object]]) -> str | None:
  """A key the file lacks or holds beyond the model, or None."""
  for section, cls in SECTION_TYPES.items():
    want = set(cls.model_fields) - set(IDENTITY_KEYS)
    got = set(values.get(section, {}))
    for key in sorted(want - got):
      return f"{section}.{key} has no value line"
    for key in sorted(got - want):
      return f"{section}.{key} is not a key of the config model"
  for section in sorted(set(values) - set(SECTION_TYPES)):
    return f"'{section}' is not a section of the config model"
  return None


def main() -> int:
  p = argparse.ArgumentParser()
  mode = p.add_mutually_exclusive_group(required=True)
  mode.add_argument("--check", action="store_true")
  mode.add_argument("--fix", action="store_true")
  p.add_argument("file", nargs="?", type=Path, default=None)
  args = p.parse_args()
  target = args.file or _TARGET
  if not target.exists():
    print(f"{target} does not exist", file=sys.stderr)
    return 1
  try:
    values = load_defaults(target)
  except ValueError as e:
    print(e, file=sys.stderr)
    return 1
  if problem := _key_problem(values):
    print(f"{target}: {problem}", file=sys.stderr)
    return 1
  text = _LICENCE + render_reference(values)
  current = target.read_text(encoding="utf-8")
  if args.check:
    stale = first_stale_key(current, text)
    if stale is not None:
      print(
          f"{target}: the comments are stale from {stale} on — run with "
          "--fix",
          file=sys.stderr)
      return 1
    return 0
  target.write_text(text, encoding="utf-8")
  print(f"wrote {target}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
