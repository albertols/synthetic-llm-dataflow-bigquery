"""Regenerate the WS6 pipeline-shape figures.

    uv run --no-sync python3 scripts/make_ws6_figures.py

Writes PNGs into docs/designs/assets/. Every measured number below is
derived from integration_tests/2026-07-26_17_10_37-5541097091204532225/
(the first GPU/CPU-separated 1M-row run on the GCP LZ) — see MEASURED. Do
not hand-edit the constants; update them from a superseding run and re-run.

Palette matches the WS5/2026-07-24/2026-07-25 assets (see
scripts/make_ws5_figures.py) so the design-doc set reads as one system.
"""

from __future__ import annotations

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

# --- MEASURED (job 2026-07-26_17_10_37-5541097091204532225) ---------------
# 1M rows, batch_size=1000 (WS5 T3 active), 67-column source, L4.
# NOTE: this run did NOT enable the WS5 pool store — zero
# freetext_pool_store_* milestones — so pool rebuilds are still per-process.
WALL_MIN = 53.3
N_BATCHES = 1000
BATCH_CPU_SECONDS = 7_257.0  # sum(batch_done seconds), n=1000, mean 7.3s
SETUP_SECONDS = 12_562.0  # sum(dofn_setup_done), n=24
POOL_BUILD_SECONDS = 29_280.0  # sum(freetext_pool_built), n=51
N_SETUPS, N_POOL_BUILDS, N_POOL_CACHE_HITS = 24, 51, 22
N_SETUP_RETRIES, N_OOM, N_VLLM_START_FAIL = 11, 12, 4
N_VLLM_SPAWN, N_VLLM_READY = 5, 3

