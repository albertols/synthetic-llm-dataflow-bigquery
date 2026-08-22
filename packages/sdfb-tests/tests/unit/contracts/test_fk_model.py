"""FK model graph + mermaid renderer (ADR 0029).

Fixture is the 6-table example (`docs/assets/fk_relationship_example.tf`):
B→A (composite), C→A (informational JOIN_KEY, absent from any DDL),
D→C, E→C, F→E.
"""

from __future__ import annotations

import pytest
from sdfb_core.contracts.fk_model import (
    FkModelError,
    build_fk_model,
    fk_model_mermaid,
    model_sha12,
)
from sdfb_core.contracts.relational import ForeignKey, RelationalContract

_P = "proj.src"


def _c(pk=("K",), fk=()):
    return RelationalContract(sdfb=1, pk=tuple(pk), fk=tuple(fk))


def _six_tables():
    tables = [f"{_P}.{t}_TABLE" for t in "ABCDEF"]
    contracts = {
        f"{_P}.A_TABLE": _c(pk=("A_COL_001", "A_COL_002", "A_COL_003")),
        f"{_P}.B_TABLE": _c(
            pk=("B_COL_001",),
            fk=[ForeignKey(
                cols=("B_COL_006", "B_COL_007", "B_COL_009"),
                ref="synthetic_data.A_TABLE",
                ref_cols=("A_COL_001", "A_COL_002", "A_COL_003"),
            )],
        ),
        f"{_P}.C_TABLE": _c(
            pk=("C_COL_001",),
            fk=[ForeignKey(
                cols=("JOIN_KEY_UNMAPPED",),
                ref="synthetic_data.A_TABLE",
                ref_cols=("JOIN_KEY_UNMAPPED",),
                informational=True,
            )],
        ),
        f"{_P}.D_TABLE": _c(
            pk=("D_COL_001",),
            fk=[ForeignKey(cols=("D_COL_001",), ref="synthetic_data.C_TABLE",
                           ref_cols=("C_COL_001",))],
        ),
        f"{_P}.E_TABLE": _c(
            pk=("E_COL_001",),
            fk=[ForeignKey(cols=("E_COL_001",), ref="synthetic_data.C_TABLE",
                           ref_cols=("C_COL_001",))],
        ),
        f"{_P}.F_TABLE": _c(
            pk=("F_COL_001",),
            fk=[ForeignKey(cols=("F_COL_001",), ref="synthetic_data.E_TABLE",
                           ref_cols=("E_COL_001",))],
        ),
    }
    return tables, contracts


class TestForeignKeyInformational:
    def test_default_is_enforced(self):
        fk = ForeignKey(cols=("X",), ref="d.t", ref_cols=("Y",))
        assert fk.informational is False

    def test_informational_accepted(self):
        fk = ForeignKey(cols=("X",), ref="d.t", ref_cols=("Y",),
                        informational=True)
        assert fk.informational is True


class TestBuildFkModel:
    def test_six_table_example_shape(self):
        tables, contracts = _six_tables()
        m = build_fk_model(tables, contracts)
        assert m.tables == tuple(tables)
        assert len(m.edges) == 5
        assert sum(1 for _, fk in m.edges if fk.informational) == 1

    def test_levels_ignore_informational_edges(self):
        tables, contracts = _six_tables()
        m = build_fk_model(tables, contracts)
        # C's only edge is informational — it is a root alongside A.
        assert m.levels[0] == (f"{_P}.A_TABLE", f"{_P}.C_TABLE")
        assert m.levels[1] == (
            f"{_P}.B_TABLE", f"{_P}.D_TABLE", f"{_P}.E_TABLE"
        )
        assert m.levels[2] == (f"{_P}.F_TABLE",)

    def test_external_parent_is_recorded_not_leveled(self):
        tables = [f"{_P}.orders"]
        contracts = {
            f"{_P}.orders": _c(fk=[ForeignKey(
                cols=("CUST",), ref="src.customers", ref_cols=("ID",)
            )]),
        }
        m = build_fk_model(tables, contracts)
        assert m.external == ("src.customers",)
        assert m.levels == ((f"{_P}.orders",),)

    def test_cycle_raises(self):
        tables = [f"{_P}.x", f"{_P}.y"]
        contracts = {
            f"{_P}.x": _c(fk=[ForeignKey(cols=("A",), ref="src.y",
                                         ref_cols=("A",))]),
            f"{_P}.y": _c(fk=[ForeignKey(cols=("B",), ref="src.x",
                                         ref_cols=("B",))]),
        }
        with pytest.raises(FkModelError, match="cycle"):
            build_fk_model(tables, contracts)

    def test_duplicate_tables_raise(self):
        with pytest.raises(FkModelError, match="duplicate"):
            build_fk_model([f"{_P}.a", f"{_P}.a"], {})


