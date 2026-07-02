"""Derive a BigQuery TableSchema dict from a `TableSchema`.

The output matches the format consumed by:
  - Beam's `WriteToBigQuery(schema={"fields": [...]})`
  - the BQ REST API `tables.insert` / `tables.update`
  - `bq load --schema_from_json`

REF: https://docs.cloud.google.com/bigquery/docs/schemas#creating_a_JSON_schema_file
"""

from __future__ import annotations

from sdfb_core.contracts.schema import FieldSchema, TableSchema


def derive_bq_schema(table_schema: TableSchema) -> dict:
    """Return a BQ `TableSchema` dict (`{"fields": [...]}`)."""
    return {"fields": [derive_bq_field(c) for c in table_schema.columns]}


def derive_bq_field(field: FieldSchema) -> dict:
    """Return a single BQ field schema dict, canonical key order."""
    out: dict = {
        "name": field.name,
        "type": field.bq_type,
        "mode": field.mode,
    }
    if field.description:
        out["description"] = field.description
    if field.max_length is not None:
        out["maxLength"] = field.max_length
    if field.precision is not None:
        out["precision"] = field.precision
    if field.scale is not None:
        out["scale"] = field.scale
    if field.default_value_expression is not None:
        out["defaultValueExpression"] = field.default_value_expression
    if field.is_struct:
        out["fields"] = [derive_bq_field(sub) for sub in (field.fields or [])]
    return out
