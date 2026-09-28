#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""Dependence from dense accumulators: one mergeable `BivariateAccumulator`
per (table, side) over a plan-fixed list of column pairs, feeding
`pair.pearson_delta`, `pair.spearman_delta`, `pair.cramers_v_delta`,
`pair.nmi_delta`, `pair.contingency_tvd`, `table.corr_rms_delta` and
`table.corr_max_delta` (`src/sdfb_evaluation/catalogue/metrics.yaml`).

The Beam accumulator (a later task) feeds `BivariateAccumulator.add_batch`
one `(b, d)` numpy batch per bundle — `d` is the accumulator's own column
list (the union of columns its `pairs` index into, not the whole table) —
then merges accumulators pairwise across bundles/workers via `merge`. Two
parallel co-moment tables are kept per pair: `com_std` over values already
standardized by the PLAN's mean/std (feeds `pair.pearson_delta`), and
`com_pit` over each side's own probability-integral-transform rank (feeds
`pair.spearman_delta` — Spearman as "Pearson of the ranks", scaled to
`[0, 1]` rather than `1..n`). Both are the sum-form co-moments `(n, Sx, Sy,
Sxx, Syy, Sxy)`, computed PAIRWISE COMPLETE: a row missing either side of a
pair (`NaN` in `z_std`/`z_pit`) contributes to neither.

Standardizing (or PIT-transforming) BEFORE summing is what keeps the
sum-form correlation formula numerically stable: raw epoch-microsecond
timestamps around `1.7e15` blow the sum-form's `n * Sxx - Sx**2` far past
float64's ~16 significant digits, but once each column is rescaled to
roughly unit scale by the plan's own mean/std, the same formula is exact to
machine precision (`test_epoch_microsecond_values_are_stable`; Chan, Golub &
LeVeque, 1983; Pébay, 2008, take the more general pairwise-merge route for
exactly this reason on RAW values — here the caller's own standardization
already does that job before any sum is taken).

`counts2d` is the joint bin/dictionary table each pair needs for
`pair.cramers_v_delta`, `pair.nmi_delta` and `pair.contingency_tvd`: `codes`
carries one integer slot per column per row, `0..bins-1` for a bin or
dictionary entry and `bins` itself for a null — so every row lands somewhere
in the `(bins + 1, bins + 1)` table, the null bin included on both axes, and
`add_batch` never needs to mask a row out of it the way it does for the
co-moments.

Design: docs/designs/2026-07-07-evaluation-framework-design.md

References:
  Pearson, K. (1896), "Mathematical Contributions to the Theory of
    Evolution. III. Regression, Heredity, and Panmixia" (the product-moment
    correlation `pearson_from_comoments` computes).
  Spearman, C. (1904), "The Proof and Measurement of Association between
    Two Things" (rank correlation; here `pearson_from_comoments` applied to
    each side's own PIT rank, per D3's exchangeable-rank convention).
  Chan, T., Golub, G., LeVeque, R. (1983), "Algorithms for Computing the
    Sample Variance: Analysis and Recommendations".
  Pébay, P. (2008), "Formulas for Robust, One-Pass Parallel Computation of
    Covariances and Arbitrary-Order Statistical Moments", Sandia Report
    SAND2008-6212.
  Bergsma, W. (2013), "A Bias-Correction for Cramér's V and Tschuprow's T".
  Cover, T., Thomas, J. (2006), "Elements of Information Theory", 2nd ed.
    (mutual information and entropy, both in nats here).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# com_std/com_pit column layout: the sum-form co-moments `(n, Sx, Sy, Sxx,
# Syy, Sxy)` a Pearson correlation needs, and nothing else — additive over
# disjoint rows, which is what keeps `merge` exact.
_N_COMOMENTS = 6

# The default bin/dictionary-slot count `B` (Task 21's plan uses 10):
# `counts2d` then has `B + 1` slots per axis, the extra slot being the null
# bin every pair's joint table carries on both axes.
_DEFAULT_BINS = 10

# A correlation needs at least this many pairwise-complete points to be
# more than a perfect fit through noise; below it `pearson_from_comoments`
# returns NaN rather than a number with no statistical content.
_MIN_PEARSON_N = 3

# Every array this module validates the shape of is (b, d): one batch axis,
# one column axis.
_NDIM_2D = 2

