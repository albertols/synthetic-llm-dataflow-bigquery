"""Pytest config + shared fixtures."""

from __future__ import annotations

import pytest

from sdfb_tests.fixtures import load_ddl, load_reference


# ---------------------------------------------------------------------------
# File-backed fixtures (canonical reference data).
# ---------------------------------------------------------------------------


@pytest.fixture
def customers_schema():
    """Narrow customers table loaded from `fixtures/ddl/customers_ddl.json`."""
    return load_ddl("customers")


@pytest.fixture
def customers_reference():
    """10 customer reference rows from `fixtures/reference/customers_reference.json`."""
    return load_reference("customers")


@pytest.fixture
def orders_schema():
    """Wide orders table (STRUCT + REPEATED) from `fixtures/ddl/orders_ddl.json`."""
    return load_ddl("orders")


@pytest.fixture
def orders_reference():
    """5 order reference rows with nested + repeated fields."""
    return load_reference("orders")


# ---------------------------------------------------------------------------
# Inline-dict fixtures (kept for isolated unit tests — small, fast, no I/O).
# ---------------------------------------------------------------------------


@pytest.fixture
def narrow_ddl_dict() -> dict:
    """A small valid `_ddl.json` with 4 columns and a single-column PK.

    Uses canonical BQ JSON-schema keys (`"type"`, not `"field_type"`).
    """
    return {
        "table_info": {
            "table_id": "demo_project.demo_dataset.customers",
            "description": "Test fixture table.",
        },
        "schema": [
            {"name": "customer_id", "type": "INT64", "mode": "REQUIRED"},
            {
                "name": "email",
                "type": "STRING",
                "mode": "REQUIRED",
                "max_length": 255,
            },
            {"name": "signup_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
            {
                "name": "lifetime_value",
                "type": "NUMERIC",
                "mode": "NULLABLE",
                "precision": 18,
                "scale": 2,
            },
        ],
        "primary_keys": ["customer_id"],
    }


@pytest.fixture
def struct_ddl_dict() -> dict:
    """A `_ddl` with STRUCT + REPEATED columns for recursion tests."""
    return {
        "table_info": {"table_id": "demo.orders"},
        "schema": [
            {"name": "order_id", "type": "STRING", "mode": "REQUIRED"},
            {
                "name": "line_items",
                "type": "RECORD",
                "mode": "REPEATED",
                "fields": [
                    {"name": "sku", "type": "STRING", "mode": "REQUIRED"},
                    {"name": "qty", "type": "INT64", "mode": "REQUIRED"},
                ],
            },
        ],
        "primary_keys": ["order_id"],
    }


@pytest.fixture
def legacy_field_type_ddl_dict() -> dict:
    """A `_ddl` using the `bigquery_ddl_metadata.py` legacy key `"field_type"`."""
    return {
        "table_info": {"table_id": "demo.legacy"},
        "schema": [
            {"name": "id", "field_type": "INT64", "mode": "REQUIRED"},
            {"name": "label", "field_type": "STRING", "mode": "NULLABLE"},
        ],
        "primary_keys": ["id"],
    }
