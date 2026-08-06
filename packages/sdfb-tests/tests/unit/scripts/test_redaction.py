"""Unit tests for `scripts/e2e/redaction.py` (extracted from the bundle exporter).

Loaded via importlib the same way `test_e2e_bundle_export.py` loads
`scripts/e2e/e2e_bundle_export.py` (see that file's docstring/idiom).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "redaction.py"
_spec = importlib.util.spec_from_file_location("redaction", _SCRIPT)
red = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = red
_spec.loader.exec_module(red)


def test_redact_text_replaces_known_identifiers():
    m = red.Mapping()
    m.identifiers["real-project-id"] = "PROJECT_1"
    out = red.redact_text(m, "job ran in real-project-id today")
    assert "real-project-id" not in out and "PROJECT_1" in out


def test_leak_scan_flags_surviving_token(tmp_path):
    m = red.Mapping()
    m.identifiers["secret-dataset"] = "DATASET_1"
    (tmp_path / "r.md").write_text("still says secret-dataset")
    hits = red.leak_scan(tmp_path, m)
    assert hits
