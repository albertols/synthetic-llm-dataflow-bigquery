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
"""Sampling-noise floors, proportion/difference intervals, and the
conditional rate-ratio test that Task 14's scorer gates a metric's `value`
against before calling a threshold crossing a real regression: "a metric
fails only if `value` crosses the fail threshold *and* exceeds its noise
floor" (D5).

Each function is one estimator of how much wobble chance alone puts on a
statistic at a given sample size, named for the catalogue's `noise_floor`
vocabulary (`src/sdfb_evaluation/catalogue/metrics.yaml`, `noise_floor:`
column). `noise_floor` is the dispatcher Task 14 calls with that vocabulary
directly. Three of its methods return an interval (`ci_low`/`ci_high`), not
a scalar floor — `wilson`, `newcombe` and `rate_ratio` — so their own metric
producers call `wilson_interval`/`newcombe_diff_interval`/`rate_ratio`
directly and the dispatcher returns `None` for them; `delong` (the AUC
noise floor) is implemented in `stats/detection.py` (a later task), and the
dispatcher returns `None` for it here too, pending that.
`folded_abs_interval` turns a signed difference interval into one for the
catalogue's unsigned deltas.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from scipy.stats import beta

# noise_floor methods that return an interval rather than a scalar floor
# (wilson, newcombe, rate_ratio) or that are not implemented in this module
# (delong, deferred to stats/detection.py) — the dispatcher returns None for
# all of these; see the module docstring.
_NO_SCALAR_FLOOR = frozenset({"wilson", "newcombe", "rate_ratio", "delong"})

# Fisher's z-transform standard error 1/sqrt(n-3) needs n > 3 (n=3 would
# divide by zero, n<3 would give a negative variance).
_MIN_FISHER_N = 3


def dkw_epsilon(n: int, alpha: float = 0.05) -> float:
  """The Dvoretzky-Kiefer-Wolfowitz band half-width for `n` samples.

  `sqrt(ln(2 / alpha) / (2n))` bounds, with probability `1 - alpha`, the
  max deviation between an empirical CDF from `n` draws and the true CDF —
  using the tight constant from Massart (1990). This is the one-sample
  noise floor a distribution-shape metric can never beat.
  """
  return math.sqrt(math.log(2.0 / alpha) / (2.0 * n))


def ks_critical(n: int, m: int, alpha: float = 0.05) -> float:
  """The asymptotic two-sample KS critical value at sizes `n`, `m`.

  `sqrt(-ln(alpha / 2) / 2) * sqrt((n + m) / (n * m))`: under the null that
  both samples come from the same distribution, the two-sample KS statistic
  (Smirnov, 1948) exceeds this value with probability `alpha`. This is the
  noise floor for `column.ks` and any other KS-gated metric.
  """
  return math.sqrt(-math.log(alpha / 2.0) / 2.0) * math.sqrt((n + m) / (n * m))


def wilson_interval(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
  """The Wilson score interval for a binomial proportion `k / n` (Wilson, 1927).

  Bounded to `[0, 1]` by construction, unlike the Wald interval, which can
  cross either bound near `p = 0` or `p = 1`. `k = 0` and `k = n` return the
  exact bounds `0.0`/`1.0` (rather than a value merely close to them, which
  the general formula's floating-point evaluation would not guarantee), so
  a fully degenerate count reads as a fully degenerate interval.
  """
  if n <= 0:
    return (0.0, 1.0)
  phat = k / n
  denom = 1.0 + z * z / n
  center = (phat + z * z / (2.0 * n)) / denom
  margin = (z / denom) * math.sqrt(phat * (1.0 - phat) / n + z * z /
                                   (4.0 * n * n))
  lo = 0.0 if k <= 0 else max(0.0, center - margin)
  hi = 1.0 if k >= n else min(1.0, center + margin)
  return (lo, hi)


def newcombe_diff_interval(
    k1: int,
    n1: int,
    k2: int,
    n2: int,
    z: float = 1.959964,
) -> tuple[float, float]:
  """The interval for `p1 - p2` from two independent proportions.

  This is Newcombe's (1998) "method 10": each side's own `wilson_interval`
  gives the score-interval slack around its point estimate, and the two
  slacks combine in quadrature — the lower bound moves `p1 - p2` down by
  `p1`'s lower slack and `p2`'s upper slack, the upper bound up by `p1`'s
  upper slack and `p2`'s lower slack. This keeps Wilson's good small-`n` and
  boundary behaviour for the two-sample difference.
  """
  p1 = k1 / n1 if n1 > 0 else 0.0
  p2 = k2 / n2 if n2 > 0 else 0.0
  lo1, hi1 = wilson_interval(k1, n1, z)
  lo2, hi2 = wilson_interval(k2, n2, z)
  diff = p1 - p2
  lo = diff - math.sqrt((p1 - lo1)**2 + (hi2 - p2)**2)
  hi = diff + math.sqrt((hi1 - p1)**2 + (p2 - lo2)**2)
  return (lo, hi)


def folded_abs_interval(lo: float, hi: float) -> tuple[float, float]:
  """The interval for `|X|` implied by a two-sided interval `(lo, hi)` for a
  signed quantity `X` that the interval always contains.

  If `(lo, hi)` straddles 0, `X` could plausibly be 0 itself, so `|X|`
  ranges from 0 up to `max(|lo|, |hi|)`. Otherwise both endpoints share
  `X`'s sign and `|X|` is monotone in `X` there, so the folded interval is
  `(min(|lo|, |hi|), max(|lo|, |hi|))`. Turns Newcombe's (1998) signed
  interval for a difference of shares into a CI for the catalogue's
  unsigned deltas (`*_rate_delta`, `zero_child_share_delta`).
  """
  if lo <= 0.0 <= hi:
    return (0.0, max(abs(lo), abs(hi)))
  return (min(abs(lo), abs(hi)), max(abs(lo), abs(hi)))


def stratified_ratio_interval(
    num: float,
    den: float,
    tail_yy: float,
    tail_xy: float,
    tail_xx: float,
    rate: float,
    sampled_den: float,
    alpha: float = 0.05) -> tuple[float, float, float] | None:
  """A share `R = Y / X` from a stratified value sample, with its
  cluster-robust interval: `(ratio, lo, hi)`, or None when `den <= 0`.

  Design: the VALUES are the sampling units (every row of a value moves
  with it, so rows are clustered). A certainty stratum enters with weight
  1 and no sampling variance; the tail is Poisson-sampled, each value
  independently with probability `rate` (weight 1 / rate). `num` and
  `den` are the weighted totals Ŷ = Σ_head y + Σ_tail,sampled y / rate
  and X̂ likewise; `tail_yy`, `tail_xy`, `tail_xx` are the plain sums
  Σ y², Σ x y, Σ x² over the SAMPLED tail values, and `sampled_den` the
  plain Σ x over every counted value (the rows actually observed). With
  the Taylor-linearised residuals e_v = y_v - R̂ x_v of R̂ = Ŷ / X̂ and the
  Horvitz-Thompson variance estimator of Poisson sampling,
  Σ_sampled (1 - π) e_v² / π² with π = rate:

      V̂(R̂) = ((1 - rate) / rate) · (Σ_sampled e_v² / rate) / X̂²
            = (1 - rate) / rate² · (Σy² - 2 R̂ Σxy + R̂² Σx²) / X̂²

  — the (1 - rate) / rate finite-population factor times the
  Horvitz-Thompson estimate of the population's residual sum of squares
  (Horvitz & Thompson, 1952; Woodruff, 1971, for the linearisation;
  Särndal, Swensson & Wretman, 1992). A normal interval on V̂ collapses
  to a point when the sample holds no event (a rare copy rate), so the
  interval is Korn & Graubard's (1998): Clopper-Pearson on the effective
  sample size n* = R̂ (1 - R̂) / V̂, capped at the observed rows (a design
  effect never below 1), with R̂ n* events; n* is the observed rows when
  R̂ is 0 or 1 or V̂ is 0. A two-sided `alpha`.
  """
  if den <= 0.0:
    return None
  ratio = min(1.0, max(0.0, num / den))
  squares = max(0.0, tail_yy - 2.0 * ratio * tail_xy + ratio * ratio * tail_xx)
  variance = (1.0 - rate) / (rate * rate) * squares / (den * den)
  observed = max(float(sampled_den), 1.0)
  n_eff = observed
  if 0.0 < ratio < 1.0 and variance > 0.0:
    n_eff = min(observed, ratio * (1.0 - ratio) / variance)
  events = ratio * n_eff
  lo = (
      float(beta.ppf(alpha / 2.0, events, n_eff - events +
                     1.0)) if events > 0.0 else 0.0)
  hi = (
      float(beta.ppf(1.0 - alpha / 2.0, events + 1.0, n_eff -
                     events)) if events < n_eff else 1.0)
  return (ratio, lo, hi)


def tvd_null_expectation(p: Sequence[float], n: int, m: int) -> float:
  """The expected TVD between two same-`p` multinomial samples of sizes `n`, `m`.

  Per category `i`, the sampled-proportion difference `a_i - b_i` is
  approximately `Normal(0, p_i(1-p_i)(1/n+1/m))` by the CLT; a zero-mean
  normal has `E|X| = sigma * sqrt(2/pi)`. Summing those half-absolute-
  differences (`0.5 * sum_i |a_i - b_i|` is exactly the TVD) gives
  `0.5 * sum_i sqrt(2 p_i(1-p_i)(1/n+1/m) / pi)` — the chance-alone TVD
  floor for `column.tvd`/`column.dow_tvd`/etc. even when both samples come
  from the identical distribution `p`.
  """
  return 0.5 * sum(
      math.sqrt(2.0 * p_i * (1.0 - p_i) * (1.0 / n + 1.0 / m) / math.pi)
      for p_i in p)


def jsd_null_expectation_bits(k: int, n: int, m: int) -> float:
  """The expected JSD (bits) between two same-distribution samples over `k` categories.

  `(k - 1)(1/n + 1/m) / (8 ln 2)`: the same large-sample chi-squared
  expansion that gives `mi_bias_nats` its `(rows-1)(cols-1)/(2n)` bias term
  (Treves & Panzeri, 1995) applies to JSD's pair of KL terms against the
  sample mixture; halving for JSD's own factor of one-half and converting
  from nats to bits (divide by `ln 2`) gives this. It is the chance-alone
  JSD floor for `column.jsd`.
  """
  return (k - 1) * (1.0 / n + 1.0 / m) / (8.0 * math.log(2.0))


def fisher_z_delta_floor(n: int, m: int, z: float = 1.959964) -> float:
  """The noise floor for a Pearson-correlation delta, via Fisher's z-transform.

  Fisher's (1915) variance-stabilizing transform `z_r = artanh(r)` has
  standard error `1/sqrt(n-3)`, independent of `r`; for two independent
  samples the transformed difference has standard error
  `sqrt(1/(n-3) + 1/(m-3))`. Near `r = 0` (where `dz/dr ~= 1`) that is also,
  to first order, the standard error of the *untransformed* correlation
  delta — the reference scale a measured `|delta r|` is compared against,
  since no measured `r` is passed in.
  """
  return z * math.sqrt(1.0 / (n - 3) + 1.0 / (m - 3))


def mi_bias_nats(r: int, c: int, n: int) -> float:
  """The large-sample bias (nats) of a plug-in MI estimate on an `r x c` table of `n` samples.

  `(r - 1)(c - 1) / (2n)` (Treves & Panzeri, 1995): even under
  independence, a plug-in mutual-information estimate from a finite
  contingency table is biased upward by this much, so it is the noise
  floor `pair.nmi_delta`/`pair.cramers_v_delta` compare against.
  """
  return (r - 1) * (c - 1) / (2.0 * n)


def rate_ratio(
    m1: int,
    t1: float,
    m2: int,
    t2: float,
    alpha: float = 0.05,
) -> tuple[float | None, float, float]:
  """The conditional rate-ratio test and its Clopper-Pearson interval.

  Conditional on the total count `m1 + m2`, and under a null of homogeneous
  Poisson rates over exposures `t1`, `t2`, `m1 | (m1 + m2)` is
  `Binomial(m1 + m2, t1 / (t1 + t2))` (Przyborowski & Wilenski, 1940). A
  Clopper-Pearson interval (Clopper & Pearson, 1934) on that binomial
  proportion `prob` maps onto the rate ratio `theta = rate1 / rate2` via
  `theta = (prob / (1 - prob)) * (t2 / t1)` — solving `prob = theta t1 /
  (theta t1 + t2)` for `theta` — which also recovers the plain point
  estimate `(m1 * t2) / (m2 * t1)` at `prob = m1 / (m1 + m2)`.

  Returns `(ratio, lo, hi)`. `ratio` is `None` when `m1 + m2 == 0` — no
  events on either side, so no ratio can be estimated — in which case
  `(lo, hi)` is the widest possible interval, `(0.0, inf)`, consistent with
  having no information to rule any ratio out.
  """
  total = m1 + m2

  def _to_ratio(prob: float) -> float:
    if prob <= 0.0:
      return 0.0
    if prob >= 1.0:
      return math.inf
    return (prob / (1.0 - prob)) * (t2 / t1)

  if total <= 0:
    return (None, 0.0, math.inf)
  p_hat = m1 / total
  p_lo = beta.ppf(alpha / 2.0, m1, total - m1 + 1) if m1 > 0 else 0.0
  p_hi = beta.ppf(1.0 - alpha / 2.0, m1 + 1, total - m1) if m1 < total else 1.0
  return (_to_ratio(p_hat), _to_ratio(p_lo), _to_ratio(p_hi))


def _as_positive_int(value: Any) -> int | None:
  """`value` as an `int`, or `None` if it is missing, non-numeric or `<= 0`.

  Narrows `noise_floor`'s `Any`-typed `**kw` values to a concrete `int |
  None` up front, so every branch below gets ordinary `mypy`-checked `int`
  arithmetic instead of re-deriving it from `Any` each time. `bool` is
  excluded even though it is technically an `int` subtype, since a boolean
  can never be a meaningful sample size or category count.
  """
  if value is None or isinstance(value, bool):
    return None
  try:
    as_int = int(value)
  except (TypeError, ValueError):
    return None
  return as_int if as_int > 0 else None


def noise_floor(  # noqa: PLR0911 — method dispatch, clearer flat than nested
    method: str | None, **kw: Any) -> float | None:
  """The scalar noise floor for `method`, from whichever of `kw`'s sizes it needs (D5).

  `method` is one of the catalogue's `noise_floor` vocabulary
  (`ks_two_sample | wilson | newcombe | tvd_null | jsd_null | fisher_z |
  mi_bias | rate_ratio | delong | none`); `None`/`"none"` and the interval-
  producing/not-yet-implemented methods (`wilson`, `newcombe`,
  `rate_ratio`, `delong`) all return `None` — see the module docstring.
  Recognised kwargs: `n`, `m` (sample sizes), `p` (probabilities, for
  `tvd_null`), `k` (categories, for `jsd_null`), `r`, `c` (table
  dimensions, for `mi_bias`).

  Returns `None` whenever a size the method needs is missing or `<= 0` (a
  degenerate or absent sample can carry no floor), rather than raising.
  Raises `ValueError` for a `method` outside the catalogue's vocabulary.
  """
  if method is None or method == "none" or method in _NO_SCALAR_FLOOR:
    return None
  n, m = _as_positive_int(kw.get("n")), _as_positive_int(kw.get("m"))
  if method == "ks_two_sample":
    if n is None or m is None:
      return None
    return ks_critical(n, m)
  if method == "tvd_null":
    p = kw.get("p")
    if p is None or n is None or m is None:
      return None
    return tvd_null_expectation(p, n, m)
  if method == "jsd_null":
    k = _as_positive_int(kw.get("k"))
    if k is None or n is None or m is None:
      return None
    return jsd_null_expectation_bits(k, n, m)
  if method == "fisher_z":
    if n is None or n <= _MIN_FISHER_N or m is None or m <= _MIN_FISHER_N:
      return None
    return fisher_z_delta_floor(n, m)
  if method == "mi_bias":
    r, c = _as_positive_int(kw.get("r")), _as_positive_int(kw.get("c"))
    if r is None or c is None or n is None:
      return None
    return mi_bias_nats(r, c, n)
  raise ValueError(f"unknown noise_floor method: {method!r}")
