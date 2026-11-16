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
"""A driven child is sized off the DISTINCT values of its driving edge's
`ref_cols` on the parent — not off the parent's declared-PK distinct count
(ADR 0036 / 0038 amendment, 2026-11-13).

The 2026-11 corp launch, anonymised. TABLE_A is the root (PK COL_KEY) and
owns the key triple (COL_P, COL_Q, COL_R). TABLE_B fans out from TABLE_A on
COL_KEY, inherits the triple from it (`fk_edge_widened`), and its PK is
(COL_KEY, COL_S, COL_T). TABLE_D is driven from TABLE_B by the TRIPLE. At
--num_rows=10M, TABLE_B was adjusted (ADR 0038) and the launcher logged:

    model_adjustment_descendant_rows table=TABLE_D parent=TABLE_B
      parent_rows=59126428 parent_distinct_keys=20005378
      mean_fanout=35.6491 derived_rows=713173103

20.0M is TABLE_B's declared-PK distinct count. TABLE_D fans out over
distinct TRIPLES, which can never exceed TABLE_A's 10M rows — so the plan
asked 713M of a ~356.5M maximum, b1 landed 12.6% and b2 37% of it, and
`validation_runs` still said PASSED.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0036, 0038, 0039).
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=import-outside-toplevel,protected-access,unbalanced-tuple-unpacking

from __future__ import annotations

import logging

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relationships import RelationshipRegistry

_MODEL = """
model: kwd
tables:
  TABLE_A:
    pk: [COL_KEY]
  TABLE_B:
    pk: [COL_KEY, COL_S, COL_T]
    fk:
      - cols: [COL_KEY]
        ref: TABLE_A
        ref_cols: [COL_KEY]
  TABLE_D:
    fk:
      - cols: [COL_P, COL_Q, COL_R]
        ref: TABLE_B
        ref_cols: [COL_P, COL_Q, COL_R]
        drives: true
      - cols: [COL_P, COL_Q, COL_R]
        ref: TABLE_A
        ref_cols: [COL_P, COL_Q, COL_R]
"""

_ROOT_ROWS = 10_000_000
_B_ROWS = 59_126_428
_B_PK_DISTINCT = 20_005_378
# mean = (35 x 3,509 + 36 x 6,491) / 10,000 = 35.6491, the logged mean.
_D_MEASURED = {
    "histogram": {
        "35": 3_509,
        "36": 6_491
    },
    "cells": None,
    "parents": 10_000,
    "children": 356_491,
}
_MEAN = 35.6491


def _reg() -> RelationshipRegistry:
  return RelationshipRegistry.from_sources([("config/relationships/kwd.yaml",
                                             _MODEL)])


def _d_schema() -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "p.src.TABLE_D"
      },
      "schema": [{
          "name": c,
          "type": "STRING",
          "mode": "REQUIRED"
      } for c in ("COL_P", "COL_Q", "COL_R", "COL_V")],
  })


def _run_d(monkeypatch, *, rows, keys):
  import sdfb_beam.cli.run_pipeline as rp

  sample = [{
      "COL_P": f"P{i % 50:03d}",
      "COL_Q": f"Q{i % 50:03d}",
      "COL_R": f"R{i % 50:03d}",
      "COL_V": f"V{i}",
  } for i in range(500)]
  monkeypatch.setattr(rp, "load_reference_rows", lambda **kw: sample)
  monkeypatch.setattr(rp, "measure_fanout", lambda **kw: _D_MEASURED)
  args, _ = rp.parse_args([
      "--reference_table",
      "p.src.TABLE_D",
      "--landing_table",
      "p.land.TABLE_D",
      "--dlq_table",
      "p.dq.dlq",
      "--num_rows",
      str(_ROOT_ROWS),
      "--run_id",
      "finding-a",
      "--source_stats",
      "off",
      "--model_uri",
      "gs://b/models/m/v/",
  ])
  args._rows_by_landing = rows
  args._distinct_keys_by_landing = keys
  _, pf, *_ = rp._load_reference_and_preflight(
      args,
      _d_schema(),
      _reg(),
      in_set_landing=frozenset(
          {"p.land.TABLE_A", "p.land.TABLE_B", "p.land.TABLE_D"}),
  )
  return pf


# --- the registry: who OWNS a parent's columns ------------------------


def test_the_triple_on_table_b_is_owned_by_the_root():
  """TABLE_B's widened edge to TABLE_A carries the triple, so the
    lineage of TABLE_B's (COL_P, COL_Q, COL_R) ends at TABLE_A."""
  cols = ("COL_P", "COL_Q", "COL_R")
  assert _reg().key_lineage("TABLE_B", cols) == (
      ("TABLE_B", cols),
      ("TABLE_A", cols),
  )


