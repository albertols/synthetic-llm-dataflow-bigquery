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
"""Regenerate the figures of the evaluation design document.

    uv run --no-sync python3 scripts/doc/make_eval_figures.py
    uv run --no-sync python3 scripts/doc/make_eval_figures.py --only eval-fanout

Writes PNGs into docs/designs/assets/ for
docs/designs/2026-07-07-evaluation-framework-design.md. numpy and matplotlib
only: the root environment carries no scipy, so the few distribution
functions the figures need (Clopper-Pearson, DeLong, the hypergeometric
rarefaction) are written out below. Nothing is imported from
`packages/sdfb-evaluation`; each figure names the code it illustrates, and
the warn and fail thresholds drawn are read from its metric catalogue
(`catalogue/metrics.yaml`, with PyYAML), so no threshold is typed here.

Two kinds of figure (the visual-first-documentation skill):

  CONCEPT   a seeded simulation that shows how a mechanism behaves. It
            carries no number measured on a run. Parameters live in the
            CONCEPT block with the reason each value was chosen.
  MEASURED  numbers observed on a machine. They are typed once, in the
            MEASURED block, with where they came from.

Each figure carries one claim:

  eval-levels               every metric looks at one unit of the data,
                            from a cell to the whole launch
  eval-ks-vs-wasserstein    two failures with the same Wasserstein distance
                            can differ 5x in KS
  eval-ks-bracket           on a fixed grid KS is exact at the edges and
                            bounded inside the bins; where the source holds
                            a point mass only the union of both sides'
                            grids closes the bracket
  eval-noise-floor          the same KS value is noise at 1,000 rows and a
                            real effect at a million
  eval-baseline             metric(R, source) is the floor a generator that
                            read only R can reach
  eval-matched-n-entropy    plug-in entropy grows with n, so a faithful
                            generator fails the ratio until both sides are
                            read at the same n
  eval-rarefied-duplicates  the duplicate share grows with n; rarefied to
                            one m a faithful generator shows no excess
  eval-sets-rhe             one fingerprint order gives the reference
                            sample, its holdout twin and the two
                            prompt-exposed prefixes
  eval-memorization-lift    chance hits R and H alike, so only copying
                            lifts the ratio; status reads its lower bound
  eval-dcr-nndr             DCR flags a row parked on a real record, NNDR
                            a row for which one record is uniquely closest
  eval-holdout-dcr          a copy fraction f moves the closer-to-reference
                            share by f / 2
  eval-c2st                 an AUC is read with its DeLong interval against
                            0.5, never alone
  eval-fanout               an equal mean fan-out can hide a wrong shape
  eval-count-rule           an edge is published only with ten source
                            records at or beyond it on each side
  eval-cpu-budget           (MEASURED) encoding bounds the per-row pass;
                            the nearest-neighbour block is fixed by the
                            sample knobs

Colour follows the entity in every figure: BLUE = the source and the
reference sample R; ORANGE = the synthetic side under test, or the reading
that misleads; AQUA = the control: the holdout H, a faithful generator, a
corrected reading. Gray is context (thresholds, model lines). The script
prints the palette separation on every run.
"""

# pyplot must be imported after matplotlib.use("Agg") selects the headless backend.
# pylint: disable=wrong-import-position

from __future__ import annotations

import argparse
import functools
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, Rectangle

_REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS = _REPO_ROOT / "docs" / "designs" / "assets"
CATALOGUE = (
    _REPO_ROOT / "packages" / "sdfb-evaluation" / "src" / "sdfb_evaluation" /
    "catalogue" / "metrics.yaml")

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CONTEXT, WASH = "#8a94a0", "#eef1f5"
NORMAL_FLOOR, CVD_TARGET = 15.0, 8.0
SRGB_CUTOFF = 0.04045
DPI = 160

# --- CONCEPT (seeded, deterministic; parameters chosen to illustrate) -----
SEED = 7  # eval-ks-vs-wasserstein and eval-dcr-nndr (unchanged since 2026-08)
N = 20_000
# Reference marginal: Normal(50, 8). Two synthetic failure modes tuned so
# BOTH have Wasserstein-1 = 5.0 against the reference:
#   - "shifted": Normal(55, 8)  — every value moved by 5  → W1 = 5, KS ~ 0.25
#   - "tail":    95% reference + 5% Normal(150, 3) — 5% of mass moved ~100
#     → W1 = 0.05 * 100 = 5, KS ~ 0.05
REF_MU, REF_SIGMA = 50.0, 8.0
SHIFT_DELTA = 5.0
TAIL_FRAC, TAIL_MU, TAIL_SIGMA = 0.05, 150.0, 3.0
# DCR/NNDR panel: hand-placed 2-D geometry. Two real clusters + one
# isolated real record; three synthetic archetypes (memorized copy,
# re-identifying neighbor of the isolated record, safe in-between row).
REAL_CLUSTERS = ((2.0, 2.0), (6.5, 3.5))
REAL_PER_CLUSTER = 14
REAL_SPREAD = 0.7
REAL_ISOLATED = (9.5, 1.0)
SYN_MEMORIZED_OFFSET = 0.05  # sits (almost) on top of a real record
SYN_REIDENT = (9.1, 1.35)  # unambiguously closest to the isolated record
SYN_SAFE = (4.3, 2.8)  # between the clusters, d1 ~= d2

# One seed per simulated figure, so a figure can be regenerated alone.
SEEDS = {
    "eval-ks-bracket": 11,
    "eval-noise-floor": 12,
    "eval-baseline": 13,
    "eval-matched-n-entropy": 14,
    "eval-rarefied-duplicates": 15,
    "eval-memorization-lift": 16,
    "eval-holdout-dcr": 17,
    "eval-c2st": 18,
    "eval-fanout": 19,
    "eval-count-rule": 20,
}
ALPHA = 0.05  # every interval and floor below is two-sided 95 %, as the code's

# eval-ks-bracket. A source with a point mass (40 % of its rows are exactly
# 0, as a discount or a fee column has) against a generator that smears
# that mass around 0. A source grid has no point inside the smear, however
# fine it is; the synthetic side's own grid does. 11-point (decile) grids
# keep the bins wide enough to draw; the bracket at the evaluator's 1,001
# points a side (`context.plan.GRID_POINTS`) is computed too and printed in
# each panel.
BRACKET_ROWS = 4_000
BRACKET_ATOM_SHARE, BRACKET_SMEAR_SIGMA = 0.40, 0.25
BRACKET_GRID_POINTS, BRACKET_FULL_GRID = 11, 1_001

# eval-noise-floor. The four table sizes the design names, per side; null
# KS statistics are simulated where a laptop can sort the rows (not at 90M).
FLOOR_SIZES = (1_000, 10_000, 1_000_000, 90_000_000)
FLOOR_SIMULATED = {1_000: 400, 10_000: 200, 1_000_000: 12}  # n -> replicates

# eval-baseline. A 200k-row source, a 10k-row reference sample R, and a
# generator that only ever read R (it resamples R's quantile function).
# One column carries a planted shift, sized to land past the fail threshold.
BASELINE_SOURCE_ROWS, BASELINE_REF_ROWS = 200_000, 10_000
BASELINE_SHIFT_SIGMAS = 0.6

# eval-matched-n-entropy. A long-tailed column (a city, a product name):
# 500k possible values with Zipf weights. The source holds 10k rows and the
# synthetic table 200x more, so the plug-in entropy of ONE distribution
# differs by sample size alone, by enough to cross the fail threshold.
ENTROPY_VALUES, ENTROPY_ZIPF = 500_000, 0.8
ENTROPY_SOURCE_ROWS, ENTROPY_SYNTH_ROWS = 10_000, 2_000_000
ENTROPY_REPLICATES = 24

# eval-rarefied-duplicates. Row contents drawn from 300k records with Zipf
# weights; the source holds 100k rows, both generators write 1M. The
# repeating generator draws from the 30k most frequent records only.
DUP_RECORDS, DUP_ZIPF = 300_000, 0.6
DUP_SOURCE_ROWS, DUP_SYNTH_ROWS = 100_000, 1_000_000
DUP_REPEATER_RECORDS = 30_000

# eval-sets-rhe. n is the launch's reference sample size (10,000 here);
# 1,024 is the number of row documents a prompt can be built from
# (`context.reference.EXPOSURE_ROWS`).
PANEL_N, PANEL_EXPOSED = 10_000, 1_024

# eval-memorization-lift. 9,800 exclusive records on each side (a 10k panel
# less the records both halves hold). Per scenario: the chance that the
# synthetic table reproduces an exclusive record at all, and how many R
# records it copies on top.
LIFT_EXCLUSIVE = 9_800
LIFT_SCENARIOS = (
    # label, chance of a hit per exclusive record, R records copied on top
    ("faithful, sparse domain", 0.0005, 0),
    ("faithful, dense domain", 0.05, 0),
    ("dense domain + 300 copied records", 0.05, 300),
    ("sparse domain + 100 copied records", 0.0005, 100),
)

# eval-holdout-dcr. R and H are 2,000 rows each of one 4-D mixture; the
# synthetic table is 4,000 rows of which a fraction are exact copies of R
# rows. The geometry panel draws a 2-D toy of the same construction.
DCR_PANEL_ROWS, DCR_SYNTH_ROWS, DCR_DIMS = 2_000, 4_000, 4
DCR_COPY_FRACTIONS = (0.0, 0.01, 0.05, 0.10, 0.20, 0.30, 0.50)
DCR_CALLOUT_FRACTION = 0.01  # the copy fraction the acceptance run plants
DCR_TOY_POINTS, DCR_TOY_SYNTH, DCR_TOY_COPIES = 26, 16, 6

# eval-c2st. Out-of-fold scores drawn from a binormal model (source rows
# Normal(0, 1), synthetic rows Normal(delta, 1)): AUC = Phi(delta / sqrt 2),
# so delta sets the separation directly and no classifier version can move
# the picture. Two sample sizes show the interval's width.
C2ST_DELTAS = (("faithful generator", 0.0), ("detectable generator", 1.8))
C2ST_SIZES = (5_000, 100)
AUC_CHANCE = 0.5  # table.detection_auc's noise reference: cannot tell

# eval-fanout. 20k parents; a quarter have no child, the rest 1 + a
# negative-binomial count. The collapsed generator gives EVERY parent the
# source's mean number of children.
FANOUT_PARENTS, FANOUT_CHILDLESS = 20_000, 0.25
FANOUT_NB_R, FANOUT_NB_P = 2, 0.4
FANOUT_CAP = 50  # `beam.relational.FANOUT_CAP`: bins 0 .. 49 and >= 50
FANOUT_SHOWN = 13

