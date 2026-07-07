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