# batch_done count per 2-minute bucket (t = minutes since first log line).
GEN_BUCKETS = {
    12: 29, 14: 89, 16: 113, 18: 115, 20: 114, 22: 103,
    30: 17, 32: 104, 34: 109, 36: 117, 38: 36,
    48: 53, 50: 1,
}
# Peak throughput at the barrier release vs during generation (MiB/s, GUI).
PEAK_BARRIER_MIBS = 13.97  # WriteLanding/BigQueryBatchFileLoads/AppendDestination
PEAK_SHUFFLE_READ_MIBS = 12.77  # EnforceUniqueness/GroupByRowDigest/Read
GEN_PLATEAU_MIBS = 1.65  # EnforceUniqueness/KeyByRowDigest during a hill


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
def fig_run_timeline():
    """THE figure: half the run generated nothing."""
    fig, ax = plt.subplots(figsize=(13.5, 5.6), facecolor=SURFACE)

    # Idle bands — no batch completed in these windows.
    idle = [(0, 12, "first worker still\nbuilding pools"),
            (24, 30, "new worker wave\nrebuilding pools"),
            (40, 48, "new worker wave\nrebuilding pools")]
    for lo, hi, label in idle:
        ax.axvspan(lo, hi, color=ORANGE, alpha=0.10, zorder=0)
        ax.text((lo + hi) / 2, 108, label, ha="center", va="top",
                color=ORANGE, fontsize=8.5, fontweight="600", linespacing=1.3)

    # The barrier release.
    ax.axvspan(WALL_MIN - 3.3, WALL_MIN, color=BLUE, alpha=0.13, zorder=0)
    ax.text(WALL_MIN - 1.6, 108, "GroupByKey\nreleases →\nBQ write",
            ha="center", va="top", color=BLUE, fontsize=8.5,
            fontweight="600", linespacing=1.3)

    xs = sorted(GEN_BUCKETS)
    ax.bar([x + 1 for x in xs], [GEN_BUCKETS[x] for x in xs], width=1.8,
           color=AQUA, zorder=3, label="batches completed (1,000 rows each)")

    ax.set_xlim(0, WALL_MIN + 0.5)
    ax.set_ylim(0, 125)
    ax.set_xlabel("minutes since job start", color=MUTED, fontsize=9)
    ax.set_ylabel("batches completed per 2 min", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK, loc="upper left")
    _style(ax)
    gen_min = 2 * len(GEN_BUCKETS)
    _title(ax,
           f"Only {gen_min} of {WALL_MIN:.0f} minutes actually generated rows",
           "every idle band is a worker paying the free-text pool ladder again — "
           "this run did NOT enable the WS5 pool store")

    fig.tight_layout()
    fig.savefig(ASSETS / "ws6-run-timeline.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_where_time_went():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2), facecolor=SURFACE,
                                   gridspec_kw={"width_ratios": [1, 1]})

    gen_min = 2 * len(GEN_BUCKETS)
    idle_min = 12 + 6 + 8
    barrier_min = WALL_MIN - gen_min - idle_min
    parts = [("Generating rows", gen_min, AQUA),
             ("Idle — workers rebuilding pools", idle_min, ORANGE),
             ("GroupByKey barrier + BQ write", barrier_min, BLUE)]
    left = 0.0
    for _label, val, colour in parts:
        ax1.barh(0, val, left=left, height=0.42, color=colour, zorder=3)
        ax1.text(left + val / 2, 0, f"{val:.0f}m", ha="center", va="center",
                 color="white", fontsize=11, fontweight="600")
        left += val
    ax1.set_xlim(0, WALL_MIN)
    ax1.set_ylim(-0.6, 0.85)
    ax1.set_yticks([])
    ax1.set_xlabel("minutes", color=MUTED, fontsize=9)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in parts]
    ax1.legend(handles, [p[0] for p in parts], frameon=False, fontsize=9,
               labelcolor=INK, loc="upper left", ncol=1)
    _style(ax1, grid_axis="x")
    _title(ax1, f"Where the {WALL_MIN:.0f} minutes went",
           f"{100*idle_min/WALL_MIN:.0f}% idle — none of it is generation compute")

    # Right: the actual compute is trivial next to the wall clock.
    labels = ["Pool builds\n(LLM service)", "DoFn setup\n(wall)",
              "Row generation\n(all 1,000 batches)"]
    vals = [POOL_BUILD_SECONDS, SETUP_SECONDS, BATCH_CPU_SECONDS]
    colours = [ORANGE, ORANGE, AQUA]
    alphas = [1.0, 0.55, 1.0]
    y = np.arange(len(labels))[::-1]
    for yi, v, c, a in zip(y, vals, colours, alphas, strict=True):
        ax2.barh(yi, v, height=0.55, color=c, alpha=a, zorder=3)
        ax2.text(v * 1.06, yi, f"{v:,.0f} s", va="center", ha="left",
                 color=INK, fontsize=9.5, fontweight="600")
    ax2.set_yticks(y, labels, fontsize=9)
    ax2.set_xlim(0, max(vals) * 1.35)
    ax2.set_xlabel("seconds (summed across workers)", color=MUTED, fontsize=9)
    _style(ax2, grid_axis="x")
    _title(ax2, "Generation is not the bottleneck",
           f"{BATCH_CPU_SECONDS:,.0f} CPU-s over {N_BATCHES:,} batches ≈ 3 min "
           "spread across the worker pool")

    fig.tight_layout()
    fig.savefig(ASSETS / "ws6-where-time-went.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_dedup_options():
    """What each uniqueness strategy costs and guarantees."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.4), facecolor=SURFACE)

    names = ["Today\nGBK barrier", "CombinePerKey\n(map-side)",
             "Unique by\nconstruction", "Post-hoc\nBQ dedup"]
    # Relative full-dataset passes through Dataflow shuffle (1.0 = whole set
    # written AND read once per GroupByKey; today's DAG chains up to three).
    shuffle_passes = [3.0, 0.35, 0.0, 0.0]
    # Time before the FIRST row is visible in the landing table, as a
    # fraction of wall clock (barrier ⇒ only at the very end).
    ttfr = [1.0, 1.0, 0.25, 0.25]

    x = np.arange(len(names))
    ax1.bar(x - 0.2, shuffle_passes, width=0.38, color=ORANGE, zorder=3,
            label="full-dataset shuffle passes")
    ax1.bar(x + 0.2, ttfr, width=0.38, color=BLUE, zorder=3,
            label="time before first row lands (x wall clock)")
    for xi, (s, t) in enumerate(zip(shuffle_passes, ttfr, strict=True)):
        ax1.text(xi - 0.2, s + 0.07, f"{s:.2g}x", ha="center", color=INK,
                 fontsize=9, fontweight="600")
        ax1.text(xi + 0.2, t + 0.07, f"{t:.0%}", ha="center", color=INK,
                 fontsize=9, fontweight="600")
    ax1.set_xticks(x, names, fontsize=9)
    ax1.set_ylim(0, 3.5)
    ax1.set_ylabel("lower is better", color=MUTED, fontsize=9)
    ax1.legend(frameon=False, fontsize=8.5, labelcolor=INK, loc="upper right")
    _style(ax1)
    _title(ax1, "Cost of each uniqueness strategy",
           "today's DAG chains up to three GroupByKeys — row digest, PK, identity")

    # Guarantee matrix.
    rows = ["Identity columns unique", "Full-row dups removed",
            "Full-row dups measured", "Rows land incrementally"]
    matrix = np.array([
        [1, 1, 1, 1],   # identity unique
        [1, 1, 0, 1],   # full-row dups removed
        [1, 1, 1, 1],   # measured
        [0, 0, 1, 1],   # incremental
    ])
    ax2.imshow(matrix, cmap=None, aspect="auto", alpha=0)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ok = bool(matrix[i, j])
            ax2.add_patch(plt.Rectangle((j - 0.46, i - 0.42), 0.92, 0.84,
                                        color=AQUA if ok else GRID,
                                        alpha=0.85 if ok else 1.0, zorder=2))
            ax2.text(j, i, "yes" if ok else "no", ha="center", va="center",
                     color="white" if ok else MUTED, fontsize=9.5,
                     fontweight="600", zorder=3)
    ax2.set_xticks(range(len(names)), names, fontsize=9)
    ax2.set_yticks(range(len(rows)), rows, fontsize=9)
    ax2.set_xlim(-0.5, len(names) - 0.5)
    ax2.set_ylim(len(rows) - 0.5, -0.5)
    ax2.tick_params(colors=MUTED, length=0)
    for s in ax2.spines.values():
        s.set_visible(False)
    ax2.grid(False)
    ax2.set_facecolor(SURFACE)
    _title(ax2, "What each one still guarantees",
           "'unique by construction' trades exact full-row dedup for a measured rate")

    fig.tight_layout()
    fig.savefig(ASSETS / "ws6-dedup-options.png", dpi=160, facecolor=SURFACE)
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
    fig_run_timeline()
    fig_where_time_went()
    fig_dedup_options()
    print(f"\nwrote 3 figures to {ASSETS}")
