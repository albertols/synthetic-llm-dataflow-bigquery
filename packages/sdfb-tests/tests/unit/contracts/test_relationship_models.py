"""`config/relationships/*.yaml` — the single source of truth (ADR 0032).

PK / identity / FK used to live inside BigQuery table descriptions, so
changing a relationship meant a `bq update` or a `terraform apply` and
the truth was scattered across N tables. It now lives in versioned model
files the repo owns: one file per relational model, readable at a
glance, with a per-table `enabled` flag that DETACHES a subgraph without
deleting anything.
"""

from __future__ import annotations

import pytest
from sdfb_core.contracts.relationships import (
    RelationshipError,
    RelationshipRegistry,
    parse_relationship_model,
)

_RETAIL = """
model: retail
description: orders chain
tables:
  A_TABLE:
    pk: [A_COL_001, A_COL_002]
    identity: [A_COL_009]
  B_TABLE:
    pk: [B_COL_001]
    fk:
      - cols:     [B_COL_006, B_COL_007]
        ref:      A_TABLE
        ref_cols: [A_COL_001, A_COL_002]
  C_TABLE:
    pk: [C_COL_001]
    fk:
      - cols:     [C_COL_004]
        ref:      B_TABLE
        ref_cols: [B_COL_001]
"""

_ISOLATED = """
model: standalone
tables:
  Z_TABLE:
    pk: [Z_COL_001]
"""


def _registry(*texts: str) -> RelationshipRegistry:
    return RelationshipRegistry.from_sources(
        [(f"config/relationships/m{i}.yaml", t) for i, t in enumerate(texts)]
    )


class TestParsing:
    def test_reads_pk_identity_and_edges(self):
        model = parse_relationship_model(_RETAIL, source="retail.yaml")
        assert model.model == "retail"
        assert model.tables["A_TABLE"].pk == ("A_COL_001", "A_COL_002")
        assert model.tables["A_TABLE"].identity == ("A_COL_009",)
        edge = model.tables["B_TABLE"].fk[0]
        assert edge.cols == ("B_COL_006", "B_COL_007")
        assert edge.ref == "A_TABLE"
        assert edge.ref_cols == ("A_COL_001", "A_COL_002")

    def test_defaults_are_enabled_and_enforced(self):
        model = parse_relationship_model(_RETAIL, source="retail.yaml")
        assert model.tables["B_TABLE"].enabled is True
        assert model.tables["B_TABLE"].fk[0].enforced is True

    def test_source_is_kept_for_provenance(self):
        model = parse_relationship_model(_RETAIL, source="retail.yaml")
        assert model.source == "retail.yaml"

    @pytest.mark.parametrize(
        "bad,match",
        [
            ("model: m\ntables:\n  T:\n    fk:\n      - cols: [A]\n"
             "        ref: MISSING\n        ref_cols: [A]\n",
             "does not name a table in model"),
            ("model: m\ntables:\n  T:\n    fk:\n      - cols: [A, B]\n"
             "        ref: ds.other\n        ref_cols: [A]\n",
             "arity"),
            ("model: m\ntables:\n  T:\n    fk:\n      - cols: [A]\n"
             "        ref: T\n        ref_cols: [A]\n",
             "references itself"),
            ("tables:\n  T:\n    pk: [A]\n", "model"),
        ],
    )
    def test_bad_models_fail_loudly(self, bad, match):
        with pytest.raises(RelationshipError, match=match):
            parse_relationship_model(bad, source="bad.yaml")

    def test_external_parent_needs_no_entry(self):
        """A parent that already landed elsewhere is referenced
        dataset-qualified and is NOT part of this model."""
        model = parse_relationship_model(
            "model: m\ntables:\n  T:\n    pk: [A]\n    fk:\n"
            "      - cols: [A]\n        ref: other_ds.PARENT\n"
            "        ref_cols: [A]\n",
            source="m.yaml",
        )
        assert model.tables["T"].fk[0].ref == "other_ds.PARENT"


class TestRegistryLookup:
    def test_finds_a_table_by_bare_name_and_by_fqn(self):
        reg = _registry(_RETAIL)
        assert reg.relations("B_TABLE") is not None
        assert reg.relations("proj.landing.B_TABLE") is not None
        assert reg.relations("proj.landing.UNKNOWN") is None

    def test_model_name_and_source_are_reachable(self):
        reg = _registry(_RETAIL)
        assert reg.model_for("proj.landing.B_TABLE").model == "retail"

    def test_a_table_declared_twice_is_a_loud_error(self):
        with pytest.raises(RelationshipError, match="declared in 2 models"):
            _registry(_RETAIL, _RETAIL)

    def test_independent_models_coexist(self):
        reg = _registry(_RETAIL, _ISOLATED)
        assert reg.component("Z_TABLE") == ("Z_TABLE",)
        assert len(reg.component("A_TABLE")) == 3


