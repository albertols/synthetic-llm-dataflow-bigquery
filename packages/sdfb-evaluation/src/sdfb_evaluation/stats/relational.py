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
"""Parent/child relational maths: the pure functions behind every
`relationship.*` entry in `catalogue/metrics.yaml` — fan-out shape (TVD,
W1, mean ratio), childless-parent share, cardinality adherence, parent
coverage, and the foreign-key orphan rate.

A parent-driven launch generates every child from its parent's landed keys
(ADR 0036/0037), so these metrics compare the resulting children-per-parent
distribution against the source's, not individual rows. `fanout_histogram`
turns one count per parent into a fixed-width histogram; `fanout_metrics`
turns a pair of those (plus the exact means/extremes a capped histogram
alone cannot recover) into every `relationship.fanout_*`/
`relationship.parent_coverage` value. `orphan_summary` is the SQL MATCH
SIMPLE-aware rate/denominator shared by `relationship.orphan_rate` (on the
synthetic table) and `relationship.orphan_rate_source` (the baseline).

Design: docs/designs/2026-07-07-evaluation-framework-design.md

References:
  Patki, N., Wedge, R., Veeramachaneni, K. (2016), "The Synthetic Data
    Vault" (parent-child cardinality as a generation target).
  Ramdas, A., Garcia Trillos, N., Cuturi, M. (2017), "On Wasserstein
    Two-Sample Testing and Related Families of Nonparametric Tests" (the
    CDF-integral identity for W1 that `_atoms_w1` applies to fan-out atoms).
  Newcombe, R. (1998), "Interval Estimation for the Difference Between
    Independent Proportions: Comparison of Eleven Methods" (the signed
    interval `_folded_abs_interval` folds into an unsigned one).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

from sdfb_evaluation.stats import distances
from sdfb_evaluation.stats.noise import newcombe_diff_interval


def fanout_histogram(children_per_parent: Iterable[int],
                     cap: int = 50) -> np.ndarray:
  """The fan-out histogram over ALL parents, zero-child ones included:
  `cap + 1` bins, one per exact count `0 .. cap - 1` and one open-ended
  `>= cap` bin for everything at or beyond the cap.

  `children_per_parent` is one count per parent row — every parent in the
  table, not just the ones with at least one child — or the zero-child bin
  (which `fanout_metrics` reads as `z_src`/`z_syn`) would be empty by
  construction instead of measuring anything. An empty `children_per_parent`
  returns an all-zero histogram rather than raising.
  """
  values = np.fromiter(children_per_parent, dtype=np.int64)
  if values.size == 0:
    return np.zeros(cap + 1, dtype=np.int64)
  clipped = np.minimum(values, cap)
  return np.bincount(clipped, minlength=cap + 1)[:cap + 1]


def _fanout_atoms(
    counts: np.ndarray,
    total: float,
    cap: int,
    overflow_mean: float | None,
) -> dict[float, float]:
  """One side's fan-out histogram as point masses, `{location: probability}`.

  Bins `0 .. cap - 1` sit at their own count as an exact atom (a fan-out of
  exactly `c` children IS the value `c`). The open-ended `>= cap` bin has no
  single exactly-known value, so it is placed at `overflow_mean` — that
  side's own observed mean fan-out among its `>= cap` parents, when the
  caller has it — or, failing that, at `cap` itself (the same convention
  the catalogue's `fanout_w1` formula assumes implicitly by treating
  `>= cap` as exactly `cap`).
  """
  centre = float(overflow_mean) if overflow_mean is not None else float(cap)
  atoms: dict[float, float] = {float(c): counts[c] / total for c in range(cap)}
  atoms[centre] = atoms.get(centre, 0.0) + counts[cap] / total
  return atoms


def _atoms_w1(atoms_src: dict[float, float], atoms_syn: dict[float,
                                                             float]) -> float:
  """The exact Wasserstein-1 distance between two finite point-mass
  distributions at arbitrary, possibly non-matching, locations.

  `W1(P, Q) = int |F_P(x) - F_Q(x)| dx`. Both CDFs are step functions,
  constant between consecutive points of the sorted union of the two
  supports `x_1 < ... < x_m`, so the integral is exact and finite:
  `sum_i (x_{i+1} - x_i) * |F_P(x_i) - F_Q(x_i)|` (Ramdas, Garcia Trillos &
  Cuturi, 2017, the CDF-integral identity for W1 — applied here to atoms
  rather than a quantile-grid's edges, so nothing is truncated: every
  fan-out atom's location is exactly known, unlike
  `stats.binned.w1_from_bins`'s unbounded outer tail). Beyond `x_m` both
  CDFs equal 1 and contribute nothing, so no explicit tail term is needed.
  """
  locations = sorted(set(atoms_src) | set(atoms_syn))
  p = np.array([atoms_src.get(x, 0.0) for x in locations])
  q = np.array([atoms_syn.get(x, 0.0) for x in locations])
  f_p = np.cumsum(p)[:-1]
  f_q = np.cumsum(q)[:-1]
  widths = np.diff(np.array(locations, dtype=float))
  return float(np.sum(np.abs(f_p - f_q) * widths))


def _folded_abs_interval(lo: float, hi: float) -> tuple[float, float]:
  """The interval for `|X|` implied by a two-sided interval `(lo, hi)` for a
  signed quantity `X` that the interval always contains.

  If `(lo, hi)` straddles 0, `X` could plausibly be 0 itself, so `|X|`
  ranges from 0 up to `max(|lo|, |hi|)`. Otherwise both endpoints share
  `X`'s sign and `|X|` is monotone in `X` there, so the folded interval is
  `(min(|lo|, |hi|), max(|lo|, |hi|))`. Used to turn Newcombe's (1998)
  signed interval for `z_syn - z_src` into a CI for the catalogued unsigned
  `zero_child_share_delta`.
  """
  if lo <= 0.0 <= hi:
    return (0.0, max(abs(lo), abs(hi)))
  return (min(abs(lo), abs(hi)), max(abs(lo), abs(hi)))


def fanout_metrics(
    h_src: Sequence[float],
    h_syn: Sequence[float],
    mean_src: float,
    mean_syn: float,
    min_src: int,
    max_src: int,
    *,
    cap: int = 50,
    mean_overflow_src: float | None = None,
    mean_overflow_syn: float | None = None,
) -> dict[str, Any] | None:
  """Every `relationship.fanout_*`/`relationship.parent_coverage` value,
  from one pair of `fanout_histogram(..., cap=cap)` outputs.

  `h_src`/`h_syn` carry the shape; `mean_src`/`mean_syn` (the exact,
  uncapped means) and `min_src`/`max_src` (the exact, uncapped source
  extremes) are passed in separately because none of the three is
  recoverable from a capped histogram alone. `mean_overflow_src`/
  `mean_overflow_syn` are each side's exact mean fan-out among its own
  `>= cap` parents, when known; see `_fanout_atoms` for what happens when
  they are omitted.

  Returns `None` when either side has no parents at all (`sum(h_src) == 0`
  or `sum(h_syn) == 0`) — every metric below needs at least one parent on
  each side to be defined.

  Returns a dict:
    - `tvd`: `relationship.fanout_tvd` — total variation between the raw
      histograms (`stats.distances.tvd`).
    - `w1`: `relationship.fanout_w1` (informational) — Wasserstein-1 in
      children per parent, on bin centres (`_atoms_w1`).
    - `mean_ratio`: `relationship.fanout_mean_ratio`, `mean_syn / mean_src`;
      `None` when `mean_src` is 0 (an undefined ratio, not a 0 or inf one).
    - `zero_child_share_source`/`zero_child_share_synthetic`: `h_src[0] /
      sum(h_src)` and its synthetic twin.
    - `zero_child_share_delta`: `relationship.zero_child_share_delta`, plus
      `zero_child_share_delta_ci_low`/`_ci_high` (`_folded_abs_interval`
      over `newcombe_diff_interval`).
    - `cardinality_adherence`: `relationship.cardinality_adherence` — the
      share of synthetic parents with a fan-out in `[min_src, max_src]`.
      Exact when `max_src < cap` (every count that matters is an exact
      histogram bin). When `max_src >= cap`, the whole `>= cap` bin is
      approximated as inside the range (a synthetic parent counted there
      could in truth exceed `max_src`, but the histogram cannot tell) —
      `cardinality_adherence_exact` records which case applied.
    - `parent_coverage`: `relationship.parent_coverage`, `Pr_syn[c >= 1] /
      Pr_src[c >= 1]`; `None` when the source has no parent with a child
      (an undefined ratio).

  Raises `ValueError` if `h_src`/`h_syn` are not both exactly `cap + 1`
  long — they must come from `fanout_histogram(..., cap=cap)`.
  """
  h_src_arr = np.asarray(h_src, dtype=np.float64)
  h_syn_arr = np.asarray(h_syn, dtype=np.float64)
  if h_src_arr.shape != (cap + 1,) or h_syn_arr.shape != (cap + 1,):
    raise ValueError(
        f"h_src and h_syn must each have cap + 1 = {cap + 1} bins, got "
        f"shapes {h_src_arr.shape} and {h_syn_arr.shape}")
  n_src = float(h_src_arr.sum())
  n_syn = float(h_syn_arr.sum())
  if n_src <= 0.0 or n_syn <= 0.0:
    return None

  # distances.tvd takes Sequence[float]; .tolist() also satisfies mypy,
  # which does not treat ndarray as a Sequence[float].
  tvd_value = distances.tvd(h_src_arr.tolist(), h_syn_arr.tolist())
  # n_src, n_syn > 0 above guarantee stats.distances.tvd finds nonzero mass
  # on both sides, so this is never actually None; the fallback just keeps
  # the return type a plain float for the dict below.
  tvd_value = 0.0 if tvd_value is None else tvd_value

  atoms_src = _fanout_atoms(h_src_arr, n_src, cap, mean_overflow_src)
  atoms_syn = _fanout_atoms(h_syn_arr, n_syn, cap, mean_overflow_syn)
  w1_value = _atoms_w1(atoms_src, atoms_syn)

  mean_ratio = mean_syn / mean_src if mean_src > 0.0 else None

  z_src = float(h_src_arr[0] / n_src)
  z_syn = float(h_syn_arr[0] / n_syn)
  zero_delta = abs(z_syn - z_src)
  ci_lo, ci_hi = newcombe_diff_interval(
      int(h_syn_arr[0]), int(n_syn), int(h_src_arr[0]), int(n_src))
  ci_lo, ci_hi = _folded_abs_interval(ci_lo, ci_hi)

  exact = max_src < cap
  lo_idx = max(0, min_src)
  if exact:
    hi_idx = min(max_src, cap - 1)
    adherent = float(h_syn_arr[lo_idx:hi_idx +
                               1].sum()) if hi_idx >= lo_idx else 0.0
  else:
    adherent = float(h_syn_arr[lo_idx:cap].sum()) + float(h_syn_arr[cap])
  adherence = adherent / n_syn

  src_share_with_child = 1.0 - z_src
  syn_share_with_child = 1.0 - z_syn
  parent_coverage = (
      syn_share_with_child /
      src_share_with_child if src_share_with_child > 0.0 else None)

  return {
      "tvd": tvd_value,
      "w1": w1_value,
      "mean_ratio": mean_ratio,
      "zero_child_share_source": z_src,
      "zero_child_share_synthetic": z_syn,
      "zero_child_share_delta": zero_delta,
      "zero_child_share_delta_ci_low": ci_lo,
      "zero_child_share_delta_ci_high": ci_hi,
      "cardinality_adherence": adherence,
      "cardinality_adherence_exact": exact,
      "parent_coverage": parent_coverage,
  }


def orphan_summary(total_nonnull: int, orphans: int,
                   null_keys: int) -> dict[str, Any]:
  """`relationship.orphan_rate`/`relationship.orphan_rate_source`'s rate and
  denominator, under SQL MATCH SIMPLE semantics: a child key tuple with any
  NULL part is neither an orphan nor part of `total_nonnull` — it is
  reported separately as `null_keys`, which never enters the rate.

  `total_nonnull` is the count of child key tuples with every part
  non-null; `orphans` is the subset of those with no matching parent row.
  `rate = orphans / total_nonnull`, or `None` when `total_nonnull` is 0 (no
  non-null tuples to measure — not the same as a rate of 0).

  Raises `ValueError` if any argument is negative, or if `orphans` exceeds
  `total_nonnull` (both are caller invariants, never data conditions).
  """
  if total_nonnull < 0 or orphans < 0 or null_keys < 0:
    raise ValueError(
        "total_nonnull, orphans and null_keys must all be non-negative: "
        f"got {total_nonnull}, {orphans}, {null_keys}")
  if orphans > total_nonnull:
    raise ValueError(
        f"orphans ({orphans}) cannot exceed total_nonnull ({total_nonnull})")
  rate = orphans / total_nonnull if total_nonnull > 0 else None
  return {
      "total_nonnull": total_nonnull,
      "orphans": orphans,
      "null_keys": null_keys,
      "rate": rate,
  }
