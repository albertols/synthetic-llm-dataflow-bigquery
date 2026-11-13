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
"""Both engines generate an adopted candidate key 1:1 with the rows they
emit, across batches, with the same mechanism (ADR 0046).

The root carries K (its PK) and a triple (P constant, Q a low-card code,
R a wide integer) that is 1:1 with K in the reference rows. Sampled per
column the triple collides; with the launcher's ``candidate_keys`` entry
and the root request's ``row_offset`` it cannot.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0036, 0046).
"""

from __future__ import annotations

import pytest
from sdfb_beam.handlers.fake_client import FakeModelClient
from sdfb_core.contracts.schema import TableSchema
from sdfb_core.engines import GenerationConfig, GenerationContext
from sdfb_core.engines.b1_rag import B1RagEngine, HashingEmbedder
from sdfb_core.engines.b2_library import B2LibraryEngine

_ROWS = 20_000
_BATCH = 2_500


def _ctx(candidate_keys: list[dict] | None = None) -> GenerationContext:
  schema = TableSchema.model_validate({
      "table_info": {
          "table_id": "p.src.TABLE_A"
      },
      "schema": [{
          "name": n,
          "type": t,
          "mode": "REQUIRED"
      } for n, t in (("K", "INT64"), ("P", "STRING"), ("Q", "INT64"),
                     ("R", "INT64"))],
  })
  rows = [{
      "K": 7_000_000 + i,
      "P": "X",
      "Q": 100 + i % 11,
      "R": 3_000_000 + 4_091 * i,
  } for i in range(600)]
  return GenerationContext(
      table_schema=schema,
      reference_rows=rows,
      reference_digest="ck",
      pipeline_run_id="ck-run",
      landing_table="p.land.TABLE_A",
      pk_columns=["K"],
      num_rows=_ROWS,
      candidate_keys=candidate_keys or [],
  )


def _engine(name: str, ctx: GenerationContext):
  engine = (
      B1RagEngine(embedder=HashingEmbedder(
          dim=64)) if name == "b1_rag" else B2LibraryEngine(use_sdgx=False))
  engine.setup(FakeModelClient(reference_pool=ctx.reference_rows), ctx)
  return engine


def _generate(engine) -> list:
  out = []
  for batch_id, offset in enumerate(range(0, _ROWS, _BATCH)):
    cfg = GenerationConfig(
        seed=11 + batch_id,
        batch_size=_BATCH,
        engine_specific={"row_offset": offset})
    out.extend(engine.generate_batch(_BATCH, cfg))
  return out


_ENTRY = [{"cols": ["P", "Q", "R"], "pk": ["K"]}]


@pytest.mark.parametrize("engine_name", ["b1_rag", "b2_library"])
def test_candidate_key_is_one_to_one_across_batches(engine_name):
  rows = _generate(_engine(engine_name, _ctx(_ENTRY)))
  assert len(rows) == _ROWS
  triples = {(r.P, r.Q, r.R) for r in rows}
  assert len(triples) == len(rows)
  observed_r = {r["R"] for r in _ctx().reference_rows}
  assert {r.P for r in rows} == {"X"}  # the constant stays constant
  assert all(100 <= r.Q <= 110 for r in rows)  # the engine's own draw
  assert not {r.R for r in rows} & observed_r  # no source value copied
  assert all(min(observed_r) < r.R < max(observed_r) for r in rows)


@pytest.mark.parametrize("engine_name", ["b1_rag", "b2_library"])
def test_without_the_entry_the_triple_collides(engine_name):
  """The defect the entry fixes: per-column draws of the same triple."""
  rows = _generate(_engine(engine_name, _ctx()))
  assert len({(r.P, r.Q, r.R) for r in rows}) < len(rows)


def test_both_engines_draw_the_same_discriminator():
  """One shared mechanism: the same run, sample and ordinals give both
    engines the same R column."""
  b1 = _generate(_engine("b1_rag", _ctx(_ENTRY)))
  b2 = _generate(_engine("b2_library", _ctx(_ENTRY)))
  assert [r.R for r in b1] == [r.R for r in b2]


@pytest.mark.parametrize("engine_name", ["b1_rag", "b2_library"])
def test_a_request_without_row_offset_keeps_the_per_column_draw(engine_name):
  engine = _engine(engine_name, _ctx(_ENTRY))
  baseline = _engine(engine_name, _ctx())
  cfg = GenerationConfig(seed=3, batch_size=500)
  assert [r.R for r in engine.generate_batch(500, cfg)
         ] == [r.R for r in baseline.generate_batch(500, cfg)]
