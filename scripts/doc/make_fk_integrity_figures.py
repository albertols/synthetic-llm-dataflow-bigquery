"""Regenerate the referential-integrity figures (ADR 0031).

    uv run --no-sync python3 scripts/doc/make_fk_integrity_figures.py

Writes PNGs into docs/designs/assets/. Claims:

  1. fk-orphan-rate.png (EVIDENCE) — the 2026-08-23 relational run
     landed 817 627 orphan rows because its only FK edge was declared
     display-only; enforcing that same edge with v1 PER-COLUMN pools
     would have been WORSE (>=97% orphans, from the run's own measured
     cardinalities), because the child assembles a combination the
     parent never held. Joint key draws put it at 0 by construction.
  2. fk-feasible-set.png (CONCEPT) — why: the parent's key set is a
     sparse subset of the product grid, so an independent draw hits a
     real key with probability |K| / prod(d_c), which decays
     geometrically in the number of FK columns.
  3. fk-marginal-fit.png (CONCEPT) — drawing UNIFORMLY over parent keys
     buys integrity by wrecking the child's marginals; the IPF-fitted
     weights (Deming & Stephan 1940) keep both.

Palette matches the design-doc asset set: BLUE = source truth / target,
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

# --- MEASURED (2026-08-23_05_12_06-966936752936039329; the only place
# these numbers are typed). Sources: _full_report.md §0/§1/§4 + the
# gcp_A_TABLE / gcp_B_TABLE metrics annexes (per-column `distinct`),
# worker_logs.jsonl `relational_e2e` / `fk_model_pretty` milestones.
RUN = "2026-08-23_05_12_06-966936752936039329"
CHILD_ROWS = 1_000_000            # B_TABLE landing_rows
PARENT_ROWS = 1_000_000           # A_TABLE landing_rows
ORPHAN_ROWS = 817_627             # §1 live LEFT JOIN orphan check
EDGES_ENFORCED = 0                # relational_single_job edges=0
EDGES_INFORMATIONAL = 1           # fk_model_pretty, informational: true

# The edge: B_TABLE.(COL_005,COL_006,COL_008) -> A_TABLE.(same).
# Per-column DISTINCT counts as landed, from the metrics annexes.
FK_COLUMNS = (  # (column, parent distinct, child distinct)
    ("COL_005", 1, 1),
    ("COL_006", 1_664, 2_827),
    ("COL_008", 22_345, 166_928),
)
PARENT_GRID = 1 * 1_664 * 22_345  # product of the parent's per-column supports

# Upper bound on distinct parent key TUPLES: the parent landed 1M rows,
# so it holds at most 1M distinct 3-tuples. Using the bound makes the
# v1 estimate CONSERVATIVE (the true orphan rate can only be higher).
PARENT_KEYS_MAX = min(PARENT_ROWS, PARENT_GRID)
V1_HIT_MAX = PARENT_KEYS_MAX / PARENT_GRID


def _measured_orphan_rate() -> float:
    return ORPHAN_ROWS / CHILD_ROWS


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
    pad = 38 if sub and "\n" in sub else 26
    ax.set_title(text, color=INK, fontsize=12.5, fontweight="600", loc="left",
                 pad=pad)
    if sub:
        ax.text(0, 1.03, sub, transform=ax.transAxes, color=MUTED, fontsize=9,
                va="bottom")


# --------------------------------------------------------------------------
def fig_orphan_rate():
    fig, axes = plt.subplots(
        1, 2, figsize=(14.6, 5.4), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [3, 2.5]},
    )

    # Panel A — three regimes for the SAME declared edge.
    ax = axes[0]
    bars = (
        ("declared\ninformational\n(what ran)",
         _measured_orphan_rate(), ORANGE, False),
        ("enforced,\nper-column pools\n(v1)", 1 - V1_HIT_MAX, ORANGE, True),
        ("enforced,\njoint key tuples\n(v2)", 0.0, AQUA, False),
    )
    x = np.arange(len(bars))
    for xi, (_, rate, color, hatched) in zip(x, bars, strict=True):
        ax.bar(
            xi, rate, width=0.55, color=color,
            hatch="//" if hatched else None,
            edgecolor=SURFACE if hatched else color, linewidth=0,
        )
        label = f"{rate:.1%}" if rate else "0 by construction"
        ax.text(xi, rate + 0.03, label, ha="center", color=INK, fontsize=10,
                fontweight="600")
    ax.set_xticks(x, [b[0] for b in bars], fontsize=9, color=MUTED)
    ax.set_ylim(0, 1.14)
    ax.set_ylabel("share of child rows with no parent", color=MUTED,
                  fontsize=9)
    ax.text(
        1, -0.28, "hatched = derived bound, not measured: with per-column "
        "pools the\nchild hits a real key at most |parent keys| / grid of "
        "the time",
        ha="center", va="top", transform=ax.get_xaxis_transform(),
        color=MUTED, fontsize=8,
    )
    _style(ax)
    _title(ax, "Per-column enforcement is worse than none",
           f"run {RUN}\n{ORPHAN_ROWS:,} of {CHILD_ROWS:,} child rows "
           f"referenced a parent that does not exist")

    # Panel B — the cardinalities behind it.
    ax = axes[1]
    y = np.arange(len(FK_COLUMNS))[::-1]
    height = 0.34
    ax.barh(y + height / 2, [c[1] for c in FK_COLUMNS], height=height,
            color=BLUE, label="parent landed distinct")
    ax.barh(y - height / 2, [c[2] for c in FK_COLUMNS], height=height,
            color=MUTED, label="child landed distinct")
    for yi, (_, pv, cv) in zip(y, FK_COLUMNS, strict=True):
        ax.text(pv * 1.25, yi + height / 2, f"{pv:,}", va="center",
                color=INK, fontsize=8.5)
        ax.text(cv * 1.25, yi - height / 2, f"{cv:,}", va="center",
                color=MUTED, fontsize=8.5)
    ax.set_yticks(y, [c[0] for c in FK_COLUMNS], fontsize=9, color=MUTED)
    ax.set_xscale("log")
    ax.set_xlim(0.5, 3e7)
    ax.set_xlabel("distinct values (log)", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK, loc="upper right")
    _style(ax, grid_axis="x")
    _title(ax, "The child invented values the parent lacks",
           f"product grid = {PARENT_GRID:,} combinations;\nthe parent holds "
           f"at most {PARENT_KEYS_MAX:,} of them")

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "fk-orphan-rate.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
# CONCEPT — seeded, deterministic, no measured numbers. Parameters chosen
# to make the geometry legible: a 24x18 grid is small enough to see every
# cell, and 90 keys (~21% occupancy) is the visual analogue of a real
# parent's sparse key set.
CONCEPT_SEED = 11
GRID_A, GRID_B = 24, 18
CONCEPT_KEYS = 90


def fig_feasible_set():
    rng = random.Random(CONCEPT_SEED)
    keys = set()
    while len(keys) < CONCEPT_KEYS:
        keys.add((rng.randrange(GRID_A), rng.randrange(GRID_B)))

    fig, axes = plt.subplots(
        1, 2, figsize=(13.5, 5.0), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [2.4, 3]},
    )

    # Panel A — the feasible set inside the product grid.
    ax = axes[0]
    grid = np.zeros((GRID_B, GRID_A))
    for a, b in keys:
        grid[b, a] = 1
    ax.imshow(
        grid, cmap=matplotlib.colors.ListedColormap([GRID, BLUE]),
        origin="lower", aspect="auto", interpolation="nearest",
    )
    ax.set_xlabel("values of FK column A", color=MUTED, fontsize=9)
    ax.set_ylabel("values of FK column B", color=MUTED, fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_color(GRID)
    hit = len(keys) / (GRID_A * GRID_B)
    _title(ax, "Parent keys are a sparse subset of the grid",
           f"blue = a key the parent holds ({len(keys)} of "
           f"{GRID_A * GRID_B} cells = {hit:.0%})")

    # Panel B — the hit probability, log scale, for three per-column
    # support sizes. The cliff is the point: it is not that "wide edges
    # are bad", it is that independence is only safe while the product
    # of supports stays BELOW the number of keys the parent holds.
    ax = axes[1]
    ks = np.arange(1, 7)
    keys_held = 1e6           # illustrative parent key count
    for support, color, dash in (
        (20, AQUA, None), (100, BLUE, (4, 2)), (1000, ORANGE, (1, 1.6)),
    ):
        hit = np.minimum(1.0, keys_held / support ** ks.astype(float))
        line, = ax.plot(ks, hit, marker="o", color=color, linewidth=2.2,
                        markersize=6, label=f"{support} values per column")
        if dash:
            line.set_dashes(dash)
    ax.axhline(1.0, color=INK, linewidth=1.6)
    ax.text(6.05, 1.0, "joint key-tuple draws\n(always 1.0)", color=INK,
            fontsize=8.5, va="center", ha="left")
    ax.set_yscale("log")
    ax.set_ylim(1e-9, 4.0)
    ax.set_xlim(0.8, 7.6)
    ax.set_xlabel("columns in the FK edge", color=MUTED, fontsize=9)
    ax.set_ylabel("P(an independent draw hits a real key)", color=MUTED,
                  fontsize=9)
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK, loc="lower left")
    _style(ax)
    _title(ax, "Independence survives only below the key count",
           "P(hit) = |parent keys| / prod(per-column supports) — each extra\n"
           "column divides it by that column's support")

    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "fk-feasible-set.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
# CONCEPT — the marginal cost of integrity. A skewed child marginal
# (Zipf-ish over 12 values) against a parent key set that pairs those
# values unevenly: uniform-over-keys flattens the child's shape, the
# IPF fit restores it. Seeded; no measured numbers.
MARGINAL_SEED = 5
MARGINAL_VALUES = 12
MARGINAL_KEYS = 240


def fig_marginal_fit():
    from sdfb_core.engines.fk_keys import FkKeyPool

    rng = random.Random(MARGINAL_SEED)
    a_values = [f"A{i}" for i in range(MARGINAL_VALUES)]
    # Zipf-ish child marginal on column A.
    weights = [1.0 / (i + 1) ** 1.4 for i in range(MARGINAL_VALUES)]
    total = sum(weights)
    target = [w / total for w in weights]
    child_rows = [
        {"A": rng.choices(a_values, weights=weights)[0], "B": rng.randrange(20)}
        for _ in range(4000)
    ]
    keys = set()
    while len(keys) < MARGINAL_KEYS:
        keys.add((rng.choice(a_values), rng.randrange(20)))
    keys = sorted(keys)

    pool = FkKeyPool.from_reference(("A", "B"), keys, child_rows)
    fitted = _share(pool.draw(20_000, random.Random(1)), a_values)
    uniform_pool = FkKeyPool.from_reference(("A", "B"), keys, [])
    uniform = _share(uniform_pool.draw(20_000, random.Random(2)), a_values)

    fig, ax = plt.subplots(figsize=(13.5, 4.8), facecolor=SURFACE)
    x = np.arange(len(a_values))
    width = 0.28
    ax.bar(x - width, target, width, color=BLUE, label="child's own marginal (target)")
    ax.bar(x, uniform, width, color=ORANGE,
           label="uniform over parent keys — integrity, wrong shape")
    ax.bar(x + width, fitted, width, color=AQUA,
           label="IPF-fitted weights — integrity AND shape")
    ax.set_xticks(x, a_values, fontsize=9, color=MUTED)
    ax.set_ylabel("share of child rows", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK)
    _style(ax)
    tv_uniform = 0.5 * sum(abs(u - t) for u, t in zip(uniform, target, strict=True))
    tv_fitted = 0.5 * sum(abs(f - t) for f, t in zip(fitted, target, strict=True))
    _title(ax, "Restricting to real parent keys need not cost the marginal",
           f"total variation vs target: uniform {tv_uniform:.3f}, "
           f"IPF-fitted {tv_fitted:.3f} (seeded concept figure)")
    fig.tight_layout(pad=1.4)
    fig.savefig(ASSETS / "fk-marginal-fit.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)
    print(f"  marginal TV: uniform {tv_uniform:.3f} -> fitted {tv_fitted:.3f}")


def _share(drawn, values) -> list[float]:
    counts = {v: 0 for v in values}
    for t in drawn:
        counts[t[0]] += 1
    return [counts[v] / len(drawn) for v in values]


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
        f"measured orphan rate {_measured_orphan_rate():.1%}; "
        f"per-column upper-bound hit rate {V1_HIT_MAX:.2%} "
        f"(orphans >= {1 - V1_HIT_MAX:.1%})"
    )
    fig_orphan_rate()
    fig_feasible_set()
    fig_marginal_fit()
    for name in ("fk-orphan-rate.png", "fk-feasible-set.png",
                 "fk-marginal-fit.png"):
        print("wrote", ASSETS / name)


if __name__ == "__main__":
    main()
