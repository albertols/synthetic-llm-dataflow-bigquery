"""Tests for `derive_bq_schema` — TableSchema → BigQuery TableSchema dict."""

from __future__ import annotations

from sdfb_core.codegen import derive_bq_field, derive_bq_schema
from sdfb_core.contracts import TableSchema


def test_bq_schema_basic_shape(narrow_ddl_dict):
    ts = TableSchema.model_validate(narrow_ddl_dict)
    bq = derive_bq_schema(ts)
    assert "fields" in bq
    assert len(bq["fields"]) == 4

    customer_id = bq["fields"][0]
    assert customer_id == {
        "name": "customer_id",
        "type": "INT64",
        "mode": "REQUIRED",
    }


def test_bq_schema_preserves_max_length(narrow_ddl_dict):
    ts = TableSchema.model_validate(narrow_ddl_dict)
    bq = derive_bq_schema(ts)
    email = next(f for f in bq["fields"] if f["name"] == "email")
    assert email["maxLength"] == 255


def test_bq_schema_preserves_numeric_precision_scale(narrow_ddl_dict):
    ts = TableSchema.model_validate(narrow_ddl_dict)
    bq = derive_bq_schema(ts)
    ltv = next(f for f in bq["fields"] if f["name"] == "lifetime_value")
    assert ltv["precision"] == 18
    assert ltv["scale"] == 2


def test_bq_schema_recurses_into_struct(struct_ddl_dict):
    ts = TableSchema.model_validate(struct_ddl_dict)
    bq = derive_bq_schema(ts)

    line_items = next(f for f in bq["fields"] if f["name"] == "line_items")
    assert line_items["type"] == "RECORD"
    assert line_items["mode"] == "REPEATED"
    assert len(line_items["fields"]) == 2

    sku = line_items["fields"][0]
    assert sku == {"name": "sku", "type": "STRING", "mode": "REQUIRED"}


def test_derive_bq_field_skips_unset_optionals(narrow_ddl_dict):
    """Optional keys (description, precision, scale, etc.) are omitted when unset."""
    ts = TableSchema.model_validate(narrow_ddl_dict)
    customer_id = derive_bq_field(ts.columns[0])
    assert "description" not in customer_id
    assert "maxLength" not in customer_id
    assert "precision" not in customer_id