# A pair is (col_i, col_j) — exactly two column indices.
_PAIR_SIZE = 2

# `cramers_v_bias_corrected` is undefined with fewer than 2 non-empty rows
# or columns (there is then no association left to measure).
_MIN_NONEMPTY_ROWS_COLS = 2


def _validate_pair_indices(pairs: tuple[tuple[int, int], ...]) -> None:
  """Raise `ValueError` unless every pair is a `(non-negative, non-negative)`
  pair of column indices — a caller bug (a negative or malformed plan index)
  should fail at construction, not silently misplace batches later.
  """
  for pair in pairs:
    if len(pair) != _PAIR_SIZE or pair[0] < 0 or pair[1] < 0:
      raise ValueError(
          f"pairs must be (col_i, col_j) with non-negative indices: got {pair!r}"
      )


@dataclass(eq=False)
class BivariateAccumulator:
  """One (table, side)'s mergeable dense dependence accumulator, over a
  plan-fixed list of column `pairs` (indices into the `d` columns of every
  `add_batch` call, not into the whole table).

  `counts2d` is `int64 (P, bins + 1, bins + 1)`: pair `p`'s joint table over
  `codes`, null bin included on both axes. `com_std`/`com_pit` are `float64
  (P, 6)`: the sum-form co-moments `(n, Sx, Sy, Sxx, Syy, Sxy)`, over
  standardized values and each side's own PIT respectively (pairwise
  complete — see the module docstring). All three start at zero counts/sums
  (`BivariateAccumulator(pairs=...)` is always a valid starting point for
  `add_batch`/`merge`, mirroring `stats.moments.Moments`).

  `eq=False`: the default dataclass `__eq__` would compare `counts2d`/
  `com_std`/`com_pit` with plain `==`, which raises on a multi-element numpy
  array rather than returning a bool — identity equality (the `object`
  default) is what a mutable, array-holding accumulator wants instead; use
  `numpy.testing.assert_array_equal` on the fields to compare two by value.
  """
  pairs: tuple[tuple[int, int], ...]
  bins: int = _DEFAULT_BINS
  counts2d: np.ndarray = field(init=False, repr=False)
  com_std: np.ndarray = field(init=False, repr=False)
  com_pit: np.ndarray = field(init=False, repr=False)

  def __post_init__(self) -> None:
    _validate_pair_indices(self.pairs)
    p = len(self.pairs)
    b1 = self.bins + 1
    self.counts2d = np.zeros((p, b1, b1), dtype=np.int64)
    self.com_std = np.zeros((p, _N_COMOMENTS), dtype=np.float64)
    self.com_pit = np.zeros((p, _N_COMOMENTS), dtype=np.float64)
    # Column indices for every pair's two sides, precomputed once so
    # `add_batch` never re-derives them from `pairs` on the hot path.
    self._i_idx = np.array([pair[0] for pair in self.pairs], dtype=np.int64)
    self._j_idx = np.array([pair[1] for pair in self.pairs], dtype=np.int64)

  def add_batch(
      self,
      z_std: np.ndarray,
      z_pit: np.ndarray,
      codes: np.ndarray,
  ) -> None:
    """Fold one `(b, d)` batch into this accumulator, in place.

    `z_std`/`z_pit` are float arrays (`NaN` = null); `codes` is an int array
    of bin/dictionary slots, `0..bins - 1` for a real slot and `bins` for a
    null — every row of `codes` lands in `counts2d` regardless of nulls,
    while `com_std`/`com_pit` only fold in rows that are non-null on BOTH
    sides of a pair (pairwise complete).

    Vectorized over every pair at once via masked matrix products (no
    per-pair Python loop): each pair's two columns are gathered into a
    `(b, P)` slice, and every accumulator update is a single `numpy`
    reduction over the batch axis across all `P` pairs simultaneously —
    what keeps this practical at the plan's ~2000-pair scale.

    Raises `ValueError` if `z_std`, `z_pit` and `codes` are not all the
    same `(b, d)` shape, if `codes` holds a value outside `[0, bins]`, or
    if a pair references a column index `>= d`.
    """
    z_std_arr = np.asarray(z_std, dtype=np.float64)
    z_pit_arr = np.asarray(z_pit, dtype=np.float64)
    codes_arr = np.asarray(codes)
    if z_std_arr.shape != z_pit_arr.shape or z_std_arr.shape != codes_arr.shape:
      raise ValueError(
          "z_std, z_pit and codes must share one (b, d) shape: got "
          f"{z_std_arr.shape}, {z_pit_arr.shape}, {codes_arr.shape}")
    if z_std_arr.ndim != _NDIM_2D:
      raise ValueError(
          f"z_std/z_pit/codes must be 2-D (b, d); got ndim={z_std_arr.ndim}")
    b, d = z_std_arr.shape
    if b == 0:
      return
    max_col = int(max(self._i_idx.max(),
                      self._j_idx.max())) if self._i_idx.size else -1
    if max_col >= d:
      raise ValueError(
          f"pairs reference column index {max_col}, but the batch has only "
          f"{d} columns")
    if codes_arr.size and (codes_arr.min() < 0 or codes_arr.max() > self.bins):
      raise ValueError(
          f"codes must be in [0, {self.bins}] (bin/dictionary slots "
          f"0..{self.bins - 1} plus null={self.bins}); got "
          f"[{codes_arr.min()}, {codes_arr.max()}]")

    self.com_std += _pairwise_comoments(z_std_arr, self._i_idx, self._j_idx)
    self.com_pit += _pairwise_comoments(z_pit_arr, self._i_idx, self._j_idx)
    self.counts2d += _pairwise_counts(codes_arr, self._i_idx, self._j_idx,
                                      self.bins)

  def merge(self, other: BivariateAccumulator) -> BivariateAccumulator:
    """The pairwise combination of `self` and `other`, as a new accumulator.

    Pure (neither operand is modified) and exact: `counts2d`/`com_std`/
    `com_pit` are plain element-wise sums, so the combination is
    associative and commutative regardless of how a Beam runner trees the
    merges. Raises `ValueError` if `other` was built over a different
    `pairs` list or a different `bins` — the two accumulators must describe
    the identical joint tables to be summed at all.
    """
    if self.pairs != other.pairs:
      raise ValueError(
          f"cannot merge accumulators with different pairs: {self.pairs!r} "
          f"vs {other.pairs!r}")
    if self.bins != other.bins:
      raise ValueError(
          f"cannot merge accumulators with different bins: {self.bins!r} "
          f"vs {other.bins!r}")
    result = BivariateAccumulator(pairs=self.pairs, bins=self.bins)
    result.counts2d = self.counts2d + other.counts2d
    result.com_std = self.com_std + other.com_std
    result.com_pit = self.com_pit + other.com_pit
    return result


