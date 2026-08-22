"""Preflight P4 (PK generation capacity) + P6 (FK activation) — ADR 0028.

The 2026-08-21 run discovered its PK/pool conflict 37 minutes and 1 586
GPU-s after launch (999 488 pk.duplicate), and its declared FK was
silently inactive. Both become launcher-side stops.
"""

from __future__ import annotations

import pytest
from sdfb_beam.cli.preflight import preflight
from sdfb_core.contracts import TableSchema

_PK_CONTRACT = '{"sdfb": 1, "pk": ["ID"]}'
_FK_CONTRACT = (
    '{"sdfb": 1, "pk": ["ID"], '
    '"fk": [{"cols": ["CUST_ID"], "ref": "ds.customers", "ref_cols": ["ID"]}]}'
)
_E2F = (
    '{"llm_prompt_constraint": {"route": "llm", '
    '"pattern": "^(E2F[13][0-9A-F]{20}|2301[0-9A-F]{20})$"}}'
)
_TINY = '{"llm_prompt_constraint": {"route": "llm", "pattern": "^[0-9]{3}$"}}'
_PROSE = '{"llm_prompt_constraint": {"route": "llm", "format": "opaque key"}}'


def _schema(table_desc: str, id_desc: str = "") -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t", "description": table_desc},
            "schema": [
                {
                    "name": "ID",
                    "type": "STRING",
                    "mode": "REQUIRED",
                    "description": id_desc,
                },
                {"name": "CUST_ID", "type": "STRING", "mode": "REQUIRED"},
            ],
        }
    )


def _rows(n: int = 10) -> list[dict]:
    return [{"ID": f"E2F3{i:020X}", "CUST_ID": "c1"} for i in range(n)]


class TestP4PkCapacity:
    def test_constrained_pk_without_pattern_stops_at_scale(self):
        with pytest.raises(SystemExit, match="preflight P4"):
            preflight(
                _schema(_PK_CONTRACT, _PROSE), (), (), _rows(),
                num_rows=1_000_000,
            )

    def test_constrained_pk_without_pattern_ok_below_pool_cap(self):
        preflight(
            _schema(_PK_CONTRACT, _PROSE), (), (), _rows(), num_rows=500
        )

    def test_pattern_pk_with_ample_capacity_passes(self):
        preflight(
            _schema(_PK_CONTRACT, _E2F), (), (), _rows(), num_rows=1_000_000
        )

    def test_pattern_pk_with_small_capacity_stops(self):
        with pytest.raises(SystemExit, match="preflight P4"):
            preflight(
                _schema(_PK_CONTRACT, _TINY), (), (), _rows(),
                num_rows=1_000_000,
            )

    def test_unconstrained_pk_is_untouched(self):
        preflight(_schema(_PK_CONTRACT), (), (), _rows(), num_rows=1_000_000)

    def test_num_rows_zero_disables_the_check(self):
        preflight(_schema(_PK_CONTRACT, _PROSE), (), (), _rows())


class TestFkActivationIsDerived:
    """ADR 0029 rev B: fk_parent_landing derives from --landing_table,
    so preflight no longer refuses a declared FK — activation is checked
    where pools LOAD (loud empty-parent stop in run_pipeline)."""

    def test_declared_fk_passes_preflight_without_any_flag(self):
        preflight(_schema(_FK_CONTRACT), (), (), _rows())

    def test_empty_parent_pool_stops_loudly(self):
        from sdfb_beam.cli.run_pipeline import assert_fk_pools_nonempty
        from sdfb_core.contracts.relational import parse_relational_contract
        contract = parse_relational_contract(_FK_CONTRACT)
        with pytest.raises(SystemExit, match=r"not landed"):
            assert_fk_pools_nonempty(contract.fk, {}, "p.landing")

    def test_populated_parent_pool_passes(self):
        from sdfb_beam.cli.run_pipeline import assert_fk_pools_nonempty
        from sdfb_core.contracts.relational import parse_relational_contract
        contract = parse_relational_contract(_FK_CONTRACT)
        assert_fk_pools_nonempty(
            contract.fk, {"CUST_ID": ("K1", "K2")}, "p.landing"
        )
