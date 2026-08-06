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


def test_parse_engine_labels_dies_on_malformed_spec():
    with pytest.raises(SystemExit):
        fetch._parse_engine_labels(["b1_rag_missing_equals"])


# --------------------------------------------------------------------------
# main()-level guards: both new validation errors must fire BEFORE any BQ
# client/session is created (preflight_adc / _bq_client), so a malformed
# invocation never touches the network. Monkeypatching preflight_adc to
# raise proves that ordering — if the guard didn't run first, the test
# itself would fail with the injected AssertionError instead of SystemExit.
# --------------------------------------------------------------------------
def _boom(*_a, **_k):
    raise AssertionError("preflight_adc must not run before CLI validation")


def test_main_dies_on_malformed_engine_label_before_touching_bq(monkeypatch, capsys):
    monkeypatch.setattr(fetch, "preflight_adc", _boom)
    with pytest.raises(SystemExit) as exc:
        fetch.main(
            [
                "--landing-fqn", "p.d.t",
                "--project", "p",
                "--job-id", "j",
                "--engine-label", "b1_rag_missing_equals",
            ]
        )
    assert exc.value.code == 2
    assert "engine-label" in capsys.readouterr().err.lower()


def test_main_dies_when_run_id_given_without_run_id_col(monkeypatch, capsys):
    monkeypatch.setattr(fetch, "preflight_adc", _boom)
    with pytest.raises(SystemExit) as exc:
        fetch.main(
            [
                "--landing-fqn", "p.d.t",
                "--project", "p",
                "--job-id", "j",
                "--engine-label", "b1_rag=r-1",
            ]
        )
    assert exc.value.code == 2
    assert "run-id-col" in capsys.readouterr().err.lower()


def test_main_allows_run_id_when_run_id_col_given(monkeypatch):
    # The guard must not false-positive when --run-id-col IS supplied; let it
    # reach (and stop at) preflight_adc, proving the guard passed.
    monkeypatch.setattr(fetch, "preflight_adc", _boom)
    with pytest.raises(AssertionError):
        fetch.main(
            [
                "--landing-fqn", "p.d.t",
                "--project", "p",
                "--job-id", "j",
                "--run-id-col", "run_id",
                "--engine-label", "b1_rag=r-1",
            ]
        )
