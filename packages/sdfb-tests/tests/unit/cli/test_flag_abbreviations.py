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
"""Master's abbreviated flags (`--landing`, `--batch`) still reach their flags.

argparse takes any unambiguous prefix of a long option; each flag this branch
added can make a prefix master accepted ambiguous. The golden is every
prefix (three or more characters) unambiguous on master `edcfdcd`, mapped to
its dest (`scripts/config/make_abbreviation_golden.py`).
"""
from __future__ import annotations

import json
from pathlib import Path

from sdfb_tests.launch_surface import generator_parser

GOLDEN = json.loads((Path(__file__).parents[2] / "fixtures" / "config_parity" /
                     "abbreviations.json").read_text())


def _value(kind: dict) -> str:
  if kind["choices"]:
    return kind["choices"][0]
  return "1000" if kind["type"] else "x"


def test_every_master_abbreviation_reaches_the_same_dest():
  parser = generator_parser()
  wrong = {}
  for prefix, dest in GOLDEN["prefixes"].items():
    kind = GOLDEN["dests"][dest]
    token = f"--{prefix}=" + _value(kind)
    try:
      namespace, extra = parser.parse_known_args([token])
    except SystemExit:
      wrong[prefix] = "ambiguous or rejected"
      continue
    if extra or dest not in vars(namespace):
      wrong[prefix] = f"reached {sorted(vars(namespace))} extra={extra}"
  assert not wrong, wrong


def test_golden_pins_the_operator_prefixes():
  assert GOLDEN["prefixes"]["landing"] == "landing_table"
  assert GOLDEN["prefixes"]["batch"] == "batch_size"
