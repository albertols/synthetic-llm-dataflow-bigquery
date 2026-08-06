"""Unit tests for scripts/e2e/e2e_fetch_samples.py (importlib idiom)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "e2e_fetch_samples.py"
_spec = importlib.util.spec_from_file_location("e2e_fetch_samples", _SCRIPT)
fetch = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fetch
_spec.loader.exec_module(fetch)


def test_sql_is_deterministic_hash_ordered():
    sql = fetch.build_sample_sql("p.d.t", rows=100, run_id_col=None, run_id=None)
    assert "FARM_FINGERPRINT(TO_JSON_STRING(t))" in sql
    assert "ORDER BY" in sql and "LIMIT 100" in sql
    assert "RAND()" not in sql


def test_sql_filters_run_id():
    sql = fetch.build_sample_sql("p.d.t", rows=5, run_id_col="run_id", run_id="r-1")
    assert "run_id = @run_id" in sql  # parameterized, never inlined


def test_write_csv_orders_columns_and_counts(tmp_path):
    out = tmp_path / "b1_rag_sample.csv"
    n = fetch.write_csv([{"b": 2, "a": 1}], ["a", "b"], out)
    assert n == 1
    assert out.read_text().splitlines()[0] == "a,b"


def test_write_csv_refuses_empty(tmp_path):
    out = tmp_path / "x.csv"
    with pytest.raises(SystemExit):
        fetch.write_csv([], ["a"], out)
    assert not out.exists()