# eval-count-rule. 2,000 source values and their 1,001-point grid: a grid
# point every two records, so the outermost points sit on single records.
# k = 10 is the evaluator's `RARE_COUNT` (`beam.dense`, `beam.census`).
RULE_ROWS, RULE_GRID, RULE_K = 2_000, 1_001, 10
RULE_SHOWN = 28  # records drawn at each tail

# --- MEASURED (typed once; laptop micro-benchmarks, not a Dataflow run) ----
# Reported by the implementation tasks while each transform was built
# (2026-09/10), on one Intel i5-6267U core (2.9 GHz) with single-threaded
# numpy. They size the design; they are not evidence of a cloud run, and the
# raw timings were not kept as a committed evidence bundle. A floor on the
# dense pass is pinned by
# packages/sdfb-evaluation/tests/beam/test_dense.py::test_throughput_8192_by_30_batch.
MEASURED_STAGES = (
    # stage, rows/s per core, rows/s of ENCODING the same batch, the batch
    ("dense profile", 56_700, 8_000, "8,192 x 30 columns, 190 pairs"),
    ("value census", 94_000, 30_000, "8,192 rows, 9 census columns"),
    ("membership", 657_000, 7_300, "R = H = 10,000, 6 non-key columns"),
)
# Exact Gower nearest neighbours of 50,000 synthetic rows against R and H
# (10,000 rows each): (feature columns, seconds). d = 6 and 30 were timed on
# 4 busy cores, d = 50 single-threaded.
MEASURED_NN = ((6, 5.1), (30, 30.4), (50, 41.1))
MEASURED_NN_QUERY, MEASURED_NN_REFERENCE = 50_000, 20_000
MEASURED_NN_NS_PER_OP = 0.84  # ns per (query, reference, feature), d = 50
MEASURED_DETECTION_SECONDS = 9.4  # 50,000 rows a class, 6 features, clean


# --------------------------------------------------------------------------
# thresholds: the catalogue's, read at run time and never retyped here
@functools.cache
def _catalogue() -> dict:
  with CATALOGUE.open(encoding="utf-8") as handle:
    return {m["id"]: m for m in yaml.safe_load(handle)["metrics"]}


def _gate(metric_id: str) -> tuple[float, float]:
  """(warn, fail) of `metric_id` in the evaluator's metric catalogue."""
  thresholds = _catalogue()[metric_id]["thresholds"]
  return float(thresholds["warn"]), float(thresholds["fail"])


# --------------------------------------------------------------------------
# drawing helpers
def _style(ax, *, grid_axis="y"):
  ax.set_facecolor(SURFACE)
  for side in ("top", "right"):
    ax.spines[side].set_visible(False)
  for side in ("left", "bottom"):
    ax.spines[side].set_color(GRID)
  ax.tick_params(which="both", colors=MUTED, labelsize=9, length=0)
  if grid_axis:
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8, alpha=0.9)
  ax.set_axisbelow(True)


def _dot(ax, x, y, color, *, hollow=False, size=10, **kwargs):
  """One marker: filled with a surface ring, or hollow in its own colour."""
  ax.plot(
      x,
      y,
      "o",
      color=SURFACE if hollow else color,
      markersize=size,
      markeredgecolor=color if hollow else SURFACE,
      markeredgewidth=2.0 if hollow else 1.6,
      **kwargs)


def _title(ax, text, sub=None):
  ax.set_title(
      text, color=INK, fontsize=12.5, fontweight="600", loc="left", pad=26)
  if sub:
    ax.text(
        0,
        1.025,
        sub,
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9,
        va="bottom")


def _legend(ax, **kwargs):
  kwargs.setdefault("fontsize", 8.5)
  ax.legend(frameon=False, labelcolor=INK, **kwargs)


def _threshold(ax, value, label, *, axis="y", where=0.995):
  """A labelled reference line: context, never a data colour."""
  if axis == "y":
    ax.axhline(value, color=CONTEXT, linewidth=1.0, zorder=1)
    ax.text(
        where,
        value,
        f" {label} ",
        transform=ax.get_yaxis_transform(),
        color=MUTED,
        fontsize=8,
        ha="right",
        va="bottom")
  else:
    ax.axvline(value, color=CONTEXT, linewidth=1.0, zorder=1)
    ax.text(
        value,
        where,
        f" {label}",
        transform=ax.get_xaxis_transform(),
        color=MUTED,
        fontsize=8,
        ha="left",
        va="top")


def _row_labels(ax, left, right=None):
  """Name the rows of a horizontal chart on its y axis (and, with `right`,
  on a twin axis), so the layout engine makes room for the text."""
  ax.set_yticks(range(len(left)))
  ax.set_yticklabels(left, color=INK, fontsize=9.3, linespacing=1.35)
  ax.spines["left"].set_visible(False)
  if right is None:
    return
  twin = ax.twinx()
  twin.set_ylim(ax.get_ylim())
  twin.set_yticks(range(len(right)))
  twin.set_yticklabels(
      right, color=INK, fontsize=9.3, linespacing=1.35, fontweight="600")
  twin.tick_params(length=0)
  for spine in twin.spines.values():
    spine.set_visible(False)


