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
"""Capture the master goldens for the generator-config refactor — ONCE.

Run on master code only (Task 1 of the A1 plan). Refuses to run once
`sdfb_core.config` exists, because goldens re-captured from refactored code
prove nothing.

    uv run --no-sync python3 scripts/config/capture_master_goldens.py

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

from sdfb_tests import config_parity as cp


def _write(name: str, data: dict) -> None:
  path = cp.GOLDENS_DIR / f"{name}.json"
  path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
  print(f"wrote {path}")


def main() -> int:
  if importlib.util.find_spec("sdfb_core.config") is not None:
    print(
        "refusing: sdfb_core.config exists — goldens are captured from "
        "master only",
        file=sys.stderr)
    return 1
  cp.GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
  _write("parser", cp.observe_parser())
  _write("namespaces", cp.observe_namespaces())
  _write("constants", cp.observe_constants())
  with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
    first, second = cp.observe_pipelines(Path(a)), cp.observe_pipelines(Path(b))
  if first != second:
    print(
        "refusing: the DirectRunner scenarios are not deterministic",
        file=sys.stderr)
    return 1
  _write("pipelines", first)
  return 0


if __name__ == "__main__":
  sys.exit(main())