def _pairwise_comoments(
    z: np.ndarray,
    i_idx: np.ndarray,
    j_idx: np.ndarray,
) -> np.ndarray:
  """`(P, 6)` batch co-moments `(n, Sx, Sy, Sxx, Syy, Sxy)`, one row per
  pair, over the pairwise-complete rows of each pair's two columns of `z`.

  A masked matrix product over every pair at once: `z[:, i_idx]`/`z[:,
  j_idx]` gather both sides of every pair into `(b, P)` slices in one step,
  `NaN` marks a null on either side, and every sum below is a single
  reduction over the batch axis across all `P` pairs — no per-pair loop.
  """
  xi = z[:, i_idx]  # (b, P)
  xj = z[:, j_idx]  # (b, P)
  mask = ~np.isnan(xi) & ~np.isnan(xj)
  xi0 = np.where(mask, xi, 0.0)
  xj0 = np.where(mask, xj, 0.0)
  n = mask.sum(axis=0, dtype=np.float64)
  sx = xi0.sum(axis=0)
  sy = xj0.sum(axis=0)
  sxx = (xi0 * xi0).sum(axis=0)
  syy = (xj0 * xj0).sum(axis=0)
  sxy = (xi0 * xj0).sum(axis=0)
  return np.stack([n, sx, sy, sxx, syy, sxy], axis=1)


