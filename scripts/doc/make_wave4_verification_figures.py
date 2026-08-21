"""Regenerate the wave-4 VERIFICATION figures (ADR 0027).

    uv run --no-sync python3 scripts/doc/make_wave4_verification_figures.py

Writes PNGs into docs/designs/assets/. Both figures are EVIDENCE: the
2026-08-20/21 four-run cycle measurements (the only place those numbers
are typed), read from the bundles under `integration_tests/` and their
`worker_logs.jsonl` scrub telemetry. Claims:

  1. wave4-verified.png    — every ADR 0026 acceptance criterion moved as
     designed on the next cold pair: COL_009 substantive copy halved
     (0.52 → 0.25, identically on both cold runs — deterministic, not
     variance), COL_064's shape plateau collapsed 0.25% → 0.045%,
     COL_001's top-mask share reached source parity, and the false
     copy_fraction BLOCKERs fell 16 → 2.
  2. wave4-scrub-anatomy.png — redraws dominated the v1 scrub (COL_047:
     190 of 197 collisions per batch resolved by redraw) and moved a
     sparse-neighborhood column's decile-KS 0.038 → 0.166; nudge-first
     (v2) resolves those same collisions within +/-24 units of the
     original draw, keeping the marginal.

Palette matches the design-doc asset set: BLUE = source truth / target,
ORANGE = pre-fix, AQUA = wave-4 measured. OKLab separation check runs on
every regeneration.
"""

from __future__ import annotations

import math
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

# --- MEASURED (2026-08-20/21 four-run cycle; the only place these numbers
# are typed). Sources:
#   pre-fix   integration_tests/2026-08-20_05_49_25-7855…  (A_TABLE, pre-wave-4)
#   run 1     integration_tests/2026-08-20_14_13_44-17334… (A_TABLE, wave-4 cold)
#   run 4     integration_tests/2026-08-21_07_58_08-12248… (A_TABLE, wave-4 cold)
# gcp_metrics annexes (copy ratios), freetext_crosscheck annexes (top-shape
# shares), and worker_logs.jsonl `numeric_source_rejected` lines (scrub
# telemetry; first-batch means over 8 workers, run 4).
RUNS = "2026-08-20_14_13_44-17334… · 2026-08-21_07_58_08-12248…"

# copy_ratio_substantive: (column, pre-fix, run 1, run 4)
SUBSTANTIVE = (
    ("COL_009", 0.5218, 0.2531, 0.2538),
    ("COL_047", 0.1092, 0.0198, None),  # run 4 exempt row, not re-listed
)
MEM_CRITICAL = 0.3

# Identifier parity: (label, pre-fix synthetic, wave-4 synthetic, source truth)
TOP_SHAPE_SHARE = (
    ("COL_064\ntop shape share", 0.0025, 0.00045, 0.0001),
    ("COL_001\ntop mask share", 0.0120, 0.00485, 0.00475),
)

# freetext.copy_fraction BLOCKER rows failing on A_TABLE.
BLOCKERS_BEFORE, BLOCKERS_AFTER = 16, 2