def _save(fig, name, *, kind="concept", seed=None, pad=1.6):
  """Stamp what kind of figure this is into the image, then write it."""
  if kind == "measured":
    stamp = ("MEASURED on one laptop core (implementation micro-benchmarks)"
             " - not a Dataflow run")
  elif seed is None:
    stamp = "CONCEPT figure: a schematic - no data, not a measured run"
  else:
    stamp = (f"CONCEPT figure: seeded simulation (seed {seed}) - "
             "not a measured run")
  fig.tight_layout(pad=pad, rect=(0, 0.035, 1, 1))
  fig.text(0.008, 0.012, stamp, color=MUTED, fontsize=7.5, ha="left")
  fig.savefig(ASSETS / f"{name}.png", dpi=DPI, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
# the statistics the figures illustrate (numpy only)
_LGAMMA = np.vectorize(math.lgamma, otypes=[float])


def _ecdf(sample: np.ndarray, grid: np.ndarray) -> np.ndarray:
  return np.searchsorted(np.sort(sample), grid, side="right") / sample.size


def _ks_two_sample(a: np.ndarray, b: np.ndarray) -> float:
  """The two-sample Kolmogorov-Smirnov statistic, from the raw samples."""
  a, b = np.sort(a), np.sort(b)
  grid = np.concatenate([a, b])
  f_a = np.searchsorted(a, grid, side="right") / a.size
  f_b = np.searchsorted(b, grid, side="right") / b.size
  return float(np.max(np.abs(f_a - f_b)))


def _w1_equal_n(a: np.ndarray, b: np.ndarray) -> float:
  """Wasserstein-1 between two samples of the same size."""
  return float(np.mean(np.abs(np.sort(a) - np.sort(b))))


def _ks_critical(n: float, m: float) -> float:
  """`stats.noise.ks_critical`: the two-sample floor of `column.ks`."""
  return math.sqrt(-math.log(ALPHA / 2.0) / 2.0) * math.sqrt((n + m) / (n * m))


def _dkw_epsilon(n: float) -> float:
  """`stats.noise.dkw_epsilon`: the one-sample band, Massart's constant."""
  return math.sqrt(math.log(2.0 / ALPHA) / (2.0 * n))


def _bin_counts(x: np.ndarray, edges: np.ndarray) -> np.ndarray:
  """`stats.binned.bin_counts`: bins (-inf, e0], (e0, e1], ..., (e_last, inf)."""
  idx = np.searchsorted(edges, x, side="left")
  return np.bincount(idx, minlength=edges.size + 1)


def _ks_bracket(c_src: np.ndarray, c_syn: np.ndarray):
  """`stats.binned.ks_bracket`: (D_lo, D_hi, edge of D_lo, bin of D_hi)."""
  f_src = np.concatenate(([0.0], np.cumsum(c_src) / c_src.sum()))
  f_syn = np.concatenate(([0.0], np.cumsum(c_syn) / c_syn.sum()))
  gaps = np.abs(f_src - f_syn)
  inside = np.maximum(f_src[1:] - f_syn[:-1], f_syn[1:] - f_src[:-1])
  return (float(gaps.max()), float(inside.max()), int(np.argmax(gaps)) - 1,
          int(np.argmax(inside)), f_src, f_syn)


def _binomial_tail(k: int, n: int, p: float, *, upper: bool) -> float:
  """P(X >= k) when `upper`, else P(X <= k), for X ~ Binomial(n, p)."""
  i = np.arange(k, n + 1) if upper else np.arange(0, k + 1)
  log_pmf = (
      math.lgamma(n + 1) - _LGAMMA(i + 1) - _LGAMMA(n - i + 1) +
      i * math.log(p) + (n - i) * math.log1p(-p))
  return float(np.exp(log_pmf).sum())


def _clopper_pearson(k: int, n: int) -> tuple[float, float]:
  """The exact binomial interval for k of n (Clopper & Pearson, 1934)."""

  def solve(upper: bool) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(60):
      mid = (lo + hi) / 2.0
      tail = _binomial_tail(k, n, mid, upper=upper)
      # P(X >= k) rises with p; P(X <= k) falls with p.
      if (tail < ALPHA / 2.0) == upper:
        lo = mid
      else:
        hi = mid
    return (lo + hi) / 2.0

  return (solve(True) if k > 0 else 0.0, solve(False) if k < n else 1.0)


def _rate_ratio(m1: int, t1: float, m2: int, t2: float):
  """`stats.noise.rate_ratio`: (ratio, lo, hi) of two Poisson rates,
  conditional on the total (Przyborowski & Wilenski, 1940)."""

  def ratio(prob: float) -> float:
    if prob <= 0.0:
      return 0.0
    return math.inf if prob >= 1.0 else (prob / (1.0 - prob)) * (t2 / t1)

  total = m1 + m2
  if total == 0:
    return None, 0.0, math.inf
  lo, hi = _clopper_pearson(m1, total)
  return ratio(m1 / total), ratio(lo), ratio(hi)


def _wilson(k: float, n: float, z: float = 1.959964) -> tuple[float, float]:
  """`stats.noise.wilson_interval` (Wilson, 1927)."""
  phat = k / n
  denom = 1.0 + z * z / n
  center = (phat + z * z / (2.0 * n)) / denom
  margin = (z / denom) * math.sqrt(phat * (1.0 - phat) / n + z * z /
                                   (4.0 * n * n))
  return max(0.0, center - margin), min(1.0, center + margin)


def _midrank(x: np.ndarray) -> np.ndarray:
  _, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
  ends = np.cumsum(counts)
  return ((2 * ends - counts + 1) / 2.0)[inverse]


def _delong(synthetic: np.ndarray, source: np.ndarray):
  """AUC and its DeLong standard error, from midranks
  (DeLong, DeLong & Clarke-Pearson, 1988; `stats.detection.c2st_auc`)."""
  m, n = synthetic.size, source.size
  pooled = _midrank(np.concatenate([synthetic, source]))
  auc = (pooled[:m].sum() - m * (m + 1) / 2.0) / (m * n)
  v_syn = (pooled[:m] - _midrank(synthetic)) / n
  v_src = 1.0 - (pooled[m:] - _midrank(source)) / m
  return float(auc), math.sqrt(v_syn.var(ddof=1) / m + v_src.var(ddof=1) / n)


def _roc(synthetic: np.ndarray, source: np.ndarray):
  scores = np.concatenate([synthetic, source])
  labels = np.concatenate([np.ones(synthetic.size), np.zeros(source.size)])
  order = np.argsort(-scores, kind="stable")
  tpr = np.concatenate(([0.0], np.cumsum(labels[order]) / synthetic.size))
  fpr = np.concatenate(([0.0], np.cumsum(1 - labels[order]) / source.size))
  return fpr, tpr


def _log_choose(a: np.ndarray, b: float) -> np.ndarray:
  return _LGAMMA(a + 1.0) - math.lgamma(b + 1.0) - _LGAMMA(a - b + 1.0)


def _rarefied_duplicate_share(counts: np.ndarray, m: int) -> float:
  """Expected share of an m-row subsample that sits in a duplicate group
  (`beam.membership`, Ruling R73): E[D_m] = sum_c f_c (c m / N - P(X = 1)),
  X ~ Hypergeometric(N, c, m), from the frequency of frequencies f_c."""
  held, f_c = np.unique(counts[counts > 0], return_counts=True)
  total = float(np.sum(held * f_c))
  held = held.astype(float)
  possible = (total - held) >= (m - 1)
  log_p1 = np.full(held.shape, -np.inf)
  log_p1[possible] = (
      np.log(held[possible]) + _log_choose(total - held[possible], m - 1.0) -
      (math.lgamma(total + 1.0) - math.lgamma(m + 1.0) -
       math.lgamma(total - m + 1.0)))
  expected = np.sum(f_c * (held * m / total - np.exp(log_p1)))
  return float(expected / m)


def _duplicate_share(counts: np.ndarray) -> float:
  """Share of rows whose content some other row also holds."""
  return float(counts[counts > 1].sum() / counts.sum())


def _zipf(values: int, exponent: float) -> np.ndarray:
  weights = 1.0 / np.arange(1, values + 1)**exponent
  return weights / weights.sum()


def _plugin_entropy_bits(counts: np.ndarray) -> float:
  p = counts[counts > 0] / counts.sum()
  return float(-(p * np.log2(p)).sum())


# --------------------------------------------------------------------------
def fig_levels():
  """Seven levels, each looking at one unit of the data (a schematic of
  `catalogue/metrics.yaml`'s `levels`; the counts live in the generated
  catalogue table, not here)."""
  levels = (
      ("field", "one cell: is the value\nvalid and allowed?",
       "field.type_validity"),
      ("column", "one column's\ndistribution", "column.ks"),
      ("pair", "two columns\ntogether", "pair.spearman_delta"),
      ("row", "whole records: copies\nand near neighbours",
       "row.memorization_lift"),
      ("table", "the table as\none object", "table.detection_auc"),
      ("relationship", "one foreign key:\nchildren per parent",
       "relationship.fanout_tvd"),
      ("model", "every table\nof the launch", "model.overall_score"),
  )
  fig, axes = plt.subplots(1, 7, figsize=(13.5, 4.0), facecolor=SURFACE)

  def table(ax, x0, y0, cols, rows, cell, lit):
    for r in range(rows):
      for c in range(cols):
        on = lit == "all" or (r, c) in lit
        ax.add_patch(
            Rectangle((x0 + c * cell, y0 - (r + 1) * cell),
                      cell,
                      cell,
                      facecolor=BLUE if on else WASH,
                      edgecolor=SURFACE,
                      linewidth=1.6))

  def link(ax, start, end):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=9,
            color=INK,
            linewidth=1.1,
            shrinkA=0,
            shrinkB=0))

  cols, rows, cell = 5, 6, 1.5
  left, top = (10 - cols * cell) / 2, 9.6
  lit = {
      "field": {(2, 3)},
      "column": {(r, 1) for r in range(rows)},
      "pair": {(r, c) for r in range(rows) for c in (1, 3)},
      "row": {(3, c) for c in range(cols)},
      "table": "all",
  }
  for ax, (name, what, example) in zip(axes, levels, strict=True):
    ax.set_xlim(0, 10)
    ax.set_ylim(-5.2, 10)
    ax.set_aspect("equal")
    ax.axis("off")
    if name in lit:
      table(ax, left, top, cols, rows, cell, lit[name])
    elif name == "relationship":
      # the parent's key column, the child's foreign-key column, the edge
      table(ax, 0.3, 9.6, 3, 3, 1.2, {(r, 2) for r in range(3)})
      table(ax, 5.6, 7.0, 3, 5, 1.2, {(r, 0) for r in range(5)})
      link(ax, (5.55, 5.6), (3.95, 7.4))
    else:
      table(ax, 0.3, 9.6, 3, 3, 1.15, "all")
      table(ax, 6.0, 8.4, 3, 3, 1.15, "all")
      table(ax, 3.0, 4.0, 3, 3, 1.15, "all")
      link(ax, (5.95, 6.9), (3.85, 7.7))
      link(ax, (4.1, 4.05), (2.4, 6.1))
      link(ax, (5.6, 4.05), (7.3, 4.9))
    ax.text(
        5, -0.75, name, color=INK, fontsize=12, fontweight="600", ha="center")
    ax.text(
        5,
        -1.55,
        what,
        color=MUTED,
        fontsize=8.8,
        ha="center",
        va="top",
        linespacing=1.25)
    ax.text(
        5,
        -4.0,
        example,
        color=INK,
        fontsize=7.6,
        ha="center",
        va="top",
        family="monospace")
  fig.suptitle(
      "Seven levels: every metric looks at one unit of the data",
      color=INK,
      fontsize=13,
      fontweight="600",
      x=0.012,
      ha="left")
  fig.text(
      0.012,
      0.895,
      "blue = what one metric row of that level reads; below, one metric "
      "id per level as an example. Scores roll up from these units to "
      "table.<family>_score, then model.<family>_score.",
      color=MUTED,
      fontsize=9,
      ha="left")
  _save(fig, "eval-levels", pad=0.6)


# --------------------------------------------------------------------------
def fig_ks_vs_wasserstein():
  """Same W1, 5x different KS — the two statistics see different failures
  (`stats.binned.ks_bracket` and `w1_from_bins`)."""
  rng = np.random.default_rng(SEED)
  ref = rng.normal(REF_MU, REF_SIGMA, N)
  shifted = rng.normal(REF_MU + SHIFT_DELTA, REF_SIGMA, N)
  n_tail = int(TAIL_FRAC * N)
  tail = np.concatenate([
      rng.normal(REF_MU, REF_SIGMA, N - n_tail),
      rng.normal(TAIL_MU, TAIL_SIGMA, n_tail),
  ])

  fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.0), facecolor=SURFACE)
  for ax, synth, label in (
      (axes[0], shifted, "shifted twin (every value +5)"),
      (axes[1], tail, "tail escape (5% of mass moved ~100)"),
  ):
    ks = _ks_two_sample(ref, synth)
    w1 = _w1_equal_n(ref, synth)
    grid = np.linspace(
        min(ref.min(), synth.min()), max(ref.max(), synth.max()), 800)
    f_ref, f_syn = _ecdf(ref, grid), _ecdf(synth, grid)
    ax.fill_between(
        grid,
        f_ref,
        f_syn,
        color=AQUA,
        alpha=0.30,
        label="area between CDFs = W₁")
    ax.plot(grid, f_ref, color=BLUE, linewidth=2, label="real CDF")
    ax.plot(grid, f_syn, color=ORANGE, linewidth=2, label="synthetic CDF")
    i = int(np.argmax(np.abs(f_ref - f_syn)))
    ax.plot([grid[i], grid[i]],
            [min(f_ref[i], f_syn[i]),
             max(f_ref[i], f_syn[i])],
            color=INK,
            linewidth=2.5)
    ax.annotate(
        f"KS = {ks:.2f}", (grid[i], (f_ref[i] + f_syn[i]) / 2),
        textcoords="offset points",
        xytext=(10, 0),
        color=INK,
        fontsize=10,
        fontweight="600")
    _style(ax, grid_axis="both")
    _title(
        ax, label, f"W1 = {w1:.1f} here, about 5 in both panels; "
        f"KS = {ks:.2f}: a 5x difference")
    ax.set_xlabel("value", color=MUTED, fontsize=9)
    ax.set_ylabel("F(x)", color=MUTED, fontsize=9)
    _legend(ax, loc="lower right")
  _save(fig, "eval-ks-vs-wasserstein", seed=SEED)


