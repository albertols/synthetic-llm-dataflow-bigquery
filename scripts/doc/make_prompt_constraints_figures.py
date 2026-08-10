"""Regenerate the wave-2 prompt-constraints figures.

    uv run --no-sync python3 scripts/doc/make_prompt_constraints_figures.py

Writes PNGs into docs/designs/assets/ for
docs/designs/2026-08-10-prompt-constraints.md.

  1. prompt-constraints-evidence.png (MEASURED) — the three 0%-recall
     columns fail through three different mechanisms while six more sit at
     the ~512 pool-cap diversity ceiling.
  2. prompt-constraints-mask-collapse.png (CONCEPT) — drawing a whole
     observed mask (text_shapes.py::sample_mask_table) reproduces the mask
     marginal by construction; the collapsed per-position template
     (text_shapes.py::sample_identifier) almost never does.

Palette matches the repo asset set (scripts/doc/make_eval_figures.py); the
same OKLab separation check runs on every regeneration. Color follows the
entity: BLUE = source/reference, ORANGE = the degenerate case,
AQUA = the fixed/derived quantity.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "designs" / "assets"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045

# --- MEASURED (freetext_crosscheck_metrics.json of the two 2026-08-09 R1
# jobs: A = 2026-08-09_00_19_04-7880358512029555343 (A_TABLE),
# B = 2026-08-09_00_31_21-17185878817912958022 (B_TABLE)) -----------------
# column → (table, shape_recall, synthetic_distinct, source_distinct)
RECALL_FAILERS = {
    "COL_001": ("A", 0.0048, 1_000_000, 52_549),
    "COL_048": ("A", 0.0031, 513, 19_815),
    "COL_037": ("B", 0.0000, 513, 73_231),
    "COL_038": ("B", 0.0000, 513, 146_046),
    "COL_019": ("B", 0.0823, 514, 144_039),
    "COL_018": ("B", 0.5930, 515, 45_708),
    "COL_015": ("B", 0.6821, 100, 4_022),
}
# The pool-cap-pinned columns (synthetic distinct within a head of the
# 512-value LLM pool cap, source distinct 45k-146k).
CEILING = {
    "COL_048 (A)": (513, 19_815),
    "COL_037 (B)": (513, 73_231),
    "COL_038 (B)": (513, 146_046),
    "COL_019 (B)": (514, 144_039),
    "COL_024 (B)": (514, 49_050),
    "COL_026 (B)": (514, 76_936),
    "COL_018 (B)": (515, 45_708),
}
POOL_CAP = 512  # _FREE_TEXT_POOL_MAX, b1_rag/engine.py

# --- CONCEPT (seeded, deterministic; parameters chosen to illustrate) -----
# A COL_001-like mask family: literal 3-char prefix + 21 hex positions —
# enough distinct masks that the top-8 mix covers well under 50%, which is
# exactly the fallthrough the wave fixes. Draw counts sized so recall
# fractions are stable to +-1pp across regenerations of the same seed.
SEED = 11
FAMILY_VALUES = 400
DRAWS = 4_000


def _style(ax, *, grid_axis="x"):
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
                 loc="left", pad=26)
    if sub:
        ax.text(0, 1.03, sub, transform=ax.transAxes, color=MUTED,
                fontsize=9, va="bottom")


# --------------------------------------------------------------------------
def fig_evidence():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 4.6))
    fig.patch.set_facecolor(SURFACE)

    cols = list(RECALL_FAILERS)
    recalls = [RECALL_FAILERS[c][1] for c in cols]
    y = range(len(cols))
    ax1.barh(y, recalls, height=0.62, color=ORANGE, zorder=3)
    for i, c in enumerate(cols):
        _table, r, *_rest = RECALL_FAILERS[c]
        ax1.text(max(r, 0.004) + 0.015, i, f"{r:.3f}", va="center",
                 color=INK, fontsize=9)
    ax1.set_yticks(list(y),
                   [f"{c} ({RECALL_FAILERS[c][0]})" for c in cols])
    ax1.invert_yaxis()
    ax1.set_xlim(0, 1.0)
    ax1.axvline(1.0, color=GRID, linewidth=0.8)
    _style(ax1)
    _title(ax1, "Shape recall of the failing free-text columns",
           "share of source top formats reproduced — 1.00 is parity")

    names = list(CEILING)
    y2 = range(len(names))
    src = [CEILING[n][1] for n in names]
    syn = [CEILING[n][0] for n in names]
    ax2.barh([i + 0.19 for i in y2], src, height=0.34, color=BLUE,
             zorder=3, label="source distinct")
    ax2.barh([i - 0.19 for i in y2], syn, height=0.34, color=ORANGE,
             zorder=3, label="synthetic distinct")
    ax2.axvline(POOL_CAP, color=INK, linewidth=1.0, linestyle="--", zorder=4)
    ax2.text(POOL_CAP * 1.25, -0.42, f"LLM pool cap = {POOL_CAP}",
             color=INK, fontsize=9, va="center")
    ax2.set_xscale("log")
    ax2.set_xlim(right=1.6e6)
    ax2.set_yticks(list(y2), names)
    ax2.invert_yaxis()
    _style(ax2)
    _title(ax2, "The pool-cap diversity ceiling",
           "log scale — synthetic pinned at ~512, source 20k-146k")
    ax2.legend(loc="lower right", fontsize=8.5, frameon=True,
               facecolor=SURFACE, edgecolor=GRID, labelcolor=INK)

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "prompt-constraints-evidence.png", dpi=160,
                facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_mask_collapse():
    # Execute the real mechanism, not a cartoon of it: both samplers come
    # from sdfb_core.engines.text_shapes (the code the figure teaches).
    from sdfb_core.engines.text_shapes import (
        build_mask_table,
        detect_identifier_shape,
        mask_alphabets,
        sample_identifier,
        sample_mask_table,
    )

    rng = random.Random(SEED)
    values = list(dict.fromkeys(
        "E2F" + "".join(rng.choice("0123456789ABCDEF") for _ in range(21))
        for _ in range(FAMILY_VALUES)
    ))

    def mask(v: str) -> str:
        return "".join(
            "9" if c.isdigit() else "A" if c.isupper() else c for c in v
        )

    observed_masks = {mask(v) for v in values}
    shape = detect_identifier_shape(values)
    assert shape is not None
    table = build_mask_table(values)
    assert table is not None
    alphabets = mask_alphabets(values)

    collapsed_hits = sum(
        mask(sample_identifier(shape, rng.randrange)) in observed_masks
        for _ in range(DRAWS)
    )
    table_hits = sum(
        mask(sample_mask_table(table, alphabets, rng.randrange))
        in observed_masks
        for _ in range(DRAWS)
    )

    fig, ax = plt.subplots(figsize=(8.6, 3.9))
    fig.patch.set_facecolor(SURFACE)
    labels = [
        "collapsed template\n(sample_identifier)",
        "mask-table draw\n(sample_mask_table)",
    ]
    fracs = [collapsed_hits / DRAWS, table_hits / DRAWS]
    bars = ax.barh([0, 1], fracs, height=0.52, color=[ORANGE, AQUA], zorder=3)
    for b, f in zip(bars, fracs, strict=True):
        ax.text(max(f, 0.005) + 0.02, b.get_y() + b.get_height() / 2,
                f"{f:.1%}", va="center", color=INK, fontsize=10)
    ax.set_yticks([0, 1], labels)
    ax.set_xlim(0, 1.12)
    ax.invert_yaxis()
    _style(ax)
    _title(
        ax,
        "Mask recall: whole-mask draws vs per-position collapse",
        f"seeded hex family, {len(values)} distinct values with a literal "
        f"'E2F' prefix — {DRAWS} draws per sampler",
    )
    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "prompt-constraints-mask-collapse.png", dpi=160,
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
    print(f"palette separation (OKLab dE x100, normal vision; "
          f"floor = {CVD_FLOOR:.0f}):")
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
    fig_mask_collapse()
    print(f"\nwrote 2 figures to {ASSETS}")