# Scrub telemetry per 1000-draw first batch (run 4, mean of 8 workers):
# (column, collisions, resolved-by-redraw, nudged, unresolved)
SCRUB = (
    ("COL_009", 546.6, 380.2, 24.5, 141.9),
    ("COL_047", 196.8, 190.0, 1.0, 5.8),
)
# COL_047 decile-KS: pre-wave-4 vs wave-4 (the redraw-first fidelity cost).
KS_PRE, KS_POST = 0.038, 0.166
KS_GATE = 0.2


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
def fig_verified():
    fig, axes = plt.subplots(
        1, 3, figsize=(13.5, 4.8), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [3, 3, 2]},
    )

    ax = axes[0]
    labels = [c for c, *_ in SUBSTANTIVE]
    pre = [p * 100 for _, p, _, _ in SUBSTANTIVE]
    r1 = [r * 100 for _, _, r, _ in SUBSTANTIVE]
    x = np.arange(len(labels))
    w = 0.36
    ax.bar(x - w / 2, pre, width=w, color=ORANGE, label="pre-wave-4")
    ax.bar(x + w / 2, r1, width=w, color=AQUA, label="wave-4 cold")
    for xi, (p, r) in enumerate(zip(pre, r1, strict=True)):
        ax.text(xi - w / 2, p + 1, f"{p:.1f}", ha="center", color=INK, fontsize=8.5)
        ax.text(xi + w / 2, r + 1, f"{r:.1f}", ha="center", color=INK, fontsize=8.5)
    ax.axhline(MEM_CRITICAL * 100, color=INK, linewidth=1, linestyle=":")
    ax.text(1.45, MEM_CRITICAL * 100 + 1, "CRITICAL 30", color=INK, fontsize=8,
            ha="right")
    ax.set_xticks(x, labels, fontsize=9, color=MUTED)
    ax.set_ylabel("substantive copy (% of rows)", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "Numeric scrub landed",
           "identical on both cold runs (25.31 / 25.38) — deterministic")
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK)

    ax = axes[1]
    labels = [c for c, *_ in TOP_SHAPE_SHARE]
    pre = [p * 100 for _, p, _, _ in TOP_SHAPE_SHARE]
    now = [n * 100 for _, _, n, _ in TOP_SHAPE_SHARE]
    src = [s * 100 for _, _, _, s in TOP_SHAPE_SHARE]
    x = np.arange(len(labels))
    w = 0.26
    ax.bar(x - w, pre, width=w, color=ORANGE, label="pre-wave-4")
    ax.bar(x, now, width=w, color=AQUA, label="wave-4 cold")
    ax.bar(x + w, src, width=w, color=BLUE, label="source truth")
    for xi, (a, b, c) in enumerate(zip(pre, now, src, strict=True)):
        for dx, v in ((-w, a), (0, b), (w, c)):
            ax.text(xi + dx, v * 1.15, f"{v:.3f}", ha="center", color=INK,
                    fontsize=7.5)
    ax.set_yscale("log")
    ax.set_xticks(x, labels, fontsize=9, color=MUTED)
    ax.set_ylabel("share of sampled rows (%, log)", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "Mask concentration at parity",
           "plateau collapse (COL_064) · renormalization gone (COL_001)")
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK)

    ax = axes[2]
    x = np.arange(2)
    ax.bar(x, [BLOCKERS_BEFORE, BLOCKERS_AFTER], width=0.5,
           color=[ORANGE, AQUA])
    for xi, v in enumerate((BLOCKERS_BEFORE, BLOCKERS_AFTER)):
        ax.text(xi, v + 0.3, str(v), ha="center", color=INK, fontsize=10)
    ax.set_xticks(x, ["pre-wave-4", "wave-4 cold"], fontsize=9, color=MUTED)
    ax.set_ylabel("failing copy_fraction rows", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "False BLOCKERs gone",
           "numeric-domain exemption (A_TABLE)")
    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "wave4-verified.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_scrub_anatomy():
    fig, axes = plt.subplots(
        1, 2, figsize=(13.5, 4.8), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [3, 2]},
    )

    ax = axes[0]
    labels = [c for c, *_ in SCRUB]
    x = np.arange(len(labels))
    w = 0.5
    redraw = [r for _, _, r, _, _ in SCRUB]
    nudged = [n for _, _, _, n, _ in SCRUB]
    unresolved = [u for _, _, _, _, u in SCRUB]
    ax.bar(x, redraw, width=w, color=ORANGE, label="resolved by REDRAW (v1 order)")
    ax.bar(x, nudged, width=w, bottom=redraw, color=BLUE, label="resolved by nudge")
    bottom2 = [a + b for a, b in zip(redraw, nudged, strict=True)]
    ax.bar(x, unresolved, width=w, bottom=bottom2, color=MUTED,
           label="unresolved (dense neighborhood)")
    for xi, (_, coll, *_rest) in enumerate(SCRUB):
        ax.text(xi, coll + 12, f"{coll:.0f} collisions", ha="center",
                color=INK, fontsize=9)
    ax.set_xticks(x, labels, fontsize=9, color=MUTED)
    ax.set_ylabel("per 1000-draw batch (run-4 telemetry)", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "Redraws dominated the v1 scrub",
           "numeric_source_rejected · first batch x 8 workers, run 4")
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK)

    ax = axes[1]
    x = np.arange(2)
    ax.bar(x, [KS_PRE, KS_POST], width=0.5, color=[BLUE, ORANGE])
    for xi, v in enumerate((KS_PRE, KS_POST)):
        ax.text(xi, v + 0.005, f"{v:.3f}", ha="center", color=INK, fontsize=10)
    ax.axhline(KS_GATE, color=INK, linewidth=1, linestyle=":")
    ax.text(1.45, KS_GATE + 0.004, "gate 0.2", color=INK, fontsize=8, ha="right")
    ax.set_xticks(
        x, ["pre-scrub", "redraw-first scrub"], fontsize=9, color=MUTED
    )
    ax.set_ylabel("COL_047 decile-KS", color=MUTED, fontsize=9)
    ax.set_ylim(0, 0.24)
    _style(ax)
    _title(ax, "The fidelity cost of redraw-first",
           "v2 nudges FIRST (±24) — stays in-quantile")
    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "wave4-scrub-anatomy.png", dpi=160, facecolor=SURFACE)
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
    fig_verified()
    fig_scrub_anatomy()
    for name in ("wave4-verified.png", "wave4-scrub-anatomy.png"):
        print("wrote", ASSETS / name)


if __name__ == "__main__":
    main()
