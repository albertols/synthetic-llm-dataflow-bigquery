"""Unit tests for `scripts/e2e/freetext_crosscheck.py` (offline-only).

Loaded via importlib like the sibling e2e script tests. Exercises the pure
shape-mining and diff functions with canned samples — no BQ, no network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "freetext_crosscheck.py"
_spec = importlib.util.spec_from_file_location("freetext_crosscheck", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


def test_shape_of_masks_charclasses():
    assert _mod.shape_of("AB-12 x") == "AA-99␣a"


def test_sample_sql_plain_has_no_column_filter():
    sql = _mod._sample_sql("p.d.t", ["A", "B"])
    assert "WHERE RAND() < @p" in sql
    assert "IS NOT NULL" not in sql


def test_sample_sql_nonempty_filter_targets_one_column():
    # 2026-08-09 B_TABLE R1, COL_037: `RAND() < p LIMIT lim` short-circuits
    # on storage order, so a 91%-empty column sampled all-empty and the
    # report showed shape recall 0.00 with NO missing shapes listed. The
    # top-up query samples that column's non-empty rows directly.
    sql = _mod._sample_sql("p.d.t", ["A"], nonempty_col="A")
    assert "`A` IS NOT NULL" in sql
    assert "!= ''" in sql
    assert "RAND() < @p" in sql


def test_needs_nonempty_topup_decision():
    # Too few sampled non-empty values + the aggregate proves substance
    # exists → top-up. A genuinely all-empty column never re-queries.
    assert _mod._needs_nonempty_topup(
        sampled_nonempty=3, agg={"n": 1000, "null_n": 100, "empty_n": 800}
    )
    assert not _mod._needs_nonempty_topup(
        sampled_nonempty=500, agg={"n": 1000, "null_n": 100, "empty_n": 300}
    )
    assert not _mod._needs_nonempty_topup(
        sampled_nonempty=0, agg={"n": 1000, "null_n": 200, "empty_n": 800}
    )


def test_collapse_compresses_runs():
    assert _mod.collapse(_mod.shape_of("2026-01-15")) == "9+-9+-9+"
    assert _mod.collapse(_mod.shape_of("AB")) == "A+"


def _profiles(src_vals, syn_vals, top_k=10):
    src = _mod._profile_sample(src_vals, top_k)
    syn = _mod._profile_sample(syn_vals, top_k)
    src["_all_values"] = src_vals
    syn["_all_values"] = syn_vals
    return src, syn


def _agg(n, null_n=0, empty_n=0, distinct=None, len_avg=10.0):
    return {
        "n": n, "null_n": null_n, "empty_n": empty_n,
        "distinct_n": distinct if distinct is not None else n,
        "len_min": 1, "len_max": 20, "len_avg": len_avg,
        "top_values": [],
    }


def test_diff_flags_missing_shapes_and_empty_gap():
    src_vals = [f"U{i:06d}" for i in range(50)] + ["AB.CD-99"] * 50
    syn_vals = [f"U{i:06d}" for i in range(100, 200)]
    src, syn = _profiles(src_vals, syn_vals)
    entry = _mod._diff_column(
        "C", _agg(1000, empty_n=900, len_avg=0.7), _agg(1000, len_avg=7.0),
        src, syn,
    )
    assert entry["diff"]["shape_recall"] < 0.9  # AB.CD-99 mass never learned
    assert entry["diff"]["empty_fraction_delta"] == -0.9
    assert any("empty fraction" in f["message"] for f in entry["findings"])
    assert entry["diff"]["copy_fraction"] == 0.0


def test_diff_copy_fraction_detects_memorization():
    vals = [f"REAL-{i:05d}" for i in range(100)]
    src, syn = _profiles(vals, vals)  # synthetic == source verbatim
    entry = _mod._diff_column("C", _agg(100), _agg(100), src, syn)
    assert entry["diff"]["copy_fraction"] == 1.0
    assert any(
        f["severity"] == "HIGH" and "memorization" in f["message"]
        for f in entry["findings"]
    )


def test_column_score_ranks_worse_higher():
    src_vals = ["A1"] * 50 + ["B2"] * 50
    good_syn = ["C3"] * 100  # same shape mask 'A9'
    bad_syn = ["totally different prose value"] * 100
    src, good = _profiles(src_vals, good_syn)
    _, bad = _profiles(src_vals, bad_syn)
    good_entry = _mod._diff_column("C", _agg(100), _agg(100), src, good)
    bad_entry = _mod._diff_column("C", _agg(100), _agg(100), src, bad)
    good_entry["score"] = _mod._column_score(good_entry)
    bad_entry["score"] = _mod._column_score(bad_entry)
    assert bad_entry["score"] > good_entry["score"]


def test_render_markdown_smoke():
    src_vals = [f"U{i:06d}" for i in range(20)]
    src, syn = _profiles(src_vals, src_vals[:10])
    entry = _mod._diff_column("COL_A", _agg(100), _agg(100), src, syn)
    entry["score"] = _mod._column_score(entry)
    meta = {
        "table": "T", "source_fqn": "p.d.T", "synthetic_fqn": "p.s.T",
        "source_rows": 100, "synthetic_rows": 100, "columns": ["COL_A"],
        "sample_size": 20, "generated_at": "2026-08-05", "caller": "test",
    }
    md = _mod.render_markdown(meta, {"COL_A": entry})
    assert "# Free-text pattern crosscheck" in md
    assert "COL_A" in md
