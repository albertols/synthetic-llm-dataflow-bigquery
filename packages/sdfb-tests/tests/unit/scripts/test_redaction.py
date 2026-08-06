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


def _write_real_png(path):
    """A real matplotlib-rendered PNG (Agg backend) — proves `leak_scan`
    tolerates binary files instead of crashing with `UnicodeDecodeError` on
    a non-UTF8 byte sequence (e.g. the PNG magic bytes `\\x89PNG`, which is
    guaranteed to raise under strict `Path.read_text()` decoding)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 4, 9])
    fig.savefig(path)
    plt.close(fig)


def test_leak_scan_tolerates_a_real_binary_png(tmp_path):
    _write_real_png(tmp_path / "chart.png")
    m = red.Mapping()
    m.identifiers["secret-dataset"] = "DATASET_1"
    # Must not raise UnicodeDecodeError, and a chart with no textual content
    # carries no identifiers to flag.
    assert red.leak_scan(tmp_path, m) == []


def test_leak_scan_catches_text_leak_next_to_a_binary_png(tmp_path):
    _write_real_png(tmp_path / "chart.png")
    m = red.Mapping()
    m.identifiers["secret-dataset"] = "DATASET_1"
    (tmp_path / "report.md").write_text("still mentions secret-dataset in prose")

    hits = red.leak_scan(tmp_path, m)
    assert ("report.md", "secret-dataset") in hits
