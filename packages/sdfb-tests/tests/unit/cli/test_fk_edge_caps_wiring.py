"""ADR 0035: the FK key-sample cap preflight sizes for a child's PK is
what the composer broadcasts — per edge, in-set parents only."""

from __future__ import annotations

from sdfb_beam.cli.run_pipeline import in_set_parent_edges
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.engines.pk_capacity import FK_KEY_SAMPLE_FLOOR

_MODEL = """
model: m
tables:
  B_TABLE:
    pk: [D_COL_001]
  C_TABLE:
    pk: [D_COL_001, C_COL_002, D_COL_018]
    fk:
      - cols: [D_COL_001]
        ref: B_TABLE
        ref_cols: [D_COL_001]
      - cols: [X]
        ref: ds.external
        ref_cols: [X]
"""


def _registry() -> RelationshipRegistry:
    return RelationshipRegistry.from_sources(
        [("config/relationships/m.yaml", _MODEL)]
    )


def test_preflight_cap_rides_on_the_in_set_edge():
    edges = in_set_parent_edges(
        _registry(),
        "proj.synthetic_data.C_TABLE",
        in_set_names={"B_TABLE", "C_TABLE"},
        key_sample_caps={("D_COL_001",): 833_334},
    )
    assert [e.child_cols for e in edges] == [("D_COL_001",)]  # external edge excluded
    assert edges[0].parent_landing == "proj.synthetic_data.B_TABLE"
    assert edges[0].key_sample_cap == 833_334


def test_edges_without_a_sized_cap_keep_the_floor():
    edges = in_set_parent_edges(
        _registry(),
        "proj.synthetic_data.C_TABLE",
        in_set_names={"B_TABLE", "C_TABLE"},
        key_sample_caps={},
    )
    assert edges[0].key_sample_cap == FK_KEY_SAMPLE_FLOOR
