"""Correlation/MI structure metrics — catch destroyed inter-column relationships."""

import numpy as np
import pandas as pd
from sdfb_core.evaluation.metrics_t1 import corr_diff_frobenius, mi_matrix_diff


def _correlated(n=200, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    return pd.DataFrame({"x": x, "y": 2 * x + rng.normal(scale=0.1, size=n)})


def _independent(n=200, seed=8) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"x": rng.normal(size=n), "y": rng.normal(size=n)})


def test_corr_diff_zero_for_same_frame_large_when_structure_destroyed():
    real = _correlated()
    assert corr_diff_frobenius(real, real) == 0.0
    assert corr_diff_frobenius(real, _independent()) > 1.0


def test_corr_diff_spearman_method_and_insufficient_columns():
    real = _correlated()
    assert corr_diff_frobenius(real, _independent(), method="spearman") > 1.0
    single = pd.DataFrame({"x": [1.0, 2.0]})
    assert corr_diff_frobenius(single, single) is None


def test_mi_matrix_diff_zero_same_positive_when_dependence_lost():
    real = _correlated(400)
    assert mi_matrix_diff(real, real) == 0.0
    assert mi_matrix_diff(real, _independent(400)) > 0.1


def test_mi_handles_categorical_columns():
    real = pd.DataFrame({"k": ["a", "a", "b", "b"] * 25, "v": [1, 1, 2, 2] * 25})
    synth = pd.DataFrame({"k": ["a", "b"] * 50, "v": [1, 2] * 50})
    assert mi_matrix_diff(real, synth) is not None