def test_a_column_set_no_edge_carries_is_owned_by_the_table_itself():
  reg = _reg()
  assert reg.key_lineage("TABLE_B", ("COL_S",)) == (("TABLE_B", ("COL_S",)),)
  # Partly carried is not carried: COL_S is TABLE_B's own column.
  assert reg.key_lineage("TABLE_B",
                         ("COL_P", "COL_S")) == (("TABLE_B", ("COL_P",
                                                              "COL_S")),)
  assert reg.key_lineage("TABLE_A",
                         ("COL_KEY",)) == (("TABLE_A", ("COL_KEY",)),)


# --- the launcher: the regression ---------------------------------------


def test_table_d_is_sized_off_the_roots_rows_not_table_bs_pk(
    monkeypatch, caplog):
  """The corp shape: ~10M x 35.65, not 20.0M x 35.65."""
  with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
    pf = _run_d(
        monkeypatch,
        rows={
            "p.land.TABLE_A": _ROOT_ROWS,
            "p.land.TABLE_B": _B_ROWS
        },
        keys={
            "p.land.TABLE_A": _ROOT_ROWS,
            "p.land.TABLE_B": _B_PK_DISTINCT
        },
    )
  assert pf.derived_rows == round(_ROOT_ROWS * _MEAN)  # 356,491,000
  assert pf.derived_rows != round(_B_PK_DISTINCT * _MEAN)  # the 713M ask
  line = next(ln for ln in caplog.text.splitlines()
              if "name=model_adjustment_descendant_rows" in ln)
  assert "parent_distinct_keys=10000000" in line
  assert "key_set=inherited" in line
  assert "key_owner=TABLE_A" in line
  assert "owner_rows=10000000" in line


def test_the_bound_is_also_capped_at_the_parents_own_rows(monkeypatch):
  """A parent that lands FEWER rows than the owner cannot hand out more
    distinct triples than its rows."""
  pf = _run_d(
      monkeypatch,
      rows={
          "p.land.TABLE_A": _ROOT_ROWS,
          "p.land.TABLE_B": 4_000_000
      },
      keys={
          "p.land.TABLE_A": _ROOT_ROWS,
          "p.land.TABLE_B": 4_000_000
      },
  )
  assert pf.derived_rows == round(4_000_000 * _MEAN)


def test_an_owner_not_landed_in_this_job_falls_back_to_today(monkeypatch):
  """No figure for the owner: the parent's PK distinct count stands,
    exactly as before (nothing better is known)."""
  pf = _run_d(
      monkeypatch,
      rows={"p.land.TABLE_B": _B_ROWS},
      keys={"p.land.TABLE_B": _B_PK_DISTINCT},
  )
  assert pf.derived_rows == round(_B_PK_DISTINCT * _MEAN)


# --- the pure bound ----------------------------------------------------


@pytest.mark.parametrize(
    "ref_cols, expected, key_set",
    [
        # The parent's own PK: today's number, byte for byte.
        (("COL_KEY", "COL_S", "COL_T"), _B_PK_DISTINCT, "parent_pk"),
        # A superset of the PK has exactly the PK's distinct count.
        (("COL_KEY", "COL_S", "COL_T", "COL_P"), _B_PK_DISTINCT, "parent_pk"),
        # Inherited from the root: the root's rows.
        (("COL_P", "COL_Q", "COL_R"), _ROOT_ROWS, "inherited"),
        # Inherited AND covering the owner's PK: the owner's DISTINCT
        # keys (an adjusted root lands fewer than its rows).
        (("COL_KEY",), 6_000_000, "inherited"),
    ],
)
def test_driving_key_bound(ref_cols, expected, key_set):
  from sdfb_beam.cli.run_pipeline import driving_key_bound

  bound = driving_key_bound(
      _reg(),
      "TABLE_B",
      ref_cols,
      landing_dataset="p.land",
      parent_rows=_B_ROWS,
      rows_by_landing={
          "p.land.TABLE_A": _ROOT_ROWS,
          "p.land.TABLE_B": _B_ROWS
      },
      keys_by_landing={
          "p.land.TABLE_A": 6_000_000,
          "p.land.TABLE_B": _B_PK_DISTINCT
      },
  )
  assert bound.keys == expected
  assert bound.key_set == key_set


