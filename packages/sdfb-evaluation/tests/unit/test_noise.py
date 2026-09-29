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
"""Tests for `sdfb_evaluation.stats.noise` (Task 5).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from sdfb_evaluation.stats import noise, relational


def test_dkw_10k_is_1_36_percent():
  assert math.isclose(noise.dkw_epsilon(10_000), 0.013581, rel_tol=1e-3)


def test_ks_critical_matches_asymptotic_table():
  # c(0.05)=1.358 -> n=m=1000: 1.358*sqrt(2/1000)=0.06073
  assert math.isclose(noise.ks_critical(1000, 1000), 0.06073, rel_tol=2e-3)


def test_ks_critical_empirical_type1_rate():
  rng = np.random.default_rng(7)
  hits = 0
  for _ in range(400):
    a, b = rng.normal(size=500), rng.normal(size=500)
    hits += stats.ks_2samp(a, b).statistic > noise.ks_critical(500, 500)
  assert 0.02 < hits / 400 < 0.09


def test_wilson_contains_p_and_is_bounded():
  lo, hi = noise.wilson_interval(0, 100)
  assert lo == 0.0 and 0.0 < hi < 0.05
  lo, hi = noise.wilson_interval(50, 100)
  assert lo < 0.5 < hi


def test_newcombe_zero_difference_straddles_zero():
  lo, hi = noise.newcombe_diff_interval(10, 100, 10, 100)
  assert lo < 0.0 < hi


@pytest.mark.slow
def test_newcombe_coverage_at_equal_proportions():
  # Monte-Carlo coverage sanity check: at the true difference 0 (p1=p2=0.1,
  # n=200), the interval should contain 0 at (at least close to) its nominal
  # 95% rate.
  rng = np.random.default_rng(13)
  n, p = 200, 0.1
  hits = 0
  for _ in range(500):
    k1, k2 = rng.binomial(n, p), rng.binomial(n, p)
    lo, hi = noise.newcombe_diff_interval(k1, n, k2, n)
    hits += lo <= 0.0 <= hi
  assert hits / 500 >= 0.93


def test_tvd_null_expectation_matches_simulation():
  rng = np.random.default_rng(3)
  p = np.array([0.5, 0.3, 0.2])
  sims = []
  for _ in range(2000):
    a = rng.multinomial(1000, p) / 1000
    b = rng.multinomial(1000, p) / 1000
    sims.append(0.5 * np.abs(a - b).sum())
  assert math.isclose(
      noise.tvd_null_expectation(p, 1000, 1000), np.mean(sims), rel_tol=0.1)


def test_jsd_null_expectation_matches_simulation():
  # k=5, equiprobable, n=m=2000: the same style of chance-alone-floor check
  # as the TVD test above, for the other null-gated shape metric.
  rng = np.random.default_rng(5)
  k = 5
  p = np.full(k, 1.0 / k)
  sims = []
  for _ in range(500):
    a = rng.multinomial(2000, p) / 2000
    b = rng.multinomial(2000, p) / 2000
    mix = 0.5 * (a + b)
    mask_a, mask_b = a > 0, b > 0
    kl_a = np.sum(a[mask_a] * np.log2(a[mask_a] / mix[mask_a]))
    kl_b = np.sum(b[mask_b] * np.log2(b[mask_b] / mix[mask_b]))
    sims.append(0.5 * kl_a + 0.5 * kl_b)
  assert math.isclose(
      noise.jsd_null_expectation_bits(k, 2000, 2000),
      np.mean(sims),
      rel_tol=0.2)


def test_mi_bias_nats_matches_formula():
  assert noise.mi_bias_nats(5, 5, 10_000) == 16 / 20_000


def test_rate_ratio_ci_straddles_one_at_equal_rates():
  ratio, lo, hi = noise.rate_ratio(20, 1000, 20, 1000)
  assert ratio == 1.0
  assert lo < 1.0 < hi


def test_rate_ratio_point_estimate_and_lower_bound():
  ratio, lo, hi = noise.rate_ratio(30, 1e4, 3, 1e4)
  assert math.isclose(ratio, 10.0, rel_tol=1e-9)
  assert lo > 2.5
  assert hi > ratio


def test_rate_ratio_degenerate_total_is_the_widest_interval():
  assert noise.rate_ratio(0, 10, 0, 10) == (None, 0.0, math.inf)


@pytest.mark.slow
def test_rate_ratio_monte_carlo_coverage_at_true_ratio_one():
  rng = np.random.default_rng(11)
  lam = 8.0
  hits = 0
  for _ in range(500):
    m1, m2 = rng.poisson(lam), rng.poisson(lam)
    _, lo, hi = noise.rate_ratio(m1, 1.0, m2, 1.0)
    hits += lo <= 1.0 <= hi
  assert hits / 500 >= 0.93


def test_degenerate_sizes_return_none():
  assert noise.noise_floor("ks_two_sample", n=0, m=10) is None
  assert noise.noise_floor(None) is None


def test_noise_floor_none_string_returns_none():
  assert noise.noise_floor("none", n=10, m=10) is None


@pytest.mark.parametrize("method",
                         ["wilson", "newcombe", "rate_ratio", "delong"])
def test_noise_floor_returns_none_for_interval_and_unimplemented_methods(
    method):
  assert noise.noise_floor(method, n=10, m=10, k=3, r=2, c=2) is None


def test_noise_floor_dispatches_ks_two_sample():
  assert noise.noise_floor(
      "ks_two_sample", n=1000, m=1000) == noise.ks_critical(1000, 1000)


def test_noise_floor_dispatches_tvd_null():
  p = [0.5, 0.3, 0.2]
  assert noise.noise_floor(
      "tvd_null", p=p, n=1000,
      m=1000) == noise.tvd_null_expectation(p, 1000, 1000)


def test_noise_floor_dispatches_jsd_null():
  assert noise.noise_floor(
      "jsd_null", k=5, n=2000,
      m=2000) == noise.jsd_null_expectation_bits(5, 2000, 2000)


def test_noise_floor_dispatches_fisher_z():
  assert noise.noise_floor(
      "fisher_z", n=100, m=100) == noise.fisher_z_delta_floor(100, 100)


def test_noise_floor_dispatches_mi_bias():
  assert noise.noise_floor(
      "mi_bias", r=5, c=5, n=10_000) == noise.mi_bias_nats(5, 5, 10_000)


def test_noise_floor_missing_kwarg_returns_none():
  assert noise.noise_floor("tvd_null", n=1000, m=1000) is None
  assert noise.noise_floor("jsd_null", n=1000, m=1000) is None
  assert noise.noise_floor("mi_bias", r=5, n=1000) is None


def test_noise_floor_fisher_z_needs_more_than_three_samples():
  assert noise.noise_floor("fisher_z", n=3, m=100) is None


def test_noise_floor_unknown_method_raises():
  with pytest.raises(ValueError, match="unknown noise_floor method"):
    noise.noise_floor("bootstrap", n=10, m=10)


@pytest.mark.parametrize(("lo", "hi", "folded"), [
    (-0.1, 0.3, (0.0, 0.3)),
    (0.2, 0.5, (0.2, 0.5)),
    (-0.5, -0.2, (0.2, 0.5)),
    (-0.4, 0.1, (0.0, 0.4)),
])
def test_folded_abs_interval(lo, hi, folded):
  assert noise.folded_abs_interval(lo, hi) == folded


def test_folded_abs_interval_is_still_importable_from_relational():
  assert relational._folded_abs_interval is noise.folded_abs_interval  # pylint: disable=protected-access  # the compatibility alias (R63)
