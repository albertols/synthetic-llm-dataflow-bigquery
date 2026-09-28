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
"""Tests for `sdfb_evaluation.stats.diversity` (Task 8).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy.stats import entropy as scipy_entropy

from sdfb_evaluation.stats import diversity as d
from sdfb_evaluation.stats import distances

# ---------------------------------------------------------------------------
# entropy_bits
# ---------------------------------------------------------------------------


def test_entropy_bits_zero_for_a_single_category():
  # All mass on one value: no uncertainty, entropy 0.
  n = 37
  clc = n * math.log(n)
  assert d.entropy_bits(n, clc) == pytest.approx(0.0, abs=1e-9)


def test_entropy_bits_log2k_for_a_uniform_distribution():
  k, per_value = 16, 5
  n = k * per_value
  clc = k * per_value * math.log(per_value)
  assert d.entropy_bits(n, clc) == pytest.approx(math.log2(k))


def test_entropy_bits_matches_scipy_on_counts():
  rng = np.random.default_rng(3)
  counts = rng.integers(1, 50, size=30)
  n = int(counts.sum())
  clc = float(sum(c * math.log(c) for c in counts))
  expected = scipy_entropy(counts, base=2)
  assert d.entropy_bits(n, clc) == pytest.approx(expected)


def test_entropy_bits_none_for_nonpositive_n():
  assert d.entropy_bits(0, 0.0) is None
  assert d.entropy_bits(-1, 0.0) is None


# ---------------------------------------------------------------------------
# miller_madow_bits
# ---------------------------------------------------------------------------


def test_miller_madow_bits_formula():
  h, k, n = 3.0, 10, 500
  expected = h + (k - 1) / (2.0 * n * math.log(2.0))
  assert d.miller_madow_bits(h, k, n) == pytest.approx(expected)


def test_miller_madow_bits_correction_shrinks_to_zero_as_n_grows():
  # The correction term is always positive (k >= 1) and vanishes as n -> inf.
  small_n_gap = d.miller_madow_bits(2.0, 5, 100) - 2.0
  large_n_gap = d.miller_madow_bits(2.0, 5, 1_000_000) - 2.0
  assert small_n_gap > large_n_gap > 0.0


# ---------------------------------------------------------------------------
# chao_shen_bits / chao_shen_term (Ruling R3)
# ---------------------------------------------------------------------------


def _chao_shen_bits_reference(counts: list[int]) -> float:
  """An independent, fully-vectorized re-implementation of Chao & Shen
  (2003), used only to cross-check `chao_shen_bits`/`chao_shen_term`
  against a second derivation of the same formula.
  """
  arr = np.array([c for c in counts if c > 0], dtype=float)
  n = float(arr.sum())
  f1 = float(np.sum(arr == 1.0))
  coverage = 1.0 - (f1 - 1.0) / n if f1 >= n else 1.0 - f1 / n
  p_tilde = coverage * arr / n
  terms = -p_tilde * np.log(p_tilde) / (1.0 - (1.0 - p_tilde)**n)
  return float(terms.sum() / math.log(2.0))


@pytest.mark.parametrize(
    "counts",
    [
        [1, 1, 2, 3, 5, 8, 13, 21],  # known frequency-of-frequencies: f1 = 2
        [5] * 20,  # no singletons: f1 = 0
        [1] * 10,  # every value a singleton: triggers the f1 == n guard
        [100, 1, 1, 1],
    ],
)
def test_chao_shen_bits_matches_reference_formula(counts):
  assert d.chao_shen_bits(counts) == pytest.approx(
      _chao_shen_bits_reference(counts))


def test_chao_shen_term_sums_to_chao_shen_bits():
  counts = [1, 1, 2, 3, 5, 8, 13, 21]
  n = sum(counts)
  f1 = sum(1 for c in counts if c == 1)
  coverage = 1.0 - f1 / n  # f1 != n here, no guard branch needed
  total_nats = sum(d.chao_shen_term(c, n, coverage) for c in counts)
  assert total_nats / math.log(2.0) == pytest.approx(d.chao_shen_bits(counts))


def test_chao_shen_bits_empty_is_none():
  assert d.chao_shen_bits([]) is None
  assert d.chao_shen_bits([0, 0]) is None


def test_chao_shen_term_degenerate_inputs_are_zero():
  assert d.chao_shen_term(0, 10, 0.9) == 0.0
  assert d.chao_shen_term(5, 0, 0.9) == 0.0
  assert d.chao_shen_term(5, 10, 0.0) == 0.0


# ---------------------------------------------------------------------------
# CensusAccumulator.add / merge — the rev-1 example
# ---------------------------------------------------------------------------


def _rev1_accumulator() -> d.CensusAccumulator:
  # source a x20, b x2; synthetic a x5, b x3, z x2 (all substantive).
  acc = d.CensusAccumulator()
  acc.add(1, 20, 5, 20, 5, substantive=True)  # a
  acc.add(2, 2, 3, 2, 3, substantive=True)  # b
  acc.add(3, 0, 2, 0, 2, substantive=True)  # z (synthetic-only)
  return acc


def test_rev1_example_coverage_novelty_substantive_copy_rate():
  res = d.summarize(_rev1_accumulator())
  assert res["coverage_mass"] == pytest.approx(1.0)
  assert res["novelty_mass"] == pytest.approx(0.2)
  assert res["substantive_copy_rate"] == pytest.approx(0.3)


def test_rev1_example_raw_accumulator_fields():
  acc = _rev1_accumulator()
  assert acc.n_src == 22
  assert acc.n_syn == 10
  assert acc.coverage_mass_num == 22  # both a (20) and b (2) are shared
  assert acc.novelty_syn == 2  # z is synthetic-only
  assert acc.copies_substantive == 3  # b: c_src=2 < 10, 3 synthetic rows
  assert acc.substantive_syn == 10


# ---------------------------------------------------------------------------
# CensusAccumulator.merge — purity, associativity, commutativity
# ---------------------------------------------------------------------------


def test_merge_is_pure():
  a, b = d.CensusAccumulator(), d.CensusAccumulator()
  a.add(1, 5, 5, 5, 5, substantive=True)
  b.add(2, 3, 3, 3, 3, substantive=True)
  a_before = (a.n_src, a.n_syn, tuple(a.top_src))
  b_before = (b.n_src, b.n_syn, tuple(b.top_src))
  merged = a.merge(b)
  assert (a.n_src, a.n_syn, tuple(a.top_src)) == a_before
  assert (b.n_src, b.n_syn, tuple(b.top_src)) == b_before
  assert merged.n_src == 8


def test_merge_with_empty_other_is_identity():
  a = d.CensusAccumulator()
  a.add(1, 5, 5, 5, 5, substantive=True)
  merged = a.merge(d.CensusAccumulator())
  assert merged.n_src == a.n_src
  assert merged.top_src == a.top_src


def _assert_accumulators_equal(a: d.CensusAccumulator, b: d.CensusAccumulator):
  assert a.n_src == b.n_src
  assert a.n_syn == b.n_syn
  assert a.n_src_m == b.n_src_m
  assert a.n_syn_m == b.n_syn_m
  assert a.k_src == b.k_src
  assert a.k_syn == b.k_syn
  assert a.k_src_m == b.k_src_m
  assert a.k_syn_m == b.k_syn_m
  assert a.k_both == b.k_both
  assert a.clc_src_m == pytest.approx(b.clc_src_m, rel=1e-9, abs=1e-9)
  assert a.clc_syn_m == pytest.approx(b.clc_syn_m, rel=1e-9, abs=1e-9)
  assert a.f1_src == b.f1_src
  assert a.f1_src_m == b.f1_src_m
  assert a.f1_syn_m == b.f1_syn_m
  assert a.coverage_mass_num == b.coverage_mass_num
  assert a.novelty_syn == b.novelty_syn
  assert a.copies_substantive == b.copies_substantive
  assert a.substantive_syn == b.substantive_syn
  assert a.top_src == b.top_src
  assert a.top_syn == b.top_syn


_entry_strategy = st.tuples(
    st.integers(0, 20),  # c_src
    st.integers(0, 20),  # c_syn
    st.integers(0, 20),  # c_src_m
    st.integers(0, 20),  # c_syn_m
    st.booleans(),  # substantive
)


@settings(max_examples=40)
@given(st.lists(_entry_strategy, min_size=1, max_size=25))
def test_merge_is_associative_and_commutative(entries):
  # Each entry gets a unique hash (its index), honouring the module
  # precondition that a value key is added exactly once.
  keyed = list(enumerate(entries))

  def build(pairs) -> d.CensusAccumulator:
    acc = d.CensusAccumulator()
    for h, (c_src, c_syn, c_src_m, c_syn_m, substantive) in pairs:
      acc.add(h, c_src, c_syn, c_src_m, c_syn_m, substantive=substantive)
    return acc

  single_pass = build(keyed)

  mid = len(keyed) // 2
  left, right = build(keyed[:mid]), build(keyed[mid:])
  split_then_merged = left.merge(right)

  reversed_pass = build(list(reversed(keyed)))

  _assert_accumulators_equal(single_pass, split_then_merged)
  _assert_accumulators_equal(single_pass, reversed_pass)
  # Commutativity of merge itself.
  _assert_accumulators_equal(left.merge(right), right.merge(left))


# ---------------------------------------------------------------------------
# top_src / top_syn — bound, tie-break, order-independence
# ---------------------------------------------------------------------------


def test_top_list_bounded_and_ties_broken_by_hash_ascending():
  acc = d.CensusAccumulator()
  # Fill to _TOP_K - 1 = 999 with strictly larger, unique counts.
  for i in range(999):
    acc.add(1000 + i, 2000 - i, 0, 0, 0, substantive=False)
  assert len(acc.top_src) == 999

  # Three ties at the boundary count (1000): only one slot is free, and the
  # tie-break must keep the SMALLEST hash among them, regardless of the
  # order they arrive in (a larger hash arriving first is evicted later by
  # a smaller one; a larger hash arriving after is rejected outright).
  acc.add(500, 1000, 0, 0, 0, substantive=False)  # hash 500, fills the slot
  acc.add(700, 1000, 0, 0, 0, substantive=False)  # hash 700 > 500, rejected
  acc.add(100, 1000, 0, 0, 0, substantive=False)  # hash 100 < 500, replaces it

  assert len(acc.top_src) == 1000
  assert acc.top_src[-1] == (1000, 100)
  assert (1000, 500) not in acc.top_src
  assert (1000, 700) not in acc.top_src
  assert acc.top_src[0] == (2000, 1000)  # the largest count leads


@settings(max_examples=25)
@given(
    st.lists(
        st.tuples(st.integers(1, 500), st.integers(0, 10_000)),
        min_size=2,
        max_size=30,
        unique_by=lambda item: item[1],  # unique hashes
    ).flatmap(lambda items: st.permutations(items).map(lambda perm:
                                                       (items, perm))))
def test_top_list_is_deterministic_regardless_of_add_order(pair):
  items, permuted = pair

  def build(order) -> list[tuple[int, int]]:
    acc = d.CensusAccumulator()
    for count, h in order:
      acc.add(h, count, 0, 0, 0, substantive=False)
    return acc.top_src

  assert build(items) == build(permuted)


# ---------------------------------------------------------------------------
# Good-Turing unseen mass (f1_src / n_src) — known frequency-of-frequencies
# ---------------------------------------------------------------------------


def test_good_turing_unseen_mass_from_known_frequency_of_frequencies():
  # Source: three singletons (A, B, C), one value at count 4 (D), one at
  # count 10 (E) -> n_src = 17, f1_src = 3.
  acc = d.CensusAccumulator()
  for h, c in [(1, 1), (2, 1), (3, 1), (4, 4), (5, 10)]:
    acc.add(h, c, c, c, c, substantive=True)
  res = d.summarize(acc)
  assert acc.f1_src == 3
  assert acc.n_src == 17
  assert res["good_turing_unseen_src"] == pytest.approx(3 / 17)


# ---------------------------------------------------------------------------
# Degenerate: an all-NULL column (no values ever added)
# ---------------------------------------------------------------------------


def test_summarize_all_null_column_is_all_none():
  res = d.summarize(d.CensusAccumulator())
  assert res["entropy_src_m_bits"] is None
  assert res["entropy_syn_m_bits"] is None
  assert res["entropy_ratio"] is None
  assert res["distinct_ratio"] is None
  assert res["coverage_mass"] is None
  assert res["novelty_mass"] is None
  assert res["good_turing_unseen_src"] is None
  assert res["top1_share_delta"] is None
  assert res["substantive_copy_rate"] is None
  assert res["chao_shen_coverage_src_m"] is None
  assert not res["top_src"]
  assert not res["top_syn"]


# ---------------------------------------------------------------------------
# Entropy ratio at matched n vs the naive full-n ratio (n-dependence bias)
# ---------------------------------------------------------------------------


def test_entropy_ratio_is_one_for_same_distribution_different_n():
  # Source (10k draws) and synthetic (900k draws) come from the IDENTICAL
  # 5,000-value Zipf(1.1) distribution. At matched n the entropy ratio must
  # be close to 1; the naive full-n ratio (900k vs 10k, no matching) is
  # biased upward by the sample-size-dependent plug-in entropy bias — this
  # documents exactly the effect D5's "n-dependent metrics use matched n"
  # rule exists to cancel.
  vocab = 5000
  ranks = np.arange(1, vocab + 1, dtype=float)
  p = ranks**(-1.1)
  p /= p.sum()

  n_src, n_syn = 10_000, 900_000
  rng = np.random.default_rng(91)
  src_draws = rng.choice(vocab, size=n_src, p=p)
  syn_draws = rng.choice(vocab, size=n_syn, p=p)

  m = min(n_src, n_syn)
  thinned = rng.random(n_syn) < (m / n_syn)  # Bernoulli(m / n_syn) thinning

  c_src = np.bincount(src_draws, minlength=vocab)
  c_syn = np.bincount(syn_draws, minlength=vocab)
  c_syn_m = np.bincount(syn_draws[thinned], minlength=vocab)
  c_src_m = c_src  # m == n_src: every source row is already matched

  acc = d.CensusAccumulator()
  for v in range(vocab):
    acc.add(
        v,
        int(c_src[v]),
        int(c_syn[v]),
        int(c_src_m[v]),
        int(c_syn_m[v]),
        substantive=True)
  matched_ratio = d.summarize(acc)["entropy_ratio"]
  assert matched_ratio == pytest.approx(1.0, abs=0.02)

  def clc(counts: np.ndarray) -> float:
    return float(sum(c * math.log(c) for c in counts if c > 0))

  naive_src = d.entropy_bits(int(c_src.sum()), clc(c_src))
  naive_syn = d.entropy_bits(int(c_syn.sum()), clc(c_syn))
  naive_ratio = naive_syn / naive_src
  assert naive_ratio > 1.05


# ---------------------------------------------------------------------------
# tvd_jsd_from_census — second-pass agreement with stats.distances
# ---------------------------------------------------------------------------


def test_tvd_jsd_from_census_matches_distances_module():
  src = {"a": 20, "b": 5}
  syn = {"a": 10, "b": 5, "c": 15}
  _, p, q = distances.align(src, syn)
  n_src, n_syn = int(p.sum()), int(q.sum())
  pairs = list(zip(p.astype(int).tolist(), q.astype(int).tolist(), strict=True))

  tvd_value, jsd_value = d.tvd_jsd_from_census(pairs, n_src, n_syn)
  assert tvd_value == pytest.approx(distances.tvd(p, q))
  assert jsd_value == pytest.approx(distances.jsd_bits(p, q))


def test_tvd_jsd_from_census_degenerate_is_zero_zero():
  assert d.tvd_jsd_from_census([], 0, 0) == (0.0, 0.0)
  assert d.tvd_jsd_from_census([(1, 1)], 0, 5) == (0.0, 0.0)
