"""Unit tests for `scripts/e2e/redact_doc.py` (post-bundle oss/ doc twin).

Loaded via importlib the same way `test_e2e_bundle_export.py` loads its
script (see that file's docstring/idiom).

The tool exists for artifacts created AFTER the bundle export ran (e.g. the
llm_prompt_constraint_recommender's recommendations report): it rebuilds the
`Mapping` from the persisted `real/mapping.json` and applies the standard
replacements, so the oss/ twin is agnostic (COL_NNN / VAL_NNNN) and
consistent with every other file in `oss/`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "redact_doc.py"
_spec = importlib.util.spec_from_file_location("redact_doc", _SCRIPT)
redact_doc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = redact_doc
_spec.loader.exec_module(redact_doc)


def _write_mapping(tmp_path: Path) -> Path:
    mapping_path = tmp_path / "real" / "mapping.json"
    mapping_path.parent.mkdir(parents=True)
    mapping_path.write_text(
        json.dumps(
            {
                "identifiers": {"my-real-gcp-project": "PROJECT_ID"},
                "columns": {"customer_name": "COL_001"},
                "values": {"Alice Smith": "VAL_0001"},
            }
        )
    )
    return mapping_path


def test_redact_doc_writes_oss_twin_with_standard_mapping(tmp_path):
    mapping_path = _write_mapping(tmp_path)
    in_path = tmp_path / "real" / "prompt_constraint_recommendations.md"
    in_path.write_text(
        "# Recommendations\n\n"
        "customer_name in my-real-gcp-project drifted; top literal was "
        "Alice Smith.\nRecommend pattern ^[A-Z]{5}$.\n"
    )
    out_path = tmp_path / "oss" / "prompt_constraint_recommendations.md"

    rc = redact_doc.main(
        [
            "--mapping", str(mapping_path),
            "--in", str(in_path),
            "--out", str(out_path),
        ]
    )
    assert rc == 0

    oss_text = out_path.read_text()
    assert "my-real-gcp-project" not in oss_text
    assert "customer_name" not in oss_text
    assert "Alice Smith" not in oss_text
    assert "COL_001" in oss_text and "VAL_0001" in oss_text
    assert "^[A-Z]{5}$" in oss_text  # structural content survives
    # real/ input is untouched
    assert "customer_name" in in_path.read_text()


def test_redact_doc_fails_on_surviving_token(tmp_path):
    """Column names are whole-word-replaced, but the leak check is substring
    (same classes as redaction.leak_scan) — a column embedded in a larger
    snake_case token escapes replacement and must fail the run."""
    mapping_path = _write_mapping(tmp_path)
    in_path = tmp_path / "real" / "notes.md"
    in_path.write_text("see pre_customer_name_post for details\n")
    out_path = tmp_path / "oss" / "notes.md"

    rc = redact_doc.main(
        [
            "--mapping", str(mapping_path),
            "--in", str(in_path),
            "--out", str(out_path),
        ]
    )
    assert rc == 1
