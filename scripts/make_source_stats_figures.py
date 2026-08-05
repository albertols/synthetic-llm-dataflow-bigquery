"""Regenerate the source_table_stats concept figures (ADR 0022).

    uv run --no-sync python3 scripts/make_source_stats_figures.py

Writes PNGs into docs/designs/assets/. These are CONCEPT figures (see the
visual-first-documentation skill): they demonstrate the mathematics behind
the ADR 0022 design decisions with seeded, deterministic synthetic data —
no measured run numbers. Evidence numbers live with their own docs
(five-run postmortem, crosscheck); the four claims here are:

  1. stats-entropy-skew.png     — distinct count cannot tell a balanced
     enum from a collapsed one; entropy and top1_share can.
  2. stats-inverse-cdf.png      — uniform-in-range flattens a skewed
     marginal; inverse transform through 11 deciles preserves it.
  3. stats-epoch-deciles.png    — decile spacing adapts to burst density,
     so sampled instants land where the source's did.
  4. stats-null-patterns.png    — independent per-column null draws invent
     ghost patterns and starve real joint sparsity.

Palette matches the WS5/WS6 assets (scripts/make_ws6_figures.py) so the
design-doc set reads as one system; the same OKLab separation check runs
on every regeneration. Color follows the entity across all four figures:
BLUE = source truth, ORANGE = the naive/degenerate model, AQUA = the
stats-driven sampler.
"""

from __future__ import annotations

import calendar
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ASSETS = Path(__file__).resolve().parents[1] / "docs" / "designs" / "assets"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045

# --- CONCEPT (seeded, deterministic; parameters chosen to illustrate) -----
SEED = 7
# Fig 1 — two 4-category columns with IDENTICAL distinct counts. The
# balanced one is uniform; the collapsed one puts 85% on its mode (the
# shape of the "skewed copy pattern" ADR 0021 wants flagged for FKs).
P_BALANCED = (0.25, 0.25, 0.25, 0.25)
P_COLLAPSED = (0.85, 0.07, 0.05, 0.03)
# Fig 2 — right-skewed source marginal: lognormal(mu=3, sigma=0.9) is the
# canonical amounts-column shape (long right tail, mean >> median).
LOGNORM_MU, LOGNORM_SIGMA, N_SOURCE = 3.0, 0.9, 20_000
N_QUANTILE_POINTS = 11  # p0..p100 — mirrors ColumnProfile.quantiles
WORKED_U = 0.35  # the worked u -> F^-1(u) example in panel B
# Fig 3 — bursty event calendar: June carries ~55% of the year's events
# (relative month weights below), the rest tapers. 8k events.
MONTH_WEIGHTS = (1, 1, 2, 2, 3, 30, 6, 3, 2, 2, 1, 1)
N_EVENTS = 8_000
# Fig 4 — three columns where B and C are null TOGETHER (an optional
# block) and A is null only when everything is: three real row patterns.
NULL_PATTERNS_TRUE = {"000": 0.50, "011": 0.40, "111": 0.10}
# Independence-model mass above this on a never-observed pattern gets the
# "ghost" label in fig 4.
GHOST_LABEL_MIN_MASS = 0.01


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


def _entropy_bits(p) -> float:
    return -sum(x * math.log2(x) for x in p if x > 0)


