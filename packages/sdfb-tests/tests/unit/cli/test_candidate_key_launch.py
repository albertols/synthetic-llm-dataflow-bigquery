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
"""The launcher adopts an implied candidate key only when the SOURCE
measures it 1:1 with the owner's PK, and says so either way (ADR 0046).

Design: docs/DESIGN.md §4 Relational generation
(ADR 0038, 0046).
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=protected-access

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from sdfb_beam.cli import run_pipeline as rp
from sdfb_beam.io.fanout_stats import measure_candidate_key
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.observability import parse_milestone

_MODEL = """
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


def _registry() -> RelationshipRegistry:
  return RelationshipRegistry.from_sources([("ck.yaml", _MODEL)])


def _schema() -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "p.src.TABLE_A"
      },
      "schema": [{
          "name": n,
          "type": t,
          "mode": "REQUIRED"
      } for n, t in (("COL_KEY", "STRING"), ("COL_P", "STRING"),
                     ("COL_Q", "INT64"), ("COL_R", "INT64"))],
  })


_REF = [{
    "COL_KEY": f"k{i}",
    "COL_P": "C",
    "COL_Q": 10 + i % 5,
    "COL_R": 1_000_000 + 211 * i
} for i in range(500)]


def _resolve(monkeypatch,
             measured,
             *,
             in_set=("TABLE_A", "TABLE_B", "TABLE_D"),
             **kw):
  if isinstance(measured, Exception):

    def _measure(**_):
      raise measured
  else:

    def _measure(**_):
      return measured

  monkeypatch.setattr(rp, "measure_candidate_key", _measure)
  return rp.resolve_candidate_keys(
      _registry(),
      "proj.land.TABLE_A",
      "src.TABLE_A",
      in_set_names=set(in_set),
      reference_rows=_REF,
      table_schema=_schema(),
      num_rows=kw.pop("num_rows", 10_000),
      driven=kw.pop("driven", False),
  )


def _milestones(caplog, name: str) -> list[dict]:
  parsed = (parse_milestone(r.getMessage()) for r in caplog.records)
  return [m for m in parsed if m and m.get("name") == name]


# The corp source, anonymised: 53,925 keys, 53,925 triples.
_ONE_TO_ONE = {
    "rows": 163_394,
    "pk_values": 53_925,
    "tuples": 53_925,
    "pairs": 53_925
}


def test_a_source_one_to_one_tuple_is_adopted(monkeypatch, caplog):
  caplog.set_level(logging.INFO)
  adopted = _resolve(monkeypatch, _ONE_TO_ONE)
  assert adopted == ({"cols": ["COL_P", "COL_Q", "COL_R"], "pk": ["COL_KEY"]},)
  (line,) = _milestones(caplog, "candidate_key_adopted")
  assert line["owner"] == "TABLE_A"
  assert line["cols"] == "COL_P,COL_Q,COL_R"
  assert line["child"] == "TABLE_D"
  assert line["tuples"] == "53925"
  assert line["source"] == "measured"
  assert line["discriminator"] == "COL_R"


def test_a_tuple_shared_by_keys_is_left_alone(monkeypatch, caplog):
  """Negative: NOT 1:1 in the source — per-column generation, announced."""
  caplog.set_level(logging.INFO)
  shared = {**_ONE_TO_ONE, "tuples": 8_824, "pairs": 53_925}
  assert not _resolve(monkeypatch, shared)
  (line,) = _milestones(caplog, "candidate_key_rejected")
  assert line["reason"] == "not_one_to_one"
  assert line["tuples"] == "8824"
  assert line["pk_values"] == "53925"


def test_a_failed_measurement_falls_back_to_the_sample(monkeypatch, caplog):
  caplog.set_level(logging.INFO)
  adopted = _resolve(monkeypatch, RuntimeError("no bq"))
  assert adopted  # the sample is 1:1 too
  (line,) = _milestones(caplog, "candidate_key_adopted")
  assert line["source"] == "sample"
  assert _milestones(caplog, "candidate_key_measure_fallback")


def test_capacity_short_is_rejected_not_stopped(monkeypatch, caplog):
  caplog.set_level(logging.INFO)
  assert not _resolve(monkeypatch, _ONE_TO_ONE, num_rows=10**9)
  (line,) = _milestones(caplog, "candidate_key_rejected")
  assert line["reason"] == "capacity"


def test_nothing_when_the_driven_child_is_not_in_the_launch(monkeypatch):
  assert not _resolve(monkeypatch, _ONE_TO_ONE, in_set=("TABLE_A",))


def test_a_driven_owner_is_reported_not_adopted(monkeypatch, caplog):
  caplog.set_level(logging.INFO)
  assert not _resolve(monkeypatch, _ONE_TO_ONE, driven=True)
  (line,) = _milestones(caplog, "candidate_key_rejected")
  assert line["reason"] == "owner_is_driven"


def test_measure_candidate_key_sql_counts_null_as_a_value():
  client = MagicMock()
  client.query.return_value.result.return_value = [{
      "row_count": 10,
      "pk_values": 4,
      "tuples": 4,
      "pairs": 4
  }]
  out = measure_candidate_key(
      source_table="p.d.T", pk_cols=("K",), cols=("A", "K2"), client=client)
  assert out == {"rows": 10, "pk_values": 4, "tuples": 4, "pairs": 4}
  sql = client.query.call_args.args[0]
  assert "TO_JSON_STRING(STRUCT(`A`, `K2`))" in sql
  assert "TO_JSON_STRING(STRUCT(`K`, `A`, `K2`))" in sql


def test_in_set_parent_edges_carry_the_anchor():
  reg = _registry()
  roles = reg.edge_roles("TABLE_D")
  specs = rp.in_set_parent_edges(
      reg,
      "proj.land.TABLE_D",
      in_set_names={"TABLE_A", "TABLE_B", "TABLE_D"},
      key_sample_caps={},
      edge_roles=roles,
  )
  fanout = [s for s in specs if s.mode == "fanout"]
  assert [s.anchor_cols for s in fanout] == [("COL_KEY",)]