class TestComponentAndDetaching:
    def test_related_tables_travel_together(self):
        assert set(_registry(_RETAIL).component("A_TABLE")) == {
            "A_TABLE", "B_TABLE", "C_TABLE"
        }

    def test_disabling_a_middle_table_detaches_everything_behind_it(self):
        """The worked case: C reaches A only through B. Disable B and a
        launch on A generates A alone — no deletion, one flag."""
        reg = _registry(_RETAIL.replace(
            "  B_TABLE:\n    pk: [B_COL_001]",
            "  B_TABLE:\n    enabled: false\n    pk: [B_COL_001]",
        ))
        assert reg.component("A_TABLE") == ("A_TABLE",)
        assert reg.enabled("B_TABLE") is False

    def test_a_disabled_target_still_generates_alone(self):
        """`enabled: false` detaches from the model; it never blocks an
        explicit request to generate that table."""
        reg = _registry(_RETAIL.replace(
            "  B_TABLE:\n    pk: [B_COL_001]",
            "  B_TABLE:\n    enabled: false\n    pk: [B_COL_001]",
        ))
        assert reg.component("B_TABLE") == ("B_TABLE",)
        assert reg.relations("B_TABLE").pk == ("B_COL_001",)

    def test_a_documented_edge_still_groups_but_never_enforces(self):
        reg = _registry(_RETAIL.replace(
            "        ref_cols: [B_COL_001]",
            "        ref_cols: [B_COL_001]\n        enforced: false",
        ))
        assert set(reg.component("A_TABLE")) == {
            "A_TABLE", "B_TABLE", "C_TABLE"
        }
        assert reg.enforced_edges("C_TABLE") == ()

    def test_edges_of_a_disabled_parent_are_not_enforced(self):
        reg = _registry(_RETAIL.replace(
            "  A_TABLE:\n    pk:", "  A_TABLE:\n    enabled: false\n    pk:"
        ))
        assert reg.enforced_edges("B_TABLE") == ()


class TestOrdering:
    def test_parents_generate_first(self):
        reg = _registry(_RETAIL)
        order = reg.generation_order(reg.component("C_TABLE"))
        assert order.index("A_TABLE") < order.index("B_TABLE")
        assert order.index("B_TABLE") < order.index("C_TABLE")

    def test_enforced_cycles_are_rejected_at_load(self):
        cyclic = (
            "model: m\ntables:\n"
            "  T1:\n    fk:\n      - cols: [A]\n        ref: T2\n"
            "        ref_cols: [A]\n"
            "  T2:\n    fk:\n      - cols: [A]\n        ref: T1\n"
            "        ref_cols: [A]\n"
        )
        with pytest.raises(RelationshipError, match="cycle"):
            _registry(cyclic)


class TestVisualCard:
    def test_card_shows_everything_at_a_glance(self):
        card = _registry(_RETAIL).card("A_TABLE")
        assert "retail" in card
        assert "config/relationships/m0.yaml" in card  # provenance
        assert "pk(A_COL_001,A_COL_002)" in card
        assert "identity(A_COL_009)" in card
        assert "-->" in card  # enforced edge arrow
        assert "B_COL_006,B_COL_007" in card
        assert "wave 0" in card and "wave 1" in card

    def test_card_marks_disabled_and_documented(self):
        reg = _registry(
            _RETAIL.replace(
                "  C_TABLE:\n    pk: [C_COL_001]",
                "  C_TABLE:\n    enabled: false\n    pk: [C_COL_001]",
            ).replace(
                "        ref_cols: [A_COL_001, A_COL_002]",
                "        ref_cols: [A_COL_001, A_COL_002]\n"
                "        enforced: false",
            )
        )
        card = reg.card("A_TABLE")
        assert "DISABLED" in card
        assert "detached" in card
        assert "..>" in card  # documented edge arrow
        assert "documented" in card


class TestDiagram:
    def test_mermaid_marks_enforced_documented_and_disabled(self):
        reg = _registry(
            _RETAIL.replace(
                "  C_TABLE:\n    pk: [C_COL_001]",
                "  C_TABLE:\n    enabled: false\n    pk: [C_COL_001]",
            )
        )
        src = reg.mermaid("A_TABLE")
        assert src.startswith("flowchart BT")
        assert "🗄️ A_TABLE" in src
        assert "🚫 C_TABLE (disabled)" in src
        assert "-->" in src
        assert "classDef store" in src

    def test_log_body_carries_card_then_fenced_mermaid(self):
        body = _registry(_RETAIL).log_body("A_TABLE")
        assert body.index("RELATIONSHIP MODEL") < body.index("```mermaid")
        assert body.rstrip().endswith("```")

    def test_a_table_outside_every_model_still_renders_a_card(self):
        reg = _registry(_RETAIL)
        assert "not in any model" in reg.log_body("proj.d.UNRELATED")


class TestWaves:
    def test_independent_tables_share_a_wave(self):
        reg = _registry(_RETAIL, _ISOLATED)
        waves = reg.generation_waves(("A_TABLE", "B_TABLE", "Z_TABLE"))
        assert waves[0] == ("A_TABLE", "Z_TABLE")  # both parentless
        assert waves[1] == ("B_TABLE",)

    def test_order_is_the_flattened_waves(self):
        reg = _registry(_RETAIL)
        component = reg.component("A_TABLE")
        flat = [t for w in reg.generation_waves(component) for t in w]
        assert tuple(flat) == reg.generation_order(component)

