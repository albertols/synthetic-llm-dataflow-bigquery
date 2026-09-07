"""Regenerate the generation-throughput figures (ADR 0034).

    uv run --no-sync python3 scripts/doc/make_throughput_figures.py

Writes PNGs into docs/designs/assets/. Every measured number below is
derived from the 2026-08-29 R6 pair's worker logs + reports
(runs/2026-08-29_07_33_36-13355700596190055276, cold, 10M rows/table;
runs/2026-08-29_09_49_17-12681434869969021419, warm re-trigger) — see
MEASURED. Do not hand-edit the constants; update them from a superseding
run and re-run. Projections are hatched and live in the PROJECTED block.

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

# --- MEASURED (cold job 2026-08-29_07_33_36-13355700596190055276) ------------
# minutes since job create 14:33:36.9Z; JOB_STATE_DONE 16:07:26 (5,629.7 s).
# Phase edges come from job_phases.phase_markers (launcher_start, autoscale,
# workers_ready, cleanup) and worker milestones (pool_branch_emitted,
# batch_done first/last, fk_key_pool_capped, TriggerLoadJobs).
WALL_MIN_COLD = 93.8
PHASES_COLD = (  # (label, start, end, class)
    ("flex-template launcher", 0.0, 10.3, "beam"),
    ("worker boot (image + driver)", 10.3, 15.9, "beam"),
    ("C_TABLE pool branch (critical path)", 15.9, 23.8, "gpu"),
    ("A_TABLE pool branch (parallel)", 15.9, 38.5, "gpu"),
    ("C_TABLE generate 10M", 23.9, 47.3, "cpu"),
    ("C_TABLE dedup + load", 47.3, 61.2, "shuffle"),
    ("A_TABLE generate 10M", 61.3, 80.5, "cpu"),
    ("A_TABLE dedup + load", 80.5, 92.6, "shuffle"),
    ("cleanup", 92.6, 93.8, "beam"),
)
GPU_BUSY_COLD = ((16.0, 17.5), (18.6, 28.4))  # embeds; vllm_spawn → last freetext_pool_built
GPU_BILLED_MIN_COLD = 16_420 / 60  # TotalGpuTime 16,420 GPU-s
AUTOSCALE_BOOT_COLD = 27.6  # 3 harness boots 15:01:10-18 (2 → 4 workers)
IDENTIFIER_FETCH_COLD = (28.4, 33.9)  # A_COL_005 944,582 values, REST paging (15:01:59 → 15:07:29)

# --- MEASURED (warm job 2026-08-29_09_49_17-12681434869969021419) ------------
# minutes since job create 16:49:17Z; JOB_STATE_DONE 18:15:23 (5,165.5 s).
WALL_MIN_WARM = 86.1
PHASES_WARM = (
    ("launcher + worker boot", 0.0, 14.3, "beam"),
    ("C_TABLE generate 10M", 14.3, 38.6, "cpu"),
    ("C_TABLE dedup + load", 38.6, 52.8, "shuffle"),
    ("A_TABLE generate 10M", 52.9, 72.7, "cpu"),
    ("A_TABLE dedup + load", 72.7, 84.6, "shuffle"),
    ("cleanup", 84.6, 86.1, "beam"),
)
GPU_BILLED_MIN_WARM = 16_310 / 60  # TotalGpuTime 16,310 GPU-s, zero vLLM spawns
AUTOSCALE_BOOT_WARM = 19.0  # 3 harness boots 17:08:15-18
PRIOR_10M_WALL_MIN = 92.9  # 2026-08-26_05_01_16 R6 10M (docs/releases/v0.1.0)

# --- MEASURED (batch_done / batch_progress_compacted, 2-min buckets) --------
# rows/s per 2-min bucket since each stage's first batch_done; 1,000 batches
# of 10k rows per table. `--dax_workflow_worker_num_threads_per_worker=8`.
RAMP_C_COLD = (1667, 2250, 2833, 2833, 11500, 10583, 10417, 10917, 9583, 10500, 7917, 2333)
RAMP_C_WARM = (1500, 2417, 2833, 2000, 12167, 7833, 10500, 11417, 8750, 12333, 7417, 4167)
RAMP_A_COLD = (1083, 12333, 13667, 10167, 12000, 16667, 7500, 8333, 1583)
RAMP_A_WARM = (12667, 12750, 9750, 12000, 11583, 12083, 10250, 2250)
# minutes after the stage's first batch_done at which the two autoscaled
# workers' SDK harnesses registered ("Set shuffle service client total
# memory limit"): cold 15:03:58 / 15:06:20 / 15:06:53 vs 14:57:53;
# warm 17:11:02 / 17:15:09 / 17:16:11 vs 17:04:40.
SDK_REGISTER_C_COLD = (6.1, 8.5, 9.0)
SDK_REGISTER_C_WARM = (6.4, 10.5, 11.5)
AVG_CONCURRENCY = {"C cold": 19.0, "A cold": 28.6, "C warm": 20.4, "A warm": 32.8}
BATCH_SECONDS_STEADY = (26, 29)  # 10k-row batch_done seconds at full concurrency
BATCH_SECONDS_FIRST = 4.0  # first batch_done on an uncontended worker
ROWS_PER_S = {"C cold": 7_241, "A cold": 10_163, "C warm": 7_179, "A warm": 11_223}
WORKERS, VCPUS_PER_WORKER, THREADS_PER_WORKER = 4, 8, 8

# --- MEASURED (per-instance setup seconds, 32 GenerateRecordsDoFn/table) ---
SETUP_C_COLD = (18.2, 59.9, 60.6, 61.9, 62.1, 62.5, 64.9, 65.5, 66.2, 68.2, 70.4,
                71.1, 72.0, 72.0, 72.0, 74.7, 75.1, 75.3, 75.8, 76.0, 76.1, 76.6,
                77.2, 77.7, 89.9, 93.4, 99.2, 110.2, 127.2, 167.7, 176.4, 444.4)
SETUP_C_WARM = (50.6, 61.5, 61.6, 61.7, 64.3, 64.7, 67.0, 67.2, 75.6, 75.7, 75.8,
                76.0, 76.2, 76.2, 76.6, 76.7, 76.8, 77.1, 77.5, 77.5, 77.6, 77.7,
                78.3, 78.8, 80.8, 82.1, 92.6, 101.6, 130.6, 190.8, 203.3, 213.3)
# A_TABLE builds its engine in the first bundle (fk_side): b1_pools_built
# per instance (32) — the pool-branch instance (671 s cold) excluded.
POOLS_A_COLD = (37.9, 40.6, 40.7, 41.2, 41.4, 42.8, 43.0, 43.7, 44.1, 44.6, 46.3,
                46.4, 46.4, 47.1, 47.4, 48.7, 49.0, 49.4, 49.6, 51.4, 51.7, 52.1,
                55.2, 56.6, 57.4, 59.1, 86.2, 91.0, 101.4, 102.0, 169.1, 180.9)
POOLS_A_WARM = (37.6, 38.4, 39.4, 40.7, 40.9, 41.4, 42.2, 42.7, 43.4, 44.2, 44.6,
                45.1, 45.5, 47.4, 47.6, 47.6, 48.2, 48.3, 48.9, 49.2, 49.6, 50.0,
                50.5, 52.2, 52.6, 53.2, 53.3, 53.7, 57.4, 58.2, 59.0, 60.2)

# --- MEASURED (shuffle) ------------------------------------------------------
SHUFFLE_GB_COLD = 123.26  # TotalShuffleDataProcessed, both tables, all barriers
ROWS_PER_TABLE, TABLES = 10_000_000, 2
CHAINED_ROW_PASSES = 6  # 3 barriers x (write + read)
# --- PROJECTED (ADR 0034 D1; hatched) ---------------------------------------
SINGLE_ROW_PASSES = 2  # one barrier x (write + read)
TUPLE_PACK_RATIO = 0.5  # value tuple vs keyed dict per row (design estimate)
KEY_GROUP_BYTES_PER_ROW = 80  # (key tuple, 32-hex digest) x 2 groups, per row
# GIL ceiling: interpreters per worker → fleet rows/s, linear until the
# shuffle write / BigQuery load bind (unknown; hatched beyond the measured point).
INTERPRETERS_MEASURED = 1
FLEET_ROWS_PER_S_MEASURED = 10_500  # C_TABLE steady state (buckets 8-20 min)


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


# Same three-colour vocabulary as the R6 asset set: Beam/IO blue, GPU orange,
# CPU aqua; the dedup shuffle barriers are Beam work drawn hatched.
_CLASS_COLOUR = {"beam": BLUE, "gpu": ORANGE, "cpu": AQUA, "shuffle": BLUE}
_CLASS_HATCH = {"beam": None, "gpu": None, "cpu": None, "shuffle": "//"}


# --------------------------------------------------------------------------
def _timeline(ax, phases, wall, *, autoscale_at, gpu_busy=(), extra=()):
    n = len(phases)
    for i, (label, s, e, cls) in enumerate(phases):
        y = n - 1 - i
        ax.barh(y, e - s, left=s, height=0.55, color=_CLASS_COLOUR[cls], zorder=3,
                hatch=_CLASS_HATCH[cls], edgecolor=SURFACE if _CLASS_HATCH[cls] else "none")
        ax.text(s - 0.8, y, label, ha="right", va="center", color=INK, fontsize=8.8)
        ax.text(e + 0.8, y, f"{e - s:.1f} min", ha="left", va="center", color=MUTED,
                fontsize=8.3)
    for lo, hi in gpu_busy:
        ax.axvspan(lo, hi, color=ORANGE, alpha=0.10, zorder=0)
    ax.axvline(autoscale_at, color=INK, linewidth=1.1, linestyle=(0, (3, 3)), zorder=2)
    ax.text(autoscale_at + 0.5, n - 0.2, "2 → 4 workers\n(harness boots)", color=INK,
            fontsize=8.2, va="top", linespacing=1.25)
    for lo, hi, text in extra:
        ax.annotate("", xy=(hi, -0.55), xytext=(lo, -0.55),
                    arrowprops={"arrowstyle": "<->", "color": ORANGE, "linewidth": 1.2})
        ax.text(hi + 0.6, -0.55, text, ha="left", va="center", color=ORANGE,
                fontsize=8.2, fontweight="600")
    ax.set_xlim(-30, wall + 10)
    ax.set_ylim(-1.4, n + 0.6)
    ax.set_yticks([])
    _style(ax, grid_axis="x")


def fig_where_time_went():
    """Claim: warming removes only the 8-minute pool branch — CPU generation
    and the dedup shuffle barriers are ~70 % of both jobs, and the GPU
    served pool ladders for ~10 of the cold job's 94 minutes."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13.5, 9.4), facecolor=SURFACE,
                                   gridspec_kw={"height_ratios": [9, 6]})
    _timeline(ax1, PHASES_COLD, WALL_MIN_COLD, autoscale_at=AUTOSCALE_BOOT_COLD,
              gpu_busy=GPU_BUSY_COLD,
              extra=((IDENTIFIER_FETCH_COLD[0], IDENTIFIER_FETCH_COLD[1],
                      "A_COL_005: 944k values via REST (5.5 min)"),))
    busy = sum(hi - lo for lo, hi in GPU_BUSY_COLD)
    ax1.text(WALL_MIN_COLD + 9, len(PHASES_COLD) + 0.05,
             f"GPU busy {busy:.1f} min (shaded) · billed {GPU_BILLED_MIN_COLD:.0f} GPU-min (4 x T4)",
             ha="right", va="bottom", color=ORANGE, fontsize=8.6, fontweight="600")
    _title(ax1, f"Cold: {WALL_MIN_COLD:.1f} min — generation 42.7 min, dedup barriers 26.0 min, startup 15.9 min",
           f"2026-08-29_07_33_36 R6 10M/table, cold pools (prior 10M run: {PRIOR_10M_WALL_MIN:.1f} min — no regression)")
    ax1.set_xlabel("minutes since job create (14:33:37Z)", color=MUTED, fontsize=9)

    _timeline(ax2, PHASES_WARM, WALL_MIN_WARM, autoscale_at=AUTOSCALE_BOOT_WARM)
    ax2.text(WALL_MIN_WARM + 9, len(PHASES_WARM) + 0.05,
             f"zero vLLM spawns · billed {GPU_BILLED_MIN_WARM:.0f} GPU-min",
             ha="right", va="bottom", color=ORANGE, fontsize=8.6, fontweight="600")
    _title(ax2, f"Warm: {WALL_MIN_WARM:.1f} min — the pool branch is the only phase warming removes",
           "2026-08-29_09_49_17, immediate re-trigger of the cold job (same digest, pools/chunks/stats from the store)")
    ax2.set_xlabel("minutes since job create (16:49:17Z)", color=MUTED, fontsize=9)
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=BLUE, edgecolor="none"),
               plt.Rectangle((0, 0), 1, 1, facecolor=ORANGE, edgecolor="none"),
               plt.Rectangle((0, 0), 1, 1, facecolor=AQUA, edgecolor="none"),
               plt.Rectangle((0, 0), 1, 1, facecolor=BLUE, hatch="//", edgecolor=SURFACE)]
    ax2.legend(handles, ["Beam / launch / IO", "GPU (embed + vLLM pool ladders)",
                         "CPU generation (one interpreter per worker)",
                         "dedup shuffle barriers + BigQuery load"],
               frameon=False, fontsize=8.8, labelcolor=INK, loc="lower left", ncol=2,
               bbox_to_anchor=(0.0, -0.42))
    fig.tight_layout()
    fig.savefig(ASSETS / "throughput-where-time-went.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_generation_ramp():
    """Claim: C_TABLE runs its first eight minutes at ~2.5k rows/s until
    the two autoscaled workers register; steady state is ~10.5k rows/s
    (C) / ~12k (A) — four interpreters for 32 vCPUs."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2), facecolor=SURFACE,
                                   sharey=True)
    for ax, series, regs, title in (
        (ax1, (("cold", RAMP_C_COLD, BLUE), ("warm", RAMP_C_WARM, AQUA)),
         (SDK_REGISTER_C_COLD, SDK_REGISTER_C_WARM), "C_TABLE — first generate stage of the job"),
        (ax2, (("cold", RAMP_A_COLD, BLUE), ("warm", RAMP_A_WARM, AQUA)), (),
         "A_TABLE — second stage, fleet already at 4 workers"),
    ):
        for label, values, colour in series:
            x = [2 * i + 1 for i in range(len(values))]
            ax.step(x, values, where="mid", color=colour, linewidth=2.0, label=label, zorder=3)
            ax.fill_between(x, values, step="mid", color=colour, alpha=0.12, zorder=1)
        for i, reg in enumerate(regs):
            colour = BLUE if i == 0 else AQUA
            for r in reg:
                ax.axvline(r, color=colour, linewidth=0.9, linestyle=(0, (2, 3)), zorder=2)
        ax.axhline(FLEET_ROWS_PER_S_MEASURED, color=MUTED, linewidth=1.0,
                   linestyle=(0, (4, 3)), zorder=2)
        ax.set_xlabel("minutes since the stage's first batch_done", color=MUTED, fontsize=9)
        _style(ax)
        ax.set_title(title, color=INK, fontsize=10.5, fontweight="600", loc="left")
        ax.legend(frameon=False, fontsize=9, labelcolor=INK, loc="upper right")
    ax1.text(23.5, FLEET_ROWS_PER_S_MEASURED * 1.04, f"steady {FLEET_ROWS_PER_S_MEASURED / 1e3:.1f}k rows/s",
             ha="right", color=MUTED, fontsize=8.5)
    ax1.annotate("autoscaled workers'\nSDK harnesses register",
                 xy=(SDK_REGISTER_C_COLD[1], 4_200), xytext=(12.6, 4_600),
                 color=INK, fontsize=8.2, linespacing=1.25,
                 arrowprops={"arrowstyle": "-|>", "color": INK, "linewidth": 1.0})
    ax1.set_ylabel("rows / s (2-min buckets, fleet-wide)", color=MUTED, fontsize=9)
    ax2.text(1.2, 17_300,
             f"avg batches in flight: C {AVG_CONCURRENCY['C cold']:.0f}-{AVG_CONCURRENCY['C warm']:.0f}, "
             f"A {AVG_CONCURRENCY['A cold']:.0f}-{AVG_CONCURRENCY['A warm']:.0f}\n"
             f"10k-row batch: {BATCH_SECONDS_FIRST:.0f} s alone, {BATCH_SECONDS_STEADY[0]}-{BATCH_SECONDS_STEADY[1]} s "
             f"at {THREADS_PER_WORKER} threads / interpreter",
             ha="left", va="top", color=MUTED, fontsize=8.3, linespacing=1.3)
    fig.suptitle("The first stage ramps for 8 minutes at a quarter of the fleet's steady state",
                 color=INK, fontsize=12.5, fontweight="600", x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(ASSETS / "throughput-generation-ramp.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_setup_cost():
    """Claim: thirty-two engine builds per table cost 2,940 thread-seconds
    cold and 2,824 warm; a process-shared engine pays one build per
    worker process."""
    fig, ax = plt.subplots(figsize=(12.5, 5.2), facecolor=SURFACE)
    groups = (
        ("C_TABLE setup\ncold", SETUP_C_COLD, BLUE),
        ("C_TABLE setup\nwarm", SETUP_C_WARM, AQUA),
        ("A_TABLE engine build\ncold", POOLS_A_COLD, BLUE),
        ("A_TABLE engine build\nwarm", POOLS_A_WARM, AQUA),
    )
    rng = np.random.default_rng(34)
    for i, (_label, values, colour) in enumerate(groups):
        vals = np.asarray(values)
        jitter = rng.uniform(-0.18, 0.18, size=vals.size)
        ax.scatter(np.full(vals.size, i) + jitter, vals, s=22, color=colour, alpha=0.75,
                   zorder=3, edgecolor="none")
        med = float(np.median(vals))
        ax.plot([i - 0.28, i + 0.28], [med, med], color=INK, linewidth=1.6, zorder=4)
        ax.text(i + 0.32, med, f"p50 {med:.0f} s", color=INK, fontsize=8.5, va="center")
        ax.text(i, 1.9, f"n={vals.size}\nΣ {vals.sum():,.0f} s", ha="center", va="bottom",
                color=MUTED, fontsize=8.5, linespacing=1.3)
    ax.axhline(SETUP_C_COLD[0], color=ORANGE, linewidth=1.2, linestyle=(0, (4, 3)), zorder=2)
    ax.text(3.45, SETUP_C_COLD[0] * 0.9,
            f"first, uncontended build: {SETUP_C_COLD[0]:.0f} s\n(what one shared build costs)",
            ha="right", va="top", color=ORANGE, fontsize=8.5, fontweight="600", linespacing=1.3)
    ax.set_yscale("log")
    ax.set_ylim(1.5, 900)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([g[0] for g in groups], fontsize=9)
    ax.set_ylabel("seconds per DoFn instance (log)", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "Thirty-two engine builds per table, serialized on process locks and one GIL",
           "dofn_setup_done seconds= (C_TABLE) and b1_pools_built seconds= (A_TABLE, deferred build) — 4 workers x 8 harness threads")
    fig.tight_layout()
    fig.savefig(ASSETS / "throughput-setup-cost.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_shuffle_barriers():
    """Claim: one full-row barrier instead of three cuts the dedup phase
    from six full-row shuffle passes per table to two — from 123 GB to
    roughly 45 GB per job — with the same envelopes and counts."""
    fig, ax = plt.subplots(figsize=(12.0, 5.0), facecolor=SURFACE)
    per_pass_gb = SHUFFLE_GB_COLD / CHAINED_ROW_PASSES  # measured mean per full-row pass (both tables)
    chained = SHUFFLE_GB_COLD
    single_rows = per_pass_gb * SINGLE_ROW_PASSES * TUPLE_PACK_RATIO
    single_keys = KEY_GROUP_BYTES_PER_ROW * ROWS_PER_TABLE * TABLES * 2 / 1e9  # x2: write + read
    single = single_rows + single_keys
    bars = (
        ("exact_chained\n(R6 pair, measured)", chained, False),
        ("exact — single barrier\n(ADR 0034, projected)", single, True),
    )
    for i, (_label, gb, projected) in enumerate(bars):
        ax.bar(i, gb, width=0.5, color=BLUE if not projected else AQUA, zorder=3,
               hatch="//" if projected else None, edgecolor=SURFACE if projected else "none")
        ax.text(i, gb + 3, f"{gb:.0f} GB", ha="center", color=INK, fontsize=10, fontweight="600")
    ax.text(0, chained / 2, "3 barriers x (write + read)\n= 6 full-row passes / table\nrows as keyed dicts",
            ha="center", va="center", color=SURFACE, fontsize=9, linespacing=1.35)
    ax.text(1, single / 2, "1 barrier x (write + read)\n= 2 full-row passes / table\nvalue tuples + key-only groups",
            ha="center", va="center", color=INK, fontsize=9, linespacing=1.35)
    ax.text(1.42, chained * 0.92,
            f"measured: TotalShuffleDataProcessed {SHUFFLE_GB_COLD:.1f} GB (cold job)\n"
            f"≈ {per_pass_gb:.1f} GB per full-row pass over 2 x 10M rows\n"
            f"projection: {TUPLE_PACK_RATIO:.0%} bytes per row (tuple vs dict) "
            f"+ {KEY_GROUP_BYTES_PER_ROW} B/row of key groups\n"
            "hatched = design estimate until the next run's counter",
            ha="right", va="top", color=MUTED, fontsize=8.5, linespacing=1.35)
    ax.set_xlim(-0.6, 1.6)
    ax.set_ylim(0, chained * 1.18)
    ax.set_xticks((0, 1))
    ax.set_xticklabels([b[0] for b in bars], fontsize=9.5)
    ax.set_ylabel("Dataflow Shuffle bytes per job (GB)", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "One dedup barrier instead of three: six full-row shuffle passes per table become two",
           "uniqueness_mode=exact before/after ADR 0034 — same row.duplicate / pk.duplicate / identity.unique envelopes, exact counts")
    fig.tight_layout()
    fig.savefig(ASSETS / "throughput-shuffle-barriers.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_gil_ceiling():
    """Claim: one Python interpreter per worker caps the fleet's
    generation near 10.5k rows/s; every other lever in this ADR trims
    minutes around that stage, only more interpreters move it."""
    fig, ax = plt.subplots(figsize=(11.5, 5.0), facecolor=SURFACE)
    interpreters = np.array([1, 2, 4, 8])
    fleet = FLEET_ROWS_PER_S_MEASURED * interpreters / INTERPRETERS_MEASURED
    ax.bar(0, fleet[0], width=0.55, color=AQUA, zorder=3)
    for i in range(1, len(interpreters)):
        ax.bar(i, fleet[i], width=0.55, color=AQUA, alpha=0.55, hatch="//",
               edgecolor=SURFACE, zorder=3)
    for i, f in enumerate(fleet):
        ax.text(i, f + 1_500, f"{f / 1e3:.1f}k rows/s", ha="center", color=INK, fontsize=9.5,
                fontweight="600")
    ax.text(0, fleet[0] + 9_500, "measured — R6 pair\n(1 SDK process / worker)", ha="center",
            va="bottom", color=INK, fontsize=8.8, linespacing=1.3)
    ax.text(0.55, fleet[-1] * 0.70,
            "projected (hatched): linear in interpreters\n"
            "until the shuffle write / BigQuery load bind —\n"
            "the R7m experiment reads the real number.\n"
            f"{WORKERS} workers x {VCPUS_PER_WORKER} vCPUs billed; "
            f"{WORKERS * INTERPRETERS_MEASURED} interpreters busy today.",
            ha="left", va="center", color=MUTED, fontsize=8.6, linespacing=1.4)
    ax.set_xticks(range(len(interpreters)))
    ax.set_xticklabels([f"{n} interpreter{'s' if n > 1 else ''}\nper worker" for n in interpreters],
                       fontsize=9.5)
    ax.set_ylabel("fleet generation rate (rows / s)", color=MUTED, fontsize=9)
    ax.set_ylim(0, fleet[-1] * 1.15)
    _style(ax)
    _title(ax, "Generation is bound by one interpreter per worker, not by the GPU",
           "10k-row batches take 26-29 s at 8 threads on one GIL (4 s alone); sdk_containers=multi is the lever, gated on the R7m acceptance run")
    fig.tight_layout()
    fig.savefig(ASSETS / "throughput-gil-ceiling.png", dpi=160, facecolor=SURFACE)
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
    fig_where_time_went()
    fig_generation_ramp()
    fig_setup_cost()
    fig_shuffle_barriers()
    fig_gil_ceiling()
    for name in ("throughput-where-time-went", "throughput-generation-ramp",
                 "throughput-setup-cost", "throughput-shuffle-barriers",
                 "throughput-gil-ceiling"):
        print("wrote", ASSETS / f"{name}.png")
