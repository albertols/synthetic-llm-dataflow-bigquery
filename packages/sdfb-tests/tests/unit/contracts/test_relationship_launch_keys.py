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
"""A model's per-table `source_table` and `num_rows` (ADR 0045): where a
whole-model launch reads a table's source and how many rows the table gets.
Both are absent unless set, so every existing model keeps its sha12.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sdfb_core.contracts.relationships import (RelationshipError,
                                               RelationshipRegistry,
                                               TableRelations)

_ROOT = Path(__file__).resolve().parents[5]
_MODELS = _ROOT / "config" / "relationships"
# RelationshipRegistry.sha12() of each committed model before ADR 0045 (the
# evaluator's parity goldens pin the same values).
_SHA_BEFORE = {
    "example_retail.yaml": "3f4df8793c4f",
    "example_star_diamond.yaml": "699326e27ea6",
    "gcp_public_fk_example.yaml": "eeb64408371e",
}


def _registry(text: str) -> RelationshipRegistry:
  return RelationshipRegistry.from_sources([("m.yaml", text)])


def _one_table(**spec) -> str:
  return yaml.safe_dump({
      "model": "m",
      "tables": {
          "T_A": {
              "pk": ["C1"],
              **spec
          }
      }
  })


@pytest.mark.parametrize("name", sorted(_SHA_BEFORE))
def test_absent_keys_leave_every_committed_sha_unchanged(name):
  text = (_MODELS / name).read_text(encoding="utf-8")
  assert _registry(text).sha12() == _SHA_BEFORE[name]


def test_the_keys_default_to_none():
  relations = TableRelations()
  assert relations.source_table is None and relations.num_rows is None


def test_an_override_moves_the_sha_and_null_is_absent():
  base = _registry(_one_table()).sha12()
  assert _registry(_one_table(source_table="ds.T_A_V2")).sha12() != base
  assert _registry(_one_table(num_rows=5000)).sha12() != base
  assert _registry(_one_table(source_table=None, num_rows=None)).sha12() == base


@pytest.mark.parametrize("value", ["ds.T_A", "proj-1.ds.T_A", "p.d_s.T-x"])
def test_a_source_table_is_dataset_and_table_with_an_optional_project(value):
  registry = _registry(_one_table(source_table=value))
  assert registry.relations("T_A").source_table == value


@pytest.mark.parametrize("value",
                         ["T_A", "a.b.c.d", "", "ds..T", "ds.T A", 7, True])
def test_a_malformed_source_table_is_a_load_error(value):
  with pytest.raises(RelationshipError, match="source_table"):
    _registry(_one_table(source_table=value))


@pytest.mark.parametrize("value", [1, 5000])
def test_num_rows_is_a_positive_integer(value):
  registry = _registry(_one_table(num_rows=value))
  assert registry.relations("T_A").num_rows == value


@pytest.mark.parametrize("value", [0, -1, "5000", 5000.5, 5000.0, True])
def test_num_rows_is_strict(value):
  with pytest.raises(RelationshipError, match="num_rows"):
    _registry(_one_table(num_rows=value))


_CHILD = """model: m
tables:
  T_P:
    pk: [P1]{parent}
  T_C:
    num_rows: 10{child}
    fk:
      - cols: [P1]
        ref: {ref}
        ref_cols: [P1]{edge}
"""


def _chain(*, parent="", child="", ref="T_P", edge="") -> str:
  return _CHILD.format(parent=parent, child=child, ref=ref, edge=edge)


def test_num_rows_on_a_driven_child_is_a_load_error():
  with pytest.raises(
      RelationshipError,
      match=r"T_C: num_rows is set, but T_C is generated from "
      r"its parent T_P's keys"):
    _registry(_chain())


@pytest.mark.parametrize(
    "shape",
    [
        {
            "edge": "\n        enforced: false"
        },  # documented, never drawn
        {
            "parent": "\n    enabled: false"
        },  # the parent is detached
        {
            "child": "\n    enabled: false"
        },  # the child is detached
        {
            "ref": "other_ds.T_P"
        },  # an external parent drives nothing
    ],
    ids=["documented_edge", "disabled_parent", "disabled_child", "external"])
def test_num_rows_is_allowed_where_no_parent_drives_the_table(shape):
  assert _registry(_chain(**shape)).relations("T_C").num_rows == 10


def test_the_card_names_an_override():
  registry = _registry(
      _one_table(source_table="other_ds.T_A_V2", num_rows=5000))
  assert "pk(C1)   source other_ds.T_A_V2 · rows 5000" in registry.card("T_A")


def test_a_card_without_overrides_is_unchanged():
  # The card's head names the model's source file ("| source m.yaml"): an
  # override is the three-space suffix after a table's keys.
  card = _registry(_one_table()).card("T_A")
  assert "   source " not in card and "   rows " not in card
