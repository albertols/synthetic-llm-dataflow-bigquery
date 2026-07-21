"""Tier-1 marginals & drift — small hand-built fixtures exercise every formula."""

import math

import pandas as pd
from sdfb_core.evaluation.metrics_t1 import (
    binned_frequencies,
    jsd,
    ks_statistic,
    numeric_and_categorical_columns,
    psi,
    tvd,
    wasserstein,
)


def test_column_split_by_dtype():
    df = pd.DataFrame({"a": [1, 2], "b": [0.5, 1.5], "c": ["x", "y"]})
    numeric, categorical = numeric_and_categorical_columns(df)
    assert numeric == ["a", "b"] and categorical == ["c"]


def test_ks_zero_for_identical_and_positive_for_shifted():
    s = pd.Series(range(100), dtype=float)
    assert ks_statistic(s, s) == 0.0
    assert ks_statistic(s, s + 50) > 0.4


def test_wasserstein_matches_shift():
    s = pd.Series(range(100), dtype=float)
    assert math.isclose(wasserstein(s, s + 10), 10.0, rel_tol=1e-9)


def test_empty_series_yield_none():
    empty = pd.Series([], dtype=float)
    s = pd.Series([1.0])
    assert ks_statistic(empty, s) is None
    assert wasserstein(s, empty) is None
    assert tvd(empty, s) is None


def test_tvd_bounds():
    r = pd.Series(["a", "a", "b", "b"])
    assert tvd(r, r) == 0.0
    assert tvd(r, pd.Series(["c", "c"])) == 1.0


def test_binned_frequencies_numeric_and_categorical():
    num = binned_frequencies(pd.Series(range(100), dtype=float), bins=5)
    assert num["kind"] == "numeric"
    assert len(num["edges"]) == 6 and len(num["freqs"]) == 5
    assert math.isclose(sum(num["freqs"]), 1.0, rel_tol=1e-9)
    cat = binned_frequencies(pd.Series(["x", "x", "y"]))
    assert cat["kind"] == "categorical"
    assert math.isclose(cat["freqs"]["x"], 2 / 3, rel_tol=1e-9)


def test_binned_frequencies_reuses_previous_edges():
    prev = binned_frequencies(pd.Series(range(100), dtype=float), bins=5)
    curr = binned_frequencies(pd.Series(range(50), dtype=float), edges=prev["edges"])
    assert curr["edges"] == prev["edges"]


def test_psi_jsd_zero_on_identical_positive_on_drift():
    a = binned_frequencies(pd.Series(range(100), dtype=float), bins=5)
    b = binned_frequencies(
        pd.Series([90.0] * 100), edges=a["edges"]
    )  # mass collapsed into one bin
    assert abs(psi(a, a)) < 1e-9
    assert psi(b, a) > 0.2
    assert abs(jsd(a, a)) < 1e-9
    assert jsd(b, a) > 0.1


def test_psi_none_on_misaligned_kinds_or_edges():
    num = binned_frequencies(pd.Series(range(100), dtype=float), bins=5)
    cat = binned_frequencies(pd.Series(["x", "y"]))
    assert psi(num, cat) is None
    other = binned_frequencies(pd.Series(range(200), dtype=float), bins=5)
    assert psi(num, other) is None  # different edges — not comparable
