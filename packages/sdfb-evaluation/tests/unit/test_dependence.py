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
"""Tests for `sdfb_evaluation.stats.dependence` (Task 10).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import stats
from scipy.stats import contingency

from sdfb_evaluation.stats.dependence import BivariateAccumulator
from sdfb_evaluation.stats.dependence import contingency_tvd
from sdfb_evaluation.stats.dependence import corr_rms_max
from sdfb_evaluation.stats.dependence import cramers_v_bias_corrected
from sdfb_evaluation.stats.dependence import nmi_min
from sdfb_evaluation.stats.dependence import pearson_from_comoments

# ---------------------------------------------------------------------------
# BivariateAccumulator — construction
# ---------------------------------------------------------------------------


def test_empty_accumulator_is_a_valid_starting_point():
  acc = BivariateAccumulator(pairs=((0, 1), (1, 2)))
  assert acc.counts2d.shape == (2, 11, 11)
  assert acc.com_std.shape == (2, 6)
  assert acc.com_pit.shape == (2, 6)
  assert acc.counts2d.sum() == 0
  assert acc.com_std.sum() == 0
  assert acc.com_pit.sum() == 0


def test_accumulator_honours_custom_bins():
  acc = BivariateAccumulator(pairs=((0, 1),), bins=3)
  assert acc.counts2d.shape == (1, 4, 4)


def test_accumulator_with_no_pairs_is_empty_but_valid():
  acc = BivariateAccumulator(pairs=())
  assert acc.counts2d.shape == (0, 11, 11)
  assert acc.com_std.shape == (0, 6)


def test_negative_pair_index_raises():
  with pytest.raises(ValueError, match="non-negative"):
    BivariateAccumulator(pairs=((-1, 0),))


# ---------------------------------------------------------------------------
# add_batch — co-moments, pairwise-complete
# ---------------------------------------------------------------------------


def test_add_batch_comoments_match_manual_pairwise_complete_sums():
  # column 0 and 1 form the pair; row 2 has a null on column 0, row 3 a
  # null on column 1 — neither row may contribute to the pair's co-moments.
  z = np.array([
      [1.0, 2.0],
      [2.0, 1.0],
      [np.nan, 5.0],
      [3.0, np.nan],
      [4.0, 0.0],
  ])
  codes = np.zeros_like(z, dtype=np.int64)
  acc = BivariateAccumulator(pairs=((0, 1),))
  acc.add_batch(z, z, codes)

  complete = z[~np.isnan(z).any(axis=1)]
  x, y = complete[:, 0], complete[:, 1]
  expected = np.array([
      x.size,
      x.sum(),
      y.sum(),
      (x**2).sum(),
      (y**2).sum(),
      (x * y).sum(),
  ])
  np.testing.assert_allclose(acc.com_std[0], expected)
  np.testing.assert_allclose(acc.com_pit[0], expected)


def test_add_batch_vectorizes_correctly_over_multiple_pairs():
  rng = np.random.default_rng(42)
  b, d = 200, 4
  z = rng.normal(size=(b, d))
  # Scatter some nulls independently per column.
  for col in range(d):
    null_rows = rng.choice(b, size=10, replace=False)
    z[null_rows, col] = np.nan
  codes = np.zeros((b, d), dtype=np.int64)

  pairs = ((0, 1), (0, 2), (2, 3))
  acc = BivariateAccumulator(pairs=pairs)
  acc.add_batch(z, z, codes)

  for p_idx, (i, j) in enumerate(pairs):
    mask = ~np.isnan(z[:, i]) & ~np.isnan(z[:, j])
    x, y = z[mask, i], z[mask, j]
    expected = np.array(
        [x.size,
         x.sum(),
         y.sum(), (x**2).sum(), (y**2).sum(), (x * y).sum()])
    np.testing.assert_allclose(acc.com_std[p_idx], expected, atol=1e-9)


def test_add_batch_shape_mismatch_raises():
  acc = BivariateAccumulator(pairs=((0, 1),))
  z = np.zeros((3, 2))
  codes = np.zeros((3, 2), dtype=np.int64)
  with pytest.raises(ValueError, match="shape"):
    acc.add_batch(z, np.zeros((4, 2)), codes)


def test_add_batch_wrong_ndim_raises():
  acc = BivariateAccumulator(pairs=((0, 1),))
  z1d = np.zeros(4)
  with pytest.raises(ValueError, match="2-D"):
    acc.add_batch(z1d, z1d, np.zeros(4, dtype=np.int64))


def test_add_batch_pair_column_out_of_range_raises():
  acc = BivariateAccumulator(pairs=((0, 5),))
  z = np.zeros((3, 2))
  codes = np.zeros((3, 2), dtype=np.int64)
  with pytest.raises(ValueError, match="column index"):
    acc.add_batch(z, z, codes)


def test_add_batch_codes_out_of_range_raises():
  acc = BivariateAccumulator(pairs=((0, 1),), bins=2)
  z = np.zeros((2, 2))
  bad_codes = np.array([[0, 1], [3, 0]], dtype=np.int64)  # 3 > bins (=2)
  with pytest.raises(ValueError, match="codes must be"):
    acc.add_batch(z, z, bad_codes)


def test_add_batch_empty_batch_is_a_no_op():
  acc = BivariateAccumulator(pairs=((0, 1),))
  acc.add_batch(
      np.zeros((0, 2)), np.zeros((0, 2)), np.zeros((0, 2), dtype=np.int64))
  assert acc.com_std.sum() == 0
  assert acc.counts2d.sum() == 0


# ---------------------------------------------------------------------------
# add_batch — counts2d, null bin included on both axes
# ---------------------------------------------------------------------------


def test_add_batch_counts2d_includes_the_null_bin_on_both_axes():
  bins = 2  # valid slots 0, 1; null = 2
  codes = np.array(
      [
          [0, 1],
          [2, 0],  # null on column 0
          [1, 2],  # null on column 1
          [2, 2],  # null on both
      ],
      dtype=np.int64)
  z = np.zeros_like(codes, dtype=np.float64)
  acc = BivariateAccumulator(pairs=((0, 1),), bins=bins)
  acc.add_batch(z, z, codes)

  table = acc.counts2d[0]
  assert table.shape == (3, 3)
  assert table[0, 1] == 1
  assert table[2, 0] == 1
  assert table[1, 2] == 1
  assert table[2, 2] == 1
  assert table.sum() == 4


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def _random_accumulator(pairs, bins, seed, b=50, d=3):
  rng = np.random.default_rng(seed)
  z_std = rng.normal(size=(b, d))
  z_pit = rng.uniform(size=(b, d))
  codes = rng.integers(0, bins + 1, size=(b, d)).astype(np.int64)
  acc = BivariateAccumulator(pairs=pairs, bins=bins)
  acc.add_batch(z_std, z_pit, codes)
  return acc


def test_merge_is_pure():
  pairs = ((0, 1), (1, 2))
  a = _random_accumulator(pairs, bins=4, seed=1)
  b = _random_accumulator(pairs, bins=4, seed=2)
  a_counts_before = a.counts2d.copy()
  b_counts_before = b.counts2d.copy()
  merged = a.merge(b)
  np.testing.assert_array_equal(a.counts2d, a_counts_before)
  np.testing.assert_array_equal(b.counts2d, b_counts_before)
  np.testing.assert_array_equal(merged.counts2d, a.counts2d + b.counts2d)


def test_merge_commutes():
  pairs = ((0, 1), (1, 2))
  a = _random_accumulator(pairs, bins=4, seed=3)
  b = _random_accumulator(pairs, bins=4, seed=4)
  left = a.merge(b)
  right = b.merge(a)
  np.testing.assert_array_equal(left.counts2d, right.counts2d)
  np.testing.assert_allclose(left.com_std, right.com_std)
  np.testing.assert_allclose(left.com_pit, right.com_pit)


def test_merge_equals_single_pass():
  pairs = ((0, 2), (1, 2))
  rng = np.random.default_rng(5)
  b, d = 80, 3
  z_std = rng.normal(size=(b, d))
  z_pit = rng.uniform(size=(b, d))
  codes = rng.integers(0, 6, size=(b, d)).astype(np.int64)

  whole = BivariateAccumulator(pairs=pairs, bins=5)
  whole.add_batch(z_std, z_pit, codes)

  split_a = BivariateAccumulator(pairs=pairs, bins=5)
  split_a.add_batch(z_std[:30], z_pit[:30], codes[:30])
  split_b = BivariateAccumulator(pairs=pairs, bins=5)
  split_b.add_batch(z_std[30:], z_pit[30:], codes[30:])
  merged = split_a.merge(split_b)

  np.testing.assert_array_equal(whole.counts2d, merged.counts2d)
  np.testing.assert_allclose(whole.com_std, merged.com_std)
  np.testing.assert_allclose(whole.com_pit, merged.com_pit)


@settings(max_examples=25)
@given(
    st.integers(min_value=0, max_value=40),
    st.integers(min_value=0, max_value=40))
def test_merge_is_associative_and_commutative_property(n1, n2):
  pairs = ((0, 1),)
  rng = np.random.default_rng(n1 * 1000 + n2)
  a = BivariateAccumulator(pairs=pairs, bins=3)
  b = BivariateAccumulator(pairs=pairs, bins=3)
  c = BivariateAccumulator(pairs=pairs, bins=3)
  for acc, n in ((a, n1), (b, n2), (c, 7)):
    if n:
      z_std = rng.normal(size=(n, 2))
      z_pit = rng.uniform(size=(n, 2))
      codes = rng.integers(0, 4, size=(n, 2)).astype(np.int64)
      acc.add_batch(z_std, z_pit, codes)
  left = a.merge(b).merge(c)
  right = a.merge(b.merge(c))
  np.testing.assert_array_equal(left.counts2d, right.counts2d)
  np.testing.assert_array_equal(a.merge(b).counts2d, b.merge(a).counts2d)


def test_merge_raises_on_mismatched_pairs():
  a = BivariateAccumulator(pairs=((0, 1),))
  b = BivariateAccumulator(pairs=((0, 2),))
  with pytest.raises(ValueError, match="pairs"):
    a.merge(b)


def test_merge_raises_on_mismatched_bins():
  a = BivariateAccumulator(pairs=((0, 1),), bins=4)
  b = BivariateAccumulator(pairs=((0, 1),), bins=5)
  with pytest.raises(ValueError, match="bins"):
    a.merge(b)


# ---------------------------------------------------------------------------
# pearson_from_comoments
# ---------------------------------------------------------------------------


def test_pearson_matches_corrcoef_on_complete_rows():
  rng = np.random.default_rng(7)
  b = 300
  z = rng.normal(size=(b, 2))
  z[:, 1] = 0.6 * z[:, 0] + 0.4 * rng.normal(size=b)
  null_rows = rng.choice(b, size=15, replace=False)
  z[null_rows[:8], 0] = np.nan
  z[null_rows[8:], 1] = np.nan
  codes = np.zeros((b, 2), dtype=np.int64)

  acc = BivariateAccumulator(pairs=((0, 1),))
  acc.add_batch(z, z, codes)
  rho = pearson_from_comoments(acc.com_std)[0]

  complete = z[~np.isnan(z).any(axis=1)]
  expected = np.corrcoef(complete[:, 0], complete[:, 1])[0, 1]
  assert rho == pytest.approx(expected, abs=1e-9)


def test_pearson_none_below_min_n():
  c = np.array([[2, 1.0, 1.0, 1.0, 1.0, 1.0]])  # n=2 < 3
  result = pearson_from_comoments(c)
  assert math.isnan(result[0])


def test_pearson_none_for_zero_variance():
  # x constant (all 5.0): Sxx = n * 25, Sx = n * 5 -> n*Sxx - Sx**2 == 0.
  n = 10.0
  c = np.array([[n, n * 5.0, n * 3.0, n * 25.0, n * 9.0 + 10, n * 15.0]])
  result = pearson_from_comoments(c)
  assert math.isnan(result[0])


def test_pearson_vectorizes_over_multiple_rows():
  # Two independent pairs stacked: perfect positive and perfect negative.
  x = np.arange(5, dtype=np.float64)
  y_pos = x.copy()
  y_neg = -x
  rows = []
  for y in (y_pos, y_neg):
    rows.append(
        [x.size,
         x.sum(),
         y.sum(), (x**2).sum(), (y**2).sum(), (x * y).sum()])
  c = np.array(rows)
  result = pearson_from_comoments(c)
  np.testing.assert_allclose(result, [1.0, -1.0], atol=1e-9)


def test_epoch_microsecond_values_are_stable():
  """Standardizing epoch-microsecond timestamps before summing co-moments
  keeps `pearson_from_comoments` exact; applying the same sum-form formula
  directly to the raw ~1.7e15 values loses almost all precision instead
  (`n * Sxx - Sx**2`, computed from numbers around `2.9e33`, tries to
  recover a true value around `3e11` — far below float64's ~16 significant
  digits of resolution), which is the failure mode standardizing first
  avoids (see the module docstring; Chan, Golub & LeVeque, 1983).
  """
  rng = np.random.default_rng(0)
  n = 2000
  base = 1.7e15
  x = base + np.arange(n, dtype=np.float64)
  noise = rng.normal(scale=1e-4, size=n)
  y = 2.0 * x + noise

  mean_x, std_x = x.mean(), x.std()
  mean_y, std_y = y.mean(), y.std()
  z = np.stack([(x - mean_x) / std_x, (y - mean_y) / std_y], axis=1)

  acc = BivariateAccumulator(pairs=((0, 1),))
  acc.add_batch(z, z, np.zeros((n, 2), dtype=np.int64))
  rho_std = pearson_from_comoments(acc.com_std)[0]
  assert abs(rho_std - 1.0) < 1e-9

  # Document the cancellation: the same sum-form formula, applied directly
  # to the unstandardized epoch-microsecond values.
  n_f = float(n)
  sx, sy = float(x.sum()), float(y.sum())
  sxx = float((x * x).sum())
  syy = float((y * y).sum())
  sxy = float((x * y).sum())
  varx_raw = n_f * sxx - sx * sx
  vary_raw = n_f * syy - sy * sy
  with np.errstate(invalid="ignore", divide="ignore"):
    rho_raw = float((n_f * sxy - sx * sy) / np.sqrt(varx_raw * vary_raw))
  raw_ok = math.isfinite(rho_raw) and -1.0 <= rho_raw <= 1.0
  if raw_ok:
    raw_error = abs(rho_raw - 1.0)
    if raw_error <= 1e-6:
      # This platform's float64 rounding happened to keep enough precision
      # for the raw formula to still look right; the standardized result
      # above is what the accumulator actually relies on either way.
      return
    assert raw_error > 1e-6
  else:
    assert not raw_ok  # NaN or out of [-1, 1]: the cancellation is total.


# ---------------------------------------------------------------------------
# PIT Spearman (Pearson of each side's own PIT rank)
# ---------------------------------------------------------------------------


def test_pit_spearman_matches_scipy_within_tolerance():
  rng = np.random.default_rng(11)
  n = 500
  x = rng.normal(size=n)
  y = 2.0 * x + rng.normal(scale=0.5, size=n)
  pit_x = stats.rankdata(x) / n
  pit_y = stats.rankdata(y) / n
  z_pit = np.stack([pit_x, pit_y], axis=1)
  codes = np.zeros((n, 2), dtype=np.int64)

  acc = BivariateAccumulator(pairs=((0, 1),))
  acc.add_batch(z_pit, z_pit, codes)
  rho_pit = pearson_from_comoments(acc.com_pit)[0]

  expected_rho, _ = stats.spearmanr(x, y)
  assert abs(rho_pit - expected_rho) < 0.01


# ---------------------------------------------------------------------------
# cramers_v_bias_corrected
# ---------------------------------------------------------------------------


def test_cramers_v_near_zero_for_independent_5x5_at_10k():
  rng = np.random.default_rng(13)
  n = 10_000
  rows = rng.integers(0, 5, size=n)
  cols = rng.integers(0, 5, size=n)
  table = np.zeros((5, 5), dtype=np.int64)
  np.add.at(table, (rows, cols), 1)
  v = cramers_v_bias_corrected(table)
  assert v is not None
  assert v < 0.03


def test_cramers_v_one_for_diagonal_table():
  table = np.eye(4, dtype=np.int64) * 100
  v = cramers_v_bias_corrected(table)
  assert v == pytest.approx(1.0, abs=1e-9)


def test_cramers_v_le_scipy_uncorrected_association():
  rng = np.random.default_rng(17)
  table = rng.integers(1, 50, size=(4, 6)).astype(np.int64)
  v = cramers_v_bias_corrected(table)
  v_scipy = contingency.association(table, method="cramer")
  assert v is not None
  assert v <= v_scipy + 1e-9


def test_cramers_v_none_for_degenerate_table():
  # After dropping the all-zero row/column, only a 1x1 table remains.
  assert cramers_v_bias_corrected(np.array([[1, 0], [0, 0]])) is None


def test_cramers_v_drops_all_zero_rows_and_columns():
  # A zero row/column padded around an otherwise-independent table must
  # not change the result versus the table with them removed.
  rng = np.random.default_rng(19)
  n = 4000
  rows = rng.integers(0, 3, size=n)
  cols = rng.integers(0, 3, size=n)
  core = np.zeros((3, 3), dtype=np.int64)
  np.add.at(core, (rows, cols), 1)
  padded = np.zeros((5, 4), dtype=np.int64)
  padded[1:4, 1:4] = core
  assert cramers_v_bias_corrected(padded) == pytest.approx(
      cramers_v_bias_corrected(core))


def test_cramers_v_raises_for_non_2d_table():
  with pytest.raises(ValueError, match="2-D"):
    cramers_v_bias_corrected(np.array([1, 2, 3]))


# ---------------------------------------------------------------------------
# nmi_min
# ---------------------------------------------------------------------------


def test_nmi_min_hand_computed_value():
  table = np.array([[3.0, 1.0], [1.0, 3.0]])
  n = table.sum()
  p = table / n
  p_row = p.sum(axis=1)
  p_col = p.sum(axis=0)
  h_x = -sum(pi * math.log(pi) for pi in p_row if pi > 0)
  h_y = -sum(pi * math.log(pi) for pi in p_col if pi > 0)
  mi = 0.0
  for i in range(2):
    for j in range(2):
      if p[i, j] > 0:
        mi += p[i, j] * math.log(p[i, j] / (p_row[i] * p_col[j]))
  expected = mi / min(h_x, h_y)

  result = nmi_min(table)
  assert result == pytest.approx(expected, rel=1e-9)


def test_nmi_min_is_one_for_perfect_diagonal_association():
  table = np.array([[10, 0], [0, 10]], dtype=np.float64)
  assert nmi_min(table) == pytest.approx(1.0, abs=1e-9)


def test_nmi_min_none_for_zero_mass():
  assert nmi_min(np.zeros((2, 2))) is None


def test_nmi_min_none_when_one_side_is_constant():
  # Every row lands in category 0 of X: H_X == 0.
  table = np.array([[5, 5], [0, 0]], dtype=np.float64)
  assert nmi_min(table) is None


def test_nmi_min_raises_for_non_2d_table():
  with pytest.raises(ValueError, match="2-D"):
    nmi_min(np.array([1.0, 2.0]))


# ---------------------------------------------------------------------------
# contingency_tvd
# ---------------------------------------------------------------------------


def test_contingency_tvd_zero_for_identical_tables():
  table = np.array([[3.0, 1.0], [1.0, 3.0]])
  assert contingency_tvd(table, table) == pytest.approx(0.0, abs=1e-12)


def test_contingency_tvd_one_for_disjoint_tables():
  t_src = np.array([[1.0, 0.0], [0.0, 0.0]])
  t_syn = np.array([[0.0, 0.0], [0.0, 1.0]])
  assert contingency_tvd(t_src, t_syn) == pytest.approx(1.0, abs=1e-12)


def test_contingency_tvd_shape_mismatch_raises():
  with pytest.raises(ValueError, match="shape"):
    contingency_tvd(np.zeros((2, 2)), np.zeros((2, 3)))


def test_contingency_tvd_none_for_zero_mass():
  assert contingency_tvd(np.zeros((2, 2)), np.array([[1.0, 0.0],
                                                     [0.0, 1.0]])) is None


# ---------------------------------------------------------------------------
# corr_rms_max
# ---------------------------------------------------------------------------


def test_corr_rms_max_basic():
  delta = np.array([0.1, -0.2, 0.3])
  rms, max_abs = corr_rms_max(delta)
  assert rms == pytest.approx(math.sqrt(np.mean(delta**2)))
  assert max_abs == pytest.approx(0.3)


def test_corr_rms_max_skips_nan():
  delta = np.array([0.1, np.nan, -0.5])
  rms, max_abs = corr_rms_max(delta)
  assert rms == pytest.approx(math.sqrt((0.1**2 + 0.5**2) / 2))
  assert max_abs == pytest.approx(0.5)


def test_corr_rms_max_none_when_no_finite_value():
  assert corr_rms_max(np.array([np.nan, np.nan])) == (None, None)


def test_corr_rms_max_none_for_empty_array():
  assert corr_rms_max(np.array([])) == (None, None)
