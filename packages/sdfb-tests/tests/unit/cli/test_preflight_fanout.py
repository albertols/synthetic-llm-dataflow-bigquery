"""ADR 0036 preflight: driven children are checked per key, not by the
random-draw model; edge roles are logged; the row count derives."""

from __future__ import annotations

import logging

import pytest
from sdfb_beam.cli.preflight import pk_cell_columns, preflight
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.engines.b1_rag.profile import profile_columns

_MODEL = """
model: m
tables:
  parent:
    pk: [PID]
  child:
    pk: [PID, C2, D18]
    fk:
      - cols: [PID]
        ref: parent
        ref_cols: [PID]
"""
_REG = RelationshipRegistry.from_sources([("config/relationships/m.yaml", _MODEL)])


def _schema(extra: str | None = None) -> TableSchema:
    cols = [
        {"name": "PID", "type": "STRING", "mode": "REQUIRED"},
        {"name": "C2", "type": "STRING", "mode": "REQUIRED"},
        {"name": "D18", "type": "STRING", "mode": "REQUIRED"},
        {"name": "AMT", "type": "INT64", "mode": "REQUIRED"},
    ]
    if extra:
        cols.append({"name": "SEQ", "type": extra, "mode": "REQUIRED"})
    return TableSchema.model_validate({"table_info": {"table_id": "p.d.child"}, "schema": cols})


def _rows(n: int = 480) -> list[dict]:
    return [{"PID": f"E2F3{i:020X}", "C2": f"C{i % 4}", "D18": f"K{i % 3}", "AMT": i, "SEQ": i}
            for i in range(n)]


def _fanout(max_k: int) -> dict:
    return {"driving_cols": ["PID"], "histogram": {"0": 10, str(max_k): 5},
            "cells": {"cols": ["C2", "D18"], "rows": [[f"C{i % 4}", f"K{i % 3}"] for i in range(12)],
                      "counts": [1.0] * 12},
            "exact_cells": True}


def test_pk_cell_columns_are_the_sampled_members_outside_the_edge():
    profiles = profile_columns(_schema("INT64"), _rows())
    assert pk_cell_columns(("PID", "C2", "D18"), ("PID",), profiles) == (("C2", "D18"), True)
    assert pk_cell_columns(("PID", "C2", "D18", "SEQ"), ("PID",), profiles) == (("C2", "D18"), False)


def test_fanout_within_cells_passes_and_derives_rows(caplog):
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        result = preflight(
            _schema(), (), (), _rows(), relations=_REG.relations("child"),
            num_rows=10_000_000, fk_parent_rows={"parent": 10_000_000},
            blocker_failure_ratio=0.2, fanout=_fanout(12), edge_roles=_REG.edge_roles("child"),
        )
    # mean k = (10*0 + 5*12)/15 = 4 -> 40M derived rows
    assert result.derived_rows == 40_000_000
    assert "name=fk_edge_role" in caplog.text and "role=driving" in caplog.text
    assert "name=pk_capacity_tight" not in caplog.text  # the random-draw model is off


def test_fanout_beyond_cells_stops():
    with pytest.raises(SystemExit, match=r"preflight P4.*13 children.*12 cells"):
        preflight(
            _schema(), (), (), _rows(), relations=_REG.relations("child"),
            num_rows=1_000, fk_parent_rows={"parent": 1_000},
            blocker_failure_ratio=0.2, fanout=_fanout(13),
        )


def test_an_unbounded_member_makes_the_check_inexact_and_passes():
    model = _MODEL.replace("pk: [PID, C2, D18]", "pk: [PID, C2, D18, SEQ]")
    reg = RelationshipRegistry.from_sources([("config/relationships/m.yaml", model)])
    preflight(
        _schema("INT64"), (), (), _rows(), relations=reg.relations("child"),
        num_rows=1_000, fk_parent_rows={"parent": 1_000},
        blocker_failure_ratio=0.2, fanout=_fanout(500),
    )


def test_missing_cell_table_with_exact_members_stops():
    """Fail CLOSED (ADR 0036 review): the PK's completing members are
    categorical, so the per-key draw needs a cell table — and there is
    none. Reading that as "0 cells measured, so 0 fit" would let a launch
    through on a measurement that never happened."""
    fanout = dict(_fanout(3))
    fanout["cells"] = None
    with pytest.raises(SystemExit, match=r"preflight P4.*no cell table was measured"):
        preflight(
            _schema(), (), (), _rows(), relations=_REG.relations("child"),
            num_rows=1_000, fk_parent_rows={"parent": 1_000},
            blocker_failure_ratio=0.2, fanout=fanout,
        )
