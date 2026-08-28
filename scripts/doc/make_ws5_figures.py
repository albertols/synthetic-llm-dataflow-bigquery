"""Regenerate the WS5 generation-throughput figures.

    uv run --no-sync python3 scripts/doc/make_ws5_figures.py

Writes PNGs into docs/designs/assets/. Every measured number below is
derived from integration_tests/2026-07-26_06_54_25-14348390798392809440/
(the 1M-row stress run) — see MEASURED below for the extraction. Re-run the
extraction if that run is superseded; do not hand-edit the constants.

Palette is the project's established design-doc palette (blue / orange /
aqua), reused so WS5 figures sit alongside the 2026-07-24 and 2026-07-25
assets without a second visual language.
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
# OKLab dE x100 floor for normal-vision separation (dataviz check 4).
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"

# --- MEASURED (1M-row run 2026-07-26_06_54_25, worker_logs.jsonl) ----------
# milestone            n     total_s
POOL_BUILD_LLM = 68_805.0  # freetext_pool_built, n=108 (LLM service time)
POOL_BUILD_WALL = 26_946.0  # b1_pools_built,      n=43  (per-setup wall)
SETUP_TOTAL = 28_982.0  # dofn_setup_done,     n=43
VLLM_SPAWN = 1_403.0  # vllm_ready,          n=6
MODEL_PULL = 326.0  # model_pull_done,     n=6
CHUNK_REUSE = 333.0  # b1_chunks_reused,    n=43
INDEX_BUILD = 2.0  # b1_index_built,      n=43
JOB_WALL = 4_050.0  # 14:04:52 -> 15:12:22

POOLS_REBUILT, POOLS_CACHED = 108, 21
PER_COLUMN = {  # freetext_pool_built totals
    "COL_047": 26_107.0,
    "COL_052": 24_478.0,
    "COL_053": 18_220.0,
}
WAVES = [(3.2, 1), (10.5, 8), (17.0, 24), (37.0, 8), (55.1, 3)]
LAST_POOL_MIN = 64.2

# Generate-stage re-materialization (SECTION 4), from the 67-column source:
# 42 INT64 + 3 TIMESTAMP + 1 DATE + 21 STRING, ~10k observed values each.
#   _numeric_numpy     rebuilds a listcomp+predicate per call  (~150 ns/item)
#   _temporal_numpy    memoizes the LIST but re-runs np.asarray (~20 ns/item)
#   _categorical_numpy rebuilds probs, but `categories` is CAPPED at 50 in
#                      profile.py (_FREE_TEXT_MAX_CATEGORIES), so it is bounded
#                      and deliberately left alone.
N_ROWS = 1_000_000
N_OBSERVED = 10_000
N_NUMERIC_COLS, NS_PER_ITEM = 42, 150e-9
N_TEMPORAL_COLS, NS_PER_ASARRAY = 4, 20e-9
N_CAT_COLS, N_CATEGORIES = 20, 50
N_SETUPS = 43


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
    ax.set_title(text, color=INK, fontsize=12, fontweight="600", loc="left", pad=26)
    if sub:
        ax.text(
            0, 1.025, sub, transform=ax.transAxes, color=MUTED, fontsize=9, va="bottom"
        )


# --------------------------------------------------------------------------
def fig_cost_anatomy():
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(13, 5.2), facecolor=SURFACE, gridspec_kw={"width_ratios": [1.15, 1]}
    )

    labels = [
        "Free-text pool build\n(LLM service time)",
        "Free-text pool build\n(per-setup wall)",
        "vLLM cold spawn",
        "Model pull (GCS)",
        "RAG chunk reuse",
        "FAISS index build",
    ]
    vals = [POOL_BUILD_LLM, POOL_BUILD_WALL, VLLM_SPAWN, MODEL_PULL, CHUNK_REUSE, INDEX_BUILD]
    colors = [ORANGE, ORANGE, BLUE, BLUE, AQUA, AQUA]
    alphas = [1.0, 0.55, 1.0, 0.55, 1.0, 0.55]

    y = np.arange(len(labels))[::-1]
    for yi, v, c, a in zip(y, vals, colors, alphas, strict=True):
        ax1.barh(yi, v, height=0.62, color=c, alpha=a, zorder=3)
        ax1.text(
            v * 1.35, yi, f"{v:,.0f}s", va="center", ha="left",
            color=INK, fontsize=9, fontweight="600",
        )
    ax1.set_yticks(y, labels, fontsize=9)
    ax1.set_xscale("log")
    ax1.set_xlim(1, POOL_BUILD_LLM * 12)
    ax1.set_xlabel("seconds (log scale)", color=MUTED, fontsize=9)
    _style(ax1, grid_axis="x")
    _title(
        ax1,
        "Where the 1M-row run spent its time",
        f"job wall clock {JOB_WALL/60:.0f} min — pool building dominates every other phase by 20x+",
    )

    cols = list(PER_COLUMN)
    tot = [PER_COLUMN[c] for c in cols]
    bars = ax2.bar(cols, tot, color=[ORANGE, BLUE, AQUA], width=0.6, zorder=3)
    for b, v in zip(bars, tot, strict=True):
        ax2.text(
            b.get_x() + b.get_width() / 2, v + 900, f"{v:,.0f}s\n36 rebuilds",
            ha="center", color=INK, fontsize=9, fontweight="600",
        )
    ax2.set_ylim(0, max(tot) * 1.28)
    ax2.set_ylabel("LLM service seconds", color=MUTED, fontsize=9)
    ax2.set_xticks(range(len(cols)), [c.replace("_", "\n") for c in cols], fontsize=8.5)
    _style(ax2)
    _title(
        ax2,
        "Every free-text column rebuilt 36 times",
        f"{POOLS_REBUILT} rebuilds vs only {POOLS_CACHED} cache hits out of {POOLS_REBUILT+POOLS_CACHED} attempts",
    )

    fig.tight_layout()
    fig.savefig(ASSETS / "ws5-cost-anatomy.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_autoscale_amplification():
    fig, ax = plt.subplots(figsize=(12, 5.4), facecolor=SURFACE)

    ax.axvspan(0, LAST_POOL_MIN, color=ORANGE, alpha=0.07, zorder=0)
    ax.text(
        LAST_POOL_MIN / 2, 27.4,
        "workers still building pools — generation throughput ramping, not steady",
        ha="center", color=ORANGE, fontsize=9.5, fontweight="600",
    )

    for t, n in WAVES:
        ax.bar(t, n, width=1.6, color=BLUE, zorder=3)
        ax.text(
            t, n + 0.6, f"{n}\nt+{t:.0f}m", ha="center", va="bottom",
            color=INK, fontsize=9, fontweight="600", linespacing=1.35,
        )

    ax.axvline(LAST_POOL_MIN, color=ORANGE, linewidth=2, linestyle="--", zorder=4)
    ax.annotate(
        f"last pool completes\nt+{LAST_POOL_MIN:.0f} min",
        xy=(LAST_POOL_MIN, 13.5), xytext=(LAST_POOL_MIN - 17, 17.5),
        color=ORANGE, fontsize=9.5, fontweight="600", ha="left",
        arrowprops={"arrowstyle": "->", "color": ORANGE, "linewidth": 1.6},
    )
    ax.axvline(JOB_WALL / 60, color=MUTED, linewidth=2, zorder=4)
    ax.annotate(
        "job ends\nt+68 min",
        xy=(JOB_WALL / 60, 8.5), xytext=(JOB_WALL / 60 + 1.2, 11),
        color=MUTED, fontsize=9.5, ha="left",
        arrowprops={"arrowstyle": "->", "color": MUTED, "linewidth": 1.4},
    )

    ax.set_xlim(-2, JOB_WALL / 60 + 9)
    ax.set_ylim(0, 29)
    ax.set_xlabel("minutes since job start", color=MUTED, fontsize=9)
    ax.set_ylabel("DoFn setup() entries", color=MUTED, fontsize=9)
    _style(ax)
    _title(
        ax,
        "Autoscaling multiplies the pool build — the cache is process-scoped",
        "5 waves of DoFn setup(); each new worker process starts with an empty _POOL_CACHE and rebuilds all 3 columns",
    )

    fig.tight_layout()
    fig.savefig(ASSETS / "ws5-autoscale-amplification.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def _modes(rng):
    """Synthetic 2-D stand-in for a column's embedding manifold: 5 modes of
    very unequal mass — the realistic case (one dominant value class, several
    rare ones). Positions are illustrative; the SELECTION algorithms below
    are the real ones."""
    centers = np.array([[0.0, 0.0], [2.6, 1.1], [-2.3, 1.5], [1.4, -2.4], [-1.8, -2.2]])
    sizes = [340, 70, 45, 28, 17]
    pts, lab = [], []
    for i, (c, s) in enumerate(zip(centers, sizes, strict=True)):
        pts.append(rng.normal(c, 0.52, size=(s, 2)))
        lab += [i] * s
    return np.vstack(pts), np.array(lab)


def _centroid_topk(pts, k):
    d = np.linalg.norm(pts - pts.mean(axis=0), axis=1)
    return np.argsort(d)[:k]


def _kcenter(pts, k, start=None):
    first = int(np.argmin(np.linalg.norm(pts - pts.mean(axis=0), axis=1))) if start is None else start
    chosen = [first]
    d = np.linalg.norm(pts - pts[first], axis=1)
    for _ in range(k - 1):
        nxt = int(np.argmax(d))
        chosen.append(nxt)
        d = np.minimum(d, np.linalg.norm(pts - pts[nxt], axis=1))
    return np.array(chosen)


def fig_seed_strategies():
    rng = np.random.default_rng(11)
    pts, lab = _modes(rng)
    k = 8

    sel = {
        "centroid": _centroid_topk(pts, k),
        "kcenter": _kcenter(pts, k),
        "kcenter_rotate": None,
    }
    rot = [_kcenter(pts, k), _kcenter(pts, k, start=int(np.argmax(pts[:, 0]))),
           _kcenter(pts, k, start=int(np.argmin(pts[:, 1])))]

    fig, axes = plt.subplots(1, 3, figsize=(14.5, 5.3), facecolor=SURFACE)
    titles = [
        ("--pool_seed_strategy=centroid", "today — control arm"),
        ("--pool_seed_strategy=kcenter", "arm B — fixed spread seeds"),
        ("--pool_seed_strategy=kcenter_rotate", "arm A — seeds rotate per ladder attempt"),
    ]

    for ax, (name, sub) in zip(axes, titles, strict=True):
        ax.scatter(pts[:, 0], pts[:, 1], s=13, color=GRID, edgecolors="none", zorder=1)
        key = name.split("=")[1]
        if key == "kcenter_rotate":
            marks = [("o", ORANGE, "attempt 1"), ("s", BLUE, "attempt 2"), ("^", AQUA, "attempt 3")]
            for idx, (m, c, lb) in zip(rot, marks, strict=True):
                ax.scatter(pts[idx, 0], pts[idx, 1], s=95, marker=m, color=c,
                           edgecolors=SURFACE, linewidths=1.6, zorder=3, label=lb)
            covered = len({*lab[rot[0]], *lab[rot[1]], *lab[rot[2]]})
            ax.legend(
                frameon=True, facecolor=SURFACE, edgecolor=GRID, framealpha=1.0,
                fontsize=8.5, loc="lower left", labelcolor=INK,
            )
        else:
            idx = sel[key]
            c = ORANGE if key == "centroid" else BLUE
            ax.scatter(pts[idx, 0], pts[idx, 1], s=110, marker="o", color=c,
                       edgecolors=SURFACE, linewidths=1.8, zorder=3)
            covered = len(set(lab[idx]))

        ax.set_title(name, color=INK, fontsize=10.5, fontweight="600", loc="left", pad=30, family="monospace")
        ax.text(0, 1.03, sub, transform=ax.transAxes, color=MUTED, fontsize=9, va="bottom")
        note = " (8 seeds, near-coincident)" if key == "centroid" else ""
        ax.text(0.5, -0.11, f"modes reached by the 8 seeds: {covered} of 5{note}",
                transform=ax.transAxes, ha="center", color=INK, fontsize=9.5, fontweight="600")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_facecolor(SURFACE)
        for s in ax.spines.values():
            s.set_color(GRID)

    fig.suptitle(
        "What each --pool_seed_strategy value shows the LLM (grey = the column's value manifold)",
        color=INK, fontsize=12.5, fontweight="600", x=0.012, ha="left", y=0.99,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.94))
    fig.savefig(ASSETS / "ws5-seed-strategies.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_sampler_hoisting():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.0), facecolor=SURFACE)

    batch = np.array([16, 32, 64, 128, 256, 512, 1024, 2048, 4096])
    calls = N_ROWS / batch
    per_call = calls * N_NUMERIC_COLS * N_OBSERVED * NS_PER_ITEM  # rebuilt every call
    hoisted = np.full_like(
        per_call, N_SETUPS * N_NUMERIC_COLS * N_OBSERVED * NS_PER_ITEM
    )

    ax1.plot(batch, per_call, color=ORANGE, linewidth=2.4, marker="o", markersize=6, zorder=3,
             label="today — array rebuilt per generate_batch() call")
    ax1.plot(batch, hoisted, color=BLUE, linewidth=2.4, marker="s", markersize=6, zorder=3,
             label="hoisted — built once per sampler (43 setups)")
    ax1.set_xscale("log", base=2)
    ax1.set_yscale("log")
    ax1.axvline(16, color=MUTED, linestyle="--", linewidth=1.4, zorder=2)
    ax1.annotate("current default\nbatch_size=16", xy=(17, per_call[0] * 0.92),
                 xytext=(70, per_call[0] * 0.42),
                 color=ORANGE, fontsize=9, fontweight="600",
                 arrowprops={"arrowstyle": "->", "color": ORANGE, "linewidth": 1.4})
    ax1.set_xlabel("batch_size (rows per element, log2)", color=MUTED, fontsize=9)
    ax1.set_ylabel("CPU-seconds across the 1M-row job (log)", color=MUTED, fontsize=9)
    ax1.set_ylim(1.0, per_call[0] * 4)
    ax1.legend(
        frameon=True, facecolor=SURFACE, edgecolor=GRID, framealpha=1.0,
        fontsize=8.5, labelcolor=INK, loc="lower left",
    )
    _style(ax1, grid_axis="both")
    _title(ax1, "Re-materializing observed values dominates Generate",
           f"{N_NUMERIC_COLS} INT64 columns x {N_OBSERVED:,} observed values, rebuilt on every call")

    n_calls = N_ROWS / 16  # today's default
    stages = [
        "_numeric_numpy\n(42 cols, listcomp)",
        "_temporal_numpy\n(4 cols, np.asarray)",
        "_categorical_numpy\n(<=50 categories)",
    ]
    now = [
        n_calls * N_NUMERIC_COLS * N_OBSERVED * NS_PER_ITEM,
        n_calls * N_TEMPORAL_COLS * N_OBSERVED * NS_PER_ASARRAY,
        n_calls * N_CAT_COLS * N_CATEGORIES * NS_PER_ITEM,
    ]
    after = [
        N_SETUPS * N_NUMERIC_COLS * N_OBSERVED * NS_PER_ITEM,
        N_SETUPS * N_TEMPORAL_COLS * N_OBSERVED * NS_PER_ASARRAY,
        now[2],  # deliberately NOT hoisted — already bounded by the cap
    ]
    x = np.arange(len(stages))
    ax2.bar(x - 0.19, now, width=0.36, color=ORANGE, zorder=3, label="today")
    ax2.bar(x + 0.19, after, width=0.36, color=BLUE, zorder=3, label="hoisted to sampler construction")
    whole_seconds_above = 100.0  # below this, a decimal carries information

    def _secs(v):
        return f"{v:,.0f}s" if v >= whole_seconds_above else f"{v:,.1f}s"

    for xi, (a, b) in enumerate(zip(now, after, strict=True)):
        ax2.text(xi - 0.19, a * 1.3, _secs(a), ha="center", color=INK, fontsize=8.5, fontweight="600")
        ax2.text(xi + 0.19, b * 1.3, _secs(b), ha="center", color=INK, fontsize=8.5, fontweight="600")
    ax2.text(
        2, now[2] * 3.2, "already bounded —\nleft alone", ha="center",
        color=MUTED, fontsize=8.5, style="italic",
    )
    ax2.set_yscale("log")
    ax2.set_ylim(0.01, max(now) * 30)
    ax2.set_xticks(x, stages, fontsize=8.5)
    ax2.set_ylabel("CPU-seconds (log)", color=MUTED, fontsize=9)
    ax2.legend(frameon=False, fontsize=8.5, labelcolor=INK, loc="upper right")
    _style(ax2)
    _title(ax2, "Two paths re-materialize; the third is capped",
           "at batch_size=16 — categories cap at 50 in profile.py, so only numeric/temporal need hoisting")

    fig.tight_layout()
    fig.savefig(ASSETS / "ws5-sampler-hoisting.png", dpi=160, facecolor=SURFACE)
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
    """Normal-vision separation of the categorical hues (OKLab dE x100)."""
    names = {"blue": BLUE, "orange": ORANGE, "aqua": AQUA}
    ks = list(names)
    print(f"palette separation (OKLab dE x100, normal vision; floor = {CVD_FLOOR:.0f}):")
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            a, b = _srgb_to_oklab(names[ks[i]]), _srgb_to_oklab(names[ks[j]])
            de = 100 * math.dist(a, b)
            print(f"  {ks[i]:6s} vs {ks[j]:6s}: {de:5.1f}  {'PASS' if de >= CVD_FLOOR else 'FAIL'}")


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    check_palette()
    fig_cost_anatomy()
    fig_autoscale_amplification()
    fig_seed_strategies()
    fig_sampler_hoisting()
    print(f"\nwrote 4 figures to {ASSETS}")