# --------------------------------------------------------------------------
def fig_entropy_skew():
    """Distinct count cannot tell a balanced enum from a collapsed one."""
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.6), facecolor=SURFACE)
    cats = ["A", "B", "C", "D"]

    for ax, p, label in (
        (axes[0], P_BALANCED, "balanced"),
        (axes[1], P_COLLAPSED, "collapsed"),
    ):
        h = _entropy_bits(p)
        h_norm = h / math.log2(len(p))
        colors = [ORANGE if x == max(p) and label == "collapsed" else BLUE for x in p]
        ax.bar(cats, p, color=colors, width=0.62)
        _style(ax)
        _title(ax, f"{label} enum",
               f"distinct=4 in BOTH · H={h:.2f} bits · "
               f"H_norm={h_norm:.2f} · top1_share={max(p):.2f}")
        ax.set_ylim(0, 1.0)
        ax.set_ylabel("frequency", color=MUTED, fontsize=9)

    # Panel C: top1_share and normalized entropy are two views of one skew.
    tops = np.linspace(0.25, 0.97, 120)
    h_norm_curve = []
    for t in tops:
        rest = (1 - t) / 3
        h_norm_curve.append(_entropy_bits((t, rest, rest, rest)) / 2.0)
    ax = axes[2]
    ax.plot(tops, h_norm_curve, color=AQUA, linewidth=2)
    for p, color, label in ((P_BALANCED, BLUE, "balanced"),
                            (P_COLLAPSED, ORANGE, "collapsed")):
        x, y = max(p), _entropy_bits(p) / 2.0
        ax.plot([x], [y], "o", color=color, markersize=8)
        ax.annotate(label, (x, y), textcoords="offset points", xytext=(8, 6),
                    color=INK, fontsize=9)
    _style(ax, grid_axis="both")
    _title(ax, "one skew, two views",
           "4-category family, remaining mass split evenly")
    ax.set_xlabel("top1_share", color=MUTED, fontsize=9)
    ax.set_ylabel("normalized entropy", color=MUTED, fontsize=9)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "stats-entropy-skew.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_inverse_cdf():
    """Uniform-in-range flattens a skewed marginal; 11 deciles preserve it."""
    rng = np.random.default_rng(SEED)
    source = rng.lognormal(LOGNORM_MU, LOGNORM_SIGMA, N_SOURCE)
    grid = np.linspace(0.0, 1.0, N_QUANTILE_POINTS)
    deciles = np.quantile(source, grid)
    inv_draws = np.interp(rng.random(N_SOURCE), grid, deciles)
    uni_draws = rng.uniform(source.min(), source.max(), N_SOURCE)
    xmax = float(np.quantile(source, 0.995))

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.6), facecolor=SURFACE)

    ax = axes[0]
    ax.hist(source, bins=80, range=(0, xmax), color=BLUE, density=True)
    med, p90 = float(np.median(source)), float(np.quantile(source, 0.9))
    for x, name in ((med, "p50"), (p90, "p90")):
        ax.axvline(x, color=INK, linewidth=1, linestyle=":", alpha=0.7)
        ax.text(x, ax.get_ylim()[1] * 0.92, f" {name}", color=INK, fontsize=9)
    _style(ax)
    _title(ax, "a skewed source marginal",
           "lognormal amounts column: mean >> median, long right tail")
    ax.set_xlabel("value", color=MUTED, fontsize=9)

    ax = axes[1]
    xs = np.sort(source)
    ax.plot(xs, np.arange(1, xs.size + 1) / xs.size, color=BLUE, linewidth=2,
            label="empirical CDF")
    ax.plot(deciles, grid, color=AQUA, linewidth=1.6, linestyle="--",
            label="11-point interpolation")
    ax.plot(deciles, grid, "o", color=AQUA, markersize=5)
    x0 = float(np.interp(WORKED_U, grid, deciles))
    ax.annotate("", xy=(x0, WORKED_U), xytext=(0, WORKED_U),
                arrowprops={"arrowstyle": "->", "color": ORANGE, "lw": 1.6})
    ax.annotate("", xy=(x0, 0.0), xytext=(x0, WORKED_U),
                arrowprops={"arrowstyle": "->", "color": ORANGE, "lw": 1.6})
    ax.text(x0, WORKED_U + 0.03, f"  u={WORKED_U} → F⁻¹(u)={x0:.0f}",
            color=INK, fontsize=9)
    ax.set_xlim(0, xmax)
    # The p90->p100 segment visibly departs from the true CDF: that IS the
    # approximation cost of 11 points — annotate it as a teaching point,
    # not an artifact.
    ax.text(float(deciles[9]) * 1.35, 0.74,
            "p90→p100 linearized:\nthe cost of 11 points",
            color=MUTED, fontsize=8.5)
    _style(ax, grid_axis="both")
    _title(ax, "inverse transform sampling",
           "uniform u on the y-axis maps through the decile vector to x")
    ax.set_xlabel("value", color=MUTED, fontsize=9)
    ax.set_ylabel("F(x)", color=MUTED, fontsize=9)
    ax.legend(loc="lower right", fontsize=8, frameon=False, labelcolor=INK)

    ax = axes[2]
    bins = np.linspace(0, xmax, 70)
    ax.hist(source, bins=bins, color=BLUE, density=True, alpha=0.35,
            label="source")
    ax.hist(inv_draws, bins=bins, histtype="step", color=AQUA, linewidth=2,
            density=True, label="inverse-CDF draws")
    ax.hist(uni_draws, bins=bins, histtype="step", color=ORANGE, linewidth=2,
            density=True, label="uniform(min, max)")
    _style(ax)
    _title(ax, "what the landing table sees",
           "uniform flattens the marginal; deciles keep the body")
    ax.set_xlabel("value", color=MUTED, fontsize=9)
    ax.legend(loc="upper right", fontsize=8, frameon=False, labelcolor=INK)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "stats-inverse-cdf.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_epoch_deciles():
    """Decile spacing adapts to burst density on the time axis."""
    rng = np.random.default_rng(SEED)
    weights = np.asarray(MONTH_WEIGHTS, dtype=float)
    weights /= weights.sum()
    month_starts = np.cumsum([0] + [calendar.monthrange(2026, m)[1]
                                    for m in range(1, 13)])
    months = rng.choice(12, size=N_EVENTS, p=weights)
    days = month_starts[months] + rng.uniform(
        0, month_starts[months + 1] - month_starts[months])
    grid = np.linspace(0.0, 1.0, N_QUANTILE_POINTS)
    deciles = np.quantile(days, grid)
    inv_draws = np.interp(rng.random(N_EVENTS), grid, deciles)
    uni_draws = rng.uniform(days.min(), days.max(), N_EVENTS)

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 4.6), facecolor=SURFACE)
    mid = (month_starts[:-1] + month_starts[1:]) / 2
    labels = [calendar.month_abbr[m] for m in range(1, 13)]

    ax = axes[0]
    ax.hist(days, bins=52, color=BLUE, density=True)
    for q in deciles:
        ax.axvline(q, color=AQUA, linewidth=1.4, alpha=0.9)
    _style(ax)
    _title(ax, "bursty timestamps, adaptive deciles",
           "aqua lines: the 11-point epoch decile vector — crowded where "
           "events cluster")
    ax.set_xticks(mid, labels)
    ax.set_ylabel("event density", color=MUTED, fontsize=9)

    ax = axes[1]
    src_m = np.histogram(days, bins=month_starts)[0] / N_EVENTS
    inv_m = np.histogram(inv_draws, bins=month_starts)[0] / N_EVENTS
    uni_m = np.histogram(uni_draws, bins=month_starts)[0] / N_EVENTS
    x = np.arange(12)
    ax.bar(x, src_m, width=0.8, color=BLUE, alpha=0.35, label="source")
    ax.step(x - 0.5, np.append(inv_m, inv_m[-1])[:12], where="post",
            color=AQUA, linewidth=2, label="inverse-CDF draws")
    ax.step(x - 0.5, np.append(uni_m, uni_m[-1])[:12], where="post",
            color=ORANGE, linewidth=2, label="uniform(min, max)")
    _style(ax)
    _title(ax, "monthly mass after sampling",
           "novel instants, source-shaped density — uniform spreads the burst")
    ax.set_xticks(x, labels)
    ax.set_ylabel("fraction of events", color=MUTED, fontsize=9)
    ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=INK)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "stats-epoch-deciles.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_null_patterns():
    """Independent per-column null draws invent ghost patterns."""
    marginals = [
        sum(f for p, f in NULL_PATTERNS_TRUE.items() if p[i] == "1")
        for i in range(3)
    ]
    patterns = [f"{i:03b}" for i in range(8)]
    observed = [NULL_PATTERNS_TRUE.get(p, 0.0) for p in patterns]
    independent = [
        math.prod(m if b == "1" else 1 - m for b, m in zip(p, marginals, strict=True))
        for p in patterns
    ]

    fig, ax = plt.subplots(figsize=(11.5, 4.8), facecolor=SURFACE)
    x = np.arange(8)
    ax.bar(x - 0.2, observed, width=0.38, color=BLUE, label="observed rows")
    ax.bar(x + 0.2, independent, width=0.38, color=ORANGE,
           label="independent per-column draws")
    for i, (o, e) in enumerate(zip(observed, independent, strict=True)):
        if o == 0 and e > GHOST_LABEL_MIN_MASS:
            ax.text(i + 0.2, e + 0.012, "ghost", ha="center", color=ORANGE,
                    fontsize=9, fontweight="600")
    _style(ax)
    _title(ax, "null patterns: joint truth vs independence",
           "columns B,C are null together (an optional block); per-column "
           "rates alone invent rows that never occur")
    ax.set_xticks(x, patterns)
    ax.set_xlabel("is-null bitstring over columns (A,B,C)", color=MUTED,
                  fontsize=9)
    ax.set_ylabel("fraction of rows", color=MUTED, fontsize=9)
    ax.legend(loc="upper right", fontsize=9, frameon=False, labelcolor=INK)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "stats-null-patterns.png", dpi=160, facecolor=SURFACE)
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
    fig_entropy_skew()
    fig_inverse_cdf()
    fig_epoch_deciles()
    fig_null_patterns()
    print(f"\nwrote 4 figures to {ASSETS}")
