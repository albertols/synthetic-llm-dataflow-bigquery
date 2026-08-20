"""Regenerate the wave-4 measurement-and-mask-integrity figures (ADR 0026).

    uv run --no-sync python3 scripts/doc/make_wave4_figures.py

Writes PNGs into docs/designs/assets/. Two EVIDENCE figures carry the
2026-08-20 R1-pair measurements (the only place those numbers are typed);
two CONCEPT figures reproduce the two sampler defects mechanically with
seeded synthetic data run through the REAL repo code (no measured numbers).
Claims:

  1. wave4-panel-vs-truth.png  — on every column the crosscheck flagged as
     "mass-flattened", the exact full-table dominant-value share matches
     synthetic within 2 pp; the flagged numbers came from the crosscheck's
     storage-front sample.
  2. wave4-mask-entropy.png    — the 1024-mask cap collapses ~unique UUID
     masks onto <=1024 digit-skewed survivors drawn uniformly; the
     Good-Turing tail bucket restores ~unique masks at the source's digit
     share.
  3. wave4-domain-weights.png  — a distinct-valued source domain appended
     to row evidence out-votes the sample and inverts the mask marginal;
     kept as support-only it leaves row mass intact.
  4. wave4-numeric-scrub.png   — dense-band inverse-CDF interpolation lands
     half its draws on rare real integers (COL_009); redraw+nudge clears
     them while multi-knot enum mass stays exact.

Palette matches the design-doc asset set (make_source_stats_figures.py):
BLUE = exact/full-table truth, ORANGE = the defective path, AQUA = the
wave-4 path. The OKLab separation check runs on every regeneration.
"""

from __future__ import annotations

import math
import random
import uuid
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sdfb_core.engines.text_shapes import (
    build_identifier_artifacts,
    identifier_sampler_from,
    mask_alphabets,
    positional_alphabets,
    sample_from_mask,
)

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "designs" / "assets"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045

# --- MEASURED (2026-08-20 R1 pair; the only place these numbers are typed).
# Source: integration_tests/2026-08-20_05_49_25-7855058315673855031 (A_TABLE)
#     and integration_tests/2026-08-20_06_28_11-7047697540022640037 (B_TABLE),
# oss-redacted bundle annexes: freetext_crosscheck_metrics.json `top_values`
# (exact APPROX_TOP_COUNT over the FULL tables) vs the same annex's sampled
# `top_shapes` panel, and e2e_gcp_metrics.json copy ratios.
RUN_A = "2026-08-20_05_49_25-7855…"  # A_TABLE R1 cold, 1M rows
RUN_B = "2026-08-20_06_28_11-7047…"  # B_TABLE R1 cold, 1M rows

# Dominant-value share of SUBSTANTIVE (non-empty) rows:
# (label, exact source, exact synthetic, storage-front panel estimate).
#   COL_054 BATCH   : 112,659/208,815  vs 528,806/988,800  vs shape AAAAA 0.751
#   COL_053 KW3000  : 112,658/210,882  vs 530,025/1,000,000 vs shape AA9999 0.746
#   COL_024 '35'    : 1,186,421/1,291,853 vs 614,777/668,140 vs shape 99 0.309
#   COL_015 DEVOL.T : 30,936/96,815    vs 15,499/45,903     vs alpha ~0.003
PANEL_VS_TRUTH = (
    ("COL_054 (A)\n'BATCH'", 0.5395, 0.5348, 0.7508),
    ("COL_053 (A)\n'KW3000'", 0.5342, 0.5300, 0.7460),
    ("COL_024 (B)\n'35'", 0.9184, 0.9201, 0.3092),
    ("COL_015 (B)\n'DEVOLUCION T'", 0.3196, 0.3376, 0.0030),
)

# INT64 substantive copy ratios (e2e_gcp_metrics.json, A_TABLE) + the
# memorization_flags CRITICAL threshold they are scored against.
NUMERIC_COPY = (
    ("COL_009", 0.5218),
    ("COL_047", 0.1092),
    ("COL_023", 0.0186),
    ("COL_020", 0.0100),
    ("COL_036", 0.0094),
    ("COL_007", 0.0048),
    ("COL_008", 0.0047),
)
MEM_CRITICAL = 0.3  # e2e_gcp_probe.py::_MEM_COPY_RATIO_THRESHOLD
COL9_SOURCE_DISTINCT = 34_622

