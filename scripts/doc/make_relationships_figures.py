"""Regenerate the relationship-model figures (ADR 0032).

    uv run --no-sync python3 scripts/doc/make_relationships_figures.py

Writes PNGs into docs/designs/assets/. Both are CONCEPT figures — they
teach what the two inputs DO, and carry no measured numbers:

  1. relationships-scenarios.png — one panel per launch scenario: what
     `--landing_table` + `--generate_fk_relationships` actually generate.
  2. relationships-flags.png — one panel per flag mode: `enabled: false`
     detaches a table AND everything that reached the model only through
     it; `enforced: false` keeps the edge visible but draws no keys.

The graph in every panel is the SAME four-table model, so the reader
compares outcomes, not drawings. Layout is fixed (no solver, no
randomness): re-running reproduces the figures exactly.

Palette matches the design-doc asset set: BLUE = generated this launch,
AQUA = enforced relationship, ORANGE = disabled/detached, grey = present
in the model but not in this launch. OKLab separation check runs on every
regeneration.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "designs" / "assets"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#1c2530", "#5b6672", "#dfe4ea", "#ffffff"
CVD_FLOOR = 15.0
SRGB_CUTOFF = 0.04045

# --- CONCEPT model: A <- B <- C (a chain) plus Z, unrelated. Positions are
# hand-placed so every panel draws the identical shape.
NODES = {"A_TABLE": (0.45, 0.80), "B_TABLE": (0.45, 0.50),
         "C_TABLE": (0.45, 0.20), "Z_TABLE": (0.95, 0.80)}
EDGES = (("B_TABLE", "A_TABLE"), ("C_TABLE", "B_TABLE"))
NODE_W, NODE_H = 0.38, 0.13


def _draw_panel(ax, title, subtitle, *, generated, disabled=(),
                documented=(), detached=()):
    ax.set_xlim(0.18, 1.20)
    ax.set_ylim(0.02, 1.0)
    ax.axis("off")
    ax.set_title(title, color=INK, fontsize=11, fontweight="600", loc="left",
                 pad=22)
    ax.text(0.18, 1.0, subtitle, transform=ax.transData, color=MUTED,
            fontsize=8.5, va="bottom")

    for child, parent in EDGES:
        x0, y0 = NODES[child]
        x1, y1 = NODES[parent]
        pair = (child, parent)
        # An edge draws keys only when BOTH ends generate in this launch.
        if pair in documented:
            style, colour, label = (0, (2, 2)), MUTED, "documented"
        elif child in generated and parent in generated:
            style, colour, label = "solid", AQUA, "keys drawn"
        else:
            style, colour, label = (0, (1, 3)), ORANGE, "not drawn"
        ax.annotate(
            "", xy=(x1, y1 - NODE_H / 2), xytext=(x0, y0 + NODE_H / 2),
            arrowprops={"arrowstyle": "-|>", "color": colour, "linewidth": 2.0,
                        "linestyle": style, "shrinkA": 2, "shrinkB": 2},
        )
        ax.text((x0 + x1) / 2 + 0.03, (y0 + y1) / 2, label, color=colour,
                fontsize=7.5, va="center")

    for name, (x, y) in NODES.items():
        if name in disabled:
            face, edge, text = SURFACE, ORANGE, ORANGE
            tag = "disabled"
        elif name in generated:
            face, edge, text = BLUE, BLUE, "#ffffff"
            tag = "generated"
        else:
            face, edge, text = SURFACE, GRID, MUTED
            tag = "detached" if name in detached else "not in this launch"
        ax.add_patch(plt.Rectangle(
            (x - NODE_W / 2, y - NODE_H / 2), NODE_W, NODE_H,
            facecolor=face, edgecolor=edge, linewidth=1.8,
            joinstyle="round", zorder=3,
        ))
        ax.text(x, y + 0.018, name, ha="center", va="center", color=text,
                fontsize=9, fontweight="600", zorder=4)
        ax.text(x, y - 0.038, tag, ha="center", va="center",
                color=text if name in generated else MUTED, fontsize=7,
                zorder=4)


def fig_scenarios():
    fig, axes = plt.subplots(1, 4, figsize=(17.0, 4.6), facecolor=SURFACE)
    _draw_panel(
        axes[0], "1 · one table, flag false",
        "--landing_table=B_TABLE\n--generate_fk_relationships=false",
        generated={"B_TABLE"}, detached={"A_TABLE", "C_TABLE"},
    )
    _draw_panel(
        axes[1], "2 · one table, flag true (default)",
        "--landing_table=B_TABLE\nthe whole enabled component, parents first",
        generated={"A_TABLE", "B_TABLE", "C_TABLE"},
    )
    _draw_panel(
        axes[2], "3 · many tables, flag false",
        "--landing_table=B_TABLE,Z_TABLE\nindependent concurrent generation",
        generated={"B_TABLE", "Z_TABLE"}, detached={"A_TABLE", "C_TABLE"},
    )
    _draw_panel(
        axes[3], "3b · many tables, flag true",
        "--landing_table=B_TABLE,Z_TABLE\nunion of components — rare, and it says so",
        generated={"A_TABLE", "B_TABLE", "C_TABLE", "Z_TABLE"},
    )
    fig.tight_layout(pad=1.4)
    fig.savefig(ASSETS / "relationships-scenarios.png", dpi=160,
                facecolor=SURFACE)
    plt.close(fig)


def fig_flags():
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.6), facecolor=SURFACE)
    _draw_panel(
        axes[0], "baseline",
        "launch on A_TABLE: the whole chain,\nevery edge drawing keys",
        generated={"A_TABLE", "B_TABLE", "C_TABLE"},
    )
    _draw_panel(
        axes[1], "B_TABLE: enabled: false",
        "B detaches — and C, which reached A\nONLY through B, detaches with it",
        generated={"A_TABLE"}, disabled={"B_TABLE"}, detached={"C_TABLE"},
    )
    _draw_panel(
        axes[2], "B→A edge: enforced: false",
        "the relationship stays visible and\ndocumented; no keys are drawn from it",
        generated={"A_TABLE", "B_TABLE", "C_TABLE"},
        documented={("B_TABLE", "A_TABLE")},
    )
    fig.tight_layout(pad=1.4)
    fig.savefig(ASSETS / "relationships-flags.png", dpi=160, facecolor=SURFACE)
    plt.close(fig)


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
    fig_scenarios()
    fig_flags()
    for name in ("relationships-scenarios.png", "relationships-flags.png"):
        print("wrote", ASSETS / name)


if __name__ == "__main__":
    main()
