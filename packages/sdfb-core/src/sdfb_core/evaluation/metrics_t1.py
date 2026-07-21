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

from sdfb_core.validation.uniqueness import row_digest

_EPS = 1e-6
_DEFAULT_BINS = 10
_MIN_COLS = 2
_MIN_REAL_ROWS = 2


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


def _gower_embed(
    real_df: pd.DataFrame, synth_df: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray] | None:
    """ONE concatenated feature matrix per side: min-max-normalized numerics
    (bounds fit on the real side) + one-hot categoricals scaled by 1/√2 (a
    category mismatch then contributes distance 1, like a full numeric span).
    Gower-STYLE approximation (design §3); never metric='precomputed' — a
    dense nxn distance matrix would blow the single-worker memory bound."""
    numeric, categorical = numeric_and_categorical_columns(real_df)
    numeric = [c for c in numeric if c in synth_df.columns]
    categorical = [c for c in categorical if c in synth_df.columns]
    if not numeric and not categorical:
        return None
    blocks_r: list[np.ndarray] = []
    blocks_s: list[np.ndarray] = []
    for c in numeric:
        r = pd.to_numeric(real_df[c], errors="coerce")
        s = pd.to_numeric(synth_df[c], errors="coerce")
        lo = float(r.min()) if r.notna().any() else 0.0
        hi = float(r.max()) if r.notna().any() else 0.0
        span = (hi - lo) or 1.0
        blocks_r.append(((r.fillna(lo) - lo) / span).clip(0.0, 1.0).to_numpy()[:, None])
        blocks_s.append(((s.fillna(lo) - lo) / span).clip(0.0, 1.0).to_numpy()[:, None])
    for c in categorical:
        cats = sorted(set(real_df[c].astype(str)) | set(synth_df[c].astype(str)))
        index = {v: i for i, v in enumerate(cats)}
        onehot_r = np.zeros((len(real_df), len(cats)))
        onehot_s = np.zeros((len(synth_df), len(cats)))
        for row_idx, v in enumerate(real_df[c].astype(str)):
            onehot_r[row_idx, index[v]] = 1.0
        for row_idx, v in enumerate(synth_df[c].astype(str)):
            onehot_s[row_idx, index[v]] = 1.0
        blocks_r.append(onehot_r / math.sqrt(2.0))
        blocks_s.append(onehot_s / math.sqrt(2.0))
    return np.hstack(blocks_r), np.hstack(blocks_s)


def dcr_nndr(
    real_df: pd.DataFrame, synth_df: pd.DataFrame
) -> tuple[float | None, float | None]:
    """(mean Distance to Closest Record, mean Nearest-Neighbor Distance
    Ratio) of the synthetic sample vs the real sample. Tree-based
    NearestNeighbors — O(n log n), never a dense pairwise matrix."""
    from sklearn.neighbors import NearestNeighbors

    if len(real_df) < _MIN_REAL_ROWS or len(synth_df) == 0:
        return None, None
    embedded = _gower_embed(real_df, synth_df)
    if embedded is None:
        return None, None
    real_matrix, synth_matrix = embedded
    nn = NearestNeighbors(n_neighbors=2).fit(real_matrix)
    dist, _ = nn.kneighbors(synth_matrix)
    d1, d2 = dist[:, 0], dist[:, 1]
    avg_dcr = float(np.mean(d1))
    nndr = float(np.mean(d1 / np.maximum(d2, 1e-12)))
    return avg_dcr, nndr


def identical_match_rate(
    real_rows: list[dict], synth_rows: list[dict]
) -> float | None:
    """Fraction of sampled synthetic rows whose full-row content digest
    exactly matches a sampled real row's — feeds the §5a gate."""
    if not real_rows or not synth_rows:
        return None
    real_digests = {row_digest(r) for r in real_rows}
    hits = sum(1 for r in synth_rows if row_digest(r) in real_digests)
    return hits / len(synth_rows)


def column_copy_ratios(
    real_rows: list[dict],
    synth_rows: list[dict],
    *,
    min_source_distinct: int = 100,
) -> dict[str, float]:
    """Spec §5b — per-column verbatim-copy ratio vs the reference sample, for
    columns whose reference distinct count exceeds min_source_distinct (the
    offline probe's exact rule; low-cardinality enums are legitimately
    verbatim). Ratio = fraction of non-null synthetic values present in the
    reference sample's value set."""
    if not real_rows or not synth_rows:
        return {}
    out: dict[str, float] = {}
    for col in real_rows[0]:
        real_values = {r.get(col) for r in real_rows if r.get(col) is not None}
        if len(real_values) <= min_source_distinct:
            continue
        synth_values = [r.get(col) for r in synth_rows if r.get(col) is not None]
        if not synth_values:
            continue
        out[col] = sum(1 for v in synth_values if v in real_values) / len(synth_values)
    return out


def cardinality_floor(
    real_rows: list[dict],
    synth_rows: list[dict],
    *,
    free_text_columns: list[str],
    num_rows: int,
) -> dict[str, float]:
    """Spec §5c — landing_distinct / min(num_rows, source_distinct) per
    FREE_TEXT column. MAJOR recorded metric; never job-failing. Values well
    below 1.0 expose bounded-pool collapse (the 2026-07-19 b1 residual)."""
    out: dict[str, float] = {}
    for col in free_text_columns:
        source_distinct = len({r.get(col) for r in real_rows if r.get(col) is not None})
        denominator = min(num_rows, source_distinct) if num_rows > 0 else source_distinct
        if denominator <= 0:
            continue
        landing_distinct = len(
            {r.get(col) for r in synth_rows if r.get(col) is not None}
        )
        out[col] = landing_distinct / denominator
    return out
