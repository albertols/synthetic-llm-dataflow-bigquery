"""Post-run freetext parity/diversity/copy rules (Task 11)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from sdfb_core.validation.thresholds import load_thresholds

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "e2e_gcp_probe.py"
_spec = importlib.util.spec_from_file_location("e2e_gcp_probe_ft", _SCRIPT)
_probe = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _probe
_spec.loader.exec_module(_probe)

_THRESHOLDS = Path(__file__).parents[5] / "config" / "thresholds.yml"


def test_catalog_carries_freetext_rules():
    t = load_thresholds(_THRESHOLDS, env="prd")
    for rule_id in (
        "freetext.empty_parity",
        "freetext.distinct_floor",
        "freetext.copy_fraction",
    ):
        assert rule_id in t.rules, rule_id
        assert t.rules[rule_id].get("scope") == "post_run"


def _columns() -> dict:
    return {
        "SPARSE": {  # source 91% empty, synthetic 0% — the crosscheck gap
            "in_source_schema": True,
            "empty_fraction": 0.0,
            "source_empty_fraction": 0.914,
            "distinct": 512,
            "source_distinct": 73231,
            "source_distinct_ratio": 0.038,
            "copy_ratio_nonsentinel": 0.0,
        },
        "IDS": {  # high-cardinality: distinct collapsed to pool size
            "in_source_schema": True,
            "empty_fraction": 0.05,
            "source_empty_fraction": 0.05,
            "distinct": 512,
            "source_distinct": 146046,
            "source_distinct_ratio": 0.76,
            "copy_ratio_nonsentinel": 0.0,
        },
        "LEAKY": {  # verbatim copies on a high-cardinality column
            "in_source_schema": True,
            "empty_fraction": 0.1,
            "source_empty_fraction": 0.1,
            "distinct": 90000,
            "source_distinct": 100000,
            "source_distinct_ratio": 0.9,
            "copy_ratio_nonsentinel": 0.4,
        },
        "NOT_IN_SRC": {"in_source_schema": False},
    }


def test_evaluate_freetext_rules_flags_the_three_defects():
    results = _probe.evaluate_freetext_rules(_columns())
    by = {(r["rule"], r["column"]): r for r in results}

    assert by[("freetext.empty_parity", "SPARSE")]["passed"] is False
    assert by[("freetext.empty_parity", "IDS")]["passed"] is True
    assert by[("freetext.distinct_floor", "IDS")]["passed"] is False
    assert by[("freetext.copy_fraction", "LEAKY")]["passed"] is False
    assert by[("freetext.copy_fraction", "IDS")]["passed"] is True
    assert not [r for r in results if r["column"] == "NOT_IN_SRC"]


def test_copy_fraction_tolerates_millionth_scale_noise():
    """2026-08-09 R1 (both tables): a few-in-a-million coincidental
    source/synthetic collision failed the strict max=0.0 gate and the
    rounded display value read 0.0 — a 'fails at 0.0' contradiction. The
    threshold is now a table-size epsilon and the reported value keeps
    enough precision to show WHY."""
    cols = {
        "NOISY": {
            "in_source_schema": True,
            "empty_fraction": 0.0,
            "source_empty_fraction": 0.0,
            "distinct": 900000,
            "source_distinct": 34622,
            "source_distinct_ratio": 0.9,
            "copy_ratio_substantive": 2.2e-05,  # COL_009's raw value
        },
        "LEAKY": {
            "in_source_schema": True,
            "empty_fraction": 0.0,
            "source_empty_fraction": 0.0,
            "distinct": 90000,
            "source_distinct": 100000,
            "source_distinct_ratio": 0.9,
            "copy_ratio_substantive": 0.4,
        },
    }
    by = {
        (r["rule"], r["column"]): r
        for r in _probe.evaluate_freetext_rules(cols)
    }
    noisy = by[("freetext.copy_fraction", "NOISY")]
    assert noisy["passed"] is True
    assert noisy["value"] == 2.2e-05  # unrounded — never displays as 0.0
    assert by[("freetext.copy_fraction", "LEAKY")]["passed"] is False


def test_distinct_floor_skips_low_cardinality_sources():
    cols = {
        "ENUMISH": {
            "in_source_schema": True,
            "empty_fraction": 0.0,
            "source_empty_fraction": 0.0,
            "distinct": 5,
            "source_distinct": 40,
            "source_distinct_ratio": 0.02,
            "copy_ratio_nonsentinel": 0.0,
        }
    }
    rules = [r["rule"] for r in _probe.evaluate_freetext_rules(cols)]
    assert "freetext.distinct_floor" not in rules
