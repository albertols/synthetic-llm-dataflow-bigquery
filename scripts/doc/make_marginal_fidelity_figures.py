"""Regenerate the marginal-fidelity wave-3 figures (ADR 0025).

    uv run --no-sync python3 scripts/doc/make_marginal_fidelity_figures.py

Writes PNGs into docs/designs/assets/. Two EVIDENCE figures carry the
2026-08-11 R1-pair measurements (the only place those numbers are typed);
two CONCEPT figures teach the mechanisms with seeded synthetic data (no
measured numbers). Claims:

  1. marginal-wave3-evidence.png       — at R1, every failing marginal
     traced to the same two samplers: 22 numeric columns at decile-KS
     0.20-0.90 and the top categorical enums at entropy gaps to -0.82.
  2. marginal-wave3-structure-loss.png — distinct-weighted shape mixes
     inverted row-mass marginals, and identifier masks lost their
     positional structure.
  3. marginal-wave3-invcdf-band.png    — a value-average convolution fills
     a banded domain with mass the source does not have; inverse transform
     sampling reproduces the band and gives the outlier its true share.
  4. marginal-wave3-positional.png     — per-position evidence pins
     structural literals (fixed prefixes, UUIDv4 nibbles) that column-wide
     class alphabets scramble.

Palette matches the design-doc asset set (make_source_stats_figures.py):
BLUE = source truth, ORANGE = the defective R1 sampler, AQUA = the wave-3
sampler. The OKLab separation check runs on every regeneration.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "designs" / "assets"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045

# --- MEASURED (2026-08-11 R1 pair; the only place these numbers are typed).
# Source: runs/2026-08-11_04_07_50-11228952274629556592 (A_TABLE)
#     and runs/2026-08-11_06_04_05-9010984585999295806 (B_TABLE),
# oss-redacted stats_diff.md / freetext_crosscheck_report.md tables.
RUN_A = "2026-08-11_04_07_50-11228…"  # A_TABLE R1 cold, 1M rows
RUN_B = "2026-08-11_06_04_05-9010…"  # B_TABLE R1 cold, 1M rows
KS_WARN, KS_FAIL = 0.2, 0.4  # numeric.decile_ks rule bands (thresholds.yml)

# stats_diff "Ranked columns": every decile-KS at or above the warn band.
KS_A = {
    "COL_016": 0.900, "COL_017": 0.900, "COL_049": 0.900, "COL_039": 0.900,
    "COL_009": 0.814, "COL_060": 0.801, "COL_047": 0.798, "COL_057": 0.711,
    "COL_007": 0.534, "COL_008": 0.531, "COL_036": 0.449, "COL_020": 0.443,
    "COL_023": 0.441, "COL_059": 0.402, "COL_015": 0.400, "COL_041": 0.400,
    "COL_014": 0.200,
}
KS_B = {
    "COL_009": 0.900, "COL_027": 0.900, "COL_017": 0.899, "COL_032": 0.807,
    "COL_003": 0.804, "COL_022": 0.801, "COL_030": 0.666, "COL_002": 0.509,
}
# stats_diff entropy_gap on categorical-routed columns (verdict "ok" — no
# rule scored them; the gap is the flattening signature). Top 8 per table.
ENTROPY_A = {
    "COL_040": -0.815, "COL_013": -0.803, "COL_056": -0.801,
    "COL_041": -0.765, "COL_043": -0.748, "COL_015": -0.729,
    "COL_067": -0.535, "COL_066": -0.527,
}
ENTROPY_B = {
    "COL_010": -0.816, "COL_028": -0.804, "COL_020": -0.699,
    "COL_029": -0.697, "COL_021": -0.683, "COL_044": -0.508,
    "COL_023": -0.508, "COL_045": -0.504,
}
# freetext_crosscheck top-shape shares (% of sampled rows): the row-mass
# inversion triple. (column, table, mask, source %, synthetic %).
SHAPE_INVERSIONS = (
    ("COL_054 (A)", "AAAAA", 81.0, 53.0),
    ("COL_024 (B)", "99", 31.9, 91.9),
    ("COL_024 (B)", "9x16", 68.1, 7.9),
    ("COL_015 (B)", "alpha (spurious)", 0.0, 47.8),
)
# Identifier structure loss (crosscheck executive summary).
IDENTIFIER_METRICS = (
    ("COL_001 (A)\nshape recall", 0.38),
    ("COL_064 (A)\nshape recall", 0.01),
    ("COL_064 (A)\nshape precision", 0.10),
)

# --- CONCEPT (seeded, deterministic; parameters chosen to illustrate) -----
SEED = 11
# COL_009-class banded domain: a 10-digit keyspace where every substantive
# value opens with "40" (band [4.0e9, 4.1e9]) plus ONE low outlier — the
# smallest structure that shows the convolution failure.
BAND_LO, BAND_HI = 4.0e9, 4.1e9
OUTLIER = 1.0e9
N_BAND, N_DRAWS = 999, 20_000
SIMILARITY = 0.5  # the pipeline default both R1 runs used
# Positional concept: E2F3-prefixed 24-char upper-hex ids (COL_001-class)
# and RFC 4122 v4 UUIDs (COL_064-class) — enough values that every free
# position saturates its alphabet, so pinned positions stand out. Positions
# with at most NARROW_MAX observed chars get their charset annotated.
N_IDS = 400
NARROW_MAX = 4


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


# --------------------------------------------------------------------------
def fig_evidence():
    """22 numeric decile-KS failures + categorical entropy flattening."""
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.2), facecolor=SURFACE)

    for ax, ks, run, table in (
        (axes[0][0], KS_A, RUN_A, "A_TABLE"),
        (axes[0][1], KS_B, RUN_B, "B_TABLE"),
    ):
        cols = list(ks)
        vals = [ks[c] for c in cols]
        colors = [ORANGE if v > KS_FAIL else MUTED for v in vals]
        y = np.arange(len(cols))[::-1]
        ax.barh(y, vals, color=colors, height=0.62)
        ax.set_yticks(y, cols, fontsize=8, color=MUTED)
        for gate, label in ((KS_WARN, "warn 0.2"), (KS_FAIL, "fail 0.4")):
            ax.axvline(gate, color=INK, linewidth=1, linestyle=":", alpha=0.6)
            ax.text(gate + 0.01, 0.02, label, color=INK, fontsize=8,
                    ha="left", va="bottom", rotation=0,
                    transform=ax.get_xaxis_transform())
        _style(ax, grid_axis="x")
        n_fail = sum(1 for v in vals if v > KS_FAIL)
        _title(ax, f"{table} R1 — numeric decile-KS",
               f"job {run} · {n_fail} fail / {len(vals) - n_fail} warn · "
               f"anchored+uniform value-average sampler")
        ax.set_xlim(0, 1.0)

    for ax, gaps, table in (
        (axes[1][0], ENTROPY_A, "A_TABLE"),
        (axes[1][1], ENTROPY_B, "B_TABLE"),
    ):
        cols = list(gaps)
        vals = [gaps[c] for c in cols]
        y = np.arange(len(cols))[::-1]
        ax.barh(y, vals, color=ORANGE, height=0.62)
        ax.set_yticks(y, cols, fontsize=8, color=MUTED)
        ax.axvline(0, color=INK, linewidth=1)
        _style(ax, grid_axis="x")
        _title(ax, f"{table} R1 — categorical entropy gap",
               "top 8 · negative = synthetic MORE uniform than source · "
               "no rule fired")
        ax.set_xlim(-1.0, 0.05)
        ax.set_xlabel("entropy gap (source - synthetic, normalized)",
                      color=MUTED, fontsize=9)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "marginal-wave3-evidence.png", dpi=160,
                facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_structure_loss():
    """Row-mass inversion + identifier positional-structure loss."""
    fig, axes = plt.subplots(
        1, 2, figsize=(13.5, 4.8), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [3, 2]},
    )

    ax = axes[0]
    labels = [f"{col}\n{mask}" for col, mask, _, _ in SHAPE_INVERSIONS]
    src = [s for _, _, s, _ in SHAPE_INVERSIONS]
    syn = [s for _, _, _, s in SHAPE_INVERSIONS]
    x = np.arange(len(labels))
    w = 0.36
    ax.bar(x - w / 2, src, width=w, color=BLUE, label="source rows")
    ax.bar(x + w / 2, syn, width=w, color=ORANGE, label="synthetic R1 rows")
    for xi, (s, t) in enumerate(zip(src, syn, strict=True)):
        ax.text(xi - w / 2, s + 1.5, f"{s:.0f}", ha="center", color=INK, fontsize=8)
        ax.text(xi + w / 2, t + 1.5, f"{t:.0f}", ha="center", color=INK, fontsize=8)
    ax.set_xticks(x, labels, fontsize=8.5, color=MUTED)
    _style(ax)
    _title(ax, "shape-share inversion (crosscheck, % of sampled rows)",
           "shape_mix weighted masks by DISTINCT values, not rows — heavy "
           "repeated heads lost to diverse tails")
    ax.set_ylim(0, 100)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK)

    ax = axes[1]
    labels = [m for m, _ in IDENTIFIER_METRICS]
    vals = [v for _, v in IDENTIFIER_METRICS]
    x = np.arange(len(labels))
    ax.bar(x, vals, width=0.5, color=ORANGE)
    ax.axhline(1.0, color=BLUE, linewidth=1.4, linestyle="--")
    ax.text(len(labels) - 0.5, 1.02, "source parity", color=BLUE, fontsize=8.5,
            ha="right")
    for xi, v in enumerate(vals):
        ax.text(xi, v + 0.03, f"{v:.2f}", ha="center", color=INK, fontsize=9)
    ax.set_xticks(x, labels, fontsize=8.5, color=MUTED)
    _style(ax)
    _title(ax, "identifier structure loss (crosscheck)",
           "mask table filled from column-wide alphabets — fixed prefixes "
           "and v4 nibbles unreachable")
    ax.set_ylim(0, 1.15)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "marginal-wave3-structure-loss.png", dpi=160,
                facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_invcdf_band():
    """Convolution vs inverse transform on a banded numeric domain."""
    rng = np.random.default_rng(SEED)
    band = rng.uniform(BAND_LO, BAND_HI, size=N_BAND)
    obs = np.sort(np.append(band, OUTLIER))
    lo, hi = float(obs.min()), float(obs.max())

    # The replaced sampler: s*(anchored+jitter) + (1-s)*uniform, redraw OOB.
    s = SIMILARITY
    anchored = obs[rng.integers(0, obs.size, size=N_DRAWS)]
    jitter = rng.uniform(-0.5, 0.5, size=N_DRAWS) * (hi - lo) * (1 - s)
    uniform = rng.uniform(lo, hi, size=N_DRAWS)
    blended = s * (anchored + jitter) + (1 - s) * uniform
    oob = (blended < lo) | (blended > hi)
    blended[oob] = rng.uniform(lo, hi, size=int(oob.sum()))

    # The wave-3 sampler: inverse transform through the sorted sample.
    grid = np.linspace(0.0, 1.0, obs.size)
    invcdf = np.interp(rng.random(N_DRAWS), grid, obs)

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.4), facecolor=SURFACE,
                             sharey=True)
    bins = np.linspace(lo, hi, 80)
    panels = (
        (axes[0], obs, BLUE, "source sample",
         "every substantive value in the 40xx band + one outlier"),
        (axes[1], blended, ORANGE, "value-average blend (replaced)",
         f"s·(anchor+jitter) + (1-s)·uniform at s={s} — a convolution"),
        (axes[2], invcdf, AQUA, "inverse transform (wave 3)",
         "u~U(0,1) through the sorted sample — Devroye 1986 ch. II"),
    )
    for ax, data, color, title, sub in panels:
        ax.hist(data, bins=bins, color=color)
        ax.axvspan(BAND_LO, BAND_HI, color=BLUE, alpha=0.07, zorder=0)
        _style(ax)
        _title(ax, title, sub)
        ax.set_xticks(
            [1.0e9, 2.0e9, 3.0e9, 4.05e9],
            ["1.0e9", "2.0e9", "3.0e9", "40… band"],
            fontsize=8, color=MUTED,
        )
        in_band = float(((data >= BAND_LO) & (data <= BAND_HI)).mean())
        ax.text(0.02, 0.94, f"in-band mass: {in_band:.0%}",
                transform=ax.transAxes, color=INK, fontsize=9.5, va="top")
        ax.set_yticks([])
    axes[0].set_ylabel("draws per bin", color=MUTED, fontsize=9)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "marginal-wave3-invcdf-band.png", dpi=160,
                facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_positional():
    """Per-position alphabets pin structural literals."""
    import uuid

    rng = random.Random(SEED)
    hex_ids = [
        "E2F3" + "".join(rng.choice("0123456789ABCDEF") for _ in range(20))
        for _ in range(N_IDS)
    ]
    uuids = [
        str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(N_IDS)
    ]

    fig, axes = plt.subplots(2, 1, figsize=(13.5, 5.6), facecolor=SURFACE)
    for ax, values, name, note in (
        (axes[0], hex_ids, "E2F3-prefixed 24-char upper-hex (COL_001-class)",
         "positions 0-3 observe ONE character each → pinned literals"),
        (axes[1], uuids, "RFC 4122 v4 UUID (COL_064-class)",
         "version nibble (pos 14) pins to '4'; variant (pos 19) narrows to 89ab"),
    ):
        length = len(values[0])
        sizes = [len({v[i] for v in values}) for i in range(length)]
        colors = [
            AQUA if s == 1 else BLUE if s <= NARROW_MAX else MUTED
            for s in sizes
        ]
        ax.bar(range(length), sizes, color=colors, width=0.7)
        for i, s in enumerate(sizes):
            if s <= NARROW_MAX:
                chars = "".join(sorted({v[i] for v in values}))
                ax.text(i, s + 0.4, chars, ha="center", color=INK, fontsize=8)
        ax.set_xticks(range(0, length, 2))
        _style(ax)
        _title(ax, name, note + " — column-wide alphabets see 16+ everywhere")
        ax.set_ylabel("observed chars\nat position", color=MUTED, fontsize=8.5)
        ax.set_ylim(0, 18)
    axes[1].set_xlabel("string position", color=MUTED, fontsize=9)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "marginal-wave3-positional.png", dpi=160,
                facecolor=SURFACE)
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
    fig_evidence()
    fig_structure_loss()
    fig_invcdf_band()
    fig_positional()
    print(f"\nwrote 4 figures to {ASSETS}")
