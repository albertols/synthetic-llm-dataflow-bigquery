"""Regenerate the constraint-router figures (ADR 0028).

    uv run --no-sync python3 scripts/doc/make_constraint_router_figures.py

Writes PNGs into docs/designs/assets/. Both figures are EVIDENCE: the
2026-08-21 first PK+FK run (the only place those numbers are typed),
read from `runs/2026-08-21_14_54_30-1966084111444777604/`
(_full_report.md annexes + worker_logs.jsonl milestones). Claims:

  1. constraint-router-pk-blocker.png — a 512-cap constrained pool
     cannot serve a 1M-row PK: 999 488 of 1 000 000 rows were
     `pk.duplicate` by construction, while every pattern clause in the
     run defines a value space (1e19-1e36) that a CPU sampler covers
     exactly; the GPU was active only during the 6.6-min pool build and
     all 8 generate workers logged `llm_route_unused`.
  2. constraint-router-outcomes.png — only the two pattern-guided
     clauses controlled decoding (format_rejected=0); the prose-only
     clause leaked 12 rejects past prompting; the binary clause never
     reached the LLM (pre-LLM `is_binary_class` fallback) and its pool
     copied source values verbatim (copy_ratio_substantive=1.0) — the
     copying its own privacy note forbade.

Palette matches the design-doc asset set: BLUE = source truth / target,
ORANGE = defect / GPU-billed, AQUA = healthy / CPU. OKLab separation
check runs on every regeneration.
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

# --- MEASURED (2026-08-21_14_54_30-1966084111444777604; the only place
# these numbers are typed). Sources: _full_report.md §0-§5 + annexes
# (gcp_metrics / offline_metrics), worker_logs.jsonl SDFB_MILESTONE
# lines (freetext_pool_built / freetext_pool_binary_fallback /
# llm_route_unused / vllm_ready) and the APIServer loggers.py throughput
# lines.
RUN = "2026-08-21_14_54_30-1966084111444777604"
NUM_ROWS = 1_000_000
POOL_CAP = 512                    # _FREE_TEXT_POOL_MAX (engine.py)
LANDED = 512                      # valid_count (validation_runs)
DLQ_PK_DUPLICATE = 999_488        # dlq_by_rule {"pk.duplicate": ...}
SOURCE_DISTINCT_PK = 52_549       # freetext_pool_source_filter PK_COL
GPU_SECONDS = 1_586               # TotalGpuTime, all inside pool build
LLM_ROUTE_UNUSED_WORKERS = 8      # WARNING count, generate workers

# Wall-time anatomy (UTC timestamps from worker_logs.jsonl milestones):
# 21:54:31 launcher -> 22:09:42 workers ready -> 22:14:18 vllm_ready
# -> 22:20:57 b1_pools_built -> 22:21:45 first batch -> 22:27:38 last
# batch_done -> 22:31:14 BlockerGate raise -> 22:32:15 job end.
PHASES = (  # (label, seconds, kind: cpu|gpu|platform)
    ("launch + worker boot", 911, "platform"),
    ("embed + filters + vLLM ignition", 276, "gpu"),
    ("LLM pool build (3 columns)", 399, "gpu"),
    ("pool emit / worker warm-up", 48, "cpu"),
    ("generation (1000 batches)", 353, "cpu"),
    ("validation + EnforceUniqueness", 216, "cpu"),
    ("gate raise + cleanup", 61, "platform"),
)

# Per-constraint-column outcomes. build_s derives from the sequential
# freetext_pool_built timestamps on the single vLLM worker
# (vllm_ready 22:14:18 -> 22:15:50 -> 22:19:37 -> 22:20:57); the
# milestone's own `seconds` field is the ladder clock, which absorbs the
# ignition wait (269.0 / 495.7 / 575.5 cumulative from branch start).
# landed_distinct: offline_metrics columns annex (512-row partial).
CONSTRAINT_COLS = (
    # (column, clause kind, build_s, format_rejected, landed_distinct)
    ("COL_053", "prose (charset)", 92, 12, 114),
    ("PK_COL", "pattern-guided", 227, 0, 512),
    ("COL_063", "pattern-guided", 80, 0, 329),
    ("COL_047", "binary fallback", 0, 0, 58),
)
COL_047_COPY_SUBSTANTIVE = 1.0    # gcp_metrics memorization annex
COL_047_SOURCE_DOMAIN = 19_815    # freetext_pool_source_filter COL_047

# Pattern-defined value spaces, computed from the DDL clauses (typed
# once, as formulas — not measured, but derived from the run's clauses):
SPACE_E2F = 3 * 16**20            # ^(E2F[13][0-9A-F]{20}|2301[0-9A-F]{20})$
SPACE_UUID4 = 4 * 16**30          # RFC 9562 v4: 122 random bits
SPACE_BYTE_TAIL = 256**8          # S1 prefix + 8 free bytes
SCI_NOTATION_MIN = 1e7            # label threshold: sci notation above


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
    ax.set_title(text, color=INK, fontsize=12.5, fontweight="600", loc="left",
                 pad=26)
    if sub:
        ax.text(0, 1.03, sub, transform=ax.transAxes, color=MUTED, fontsize=9,
                va="bottom")


# --------------------------------------------------------------------------
def fig_pk_blocker():
    fig, axes = plt.subplots(
        1, 2, figsize=(13.5, 5.0), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [3, 3]},
    )

    # Panel A — the pool-cap geometry, log scale.
    ax = axes[0]
    rows = (
        ("UUIDv4 space (COL_063)", SPACE_UUID4, BLUE),
        ("E2F/2301 space (PK_COL)", SPACE_E2F, BLUE),
        ("8-byte tail space (COL_047)", SPACE_BYTE_TAIL, BLUE),
        ("rows requested", NUM_ROWS, INK),
        ("PK_COL source distinct", SOURCE_DISTINCT_PK, MUTED),
        ("pk.duplicate DLQ", DLQ_PK_DUPLICATE, ORANGE),
        ("pool cap / rows landed", POOL_CAP, ORANGE),
    )
    y = np.arange(len(rows))[::-1]
    vals = [float(v) for _, v, _ in rows]
    ax.barh(y, vals, height=0.55, color=[c for _, _, c in rows])
    for yi, (_, v, _) in zip(y, rows, strict=True):
        ax.text(v * 1.8, yi, f"{v:.2e}" if v >= SCI_NOTATION_MIN else f"{v:,}",
                va="center", color=INK, fontsize=8.5)
    ax.set_yticks(y, [n for n, _, _ in rows], fontsize=9, color=MUTED)
    ax.set_xscale("log")
    ax.set_xlim(1e2, 1e42)
    ax.set_xlabel("unique values (log)", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "512 values cannot key 1M rows",
           "pattern clauses define 1e19-1e36 spaces; the pool caps at 512")

    # Panel B — where the 37.7 min went, and where the GPU was.
    ax = axes[1]
    kinds = {"cpu": AQUA, "gpu": ORANGE, "platform": MUTED}
    y = np.arange(len(PHASES))[::-1]
    ax.barh(y, [s for _, s, _ in PHASES], height=0.55,
            color=[kinds[k] for _, _, k in PHASES])
    for yi, (_, s, _) in zip(y, PHASES, strict=True):
        ax.text(s + 12, yi, f"{s} s", va="center", color=INK, fontsize=8.5)
    ax.set_yticks(y, [n for n, _, _ in PHASES], fontsize=8.5, color=MUTED)
    ax.set_xlabel("wall seconds (total 2 264 s)", color=MUTED, fontsize=9)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in
               (ORANGE, AQUA, MUTED)]
    ax.legend(handles,
              [f"GPU-resident ({GPU_SECONDS} GPU-s billed here)",
               "CPU work (llm_route_unused x"
               f"{LLM_ROUTE_UNUSED_WORKERS} workers)",
               "platform overhead"],
              frameon=False, fontsize=8.5, labelcolor=INK, loc="lower right")
    _style(ax)
    _title(ax, "All GPU seconds sit in the pool phase",
           "generation itself ran CPU-only on warm pools")

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "constraint-router-pk-blocker.png", dpi=160,
                facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_outcomes():
    fig, axes = plt.subplots(
        1, 2, figsize=(13.5, 4.8), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [3, 3]},
    )
    route_color = {
        "pattern-guided": AQUA, "prose (charset)": BLUE,
        "binary fallback": ORANGE,
    }

    # Panel A — T4 seconds per 512-value pool, by clause kind.
    ax = axes[0]
    x = np.arange(len(CONSTRAINT_COLS))
    secs = [s for _, _, s, _, _ in CONSTRAINT_COLS]
    kinds = [k for _, k, _, _, _ in CONSTRAINT_COLS]
    ax.bar(x, secs, width=0.55, color=[route_color[k] for k in kinds])
    for xi, (_col, _kind, s, rej, _) in enumerate(CONSTRAINT_COLS):
        top = f"{s} s" if s else "0 s (pre-LLM)"
        ax.text(xi, s + 6, top, ha="center", color=INK, fontsize=9)
        ax.text(xi, -34, f"rejects {rej}", ha="center", color=MUTED,
                fontsize=8)
    ax.set_xticks(x, [c for c, *_ in CONSTRAINT_COLS], fontsize=9,
                  color=MUTED)
    ax.set_ylim(0, 265)
    ax.set_ylabel("T4 seconds per 512-value pool", color=MUTED, fontsize=9)
    handles = [plt.Rectangle((0, 0), 1, 1, color=route_color[k])
               for k in ("pattern-guided", "prose (charset)",
                         "binary fallback")]
    ax.legend(handles, ["pattern-guided (xgrammar)", "prose clause",
                        "binary fallback (no LLM)"],
              frameon=False, fontsize=8.5, labelcolor=INK)
    _style(ax, grid_axis="y")
    _title(ax, "Guided decoding rejects nothing",
           "prose leaks 12 rejects; the binary clause never reached the LLM")

    # Panel B — what actually landed (512-row partial sample).
    ax = axes[1]
    order = sorted(CONSTRAINT_COLS, key=lambda t: -t[4])
    x = np.arange(len(order))
    landed = [d for *_, d in order]
    ax.bar(x, landed, width=0.55,
           color=[route_color[k] for _, k, *_ in order])
    ax.axhline(LANDED, color=INK, linewidth=1, linestyle=":")
    ax.text(len(order) - 0.55, LANDED + 8, f"rows in sample {LANDED}",
            color=INK, fontsize=8, ha="right")
    for xi, (_col, _, _, _, d) in enumerate(order):
        ax.text(xi, d + 8, str(d), ha="center", color=INK, fontsize=9)
    labels = [c for c, *_ in order]
    labels[labels.index("COL_047")] = (
        f"COL_047\ncopy={COL_047_COPY_SUBSTANTIVE:.0%} of "
        f"{COL_047_SOURCE_DOMAIN:,}-value domain"
    )
    ax.set_xticks(x, labels, fontsize=8.5, color=MUTED)
    ax.set_ylabel("distinct values landed (of 512 rows)", color=MUTED,
                  fontsize=9)
    _style(ax, grid_axis="y")
    _title(ax, "The fallback pool is verbatim source",
           "COL_047 landed 58 distinct real values — its clause said "
           "'never copied'")

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "constraint-router-outcomes.png", dpi=160,
                facecolor=SURFACE)
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
    fig_pk_blocker()
    fig_outcomes()
    for name in ("constraint-router-pk-blocker.png",
                 "constraint-router-outcomes.png"):
        print("wrote", ASSETS / name)


if __name__ == "__main__":
    main()