class TestEdgeRoles:
    """Design 2026-09-10 (ADR 0036): one DRIVING edge per child — the
    parent whose keys the child is generated from; every other enforced
    in-model edge must be IMPLIED (its columns are carried by the driving
    parent from that other parent), else the launch stops. Today the
    engine writes each edge's columns in turn and the last one wins."""

    _THREE = """
model: kw
tables:
  B_TABLE:
    pk: [D_COL_001]
  C_TABLE:
    pk: [D_COL_001, C_COL_002, D_COL_018]
    fk:
      - cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009]
        ref: B_TABLE
        ref_cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009]
  A_TABLE:
    pk: [D_COL_024, D_COL_025, C_COL_009, C_COL_045, A_COL_005]
    fk:
      - cols: [D_COL_024, D_COL_025, C_COL_009]
        ref: C_TABLE
        ref_cols: [D_COL_024, D_COL_025, C_COL_009]
        drives: true
      - cols: [D_COL_024, D_COL_025, C_COL_009]
        ref: B_TABLE
        ref_cols: [D_COL_024, D_COL_025, C_COL_009]
"""

    def _registry(self, text: str) -> RelationshipRegistry:
        return RelationshipRegistry.from_sources([("config/relationships/kw.yaml", text)])

    def test_single_edge_drives_by_itself(self):
        reg = self._registry(self._THREE)
        (edge,) = reg.enforced_edges("C_TABLE")
        assert reg.edge_roles("C_TABLE") == {edge: "driving"}
        assert reg.driving_edge("C_TABLE") == edge

    def test_marked_edge_drives_and_the_other_is_implied(self):
        reg = self._registry(self._THREE)
        to_c, to_b = reg.enforced_edges("A_TABLE")
        assert reg.edge_roles("A_TABLE") == {to_c: "driving", to_b: "implied"}

    def test_root_has_no_driving_edge(self):
        reg = self._registry(self._THREE)
        assert reg.driving_edge("B_TABLE") is None
        assert reg.edge_roles("B_TABLE") == {}

    def test_two_unmarked_edges_stop_with_the_edit(self):
        text = self._THREE.replace("        drives: true\n", "")
        with pytest.raises(RelationshipError, match=r"drives: true"):
            self._registry(text).edge_roles("A_TABLE")

    def test_an_edge_the_driving_parent_does_not_carry_stops(self):
        # C_TABLE's edge no longer carries the account tuple -> A_TABLE's
        # B_TABLE edge is not implied.
        text = self._THREE.replace(
            "      - cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009]\n"
            "        ref: B_TABLE\n"
            "        ref_cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009]\n",
            "      - cols: [D_COL_001]\n        ref: B_TABLE\n        ref_cols: [D_COL_001]\n",
        )
        with pytest.raises(RelationshipError, match=r"neither driving nor implied"):
            self._registry(text).edge_roles("A_TABLE")

    def test_external_edges_are_external(self):
        text = """
model: m
tables:
  T:
    pk: [ID]
    fk:
      - cols: [X]
        ref: ds.other
        ref_cols: [X]
"""
        reg = self._registry(text)
        (edge,) = reg.enforced_edges("T")
        assert reg.edge_roles("T") == {edge: "external"}
        assert reg.driving_edge("T") is None

    def test_card_names_the_roles(self):
        card = self._registry(self._THREE).card("A_TABLE")
        assert "[enforced, DRIVES]" in card
        assert "[enforced, implied via C_TABLE]" in card

    def test_diamond_ancestry_is_implied_through_the_reaching_branch(self):
        # ADR 0036 fix: _carries must track (table, cols) not just table,
        # so a table visited with one column set that fails doesn't poison
        # a later branch with different column names that succeeds.
        text = """
model: diamond
tables:
  TARGET:
    pk: [K, Z]
  SH:
    pk: [K, Z]
    fk:
      - cols: [K, Z]
        ref: TARGET
        ref_cols: [K, Z]
  P:
    pk: [K, Z]
    fk:
      - cols: [K, Z]
        ref: SH
        ref_cols: [A, B]
  Q:
    pk: [K, Z]
    fk:
      - cols: [K, Z]
        ref: SH
        ref_cols: [K, Z]
  M:
    pk: [K, Z]
    fk:
      - cols: [K, Z]
        ref: P
        ref_cols: [K, Z]
      - cols: [K, Z]
        ref: Q
        ref_cols: [K, Z]
  CHILD:
    pk: [K, Z, C]
    fk:
      - cols: [K, Z]
        ref: M
        ref_cols: [K, Z]
        drives: true
      - cols: [K, Z]
        ref: TARGET
        ref_cols: [K, Z]
"""
        reg = self._registry(text)
        to_m, to_target = reg.enforced_edges("CHILD")
        assert reg.edge_roles("CHILD") == {to_m: "driving", to_target: "implied"}
