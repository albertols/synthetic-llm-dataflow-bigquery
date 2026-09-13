"""Regenerate the parent-driven fan-out concept figure (design 2026-09-10).

    uv run --no-sync python3 scripts/doc/make_fanout_figures.py

Writes docs/designs/assets/fanout-generation.png. Claims (CONCEPT —
seeded, deterministic, no measured numbers; the two measured runs live in
`make_pk_capacity_figures.py`):

  left  — a child's size is not a free parameter: parents x the source
          fan-out histogram (zero bucket included) IS the child's row
          count, and the histogram is what a `--num_rows` on the child
          cannot express.
  right — inside one parent key, drawing the PK-completing cells at
          random collides as balls-into-bins (k children into C cells:
          1 - C/k (1 - e^(-k/C)) duplicates); drawing them WITHOUT
          replacement never collides and is exactly what the source
          PK promises (k <= C).

Palette matches the design-doc asset set: BLUE = truth / target,
ORANGE = defect, AQUA = healthy. OKLab separation check runs on every
regeneration.
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

# --- CONCEPT (seeded; parameters chosen to show the mechanism, not a run).
CONCEPT_SEED = 7
# A skewed fan-out: many parents with 0-2 children, a long tail — the
# shape of an account -> movements relationship. Weights over k = 0..12.
FANOUT_WEIGHTS = [18, 30, 20, 11, 7, 5, 3, 2, 1.5, 1, 0.7, 0.5, 0.3]
PARENTS = 1_000  # illustrative parent count for the left panel
CELLS = 12       # PK-completing joint cells available per key (C)


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
    ax.set_title(text, color=INK, fontsize=12.5, fontweight="600",
                 loc="left", pad=30)
    if sub:
        ax.text(0, 1.03, sub, transform=ax.transAxes, color=MUTED,
                fontsize=9, va="bottom")


def panel_fanout(ax):
    rng = random.Random(CONCEPT_SEED)
    ks = list(range(len(FANOUT_WEIGHTS)))
    total = sum(FANOUT_WEIGHTS)
    probs = [w / total for w in FANOUT_WEIGHTS]
    drawn = rng.choices(ks, weights=probs, k=PARENTS)
    counts = [drawn.count(k) for k in ks]
    colors = [ORANGE if k == 0 else BLUE for k in ks]
    ax.bar(ks, counts, width=0.72, color=colors, zorder=3)
    mean_k = sum(drawn) / PARENTS
    children = sum(drawn)
    ax.text(0.55, counts[0] + 14, "zero bucket:\nparents with\nno children",
            ha="left", color=ORANGE, fontsize=8.6)
    ax.axvline(mean_k, color=INK, linewidth=1.2, linestyle="--")
    ax.text(mean_k + 0.2, max(counts) * 0.92,
            f"mean fan-out {mean_k:.2f}\n{PARENTS:,} parents x {mean_k:.2f}\n"
            f"= {children:,} child rows", color=INK, fontsize=9)
    ax.set_xticks(ks)
    ax.set_xlabel("children per parent key (k, source histogram)",
                  color=MUTED, fontsize=9.5)
    ax.set_ylabel("parent keys", color=MUTED, fontsize=9.5)
    _style(ax)
    _title(ax, "The child's size is the parent's fan-out",
           "seeded draw of 1,000 parents from a skewed source histogram; "
           "rows = parents x mean k, zero bucket included")


def panel_cells(ax):
    ks = np.arange(1, CELLS + 1)
    random_dup = [1 - (CELLS / k) * (1 - math.exp(-k / CELLS)) for k in ks]
    ax.plot(ks, random_dup, color=ORANGE, linewidth=2, marker="o",
            markersize=5, label="random draw: 1 - C/k (1 - e^(-k/C))")
    ax.plot(ks, [0.0] * len(ks), color=AQUA, linewidth=2.4, marker="o",
            markersize=5, label="without replacement (k <= C): 0")
    ax.axvline(CELLS, color=BLUE, linewidth=1.2, linestyle="--")
    ax.text(CELLS - 0.15, 0.5, f"C = {CELLS} cells\n(sample's joint\n"
            f"(C_COL_002, D_COL_018))", ha="right", color=BLUE, fontsize=9)
    ax.annotate(f"k = 6 of 12 cells:\n{random_dup[5]:.0%} duplicates at random",
                (6, random_dup[5]), xytext=(1.3, 0.38), color=INK, fontsize=9,
                arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})
    ax.set_ylim(-0.02, 0.62)
    ax.set_yticks([0, 0.2, 0.4, 0.6])
    ax.set_yticklabels([f"{t:.0%}" for t in ax.get_yticks()])
    ax.set_xticks(ks)
    ax.set_xlabel("children of one parent key (k)", color=MUTED, fontsize=9.5)
    ax.set_ylabel("expected pk.duplicate share inside the key",
                  color=MUTED, fontsize=9.5)
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK)
    _style(ax)
    _title(ax, "Inside a key: draw the PK cells without replacement",
           "the source PK guarantees k <= C; without replacement, the "
           "synthetic PK is unique by construction")


def fig_fanout():
    fig, axes = plt.subplots(
        1, 2, figsize=(14.6, 5.6), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [3, 2.6]},
    )
    panel_fanout(axes[0])
    panel_cells(axes[1])
    fig.tight_layout(w_pad=3)
    fig.savefig(ASSETS / "fanout-generation.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def _srgb_to_oklab(hexstr):
    r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(u):
        return u / 12.92 if u <= SRGB_CUTOFF else ((u + 0.055) / 1.055) ** 2.4

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
            de = 100 * math.dist(_srgb_to_oklab(names[ks[i]]),
                                 _srgb_to_oklab(names[ks[j]]))
            flag = "ok" if de >= CVD_FLOOR else "FAIL"
            ok = ok and de >= CVD_FLOOR
            print(f"  {ks[i]:>6} vs {ks[j]:<6} dE = {de:5.1f}  {flag}")
    if not ok:
        raise SystemExit("palette separation below floor")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    _check_palette()
    fig_fanout()
    print("wrote", ASSETS / "fanout-generation.png")


if __name__ == "__main__":
    main()
