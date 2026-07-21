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
