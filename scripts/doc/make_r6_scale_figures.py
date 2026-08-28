"""Regenerate the R6-scale pool-ladder figures (ADR 0033).

    uv run --no-sync python3 scripts/doc/make_r6_scale_figures.py

Writes PNGs into docs/designs/assets/. Every measured number below is
derived from the two R6 relational runs' worker logs + reports
(integration_tests/2026-08-25_12_05_08-14035293654817605690, 1M rows/table;
integration_tests/2026-08-26_05_01_16-3186876581127148459, 10M rows/table)
— see MEASURED. Do not hand-edit the constants; update them from a
superseding run and re-run. The CONCEPT block is seeded and deterministic.

Palette matches the design-doc asset set (scripts/doc/make_ws6_figures.py):
BLUE / ORANGE / AQUA with the OKLab separation check on every regeneration.
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

# --- MEASURED (job 2026-08-25_12_05_08-14035293654817605690, 1M/table) ------
# A_TABLE/BuildFreeTextPools, attempt 1. t = seconds since 19:24:16Z (the
# branch's first milestone). Four ladders on a 4-thread pool; the shared
# VLLMModelClient is set up lazily inside the first call of each thread.
RACE_T1_SETUP = 20  # thread 1 model_client_setup_start (19:24:36)
RACE_T1_SPAWN = 90  # model_pull_done 69.9 s → vllm_spawn; fitted=3616 < 4096
RACE_T1_WAITS = (90, 110, 131, 151, 171)  # 5 x vllm_unfittable_wait 20 s
RACE_T1_RAISE = 191  # 6th measure fitted=3360 → ModelLenUnfittableError
RACE_T2_FIT = 251  # thread 2: fraction 0.85, free 14093 MiB → spawn
RACE_VLLM_READY = 373  # vllm_ready seconds=182.4 (19:30:29)
RACE_LADDERS = {  # column: (built at t, ladder seconds)
    "A_COL_018": (585, 564.5),
    "A_COL_037": (613, 592.6),
    "A_COL_019": (706, 685.2),
}
RACE_BUNDLE_FAIL = 706  # first_error re-raised → work item UNSUCCESSFUL
RACE_RETRY_SETUP = 711  # attempt 2 dofn setup start (19:36:07)
RACE_REEMBED = (744, 900)  # b1_embed_done seconds=155.5 (vs 23.5 s cold)
RACE_CACHE_HITS = (957, 1006, 1013)  # freetext_pool_cache_hit x3
RACE_A15_LADDER = (1013, 1083)  # A_COL_015 built, seconds=70.1
RACE_STORE_WRITTEN = 1188  # freetext_pool_store_written rows=4 (19:44:04)
# Fit-wait window = time between the FIRST and the LAST measure =
# (_UNFITTABLE_RETRY_ATTEMPTS - 1) x _UNFITTABLE_RETRY_WAIT_S.
OLD_WINDOW_S = (6 - 1) * 20.0  # before ADR 0033: 100 s
NEW_WINDOW_S = (12 - 1) * 20.0  # after ADR 0033: 220 s
NEEDED_WINDOW_S = RACE_T2_FIT - RACE_T1_SPAWN  # 161 s

# --- MEASURED (both runs — identical pool plans) ----------------------------
POOL_COLUMNS = ("B_COL_007", "A_COL_015", "A_COL_018", "A_COL_019", "A_COL_037")
POOL_TABLES = ("C_TABLE", "A_TABLE", "A_TABLE", "A_TABLE", "A_TABLE")
SOURCE_FILTER_DISTINCT = (3_030, 4_022, 45_708, 144_039, 73_231)  # freetext_pool_source_filter size=
POOL_TARGET = (512, 94, 512, 512, 512)  # freetext_pool_built target=
A15_SAMPLE_DISTINCT = 94  # the 10k-sample cardinality that sized the target
POOL_CAP = 512  # FREE_TEXT_POOL_MAX
FORMAT_REJECTED_1M = (9, 46, 45, 278, 385)
FORMAT_REJECTED_10M = (15, 59, 61, 301, 386)
A37_PARSED = 393  # both runs; attempts=3; shape fallback both runs
LADDER_SECONDS_1M = (428.0, 70.1, 564.5, 685.2, 592.6)
LADDER_SECONDS_10M = (303.7, 307.4, 442.9, 536.8, 452.7)

# --- MEASURED (freetext_crosscheck, 10M run, A_COL_019 = COL_027 in oss) ----
A19_SOURCE_LEN = {"p05": 18, "p50": 34, "p95": 35, "max": 35}
A19_SYNTH_LEN = {"p05": 29, "p50": 34, "p95": 42, "max": 62}
A19_SYNTH_MAX_1M = 50

# --- MEASURED (job 2026-08-26_05_01_16-3186876581127148459, 10M/table) -----
# minutes since job create/start 12:01:16Z; JOB_STATE_DONE at 13:34:10Z.
WALL_MIN_10M = 92.9
PHASES_10M = (  # (label, start, end, class)
    ("worker startup (image pull)", 0.0, 15.4, "beam"),
    ("C_TABLE pool branch", 15.4, 22.4, "gpu"),
    ("A_TABLE pool branch", 15.4, 32.3, "gpu"),
    ("C_TABLE generate 10M", 22.95, 46.4, "cpu"),
    ("C_TABLE PK enforce (CombineByPk)", 50.7, 58.7, "beam"),
    ("A_TABLE generate setup (FK pool bind)", 60.0, 63.25, "cpu"),
    ("A_TABLE generate 10M", 63.25, 79.7, "cpu"),
    ("BQ FILE_LOADS + cleanup", 79.7, 92.9, "beam"),
)
GPU_BUSY_10M = ((16.1, 17.0), (17.9, 25.75))  # embeds; vllm_spawn → last pool
GPU_BILLED_MIN_10M = 16_345 / 60  # TotalGpuTime 16,345 GPU-s, 4 x T4
ROWS_PER_S_10M = {"C_TABLE": 10e6 / (23.45 * 60), "A_TABLE": 10e6 / (16.4 * 60)}
BATCH_P50_S_10M = 25.3  # batch_done seconds, 10k rows, both tables

# --- CONCEPT (seeded; no measured numbers) ----------------------------------
# Three length distributions that teach where text_shapes.length_ceiling
# fires: (a) a fixed-width field folds its upper tail onto the width
# (p95 == max, wide spread below) → ceiling; (b) free-length prose has a
# lone maximum (p95 < max) → none; (c) a narrow band (p05 within
# max(4, max/4) of max) is a width the shape template carries → none.
CONCEPT_SEED = 33
CONCEPT_N = 600
CONCEPT_WIDTH = 35


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
def fig_pool_race():
    """Claim: thread 1 gave up 60 s before the card became fittable; the
    bundle failed after three finished ladders; the retry landed a 70 s
    ladder eight minutes later."""
    fig, ax = plt.subplots(figsize=(13.5, 5.8), facecolor=SURFACE)
    rows = []  # (label, start, end, colour, text)
    rows.append(("thread 1: setup + fit-wait", RACE_T1_SETUP, RACE_T1_RAISE, ORANGE,
                 "5 x 20 s waits → raise at t+191"))
    rows.append(("thread 2: setup → vllm_ready", RACE_T1_RAISE, RACE_VLLM_READY, BLUE,
                 "fittable at t+251 (0.85, 14.1 GiB free)"))
    for col, (built, secs) in RACE_LADDERS.items():
        rows.append((f"ladder {col}", built - secs, built, AQUA, f"{secs:.0f} s"))
    rows.append(("attempt 2: re-embed", RACE_REEMBED[0], RACE_REEMBED[1], ORANGE,
                 "155 s (23 s cold)"))
    rows.append(("attempt 2: cache hits x3", RACE_CACHE_HITS[0], RACE_CACHE_HITS[-1],
                 BLUE, ""))
    rows.append(("ladder A_COL_015 (attempt 2)", RACE_A15_LADDER[0], RACE_A15_LADDER[1],
                 AQUA, "70 s"))
    n = len(rows)
    for i, (label, s, e, colour, text) in enumerate(rows):
        y = n - 1 - i
        ax.barh(y, e - s, left=s, height=0.55, color=colour, zorder=3)
        ax.text(s - 8, y, label, ha="right", va="center", color=INK, fontsize=9)
        if text:
            ax.text(e + 8, y, text, ha="left", va="center", color=MUTED, fontsize=8.5)
    for t in RACE_T1_WAITS:
        ax.plot([t], [n - 1], marker="|", color=INK, markersize=12, zorder=4)
    ax.axvline(RACE_BUNDLE_FAIL, color=ORANGE, linewidth=1.6, linestyle=(0, (4, 3)),
               zorder=2)
    ax.text(RACE_BUNDLE_FAIL + 6, n - 0.35, "bundle FAILS (t+706):\nthread 1's error re-raised\nafter every sibling landed",
            color=ORANGE, fontsize=8.5, fontweight="600", va="top", linespacing=1.3)
    ax.axvline(RACE_STORE_WRITTEN, color=BLUE, linewidth=1.2, zorder=2)
    ax.text(RACE_STORE_WRITTEN + 8, 1.0, "pools stored\nt+1188", color=BLUE,
            fontsize=8.5, ha="left", va="center")
    # The window that would have covered it.
    ax.annotate("", xy=(RACE_T1_SPAWN + NEW_WINDOW_S, n + 0.15),
                xytext=(RACE_T1_SPAWN, n + 0.15),
                arrowprops={"arrowstyle": "<->", "color": AQUA, "linewidth": 1.4})
    ax.text(RACE_T1_SPAWN + NEW_WINDOW_S / 2, n + 0.25,
            f"ADR 0033 window {NEW_WINDOW_S:.0f} s (needed {NEEDED_WINDOW_S:.0f} s; old {OLD_WINDOW_S:.0f} s)",
            ha="center", va="bottom", color=AQUA, fontsize=8.5, fontweight="600")
    ax.set_xlim(-260, RACE_STORE_WRITTEN + 200)
    ax.set_ylim(-0.9, n + 0.9)
    ax.set_yticks([])
    ax.set_xlabel("seconds since the A_TABLE pool branch started (19:24:16Z)",
                  color=MUTED, fontsize=9)
    _style(ax, grid_axis="x")
    _title(ax, "One lost fit race failed a bundle whose three sibling ladders had already finished",
           "2026-08-25 R6 1M — A_TABLE/BuildFreeTextPools: attempt 1 (t+0 … t+706) and the Dataflow retry (t+711 … t+1188)")
    fig.tight_layout()
    fig.savefig(ASSETS / "r6-scale-pool-race.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_pool_targets():
    """Claim: A_COL_015's pool target was its 10k-sample cardinality (94)
    while the source filter the same setup fetched held 4,022."""
    fig, ax = plt.subplots(figsize=(11.5, 5.2), facecolor=SURFACE)
    x = np.arange(len(POOL_COLUMNS))
    w = 0.36
    ax.bar(x - w / 2, SOURCE_FILTER_DISTINCT, width=w, color=BLUE, zorder=3,
           label="source-filter distinct (freetext_pool_source_filter size=)")
    ax.bar(x + w / 2, POOL_TARGET, width=w, color=AQUA, zorder=3,
           label="pool target (freetext_pool_built target=)")
    ax.axhline(POOL_CAP, color=MUTED, linewidth=1.0, linestyle=(0, (4, 3)), zorder=2)
    ax.text(len(POOL_COLUMNS) - 0.55, POOL_CAP * 1.12, f"cap {POOL_CAP}", color=MUTED,
            fontsize=8.5, ha="right")
    for i, (d, t) in enumerate(zip(SOURCE_FILTER_DISTINCT, POOL_TARGET, strict=True)):
        ax.text(i - w / 2, d * 1.15, f"{d:,}", ha="center", color=INK, fontsize=8.5)
        ax.text(i + w / 2, t * 1.15, f"{t}", ha="center", color=INK, fontsize=8.5)
    i15 = POOL_COLUMNS.index("A_COL_015")
    ax.annotate(f"target = sample distinct {A15_SAMPLE_DISTINCT}\n(Tier-2 stats absent)\n→ ADR 0033: min(4,022, cap) = {POOL_CAP}",
                xy=(i15 + w / 2, POOL_TARGET[i15]), xytext=(i15 - 0.85, 9_000),
                color=ORANGE, fontsize=8.8, fontweight="600", linespacing=1.3,
                arrowprops={"arrowstyle": "-|>", "color": ORANGE, "linewidth": 1.4})
    ax.set_yscale("log")
    ax.set_ylim(40, 400_000)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{c}\n{t}" for c, t in zip(POOL_COLUMNS, POOL_TABLES, strict=True)],
                       fontsize=9)
    ax.set_ylabel("distinct values (log)", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK, loc="upper left")
    _style(ax)
    _title(ax, "The one starved pool was sized by the sample, not by the filter it had just fetched",
           "both R6 runs (1M and 10M) built the same five LLM pools; four hit the 512 cap, A_COL_015 stopped at 94")
    fig.tight_layout()
    fig.savefig(ASSETS / "r6-scale-pool-targets.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_format_gate():
    """Claim: A_COL_037 rejected 98% of its parsed values for three rounds
    in both runs; A_COL_019 ran to 62 chars past a 35-char wall."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2), facecolor=SURFACE,
                                   gridspec_kw={"width_ratios": [1.25, 1]})
    x = np.arange(len(POOL_COLUMNS))
    w = 0.36
    ax1.bar(x - w / 2, FORMAT_REJECTED_1M, width=w, color=BLUE, zorder=3, label="1M run")
    ax1.bar(x + w / 2, FORMAT_REJECTED_10M, width=w, color=AQUA, zorder=3, label="10M run")
    i37 = POOL_COLUMNS.index("A_COL_037")
    ax1.annotate(f"{FORMAT_REJECTED_10M[i37]} of {A37_PARSED} parsed\n3 rounds x ~150 s → shape fallback\n(the clause example is 28 chars; the column is 31)",
                 xy=(i37 + w / 2, FORMAT_REJECTED_10M[i37]), xytext=(1.2, 330),
                 color=ORANGE, fontsize=8.8, fontweight="600", linespacing=1.3,
                 arrowprops={"arrowstyle": "-|>", "color": ORANGE, "linewidth": 1.4})
    ax1.set_xticks(x)
    ax1.set_xticklabels(POOL_COLUMNS, fontsize=9)
    ax1.set_ylabel("format_rejected (LLM values the gate refused)", color=MUTED, fontsize=9)
    ax1.legend(frameon=False, fontsize=9, labelcolor=INK, loc="upper left")
    _style(ax1)
    _title(ax1, "Format rejection is concentrated in one column",
           "freetext_pool_built format_rejected= per LLM pool, both runs")

    qs = ("p05", "p50", "p95", "max")
    y = np.arange(len(qs))[::-1]
    src = [A19_SOURCE_LEN[q] for q in qs]
    syn = [A19_SYNTH_LEN[q] for q in qs]
    for yi, s, t in zip(y, src, syn, strict=True):
        ax2.plot([s, t], [yi, yi], color=GRID, linewidth=2.2, zorder=2)
    ax2.scatter(src, y, color=BLUE, s=70, zorder=3, label="source")
    ax2.scatter(syn, y, color=ORANGE, s=70, zorder=3, label="synthetic (10M)")
    for yi, s, t in zip(y, src, syn, strict=True):
        ax2.text(s - 1.2, yi, f"{s}", ha="right", va="center", color=INK, fontsize=8.5)
        ax2.text(t + 1.2, yi, f"{t}", ha="left", va="center", color=INK, fontsize=8.5)
    ax2.axvline(A19_SOURCE_LEN["max"], color=BLUE, linewidth=1.2, linestyle=(0, (4, 3)))
    ax2.text(A19_SOURCE_LEN["max"] + 0.6, 3.42, "35-char wall\n(p95 == max)", color=BLUE,
             fontsize=8.5, va="top")
    ax2.set_yticks(y)
    ax2.set_yticklabels(qs, fontsize=9)
    ax2.set_xlim(10, 70)
    ax2.set_ylim(-0.6, 3.6)
    ax2.set_xlabel("value length (characters)", color=MUTED, fontsize=9)
    ax2.legend(frameon=False, fontsize=9, labelcolor=INK, loc="lower right")
    _style(ax2, grid_axis="x")
    _title(ax2, "A_COL_019: the prose gate had no ceiling",
           f"length quantiles, source vs synthetic; the 1M run reached {A19_SYNTH_MAX_1M}")
    fig.tight_layout()
    fig.savefig(ASSETS / "r6-scale-format-gate.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_where_time_went():
    """Claim: vLLM served pool builds for ~8 of the job's 93 minutes while
    four T4s were billed for 272 GPU-minutes."""
    fig, ax = plt.subplots(figsize=(13.5, 5.6), facecolor=SURFACE)
    colour = {"beam": BLUE, "gpu": ORANGE, "cpu": AQUA}
    n = len(PHASES_10M)
    for i, (label, s, e, cls) in enumerate(PHASES_10M):
        y = n - 1 - i
        ax.barh(y, e - s, left=s, height=0.55, color=colour[cls], zorder=3)
        ax.text(s - 0.8, y, label, ha="right", va="center", color=INK, fontsize=9)
        ax.text(e + 0.8, y, f"{e - s:.1f} min", ha="left", va="center", color=MUTED,
                fontsize=8.5)
    for lo, hi in GPU_BUSY_10M:
        ax.axvspan(lo, hi, color=ORANGE, alpha=0.12, zorder=0)
    busy = sum(hi - lo for lo, hi in GPU_BUSY_10M)
    ax.text((GPU_BUSY_10M[1][0] + GPU_BUSY_10M[1][1]) / 2, n + 0.05,
            f"GPU busy {busy:.1f} min\nbilled {GPU_BILLED_MIN_10M:.0f} GPU-min (4 x T4)",
            ha="center", va="bottom", color=ORANGE, fontsize=8.8, fontweight="600",
            linespacing=1.3)
    ax.text(WALL_MIN_10M + 8, n + 0.05,
            f"C_TABLE {ROWS_PER_S_10M['C_TABLE'] / 1e3:.1f}k rows/s\nA_TABLE {ROWS_PER_S_10M['A_TABLE'] / 1e3:.1f}k rows/s\nbatch p50 {BATCH_P50_S_10M:.0f} s / 10k rows",
            ha="right", va="bottom", color=MUTED, fontsize=8.5, linespacing=1.3)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (BLUE, ORANGE, AQUA)]
    ax.legend(handles, ["Beam / IO", "GPU (embed + vLLM pool ladders)", "CPU generation"],
              frameon=False, fontsize=9, labelcolor=INK, loc="lower left", ncol=3,
              bbox_to_anchor=(0.0, -0.32))
    ax.set_xlim(-26, WALL_MIN_10M + 9)
    ax.set_ylim(-0.9, n + 1.2)
    ax.set_yticks([])
    ax.set_xlabel("minutes since job start (12:01:16Z)", color=MUTED, fontsize=9)
    _style(ax, grid_axis="x")
    _title(ax, "The GPU served pool builds for 9 of 93 minutes",
           "2026-08-26 R6 10M — one Dataflow job, two tables, wave 0 → wave 1 (ADR 0030); phases from worker milestones + job graph")
    fig.tight_layout()
    fig.savefig(ASSETS / "r6-scale-where-time-went.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def _ceiling(lengths):
    """Mirror of text_shapes.length_ceiling on a length array."""
    ls = sorted(lengths)
    last = len(ls) - 1
    p05, p95, top = ls[int(0.05 * last)], ls[int(0.95 * last)], ls[-1]
    if p95 != top or top - p05 < max(4, top // 4):
        return None
    return top


def fig_length_ceiling_concept():
    """Concept: where `length_ceiling` fires — a fixed-width wall, a lone
    maximum, a narrow band."""
    rng = np.random.default_rng(CONCEPT_SEED)
    free = np.clip(rng.lognormal(3.3, 0.35, CONCEPT_N).round().astype(int), 8, 90)
    wall = np.minimum(free, CONCEPT_WIDTH)  # the same prose stored in a 35-char field
    band = np.clip(rng.normal(33.5, 0.7, CONCEPT_N).round().astype(int), 32, 35)
    panels = (
        ("(a) fixed-width field: upper tail folded onto the width", wall),
        ("(b) free-length prose: a lone maximum", free),
        ("(c) narrow band: a width, not a wall", band),
    )
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.4), facecolor=SURFACE, sharey=True)
    for ax, (title, ls) in zip(axes, panels, strict=True):
        bins = np.arange(ls.min() - 0.5, ls.max() + 1.5, 1.0)
        ax.hist(ls, bins=bins, color=BLUE, zorder=3)
        c = _ceiling(ls)
        srt = np.sort(ls)
        p05, p95 = srt[int(0.05 * (len(ls) - 1))], srt[int(0.95 * (len(ls) - 1))]
        for q, lab, dx in ((p05, "p05", -0.4), (p95, "p95", 0.4)):
            ax.axvline(q, color=MUTED, linewidth=1.0, linestyle=(0, (3, 3)))
            ax.text(q + dx, 0.6, lab, transform=ax.get_xaxis_transform(),
                    color=MUTED, fontsize=8, ha="right" if dx < 0 else "left",
                    va="center")
        verdict = f"ceiling = {c}" if c else "no ceiling"
        ax.text(0.98, 0.9, verdict, transform=ax.transAxes, ha="right",
                color=ORANGE if c else AQUA, fontsize=10, fontweight="600")
        if c:
            ax.axvline(c, color=ORANGE, linewidth=1.6)
        ax.set_title(title, color=INK, fontsize=10, loc="left", pad=8)
        ax.set_xlabel("value length", color=MUTED, fontsize=9)
        _style(ax)
    axes[0].set_ylabel("values", color=MUTED, fontsize=9)
    fig.suptitle("length_ceiling fires only where p95 == max AND the spread below is wide (max(4, max/4))",
                 color=INK, fontsize=12, fontweight="600", x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(ASSETS / "r6-scale-length-ceiling-concept.png", dpi=160, facecolor=SURFACE)
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
    fig_pool_race()
    fig_pool_targets()
    fig_format_gate()
    fig_where_time_went()
    fig_length_ceiling_concept()
    for name in ("r6-scale-pool-race", "r6-scale-pool-targets", "r6-scale-format-gate",
                 "r6-scale-where-time-went", "r6-scale-length-ceiling-concept"):
        print("wrote", ASSETS / f"{name}.png")