# --------------------------------------------------------------------------
def fig_ks_bracket():
  """D_lo is exact at the edges, D_hi bounds the inside of every bin, and
  only the union of both sides' grids closes the gap when the source holds
  a point mass (`stats.binned.union_edges`, `ks_bracket`)."""
  seed = SEEDS["eval-ks-bracket"]
  rng = np.random.default_rng(seed)
  n_atom = int(BRACKET_ATOM_SHARE * BRACKET_ROWS)

  def positive() -> np.ndarray:
    return 0.3 + rng.lognormal(0.0, 0.45, BRACKET_ROWS - n_atom)

  src = np.concatenate([np.zeros(n_atom), positive()])
  syn = np.concatenate(
      [rng.normal(0.0, BRACKET_SMEAR_SIGMA, n_atom),
       positive()])
  true_ks = _ks_two_sample(src, syn)

  def grids(points: int) -> tuple[np.ndarray, np.ndarray]:
    probs = np.linspace(0.0, 1.0, points)
    q_src = np.quantile(src, probs, method="inverted_cdf")
    q_syn = np.quantile(syn, probs, method="inverted_cdf")
    return np.unique(q_src), np.unique(np.concatenate([q_src, q_syn]))

  def width(edges: np.ndarray) -> float:
    d_lo, d_hi, *_ = _ks_bracket(
        _bin_counts(src, edges), _bin_counts(syn, edges))
    return d_hi - d_lo

  drawn, full = grids(BRACKET_GRID_POINTS), grids(BRACKET_FULL_GRID)
  panels = (("source grid only", drawn[0], full[0]),
            ("union of both sides' grids", drawn[1], full[1]))
  x_lo, x_hi = -0.9, 2.9
  fine = np.linspace(x_lo, x_hi, 900)

  fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2), facecolor=SURFACE)
  for ax, (label, edges, full_edges) in zip(axes, panels, strict=True):
    d_lo, d_hi, at_edge, in_bin, f_src, f_syn = _ks_bracket(
        _bin_counts(src, edges), _bin_counts(syn, edges))
    bounds = np.concatenate(([x_lo], edges, [x_hi]))
    # Between two edges each CDF is only known to stay inside its box.
    for b in range(edges.size + 1):
      for f_side, color in ((f_src, BLUE), (f_syn, ORANGE)):
        ax.add_patch(
            Rectangle((bounds[b], f_side[b]),
                      bounds[b + 1] - bounds[b],
                      f_side[b + 1] - f_side[b],
                      facecolor=color,
                      alpha=0.14,
                      linewidth=0))
    for edge in edges:
      ax.axvline(edge, color=GRID, linewidth=0.8, zorder=0)
    ax.plot(fine, _ecdf(src, fine), color=BLUE, linewidth=1.6, label="source")
    ax.plot(
        fine, _ecdf(syn, fine), color=ORANGE, linewidth=1.6, label="synthetic")
    ax.plot(edges, f_src[1:-1], "o", color=BLUE, markersize=4.5)
    ax.plot(edges, f_syn[1:-1], "o", color=ORANGE, markersize=4.5)
    # D_lo: the widest gap at an edge. D_hi: the widest gap a bin allows.
    e = edges[at_edge]
    ax.plot([e, e],
            sorted((f_src[at_edge + 1], f_syn[at_edge + 1])),
            color=INK,
            linewidth=2.6)
    hi_x = (bounds[in_bin] + bounds[in_bin + 1]) / 2
    top = max(f_src[in_bin + 1], f_syn[in_bin + 1])
    ax.plot([hi_x, hi_x], [top - d_hi, top], color=CONTEXT, linewidth=2.6)
    ax.text(
        0.975,
        0.05, f"D_lo = {d_lo:.3f}   exact, at an edge (black bar)\n"
        f"D_hi = {d_hi:.3f}   the most a bin allows (gray bar)\n"
        f"KS of the raw rows = {true_ks:.3f}\n"
        f"with {BRACKET_FULL_GRID:,} points a side the bracket is "
        f"{width(full_edges):.3f} wide",
        transform=ax.transAxes,
        color=INK,
        fontsize=9.5,
        ha="right",
        va="bottom",
        linespacing=1.5,
        bbox={
            "facecolor": SURFACE,
            "edgecolor": "none",
            "alpha": 0.85,
            "pad": 4
        })
    _style(ax, grid_axis="y")
    _title(
        ax, label, f"{BRACKET_GRID_POINTS} points a side, {edges.size} "
        f"distinct edges: the bracket is {d_hi - d_lo:.3f} wide")
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(0, 1)
    ax.set_xlabel(
        "value (shaded boxes: where each CDF can run between two edges)",
        color=MUTED,
        fontsize=9)
    ax.set_ylabel("F(x)", color=MUTED, fontsize=9)
    _legend(ax, loc="upper left")
  _save(fig, "eval-ks-bracket", seed=seed)


# --------------------------------------------------------------------------
def fig_noise_floor():
  """The KS noise floor against n, with the DKW band: an effect size keeps
  its meaning at every n, a significance test does not
  (`stats.noise.ks_critical`, `dkw_epsilon`; `scoring.status_for` step 9)."""
  seed = SEEDS["eval-noise-floor"]
  rng = np.random.default_rng(seed)
  sizes = np.logspace(2, 8.3, 200)
  floor = np.array([_ks_critical(n, n) for n in sizes])
  band = np.array([_dkw_epsilon(n) for n in sizes])

  fig, ax = plt.subplots(figsize=(13.5, 5.4), facecolor=SURFACE)
  ax.fill_between(sizes, 1e-5, floor, color=WASH, zorder=0)
  ax.plot(
      sizes,
      floor,
      color=ORANGE,
      linewidth=2,
      label="two-sample KS floor: the row's noise_floor")
  ax.plot(
      sizes,
      band,
      color=AQUA,
      linewidth=2,
      label="DKW-Massart band: one empirical CDF against the truth")
  label_once = "simulated KS of two samples of ONE distribution (5-95 %)"
  for n, replicates in FLOOR_SIMULATED.items():
    null = np.array([
        _ks_two_sample(rng.random(n), rng.random(n)) for _ in range(replicates)
    ])
    lo, mid, hi = np.quantile(null, (0.05, 0.5, 0.95))
    ax.plot([n, n], [lo, hi], color=BLUE, linewidth=2)
    _dot(ax, n, mid, BLUE, size=8, label=label_once)
    label_once = None
  for n in FLOOR_SIZES:
    value = _ks_critical(n, n)
    _dot(ax, n, value, ORANGE, hollow=True, size=7, zorder=5)
    ax.annotate(
        f"n = {n:,}\nfloor {value:.2g}", (n, value),
        textcoords="offset points",
        xytext=(8, 10),
        color=INK,
        fontsize=9)
  warn, fail = _gate("column.ks")
  _threshold(ax, warn, f"column.ks warn {warn:.2f}")
  _threshold(ax, fail, f"column.ks fail {fail:.2f}")
  # where the floor meets the warn threshold: floor(n) = warn
  crossing = -math.log(ALPHA / 2.0) / warn**2
  ax.annotate(
      f"below n = {crossing:,.0f} the floor is above\nthe warn threshold",
      (crossing, warn),
      textcoords="offset points",
      xytext=(-6, 40),
      color=INK,
      fontsize=9,
      arrowprops={
          "arrowstyle": "-",
          "color": CONTEXT,
          "linewidth": 1.0
      })
  ax.text(
      130,
      3.0e-4, "inside the floor: a difference this small is\n"
      "what two samples of one distribution show.\n"
      "A WARN or FAIL in here is recorded as PASS.",
      color=MUTED,
      fontsize=9,
      linespacing=1.4)
  ax.set_xscale("log")
  ax.set_yscale("log")
  ax.set_xlim(1e2, 2.6e8)
  ax.set_ylim(1e-4, 1.6)
  _style(ax, grid_axis="both")
  largest = max(FLOOR_SIZES)
  _title(
      ax, "The KS noise floor shrinks as 1/sqrt(n); the thresholds do not move",
      f"at {largest:,} rows a side the floor is "
      f"{_ks_critical(largest, largest):.1g}: every difference is "
      "'significant', so status reads effect sizes")
  ax.set_xlabel("rows per side (n = m)", color=MUTED, fontsize=9)
  ax.set_ylabel(
      "KS distance (largest CDF gap)", color=MUTED, fontsize=9, labelpad=6)
  _legend(ax, loc="upper right", bbox_to_anchor=(1.0, 0.72))
  _save(fig, "eval-noise-floor", seed=seed)


# --------------------------------------------------------------------------
def fig_baseline():
  """metric(R, source) is the floor a generator that read only R can reach
  (D4; `beam.dense` stores it as `baseline_value` on every fidelity row)."""
  seed = SEEDS["eval-baseline"]
  rng = np.random.default_rng(seed)
  n = BASELINE_SOURCE_ROWS
  price = rng.lognormal(3.4, 0.8, n)
  columns = {
      "users.age": rng.normal(41, 13, n).clip(18, 90),
      "orders.created_at": rng.beta(2.2, 1.3, n) * 1_800,
      "order_items.sale_price": price,
      "order_items.cost": price * rng.normal(0.5, 0.06, n),
      "products.retail_price": rng.gamma(2.0, 30.0, n),
      "orders.amount": rng.gamma(3.0, 45.0, n),
  }
  planted = "orders.amount"
  rows = []
  for name, source in columns.items():
    ref = rng.permutation(source)[:BASELINE_REF_ROWS]
    # The generator knows the column only through R: it draws from R's own
    # quantile function, as an inverse-CDF sampler does.
    synthetic = np.quantile(ref, rng.random(n))
    if name == planted:
      synthetic = synthetic + BASELINE_SHIFT_SIGMAS * source.std()
    rows.append(
        (name, _ks_two_sample(ref, source), _ks_two_sample(synthetic, source)))
  rows.reverse()

  fig, ax = plt.subplots(figsize=(13.5, 4.6), facecolor=SURFACE)
  for y, (name, base, value) in enumerate(rows):
    first = y == 0
    ax.plot([base, value], [y, y], color=CONTEXT, linewidth=1.4, zorder=2)
    _dot(
        ax,
        value,
        y,
        ORANGE,
        zorder=3,
        label="value = KS(synthetic, source)" if first else None)
    _dot(
        ax,
        base,
        y,
        BLUE,
        zorder=4,
        label="baseline_value = KS(R, source)" if first else None)
    if name == planted:
      ax.text(
          math.sqrt(base * value),
          y + 0.16,
          f"planted shift: {value / base:.0f}x its baseline",
          color=INK,
          fontsize=9,
          ha="center",
          va="bottom")
    else:
      ax.text(
          max(base, value) * 1.18,
          y,
          f"{value / base:.1f}x its baseline",
          color=MUTED,
          fontsize=9,
          va="center")
  warn, fail = _gate("column.ks")
  _threshold(ax, warn, f"warn {warn:.2f}", axis="x")
  _threshold(ax, fail, f"fail {fail:.2f}", axis="x")
  ax.set_xscale("log")
  ax.set_xlim(2e-3, 1.0)
  ax.set_ylim(-0.7, len(rows) - 0.3)
  _style(ax, grid_axis="x")
  _row_labels(ax, [name for name, _, _ in rows])
  _title(
      ax, "A value is read against its baseline, not against zero",
      f"R = {BASELINE_REF_ROWS:,} of {n:,} source rows; the generator read "
      "only R. A perfect copier of R scores exactly the blue dot")
  ax.set_xlabel("column.ks (log scale)", color=MUTED, fontsize=9)
  _legend(ax, loc="lower left")
  _save(fig, "eval-baseline", seed=seed)


