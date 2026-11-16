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
"""Regenerate the B.2 backend-assessment concept figure (design 2026-11-16).

    uv run --no-sync python3 scripts/doc/make_b2_library_figures.py

Writes docs/designs/assets/b2-library-dependence.png. Claim (CONCEPT —
seeded, deterministic, no measured numbers; the measured compatibility
table is printed by `scripts/doc/probe_sdgx_compat.py`):

  Sampling each column on its own keeps every marginal and loses the
  dependence between columns; a Gaussian copula keeps both.

  left   — a source whose two skewed columns move together.
  middle — each column drawn through its own inverse CDF from independent
           uniforms, which is what `EmpiricalBackend.sample_columns`
           (`sdfb_core/engines/b2_library/backends.py`) does today.
  right  — the same two inverse CDFs fed by CORRELATED uniforms from a
           Gaussian copula fitted on the source's normal scores: the
           assessment's option B, not implemented.

Each panel prints its rank correlation and its largest marginal
Kolmogorov-Smirnov distance to the source, both computed here from the
seeded draw.

Palette matches the design-doc asset set: BLUE = truth / target,
ORANGE = defect, AQUA = healthy. OKLab separation check runs on every
regeneration.
"""

# pyplot must be imported after matplotlib.use("Agg") selects the headless backend.
# pylint: disable=wrong-import-position

from __future__ import annotations

import math
from pathlib import Path
from statistics import NormalDist

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "designs" / "assets"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045

# --- CONCEPT (seeded; parameters chosen to show the mechanism, not a run).
CONCEPT_SEED = 7
# Enough points to read a cloud's shape, few enough to see single points.
POINTS = 1_200
# Latent correlation of the source: strong enough that losing it is
# visible at a glance, short of a line.
SOURCE_RHO = 0.8
# Two differently skewed marginals (a lognormal and an exponential), so
# the figure shows the copula keeps marginals that are not normal.
A_LOG_MEAN, A_LOG_SIGMA = 3.0, 0.5
B_SCALE = 20.0
# Axes stop at this quantile of the source so a few tail points do not
# flatten the cloud.
AXIS_QUANTILE = 0.995

_NORMAL = NormalDist()
_cdf = np.vectorize(_NORMAL.cdf)
_inv_cdf = np.vectorize(_NORMAL.inv_cdf)


def _correlated_normals(rng, rho: float, n: int) -> np.ndarray:
  z1 = rng.standard_normal(n)
  z2 = rho * z1 + math.sqrt(1 - rho * rho) * rng.standard_normal(n)
  return np.column_stack([z1, z2])


def source_sample(rng) -> np.ndarray:
  z = _correlated_normals(rng, SOURCE_RHO, POINTS)
  a = np.exp(A_LOG_MEAN + A_LOG_SIGMA * z[:, 0])
  b = -B_SCALE * np.log(1 - _cdf(z[:, 1]))
  return np.column_stack([a, b])


def _through_marginals(source: np.ndarray, uniforms: np.ndarray) -> np.ndarray:
  """Each uniform column through its source column's empirical inverse CDF."""
  return np.column_stack(
      [np.quantile(source[:, j], uniforms[:, j]) for j in range(2)])


def independent_sample(rng, source: np.ndarray) -> np.ndarray:
  return _through_marginals(source, rng.random((POINTS, 2)))


def copula_sample(rng, source: np.ndarray) -> np.ndarray:
  """Fit a Gaussian copula on the source's normal scores, then sample."""
  ranks = source.argsort(axis=0).argsort(axis=0) + 1
  scores = _inv_cdf(ranks / (POINTS + 1))
  rho = float(np.corrcoef(scores, rowvar=False)[0, 1])
  return _through_marginals(source, _cdf(_correlated_normals(rng, rho, POINTS)))


def rank_correlation(sample: np.ndarray) -> float:
  ranks = sample.argsort(axis=0).argsort(axis=0)
  return float(np.corrcoef(ranks, rowvar=False)[0, 1])


def _ks(left: np.ndarray, right: np.ndarray) -> float:
  grid = np.sort(np.concatenate([left, right]))
  cdf_left = np.searchsorted(np.sort(left), grid, side="right") / len(left)
  cdf_right = np.searchsorted(np.sort(right), grid, side="right") / len(right)
  return float(np.abs(cdf_left - cdf_right).max())