# --- CONCEPT (seeded, deterministic; parameters chosen to illustrate) -----
SEED = 17
# Figure 2: enough UUIDs that distinct masks far exceed the 1024 cap, and
# enough draws that the per-mask plateau is visible.
N_UUIDS, N_MASK_DRAWS = 20_000, 20_000
MASK_CAP = 1024  # text_shapes.py::_MASK_TABLE_CAP
# Figure 3: a 90/10 two-family column with a large foreign-mask domain —
# the smallest structure that shows the D1 inversion.
N_DOM_ROWS, N_DOM_VALUES, N_DOM_DRAWS = 400, 5000, 20_000
# Figure 4: every even integer in a band is a real (rare) source value; one
# value repeats (multi-knot enum mass). Odd integers are the free lattice.
BAND_LO, BAND_N, ENUM_VALUE, ENUM_REPEATS = 1000, 250, 2000, 50
N_NUM_DRAWS = 20_000
NUDGE_MAX = 8  # engine.py::_NUMERIC_NUDGE_MAX


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


def _mask(v: str) -> str:
    return "".join(
        "9" if c.isdigit() else "A" if c.isupper() else "a" if c.islower() else c
        for c in v
    )


# --------------------------------------------------------------------------
def fig_panel_vs_truth():
    """Evidence: the flagged mass numbers were a sampling artifact."""
    fig, ax = plt.subplots(figsize=(13.5, 4.8), facecolor=SURFACE)
    labels = [c for c, *_ in PANEL_VS_TRUTH]
    src = [s * 100 for _, s, _, _ in PANEL_VS_TRUTH]
    syn = [s * 100 for _, _, s, _ in PANEL_VS_TRUTH]
    panel = [s * 100 for _, _, _, s in PANEL_VS_TRUTH]
    x = np.arange(len(labels))
    w = 0.26
    ax.bar(x - w, src, width=w, color=BLUE, label="source, exact full table")
    ax.bar(x, syn, width=w, color=AQUA, label="synthetic, exact full table")
    ax.bar(x + w, panel, width=w, color=ORANGE,
           label="crosscheck source panel (storage-front sample)")
    for xi, (a, b, c) in enumerate(zip(src, syn, panel, strict=True)):
        for dx, v in ((-w, a), (0, b), (w, c)):
            ax.text(xi + dx, v + 1.5, f"{v:.1f}", ha="center", color=INK,
                    fontsize=8)
    ax.set_xticks(x, labels, fontsize=9, color=MUTED)
    ax.set_ylabel("dominant-value share of substantive rows (%)",
                  color=MUTED, fontsize=9)
    ax.set_ylim(0, 100)
    _style(ax)
    _title(ax, "The engine matched the exact marginal; the panel did not",
           f"jobs {RUN_A} · {RUN_B} — exact source vs synthetic within 2 pp "
           "on every flagged column; the panel sampled storage-front only")
    ax.legend(frameon=False, fontsize=9, labelcolor=INK, ncol=3,
              loc="upper center", bbox_to_anchor=(0.5, -0.12))
    fig.tight_layout(pad=1.6, rect=(0, 0.06, 1, 1))
    fig.savefig(ASSETS / "wave4-panel-vs-truth.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def _old_capped_draw(values: list[str], rng: random.Random, n: int) -> list[str]:
    """The pre-wave-4 table route: lexicographic-tie survivors, kept-mass
    renormalization, NO tail. Reproduced here only to draw the 'before'
    panel; the live path is text_shapes.py::identifier_sampler_from."""
    counts = Counter(_mask(v) for v in values)
    kept = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MASK_CAP]
    alphabets = mask_alphabets(values)
    positional = positional_alphabets(values)
    total = sum(c for _, c in kept)
    out = []
    for _ in range(n):
        r = rng.randrange(total)
        mask = kept[-1][0]
        for m, c in kept:
            if r < c:
                mask = m
                break
            r -= c
        out.append(sample_from_mask(mask, alphabets, rng.randrange,
                                    positional=positional))
    return out


