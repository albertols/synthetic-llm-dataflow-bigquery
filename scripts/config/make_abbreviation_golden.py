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
"""Capture master's unambiguous flag abbreviations — ONCE, on master code.

argparse accepts any unambiguous prefix of a long option. Operators typed
such prefixes against master (`--landing`, `--batch`); new flags can make a
prefix ambiguous. This maps every prefix (at least 3 characters after the
dashes) that is unambiguous on master's parser to the `dest` it reaches (`--help` aside: it exits).

    uv run --no-sync python3 scripts/config/make_abbreviation_golden.py

Run it from a checkout of master's code (edcfdcd, the script copied in); it
refuses once `sdfb_core.config` exists.

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from sdfb_tests.launch_surface import generator_parser

MIN_PREFIX = 3
GOLDEN = (
    Path(__file__).resolve().parents[2] / "packages" / "sdfb-tests" / "tests" /
    "fixtures" / "config_parity" / "abbreviations.json")


def abbreviations() -> dict[str, str]:
  """Every unambiguous prefix of a long option string -> its dest."""
  by_option = {}
  for action in generator_parser()._actions:  # pylint: disable=protected-access  # argparse exposes no public action list
    for option in action.option_strings:
      if option.startswith("--") and action.dest != "help":
        by_option[option[2:]] = action
  out = {}
  for name in by_option:
    for size in range(MIN_PREFIX, len(name) + 1):
      prefix = name[:size]
      matches = {
          id(action): action
          for other, action in by_option.items()
          if other.startswith(prefix)
      }
      if len(matches) == 1:
        out[prefix] = next(iter(matches.values())).dest
  return dict(sorted(out.items()))


def kinds() -> dict[str, dict]:
  """How a test must feed each dest: nargs, choices, const."""
  out = {}
  for action in generator_parser()._actions:  # pylint: disable=protected-access
    if action.dest == "help":
      continue
    out[action.dest] = {
        "takes_value": action.nargs != 0,
        "choices": [str(c) for c in action.choices] if action.choices else None,
        "type": getattr(action.type, "__name__", None),
    }
  return out


def main() -> int:
  if importlib.util.find_spec("sdfb_core.config") is not None:
    print(
        "refusing: sdfb_core.config exists — capture from master only",
        file=sys.stderr)
    return 1
  path = GOLDEN
  prefixes = abbreviations()
  data = {"prefixes": prefixes, "dests": kinds()}
  path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
  print(f"wrote {path}: {len(prefixes)} prefixes")
  return 0


if __name__ == "__main__":
  sys.exit(main())
