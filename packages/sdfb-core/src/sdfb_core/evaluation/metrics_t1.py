"""Tier-1 evaluation metrics — scipy/scikit-learn/pandas only (WS3 §3).

Pure functions over sampled rows / Series / DataFrames. This module never
imports Beam or GCP; the Beam EvaluationDoFn is its only pipeline caller.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial.distance import jensenshannon

_EPS = 1e-6
_DEFAULT_BINS = 10
_MIN_COLS = 2


def numeric_and_categorical_columns(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    return numeric, [c for c in df.columns if c not in numeric]


def ks_statistic(real: pd.Series, synth: pd.Series) -> float | None:
    r, s = real.dropna(), synth.dropna()
    if r.empty or s.empty:
        return None
    return float(stats.ks_2samp(r, s).statistic)


def wasserstein(real: pd.Series, synth: pd.Series) -> float | None:
    r, s = real.dropna(), synth.dropna()
    if r.empty or s.empty:
        return None
    return float(stats.wasserstein_distance(r, s))


def tvd(real: pd.Series, synth: pd.Series) -> float | None:
    """Total variation distance over the union of observed categories, [0,1]."""
    r = real.dropna().astype(str).value_counts(normalize=True)
    s = synth.dropna().astype(str).value_counts(normalize=True)
    if r.empty or s.empty:
        return None
    cats = r.index.union(s.index)
    return float(
        0.5
        * (r.reindex(cats, fill_value=0.0) - s.reindex(cats, fill_value=0.0))
        .abs()
        .sum()
    )


def binned_frequencies(
    series: pd.Series, *, bins: int = _DEFAULT_BINS, edges: list[float] | None = None
) -> dict:
    """Sufficient statistics for PSI/JSD, persisted in raw_metrics_json so the
    NEXT run can diff without re-reading raw rows. Passing the previous run's
    ``edges`` forces bin alignment across runs (numeric only)."""
    s = series.dropna()
    is_numeric = pd.api.types.is_numeric_dtype(s)
    if is_numeric and (edges is not None or s.nunique() > bins):
        if edges is None:
            edges = [float(e) for e in np.histogram_bin_edges(s, bins=bins)]
        counts, _ = np.histogram(s, bins=np.asarray(edges, dtype=float))
        total = max(int(counts.sum()), 1)
        return {
            "kind": "numeric",
            "edges": [float(e) for e in edges],
            "freqs": [float(c) / total for c in counts],
        }
    freqs = s.astype(str).value_counts(normalize=True)
    return {
        "kind": "categorical",
        "freqs": {str(k): float(v) for k, v in freqs.items()},
    }


def _aligned(curr: dict, prev: dict) -> tuple[np.ndarray, np.ndarray] | None:
    if curr.get("kind") != prev.get("kind"):
        return None
    if curr.get("kind") == "numeric":
        if curr.get("edges") != prev.get("edges"):
            return None
        p, q = np.asarray(curr["freqs"]), np.asarray(prev["freqs"])
    else:
        cats = sorted(set(curr.get("freqs", {})) | set(prev.get("freqs", {})))
        if not cats:
            return None
        p = np.asarray([curr["freqs"].get(c, 0.0) for c in cats])
        q = np.asarray([prev["freqs"].get(c, 0.0) for c in cats])
    return p + _EPS, q + _EPS


def psi(curr: dict, prev: dict) -> float | None:
    """Population Stability Index, this run vs the previous run's stats."""
    aligned = _aligned(curr, prev)
    if aligned is None:
        return None
    p, q = aligned
    p, q = p / p.sum(), q / q.sum()
    return float(np.sum((p - q) * np.log(p / q)))


def jsd(curr: dict, prev: dict) -> float | None:
    """Jensen-Shannon divergence (natural log; bounded [0, ln 2])."""
    aligned = _aligned(curr, prev)
    if aligned is None:
        return None
    p, q = aligned
    return float(jensenshannon(p, q, base=math.e) ** 2)


def corr_diff_frobenius(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, *, method: str = "pearson"
) -> float | None:
    """‖corr_real - corr_synth‖_F over the shared numeric columns."""
    numeric, _ = numeric_and_categorical_columns(real_df)
    cols = [c for c in numeric if c in synth_df.columns]
    if len(cols) < _MIN_COLS:
        return None
    r = real_df[cols].corr(method=method).fillna(0.0)
    s = synth_df[cols].corr(method=method).fillna(0.0)
    return float(np.linalg.norm(r.to_numpy() - s.to_numpy(), ord="fro"))


def _discretize(df: pd.DataFrame, *, bins: int = _DEFAULT_BINS) -> pd.DataFrame:
    """Quantile-bin high-cardinality numerics so a single mutual_info_score
    call covers every column pair (numeric↔numeric, mixed, cat↔cat)."""
    out: dict[str, pd.Series] = {}
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) and s.nunique() > bins:
            out[c] = pd.qcut(s, q=bins, labels=False, duplicates="drop").astype(str)
        else:
            out[c] = s.astype(str)
    return pd.DataFrame(out)


def mi_matrix_diff(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, *, bins: int = _DEFAULT_BINS
) -> float | None:
    """Frobenius diff of pairwise mutual-information matrices — catches
    nonlinear dependency loss that Pearson/Spearman miss."""
    from sklearn.metrics import mutual_info_score

    cols = [c for c in real_df.columns if c in synth_df.columns]
    if len(cols) < _MIN_COLS:
        return None
    r = _discretize(real_df[cols], bins=bins)
    s = _discretize(synth_df[cols], bins=bins)
    n = len(cols)
    rm, sm = np.zeros((n, n)), np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            rm[i, j] = rm[j, i] = mutual_info_score(r[cols[i]], r[cols[j]])
            sm[i, j] = sm[j, i] = mutual_info_score(s[cols[i]], s[cols[j]])
    return float(np.linalg.norm(rm - sm, ord="fro"))
