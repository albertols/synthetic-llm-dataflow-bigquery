"""Regenerate the evaluation-framework concept figures.

    uv run --no-sync python3 scripts/make_eval_figures.py

Writes PNGs into docs/designs/assets/. CONCEPT figures (see the
visual-first-documentation skill): seeded, deterministic synthetic data —
no measured run numbers. The two claims:

  1. eval-ks-vs-wasserstein.png — KS is the tallest vertical gap between
     two CDFs, Wasserstein-1 is the whole area between them: two failure
     modes with the SAME Wasserstein distance can differ 5x in KS.
  2. eval-dcr-nndr.png — DCR flags a synthetic row parked on a real
     record; NNDR flags a row for which ONE real record is uniquely
     closest. They catch different privacy failures.

Palette matches the repo asset set (scripts/make_ws6_figures.py); the same
OKLab separation check runs on every regeneration. Color follows the
entity: BLUE = real/reference, ORANGE = the risky/degenerate case,
AQUA = the safe/derived quantity.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import ks_2samp, wasserstein_distance

ASSETS = Path(__file__).resolve().parents[1] / "docs" / "designs" / "assets"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045

# --- CONCEPT (seeded, deterministic; parameters chosen to illustrate) -----
SEED = 7
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
SYN_MEMORIZED_OFFSET = 0.05   # sits (almost) on top of a real record
SYN_REIDENT = (9.1, 1.35)     # unambiguously closest to the isolated record
SYN_SAFE = (4.3, 2.8)         # between the clusters, d1 ~= d2


def _style(ax, *, grid_axis="y"):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)


def _title(ax, text, sub=None):
    ax.set_title(text, color=INK, fontsize=12.5, fontweight="600", loc="left", pad=26)
    if sub:
        ax.text(0, 1.025, sub, transform=ax.transAxes, color=MUTED, fontsize=9,
                va="bottom")


def _ecdf(sample: np.ndarray, grid: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.sort(sample), grid, side="right") / sample.size


# --------------------------------------------------------------------------
def fig_ks_vs_wasserstein():
    """Same W1, 5x different KS — the two statistics see different failures."""
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
        ks = ks_2samp(ref, synth).statistic
        w1 = wasserstein_distance(ref, synth)
        grid = np.linspace(
            min(ref.min(), synth.min()), max(ref.max(), synth.max()), 800
        )
        f_ref, f_syn = _ecdf(ref, grid), _ecdf(synth, grid)
        ax.fill_betweenx(  # area between the CDFs, drawn horizontally = W1
            np.linspace(0, 1, grid.size), grid, grid, alpha=0.0
        )
        ax.fill_between(grid, f_ref, f_syn, color=AQUA, alpha=0.30,
                        label="area between CDFs = W₁")
        ax.plot(grid, f_ref, color=BLUE, linewidth=2, label="real CDF")
        ax.plot(grid, f_syn, color=ORANGE, linewidth=2, label="synthetic CDF")
        i = int(np.argmax(np.abs(f_ref - f_syn)))
        ax.plot([grid[i], grid[i]], [min(f_ref[i], f_syn[i]), max(f_ref[i], f_syn[i])],
                color=INK, linewidth=2.5)
        ax.annotate(f"KS = {ks:.2f}", (grid[i], (f_ref[i] + f_syn[i]) / 2),
                    textcoords="offset points", xytext=(10, 0), color=INK,
                    fontsize=10, fontweight="600")
        _style(ax, grid_axis="both")
        _title(ax, label, f"W1 = {w1:.1f} in BOTH panels - KS differs 5x")
        ax.set_xlabel("value", color=MUTED, fontsize=9)
        ax.set_ylabel("F(x)", color=MUTED, fontsize=9)
        ax.legend(loc="lower right", fontsize=8, frameon=False, labelcolor=INK)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "eval-ks-vs-wasserstein.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


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
    """DCR catches the parked copy; NNDR catches the unambiguous neighbor."""
    real = _real_points()
    syn_memorized = real[3] + SYN_MEMORIZED_OFFSET

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2), facecolor=SURFACE)

    for ax in axes:
        ax.scatter(real[:, 0], real[:, 1], s=42, color=BLUE, label="real rows",
                   zorder=3)
        _style(ax, grid_axis="both")
        ax.set_xlabel("feature 1 (Gower-embedded)", color=MUTED, fontsize=9)
        ax.set_ylabel("feature 2", color=MUTED, fontsize=9)
        ax.set_xlim(0, 11.5)
        ax.set_ylim(-0.5, 5.5)

    # Panel A — DCR: distance from each synthetic row to its closest real row.
    ax = axes[0]
    for point, color, name, offset, align in (
        (syn_memorized, ORANGE, "memorized copy", (-10, 16), "right"),
        (SYN_SAFE, AQUA, "novel row", (0, -34), "center"),
    ):
        i1, _, d1, _ = _nearest_two(point, real)
        ax.scatter(*point, marker="X", s=130, color=color, zorder=4,
                   edgecolors=SURFACE, linewidths=1.5)
        ax.plot([point[0], real[i1][0]], [point[1], real[i1][1]],
                color=color, linewidth=1.6, linestyle=":")
        ax.annotate(f"{name}\nDCR = {d1:.2f}", point,
                    textcoords="offset points", xytext=offset, ha=align,
                    color=INK, fontsize=9)
    _title(ax, "DCR — distance to closest record",
           "a synthetic row parked on a real row has DCR → 0 (memorization)")
    ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=INK)

    # Panel B — NNDR: d1/d2 over the same cloud.
    ax = axes[1]
    for point, color, name, offset, align in (
        (SYN_REIDENT, ORANGE, "re-identifying", (0, -36), "center"),
        (SYN_SAFE, AQUA, "safe", (-12, 14), "right"),
    ):
        i1, i2, d1, d2 = _nearest_two(point, real)
        ax.scatter(*point, marker="X", s=130, color=color, zorder=4,
                   edgecolors=SURFACE, linewidths=1.5)
        for j, style in ((i1, "-"), (i2, "--")):
            ax.plot([point[0], real[j][0]], [point[1], real[j][1]],
                    color=color, linewidth=1.6, linestyle=style)
        ax.annotate(f"{name}\nNNDR = d₁/d₂ = {d1 / d2:.2f}", point,
                    textcoords="offset points", xytext=offset, ha=align,
                    color=INK, fontsize=9)
    _title(ax, "NNDR — nearest-neighbor distance ratio",
           "d₁ ≪ d₂ ⇒ ONE real record is unambiguously closest "
           "(re-identification risk)")
    ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=INK)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "eval-dcr-nndr.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def _srgb_to_oklab(hexstr):
    r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def _lin(c):
        return c / 12.92 if c <= SRGB_CUTOFF else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = _lin(r), _lin(g), _lin(b)
    l_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l_ + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l_ - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l_ + 0.7827717662 * m - 0.8086757660 * s,
    )


def check_palette():
    names = {"blue": BLUE, "orange": ORANGE, "aqua": AQUA}
    ks = list(names)
    print(f"palette separation (OKLab dE x100, normal vision; floor = {CVD_FLOOR:.0f}):")
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            de = 100 * math.dist(_srgb_to_oklab(names[ks[i]]),
                                 _srgb_to_oklab(names[ks[j]]))
            print(f"  {ks[i]:6s} vs {ks[j]:6s}: {de:5.1f}  "
                  f"{'PASS' if de >= CVD_FLOOR else 'FAIL'}")


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    check_palette()
    fig_ks_vs_wasserstein()
    fig_dcr_nndr()
    print(f"\nwrote 2 figures to {ASSETS}")
