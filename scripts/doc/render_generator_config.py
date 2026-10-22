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
"""Write or --check config/generator.yaml from sdfb_core/config (ADR 0043).

    uv run python scripts/doc/render_generator_config.py [--check]

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sdfb_core.config.render import render_reference

_TARGET = Path(__file__).resolve().parents[2] / "config" / "generator.yaml"
# Lines 2-14 of this file are the licence header (line 1 is the shebang).
_LICENCE = "\n".join(
    Path(__file__).read_text(encoding="utf-8").splitlines()[1:14]) + "\n\n"


def main() -> int:
  p = argparse.ArgumentParser()
  p.add_argument("--check", action="store_true")
  args = p.parse_args()
  text = _LICENCE + render_reference()
  if args.check:
    if not _TARGET.exists() or _TARGET.read_text(encoding="utf-8") != text:
      print(f"{_TARGET} is stale — run without --check", file=sys.stderr)
      return 1
    return 0
  _TARGET.write_text(text, encoding="utf-8")
  print(f"wrote {_TARGET}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
