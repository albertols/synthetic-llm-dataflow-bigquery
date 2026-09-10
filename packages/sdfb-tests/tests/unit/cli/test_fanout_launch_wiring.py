"""ADR 0036 launcher: roles -> measurement -> derived rows -> config."""

from __future__ import annotations

from sdfb_beam.cli.run_pipeline import (
    in_set_parent_edges,
    resolve_driven_uniqueness_mode,
    resolve_fanout,
)
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relationships import RelationshipRegistry

_MODEL = """
model: kw
tables:
  B_TABLE:
    pk: [D_COL_001]
  C_TABLE:
    pk: [D_COL_001, C_COL_002]
    fk:
      - cols: [D_COL_001, D_COL_024]
        ref: B_TABLE
        ref_cols: [D_COL_001, D_COL_024]
  A_TABLE:
    pk: [D_COL_024, X]
    fk:
      - cols: [D_COL_024]
        ref: C_TABLE
        ref_cols: [D_COL_024]
        drives: true
      - cols: [D_COL_024]
        ref: B_TABLE
        ref_cols: [D_COL_024]
"""
_REG = RelationshipRegistry.from_sources([("config/relationships/kw.yaml", _MODEL)])
_SCHEMA = TableSchema.model_validate(
    {"table_info": {"table_id": "p.src.C_TABLE"},
     "schema": [{"name": n, "type": "STRING", "mode": "REQUIRED"}
                for n in ("D_COL_001", "D_COL_024", "C_COL_002")]}
)
_ROWS = [{"D_COL_001": f"K{i}", "D_COL_024": "A", "C_COL_002": "xy"[i % 2]} for i in range(40)]


class _Store:
    def __init__(self):
        self.saved = {}

    def get(self, t, cols, sha):
        return self.saved.get((t, cols, sha))

    def put(self, t, cols, sha, payload):
        self.saved[(t, cols, sha)] = payload


def _measure(**kw):
    return {"histogram": {"0": 1, "2": 1}, "cells": {"cols": ["C_COL_002"], "rows": [["x"], ["y"]],
            "counts": [1.0, 1.0]}, "parents": 2, "children": 2}


def test_resolve_fanout_measures_the_driving_edge_and_caches(monkeypatch):
    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "measure_fanout", _measure)
    store = _Store()
    payload, roles = resolve_fanout(
        _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=_ROWS, table_schema=_SCHEMA, stats_store=store, bq_client=object(),
    )
    assert payload["driving_cols"] == ["D_COL_001", "D_COL_024"]
    assert payload["exact_cells"] is True and payload["cells"]["cols"] == ["C_COL_002"]
    assert list(roles.values()) == ["driving"]
    assert store.saved  # cached under the model sha
    # second call hits the cache: measure_fanout must not run
    monkeypatch.setattr(rp, "measure_fanout", lambda **kw: (_ for _ in ()).throw(AssertionError("measured twice")))
    again, _ = resolve_fanout(
        _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=_ROWS, table_schema=_SCHEMA, stats_store=store, bq_client=object(),
    )
    assert again == payload


def test_root_has_no_fanout():
    payload, roles = resolve_fanout(
        _REG, "proj.synthetic_data.B_TABLE", "proj.src.B_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=[], table_schema=_SCHEMA, stats_store=None, bq_client=None,
    )
    assert payload is None and roles == {}


def test_edges_get_their_modes():
    roles = _REG.edge_roles("A_TABLE")
    edges = in_set_parent_edges(
        _REG, "proj.synthetic_data.A_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"}, key_sample_caps={},
        edge_roles=roles, keys_per_batch=250,
    )
    assert [(e.parent_landing.rsplit(".", 1)[-1], e.mode) for e in edges] == [
        ("C_TABLE", "fanout"), ("B_TABLE", "implied")]
    assert edges[0].keys_per_batch == 250


def test_driven_uniqueness_mode():
    assert resolve_driven_uniqueness_mode("streaming", driven=True, identity_cols=()) == "streaming"
    assert resolve_driven_uniqueness_mode("streaming", driven=True, identity_cols=("X",)) == "exact"
    assert resolve_driven_uniqueness_mode("streaming", driven=False, identity_cols=()) is None