def _pairwise_counts(
    codes: np.ndarray,
    i_idx: np.ndarray,
    j_idx: np.ndarray,
    bins: int,
) -> np.ndarray:
  """`(P, bins + 1, bins + 1)` batch joint-table counts, one table per pair,
  over EVERY row (nulls included via the `bins` code).

  A single flattened `numpy.bincount` over `(pair, code_i, code_j)` indices
  stands in for a per-pair, per-row loop: pair `p`'s table occupies its own
  contiguous block of the flat index space, so one `bincount` fills every
  pair's table at once.
  """
  b1 = bins + 1
  p = i_idx.size
  ci = codes[:, i_idx].astype(np.int64)  # (b, P)
  cj = codes[:, j_idx].astype(np.int64)  # (b, P)
  pair_offset = (np.arange(p, dtype=np.int64) * b1 * b1)[np.newaxis, :]
  flat = (pair_offset + ci * b1 + cj).ravel()
  counts = np.bincount(flat, minlength=p * b1 * b1)
  return counts.reshape((p, b1, b1)).astype(np.int64)


def pearson_from_comoments(c: np.ndarray) -> np.ndarray:
  """Pearson product-moment correlation (Pearson, 1896) per pair, `(P,)`,
  from co-moment rows `[n, Sx, Sy, Sxx, Syy, Sxy]` (`BivariateAccumulator
  .com_std`/`.com_pit`, shape `(P, 6)`).

  Uses the algebraically-equivalent sum-form identity `rho = (n*Sxy -
  Sx*Sy) / sqrt((n*Sxx - Sx**2) * (n*Syy - Sy**2))` rather than re-centering
  from the co-moments — safe here specifically because `com_std`/`com_pit`
  are sums over values already standardized (or PIT-transformed) by the
  caller, so no large raw magnitude ever reaches this formula (see the
  module docstring and `test_epoch_microsecond_values_are_stable`).

  `NaN` — this module's internal null sentinel; callers convert it to
  `None` — where `n < 3` (fewer points than needed for a correlation to
  mean anything beyond a perfect fit) or either side has zero variance (an
  undefined slope).
  """
  c_arr = np.asarray(c, dtype=np.float64)
  n = c_arr[..., 0]
  sx = c_arr[..., 1]
  sy = c_arr[..., 2]
  sxx = c_arr[..., 3]
  syy = c_arr[..., 4]
  sxy = c_arr[..., 5]
  varx = n * sxx - sx * sx
  vary = n * syy - sy * sy
  with np.errstate(divide="ignore", invalid="ignore"):
    rho = (n * sxy - sx * sy) / np.sqrt(varx * vary)
  invalid = (n < _MIN_PEARSON_N) | (varx <= 0.0) | (vary <= 0.0)
  return np.where(invalid, np.nan, rho)


def cramers_v_bias_corrected(table: np.ndarray) -> float | None:
  """Bergsma's (2013) bias-corrected Cramér's V from a 2-D contingency
  `table` of counts.

  All-zero rows and columns are dropped first (they carry no association
  information and would otherwise make an `expected` cell zero). From the
  remaining `r x k` table: `chi2` (expected cells from the margins, skipping
  any that are still zero), `phi2 = chi2 / n`, then the bias correction
  `phi2_tilde = max(0, phi2 - (k-1)(r-1)/(n-1))`, `k_tilde = k -
  (k-1)**2/(n-1)`, `r_tilde = r - (r-1)**2/(n-1)`, and finally `V_tilde =
  sqrt(phi2_tilde / min(k_tilde - 1, r_tilde - 1))`.

  `None` when `n <= 1`, fewer than 2 non-empty rows or columns remain after
  dropping, or the final denominator is `<= 0` — every one of these makes
  the bias correction undefined rather than merely noisy. Raises
  `ValueError` if `table` is not 2-D.
  """
  t = np.asarray(table, dtype=np.float64)
  if t.ndim != _NDIM_2D:
    raise ValueError(f"table must be 2-D; got ndim={t.ndim}")
  t = t[t.sum(axis=1) > 0][:, t.sum(axis=0) > 0]
  r, k = t.shape
  n = float(t.sum())
  if n <= 1.0 or r < _MIN_NONEMPTY_ROWS_COLS or k < _MIN_NONEMPTY_ROWS_COLS:
    return None
  row_sums = t.sum(axis=1, keepdims=True)
  col_sums = t.sum(axis=0, keepdims=True)
  expected = row_sums * col_sums / n
  valid = expected > 0.0
  safe_expected = np.where(valid, expected, 1.0)
  chi2 = float(np.sum(np.where(valid, (t - expected)**2 / safe_expected, 0.0)))
  phi2 = chi2 / n
  phi2_tilde = max(0.0, phi2 - (k - 1) * (r - 1) / (n - 1))
  k_tilde = k - (k - 1)**2 / (n - 1)
  r_tilde = r - (r - 1)**2 / (n - 1)
  denom = min(k_tilde - 1.0, r_tilde - 1.0)
  if denom <= 0.0:
    return None
  return math.sqrt(phi2_tilde / denom)


