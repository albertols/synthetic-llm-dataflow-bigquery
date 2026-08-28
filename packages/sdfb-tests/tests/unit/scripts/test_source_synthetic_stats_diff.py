from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "source_synthetic_stats_diff.py"
_spec = importlib.util.spec_from_file_location("source_synthetic_stats_diff", _SCRIPT)
sd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sd
_spec.loader.exec_module(sd)


def _profile(rows, cols):
    from sdfb_core.contracts.schema import FieldSchema, TableInfo, TableSchema
    schema = TableSchema(
        table_info=TableInfo(table_id="t"),
        columns=[FieldSchema(name=c, bq_type="STRING", mode="NULLABLE") for c in cols],
    )
    from sdfb_core.stats.source_stats import profile_source_table
    return profile_source_table(schema, rows)


def test_entropy_gap_detects_collapse():
    src = _profile([{"c": v} for v in "abcd" * 25], ["c"])       # balanced, 4 values
    syn = _profile([{"c": "a"} for _ in range(100)], ["c"])      # collapsed
    diff = sd.diff_profiles(src, syn)
    assert diff["columns"]["c"]["entropy_gap"] > 0.5


def test_decile_ks_zero_for_identical():
    assert sd.decile_ks([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 0.0


def test_decile_ks_positive_for_shifted():
    a = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    b = [x + 5 for x in a]
    assert sd.decile_ks(a, b) > 0.3


def test_diff_reserves_evaluation_slot_and_renders():
    src = _profile([{"c": "x"}], ["c"])
    diff = sd.diff_profiles(src, src)
    assert diff["evaluation"] is None
    md = sd.render_md(diff)
    assert "entropy" in md.lower()
