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
"""`scripts/doc/b1_rag_walkthrough.py` — the data examples of article 4.

`docs/articles/04-b1-rag-deep-dive.md` quotes this script's output as
"what the code does". These tests pin the claims the article builds on, so
a change to the retrieval path that would make the article wrong fails
here first — the article then flips to `Needs-sync` on purpose, not by
accident.

Loaded via importlib the same way `test_relationships_card.py` loads its
script.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(
    __file__).parents[5] / "scripts" / "doc" / "b1_rag_walkthrough.py"
_spec = importlib.util.spec_from_file_location("b1_rag_walkthrough", _SCRIPT)
walkthrough = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = walkthrough
_spec.loader.exec_module(walkthrough)


def _section(out: str, number: int) -> str:
  start = out.index(f"\n{number}. ")
  nxt = out.find(f"\n{number + 1}. ", start)
  return out[start:nxt if nxt != -1 else len(out)]


def test_the_walkthrough_runs_end_to_end_on_a_bare_laptop(capsys):
  walkthrough.main()
  out = capsys.readouterr().out
  for number in range(1, 8):
    assert f"\n{number}. " in out


def test_row_identity_survives_key_order_and_the_text_is_great_style(capsys):
  walkthrough.step_chunking()
  out = capsys.readouterr().out
  assert ("row_doc text   : txn_id is T00104, channel is pos, amount is 4.5, "
          "currency is EUR, merchant_name is CAFE ARBOL*MADRID") in out
  assert "(differ)" in out and "(identical)" in out
  assert "row_doc text identical: True" in out
  assert "96 rows -> 81 distinct values to embed" in out
  assert "source_pk  : None" in out


def test_centroid_stays_in_the_dominant_format_and_kcenter_spans_all(capsys):
  vectors, values = walkthrough.step_embedding()
  capsys.readouterr()
  walkthrough.step_strategies(vectors, values)
  out = capsys.readouterr().out
  centroid, rest = out.split("--pool_seed_strategy=kcenter\n", 1)
  kcenter = rest.split("--pool_seed_strategy=kcenter_rotate", 1)[0]
  assert "formats reached: ['name*city']" in centroid
  assert ("formats reached: ['aggregator', 'name*city', 'transport', 'web']"
          in kcenter)


def test_the_gates_account_for_every_scripted_candidate(capsys):
  vectors, values = walkthrough.step_embedding()
  seeds = walkthrough.step_strategies(vectors, values)
  capsys.readouterr()
  walkthrough.step_prompt_and_gates(seeds)
  out = capsys.readouterr().out
  assert "head_values [('ATM WITHDRAWAL', 0.167)]" in out
  assert "Column constraint: format=short store name" in out
  assert ("gate outcome   : parsed 12 | format_rejected 2 | copies 2 | "
          "seed echoes 1 | attempts 1") in out
  pool_line = next(
      line for line in out.splitlines() if line.startswith("pool "))
  for real in ("CAFE ARBOL*MADRID", "FARMACIA RIO*MALAGA", "merchant_name"):
    assert real not in pool_line


def test_no_generated_row_is_a_copy_and_the_head_literal_is_re_emitted(capsys):
  walkthrough.step_end_to_end()
  rows = _section("\n" + capsys.readouterr().out, 6)
  assert "COPY" not in rows
  assert "<- novel" in rows
  assert "<- head literal" in rows


def test_pes_mode_names_are_categorical_until_routed_and_renames_pass_the_wall(
    capsys):
  """The claims of article 4's "PES mode" section, against the real
    profiler, picker and gates."""
  walkthrough.step_pes_mode()
  out = capsys.readouterr().out
  assert "no clause      -> kind = categorical" in out
  assert 'route: "llm"   -> kind = free_text' in out
  assert "row_doc text   : player_name is Roberto Carlos, shirt is 8" in out
  assert ("gate outcome   : parsed 22 | format_rejected 4 | copies 3 | "
          "seed echoes 2") in out
  verdicts = {
      line[:20].strip(): line[21:39].strip()
      for line in out.splitlines()
      if line[21:39].strip() in ("COPY - rejected", "pooled (rename!)",
                                 "pooled", "pooled (off-style)", "off-format")
  }
  assert verdicts["Roberto Carlos"] == "COPY - rejected"
  assert verdicts["Cafu"] == "COPY - rejected"  # never shown as a seed
  assert verdicts["Roberto Larcos"] == "pooled (rename!)"
  assert verdicts["Fergalinho"] == "pooled"
  assert verdicts["Raphinha Keane"] == "pooled"
  assert verdicts["Seamus da Silva"] == "pooled"
  assert verdicts["Paddy O'Rivaldo"] == "off-format"
  assert verdicts["RONALDO 9"] == "off-format"
  # No gate checks style: a name with nothing Brazilian left is pooled.
  assert verdicts["Ciaran Kelly"] == "pooled (off-style)"
  pool_line = next(
      line for line in out.splitlines() if line.startswith("pool "))
  assert "'Roberto Carlos'" not in pool_line
  assert "'Ronaldinho'" not in pool_line
  assert "'Ciaran Kelly'" in pool_line
  assert len(verdicts) == len(walkthrough.SQUAD_CANDIDATES)