# --------------------------------------------------------------------------
def fig_matched_n_entropy():
  """Plug-in entropy grows with n: at each side's own n a faithful generator
  fails the ratio, at matched n it sits at 1 (D5; `beam.encode`'s
  `subsample_m`, `stats.diversity`)."""
  seed = SEEDS["eval-matched-n-entropy"]
  rng = np.random.default_rng(seed)
  weights = _zipf(ENTROPY_VALUES, ENTROPY_ZIPF)
  true_bits = float(-(weights * np.log2(weights)).sum())
  n_src, n_syn = ENTROPY_SOURCE_ROWS, ENTROPY_SYNTH_ROWS
  matched_rate = n_src / n_syn

  def counts(rows: int) -> np.ndarray:
    return rng.multinomial(rows, weights)

  curve_n = np.unique(np.logspace(3, np.log10(n_syn), 22).astype(int))
  curve = [
      np.mean([_plugin_entropy_bits(counts(int(rows)))
               for _ in range(3)])
      for rows in curve_n
  ]
  full, matched, distinct_full, distinct_matched = [], [], [], []
  for _ in range(ENTROPY_REPLICATES):
    source, synthetic = counts(n_src), counts(n_syn)
    # the evaluator's matched-n subsample: each row kept with p = m / n
    thinned = rng.binomial(synthetic, matched_rate)
    h_src, k_src = _plugin_entropy_bits(source), np.count_nonzero(source)
    full.append(_plugin_entropy_bits(synthetic) / h_src)
    matched.append(_plugin_entropy_bits(thinned) / h_src)
    distinct_full.append(np.count_nonzero(synthetic) / k_src)
    distinct_matched.append(np.count_nonzero(thinned) / k_src)

  fig, axes = plt.subplots(
      1,
      2,
      figsize=(13.5, 5.0),
      facecolor=SURFACE,
      gridspec_kw={"width_ratios": (1.2, 1)})
  ax = axes[0]
  ax.plot(curve_n, curve, color=BLUE, linewidth=2)
  ax.axhline(true_bits, color=CONTEXT, linewidth=1.0)
  ax.text(
      1_100,
      true_bits,
      f" the distribution's entropy: {true_bits:.1f} bits",
      color=MUTED,
      fontsize=8.5,
      va="bottom")
  for rows, name, color, side in ((n_src, "source", BLUE, "left"),
                                  (n_syn, "synthetic", ORANGE, "right")):
    bits = float(np.interp(rows, curve_n, curve))
    _dot(ax, rows, bits, color, zorder=4)
    ax.annotate(
        f"{name}: {rows:,} rows\n{bits:.1f} bits", (rows, bits),
        textcoords="offset points",
        xytext=(14, -26) if side == "left" else (0, -80),
        ha=side,
        color=INK,
        fontsize=9)
  ax.set_xscale("log")
  ax.set_ylim(top=true_bits + 0.9)
  _style(ax, grid_axis="both")
  _title(ax, "One distribution, read at two sample sizes",
         "plug-in Shannon entropy of the rows read, in bits")
  ax.set_xlabel("rows read (n)", color=MUTED, fontsize=9)
  ax.set_ylabel("plug-in entropy (bits)", color=MUTED, fontsize=9)

  ax = axes[1]
  jitter = np.random.default_rng(seed + 1).uniform(-0.16, 0.16,
                                                   ENTROPY_REPLICATES)
  strips = ((matched, AQUA, "both sides at\nmatched n"),
            (full, ORANGE, "each side at\nits own n"))
  for y, (values, color, _) in enumerate(strips):
    ax.plot(
        values,
        y + jitter,
        "o",
        color=color,
        markersize=7,
        markeredgecolor=SURFACE,
        markeredgewidth=1.2)
    ax.text(
        float(np.mean(values)) + 0.022,
        y,
        f"mean {np.mean(values):.2f}",
        color=INK,
        fontsize=9.5,
        va="center")
  ax.axvline(1.0, color=INK, linewidth=1.0)
  warn, fail = _gate("column.entropy_ratio")  # distances from the target, 1
  ax.axvspan(1 - warn, 1 + warn, color=WASH, zorder=0)
  for bound, text in ((1 + warn, "warn"), (1 + fail, "fail")):
    _threshold(ax, bound, f"{text} {bound:.2f}", axis="x")
  ax.text(
      0.98,
      0.04, "column.distinct_ratio, same draws:\n"
      f"{np.mean(distinct_full):.0f} at each side's own n, "
      f"{np.mean(distinct_matched):.2f} at matched n",
      transform=ax.transAxes,
      color=MUTED,
      fontsize=9,
      ha="right",
      va="bottom",
      linespacing=1.4)
  ax.set_xlim(0.85, 1.5)
  ax.set_ylim(-0.75, 1.6)
  _style(ax, grid_axis="x")
  _row_labels(ax, [label for _, _, label in strips])
  _title(
      ax, "column.entropy_ratio of a faithful generator",
      f"{ENTROPY_REPLICATES} seeded draws; the gate reads the distance "
      "from 1")
  ax.set_xlabel("synthetic entropy / source entropy", color=MUTED, fontsize=9)
  _save(fig, "eval-matched-n-entropy", seed=seed)


# --------------------------------------------------------------------------
def fig_rarefied_duplicates():
  """The duplicate share grows with the rows compared; rarefied to one m a
  faithful generator shows no excess and a repeating one keeps it
  (Ruling R73; `beam.membership`, `row.internal_duplicate_excess`)."""
  seed = SEEDS["eval-rarefied-duplicates"]
  rng = np.random.default_rng(seed)
  weights = _zipf(DUP_RECORDS, DUP_ZIPF)
  narrow = weights[:DUP_REPEATER_RECORDS] / weights[:DUP_REPEATER_RECORDS].sum()
  sides = (
      ("source", BLUE, rng.multinomial(DUP_SOURCE_ROWS, weights)),
      ("faithful generator", AQUA, rng.multinomial(DUP_SYNTH_ROWS, weights)),
      ("repeating generator", ORANGE, rng.multinomial(DUP_SYNTH_ROWS, narrow)),
  )
  m = DUP_SOURCE_ROWS
  source_at_m = _rarefied_duplicate_share(sides[0][2], m)

  fig, ax = plt.subplots(figsize=(13.5, 5.2), facecolor=SURFACE)
  readout = [
      "".ljust(21) + "own n".rjust(8) + "at m".rjust(8) +
      "excess at m".rjust(13)
  ]
  for name, color, counts in sides:
    rows = int(counts.sum())
    grid = np.unique(np.logspace(3.3, np.log10(rows), 26).astype(int))
    share = [_rarefied_duplicate_share(counts, int(k)) for k in grid]
    # the source lies under the faithful generator: draw it as a wide band
    is_source = name == "source"
    ax.plot(
        grid,
        share,
        color=color,
        linewidth=6 if is_source else 2,
        alpha=0.45 if is_source else 1.0,
        solid_capstyle="round",
        label=name)
    raw, at_m = _duplicate_share(counts), _rarefied_duplicate_share(counts, m)
    _dot(ax, rows, raw, color, hollow=True, size=9, zorder=5)
    _dot(ax, m, at_m, color, size=9, zorder=6)
    readout.append(
        name.ljust(21) + f"{raw:8.2f}{at_m:8.2f}{at_m - source_at_m:+13.3f}")
  ax.axvline(m, color=CONTEXT, linewidth=1.0)
  ax.text(
      m * 0.94,
      0.97,
      f"m = min(n_src, n_syn) = {m:,}",
      color=INK,
      fontsize=9,
      ha="right",
      va="top")
  ax.text(
      0.985,
      0.05,
      "\n".join(readout),
      transform=ax.transAxes,
      color=INK,
      fontsize=9,
      family="monospace",
      ha="right",
      va="bottom",
      linespacing=1.5)
  handles, labels = ax.get_legend_handles_labels()
  for hollow, text in ((True, "each side at its own n"),
                       (False, "every side at the common m")):
    handles.append(
        Line2D([], [],
               linestyle="",
               marker="o",
               markersize=8,
               color=SURFACE if hollow else MUTED,
               markeredgecolor=MUTED if hollow else SURFACE,
               markeredgewidth=2.0 if hollow else 1.6))
    labels.append(text)
  ax.set_xscale("log")
  ax.set_ylim(0, 1.06)
  _style(ax, grid_axis="both")
  _title(
      ax, "Duplicates are compared at the same number of rows",
      "expected share of an m-row subsample whose content another of its "
      "rows also holds (exact rarefaction)")
  ax.set_xlabel("rows compared (m)", color=MUTED, fontsize=9)
  ax.set_ylabel("share of rows in a duplicate group", color=MUTED, fontsize=9)
  _legend(ax, handles=handles, labels=labels, loc="upper left")
  _save(fig, "eval-rarefied-duplicates", seed=seed)


# --------------------------------------------------------------------------
def fig_sets_rhe():
  """One fingerprint order gives R, E, H and H_E (D3;
  `context.reference.panel_sql`)."""
  n, exposed = PANEL_N, PANEL_EXPOSED
  fig, ax = plt.subplots(figsize=(13.5, 4.4), facecolor=SURFACE)
  bars = (
      (2.0, 1, exposed, BLUE, f"E: ranks 1 to {exposed:,}"),
      (2.0, n + 1, n + exposed, AQUA, f"H_E: ranks n + 1 to n + {exposed:,}"),
      (1.0, 1, n, BLUE, "R: the reference sample the generator read"),
      (1.0, n + 1, 2 * n, AQUA, "H: the holdout it never saw"),
      (0.0, 2 * n + 1, 2.42 * n, CONTEXT, "rest of the source"),
  )
  for y, start, end, color, label in bars:
    ax.add_patch(
        Rectangle((start, y - 0.27),
                  end - start,
                  0.54,
                  facecolor=color,
                  edgecolor=SURFACE,
                  linewidth=2))
    wide = end - start > 0.2 * n
    ax.text(
        (start + end) / 2 if wide else end + 0.012 * n,
        y,
        label,
        color=SURFACE if wide else INK,
        fontsize=10,
        fontweight="600",
        va="center",
        ha="center" if wide else "left")
  ax.annotate(
      "",
      xy=(2.5 * n, 0.0),
      xytext=(2.42 * n, 0.0),
      arrowprops={
          "arrowstyle": "-|>",
          "color": CONTEXT,
          "linewidth": 2
      })
  ax.plot([n + 0.5, n + 0.5], [0.55, 2.45], color=INK, linewidth=1.0)
  ax.text(
      n + 0.5,
      2.62, "rank n: R ends, H begins. Two halves of one random order, so a "
      "chance match is as likely in either",
      color=INK,
      fontsize=9.5,
      ha="center")
  notes = (
      (2.0, "prompt-exposed prefix\n(row documents)"),
      (1.0, "what the generator read\nvs never saw"),
      (0.0, "in no panel"),
  )
  for y, text in notes:
    ax.text(
        -0.02 * n,
        y,
        text,
        color=MUTED,
        fontsize=9,
        ha="right",
        va="center",
        linespacing=1.3)
  ax.set_xlim(-0.42 * n, 2.52 * n)
  ax.set_ylim(-0.7, 3.0)
  ax.set_yticks([])
  ax.set_xticks([1, n, 2 * n])
  ax.set_xticklabels(["1", f"n = {n:,}", f"2n = {2 * n:,}"])
  _style(ax, grid_axis="")
  ax.spines["left"].set_visible(False)
  _title(
      ax, "One fingerprint order gives the four sets",
      "source rows ranked by FARM_FINGERPRINT(TO_JSON_STRING(row)): the "
      "order of the generator's own LIMIT n")
  ax.set_xlabel("rank in the fingerprint order", color=MUTED, fontsize=9)
  _save(fig, "eval-sets-rhe")