def fig_mask_entropy():
    """Concept: cap collapse vs Good-Turing tail, on seeded UUID v4s."""
    rng = random.Random(SEED)
    values = [str(uuid.UUID(int=rng.getrandbits(128), version=4))
              for _ in range(N_UUIDS)]
    # Equal-size reference set: N_MASK_DRAWS fresh v4 ids from the true
    # process, so all three bars count distinct masks over the same N.
    src_masks = [
        _mask(str(uuid.UUID(int=rng.getrandbits(128), version=4)))
        for _ in range(N_MASK_DRAWS)
    ]

    old = _old_capped_draw(values, random.Random(SEED + 1), N_MASK_DRAWS)
    artifacts = build_identifier_artifacts(tuple("x" * 36), None, values)
    draw = identifier_sampler_from(artifacts, random.Random(SEED + 2).randrange)
    new = [draw() for _ in range(N_MASK_DRAWS)]

    def digit_share(masks):
        joined = "".join(masks)
        return sum(1 for c in joined if c == "9") / len(joined)

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 4.8), facecolor=SURFACE)

    ax = axes[0]
    names = ["true v4 process", "capped table\n(pre-wave-4)",
             "tail bucket\n(wave 4)"]
    distinct = [len(set(src_masks)),
                len(set(_mask(v) for v in old)),
                len(set(_mask(v) for v in new))]
    colors = [BLUE, ORANGE, AQUA]
    x = np.arange(3)
    ax.bar(x, distinct, width=0.5, color=colors)
    for xi, v in enumerate(distinct):
        ax.text(xi, v + 260, f"{v:,}", ha="center", color=INK, fontsize=9)
    ax.set_xticks(x, names, fontsize=9, color=MUTED)
    ax.set_ylabel("distinct charclass masks", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "Mask entropy: collapse vs tail bucket",
           f"distinct masks per {N_MASK_DRAWS:,} draws · {N_UUIDS:,} seeded "
           f"RFC 4122 v4 ids · cap = {MASK_CAP}")

    ax = axes[1]
    shares = [digit_share(src_masks),
              digit_share([_mask(v) for v in old]),
              digit_share([_mask(v) for v in new])]
    ax.bar(x, [s * 100 for s in shares], width=0.5, color=colors)
    for xi, s in enumerate(shares):
        ax.text(xi, s * 100 + 0.8, f"{s * 100:.1f}", ha="center", color=INK,
                fontsize=9)
    ax.set_xticks(x, names, fontsize=9, color=MUTED)
    ax.set_ylabel("digit share of mask positions (%)", color=MUTED, fontsize=9)
    ax.set_ylim(0, 100)
    _style(ax)
    _title(ax, "Digit skew of the lexicographic tie-break",
           "ties sorted by mask string kept digit-front-loaded masks "
           "('-' < '9' < 'a') · wave 4: crc32")
    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "wave4-mask-entropy.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
def fig_domain_weights():
    """Concept: D1 — domain concatenated as evidence vs support-only."""
    rows = ["QX" + f"{i % 40:08d}" for i in range(int(N_DOM_ROWS * 0.9))] + [
        f"{i:04d}QRSTUV" for i in range(N_DOM_ROWS - int(N_DOM_ROWS * 0.9))
    ]
    dominant_mask = _mask(rows[0])
    domain = [f"J{i:07d}ZZ" for i in range(N_DOM_VALUES)]

    def share(drawn):
        return sum(1 for v in drawn if _mask(v) == dominant_mask) / len(drawn)

    # Pre-wave-4: domain concatenated onto the row multiset.
    concat = build_identifier_artifacts(tuple("x" * 10), None, rows + domain)
    draw_old = identifier_sampler_from(concat, random.Random(SEED + 3).randrange)
    # Wave 4: rows carry the weights, the domain is support-only.
    split = build_identifier_artifacts(tuple("x" * 10), None, rows,
                                       domain=frozenset(domain))
    draw_new = identifier_sampler_from(split, random.Random(SEED + 4).randrange)

    old_share = share([draw_old() for _ in range(N_DOM_DRAWS)])
    new_share = share([draw_new() for _ in range(N_DOM_DRAWS)])
    true_share = share(rows)

    fig, ax = plt.subplots(figsize=(8.6, 4.8), facecolor=SURFACE)
    x = np.arange(3)
    vals = [true_share * 100, old_share * 100, new_share * 100]
    ax.bar(x, vals, width=0.5, color=[BLUE, ORANGE, AQUA])
    for xi, v in enumerate(vals):
        ax.text(xi, v + 1.5, f"{v:.1f}", ha="center", color=INK, fontsize=9)
    ax.set_xticks(
        x,
        ["source rows", "domain as evidence\n(pre-wave-4)",
         "domain as support\n(wave 4)"],
        fontsize=9, color=MUTED,
    )
    ax.set_ylabel("dominant-mask share of draws (%)", color=MUTED, fontsize=9)
    ax.set_ylim(0, 100)
    _style(ax)
    _title(ax, "A distinct-valued domain must not out-vote the sample",
           f"{N_DOM_ROWS} rows (90/10 mask families) + {N_DOM_VALUES:,}-value "
           "foreign-mask domain · engine.py::_identifier_draw")
    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "wave4-domain-weights.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


