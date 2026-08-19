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


# --------------------------------------------------------------------------
# build_mapping + stats_diff.json: source-only column names (table.skipped)
# and every profiled column name (columns dict keys) must be registered, not
# just the landing-side columns _collect_columns already walks.
# --------------------------------------------------------------------------
def _stats_diff_metrics() -> dict:
    return {
        "stats_diff": {
            "columns": {"amount": {"entropy_gap": 0.05, "decile_ks": 0.1}},
            "table": {
                "skipped": ["source_only_ssn_col"],
                "source_rows": 100,
                "synthetic_rows": 100,
            },
            "evaluation": None,
        },
    }


def test_build_mapping_registers_stats_diff_skipped_column():
    m = red.build_mapping(_stats_diff_metrics())
    assert "source_only_ssn_col" in m.columns
    assert "amount" in m.columns


def test_redact_text_replaces_stats_diff_skipped_column():
    m = red.build_mapping(_stats_diff_metrics())
    out = red.redact_text(
        m, "column source_only_ssn_col diverged badly; amount also drifted"
    )
    assert "source_only_ssn_col" not in out
    assert "amount" not in out


def test_leak_scan_catches_stats_diff_skipped_column_survivor(tmp_path):
    m = red.build_mapping(_stats_diff_metrics())
    # Simulate a bundle export that forgot to redact the raw stats_diff.json
    # copy (the exact bug this fix closes): the source-only column name
    # survives verbatim into an oss/ artifact.
    (tmp_path / "stats_diff_metrics.json").write_text(
        '{"table": {"skipped": ["source_only_ssn_col"]}}'
    )
    hits = red.leak_scan(tmp_path, m)
    assert ("stats_diff_metrics.json", "source_only_ssn_col") in hits


def test_build_mapping_ignores_non_stats_diff_shaped_metrics():
    # gcp/offline dicts have no top-level "columns" + "table" pair — must
    # not be misdetected as stats_diff-shaped.
    metrics = {
        "gcp": {"project": "p", "bigquery": {}},
        "offline": {"primary_key": [], "identity_columns": [], "engines": {}},
    }
    m = red.build_mapping(metrics)
    assert m.columns == {}


def test_mapping_from_dict_reapplies_the_same_replacements():
    """A persisted mapping.json must redact post-bundle artifacts with the
    SAME replacements the original export used (oss/ twins of late docs)."""
    m = red.Mapping()
    m.add_identifier("real-project-id", "PROJECT_1")
    m.add_column("customer_name")
    m.add_value("Alice Smith")

    rebuilt = red.mapping_from_dict(m.to_dict())
    text = "customer_name in real-project-id was Alice Smith"
    assert rebuilt.redact_text(text) == m.redact_text(text)
    assert "Alice Smith" not in rebuilt.redact_text(text)
