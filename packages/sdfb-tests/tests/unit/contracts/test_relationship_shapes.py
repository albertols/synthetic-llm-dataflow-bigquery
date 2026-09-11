"""Every relational SHAPE a model file can declare, through the registry
(ADR 0032/0036/0037): what the launcher resolves — waves, driving/
implied/independent/conditional roles — with no `drives:` marker and no
model edit beyond `enabled`.

The 2026-09-11 5-table expansion showed the launch stopping on shapes
the model can legitimately declare. Each case here is one shape,
including a child with two parents that are not on one ancestry line —
the star-schema fact table and the true diamond — resolved by the
`independent` and `conditional` roles (ADR 0037).
"""

from __future__ import annotations

import pytest
from sdfb_core.contracts.relationships import RelationshipError, RelationshipRegistry


def _registry(text: str) -> RelationshipRegistry:
    return RelationshipRegistry.from_sources([("config/relationships/shape.yaml", text)])


def _roles(reg: RelationshipRegistry, table: str) -> dict[str, str]:
    return {
        f"({','.join(e.cols)})->{e.ref}": role
        for e, role in reg.edge_roles(table).items()
    }


def _tables(reg: RelationshipRegistry) -> tuple[str, ...]:
    return tuple(t for m in reg.models for t in m.tables)


class TestShapesTheRegistryResolves:
    def test_star_hub_one_parent_many_children(self):
        reg = _registry("""
model: s
tables:
  hub: {pk: [H]}
  c1: {pk: [H, X], fk: [{cols: [H], ref: hub, ref_cols: [H]}]}
  c2: {pk: [H, Y], fk: [{cols: [H], ref: hub, ref_cols: [H]}]}
  c3: {pk: [H], fk: [{cols: [H], ref: hub, ref_cols: [H]}]}
""")
        assert reg.generation_waves(_tables(reg)) == (("hub",), ("c1", "c2", "c3"))
        for child in ("c1", "c2", "c3"):
            assert _roles(reg, child) == {"(H)->hub": "driving"}

    def test_chain_and_tree(self):
        reg = _registry("""
model: t
tables:
  root: {pk: [R]}
  mid: {pk: [R, M], fk: [{cols: [R], ref: root, ref_cols: [R]}]}
  leaf1: {pk: [R, M, L], fk: [{cols: [R, M], ref: mid, ref_cols: [R, M]}]}
  leaf2: {pk: [R, M, Q], fk: [{cols: [R, M], ref: mid, ref_cols: [R, M]}]}
""")
        assert reg.generation_waves(_tables(reg)) == (("root",), ("mid",), ("leaf1", "leaf2"))
        assert _roles(reg, "leaf1") == {"(R,M)->mid": "driving"}

    def test_forest_of_components_with_a_one_to_one_chain(self):
        reg = _registry("""
model: c
tables:
  a: {pk: [A]}
  b: {pk: [A, B], fk: [{cols: [A], ref: a, ref_cols: [A]}]}
  e: {pk: [E]}
  f: {pk: [E], fk: [{cols: [E], ref: e, ref_cols: [E]}]}
""")
        assert reg.component("a") == ("a", "b")
        assert reg.component("e") == ("e", "f")
        assert reg.generation_waves(_tables(reg)) == (("a", "e"), ("b", "f"))
        # f's PK IS its driving edge: a true 1:1 child (E_TABLE, 2026-09-11).
        assert _roles(reg, "f") == {"(E)->e": "driving"}

    def test_grandparent_edge_next_to_the_parent_edge_is_implied(self):
        reg = _registry("""
model: g
tables:
  gp: {pk: [G]}
  p: {pk: [G, P], fk: [{cols: [G], ref: gp, ref_cols: [G]}]}
  c:
    pk: [G, P, C]
    fk:
      - {cols: [G, P], ref: p, ref_cols: [G, P]}
      - {cols: [G], ref: gp, ref_cols: [G]}
""")
        assert _roles(reg, "c") == {"(G,P)->p": "driving", "(G)->gp": "implied"}

    def test_disabling_the_hub_splits_a_star_into_roots(self):
        reg = _registry("""
model: s
tables:
  hub: {pk: [H], enabled: false}
  c1: {pk: [H, X], fk: [{cols: [H], ref: hub, ref_cols: [H]}]}
  c2: {pk: [H, Y], fk: [{cols: [H], ref: hub, ref_cols: [H]}]}
""")
        assert reg.enforced_edges("c1") == ()
        assert reg.generation_waves(("c1", "c2")) == (("c1", "c2"),)

    def test_star_fact_dimensions_are_independent(self):
        reg = _registry(_STAR_FACT)
        _to_a, to_b = reg.enforced_edges("fact")
        assert reg.edge_overlap("fact", to_b) == ()
        assert reg.edge_rest("fact", to_b) == ("B_ID",)
        assert reg.driving_choice("fact") == "first_declared"
        assert reg.driving_choice("dim_a") is None

    def test_diamond_branch_overlap_and_rest(self):
        reg = _registry(_DIAMOND)
        _to_left, to_right = reg.enforced_edges("bottom")
        assert reg.edge_overlap("bottom", to_right) == ("T",)
        assert reg.edge_rest("bottom", to_right) == ("R",)
        assert "conditional on (T)" in reg.card("bottom")

    def test_two_marked_edges_still_stop(self):
        text = _STAR_FACT.replace("ref: dim_a, ref_cols: [A_ID]}", "ref: dim_a, ref_cols: [A_ID], drives: true}") \
                         .replace("ref: dim_b, ref_cols: [B_ID]}", "ref: dim_b, ref_cols: [B_ID], drives: true}")
        with pytest.raises(RelationshipError, match="2 marked"):
            _registry(text).edge_roles("fact")


_STAR_FACT = """
model: s
tables:
  dim_a: {pk: [A_ID]}
  dim_b: {pk: [B_ID]}
  fact:
    pk: [A_ID, B_ID, SEQ]
    fk:
      - {cols: [A_ID], ref: dim_a, ref_cols: [A_ID]}
      - {cols: [B_ID], ref: dim_b, ref_cols: [B_ID]}
"""

_DIAMOND = """
model: d
tables:
  top: {pk: [T]}
  left: {pk: [T, L], fk: [{cols: [T], ref: top, ref_cols: [T]}]}
  right: {pk: [T, R], fk: [{cols: [T], ref: top, ref_cols: [T]}]}
  bottom:
    pk: [T, L, R, S]
    fk:
      - {cols: [T, L], ref: left, ref_cols: [T, L]}
      - {cols: [T, R], ref: right, ref_cols: [T, R]}
"""


class TestShapesStillPending:
    """A child with two in-set parents that are not on one ancestry line —
    the star-schema fact table and the true diamond. Resolved by the
    `independent` and `conditional` roles (ADR 0037)."""

    def test_star_schema_fact_with_two_independent_dimensions(self):
        reg = _registry(_STAR_FACT)
        roles = _roles(reg, "fact")
        assert roles["(A_ID)->dim_a"] == "driving"
        assert roles["(B_ID)->dim_b"] == "independent"

    def test_true_diamond_rejoining_at_the_bottom(self):
        reg = _registry(_DIAMOND)
        roles = _roles(reg, "bottom")
        assert roles["(T,L)->left"] == "driving"
        assert roles["(T,R)->right"] == "conditional"