def nmi_min(table: np.ndarray) -> float | None:
  """Mutual information normalized by the smaller marginal entropy,
  `I(X; Y) / min(H_X, H_Y)`, in natural logs (units cancel in the ratio),
  from a 2-D contingency `table` of counts (Cover & Thomas, 2006).

  Not the same normalization as `sklearn.metrics.normalized_mutual_info_
  score`'s default (arithmetic mean of `H_X`, `H_Y`); this deliberately
  uses the min instead — a caller comparing to sklearn needs a hand-computed
  value, not that default (see `test_nmi_min_hand_computed_value`).

  `None` when `table` has zero total mass, or `min(H_X, H_Y) == 0` (one
  side is a single category, so the ratio's denominator is genuinely zero
  rather than merely small). Raises `ValueError` if `table` is not 2-D.
  """
  t = np.asarray(table, dtype=np.float64)
  if t.ndim != _NDIM_2D:
    raise ValueError(f"table must be 2-D; got ndim={t.ndim}")
  n = float(t.sum())
  if n <= 0.0:
    return None
  p = t / n
  p_row = p.sum(axis=1)
  p_col = p.sum(axis=0)
  h_x = _entropy_nats(p_row)
  h_y = _entropy_nats(p_col)
  min_h = min(h_x, h_y)
  if min_h <= 0.0:
    return None
  outer = np.outer(p_row, p_col)
  mask = p > 0.0
  with np.errstate(divide="ignore", invalid="ignore"):
    terms = p * np.log(p / outer)
  mi = float(np.sum(np.where(mask, terms, 0.0)))
  return mi / min_h


def _entropy_nats(p: np.ndarray) -> float:
  """Shannon entropy in nats, `-sum(p * ln(p))` over `p > 0` only (`x *
  ln(x) -> 0` as `x -> 0`, so a zero-mass category contributes nothing
  rather than raising on `ln(0)`).
  """
  mask = p > 0.0
  with np.errstate(divide="ignore", invalid="ignore"):
    terms = p * np.log(p)
  return float(-np.sum(np.where(mask, terms, 0.0)))


def contingency_tvd(t_src: np.ndarray, t_syn: np.ndarray) -> float | None:
  """Total variation distance `0.5 * sum(|p_ab - q_ab|)` between the
  normalized `t_src` and `t_syn` joint tables (same shape; `p`/`q` are
  `t_src`/`t_syn` each divided by their own total).

  `None` when either table has zero total mass. Raises `ValueError` if
  `t_src` and `t_syn` are not the same shape — they must be counted on the
  identical joint dictionary (source values plus the null bin) to be
  comparable at all.
  """
  src = np.asarray(t_src, dtype=np.float64)
  syn = np.asarray(t_syn, dtype=np.float64)
  if src.shape != syn.shape:
    raise ValueError(
        f"t_src and t_syn must be the same shape: got {src.shape} and "
        f"{syn.shape}")
  total_src = float(src.sum())
  total_syn = float(syn.sum())
  if total_src <= 0.0 or total_syn <= 0.0:
    return None
  p = src / total_src
  q = syn / total_syn
  return float(0.5 * np.sum(np.abs(p - q)))


def corr_rms_max(delta: np.ndarray) -> tuple[float | None, float | None]:
  """`(sqrt(mean(Delta**2)), max(abs(Delta)))` over the finite entries of a
  1-D array `delta` of per-pair correlation differences (`table.corr_rms_
  delta`, `table.corr_max_delta`); `NaN` entries (a pair `pearson_from_
  comoments` could not score) are skipped.

  `(None, None)` when no entry is finite — nothing to summarize.
  """
  d = np.asarray(delta, dtype=np.float64)
  valid = d[~np.isnan(d)]
  if valid.size == 0:
    return None, None
  rms = float(np.sqrt(np.mean(valid**2)))
  max_abs = float(np.max(np.abs(valid)))
  return rms, max_abs
