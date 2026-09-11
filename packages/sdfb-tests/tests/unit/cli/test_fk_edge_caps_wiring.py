"""ADR 0035: the FK key-sample cap preflight sizes for a child's PK is
what the composer broadcasts — per edge, in-set parents only."""

from __future__ import annotations

from sdfb_beam.cli.run_pipeline import (
    conditional_plan_entries,
    in_set_parent_edges,
)
from sdfb_core.contracts import TableSchema
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


# --- ADR 0037: independent / conditional edges reach the composer ------

_STAR = """
model: star
tables:
  DIM_A_TABLE:
    pk: [A_ID]
  DIM_B_TABLE:
    pk: [B_ID]
  FACT_TABLE:
    pk: [A_ID, F_SEQ]
    fk:
      - cols: [A_ID]
        ref: DIM_A_TABLE
        ref_cols: [A_ID]
      - cols: [B_ID]
        ref: DIM_B_TABLE
        ref_cols: [B_ID]
"""

_DIAMOND = """
model: diamond
tables:
  TOP_TABLE:
    pk: [T]
  LEFT_TABLE:
    pk: [T, L]
    fk:
      - cols: [T]
        ref: TOP_TABLE
        ref_cols: [T]
  RIGHT_TABLE:
    pk: [T, R]
    fk:
      - cols: [T]
        ref: TOP_TABLE
        ref_cols: [T]
  BOTTOM_TABLE:
    pk: [T, L, R]
    fk:
      - cols: [T, L]
        ref: LEFT_TABLE
        ref_cols: [T, L]
      - cols: [T, R]
        ref: RIGHT_TABLE
        ref_cols: [T, R]
"""

# `(K)->P_TABLE` drives, `(K)->Q_TABLE` is a pure existence filter: it
# shares its ONLY column with the driving edge, so `rest` is empty.
_EXISTENCE = """
model: existence
tables:
  P_TABLE:
    pk: [K]
  Q_TABLE:
    pk: [K]
  CH_TABLE:
    pk: [K, S]
    fk:
      - cols: [K]
        ref: P_TABLE
        ref_cols: [K]
      - cols: [K]
        ref: Q_TABLE
        ref_cols: [K]
"""

_STAR_NAMES = {"DIM_A_TABLE", "DIM_B_TABLE", "FACT_TABLE"}
_DIAMOND_NAMES = {"TOP_TABLE", "LEFT_TABLE", "RIGHT_TABLE", "BOTTOM_TABLE"}
_EXISTENCE_NAMES = {"P_TABLE", "Q_TABLE", "CH_TABLE"}


def _reg(text: str, name: str) -> RelationshipRegistry:
    return RelationshipRegistry.from_sources(
        [(f"config/relationships/{name}.yaml", text)]
    )


def _schema(*columns: tuple[str, str]) -> TableSchema:
    """A landing schema from ``(column, mode)`` pairs."""
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.land.T"},
            "schema": [
                {"name": n, "type": "STRING", "mode": m} for n, m in columns
            ],
        }
    )


def test_star_fact_gets_a_fanout_and_a_side_input_edge():
    """Two dimensions with no ancestry: the first declared edge drives,
    the other shares no column, so it stays the ADR 0030 pool path."""
    registry = _reg(_STAR, "star")
    roles = registry.edge_roles("FACT_TABLE")
    edges = in_set_parent_edges(
        registry,
        "proj.synthetic_data.FACT_TABLE",
        in_set_names=_STAR_NAMES,
        key_sample_caps={},
        edge_roles=roles,
        table_schema=_schema(("A_ID", "REQUIRED"), ("B_ID", "REQUIRED"),
                             ("F_SEQ", "REQUIRED")),
    )
    assert [(e.child_cols, e.mode) for e in edges] == [
        (("A_ID",), "fanout"),
        (("B_ID",), "side_input"),
    ]
    # Task 5: parent_pk on EVERY in-set spec — the composer skips a
    # Distinct shuffle when the projection already holds the parent PK.
    assert [e.parent_pk for e in edges] == [("A_ID",), ("B_ID",)]
    assert all(e.overlap == () and e.nullable is False for e in edges)
    # No conditional edge: the requested keys_per_batch stands.
    assert [e.keys_per_batch for e in edges] == [100, 100]


