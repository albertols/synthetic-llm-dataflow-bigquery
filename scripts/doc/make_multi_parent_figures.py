"""Regenerate the multi-parent candidate-cap concept figure (ADR 0037).

    uv run --no-sync python3 scripts/doc/make_multi_parent_figures.py

Writes docs/designs/assets/multi-parent-candidate-cap.png. ONE claim
(design `2026-09-11-multi-parent-children.md` §4):

  with the default cap, wrapping starts only where the fan-out per
  shared value exceeds 64 children AND the parent offers more than 64
  candidates for that value — the source's tail decides, and the cap is
  a flag.

Three panels, one per `--fk_candidate_cap` mode (16 / 64 / 256) over the
SAME seeded source, because a parameter table cannot show what a flag
value does (visual-first-documentation § Document contract).

Mechanism, in code: `_conditional_candidates`
(`sdfb_beam/pipeline.py`) keeps at most M candidate tuples per shared
value (`Top.SmallestPerKey` on a `blake2b(run_id, rest)` order), and
`sdfb_core.engines.fanout.conditional_values` hands child `i` the
`i % len`-th entry of that list — so a key wraps (reuses a candidate)
exactly when its fan-out `k` exceeds `min(c, M)`, where `c` is how many
distinct candidates the parent actually holds for the shared value.
Two disjoint causes, and the figure separates them:

  * `k > c` — **the source forces it**: the parent has fewer distinct
    branch values than the child has children. No cap can help; this
    share is identical in all three panels.
  * `c >= k > M` — **the cap adds it**: the parent had enough
    candidates and the Top-M combine threw the surplus away. This is
    the only share the flag moves.

CONCEPT figure: seeded, deterministic, no measured numbers (the
distributions below are chosen to show the mechanism, not to report a
run — a measured fan-out lives in `fk_fanout_measured`, ADR 0036).

Palette matches the design-doc asset set (`dataviz` reference palette):
BLUE = the source's own limit, ORANGE = what the flag costs, AQUA =
healthy. OKLab separation check runs on every regeneration.
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

# --- CONCEPT (seeded; parameters chosen to show the mechanism, not a run).
CONCEPT_SEED = 11
SHARED_VALUES = 6_000
# Children per shared value: a Zipf fan-out — most shared values carry a
# handful of children, a thin tail carries thousands. `a` is the Zipf
# exponent; the clip is a source-side ceiling, not a cap.
FANOUT_ZIPF_A = 1.65
FANOUT_CLIP = 3_000
# Distinct candidate tuples the conditional parent holds for the same
# shared value: heavier-tailed and typically wider than the fan-out (a
# diamond's other branch is a dimension, not a fact), so the parent
# usually HAS enough candidates and the cap is what removes them.
CANDIDATE_ZIPF_A = 1.40
CANDIDATE_SCALE = 5
CANDIDATE_CLIP = 50_000
# The three flag modes the panels compare (`--fk_candidate_cap`).
CAPS = (16, 64, 256)
DEFAULT_CAP = 64
JITTER_SIGMA = 0.06  # log-space, plotting only — never used to classify


def _draw():
  """The seeded source: one `(candidates, fan-out)` pair per shared
    value, plus the log-space jitter used for plotting only."""
  rng = np.random.default_rng(CONCEPT_SEED)
  fanout = np.minimum(rng.zipf(FANOUT_ZIPF_A, SHARED_VALUES), FANOUT_CLIP)
  candidates = np.minimum(
      CANDIDATE_SCALE * rng.zipf(CANDIDATE_ZIPF_A, SHARED_VALUES),
      CANDIDATE_CLIP,
  )
  jx = np.exp(rng.normal(0.0, JITTER_SIGMA, SHARED_VALUES))
  jy = np.exp(rng.normal(0.0, JITTER_SIGMA, SHARED_VALUES))
  return candidates, fanout, jx, jy


def _style(ax):
  ax.set_facecolor(SURFACE)
  for side in ("top", "right"):
    ax.spines[side].set_visible(False)
  for side in ("left", "bottom"):
    ax.spines[side].set_color(GRID)
  ax.tick_params(colors=MUTED, labelsize=9, length=0)
  ax.grid(color=GRID, linewidth=0.8, alpha=0.9)
  ax.set_axisbelow(True)


def _title(ax, text, sub=None):
  ax.set_title(
      text, color=INK, fontsize=12.5, fontweight="600", loc="left", pad=30)
  if sub:
    ax.text(
        0,
        1.03,
        sub,
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9,
        va="bottom")


def panel_cap(ax, cap, candidates, fanout, jx, jy, *, first):
  """One `--fk_candidate_cap` mode: where a shared value wraps, and
    which of the two causes put it there."""
  source_forced = fanout > candidates
  cap_added = (~source_forced) & (fanout > cap) & (candidates > cap)
  healthy = ~(source_forced | cap_added)

  lo_x, hi_x = 3.0, 1.2 * CANDIDATE_CLIP
  lo_y, hi_y = 0.7, 1.6 * FANOUT_CLIP

  # The wrap frontier: a key wraps when k > min(c, cap). Below the
  # diagonal AND below the cap line is the only safe quadrant. The two
  # shaded regions are disjoint on purpose — blue is what no flag value
  # can fix, orange is exactly what THIS flag value costs.
  diag = np.array([lo_x, hi_x])
  ax.fill_between(diag, diag, hi_y, color=BLUE, alpha=0.09, zorder=0)
  wedge = np.array([cap, hi_x])
  ax.fill_between(wedge, cap, wedge, color=ORANGE, alpha=0.13, zorder=0)
  xs = np.array([lo_x, cap, cap, hi_x])
  frontier = np.array([lo_x, cap, cap, cap])
  ax.plot(
      xs,
      frontier,
      color=INK,
      linewidth=1.6,
      zorder=4,
      label="wrap frontier  k = min(c, cap)")
  ax.plot(
      diag,
      diag,
      color=BLUE,
      linewidth=1.1,
      linestyle=":",
      zorder=4,
      label="the parent's own limit  k = c")
  ax.axvline(cap, color=MUTED, linewidth=1.0, linestyle="--", zorder=1)

  for mask, color, label in (
      (healthy, AQUA, "no wrap"),
      (source_forced, BLUE, "source forces the wrap (k > c)"),
      (cap_added, ORANGE, "the cap adds the wrap (c >= k > cap)"),
  ):
    ax.scatter(
        candidates[mask] * jx[mask],
        fanout[mask] * jy[mask],
        s=6,
        color=color,
        alpha=0.45,
        linewidths=0,
        zorder=3,
        label=label)

  ax.set_xscale("log")
  ax.set_yscale("log")
  ax.set_xlim(lo_x, hi_x)
  ax.set_ylim(lo_y, hi_y)
  ax.text(
      cap * 1.35,
      cap * 0.42,
      f"cap = {cap}",
      color=MUTED,
      fontsize=8.8,
      zorder=5,
      bbox={
          "facecolor": SURFACE,
          "edgecolor": "none",
          "pad": 1.6
      })
  ax.text(
      0.035,
      0.965,
      f"wraps: {(source_forced | cap_added).mean():.1%} of shared values\n"
      f"  source forces {source_forced.mean():.1%}  (same in every panel)\n"
      f"  the cap adds {cap_added.mean():.2%}",
      transform=ax.transAxes,
      va="top",
      ha="left",
      color=INK,
      fontsize=9,
      bbox={
          "facecolor": SURFACE,
          "edgecolor": GRID,
          "boxstyle": "round,pad=0.4"
      },
      zorder=5,
  )
  ax.set_xlabel(
      "candidates the parent holds for the shared value (c)",
      color=MUTED,
      fontsize=9.5)
  if first:
    ax.set_ylabel(
        "children per shared value (fan-out k)", color=MUTED, fontsize=9.5)
  _style(ax)
  _title(
      ax,
      f"--fk_candidate_cap = {cap}" +
      ("   (default)" if cap == DEFAULT_CAP else ""),
      "orange wedge = wrapping this flag value causes",
  )


def fig_candidate_cap():
  candidates, fanout, jx, jy = _draw()
  fig, axes = plt.subplots(
      1, len(CAPS), figsize=(16.2, 5.8), facecolor=SURFACE, sharey=True)
  for i, (ax, cap) in enumerate(zip(axes, CAPS, strict=True)):
    panel_cap(ax, cap, candidates, fanout, jx, jy, first=(i == 0))
  handles, labels = axes[0].get_legend_handles_labels()
  fig.legend(
      handles,
      labels,
      loc="lower center",
      ncol=5,
      frameon=False,
      fontsize=9.5,
      labelcolor=INK,
      bbox_to_anchor=(0.5, -0.015),
      markerscale=2.6)
  fig.suptitle(
      "The candidate cap only bites where the fan-out AND the parent "
      "both exceed it",
      color=INK,
      fontsize=14,
      fontweight="600",
      x=0.006,
      ha="left",
      y=0.995,
  )
  fig.tight_layout(w_pad=2.2, rect=(0, 0.055, 1, 0.965))
  fig.savefig(
      ASSETS / "multi-parent-candidate-cap.png",
      dpi=180,
      facecolor=SURFACE,
      bbox_inches="tight")
  plt.close(fig)


# --------------------------------------------------------------------------
def _srgb_to_oklab(hexstr):
  r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))

  def lin(u):
    return u / 12.92 if u <= SRGB_CUTOFF else ((u + 0.055) / 1.055)**2.4

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
      de = 100 * math.dist(
          _srgb_to_oklab(names[ks[i]]), _srgb_to_oklab(names[ks[j]]))
      flag = "ok" if de >= CVD_FLOOR else "FAIL"
      ok = ok and de >= CVD_FLOOR
      print(f"  {ks[i]:>6} vs {ks[j]:<6} dE = {de:5.1f}  {flag}")
  if not ok:
    raise SystemExit("palette separation below floor")


def _report_shares():
  """Print the shares the panels annotate — the figure's numbers, so a
    reader of the log can check them without opening the PNG."""
  candidates, fanout, _, _ = _draw()
  source_forced = fanout > candidates
  print(f"shared values: {SHARED_VALUES:,} (seed {CONCEPT_SEED})")
  print(f"  source-forced wrap (k > c): {source_forced.mean():.2%}")
  for cap in CAPS:
    added = (~source_forced) & (fanout > cap) & (candidates > cap)
    total = source_forced | added
    print(f"  cap={cap:>3}: wrap {total.mean():6.2%}   "
          f"cap-added {added.mean():6.2%}")


def main() -> None:
  ASSETS.mkdir(parents=True, exist_ok=True)
  _check_palette()
  _report_shares()
  fig_candidate_cap()
  print("wrote", ASSETS / "multi-parent-candidate-cap.png")


if __name__ == "__main__":
  main()