def marginal_distance(sample: np.ndarray, source: np.ndarray) -> float:
  return max(_ks(sample[:, j], source[:, j]) for j in range(2))


def _style(ax):
  ax.set_facecolor(SURFACE)
  for side in ("top", "right"):
    ax.spines[side].set_visible(False)
  for side in ("left", "bottom"):
    ax.spines[side].set_color(GRID)
  ax.tick_params(colors=MUTED, labelsize=9, length=0)
  ax.grid(color=GRID, linewidth=0.8, alpha=0.9)
  ax.set_axisbelow(True)


def _title(ax, text, sub):
  ax.set_title(
      text, color=INK, fontsize=12.5, fontweight="600", loc="left", pad=30)
  ax.text(
      0,
      1.03,
      sub,
      transform=ax.transAxes,
      color=MUTED,
      fontsize=9,
      va="bottom")


def fig_dependence():
  rng = np.random.default_rng(CONCEPT_SEED)
  source = source_sample(rng)
  panels = (
      ("Source", "two skewed columns that move together", BLUE, source, None),
      ("Each column on its own", "EmpiricalBackend today: dependence lost",
       ORANGE, independent_sample(rng, source), source),
      ("Gaussian copula", "option B, not implemented: dependence kept", AQUA,
       copula_sample(rng, source), source),
  )
  limits = np.quantile(source, AXIS_QUANTILE, axis=0)
  fig, axes = plt.subplots(1, 3, figsize=(13.6, 4.7), sharex=True, sharey=True)
  fig.patch.set_facecolor(SURFACE)
  for ax, (title, sub, color, sample, reference) in zip(
      axes, panels, strict=True):
    _style(ax)
    _title(ax, title, sub)
    ax.scatter(
        sample[:, 0],
        sample[:, 1],
        s=9,
        color=color,
        alpha=0.5,
        linewidths=0,
        zorder=3)
    lines = [f"rank correlation {rank_correlation(sample):.2f}"]
    if reference is not None:
      lines.append("marginals: KS distance to source "
                   f"{marginal_distance(sample, reference):.2f}")
    # Under the axis, not on the plot: a box inside would hide points.
    ax.text(
        0,
        -0.17,
        "\n".join(lines),
        transform=ax.transAxes,
        ha="left",
        va="top",
        color=INK,
        fontsize=10,
        linespacing=1.5)
    ax.set_xlim(0, limits[0])
    ax.set_ylim(0, limits[1])
    ax.set_xlabel("column A", color=MUTED, fontsize=9.5)
  axes[0].set_ylabel("column B", color=MUTED, fontsize=9.5)
  fig.tight_layout(w_pad=2.2)
  fig.savefig(ASSETS / "b2-library-dependence.png", dpi=170)
  plt.close(fig)


def _srgb_to_oklab(hexstr):
  r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))

  def lin(u):
    return u / 12.92 if u <= SRGB_CUTOFF else ((u + 0.055) / 1.055)**2.4

  r, g, b = lin(r), lin(g), lin(b)
  l_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  s_ = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return (
      0.2104542553 * l_ + 0.7936177850 * m - 0.0040720468 * s_,
      1.9779984951 * l_ - 2.4285922050 * m + 0.4505937099 * s_,
      0.0259040371 * l_ + 0.7827717662 * m - 0.8086757660 * s_,
  )


def _check_palette():
  names = {"BLUE": BLUE, "ORANGE": ORANGE, "AQUA": AQUA}
  ks = list(names)
  print(f"palette separation (OKLab dE x100, floor = {CVD_FLOOR:.0f}):")
  ok = True
  for i in range(len(ks)):
    for j in range(i + 1, len(ks)):
      de = 100 * math.dist(
          _srgb_to_oklab(names[ks[i]]), _srgb_to_oklab(names[ks[j]]))
      flag = "ok" if de >= CVD_FLOOR else "FAIL"
      ok = ok and de >= CVD_FLOOR
      print(f"  {ks[i]:>6} vs {ks[j]:<6} dE = {de:5.1f}  {flag}")
  if not ok:
    raise SystemExit("palette separation below floor")


def main() -> None:
  ASSETS.mkdir(parents=True, exist_ok=True)
  _check_palette()
  fig_dependence()
  print("wrote", ASSETS / "b2-library-dependence.png")


if __name__ == "__main__":
  main()