# --------------------------------------------------------------------------
def fig_memorization_lift():
  """Chance hits R and H alike, so only copying lifts the ratio, and status
  reads the interval's lower bound (`stats.noise.rate_ratio`;
  `beam.membership`, `row.memorization_lift`)."""
  seed = SEEDS["eval-memorization-lift"]
  rng = np.random.default_rng(seed)
  warn, fail = _gate("row.memorization_lift")
  left, right, drawn = [], [], []
  for label, chance, copied in reversed(LIFT_SCENARIOS):
    m_h = int(rng.binomial(LIFT_EXCLUSIVE, chance))
    m_r = int(rng.binomial(LIFT_EXCLUSIVE - copied, chance)) + copied
    ratio, lo, hi = _rate_ratio(m_r, LIFT_EXCLUSIVE, m_h, LIFT_EXCLUSIVE)
    status = "FAIL" if lo >= fail else "WARN" if lo >= warn else "PASS"
    left.append(
        f"{label}\n{m_r:,} R-only and {m_h:,} H-only records reproduced")
    right.append(f"ci_low {lo:.2f}\n{status}")
    drawn.append((ratio, lo, hi))

  fig, ax = plt.subplots(figsize=(13.5, 4.8), facecolor=SURFACE)
  x_min, x_max = 0.1, 400.0
  for y, (ratio, lo, hi) in enumerate(drawn):
    first = y == 0
    ax.plot([max(lo, x_min * 1.1), min(hi, x_max)], [y, y],
            color=INK,
            linewidth=1.8)
    if hi > x_max:
      ax.annotate(
          "",
          xy=(x_max * 1.25, y),
          xytext=(x_max, y),
          arrowprops={
              "arrowstyle": "-|>",
              "color": INK,
              "linewidth": 1.8
          })
    if ratio is not None and ratio < x_max:
      _dot(
          ax,
          ratio,
          y,
          INK,
          hollow=True,
          size=9,
          zorder=4,
          label="lift (point estimate)" if first else None)
    _dot(
        ax,
        max(lo, x_min * 1.1),
        y,
        ORANGE,
        size=11,
        zorder=5,
        label="ci_low: the value status reads" if first else None)
  ax.axvline(1.0, color=INK, linewidth=1.0)
  _threshold(ax, warn, f"warn {warn:g}", axis="x")
  _threshold(ax, fail, f"fail {fail:g}", axis="x")
  ax.set_xscale("log")
  ax.set_xlim(x_min, x_max * 1.3)
  ax.set_ylim(-0.7, len(drawn) - 0.25)
  _style(ax, grid_axis="x")
  _row_labels(ax, left, right)
  _title(
      ax, "Memorization lift: reference-only records against holdout-only ones",
      f"{LIFT_EXCLUSIVE:,} exclusive records on each side; 95 % "
      "Clopper-Pearson interval of the rate ratio. 1 = chance alone")
  ax.set_xlabel(
      "lift = (R-only reproduced / R-only) / (H-only reproduced / H-only),"
      " log scale",
      color=MUTED,
      fontsize=9)
  _legend(ax, loc="upper right")
  _save(fig, "eval-memorization-lift", seed=seed)


# --------------------------------------------------------------------------
def _real_points() -> np.ndarray:
  rng = np.random.default_rng(SEED)
  pts = [
      rng.normal(c, REAL_SPREAD, size=(REAL_PER_CLUSTER, 2))
      for c in REAL_CLUSTERS
  ]
  return np.vstack([*pts, np.asarray([REAL_ISOLATED])])


def _nearest_two(point, real: np.ndarray):
  d = np.linalg.norm(real - np.asarray(point), axis=1)
  order = np.argsort(d)
  return order[0], order[1], d[order[0]], d[order[1]]


def fig_dcr_nndr():
  """DCR catches the parked copy; NNDR catches the unambiguous neighbor
  (`stats.privacy.gower_knn`, `row.dcr_p5_ratio`, `row.nndr_p5_ratio`)."""
  real = _real_points()
  syn_memorized = real[3] + SYN_MEMORIZED_OFFSET

  fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2), facecolor=SURFACE)

  for ax in axes:
    ax.scatter(
        real[:, 0], real[:, 1], s=42, color=BLUE, label="real rows", zorder=3)
    _style(ax, grid_axis="both")
    ax.set_xlabel("feature 1 (Gower-embedded)", color=MUTED, fontsize=9)
    ax.set_ylabel("feature 2", color=MUTED, fontsize=9)
    ax.set_xlim(0, 11.5)
    ax.set_ylim(-0.5, 5.5)

  # Panel A — DCR: distance from each synthetic row to its closest real row.
  ax = axes[0]
  for point, color, name, offset, align in (
      (syn_memorized, ORANGE, "memorized copy", (12, 14), "left"),
      (SYN_SAFE, AQUA, "novel row", (0, -34), "center"),
  ):
    i1, _, d1, _ = _nearest_two(point, real)
    ax.scatter(
        *point,
        marker="X",
        s=130,
        color=color,
        zorder=4,
        edgecolors=SURFACE,
        linewidths=1.5)
    ax.plot([point[0], real[i1][0]], [point[1], real[i1][1]],
            color=color,
            linewidth=1.6,
            linestyle=":")
    ax.annotate(
        f"{name}\nDCR = {d1:.2f}",
        point,
        textcoords="offset points",
        xytext=offset,
        ha=align,
        color=INK,
        fontsize=9)
  _title(ax, "DCR — distance to closest record",
         "a synthetic row parked on a real row has DCR → 0 (memorization)")
  _legend(ax, loc="upper left")

  # Panel B — NNDR: d1/d2 over the same cloud.
  ax = axes[1]
  for point, color, name, offset, align in (
      (SYN_REIDENT, ORANGE, "re-identifying", (0, -36), "center"),
      (SYN_SAFE, AQUA, "safe", (-12, 14), "right"),
  ):
    i1, i2, d1, d2 = _nearest_two(point, real)
    ax.scatter(
        *point,
        marker="X",
        s=130,
        color=color,
        zorder=4,
        edgecolors=SURFACE,
        linewidths=1.5)
    for j, style in ((i1, "-"), (i2, "--")):
      ax.plot([point[0], real[j][0]], [point[1], real[j][1]],
              color=color,
              linewidth=1.6,
              linestyle=style)
    ax.annotate(
        f"{name}\nNNDR = d₁/d₂ = {d1 / d2:.2f}",
        point,
        textcoords="offset points",
        xytext=offset,
        ha=align,
        color=INK,
        fontsize=9)
  _title(
      ax, "NNDR — nearest-neighbor distance ratio",
      "d₁ ≪ d₂ ⇒ ONE real record is unambiguously closest "
      "(re-identification risk)")
  _legend(ax, loc="upper left")
  _save(fig, "eval-dcr-nndr", seed=SEED)


def _dcr_geometry_panel(ax, rng):
  """The holdout test on a toy small enough to see every row."""
  ref = rng.normal(0.0, 1.0, (DCR_TOY_POINTS, 2))
  holdout = rng.normal(0.0, 1.0, (DCR_TOY_POINTS, 2))
  fresh = rng.normal(0.0, 1.0, (DCR_TOY_SYNTH - DCR_TOY_COPIES, 2))
  copies = ref[:DCR_TOY_COPIES]
  for points, color, label in ((ref, BLUE, "R: rows the generator read"),
                               (holdout, AQUA, "H: rows it never saw")):
    ax.scatter(
        points[:, 0],
        points[:, 1],
        s=54,
        color=color,
        edgecolors=SURFACE,
        linewidths=1.2,
        zorder=3,
        label=label)
  closer = 0.0
  for row in np.vstack([fresh, copies]):
    d_r = np.linalg.norm(ref - row, axis=1)
    d_h = np.linalg.norm(holdout - row, axis=1)
    to_ref = d_r.min() <= d_h.min()
    closer += 1.0 if d_r.min() < d_h.min() else 0.5 * (d_r.min() == d_h.min())
    target = ref[d_r.argmin()] if to_ref else holdout[d_h.argmin()]
    ax.plot([row[0], target[0]], [row[1], target[1]],
            color=BLUE if to_ref else AQUA,
            linewidth=1.3,
            zorder=2)
  ax.scatter(
      fresh[:, 0],
      fresh[:, 1],
      marker="X",
      s=70,
      color=INK,
      edgecolors=SURFACE,
      linewidths=1.0,
      zorder=4,
      label="synthetic row, joined to its nearest real row")
  ax.scatter(
      copies[:, 0],
      copies[:, 1],
      marker="o",
      s=210,
      facecolors="none",
      edgecolors=ORANGE,
      linewidths=2.2,
      zorder=5,
      label="synthetic row that copies an R row")
  low, high = ax.get_ylim()
  ax.set_ylim(low, high + 0.42 * (high - low))  # room for the legend
  _style(ax, grid_axis="both")
  _title(
      ax, "Which half holds each synthetic row's nearest neighbour?",
      f"{DCR_TOY_SYNTH} synthetic rows, {DCR_TOY_COPIES} of them copies: "
      f"share closer to R = {closer / DCR_TOY_SYNTH:.2f}")
  ax.set_xlabel("feature 1", color=MUTED, fontsize=9)
  ax.set_ylabel("feature 2", color=MUTED, fontsize=9)
  _legend(ax, loc="upper left", fontsize=8)


