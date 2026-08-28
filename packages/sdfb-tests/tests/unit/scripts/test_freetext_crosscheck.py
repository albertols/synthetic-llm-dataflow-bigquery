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


def test_sample_sql_is_deterministic_hash_ordered():
    # 2026-08-20 R1 pair: `RAND() < @p LIMIT @lim` with p oversampled 3x
    # short-circuits on storage order, so the sample covered only the
    # storage-front slice of the table. Every "shape mass" finding on a
    # skewed column was distorted by it (COL_015 "49% spurious alpha mass",
    # COL_024 68.6%->7.7% "inversion" — both contradicted by the same
    # bundle's full-table top_values). Hash-ordered sampling is unbiased
    # w.r.t. storage order AND deterministic (same table -> same sample),
    # mirroring e2e_fetch_samples.py / source_synthetic_stats_diff.py.
    sql = _mod._sample_sql("p.d.t", ["A", "B"])
    assert "FARM_FINGERPRINT(TO_JSON_STRING(t))" in sql
    assert "RAND()" not in sql
    assert "IS NOT NULL" not in sql


def test_sample_sql_nonempty_filter_targets_one_column():
    # The top-up variant samples the column's substantive rows directly
    # (2026-08-09 B_TABLE R1, COL_037: a 91%-empty column sampled all-empty
    # and the report showed shape recall 0.00 with NO missing shapes).
    sql = _mod._sample_sql("p.d.t", ["A"], nonempty_col="A")
    assert "`A` IS NOT NULL" in sql
    assert "!= ''" in sql
    assert "FARM_FINGERPRINT(TO_JSON_STRING(t))" in sql
    assert "RAND()" not in sql


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


def test_diff_copy_fraction_carves_out_enum_reuse():
    # 2026-08-11 B_TABLE R1, COL_015: the full-table probe scored
    # `copy_ratio_substantive = 0.000` while this sample crosscheck said
    # 0.27 for the same column — the ~5% substantive mass is a handful of
    # low-cardinality codes whose reuse IS categorical fidelity (the
    # k-anonymity floor of WS8 §5b). A synthetic value observed >= 10 times
    # in the source sample is enum mass: it leaves numerator AND
    # denominator; the raw number stays visible as copy_fraction_raw.
    enum_vals = ["1007"] * 60 + ["6009"] * 40
    tail_src = [f"SRC-{i:04d}" for i in range(40)]
    tail_syn = [f"NEW-{i:04d}" for i in range(38)] + tail_src[:2]
    src, syn = _profiles(enum_vals + tail_src, enum_vals + tail_syn)
    entry = _mod._diff_column("C", _agg(140), _agg(140), src, syn)
    # 2 verbatim copies over 40 substantive (non-enum) synthetic values.
    assert entry["diff"]["copy_fraction"] == 0.05
    assert entry["diff"]["copy_fraction_raw"] > 0.7


def test_diff_copy_fraction_unchanged_without_enum_mass():
    vals = [f"REAL-{i:05d}" for i in range(100)]
    src, syn = _profiles(vals, vals)
    entry = _mod._diff_column("C", _agg(100), _agg(100), src, syn)
    assert entry["diff"]["copy_fraction"] == 1.0
    assert entry["diff"]["copy_fraction_raw"] == 1.0


def test_diff_surfaces_long_tail_missing_shapes():
    # 2026-08-20 B_TABLE R1, COL_037: shape recall 0.68 with
    # `missing_shapes: []` — every unreproduced shape sat under the 2% mass
    # floor, so the report said "top missing: n/a" while a third of the
    # source mass was missing. The floored list now falls back to the top
    # missing shapes and the tail is counted explicitly.
    head = ["OK1"] * 150
    tail = [f"{'X' * (i + 1)}-7" for i in range(25)] * 4  # 25 shapes, 1.6% each
    syn = ["OK9"] * 100  # reproduces only the head shape 'AA9'
    src_prof, syn_prof = _profiles(head + tail, syn)
    entry = _mod._diff_column("C", _agg(250), _agg(100), src_prof, syn_prof)
    d = entry["diff"]
    assert d["shape_recall"] < 0.9
    assert d["missing_shapes"], "long-tail miss must still name examples"
    assert d["missing_shapes_below_floor"] == 25
    assert not any(
        "top missing: n/a" in f["message"] for f in entry["findings"]
    )


def test_diff_scores_shape_mass_inversion():
    # 2026-08-20 B_TABLE R1, COL_024: source shape mass 68.6%/30.9% vs
    # synthetic 7.7%/92.2% over the SAME two masks scored 0.033 ("no
    # material divergence") because recall/precision are presence-only.
    # The total-variation term sees the inversion.
    src_vals = ["1" * 16] * 69 + ["35"] * 31
    inverted = ["2" * 16] * 8 + ["46"] * 92
    faithful = ["3" * 16] * 69 + ["57"] * 31
    src, bad = _profiles(src_vals, inverted)
    _, good = _profiles(src_vals, faithful)
    bad_entry = _mod._diff_column("C", _agg(100), _agg(100), src, bad)
    good_entry = _mod._diff_column("C", _agg(100), _agg(100), src, good)
    assert bad_entry["diff"]["shape_recall"] == 1.0
    assert bad_entry["diff"]["shape_precision"] == 1.0
    assert bad_entry["diff"]["shape_mass_tv"] > 0.5
    assert bad_entry["diff"]["shape_head_tv"] > 0.5  # both shapes are head
    assert good_entry["diff"]["shape_mass_tv"] < 0.05
    assert any("shape mass" in f["message"] for f in bad_entry["findings"])
    assert (
        _mod._column_score(bad_entry) > _mod._column_score(good_entry)
    )


def test_head_tv_is_quiet_on_near_unique_mask_columns():
    # 2026-08-21 four-run cycle: raw exact-mask TV saturates at ~1.0 on
    # near-unique-mask columns (UUID measured TV 0.956) — two ~unique-mask
    # sets are disjoint even for a PERFECT generator, the same artifact
    # that already invalidated recall there (ADR 0026). The actionable
    # metric is TV over the NAMED head shapes (source mass >= the 2%
    # floor) with the long tail grouped as one bucket: near-unique columns
    # have no head, so they stay quiet; mass inversions on named shapes
    # still trip it.
    src_vals = ["A" * (10 + i) for i in range(100)]  # 100 unique masks, 1% each
    syn_vals = ["B" * (111 + i) for i in range(100)]  # disjoint unique masks
    src, syn = _profiles(src_vals, syn_vals)
    entry = _mod._diff_column("C", _agg(100), _agg(100), src, syn)
    d = entry["diff"]
    assert d["shape_mass_tv"] > 0.9  # the raw metric saturates by design
    assert d["shape_head_tv"] < 0.05
    assert not any("shape mass" in f["message"] for f in entry["findings"])
