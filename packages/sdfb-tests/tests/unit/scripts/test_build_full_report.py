"""Unit tests for `scripts/e2e/build_full_report.py` (one-file bundle recap).

Loaded via importlib the same way `test_e2e_bundle_export.py` loads its
script (see that file's docstring/idiom).

The tool unifies every report `.md` + metrics `.json` of a bundle dir
(`real/` or `oss/`) into a single `_full_report.md` — ToC at the top, one
section per document (content verbatim), metrics embedded as ```json
annexes at the bottom — so one paste carries a whole deployment's evidence.
Discovery-based: future `.md`/`.json` artifacts are included automatically;
the output itself and `mapping.json` (the decode key, not a report input)
are excluded.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "build_full_report.py"
_spec = importlib.util.spec_from_file_location("build_full_report", _SCRIPT)
build_full_report = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = build_full_report
_spec.loader.exec_module(build_full_report)

_JOB = "2026-08-11_06_04_05-9010984585999295806"


def _make_bundle(tmp_path: Path, variant: str = "real") -> Path:
    d = tmp_path / _JOB / variant
    d.mkdir(parents=True)
    (d / "report.md").write_text("# E2E report\n\nverdict fine.\n")
    (d / "stats_diff.md").write_text("# Stats diff\n\ndrift none.\n")
    (d / "freetext_crosscheck_report.md").write_text(
        "# Crosscheck\n\nshapes ok.\n"
    )
    (d / "prompt_constraint_recommendations.md").write_text(
        "# Recommendations\n\nadd a pattern.\n"
    )
    (d / "gcp_metrics.json").write_text('{"dataflow": []}')
    (d / "offline_metrics.json").write_text('{"engines": {}}')
    (d / "stats_diff_metrics.json").write_text('{"columns": {}}')
    (d / "freetext_crosscheck_metrics.json").write_text('{"meta": {}}')
    (d / "mapping.json").write_text('{"identifiers": {"decode-key-secret": "X"}}')
    return d


def test_full_report_unifies_docs_and_annexes(tmp_path):
    d = _make_bundle(tmp_path)
    rc = build_full_report.main(["--dir", str(d)])
    assert rc == 0

    text = (d / "_full_report.md").read_text()
    # Title names the job id + variant; ToC references every doc and annex.
    assert _JOB in text.splitlines()[0]
    assert "(real)" in text.splitlines()[0]
    assert "## Table of contents" in text
    for doc in (
        "report.md",
        "stats_diff.md",
        "freetext_crosscheck_report.md",
        "prompt_constraint_recommendations.md",
    ):
        assert f"[{doc}](#doc-" in text
    for annex in (
        "gcp_metrics.json",
        "offline_metrics.json",
        "stats_diff_metrics.json",
        "freetext_crosscheck_metrics.json",
    ):
        assert f"[{annex}](#annex-" in text

    # Document content verbatim; metrics fenced as ```json.
    for chunk in ("verdict fine.", "drift none.", "shapes ok.", "add a pattern."):
        assert chunk in text
    assert "```json" in text
    assert '{"dataflow": []}' in text

    # Canonical order: report first, then stats diff, crosscheck, recs;
    # annexes follow all docs.
    assert (
        text.index('id="doc-report-md"')
        < text.index('id="doc-stats-diff-md"')
        < text.index('id="doc-freetext-crosscheck-report-md"')
        < text.index('id="doc-prompt-constraint-recommendations-md"')
        < text.index('id="annex-gcp-metrics-json"')
        < text.index('id="annex-offline-metrics-json"')
        < text.index('id="annex-stats-diff-metrics-json"')
    )

    # mapping.json is the decode key — never embedded, never in the ToC.
    assert "decode-key-secret" not in text
    assert "mapping.json" not in text


def test_full_report_is_idempotent_and_excludes_itself(tmp_path):
    d = _make_bundle(tmp_path, variant="oss")
    assert build_full_report.main(["--dir", str(d)]) == 0
    assert build_full_report.main(["--dir", str(d)]) == 0

    text = (d / "_full_report.md").read_text()
    assert text.count("## Table of contents") == 1
    assert "[_full_report.md]" not in text
    assert "(oss)" in text.splitlines()[0]


def test_full_report_discovers_future_files_after_canonical(tmp_path):
    d = _make_bundle(tmp_path)
    (d / "custom_notes.md").write_text("# Notes\n\nfuture doc content.\n")
    (d / "extra_metrics.json").write_text('{"future": true}')

    assert build_full_report.main(["--dir", str(d)]) == 0
    text = (d / "_full_report.md").read_text()

    assert "[custom_notes.md](#doc-custom-notes-md)" in text
    assert "future doc content." in text
    assert "[extra_metrics.json](#annex-extra-metrics-json)" in text
    assert '{"future": true}' in text
    # Non-canonical files land after the canonical ones in their group.
    assert text.index('id="doc-prompt-constraint-recommendations-md"') < text.index(
        'id="doc-custom-notes-md"'
    )
    assert text.index('id="annex-extra-metrics-json"') > text.index(
        'id="annex-freetext-crosscheck-metrics-json"'
    )


def test_full_report_handles_both_dirs_in_one_call(tmp_path):
    real = _make_bundle(tmp_path, variant="real")
    oss = _make_bundle(tmp_path, variant="oss")
    rc = build_full_report.main(["--dir", str(real), "--dir", str(oss)])
    assert rc == 0
    assert (real / "_full_report.md").exists()
    assert (oss / "_full_report.md").exists()
