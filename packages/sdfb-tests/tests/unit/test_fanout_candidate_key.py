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
"""ADR 0046 on DirectRunner: TABLE_A -> TABLE_B -> TABLE_D, where TABLE_B
inherits the triple (P, Q, R) from TABLE_A on its widened driving edge
and TABLE_D is driven by TABLE_B on that triple. With TABLE_A's candidate
key adopted, TABLE_D is driven by one distinct triple per TABLE_A row in
BOTH engines; without it the per-column triple collides and TABLE_D loses
parent keys — the corp b1-vs-b2 gap in miniature.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0036, 0046).
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=unnecessary-lambda-assignment

from __future__ import annotations

import json
import logging
from pathlib import Path

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import FkEdgeSpec, PipelineConfig, TableSpec, build_relational_pipeline
from sdfb_core.contracts import TableSchema
from sdfb_core.observability import parse_milestone
from sdfb_tests.fakes import FakeModelClient

_A_ROWS = 3_000


def _schema(table: str, cols: list[tuple[str, str]]) -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": f"p.src.{table}"
      },
      "schema": [{
          "name": n,
          "type": t,
          "mode": "REQUIRED"
      } for n, t in cols]
  })


def _read(prefix: Path) -> list[dict]:
  rows = []
  for f in sorted(prefix.parent.glob(prefix.name + "*")):
    rows.extend(json.loads(line) for line in f.read_text().splitlines() if line)
  return rows


_TRIPLE = ("P", "Q", "R")


def _run(tmp_path: Path, engine: str, candidate_keys: tuple) -> dict:
  # W is a free column: without it the whole non-identity row IS the
  # triple, and the root's exact barrier would divert triple collisions
  # as `row.duplicate`, hiding the defect this test pins.
  a_schema = _schema("TABLE_A",
                     [("K", "INT64"), ("P", "STRING"), ("Q", "INT64"),
                      ("R", "INT64"), ("W", "INT64")])
  a_ref = [{
      "K": i,
      "P": "X",
      "Q": 100 + i % 11,
      "R": 10_000 + 13 * i,
      "W": (i * 7_919_993) % 1_000_000_007,
  } for i in range(300)]
  b_schema = _schema("TABLE_B",
                     [("K", "INT64"), ("P", "STRING"), ("Q", "INT64"),
                      ("R", "INT64"), ("S", "INT64")])
  b_ref = [{
      "K": r["K"],
      "P": r["P"],
      "Q": r["Q"],
      "R": r["R"],
      "S": j
  } for r in a_ref[:100] for j in range(2)]
  d_schema = _schema("TABLE_D", [("P", "STRING"), ("Q", "INT64"),
                                 ("R", "INT64"), ("V", "INT64")])
  d_ref = [{"P": "X", "Q": 100, "R": 10_000, "V": i} for i in range(50)]

  def cfg(schema, ref, name, **kw):
    return PipelineConfig(
        table_schema=schema,
        engine_name=engine,
        model_client=FakeModelClient(reference_pool=ref),
        run_id=f"ck-{name}",
        landing_table=f"p.land.{name}",
        log_table_prefix=name,
        batch_size=500,
        **kw)

  specs = [
      TableSpec(
          config=cfg(
              a_schema,
              a_ref,
              "TABLE_A",
              num_rows=_A_ROWS,
              pk_columns=("K",),
              identity_columns=("K",),
              candidate_keys=candidate_keys),
          reference_rows=a_ref,
          landing_sink=WriteToJsonLines(str(tmp_path / "TABLE_A")),
          dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_TABLE_A"))),
      TableSpec(
          config=cfg(
              b_schema,
              b_ref,
              "TABLE_B",
              num_rows=1,
              pk_columns=("K", "S"),
              uniqueness_mode="streaming",
              fanout={
                  "driving_cols": ["K", "P", "Q", "R"],
                  "histogram": {
                      "1": 1,
                      "2": 1
                  },
                  "cells": None,
                  "exact_cells": False
              }),
          reference_rows=b_ref,
          landing_sink=WriteToJsonLines(str(tmp_path / "TABLE_B")),
          dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_TABLE_B")),
          parent_edges=(FkEdgeSpec(
              child_cols=("K", "P", "Q", "R"),
              ref_cols=("K", "P", "Q", "R"),
              parent_landing="p.land.TABLE_A",
              parent_table="TABLE_A",
              parent_pk=("K",),
              mode="fanout",
              keys_per_batch=200,
              anchor_cols=("K",)),)),
      TableSpec(
          config=cfg(
              d_schema,
              d_ref,
              "TABLE_D",
              num_rows=1,
              uniqueness_mode="streaming",
              fanout={
                  "driving_cols": list(_TRIPLE),
                  "histogram": {
                      "1": 1
                  },
                  "cells": None,
                  "exact_cells": False
              }),
          reference_rows=d_ref,
          landing_sink=WriteToJsonLines(str(tmp_path / "TABLE_D")),
          dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_TABLE_D")),
          parent_edges=(FkEdgeSpec(
              child_cols=_TRIPLE,
              ref_cols=_TRIPLE,
              parent_landing="p.land.TABLE_B",
              parent_table="TABLE_B",
              parent_pk=("K", "S"),
              mode="fanout",
              keys_per_batch=200,
              anchor_cols=("K",)),)),
  ]
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_relational_pipeline(p, specs)
  return {
      name: _read(tmp_path / name) for name in ("TABLE_A", "TABLE_B", "TABLE_D")
  }


def _collapse_lines(caplog) -> list[dict]:
  parsed = (parse_milestone(r.getMessage()) for r in caplog.records)
  return [m for m in parsed if m and m.get("name") == "fk_parent_key_collapse"]


@pytest.mark.parametrize("engine", ["b1_rag", "b2_library"])
def test_grandchild_is_driven_by_every_root_key(tmp_path, caplog, engine):
  caplog.set_level(logging.INFO)
  out = _run(tmp_path, engine, ({"cols": list(_TRIPLE), "pk": ["K"]},))
  a, b, d = out["TABLE_A"], out["TABLE_B"], out["TABLE_D"]
  assert len(a) == _A_ROWS
  assert len({tuple(r[c] for c in _TRIPLE) for r in a}) == len(a)
  # TABLE_B inherits the triple: one per TABLE_A key, every key used.
  assert len({r["K"] for r in b}) == len(a)
  assert len({tuple(r[c] for c in _TRIPLE) for r in b}) == len(a)
  # TABLE_D fans out over every distinct triple — parent-key coverage 1.
  d_parents = {tuple(r[c] for c in _TRIPLE) for r in d}
  assert len(d_parents) == len(a)
  assert d_parents <= {tuple(r[c] for c in _TRIPLE) for r in b}
  lines = {m["child"]: m for m in _collapse_lines(caplog)}
  assert int(lines["TABLE_D"]["landed"]) == len(a)
  assert int(lines["TABLE_D"]["expected"]) == len(a)
  assert lines["TABLE_D"]["expected_method"] == "anchor_distinct"


@pytest.mark.parametrize("engine", ["b1_rag", "b2_library"])
def test_without_the_candidate_key_the_grandchild_loses_parents(
    tmp_path, caplog, engine):
  caplog.set_level(logging.INFO)
  out = _run(tmp_path, engine, ())
  a, d = out["TABLE_A"], out["TABLE_D"]
  d_parents = {tuple(r[c] for c in _TRIPLE) for r in d}
  assert len(d_parents) < len(a)
  lines = {m["child"]: m for m in _collapse_lines(caplog)}
  assert int(lines["TABLE_D"]["landed"]) < int(lines["TABLE_D"]["expected"])
