"""Relational contract flows extractor → _ddl.json → TableSchema (Task 12)."""

from unittest.mock import MagicMock

from sdfb_beam.ddl.extractor import extract_ddl_metadata
from sdfb_core.contracts import TableSchema

_CONTRACT_DESC = (
    "Ops table. "
    '{"sdfb": 1, "pk": ["ID", "SEQ"], '
    '"fk": [{"cols": ["CUST_ID"], "ref": "ds.customers", "ref_cols": ["ID"]}], '
    '"identity": ["ID"]}'
)


def _make_field(name: str, ftype: str) -> MagicMock:
    f = MagicMock()
    f.name = name
    f.field_type = ftype
    f.mode = "REQUIRED"
    f.description = ""
    f.fields = None
    f.max_length = None
    f.precision = None
    f.scale = None
    return f


def _make_table(description: str) -> MagicMock:
    t = MagicMock()
    t.schema = [_make_field(n, "STRING") for n in ("ID", "SEQ", "CUST_ID")]
    t.created = None
    t.modified = None
    t.expires = None
    t.location = "EU"
    t.description = description
    t.labels = {}
    t.table_type = "TABLE"
    t.encryption_configuration = None
    t.time_partitioning = None
    t.range_partitioning = None
    t.clustering_fields = None
    t.num_rows = 100
    t.require_partition_filter = False
    t._properties = {}
    t.table_constraints = None
    return t


def _extract(description: str) -> dict:
    client = MagicMock()
    client.get_table.return_value = _make_table(description)
    client.query.return_value.result.return_value = iter([])
    return extract_ddl_metadata(project="p", dataset="d", table="t", client=client)


def test_contract_pk_wins_over_absent_constraints():
    result = _extract(_CONTRACT_DESC)
    assert result["primary_keys"] == ["ID", "SEQ"]


def test_table_info_carries_relational_mirror():
    result = _extract(_CONTRACT_DESC)
    rel = result["table_info"]["relational"]
    assert rel["pk"] == ["ID", "SEQ"]
    assert rel["fk"][0]["ref"] == "ds.customers"


def test_no_contract_keeps_legacy_paths():
    result = _extract("Test table.\nPRIMARY KEY: ID\nOther notes.")
    assert result["primary_keys"] == ["ID"]
    assert result["table_info"]["relational"] is None


def test_table_schema_parses_contract_from_description():
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t", "description": _CONTRACT_DESC},
            "schema": [{"name": "ID", "type": "STRING", "mode": "REQUIRED"}],
        }
    )
    contract = schema.relational_contract()
    assert contract is not None
    assert contract.identity == ("ID",)
    assert contract.fk[0].cols == ("CUST_ID",)


def test_table_schema_without_contract_is_none():
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t", "description": "prose"},
            "schema": [{"name": "ID", "type": "STRING", "mode": "REQUIRED"}],
        }
    )
    assert schema.relational_contract() is None