def _dcr_share_panel(ax, rng):
  """The share against the fraction of synthetic rows that copy R."""
  n, n_syn = DCR_PANEL_ROWS, DCR_SYNTH_ROWS
  centres = rng.normal(0.0, 2.0, (6, DCR_DIMS))

  def draw(rows: int) -> np.ndarray:
    return centres[rng.integers(0, 6, rows)] + rng.normal(
        0.0, 1.0, (rows, DCR_DIMS))

  ref, holdout = draw(n), draw(n)
  line = np.linspace(0, 0.52, 50)
  ax.plot(
      line, 0.5 + line / 2, color=CONTEXT, linewidth=1.6, label="0.5 + f / 2")
  for fraction in DCR_COPY_FRACTIONS:
    first = fraction == DCR_COPY_FRACTIONS[0]
    n_copies = round(fraction * n_syn)
    synthetic = np.vstack(
        [draw(n_syn - n_copies), ref[rng.integers(0, n, n_copies)]])
    share = _closer_share(synthetic, ref, holdout)
    ci_low = _wilson(share * n_syn, n_syn)[0]
    ax.plot([fraction, fraction], [ci_low, share], color=INK, linewidth=1.6)
    _dot(
        ax,
        fraction,
        share,
        INK,
        hollow=True,
        size=8,
        zorder=4,
        label="share" if first else None)
    _dot(
        ax,
        fraction,
        ci_low,
        ORANGE,
        zorder=5,
        label="ci_low: the value status reads" if first else None)
    if fraction == DCR_CALLOUT_FRACTION:
      ax.annotate(
          f"{fraction:.0%} copies: ci_low {ci_low:.3f}\nfar below the gate",
          (fraction, ci_low),
          textcoords="offset points",
          xytext=(26, -34),
          color=INK,
          fontsize=9,
          arrowprops={
              "arrowstyle": "-",
              "color": CONTEXT,
              "linewidth": 1.0
          })
  warn, fail = _gate("row.dcr_train_holdout_share")
  _threshold(ax, warn, f"warn {warn:.2f}")
  _threshold(ax, fail, f"fail {fail:.2f}")
  ax.set_xlim(-0.02, 0.54)
  ax.set_ylim(0.44, 0.80)
  _style(ax, grid_axis="both")
  _title(
      ax, "A copy fraction f moves the share by only f / 2",
      f"R = H = {n:,} rows, {n_syn:,} synthetic rows, {DCR_DIMS} features; "
      "the gate reads ci_low (Wilson here)")
  ax.set_xlabel(
      "fraction f of synthetic rows that are exact copies of R rows",
      color=MUTED,
      fontsize=9)
  ax.set_ylabel("share closer to R than to H", color=MUTED, fontsize=9)
  _legend(ax, loc="upper left")


# --------------------------------------------------------------------------
def _nearest_distance(queries: np.ndarray, reference: np.ndarray) -> np.ndarray:
  out = np.empty(queries.shape[0])
  for start in range(0, queries.shape[0], 500):
    block = queries[start:start + 500, None, :] - reference[None, :, :]
    out[start:start + 500] = np.sqrt((block**2).sum(axis=2)).min(axis=1)
  return out


def _closer_share(synthetic, ref, holdout) -> float:
  """`stats.privacy`'s holdout share: closer to R counts 1, a tie one half."""
  d_r = _nearest_distance(synthetic, ref)
  d_h = _nearest_distance(synthetic, holdout)
  return float(np.mean((d_r < d_h) + 0.5 * (d_r == d_h)))


def fig_holdout_dcr():
  """A copy fraction f moves the closer-to-reference share by f / 2, so the
  share needs a large f to fail (Ruling R87; `stats.privacy`,
  `row.dcr_train_holdout_share`)."""
  seed = SEEDS["eval-holdout-dcr"]
  rng = np.random.default_rng(seed)
  fig, axes = plt.subplots(
      1,
      2,
      figsize=(13.5, 5.2),
      facecolor=SURFACE,
      gridspec_kw={"width_ratios": (1, 1.15)})
  _dcr_geometry_panel(axes[0], rng)
  _dcr_share_panel(axes[1], rng)
  _save(fig, "eval-holdout-dcr", seed=seed)


# --------------------------------------------------------------------------
def fig_c2st():
  """An AUC is read with its DeLong interval against 0.5
  (`stats.detection.c2st_auc`, `table.detection_auc`)."""
  seed = SEEDS["eval-c2st"]
  rng = np.random.default_rng(seed)
  fig, axes = plt.subplots(
      1,
      2,
      figsize=(13.5, 5.2),
      facecolor=SURFACE,
      gridspec_kw={"width_ratios": (1, 1.25)})
  colors = {"faithful generator": AQUA, "detectable generator": ORANGE}
  warn, fail = _gate("table.detection_auc")
  left, right, drawn = [], [], []
  ax = axes[0]
  ax.plot([0, 1], [0, 1], color=CONTEXT, linewidth=1.2)
  for name, delta in reversed(C2ST_DELTAS):
    for size in reversed(C2ST_SIZES):
      source = rng.normal(0.0, 1.0, size)
      synthetic = rng.normal(delta, 1.0, size)
      auc, se = _delong(synthetic, source)
      lo, hi = max(0.0, auc - 1.959964 * se), min(1.0, auc + 1.959964 * se)
      verdict = "FAIL" if auc >= fail else "WARN" if auc >= warn else "PASS"
      note = "covers" if lo <= AUC_CHANCE <= hi else "clears"
      left.append(f"{name}\n{size:,} rows per class")
      right.append(
          f"{auc:.2f} [{lo:.2f}, {hi:.2f}]\n{verdict}: {note} {AUC_CHANCE}")
      drawn.append((colors[name], auc, lo, hi))
      if size == max(C2ST_SIZES):
        fpr, tpr = _roc(synthetic, source)
        ax.plot(fpr, tpr, color=colors[name], linewidth=2, label=name)
        ax.annotate(
            f"AUC {auc:.2f}", (0.30, float(np.interp(0.30, fpr, tpr))),
            textcoords="offset points",
            xytext=(8, -16),
            color=INK,
            fontsize=9.5,
            fontweight="600")
  ax.set_xlim(0, 1)
  ax.set_ylim(0, 1)
  _style(ax, grid_axis="both")
  _title(ax, "ROC of the out-of-fold classifier scores",
         f"{max(C2ST_SIZES):,} rows per class; the diagonal is 'cannot tell'")
  ax.set_xlabel(
      "source rows scored as synthetic (false positive rate)",
      color=MUTED,
      fontsize=9)
  ax.set_ylabel(
      "synthetic rows found (true positive rate)", color=MUTED, fontsize=9)
  handles, labels = ax.get_legend_handles_labels()
  _legend(ax, handles=handles[::-1], labels=labels[::-1], loc="lower right")

  ax = axes[1]
  for y, (color, auc, lo, hi) in enumerate(drawn):
    ax.plot([lo, hi], [y, y], color=color, linewidth=3)
    _dot(ax, auc, y, color, zorder=4)
  ax.axvline(AUC_CHANCE, color=INK, linewidth=1.0)
  _threshold(ax, warn, f"warn {warn:.2f}", axis="x")
  _threshold(ax, fail, f"fail {fail:.2f}", axis="x")
  ax.set_xlim(0.39, 1.0)
  ax.set_ylim(-0.7, len(drawn) - 0.3)
  _style(ax, grid_axis="x")
  _row_labels(ax, left, right)
  _title(ax, "The same AUC with its DeLong interval",
         "0.5 = chance; fewer rows give a wider interval")
  ax.set_xlabel("table.detection_auc", color=MUTED, fontsize=9)
  _save(fig, "eval-c2st", seed=seed)


# --------------------------------------------------------------------------
def _fanout_hist(fanout: np.ndarray) -> np.ndarray:
  """`stats.relational.fanout_histogram`: bins 0 .. 49 and >= 50."""
  return np.bincount(np.minimum(fanout, FANOUT_CAP), minlength=FANOUT_CAP + 1)


def fig_fanout():
  """An equal mean fan-out can hide a wrong shape (`stats.relational.
  fanout_metrics`; `relationship.fanout_tvd`, `fanout_mean_ratio`)."""
  seed = SEEDS["eval-fanout"]
  rng = np.random.default_rng(seed)

  def draw() -> np.ndarray:
    children = 1 + rng.negative_binomial(FANOUT_NB_R, FANOUT_NB_P,
                                         FANOUT_PARENTS)
    return np.where(rng.random(FANOUT_PARENTS) < FANOUT_CHILDLESS, 0, children)

  source = draw()
  mean = source.mean()
  # every parent gets floor or ceil of the mean, so the mean is the source's
  collapsed = np.floor(mean + rng.random(FANOUT_PARENTS)).astype(int)
  panels = (
      ("faithful generator: the same law", AQUA, draw()),
      ("collapsed generator: every parent gets the mean", ORANGE, collapsed),
  )
  p_src = _fanout_hist(source) / FANOUT_PARENTS
  x = np.arange(FANOUT_SHOWN)

  fig, axes = plt.subplots(
      1, 2, figsize=(13.5, 5.0), facecolor=SURFACE, sharey=True)
  for ax, (label, color, synthetic) in zip(axes, panels, strict=True):
    p_syn = _fanout_hist(synthetic) / FANOUT_PARENTS
    tvd = 0.5 * float(np.abs(p_src - p_syn).sum())
    ratio = synthetic.mean() / mean
    childless = abs(float(p_syn[0] - p_src[0]))
    ax.bar(
        x - 0.2, p_src[:FANOUT_SHOWN], width=0.36, color=BLUE, label="source")
    ax.bar(
        x + 0.2,
        p_syn[:FANOUT_SHOWN],
        width=0.36,
        color=color,
        label="synthetic")
    ax.text(
        0.97,
        0.72, f"fanout_mean_ratio  {ratio:.2f}\n"
        f"fanout_tvd  {tvd:.2f}\n"
        f"zero_child_share_delta  {childless:.2f}",
        transform=ax.transAxes,
        color=INK,
        fontsize=10,
        ha="right",
        va="top",
        linespacing=1.6)
    ax.set_xticks(x)
    _style(ax, grid_axis="y")
    _title(
        ax, label, f"{FANOUT_PARENTS:,} parents; "
        f"mean children per parent {synthetic.mean():.2f}")
    ax.set_xlabel("children per parent", color=MUTED, fontsize=9)
    _legend(ax, loc="upper right")
  axes[0].set_ylabel("share of parents", color=MUTED, fontsize=9)
  _save(fig, "eval-fanout", seed=seed)


