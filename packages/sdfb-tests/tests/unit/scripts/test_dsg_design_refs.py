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
"""Unit tests for `scripts/doc/sync_design_refs.py`.

Code cites decisions by number (`ADR 0036`), but the decision records do not
ship to the Dataflow Solution Guides. Each shipped module that cites one
carries a generated docstring line naming the section of `docs/DESIGN.md` to
read, and the map in that document is the only place the pairing is typed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[5]
_SCRIPT = _ROOT / "scripts" / "doc" / "sync_design_refs.py"
_spec = importlib.util.spec_from_file_location("doc_design_refs", _SCRIPT)
refs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = refs
_spec.loader.exec_module(refs)

_DESIGN = """# Design

## 4. Relational generation

## 6. Throughput

<!-- adr-map:start -->
| ADR | Section | Decision record |
| :-- | :-- | :-- |
| 0030 | [§4 Relational generation](#4-relational-generation) | [x](adr/0030-x.md) |
| 0034 | [§6 Throughput](#6-throughput) | [y](adr/0034-y.md) |
| 0036 | [§4 Relational generation](#4-relational-generation) | [z](adr/0036-z.md) |
<!-- adr-map:end -->
"""
_MAP = {
    "0030": "§4 Relational generation",
    "0034": "§6 Throughput",
    "0036": "§4 Relational generation",
}


def test_the_map_is_read_from_the_design_document():
  assert refs.parse_adr_map(_DESIGN) == _MAP


def test_citations_cover_the_forms_the_code_uses():
  text = ("# ADR 0036: a\n# see ADR 0030/0034\n# ADR-0036 again\n"
          "# (ADR 0034, 2026-08-25 run) is a date, not a list\n")
  assert refs.cited_adrs(text) == ["0030", "0034", "0036"]


def test_design_line_groups_sections_and_sorts():
  assert refs.design_line(["0036", "0030", "0034"], _MAP) == (
      "Design: docs/DESIGN.md §4 Relational generation; §6 Throughput\n"
      "(ADR 0030, 0034, 0036).")
  assert all(
      len(line) <= 79
      for line in refs.design_line(["0030", "0034", "0036"], _MAP).split("\n"))


def test_a_long_design_line_never_splits_a_section_label():
  wide = {
      "0001": "§4 Relational generation",
      "0002": "§5 Fidelity",
      "0003": "§6 Throughput",
      "0004": "§8 Configuration",
  }
  lines = refs.design_line(sorted(wide), wide).split("\n")
  assert lines == [
      "Design: docs/DESIGN.md §4 Relational generation; §5 Fidelity; "
      "§6 Throughput;",
      "§8 Configuration",
      "(ADR 0001, 0002, 0003, 0004).",
  ]


def test_an_adr_with_no_map_row_is_an_error():
  with pytest.raises(refs.DesignRefError, match="ADR 0099"):
    refs.design_line(["0099"], _MAP)


def test_a_one_line_docstring_gains_the_line():
  source = '#  header\n"""Fan-out."""\n\nX = 1  # ADR 0036\n'
  assert refs.apply(
      source,
      _MAP) == ('#  header\n"""Fan-out.\n\n'
                "Design: docs/DESIGN.md §4 Relational generation\n(ADR 0036).\n"
                '"""\n\nX = 1  # ADR 0036\n')


def test_a_multi_line_docstring_gains_a_last_paragraph():
  source = '"""Fan-out.\n\nDetails.\n"""\n\nX = 1  # ADR 0036\n'
  assert refs.apply(
      source,
      _MAP) == ('"""Fan-out.\n\nDetails.\n\n'
                "Design: docs/DESIGN.md §4 Relational generation\n(ADR 0036).\n"
                '"""\n\nX = 1  # ADR 0036\n')


def test_apply_is_idempotent_and_refreshes_a_stale_line():
  source = '"""Fan-out."""\n\nX = 1  # ADR 0036\n'
  once = refs.apply(source, _MAP)
  assert refs.apply(once, _MAP) == once
  grown = once + "Y = 2  # ADR 0034\n"
  assert ("Design: docs/DESIGN.md §4 Relational generation; §6 Throughput\n"
          "(ADR 0034, 0036).\n") in refs.apply(grown, _MAP)


def test_a_module_that_cites_nothing_is_left_alone():
  source = '"""Plain."""\n\nX = 1\n'
  assert refs.apply(source, _MAP) == source


def test_the_line_goes_when_the_last_citation_goes():
  cited = refs.apply('"""Fan-out.\n\nDetails.\n"""\n\nX = 1  # ADR 0036\n',
                     _MAP)
  uncited = cited.replace("  # ADR 0036", "")
  assert refs.apply(uncited, _MAP) == ('"""Fan-out.\n\nDetails.\n"""\n\n'
                                       "X = 1\n")


def test_a_citing_module_needs_a_docstring():
  with pytest.raises(refs.DesignRefError, match="module docstring"):
    refs.apply("X = 1  # ADR 0036\n", _MAP)


def test_this_repository_is_in_sync_with_its_design_document():
  assert refs.check(_ROOT) == []
