"""Informational FK edges are display-only (ADR 0029).

The 6-table example's PARTY_KEY edge names a column absent from every
DDL — declared with ``informational: true`` it must draw in diagrams
without tripping P2 (unknown column), P6 (activation), or FK pool loads.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sdfb_beam.cli.preflight import preflight
from sdfb_beam.io.fk_pools import load_fk_pools
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relational import ForeignKey

_INFO_ONLY = (
    '{"sdfb": 1, "pk": ["ID"], '
    '"fk": [{"cols": ["PARTY_KEY_UNMAPPED"], "ref": "ds.a_table", '
    '"ref_cols": ["PARTY_KEY_UNMAPPED"], "informational": true}]}'
)
_MIXED = (
    '{"sdfb": 1, "pk": ["ID"], "fk": ['
    '{"cols": ["PARTY_KEY_UNMAPPED"], "ref": "ds.a_table", '
    '"ref_cols": ["PARTY_KEY_UNMAPPED"], "informational": true}, '
    '{"cols": ["CUST_ID"], "ref": "ds.customers", "ref_cols": ["ID"]}]}'
)


def _schema(table_desc: str) -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t", "description": table_desc},
            "schema": [
                {"name": "ID", "type": "STRING", "mode": "REQUIRED"},
                {"name": "CUST_ID", "type": "STRING", "mode": "REQUIRED"},
            ],
        }
    )


def _rows(n: int = 5) -> list[dict]:
    return [{"ID": f"id{i}", "CUST_ID": "c"} for i in range(n)]


class TestPreflight:
    def test_informational_edge_with_unknown_cols_passes_p2(self):
        preflight(_schema(_INFO_ONLY), (), (), _rows())

    def test_informational_only_fk_needs_no_parent_landing(self):
        preflight(
            _schema(_INFO_ONLY), (), (), _rows(), fk_parent_landing=""
        )

    def test_enforced_edge_still_requires_parent_landing(self):
        with pytest.raises(SystemExit, match="preflight P6"):
            preflight(
                _schema(_MIXED), (), (), _rows(), fk_parent_landing=""
            )


class TestFkPoolLoader:
    def test_informational_edges_load_no_pool(self):
        client = MagicMock()
        fks = (
            ForeignKey(
                cols=("PARTY_KEY_UNMAPPED",), ref="ds.a_table",
                ref_cols=("PARTY_KEY_UNMAPPED",), informational=True,
            ),
        )
        pools = load_fk_pools(fks, "p.landing", client=client)
        assert pools == {}
        client.query.assert_not_called()