# --------------------------------------------------------------------------
def fig_count_rule():
  """An edge is published only when at least k = 10 source records lie at
  or below it AND at or above it (Rulings R65, R69, R71, R74, R77;
  `beam.dense`)."""
  seed = SEEDS["eval-count-rule"]
  rng = np.random.default_rng(seed)
  values = np.sort(rng.lognormal(3.4, 0.7, RULE_ROWS))
  grid = np.unique(
      np.quantile(values, np.linspace(0, 1, RULE_GRID), method="inverted_cdf"))
  at_or_below = np.searchsorted(values, grid, side="right")
  at_or_above = values.size - np.searchsorted(values, grid, side="left")
  kept = (at_or_below >= RULE_K) & (at_or_above >= RULE_K)
  first, last = grid[kept][0], grid[kept][-1]

  fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.0), facecolor=SURFACE)
  tails = (
      (True, values[:RULE_SHOWN], first, "at or below"),
      (False, values[-RULE_SHOWN:], last, "at or above"),
  )
  for ax, (lower, shown, bound, wording) in zip(axes, tails, strict=True):
    lo, hi = shown[0], shown[-1]
    pad = 0.05 * (hi - lo)
    inside = (grid >= lo - pad) & (grid <= hi + pad)
    ax.axvspan(
        bound if lower else lo - pad,
        hi + pad if lower else bound,
        color=AQUA,
        alpha=0.13,
        zorder=0)
    ranks = np.arange(1, shown.size + 1)
    ax.step(
        shown,
        ranks if lower else ranks[::-1],
        where="post" if lower else "pre",
        color=BLUE,
        linewidth=1.8)
    ax.plot(
        shown,
        np.full(shown.size, -1.3),
        "|",
        color=BLUE,
        markersize=11,
        markeredgewidth=1.4)
    for edge, ok in zip(grid[inside], kept[inside], strict=True):
      ax.plot([edge, edge], [0, RULE_SHOWN + 1],
              color=INK if ok else CONTEXT,
              linewidth=1.2 if ok else 0.9,
              zorder=1)
    ax.axhline(RULE_K, color=INK, linewidth=1.0)
    ax.text(
        lo - 0.6 * pad if lower else hi + 0.6 * pad,
        RULE_K + 0.5,
        f"k = {RULE_K} records",
        color=INK,
        fontsize=9,
        ha="left" if lower else "right",
        va="bottom")
    ax.annotate(
        "the exact " + ("minimum" if lower else "maximum") +
        ": one record's\nvalue, never published", (lo if lower else hi, -1.3),
        textcoords="offset points",
        xytext=(24, 56) if lower else (-14, 56),
        ha="left" if lower else "right",
        color=INK,
        fontsize=9,
        bbox={
            "facecolor": SURFACE,
            "edgecolor": "none",
            "alpha": 0.9,
            "pad": 2
        },
        arrowprops={
            "arrowstyle": "-",
            "color": CONTEXT,
            "linewidth": 1.0
        })
    ax.annotate(
        ("first" if lower else "last") + " kept edge", (bound, RULE_SHOWN + 1),
        textcoords="offset points",
        xytext=(6, 3) if lower else (-6, 3),
        ha="left" if lower else "right",
        color=INK,
        fontsize=9,
        fontweight="600")
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_ylim(-3, RULE_SHOWN + 4.5)
    _style(ax, grid_axis="y")
    _title(
        ax, ("Lower" if lower else "Upper") + " tail: which grid points "
        "may be published", "gray edge: dropped; black edge: kept; "
        "green: the publishable range")
    ax.set_xlabel(
        "column value (ticks under the axis: one source record each)",
        color=MUTED,
        fontsize=9)
    ax.set_ylabel(
        f"source records {wording} the value", color=MUTED, fontsize=9)
  _save(fig, "eval-count-rule", seed=seed)


# --------------------------------------------------------------------------
def fig_cpu_budget():
  """MEASURED: the per-row pass is bounded by encoding, and the
  nearest-neighbour block by the sample knobs (`beam.encode`, `beam.dense`,
  `beam.census`, `beam.membership`, `stats.privacy.gower_knn`)."""
  fig, axes = plt.subplots(
      1,
      2,
      figsize=(13.5, 4.8),
      facecolor=SURFACE,
      gridspec_kw={"width_ratios": (1.15, 1)})
  ax = axes[0]
  stages = MEASURED_STAGES[::-1]
  for y, (_, rate, encode, _) in enumerate(stages):
    first = y == 0
    ax.barh(
        y + 0.17,
        rate,
        height=0.28,
        color=BLUE,
        label="the stage" if first else None)
    ax.barh(
        y - 0.17,
        encode,
        height=0.28,
        color=CONTEXT,
        label="encoding the same batch" if first else None)
    ax.text(
        rate * 1.12,
        y + 0.17,
        f"{rate:,} rows/s: {rate / encode:.0f}x the encoder",
        color=INK,
        fontsize=9,
        va="center")
    ax.text(
        encode * 1.12,
        y - 0.17,
        f"{encode:,} rows/s",
        color=MUTED,
        fontsize=9,
        va="center")
  ax.set_xscale("log")
  ax.set_xlim(2_500, 2.2e7)
  ax.set_ylim(-0.6, len(stages) - 0.35)
  _style(ax, grid_axis="x")
  _row_labels(ax, [f"{stage}\n{batch}" for stage, _, _, batch in stages])
  _title(ax, "Per-row pass: every statistic outruns the encoder",
         "rows per second on one core; each pair timed on its own batch")
  ax.set_xlabel("rows per second per core (log scale)", color=MUTED, fontsize=9)
  _legend(ax, loc="lower right")

  ax = axes[1]
  pairs = MEASURED_NN_QUERY * MEASURED_NN_REFERENCE
  width = np.linspace(0, 56, 50)
  ax.plot(
      width,
      pairs * width * MEASURED_NN_NS_PER_OP * 1e-9,
      color=CONTEXT,
      linewidth=1.6,
      label=f"{MEASURED_NN_NS_PER_OP} ns x rows x (|R| + |H|) x features")
  for index, (columns, seconds) in enumerate(MEASURED_NN):
    _dot(ax, columns, seconds, BLUE, label="timed" if index == 0 else None)
    ax.annotate(
        f"{seconds:g} s", (columns, seconds),
        textcoords="offset points",
        xytext=(-8, 9),
        ha="right",
        color=INK,
        fontsize=9)
  ax.axhline(MEASURED_DETECTION_SECONDS, color=INK, linewidth=1.0)
  ax.text(
      56,
      MEASURED_DETECTION_SECONDS + 0.8,
      f"detection, {MEASURED_NN_QUERY:,} rows a class: "
      f"{MEASURED_DETECTION_SECONDS:g} s",
      color=INK,
      fontsize=9,
      ha="right")
  ax.set_xlim(0, 56)
  ax.set_ylim(0, 50)
  _style(ax, grid_axis="both")
  _title(
      ax, "Fixed block: set by the sample knobs, not the table",
      f"exact Gower search: {MEASURED_NN_QUERY:,} synthetic rows against "
      f"R and H, {MEASURED_NN_REFERENCE // 2:,} rows each")
  ax.set_xlabel("feature columns", color=MUTED, fontsize=9)
  ax.set_ylabel("seconds", color=MUTED, fontsize=9)
  _legend(ax, loc="upper left")
  _save(fig, "eval-cpu-budget", kind="measured")


# --------------------------------------------------------------------------
# palette check (OKLab distances x100, as the dataviz validator computes them)
_CVD = {
    # Machado, Oliveira & Fernandes (2009), severity 1.0, on linear RGB
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
               (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
               (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779),
               (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}


def _linear_rgb(hexstr):
  channels = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
  return tuple(c / 12.92 if c <= SRGB_CUTOFF else ((c + 0.055) / 1.055)**2.4
               for c in channels)


def _oklab(rgb):
  r, g, b = (min(1.0, max(0.0, c)) for c in rgb)
  l_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return (
      0.2104542553 * l_ + 0.7936177850 * m - 0.0040720468 * s,
      1.9779984951 * l_ - 2.4285922050 * m + 0.4505937099 * s,
      0.0259040371 * l_ + 0.7827717662 * m - 0.8086757660 * s,
  )


def _simulate(rgb, matrix):
  return tuple(
      sum(w * c for w, c in zip(row, rgb, strict=True)) for row in matrix)


def check_palette() -> bool:
  """Print the OKLab separation of every pair of series colours, in normal
  vision and under simulated colour-vision deficiency. Any two series can
  sit side by side in these figures, so every pair is checked."""
  names = {"blue": BLUE, "orange": ORANGE, "aqua": AQUA}
  keys = list(names)
  ok = True
  print(f"palette separation (OKLab dE x100; normal floor {NORMAL_FLOOR:.0f}, "
        f"colour-vision-deficiency target {CVD_TARGET:.0f}):")
  for i, first in enumerate(keys):
    for second in keys[i + 1:]:
      a, b = _linear_rgb(names[first]), _linear_rgb(names[second])
      normal = 100 * math.dist(_oklab(a), _oklab(b))
      worst = min((
          100 *
          math.dist(_oklab(_simulate(a, matrix)), _oklab(_simulate(b, matrix))),
          kind) for kind, matrix in _CVD.items())
      passed = normal >= NORMAL_FLOOR and worst[0] >= CVD_TARGET
      ok = ok and passed
      verdict = "PASS" if passed else "FAIL"
      print(f"  {first:6s} vs {second:6s}: normal {normal:5.1f}   "
            f"worst CVD {worst[0]:5.1f} ({worst[1]})   {verdict}")
  return ok


FIGURES = {
    "eval-levels": fig_levels,
    "eval-ks-vs-wasserstein": fig_ks_vs_wasserstein,
    "eval-ks-bracket": fig_ks_bracket,
    "eval-noise-floor": fig_noise_floor,
    "eval-baseline": fig_baseline,
    "eval-matched-n-entropy": fig_matched_n_entropy,
    "eval-rarefied-duplicates": fig_rarefied_duplicates,
    "eval-sets-rhe": fig_sets_rhe,
    "eval-memorization-lift": fig_memorization_lift,
    "eval-dcr-nndr": fig_dcr_nndr,
    "eval-holdout-dcr": fig_holdout_dcr,
    "eval-c2st": fig_c2st,
    "eval-fanout": fig_fanout,
    "eval-count-rule": fig_count_rule,
    "eval-cpu-budget": fig_cpu_budget,
}


def main(argv=None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
  parser.add_argument(
      "--only",
      action="append",
      choices=sorted(FIGURES),
      help="regenerate this figure only (repeatable)")
  args = parser.parse_args(argv)
  ASSETS.mkdir(parents=True, exist_ok=True)
  palette_ok = check_palette()
  names = args.only or list(FIGURES)
  for name in names:
    FIGURES[name]()
    print(f"  wrote {name}.png")
  print(f"\nwrote {len(names)} figure(s) to {ASSETS}")
  return 0 if palette_ok else 1


if __name__ == "__main__":
  sys.exit(main())
