"""Regenerate the PK-capacity-under-random-draws figure (ADR 0035).

    uv run --no-sync python3 scripts/doc/make_pk_capacity_figures.py

Writes docs/designs/assets/pk-capacity-random-draws.png. Claims:

  left  (CONCEPT + one EVIDENCE point) — a PK tuple drawn at random with
        no collision rejection is balls-into-bins: capacity EQUAL to
        num_rows still loses 36.8% of rows as pk.duplicate; the
        2026-09-09 C_TABLE run sat at capacity/num_rows = 0.12 and lost
        87.9% (measured) against 87.9% (predicted).
  right (EVIDENCE) — C_TABLE's tuple capacity is (parent keys the child
        sees) x 12; at the flat 100k cap that is 1.2M tuples for 10M
        rows. The ADR 0035 ceiling (1M keys) lifts it to 12M — still 32%
        duplicates at 10M rows — so the largest gate-safe run under the
        ceiling is ~5.6M rows, and even the whole 10M-key parent leaves
        4% duplicates at 10M rows.

Palette matches the design-doc asset set: BLUE = target / truth,
ORANGE = defect, AQUA = healthy. OKLab separation check runs on every
regeneration.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sdfb_core.engines.pk_capacity import (
    FK_KEY_SAMPLE_CEILING,
    FK_KEY_SAMPLE_FLOOR,
    FK_KEY_SAMPLE_MARGIN,
    expected_duplicate_share,
    max_rows_under_share,
)

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "designs" / "assets"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045

# --- MEASURED (2026-09-09_09_00_54-16364509521974163594; the only place
# these numbers are typed). Sources: worker_logs.jsonl —
# `BlockerThresholdExceeded` (blocker_count, gate), `fk_key_pool_bound
# columns=D_COL_001 key_tuples=` (the flat cap), `relational_e2e` (the
# PK tuple), `generation_plan` (C_COL_002 / D_COL_018 kind=categorical).
RUN = "2026-09-09_09_00_54-16364509521974163594"
NUM_ROWS = 10_000_000              # --num_rows, every table in the launch
PK_DUPLICATES = 8_789_594          # blocker_count (all pk.duplicate)
BLOCKER_GATE = 0.2                 # blocker_failure_ratio (env=dev)
FK_KEYS_SEEN = 100_000             # key_tuples: the ADR 0030 side-input cap
PARENT_ROWS = 10_000_000           # B_TABLE landed rows = distinct PK keys

# DERIVED: rows that survived the barrier, and the factor the two
# categorical PK members contribute (survivors ~ capacity at N/K = 8.3).
SURVIVORS = NUM_ROWS - PK_DUPLICATES
OTHER_FACTOR = round(SURVIVORS / FK_KEYS_SEEN)


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


MILLION = 1_000_000


def _fmt_m(v: float) -> str:
    return f"{v / MILLION:.1f}M" if v >= MILLION else f"{v / 1e3:.0f}k"


# --------------------------------------------------------------------------
def panel_curve(ax):
    ratios = np.logspace(-1.5, 2, 400)  # capacity / num_rows
    shares = [expected_duplicate_share(1_000_000, int(1_000_000 * r)) for r in ratios]
    ax.plot(ratios, shares, color=BLUE, linewidth=2)
    ax.axhline(BLOCKER_GATE, color=ORANGE, linewidth=1.2, linestyle="--")
    ax.text(0.035, BLOCKER_GATE + 0.015, f"BLOCKER gate {BLOCKER_GATE:.0%}",
            color=ORANGE, fontsize=9)

    run_ratio = SURVIVORS / NUM_ROWS
    measured = PK_DUPLICATES / NUM_ROWS
    ax.scatter([run_ratio], [measured], s=70, color=ORANGE, zorder=5,
               edgecolor=SURFACE, linewidth=1.5)
    ax.annotate(
        f"{RUN[:10]} C_TABLE\ncapacity/rows = {run_ratio:.2f}\n"
        f"measured {measured:.1%} · predicted "
        f"{expected_duplicate_share(NUM_ROWS, SURVIVORS):.1%}",
        (run_ratio, measured), xytext=(0.5, 0.62), color=INK, fontsize=9,
        arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8},
    )
    one = expected_duplicate_share(1_000, 1_000)
    ax.scatter([1.0], [one], s=55, color=BLUE, zorder=5,
               edgecolor=SURFACE, linewidth=1.5)
    ax.annotate(f"capacity = rows\nstill {one:.1%} duplicates",
                (1.0, one), xytext=(1.6, 0.42), color=INK, fontsize=9,
                arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})
    margin = expected_duplicate_share(1_000, 1_000 * FK_KEY_SAMPLE_MARGIN)
    ax.scatter([FK_KEY_SAMPLE_MARGIN], [margin], s=55, color=AQUA, zorder=5,
               edgecolor=SURFACE, linewidth=1.5)
    ax.annotate(f"sizing margin x{FK_KEY_SAMPLE_MARGIN}\n{margin:.1%} duplicates",
                (FK_KEY_SAMPLE_MARGIN, margin), xytext=(14, 0.30), color=INK,
                fontsize=9,
                arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.8})

    ax.set_xscale("log")
    ax.set_xlim(0.03, 100)
    ax.set_ylim(0, 1.0)
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels([f"{t:.0%}" for t in ax.get_yticks()])
    ax.set_xticks([0.1, 1, 10, 100])
    ax.set_xticklabels(["0.1x", "1x", "10x", "100x"])
    ax.set_xlabel("PK tuple capacity / num_rows", color=MUTED, fontsize=9.5)
    ax.set_ylabel("expected pk.duplicate share", color=MUTED, fontsize=9.5)
    _style(ax)
    _title(ax, "Random PK draws are balls into bins",
           "share of N draws into K tuples that repeat: 1 - K/N * (1 - e^(-N/K))")


def panel_ladder(ax):
    rungs = [
        ("flat cap\n(ADR 0030)", FK_KEYS_SEEN, ORANGE),
        ("sizing ceiling\n(ADR 0035)", FK_KEY_SAMPLE_CEILING, BLUE),
        ("whole parent\n(co-partitioned join)", PARENT_ROWS, AQUA),
    ]
    xs = np.arange(len(rungs))
    caps = [k * OTHER_FACTOR for _, k, _ in rungs]
    ax.bar(xs, caps, width=0.56, color=[c for _, _, c in rungs], zorder=3)
    ax.axhline(NUM_ROWS, color=INK, linewidth=1.2, linestyle="--")
    ax.text(-0.42, NUM_ROWS * 1.12, f"num_rows = {_fmt_m(NUM_ROWS)}",
            color=INK, fontsize=9)
    for x, (_, keys, color), cap in zip(xs, rungs, caps, strict=True):
        share = expected_duplicate_share(NUM_ROWS, cap)
        safe = max_rows_under_share(cap, BLOCKER_GATE) or 0
        ax.text(x, cap * 1.25,
                f"{_fmt_m(keys)} keys x {OTHER_FACTOR} = {_fmt_m(cap)} tuples\n"
                f"at {_fmt_m(NUM_ROWS)} rows: {share:.0%} duplicates\n"
                f"gate-safe run: {_fmt_m(safe)} rows",
                ha="center", va="bottom", color=color, fontsize=9,
                fontweight="600")
    ax.set_yscale("log")
    ax.set_ylim(3e5, 3e9)
    ax.set_xticks(xs)
    ax.set_xticklabels([r[0] for r in rungs], color=INK, fontsize=9.5)
    ax.set_yticks([1e6, 1e7, 1e8, 1e9])
    ax.set_yticklabels(["1M", "10M", "100M", "1B"])
    ax.set_ylabel("C_TABLE PK tuple capacity (log)", color=MUTED, fontsize=9.5)
    _style(ax)
    _title(ax, "C_TABLE: parent keys the child sees x 12",
           f"PK (D_COL_001 → B_TABLE, C_COL_002, D_COL_018); floor "
           f"{_fmt_m(FK_KEY_SAMPLE_FLOOR)} · ceiling {_fmt_m(FK_KEY_SAMPLE_CEILING)}")


def fig_pk_capacity():
    fig, axes = plt.subplots(
        1, 2, figsize=(14.6, 5.6), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [3, 2.4]},
    )
    panel_curve(axes[0])
    panel_ladder(axes[1])
    fig.tight_layout(w_pad=3)
    fig.savefig(ASSETS / "pk-capacity-random-draws.png", dpi=160,
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
    print(
        f"{RUN}: {PK_DUPLICATES / NUM_ROWS:.1%} measured pk.duplicate; "
        f"predicted {expected_duplicate_share(NUM_ROWS, SURVIVORS):.1%} "
        f"at capacity {SURVIVORS:,} (other factor x{OTHER_FACTOR})"
    )
    for keys in (FK_KEY_SAMPLE_FLOOR, FK_KEY_SAMPLE_CEILING, PARENT_ROWS):
        cap = keys * OTHER_FACTOR
        print(
            f"  {keys:>10,} keys -> {cap:>12,} tuples: "
            f"{expected_duplicate_share(NUM_ROWS, cap):.1%} dup at {NUM_ROWS:,}; "
            f"gate-safe {max_rows_under_share(cap, BLOCKER_GATE):,} rows"
        )
    fig_pk_capacity()
    print("wrote", ASSETS / "pk-capacity-random-draws.png")


if __name__ == "__main__":
    main()