class TestMermaid:
    def test_renders_house_style_with_edge_labels(self):
        tables, contracts = _six_tables()
        text = fk_model_mermaid(build_fk_model(tables, contracts))
        assert text.startswith("flowchart")
        assert "classDef store" in text
        # composite edge label: child cols -> ref cols
        assert "B_COL_006" in text and "A_COL_001" in text
        # informational edge is dashed
        assert "-.->" in text

    def test_aliases_rename_nodes(self):
        tables, contracts = _six_tables()
        aliases = {f"{_P}.A_TABLE": "A_TABLE"}
        text = fk_model_mermaid(build_fk_model(tables, contracts), aliases)
        assert "A_TABLE" in text
        assert f"{_P}.A_TABLE" not in text


class TestModelSha:
    def test_stable_and_sensitive(self):
        tables, contracts = _six_tables()
        a = model_sha12(build_fk_model(tables, contracts))
        b = model_sha12(build_fk_model(list(tables), dict(contracts)))
        assert a == b and len(a) == 12
        contracts2 = dict(contracts)
        contracts2[f"{_P}.F_TABLE"] = _c(pk=("F_COL_001",))  # drop F→E
        assert model_sha12(build_fk_model(tables, contracts2)) != a


class TestConnectedComponent:
    """Scenario-2 grouping (ADR 0029 rev B): 'related' includes
    informational edges — the 6-table model connects C to A only via
    JOIN_KEY — while ORDERING still ignores them."""

    def test_component_spans_informational_edges(self):
        tables, contracts = _six_tables()
        from sdfb_core.contracts.fk_model import connected_component
        got = connected_component(f"{_P}.A_TABLE", tables, contracts)
        assert got == tuple(tables)  # all six, via C's dashed edge

    def test_component_from_a_leaf(self):
        tables, contracts = _six_tables()
        from sdfb_core.contracts.fk_model import connected_component
        got = connected_component(f"{_P}.F_TABLE", tables, contracts)
        assert got == tuple(tables)

    def test_unrelated_table_is_its_own_component(self):
        tables, contracts = _six_tables()
        tables = [*tables, f"{_P}.LONER"]
        contracts[f"{_P}.LONER"] = None
        from sdfb_core.contracts.fk_model import connected_component
        got = connected_component(f"{_P}.LONER", tables, contracts)
        assert got == (f"{_P}.LONER",)


class TestAsciiRendering:
    """Human-glanceable log rendering (2026-08-22 operator ask): waves,
    `-->` enforced edges, `..>` informational, field lists on both ends
    — readable straight in Cloud Logging, no renderer needed."""

    def test_waves_and_enforced_arrows(self):
        tables, contracts = _six_tables()
        from sdfb_core.contracts.fk_model import fk_model_ascii
        text = fk_model_ascii(build_fk_model(tables, contracts))
        assert "wave 0" in text and "wave 1" in text and "wave 2" in text
        # child(cols) --> parent(ref_cols), fields visible on both ends
        assert (
            "B_TABLE (B_COL_006,B_COL_007,B_COL_009) "
            "--> A_TABLE (A_COL_001,A_COL_002,A_COL_003)"
        ) in text.replace(f"{_P}.", "")
        assert "D_TABLE (D_COL_001) --> C_TABLE (C_COL_001)" in (
            text.replace(f"{_P}.", "")
        )

    def test_informational_edges_are_dotted_and_tagged(self):
        tables, contracts = _six_tables()
        from sdfb_core.contracts.fk_model import fk_model_ascii
        text = fk_model_ascii(build_fk_model(tables, contracts))
        assert "..>" in text
        assert "[informational" in text
        assert "-->" not in text.split("[informational")[0].rsplit(
            "JOIN_KEY_UNMAPPED", 1
        )[-1]

    def test_header_counts_and_aliases(self):
        tables, contracts = _six_tables()
        from sdfb_core.contracts.fk_model import fk_model_ascii
        aliases = {t: t.rsplit(".", 1)[-1] for t in tables}
        text = fk_model_ascii(build_fk_model(tables, contracts), aliases)
        assert "6 tables" in text
        assert "4 enforced" in text and "1 informational" in text
        assert f"{_P}." not in text  # aliases applied

    def test_external_parent_is_labelled(self):
        tables = [f"{_P}.orders"]
        contracts = {
            f"{_P}.orders": _c(fk=[ForeignKey(
                cols=("CUST",), ref="src.customers", ref_cols=("ID",)
            )]),
        }
        from sdfb_core.contracts.fk_model import fk_model_ascii
        text = fk_model_ascii(build_fk_model(tables, contracts))
        assert "src.customers" in text
        assert "[external]" in text