# --- the projection agrees with the preflight ----------------------------


def test_the_row_projection_uses_the_same_key_set():
  """ADR 0039: the block must print the count the run requests, so it
    reads the same inherited bound the preflight sized TABLE_D with."""
  from sdfb_beam.cli.run_pipeline import table_projections
  from sdfb_beam.pipeline import FkEdgeSpec, PipelineConfig, TableSpec
  from sdfb_tests.fakes import FakeModelClient

  triple = ("COL_P", "COL_Q", "COL_R")
  config = PipelineConfig(
      table_schema=_d_schema(),
      engine_name="b1_rag",
      model_client=FakeModelClient(reference_pool=[{}]),
      num_rows=round(_ROOT_ROWS * _MEAN),
      landing_table="p.land.TABLE_D",
      fanout={
          "driving_cols": list(triple),
          "histogram": _D_MEASURED["histogram"],
          "cells": None,
          "exact_cells": False,
          "parents": _D_MEASURED["parents"],
      },
  )
  spec = TableSpec(
      config=config,
      reference_rows=[],
      landing_sink=None,  # type: ignore[arg-type]
      dlq_sink=None,  # type: ignore[arg-type]
      parent_edges=(FkEdgeSpec(
          child_cols=triple,
          ref_cols=triple,
          parent_landing="p.land.TABLE_B",
          parent_table="TABLE_B",
          mode="fanout",
      ),),
  )
  rows = {"p.land.TABLE_A": _ROOT_ROWS, "p.land.TABLE_B": _B_ROWS}
  keys = {"p.land.TABLE_A": _ROOT_ROWS, "p.land.TABLE_B": _B_PK_DISTINCT}
  (projection,) = table_projections([spec],
                                    launch_rows=_ROOT_ROWS,
                                    rows_by_landing=rows,
                                    keys_by_landing=keys,
                                    registry=_reg())
  assert projection.rows == round(_ROOT_ROWS * _MEAN)
  assert projection.parent_keys == _ROOT_ROWS
  # Without the registry the projection is today's, unchanged.
  (legacy,) = table_projections([spec],
                                launch_rows=_ROOT_ROWS,
                                rows_by_landing=rows,
                                keys_by_landing=keys)
  assert legacy.parent_keys == _B_PK_DISTINCT


# --- the end-of-run shortfall warning ------------------------------------


def _row(valid_count: int, num_rows: int) -> dict:
  from sdfb_beam.pipeline import _build_validation_run_row
  from sdfb_core.validation.thresholds import Thresholds

  return _build_validation_run_row(
      None,
      valid_count=valid_count,
      dlq_by_rule={},
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.2),
      run_id="r",
      reference_digest="d",
      num_rows=num_rows,
      reference_table="p.src.TABLE_D",
      landing_table="p.land.TABLE_D",
      engine="b1_rag",
      model_uri="gs://b/m/v/",
      repeat_share_tolerance=0.05,
  )


def test_a_table_that_lands_far_below_its_request_warns(caplog):
  """b1 landed 12.6% of TABLE_D's request and the row said PASSED; the
    status stays PASSED (a warning, never a gate change), but the run now
    says it missed."""
  with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
    row = _row(89_859_811, 713_173_103)
  assert row["status"] == "PASSED"
  line = next(ln for ln in caplog.text.splitlines()
              if "name=row_projection_shortfall" in ln)
  assert "table=p.land.TABLE_D" in line
  assert "requested=713173103" in line
  assert "valid_count=89859811" in line
  assert "ratio=0.126" in line
  assert "tolerance=0.9" in line


def test_a_table_that_lands_its_request_does_not_warn(caplog):
  with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
    _row(9_500, 10_000)
    _row(0, 0)  # nothing requested: no ratio, no warning
  assert "row_projection_shortfall" not in caplog.text
