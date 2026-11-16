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
"""Candidate keys are IMPLIED by the model's driving edges (ADR 0046):
no YAML key, the ``ref_cols`` of every driving edge traced back through
widened edges to the table that generates them.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0046).
"""

from __future__ import annotations

from sdfb_core.contracts.relationships import RelationshipRegistry


def _registry(text: str) -> RelationshipRegistry:
  return RelationshipRegistry.from_sources([("config/relationships/ck.yaml",
                                             text)])


# The corp shape, anonymised: TABLE_D references the triple in BOTH
# TABLE_B and TABLE_A, so the registry widens TABLE_B's edge to TABLE_A
# with the triple (`fk_edge_widened`) and TABLE_D is driven by TABLE_B.
_CORP = """
model: ck
tables:
  TABLE_A:
    pk: [COL_KEY]
  TABLE_B:
    pk: [COL_KEY, COL_SEQ]
    fk:
      - cols: [COL_KEY]
        ref: TABLE_A
        ref_cols: [COL_KEY]
  TABLE_D:
    fk:
      - cols: [COL_P, COL_Q, COL_R]
        ref: TABLE_B
        ref_cols: [COL_P, COL_Q, COL_R]
      - cols: [COL_P, COL_Q, COL_R]
        ref: TABLE_A
        ref_cols: [COL_P, COL_Q, COL_R]
"""


def test_the_inherited_triple_is_a_candidate_key_of_its_owner():
  records = _registry(_CORP).implied_candidate_keys()
  assert len(records) == 1
  record = records[0]
  assert record == {
      "owner": "TABLE_A",
      "cols": ("COL_P", "COL_Q", "COL_R"),
      "pk": ("COL_KEY",),
      "child": "TABLE_D",
      "parent": "TABLE_B",
      "parent_cols": ("COL_P", "COL_Q", "COL_R"),
      "anchor": ("COL_KEY",),
      "owner_driven": False,
  }


def test_the_driving_parent_anchor_is_the_owner_pk_image():
  reg = _registry(_CORP)
  assert reg.driving_key_anchor("TABLE_D") == ("COL_KEY",)
  # TABLE_B is driven by TABLE_A's PK itself: its anchor IS that PK.
  assert reg.driving_key_anchor("TABLE_B") == ("COL_KEY",)
  assert not reg.driving_key_anchor("TABLE_A")


def test_a_driving_edge_onto_the_parent_pk_implies_nothing():
  reg = _registry("""
model: ck
tables:
  P:
    pk: [K]
  C:
    fk:
      - cols: [K]
        ref: P
        ref_cols: [K]
""")
  assert not reg.implied_candidate_keys()


def test_a_tuple_generated_by_a_driven_owner_is_flagged():
  reg = _registry("""
model: ck
tables:
  A:
    pk: [K]
  B:
    pk: [K, S]
    fk:
      - cols: [K]
        ref: A
        ref_cols: [K]
  D:
    fk:
      - cols: [X, Y]
        ref: B
        ref_cols: [X, Y]
""")
  records = reg.implied_candidate_keys()
  assert len(records) == 1
  record = records[0]
  assert record["owner"] == "B"
  assert record["owner_driven"] is True
  assert record["anchor"] == ("K", "S")


def test_an_owner_without_a_pk_implies_nothing():
  reg = _registry("""
model: ck
tables:
  P: {}
  C:
    fk:
      - cols: [X]
        ref: P
        ref_cols: [X]
""")
  assert not reg.implied_candidate_keys()
