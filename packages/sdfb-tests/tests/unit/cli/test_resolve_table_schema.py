"""--ddl_uri precedence and the 404 fallback (TEST_1, 2026-07-25 16:38).

The 1M-row campaign's first blocker was not a worker failure at all: a
pinned DDL object that did not exist killed the template launch before any
worker started, even though the source table was right there and
`extract_table_schema` already existed.
"""

from __future__ import annotations

import json

import pytest
from sdfb_beam.cli import run_pipeline
from sdfb_core.contracts import FieldSchema, TableInfo, TableSchema


def _schema(name: str) -> TableSchema:
    return TableSchema(
        table_info=TableInfo(table_id="proj.ds.tbl"),
        columns=[FieldSchema(name=name, bq_type="STRING", mode="NULLABLE")],
    )


def test_explicit_ddl_uri_still_wins_when_it_resolves(monkeypatch):
    monkeypatch.setattr(run_pipeline, "load_ddl", lambda uri: _schema("from_pin"))
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live")
    )
    got = run_pipeline.resolve_table_schema("gs://b/ddl.json", "p.d.t")
    assert got.columns[0].name == "from_pin"


def test_no_ddl_uri_live_extracts(monkeypatch):
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live")
    )
    got = run_pipeline.resolve_table_schema("", "p.d.t")
    assert got.columns[0].name == "from_live"


def test_missing_ddl_uri_falls_back_to_live_extraction(monkeypatch):
    """A 404 on the pin must NOT kill the launch — the source table is the
    authority and live extraction already exists two lines below."""

    class _NotFoundError(Exception):
        pass

    def _boom(uri):
        raise _NotFoundError(
            "404 GET https://storage.googleapis.com/... : No such object: "
            "synthetic/ddls/ddl_metadata_CDH_dataset_KW111T_RR.json"
        )

    monkeypatch.setattr(run_pipeline, "load_ddl", _boom)
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live")
    )
    got = run_pipeline.resolve_table_schema("gs://b/missing.json", "p.d.t")
    assert got.columns[0].name == "from_live"


def test_file_not_found_also_falls_back(monkeypatch):
    monkeypatch.setattr(
        run_pipeline,
        "load_ddl",
        lambda uri: (_ for _ in ()).throw(FileNotFoundError(uri)),
    )
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live")
    )
    got = run_pipeline.resolve_table_schema("/tmp/nope.json", "p.d.t")
    assert got.columns[0].name == "from_live"


def test_corrupt_ddl_still_fails_loudly(monkeypatch):
    """Silently ignoring a MALFORMED pin is worse than the 404 — the operator
    asked for that exact schema and would instead get a different one."""

    def _bad(uri):
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(run_pipeline, "load_ddl", _bad)
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live")
    )
    with pytest.raises(json.JSONDecodeError):
        run_pipeline.resolve_table_schema("gs://b/corrupt.json", "p.d.t")


def test_schema_validation_error_still_fails_loudly(monkeypatch):
    """A pin that parses but is not a TableSchema is also an operator error."""

    def _bad(uri):
        raise ValueError("2 validation errors for TableSchema")

    monkeypatch.setattr(run_pipeline, "load_ddl", _bad)
    monkeypatch.setattr(
        run_pipeline, "extract_table_schema", lambda t: _schema("from_live")
    )
    with pytest.raises(ValueError, match="validation errors"):
        run_pipeline.resolve_table_schema("gs://b/wrong-shape.json", "p.d.t")
