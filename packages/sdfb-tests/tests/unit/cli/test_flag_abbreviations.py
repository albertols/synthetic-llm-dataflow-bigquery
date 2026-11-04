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
"""Master's abbreviated flags (`--landing`, `--batch`, `--t`) still reach their flags.

argparse takes any unambiguous prefix of a long option; each flag this branch
added can make a prefix master accepted ambiguous. Every such prefix, of any
length, keeps reaching master's flag through a hidden alias
(`run_pipeline._FLAG_ALIASES`). The golden is every prefix of three or more
characters unambiguous on master `edcfdcd`, mapped to its dest
(`scripts/config/make_abbreviation_golden.py`); the one- and two-character
spellings are hand-listed below, and are derived again from the golden's
master flag names so a later flag cannot break one unnoticed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

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


# The short spellings a flag added since master made ambiguous: `--tables`
# (`--t`), `--landing_dataset` and `--launcher_machine_type` (`--l`, `--la`),
# `--batch_index` and `--batch_count` (`--ba`), `--gpu` and
# `--generator_defaults_digest` (`--g`, `--ge`).
SHORT_MASTER_SPELLINGS = (
    ("--t", "gs://bucket/thresholds.yaml", "thresholds_uri"),
    ("--l", "proj.ds.X_TABLE", "landing_table"),
    ("--la", "proj.ds.X_TABLE", "landing_table"),
    ("--ba", "32", "batch_size"),
    ("--g", "false", "generate_fk_relationships"),
    ("--ge", "false", "generate_fk_relationships"),
)


@pytest.mark.parametrize("spelling,value,dest", SHORT_MASTER_SPELLINGS)
def test_short_master_spelling_reaches_masters_flag(spelling, value, dest):
  namespace, extra = generator_parser().parse_known_args([spelling, value])
  assert not extra
  assert set(vars(namespace)) == {dest}
  expected = int(value) if dest == "batch_size" else value
  assert getattr(namespace, dest) == expected


def test_every_short_master_abbreviation_still_parses():
  """Every one- or two-character prefix unambiguous on master still parses.

  Master's flag names are the golden's dests (each master flag's dest is
  its name), so this needs no master checkout.
  """
  names = sorted(GOLDEN["dests"])
  wrong = {}
  for size in (1, 2):
    for name in names:
      prefix = name[:size]
      if sum(other.startswith(prefix) for other in names) != 1:
        continue
      token = f"--{prefix}=" + _value(GOLDEN["dests"][name])
      try:
        namespace, extra = generator_parser().parse_known_args([token])
      except SystemExit:
        wrong[prefix] = "ambiguous or rejected"
        continue
      if extra or name not in vars(namespace):
        wrong[prefix] = f"reached {sorted(vars(namespace))} extra={extra}"
  assert not wrong, wrong
  short = {s for s, _, _ in SHORT_MASTER_SPELLINGS}
  assert short <= {f"--{n[:k]}" for n in names for k in (1, 2)}


@pytest.mark.parametrize("spelling", ["--b", "--en"])
def test_a_prefix_master_rejected_still_stops_as_ambiguous(spelling, capsys):
  """Aliases cover master's spellings only; they add no new short ones.

  On master `--b` could be `--batch_size` or `--build_*_layer`, and `--en`
  could be `--engine` or `--env`, so master stopped both as ambiguous and no
  launch can carry them. They must keep stopping, loudly, not reach a flag.
  """
  with pytest.raises(SystemExit):
    generator_parser().parse_known_args([spelling, "x"])
  assert "ambiguous option" in capsys.readouterr().err
