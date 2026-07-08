"""Smoke tests for `scripts/e2e_validation_analysis.py` (vendored e2e analysis).

Offline-only: no CSV/schema paths ever leave the tmp_path fixture. Loaded via
importlib the same way `test_deployment_prerequisites.py` loads
`scripts/deployment_prerequisites.py` (see that file's docstring/idiom).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pandas")

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e_validation_analysis.py"
_spec = importlib.util.spec_from_file_location("e2e_validation_analysis", _SCRIPT)
analysis_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = analysis_module
_spec.loader.exec_module(analysis_module)


def test_analysis_end_to_end(tmp_path):
    schema = [{"name": "id", "type": "STRING"}, {"name": "amt", "type": "INTEGER"}]
    (tmp_path / "s.json").write_text(json.dumps(schema))
    csv = "id,amt\r\n" + "\r\n".join(f"row-{i % 4},{i}" for i in range(16))
    (tmp_path / "e.csv").write_text(csv)
    out = tmp_path / "m.json"
    rc = analysis_module.main_with_args([
        "--csv", f"eng={tmp_path / 'e.csv'}", "--schema", str(tmp_path / 's.json'),
        "--identity-cols", "id", "--out", str(out),
    ])
    assert rc == 0
    m = json.loads(out.read_text())
    eng = m["engines"]["eng"]
    assert eng["row_count"] == 16
    assert eng["identity_columns"]["id"]["distinct"] == 4
    assert eng["columns"]["amt"]["int_type_conformance"] == 1.0


def test_run_lengths_flag_batch_size(tmp_path):
    # 32 rows of an "id" column repeating each value 8 times consecutively —
    # the signature of batch-level replay when --batch-size 8 matches.
    schema = [{"name": "id", "type": "STRING"}]
    (tmp_path / "s.json").write_text(json.dumps(schema))
    rows = []
    for block in range(4):
        rows.extend([f"row-{block}"] * 8)
    csv = "id\r\n" + "\r\n".join(rows)
    (tmp_path / "e.csv").write_text(csv)
    out = tmp_path / "m.json"
    rc = analysis_module.main_with_args([
        "--csv", f"eng={tmp_path / 'e.csv'}", "--schema", str(tmp_path / 's.json'),
        "--identity-cols", "id", "--batch-size", "8", "--out", str(out),
    ])
    assert rc == 0
    m = json.loads(out.read_text())
    rl = m["engines"]["eng"]["identity_columns"]["id"]["run_lengths"]
    assert rl["max_run"] == 8
    assert rl["equals_batch_size"] is True


def test_run_lengths_no_batch_size_flag_false(tmp_path):
    # Same repeating-run shape, but --batch-size is left at its default (0 =
    # unknown), so equals_batch_size must never fire.
    schema = [{"name": "id", "type": "STRING"}]
    (tmp_path / "s.json").write_text(json.dumps(schema))
    rows = []
    for block in range(4):
        rows.extend([f"row-{block}"] * 8)
    csv = "id\r\n" + "\r\n".join(rows)
    (tmp_path / "e.csv").write_text(csv)
    out = tmp_path / "m.json"
    rc = analysis_module.main_with_args([
        "--csv", f"eng={tmp_path / 'e.csv'}", "--schema", str(tmp_path / 's.json'),
        "--identity-cols", "id", "--out", str(out),
    ])
    assert rc == 0
    m = json.loads(out.read_text())
    rl = m["engines"]["eng"]["identity_columns"]["id"]["run_lengths"]
    assert rl["equals_batch_size"] is False


def test_entropy_reported(tmp_path):
    schema = [
        {"name": "const_col", "type": "STRING"},
        {"name": "diverse_col", "type": "STRING"},
    ]
    (tmp_path / "s.json").write_text(json.dumps(schema))
    rows = [f"same,val-{i}" for i in range(16)]
    csv = "const_col,diverse_col\r\n" + "\r\n".join(rows)
    (tmp_path / "e.csv").write_text(csv)
    out = tmp_path / "m.json"
    rc = analysis_module.main_with_args([
        "--csv", f"eng={tmp_path / 'e.csv'}", "--schema", str(tmp_path / 's.json'),
        "--out", str(out),
    ])
    assert rc == 0
    m = json.loads(out.read_text())
    cols = m["engines"]["eng"]["columns"]
    assert cols["const_col"]["normalized_entropy"] == 0.0
    # Uniform all-distinct column: h = log2(n), normalized = exactly 1.0.
    assert cols["diverse_col"]["normalized_entropy"] == 1.0


def test_entropy_empty_column_is_zero(tmp_path):
    schema = [{"name": "empty_col", "type": "STRING"}]
    (tmp_path / "s.json").write_text(json.dumps(schema))
    csv = "empty_col\r\n" + "\r\n".join([""] * 8)
    (tmp_path / "e.csv").write_text(csv)
    out = tmp_path / "m.json"
    rc = analysis_module.main_with_args([
        "--csv", f"eng={tmp_path / 'e.csv'}", "--schema", str(tmp_path / 's.json'),
        "--out", str(out),
    ])
    assert rc == 0
    m = json.loads(out.read_text())
    assert m["engines"]["eng"]["columns"]["empty_col"]["normalized_entropy"] == 0.0


def test_identity_column_sequential_detected(tmp_path):
    schema = [{"name": "id", "type": "STRING"}]
    (tmp_path / "s.json").write_text(json.dumps(schema))
    csv = "id\r\n" + "\r\n".join(str(i) for i in range(1, 17))
    (tmp_path / "e.csv").write_text(csv)
    out = tmp_path / "m.json"
    rc = analysis_module.main_with_args([
        "--csv", f"eng={tmp_path / 'e.csv'}", "--schema", str(tmp_path / 's.json'),
        "--identity-cols", "id", "--out", str(out),
    ])
    assert rc == 0
    m = json.loads(out.read_text())
    assert m["engines"]["eng"]["identity_columns"]["id"]["sequential"] is True


def test_identity_column_non_sequential(tmp_path):
    schema = [{"name": "id", "type": "STRING"}]
    (tmp_path / "s.json").write_text(json.dumps(schema))
    # A UUID-like identity column: unique per row, but not numeric/increasing.
    csv = "id\r\n" + "\r\n".join(f"row-{i}" for i in range(16))
    (tmp_path / "e.csv").write_text(csv)
    out = tmp_path / "m.json"
    rc = analysis_module.main_with_args([
        "--csv", f"eng={tmp_path / 'e.csv'}", "--schema", str(tmp_path / 's.json'),
        "--identity-cols", "id", "--out", str(out),
    ])
    assert rc == 0
    m = json.loads(out.read_text())
    assert m["engines"]["eng"]["identity_columns"]["id"]["sequential"] is False


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["7"], False),  # single value: no step to compare
        (["1", "2"], True),  # minimal strictly increasing pair
        (["5", "5", "5"], False),  # constant column: step 0 is not increasing
        (["9", "6", "3"], False),  # strictly decreasing: negative step
    ],
    ids=["single-value", "two-increasing", "constant-step-zero", "decreasing"],
)
def test_is_sequential_edge_branches(values, expected):
    assert analysis_module._is_sequential(values) is expected