def _band_concept():
    """Seeded dense-band inverse-CDF draws, split before/after the scrub.

    np.interp over the sorted sample is the same inverse transform
    _fidelity.py::_numeric_numpy uses; the nudge walk mirrors
    engine.py::_scrub_numeric_collisions."""
    band = [BAND_LO + 2 * i for i in range(BAND_N)]
    sample = band + [ENUM_VALUE] * ENUM_REPEATS
    domain = {str(v) for v in range(BAND_LO, BAND_LO + 4 * BAND_N, 2)} | {
        str(ENUM_VALUE)
    }
    multi_knot = {str(ENUM_VALUE)}
    obs = np.sort(np.asarray(sample, dtype="float64"))
    rng = np.random.default_rng(SEED)
    draws = np.rint(
        np.interp(rng.random(N_NUM_DRAWS), np.linspace(0, 1, obs.size), obs)
    ).astype(int)

    def split(vals_):
        rare = sum(
            1 for v in vals_ if str(v) in domain and str(v) not in multi_knot
        )
        enum = sum(1 for v in vals_ if str(v) in multi_knot)
        return rare, enum, len(vals_) - rare - enum

    def nudge(v: int) -> int:
        for step in range(1, NUDGE_MAX + 1):
            if str(v - step) not in domain:
                return v - step
            if str(v + step) not in domain:
                return v + step
        return v

    scrubbed = [
        nudge(v) if str(v) in domain and str(v) not in multi_knot else v
        for v in draws.tolist()
    ]
    return split(draws.tolist()), split(scrubbed)


# --------------------------------------------------------------------------
def fig_numeric_scrub():
    """Evidence (left): COL_009 is the one CRITICAL numeric column.
    Concept (right): dense-band interpolation vs the scrub."""
    fig, axes = plt.subplots(
        1, 2, figsize=(13.5, 4.8), facecolor=SURFACE,
        gridspec_kw={"width_ratios": [2, 3]},
    )

    ax = axes[0]
    cols = [c for c, _ in NUMERIC_COPY]
    vals = [v for _, v in NUMERIC_COPY]
    y = np.arange(len(cols))[::-1]
    colors = [ORANGE if v >= MEM_CRITICAL else MUTED for v in vals]
    ax.barh(y, vals, color=colors, height=0.6)
    ax.set_yticks(y, cols, fontsize=8.5, color=MUTED)
    ax.axvline(MEM_CRITICAL, color=INK, linewidth=1, linestyle=":")
    ax.text(MEM_CRITICAL + 0.01, 0.02, "CRITICAL 0.3", color=INK, fontsize=8,
            transform=ax.get_xaxis_transform())
    for yi, v in zip(y, vals, strict=True):
        ax.text(v + 0.01, yi, f"{v:.3f}", va="center", color=INK, fontsize=8)
    _style(ax, grid_axis="x")
    _title(ax, "A_TABLE INT64 substantive copy",
           f"job {RUN_A} · COL_009: {COL9_SOURCE_DISTINCT:,} "
           "source-distinct ids")
    ax.set_xlim(0, 0.62)

    before, after = _band_concept()

    ax = axes[1]
    cats = ["rare real value\n(privacy risk)", "multi-knot enum\n(kept by design)",
            "novel value"]
    x = np.arange(3)
    w = 0.36
    b = [100 * c / N_NUM_DRAWS for c in before]
    a = [100 * c / N_NUM_DRAWS for c in after]
    ax.bar(x - w / 2, b, width=w, color=ORANGE, label="inverse-CDF draw")
    ax.bar(x + w / 2, a, width=w, color=AQUA, label="after redraw + nudge")
    for xi, (u, v) in enumerate(zip(b, a, strict=True)):
        ax.text(xi - w / 2, u + 1.2, f"{u:.1f}", ha="center", color=INK, fontsize=8)
        ax.text(xi + w / 2, v + 1.2, f"{v:.1f}", ha="center", color=INK, fontsize=8)
    ax.set_xticks(x, cats, fontsize=9, color=MUTED)
    ax.set_ylabel("share of draws (%)", color=MUTED, fontsize=9)
    _style(ax)
    _title(ax, "Dense-band collisions and the scrub",
           f"seeded even-integer band ({BAND_N} singleton + one "
           f"x{ENUM_REPEATS} enum knot)")
    ax.legend(frameon=False, fontsize=9, labelcolor=INK)
    fig.tight_layout(pad=1.6)
    fig.savefig(ASSETS / "wave4-numeric-scrub.png", dpi=160, facecolor=SURFACE)
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
    fig_panel_vs_truth()
    fig_mask_entropy()
    fig_domain_weights()
    fig_numeric_scrub()
    for name in (
        "wave4-panel-vs-truth.png",
        "wave4-mask-entropy.png",
        "wave4-domain-weights.png",
        "wave4-numeric-scrub.png",
    ):
        print("wrote", ASSETS / name)


if __name__ == "__main__":
    main()