def test_diamond_bottom_gets_a_conditional_edge_on_the_shared_column():
    registry = _reg(_DIAMOND, "diamond")
    roles = registry.edge_roles("BOTTOM_TABLE")
    edges = in_set_parent_edges(
        registry,
        "proj.synthetic_data.BOTTOM_TABLE",
        in_set_names=_DIAMOND_NAMES,
        key_sample_caps={},
        edge_roles=roles,
        table_schema=_schema(("T", "REQUIRED"), ("L", "REQUIRED"),
                             ("R", "NULLABLE")),
    )
    assert [(e.child_cols, e.mode) for e in edges] == [
        (("T", "L"), "fanout"),
        (("T", "R"), "conditional"),
    ]
    assert edges[0].overlap == ()
    assert edges[1].overlap == ("T",)
    assert edges[1].edge_id == "T,R"
    assert edges[1].candidate_cap == 64
    assert edges[1].parent_pk == ("T", "R")
    # Every `rest` column NULLABLE in the landing schema.
    assert edges[1].nullable is True


def test_a_required_rest_column_makes_the_conditional_edge_non_nullable():
    registry = _reg(_DIAMOND, "diamond")
    edges = in_set_parent_edges(
        registry,
        "proj.synthetic_data.BOTTOM_TABLE",
        in_set_names=_DIAMOND_NAMES,
        key_sample_caps={},
        edge_roles=registry.edge_roles("BOTTOM_TABLE"),
        table_schema=_schema(("T", "REQUIRED"), ("L", "REQUIRED"),
                             ("R", "REQUIRED")),
    )
    assert edges[1].nullable is False


def test_keys_per_batch_is_bounded_by_the_candidate_cap():
    """Design §7: a request never carries more than ~100k candidate
    values, so `keys_per_batch` is lowered, not the cap."""
    registry = _reg(_DIAMOND, "diamond")
    edges = in_set_parent_edges(
        registry,
        "proj.synthetic_data.BOTTOM_TABLE",
        in_set_names=_DIAMOND_NAMES,
        key_sample_caps={},
        edge_roles=registry.edge_roles("BOTTOM_TABLE"),
        keys_per_batch=5000,
        table_schema=_schema(("T", "REQUIRED"), ("L", "REQUIRED"),
                             ("R", "NULLABLE")),
    )
    assert [e.keys_per_batch for e in edges] == [1562, 1562]  # 100_000 // 64


def test_conditional_plan_entries_name_the_rest_columns():
    registry = _reg(_DIAMOND, "diamond")
    entries = conditional_plan_entries(
        registry,
        "proj.synthetic_data.BOTTOM_TABLE",
        registry.edge_roles("BOTTOM_TABLE"),
        _schema(("T", "REQUIRED"), ("L", "REQUIRED"), ("R", "NULLABLE")),
    )
    assert entries == [{"id": "T,R", "cols": ["R"], "nullable": True}]
    # The plan id and the composer's edge id are ONE string (Task 5).
    edges = in_set_parent_edges(
        registry,
        "proj.synthetic_data.BOTTOM_TABLE",
        in_set_names=_DIAMOND_NAMES,
        key_sample_caps={},
        edge_roles=registry.edge_roles("BOTTOM_TABLE"),
        table_schema=_schema(("T", "REQUIRED"), ("L", "REQUIRED"),
                             ("R", "NULLABLE")),
    )
    assert entries[0]["id"] == edges[1].edge_id


def test_a_pure_existence_filter_is_never_nullable():
    """Empty `rest`: there is nothing to write NULL into, so an
    unmatched key is dropped and counted (design §4 ruling B)."""
    registry = _reg(_EXISTENCE, "existence")
    roles = registry.edge_roles("CH_TABLE")
    schema = _schema(("K", "NULLABLE"), ("S", "NULLABLE"))
    edges = in_set_parent_edges(
        registry,
        "proj.synthetic_data.CH_TABLE",
        in_set_names=_EXISTENCE_NAMES,
        key_sample_caps={},
        edge_roles=roles,
        table_schema=schema,
    )
    assert [(e.child_cols, e.mode) for e in edges] == [
        (("K",), "fanout"),
        (("K",), "conditional"),
    ]
    assert edges[1].overlap == ("K",)
    assert edges[1].nullable is False
    assert conditional_plan_entries(
        registry, "proj.synthetic_data.CH_TABLE", roles, schema
    ) == [{"id": "K", "cols": [], "nullable": False}]
