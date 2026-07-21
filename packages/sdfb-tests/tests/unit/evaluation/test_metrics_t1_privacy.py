"""Privacy tier — the metrics that feed the §5a/§5b gate and the §5c floor."""

import numpy as np
import pandas as pd
from sdfb_core.evaluation.metrics_t1 import (
    cardinality_floor,
    column_copy_ratios,
    dcr_nndr,
    identical_match_rate,
)


def test_dcr_zero_when_synth_copies_real_larger_when_novel():
    rng = np.random.default_rng(3)
    real = pd.DataFrame({"a": rng.normal(size=50), "k": ["x", "y"] * 25})
    copied, _ = dcr_nndr(real, real.copy())
    novel, _ = dcr_nndr(real, pd.DataFrame({"a": rng.normal(loc=10, size=50), "k": ["z"] * 50}))
    assert copied == 0.0
    assert novel > copied


def test_nndr_near_zero_flags_reidentification():
    # One real record isolated far from the rest; synth sits on top of it.
    real = pd.DataFrame({"a": [0.0, 0.1, 0.2, 100.0]})
    synth = pd.DataFrame({"a": [100.0]})
    _, nndr = dcr_nndr(real, synth)
    assert nndr is not None and nndr < 0.05


def test_dcr_nndr_none_on_degenerate_input():
    assert dcr_nndr(pd.DataFrame({"a": [1.0]}), pd.DataFrame({"a": [1.0]})) == (None, None)
    assert dcr_nndr(pd.DataFrame({"a": [1.0, 2.0]}), pd.DataFrame({"a": []})) == (None, None)


def test_identical_match_rate_counts_exact_digest_hits():
    real = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    synth = [{"a": 1, "b": "x"}, {"a": 3, "b": "z"}]
    assert identical_match_rate(real, synth) == 0.5
    assert identical_match_rate(real, []) is None
    assert identical_match_rate([], synth) is None


def test_column_copy_ratios_only_high_cardinality_columns():
    n = 150
    real = [{"id": f"id-{i}", "tier": f"t{i % 3}"} for i in range(n)]
    # Half the synth ids are verbatim copies; 'tier' (3 distinct ≤ 100) is exempt.
    synth = [{"id": f"id-{i}" if i % 2 else f"new-{i}", "tier": "t0"} for i in range(n)]
    ratios = column_copy_ratios(real, synth)
    assert "tier" not in ratios
    assert abs(ratios["id"] - 0.5) < 0.01


def test_column_copy_ratio_full_copy_is_one():
    n = 150
    real = [{"id": f"id-{i}"} for i in range(n)]
    ratios = column_copy_ratios(real, list(real))
    assert ratios["id"] == 1.0


def test_cardinality_floor_flags_bounded_pool_collapse():
    real = [{"note": f"text-{i}"} for i in range(1000)]
    synth = [{"note": f"text-{i % 32}"} for i in range(500)]  # 32-value pool collapse
    floor = cardinality_floor(real, synth, free_text_columns=["note"], num_rows=500)
    assert abs(floor["note"] - 32 / 500) < 1e-9
    healthy = cardinality_floor(real, real[:500], free_text_columns=["note"], num_rows=500)
    assert healthy["note"] == 1.0
    assert cardinality_floor(real, synth, free_text_columns=[], num_rows=500) == {}
