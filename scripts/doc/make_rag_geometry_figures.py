#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""Regenerate the B.1 retrieval-geometry figures (article 4).

    uv run --no-sync python3 scripts/doc/make_rag_geometry_figures.py

Writes into docs/designs/assets/:

  rag-dense-vectors-3d.png        concept  — chunks as unit vectors; centroid
                                             top-8 vs k-center on the sphere
  rag-retrieval-sphere.gif        concept  — the same sphere, animated
  rag-seed-pickers.png            concept  — six ways to pick eight seeds
  rag-seed-budget.png             concept  — modes reached and prompt bytes
                                             as k grows
  rag-fidelity-originality.png    concept  — scripted candidates on the
                                             nearest-source-similarity axis,
                                             coloured by the REAL gates
  rag-great-serialization.png     concept  — a table row as a GReaT sentence,
                                             clause by clause (real
                                             `serialize_row` output)
  rag-names-pes-map.png           concept  — a column of names in embedding
                                             space: seeds per strategy, where
                                             scripted candidates land, and
                                             what a vector looks like
  rag-setup-cost.png              evidence — where one worker's setup went
  prefix-vs-kcenter-coverage.png  concept  — which 1,024 rows get indexed
                                             (owned by the 2026-07-25 design;
                                             regenerated here since 2026-09-21,
                                             it had no committed generator)

Every SELECTION drawn here is computed by the pipeline's own functions
(`sdfb_core.rag.retrieval.select_seed_examples` / `retrieve_kcenter_k`,
`sdfb_core.rag.index.build_index`) on the plotted vectors — the toy data is
synthetic, the algorithms are not re-implemented. The three contrast
pickers the pipeline does NOT ship (random, query top-k, MMR) are
implemented below and labelled as such in the figure.

MEASURED numbers are not typed here: the one evidence figure imports the
WS5 `MEASURED` block (`make_ws5_figures.py`, run
2026-07-26_06_54_25) so each stays typed once.
"""

# pyplot must be imported after matplotlib.use("Agg") selects the headless backend.
# pylint: disable=wrong-import-position

# f-string fields keep single quotes while Python 3.11 is supported;
# pylint on Python >= 3.12 reads those quotes as inconsistent.
# pylint: disable=inconsistent-quotes

from __future__ import annotations

import io
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

import b1_rag_walkthrough as walkthrough
import make_ws5_figures as ws5
# The article quotes what these private helpers do, so the figures run them.
# pylint: disable=protected-access
from sdfb_core.engines.b1_rag import engine as b1
from sdfb_core.engines.b1_rag.profile import profile_columns
from sdfb_core.rag.chunking import MAX_ROW_DOC_ROWS
from sdfb_core.rag.embedding import HashingEmbedder
from sdfb_core.rag.index import build_index
from sdfb_core.rag.retrieval import (
    centroid,
    retrieve_kcenter_k,
    select_seed_examples,
)
from sdfb_core.rag.serialize import serialize_row

ASSETS = ws5.ASSETS
BLUE, ORANGE, AQUA = ws5.BLUE, ws5.ORANGE, ws5.AQUA
INK, MUTED, GRID, SURFACE = ws5.INK, ws5.MUTED, ws5.GRID, ws5.SURFACE
DOT = "#c3cad3"  # recessive data points (darker than GRID: they are data)
# The drawio/mermaid house classes lend two more hues when five columns of a
# table each need their own (identity is also carried by the printed name).
PURPLE, SLATE = "#7a3fd1", "#6b7280"
K = b1._DEFAULT_TOP_K  # 8 — the engine's own constant
# What article 4 prints for (seeds per column, indexed rows): a change to
# either constant must fail here, so the article is re-synced on purpose.
ARTICLE_CONSTANTS = (8, 1024)
# GIF storyboard: frames [0, 12) show the chunks, [12, 32) the centroid
# query, the rest reveal one k-center pick every 4 frames.
GIF_FRAMES, GIF_CENTROID_AT, GIF_KCENTER_AT, GIF_FRAMES_PER_PICK = 72, 12, 32, 4

# --- CONCEPT (seeded; no measured number lives here) -----------------------
# A free-text column's value manifold, as unit vectors. Five modes of very
# unequal mass — the realistic case (Part 2's measured columns: one dominant
# format, several rare ones). Three dimensions instead of 384 so the sphere
# can be drawn; every function below is dimension-agnostic.
SEED = 4
#              direction (x, y, z)     points  spread
MODES = (
    ((0.10, 0.05, 1.00), 300, 0.20),  # dominant format
    ((0.95, 0.10, 0.45), 55, 0.13),
    ((-0.60, 0.75, 0.40), 35, 0.12),
    ((-0.45, -0.85, 0.35), 20, 0.10),
    ((0.55, -0.80, 0.10), 10, 0.09),  # the 2 % mode seeds tend to miss
)
# The classic-RAG contrast panel needs a query; this one sits in mode 2.
QUERY_DIRECTION = (0.93, 0.18, 0.40)
MMR_LAMBDA = 0.5  # Carbonell & Goldstein's relevance/diversity mix
# prefix-vs-kcenter: 2-D illustration at a 3 % budget (the design doc's own).
PREFIX_MODES = (((-1.6, -0.9), 2300, 0.42), ((0.9, -0.2), 1500, 0.36),
                ((-0.7, 1.4), 1150, 0.30), ((2.0, -2.1), 48, 0.13))
PREFIX_BUDGET = 0.03


def _unit(v: np.ndarray) -> np.ndarray:
  return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _manifold() -> tuple[np.ndarray, np.ndarray]:
  rng = np.random.default_rng(SEED)
  pts, lab = [], []
  for i, (direction, n, spread) in enumerate(MODES):
    center = _unit(np.array(direction))
    pts.append(_unit(center + rng.normal(0.0, spread, size=(n, 3))))
    lab += [i] * n
  pts_arr, lab_arr = np.vstack(pts), np.array(lab)
  # Shuffle: the reference sample is fingerprint-ordered, i.e. mode-agnostic.
  order = rng.permutation(len(pts_arr))
  return pts_arr[order], lab_arr[order]


def _pick(pts: np.ndarray, strategy: str, k: int = K, attempt: int = 0):
  """Indices chosen by the PIPELINE's picker for `strategy`."""
  vectors = pts.tolist()
  texts = [str(i) for i in range(len(vectors))]
  chosen = select_seed_examples(
      vectors, texts, k, strategy=strategy, attempt=attempt)
  return np.array([int(t) for t in chosen])


def _query_topk(pts: np.ndarray, query: np.ndarray, k: int = K) -> np.ndarray:
  """Classic RAG: the k nearest neighbours of a QUERY (same exact index)."""
  index = build_index(pts.tolist(), pts.shape[1])
  try:
    return np.array(index.search(query.tolist(), k))
  finally:
    index.release()


def _mmr(pts: np.ndarray, query: np.ndarray, k: int = K) -> np.ndarray:
  """Maximal Marginal Relevance (contrast only — not in the pipeline)."""
  rel = pts @ _unit(query)
  chosen = [int(np.argmax(rel))]
  while len(chosen) < k:
    redundancy = (pts @ pts[chosen].T).max(axis=1)
    score = MMR_LAMBDA * rel - (1 - MMR_LAMBDA) * redundancy
    score[chosen] = -np.inf
    chosen.append(int(np.argmax(score)))
  return np.array(chosen)


def _flatten(pts: np.ndarray) -> np.ndarray:
  """Lambert azimuthal equal-area projection about +z: the sphere, flat."""
  scale = np.sqrt(2.0 / (1.0 + np.clip(pts[:, 2], -0.999, 1.0)))
  return np.column_stack([pts[:, 0] * scale, pts[:, 1] * scale])


# --------------------------------------------------------------------------
def _sphere(ax, elev: float, azim: float) -> None:
  u, v = np.mgrid[0:2 * np.pi:40j, 0:np.pi:20j]
  ax.plot_wireframe(
      np.cos(u) * np.sin(v),
      np.sin(u) * np.sin(v),
      np.cos(v),
      color=GRID,
      linewidth=0.4,
      alpha=0.7)
  # Depth-sorting would bury the eight picks under the dense mode's dots.
  ax.computed_zorder = False
  ax.set_box_aspect((1, 1, 1), zoom=1.32)
  ax.set_xlim(-1, 1)
  ax.set_ylim(-1, 1)
  ax.set_zlim(-1, 1)
  ax.view_init(elev=elev, azim=azim)
  ax.set_axis_off()
  ax.set_facecolor(SURFACE)


def _draw_sphere_panel(ax,
                       pts,
                       picks,
                       *,
                       color,
                       show_centroid,
                       numbered,
                       elev=34.0,
                       azim=-58.0) -> None:
  _sphere(ax, elev, azim)
  ax.scatter(
      pts[:, 0],
      pts[:, 1],
      pts[:, 2],
      s=9,
      color=DOT,
      depthshade=False,
      linewidths=0,
      alpha=0.9,
      zorder=2)
  if show_centroid:
    c = np.array(centroid(pts.tolist()))
    ax.quiver(
        0,
        0,
        0,
        *c,
        color=INK,
        linewidth=1.8,
        arrow_length_ratio=0.12,
        zorder=3)
    ax.text(
        *(c * 0.5 + np.array([0.14, 0, 0])),
        f"c  (‖c‖ = {np.linalg.norm(c):.2f} — inside the sphere)",
        color=INK,
        fontsize=9,
        zorder=5)
  if len(picks):
    p = pts[picks]
    ax.scatter(
        p[:, 0],
        p[:, 1],
        p[:, 2],
        s=80,
        color=color,
        edgecolors=SURFACE,
        linewidths=1.4,
        depthshade=False,
        zorder=4)
    if numbered:
      for n, q in enumerate(p, start=1):
        ax.text(
            *(q * 1.14),
            str(n),
            color=INK,
            fontsize=9.5,
            ha="center",
            fontweight="600",
            zorder=5)


def fig_dense_vectors_3d(pts, lab) -> None:
  cen, kc = _pick(pts, "centroid"), _pick(pts, "kcenter")
  fig = plt.figure(figsize=(13, 6.4), facecolor=SURFACE)
  specs = (
      (cen, ORANGE, True, False, "--pool_seed_strategy=centroid  (default)",
       f"one query — the mean vector c — and its {K} nearest: "
       f"{len(set(lab[cen]))} of {len(MODES)} modes shown to the LLM"),
      (kc, BLUE, False, True, "--pool_seed_strategy=kcenter",
       f"pick 1 is the medoid; each next pick is the farthest from all "
       f"picked: {len(set(lab[kc]))} of {len(MODES)} modes"),
  )
  for i, (picks, color, show_c, numbered, name, sub) in enumerate(specs):
    ax = fig.add_subplot(1, 2, i + 1, projection="3d")
    _draw_sphere_panel(
        ax, pts, picks, color=color, show_centroid=show_c, numbered=numbered)
    ax.text2D(
        0.02,
        1.02,
        name,
        transform=ax.transAxes,
        color=INK,
        fontsize=11,
        fontweight="600",
        family="monospace")
    ax.text2D(
        0.02, 0.98, sub, transform=ax.transAxes, color=MUTED, fontsize=9.5)
  fig.suptitle(
      "Every chunk is a point on a unit sphere (3 of 384 dimensions drawn); "
      "retrieval only decides WHICH eight the prompt shows",
      color=INK,
      fontsize=12.5,
      fontweight="600",
      x=0.02,
      ha="left",
      y=0.985)
  fig.subplots_adjust(left=0, right=1, bottom=-0.04, top=0.85, wspace=0)
  fig.savefig(ASSETS / "rag-dense-vectors-3d.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


def fig_retrieval_gif(pts, lab) -> None:
  """72 frames: chunks → centroid top-8 → k-center picks, one at a time."""
  cen, kc = _pick(pts, "centroid"), _pick(pts, "kcenter")
  frames: list[Image.Image] = []
  n_frames = GIF_FRAMES
  for f in range(n_frames):
    fig = plt.figure(figsize=(5.6, 5.9), facecolor=SURFACE)
    ax = fig.add_subplot(1, 1, 1, projection="3d")
    azim = -70.0 + 150.0 * f / (n_frames - 1)
    if f < GIF_CENTROID_AT:
      picks, color, show_c, numbered = np.array([],
                                                dtype=int), DOT, False, False
      caption = ("1. embed: every chunk becomes a unit vector\n"
                 "(3 of 384 dimensions drawn)")
    elif f < GIF_KCENTER_AT:
      picks, color, show_c, numbered = cen, ORANGE, True, False
      caption = (f"2. centroid: query with the mean vector c, keep the {K} "
                 f"nearest\n→ {len(set(lab[cen]))} of {len(MODES)} modes "
                 "reach the prompt")
    else:
      shown = min(K, 1 + (f - GIF_KCENTER_AT) // GIF_FRAMES_PER_PICK)
      picks, color, show_c, numbered = kc[:shown], BLUE, False, True
      caption = (f"3. kcenter: pick {shown} of {K} — each is the farthest "
                 f"from all picked\n→ {len(set(lab[kc[:shown]]))} of "
                 f"{len(MODES)} modes so far")
    _draw_sphere_panel(
        ax,
        pts,
        picks,
        color=color,
        show_centroid=show_c,
        numbered=numbered,
        azim=azim)
    fig.text(0.5, 0.045, caption, ha="center", color=INK, fontsize=10)
    fig.subplots_adjust(left=0, right=1, bottom=0.08, top=1)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=90, facecolor=SURFACE, format="png")
    plt.close(fig)
    buf.seek(0)
    frames.append(Image.open(buf).convert("RGB"))
  # ONE palette for every frame, taken from a strip with all three phases in
  # it: a per-frame median cut spends its colours on the grey dots and
  # washes the eight orange/blue picks out.
  strip = Image.new("RGB", (frames[0].width * 3, frames[0].height))
  for i, f in enumerate((5, 20, n_frames - 1)):
    strip.paste(frames[f], (frames[0].width * i, 0))
  palette = strip.quantize(colors=96, method=Image.Quantize.FASTOCTREE)
  frames = [
      frame.quantize(palette=palette, dither=Image.Dither.NONE)
      for frame in frames
  ]
  durations = [90] * n_frames
  durations[-1] = 2500  # hold the finished k-center cover
  frames[0].save(
      ASSETS / "rag-retrieval-sphere.gif",
      save_all=True,
      append_images=frames[1:],
      duration=durations,
      loop=0,
      optimize=True)


# --------------------------------------------------------------------------
def fig_seed_pickers(pts, lab) -> None:
  rng = np.random.default_rng(SEED)
  flat = _flatten(pts)
  query = _unit(np.array(QUERY_DIRECTION))
  qflat = _flatten(query[None, :])[0]
  rotate = [_pick(pts, "kcenter_rotate", attempt=a) for a in range(3)]
  panels = (
      ("random", "contrast — not in the pipeline", BLUE,
       rng.choice(len(pts), size=K, replace=False), None),
      ("query top-k  (classic RAG)", "contrast — needs a query; b1_rag has "
       "none", BLUE, _query_topk(pts, query), qflat),
      (f"MMR  (λ = {MMR_LAMBDA})", "contrast — relevance to c, minus "
       "redundancy", BLUE, _mmr(pts, np.array(centroid(pts.tolist()))), None),
      ("--pool_seed_strategy=centroid", "default — typicality", ORANGE,
       _pick(pts, "centroid"), None),
      ("--pool_seed_strategy=kcenter", "coverage, prompt bytes fixed", ORANGE,
       _pick(pts, "kcenter"), None),
      ("--pool_seed_strategy=kcenter_rotate",
       "coverage, re-seeded per ladder attempt", ORANGE, None, None),
  )
  fig, axes = plt.subplots(2, 3, figsize=(14.5, 9.6), facecolor=SURFACE)
  for ax, (name, sub, color, picks, star) in zip(
      axes.ravel(), panels, strict=True):
    ax.scatter(flat[:, 0], flat[:, 1], s=11, color=DOT, linewidths=0, zorder=1)
    if picks is None:
      marks = (("o", "attempt 0"), ("s", "attempt 1"), ("^", "attempt 2"))
      for idx, (marker, label) in zip(rotate, marks, strict=True):
        ax.scatter(
            flat[idx, 0],
            flat[idx, 1],
            s=85,
            marker=marker,
            facecolors=color if marker == "o" else SURFACE,
            edgecolors=color,
            linewidths=1.8,
            zorder=3,
            label=label)
      reached = len({m for idx in rotate for m in lab[idx]})
      note = f"modes reached over 3 attempts: {reached} of {len(MODES)}"
      ax.legend(
          frameon=True,
          facecolor=SURFACE,
          edgecolor=GRID,
          framealpha=1.0,
          fontsize=8.5,
          loc="lower left",
          labelcolor=INK)
    else:
      ax.scatter(
          flat[picks, 0],
          flat[picks, 1],
          s=95,
          color=color,
          edgecolors=SURFACE,
          linewidths=1.6,
          zorder=3)
      note = (f"modes reached by the {K} seeds: "
              f"{len(set(lab[picks]))} of {len(MODES)}")
    if star is not None:
      ax.scatter(*star, s=190, marker="*", color=INK, zorder=4)
      ax.annotate(
          "query",
          star,
          xytext=(9, 7),
          textcoords="offset points",
          color=INK,
          fontsize=9)
    ax.set_title(
        name,
        color=INK,
        fontsize=10.5,
        fontweight="600",
        loc="left",
        pad=24,
        family="monospace")
    ax.text(
        0,
        1.025,
        sub,
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9,
        va="bottom")
    ax.text(
        0.5,
        -0.075,
        note,
        transform=ax.transAxes,
        ha="center",
        color=INK,
        fontsize=9.5,
        fontweight="600")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal")
    ax.set_facecolor(SURFACE)
    for side in ax.spines.values():
      side.set_color(GRID)
  fig.suptitle(
      "Six ways to pick eight seeds from one column's value manifold "
      "(the sphere, flattened) — blue = contrast, orange = what the "
      "pipeline ships",
      color=INK,
      fontsize=12.5,
      fontweight="600",
      x=0.012,
      ha="left",
      y=0.992)
  fig.tight_layout(rect=(0, 0.01, 1, 0.955), h_pad=2.6)
  fig.savefig(ASSETS / "rag-seed-pickers.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
def fig_seed_budget(pts, lab) -> None:
  ks = list(range(1, 25))
  reached = {
      s: [len(set(lab[_pick(pts, s, k=k)])) for k in ks]
      for s in ("centroid", "kcenter")
  }
  # Prompt bytes from the REAL prompt builder over the walkthrough's values.
  values = list(dict.fromkeys(walkthrough._MERCHANTS))
  prompt_bytes = [
      len(
          b1._build_pool_prompt("merchant_name", b1._POOL_VALUES_PER_CALL,
                                values[:k]).encode()) for k in ks
  ]
  fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.9), facecolor=SURFACE)
  for ax in (ax1, ax2):
    ws5._style(ax)
    ax.axvline(K, color=MUTED, linewidth=1.0, linestyle=(0, (4, 3)))
  ax1.plot(
      ks,
      reached["centroid"],
      color=ORANGE,
      linewidth=2,
      marker="o",
      markersize=4.5,
      label="centroid")
  ax1.plot(
      ks,
      reached["kcenter"],
      color=BLUE,
      linewidth=2,
      marker="s",
      markersize=4.5,
      label="kcenter")
  ax1.set_ylim(0, len(MODES) + 0.6)
  ax1.set_yticks(range(len(MODES) + 1))
  ax1.set_xlabel("k — seeds shown in the prompt", color=MUTED, fontsize=9.5)
  ax1.set_ylabel(f"modes reached (of {len(MODES)})", color=MUTED, fontsize=9.5)
  ax1.text(
      ks[-1],
      reached["kcenter"][-1] + 0.18,
      "kcenter",
      color=INK,
      fontsize=9.5,
      ha="right")
  ax1.text(
      ks[-1],
      reached["centroid"][-1] + 0.18,
      "centroid",
      color=INK,
      fontsize=9.5,
      ha="right")
  ax1.text(K + 0.4, 0.25, f"k = {K}\n(_DEFAULT_TOP_K)", color=MUTED, fontsize=9)
  ws5._title(ax1, "Coverage saturates early when seeds are spread",
             "toy column, 5 modes — picks by select_seed_examples()")
  ax2.plot(ks, prompt_bytes, color=INK, linewidth=2, marker="o", markersize=4.5)
  ax2.set_xlabel("k — seeds shown in the prompt", color=MUTED, fontsize=9.5)
  ax2.set_ylabel("prompt bytes (one column)", color=MUTED, fontsize=9.5)
  ax2.set_ylim(0, max(prompt_bytes) * 1.15)
  ax2.text(
      K + 0.4,
      prompt_bytes[K - 1] * 0.55,
      f"{prompt_bytes[K - 1]} B at k = {K}",
      color=MUTED,
      fontsize=9)
  ws5._title(
      ax2, "Every extra seed is prefix bytes and one more real value shown",
      "bytes from _build_pool_prompt() over the walkthrough's toy values")
  fig.tight_layout()
  fig.savefig(ASSETS / "rag-seed-budget.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
def fig_fidelity_originality() -> None:
  """Scripted candidates vs their nearest source value, by REAL gate."""
  prof = profile_columns(walkthrough._schema(),
                         walkthrough.ROWS)["merchant_name"]
  in_format = b1._format_gate(prof)
  source = list(dict.fromkeys(walkthrough._MERCHANTS))
  # 4,096 buckets instead of the engine's 384: at 384 two unrelated tokens
  # share a bucket often enough to fake a similarity on a 12-point figure.
  embedder = HashingEmbedder(dim=4096)
  src = np.array(embedder.embed(source))
  rows = []
  for cand in walkthrough._SCRIPTED_CANDIDATES:
    sim = float((src @ np.array(embedder.embed([cand])[0])).max())
    # Same order as `_pool_llm_yield`: format gate first, then novelty.
    if not in_format(cand):
      outcome = "format-rejected"
    elif cand in set(source):
      outcome = "copy — rejected"
    else:
      outcome = "novel — pooled"
    rows.append((cand, sim, outcome))
  rows.sort(key=lambda r: (r[1], r[0]))
  style = {
      "novel — pooled": (AQUA, "o"),
      "copy — rejected": (ORANGE, "X"),
      "format-rejected": (MUTED, "s"),
  }
  fig, ax = plt.subplots(figsize=(12.5, 5.6), facecolor=SURFACE)
  ws5._style(ax, grid_axis="x")
  seen = set()
  for y, (cand, sim, outcome) in enumerate(rows):
    color, marker = style[outcome]
    ax.plot([0, sim], [y, y], color=GRID, linewidth=1.2, zorder=1)
    ax.scatter(
        sim,
        y,
        s=110,
        color=color,
        marker=marker,
        edgecolors=SURFACE,
        linewidths=1.4,
        zorder=3,
        label=None if outcome in seen else outcome)
    seen.add(outcome)
    ax.text(
        sim + 0.025,
        y,
        cand,
        color=INK,
        fontsize=9.5,
        va="center",
        family="monospace")
  ax.set_yticks([])
  ax.set_xlim(-0.03, 1.55)
  ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
  ax.set_ylim(-0.8, len(rows) - 0.2)
  ax.set_xlabel(
      "cosine similarity to the NEAREST source value  "
      "(0 = unlike anything real · 1 = a real value)",
      color=MUTED,
      fontsize=9.5)
  ax.axvline(1.0, color=ORANGE, linewidth=1.0, linestyle=(0, (4, 3)))
  ax.legend(
      frameon=True,
      facecolor=SURFACE,
      edgecolor=GRID,
      framealpha=1.0,
      fontsize=9,
      loc="lower right",
      labelcolor=INK)
  ws5._title(
      ax, "Fidelity pulls right, originality stops at the wall: "
      "a candidate may sit near a real value, never on one",
      "12 scripted candidates · gates = _format_gate() + the rejection set · "
      "similarity = HashingEmbedder, 4,096 buckets (lexical, laptop)")
  fig.tight_layout()
  fig.savefig(
      ASSETS / "rag-fidelity-originality.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
def _clause_boxes(ax, clauses, colors, x0, y, *, width, line_h, size=9.2):
  """Lay monospace clause boxes left to right, wrapping at `width`, using
    each box's MEASURED extent; returns the y of the last line."""
  renderer = ax.figure.canvas.get_renderer()
  to_axes = ax.transAxes.inverted()
  x, gap = x0, 0.008
  for text, color in zip(clauses, colors, strict=True):
    box = ax.text(
        x,
        y,
        text,
        family="monospace",
        fontsize=size,
        color=INK,
        va="center",
        ha="left",
        bbox={
            "boxstyle": "round,pad=0.32",
            "facecolor": color + "26",
            "edgecolor": color,
            "linewidth": 1.1
        })
    # The text extent is final at creation; the bbox patch is not (it is
    # sized at draw time), so add its padding — 0.32 em per side — by hand.
    ext = box.get_window_extent(renderer)
    pad_px = 2 * 0.32 * size * ax.figure.dpi / 72.0
    w = (
        to_axes.transform((ext.x1 + pad_px, 0))[0] - to_axes.transform(
            (ext.x0, 0))[0])
    if x + w > x0 + width and x > x0:
      x, y = x0, y - line_h
      box.set_position((x, y))
    x += w + gap
  return y


def fig_great_serialization() -> None:
  columns = walkthrough.COLUMNS
  colors = [BLUE, AQUA, ORANGE, SLATE, PURPLE]
  rows = [dict(walkthrough.ROWS[0]), dict(walkthrough.ROWS[72])]
  rows.append({**walkthrough.ROWS[5], "merchant_name": None})

  fig, ax = plt.subplots(figsize=(14.5, 8.0), facecolor=SURFACE)
  fig.subplots_adjust(left=0.005, right=0.995, bottom=0.005, top=0.95)
  ax.set_axis_off()
  ax.set_xlim(0, 1)
  ax.set_ylim(0, 1)
  fig.canvas.draw()  # a renderer must exist before extents are measured

  # -- the table ------------------------------------------------------------
  col_x = [0.015, 0.085, 0.150, 0.215, 0.290]
  col_w = [0.066, 0.061, 0.061, 0.071, 0.170]
  top, cell_h = 0.855, 0.052
  for cx, cw, name, color in zip(col_x, col_w, columns, colors, strict=True):
    ax.add_patch(
        plt.Rectangle((cx, top), cw, cell_h, facecolor=color,
                      edgecolor=SURFACE))
    ax.text(
        cx + cw / 2,
        top + cell_h / 2,
        name,
        color=SURFACE,
        fontsize=8.6,
        fontweight="600",
        ha="center",
        va="center",
        family="monospace")
  for r, row in enumerate(rows):
    y = top - (cell_h + 0.002) * (r + 1)
    for cx, cw, name in zip(col_x, col_w, columns, strict=True):
      ax.add_patch(
          plt.Rectangle((cx, y), cw, cell_h, facecolor=SURFACE, edgecolor=GRID))
      value = row[name]
      ax.text(
          cx + cw / 2,
          y + cell_h / 2,
          "NULL" if value is None else str(value),
          color=MUTED if value is None else INK,
          fontsize=8.2,
          ha="center",
          va="center",
          family="monospace")
  ax.text(
      0.015,
      0.935,
      "a BigQuery table — rows and cells",
      color=INK,
      fontsize=11,
      fontweight="600")
  ax.text(
      0.015,
      0.60, "what the embedder receives for row 1 — one string, "
      "clauses joined by “, ”:",
      color=INK,
      fontsize=9.6,
      fontweight="600")
  ax.text(
      0.015,
      0.545,
      textwrap.fill(serialize_row(rows[0], columns), width=64),
      color=INK,
      fontsize=9.2,
      family="monospace",
      va="top",
      bbox={
          "boxstyle": "round,pad=0.5",
          "facecolor": "#f1f3f4",
          "edgecolor": SLATE,
          "linewidth": 1.0
      })

  # -- the sentences ----------------------------------------------------------
  ax.text(
      0.50,
      0.935, "serialize_row()  →  one sentence per row: "
      "“column is value”, in schema order",
      color=INK,
      fontsize=11,
      fontweight="600")
  y = 0.88
  for r, row in enumerate(rows):
    clauses = serialize_row(row, columns).split(", ")
    ax.annotate(
        "",
        xy=(0.495, y),
        xytext=(0.465, top - (cell_h + 0.002) * (r + 1) + cell_h / 2),
        arrowprops={
            "arrowstyle": "-|>",
            "color": MUTED,
            "linewidth": 1.1
        })
    y = _clause_boxes(
        ax, clauses, colors, 0.50, y, width=0.49, line_h=0.054) - 0.082
  ax.text(
      0.50,
      y + 0.03, "a missing value is written down (“is null”), so two rows that "
      "differ only in what is absent still differ",
      color=MUTED,
      fontsize=9.2,
      style="italic")

  # -- order: the paper vs the pipeline ------------------------------------------
  base = serialize_row(rows[0], columns).split(", ")
  order = np.random.default_rng(SEED).permutation(len(base))
  ax.plot([0.015, 0.985], [0.405, 0.405], color=GRID, linewidth=1.0)
  ax.text(
      0.015,
      0.36, "GReaT, the paper — fine-tunes an LLM on these "
      "sentences, so it SHUFFLES the clauses on every pass: the model "
      "must not learn a column order",
      color=INK,
      fontsize=10.2,
      fontweight="600")
  _clause_boxes(
      ax, [base[i] for i in order], [colors[i] for i in order],
      0.015,
      0.305,
      width=0.97,
      line_h=0.054)
  ax.text(
      0.015,
      0.235, "this pipeline — EMBEDS them, so the order is the "
      "schema's, always: same row → same bytes → same vector → same "
      "row_digest, on any worker, on any run",
      color=INK,
      fontsize=10.2,
      fontweight="600")
  _clause_boxes(ax, base, colors, 0.015, 0.18, width=0.97, line_h=0.054)

  # -- the other chunk kind ---------------------------------------------------------
  ax.text(
      0.015,
      0.105, "the second chunk kind needs no sentence at all — "
      "one DISTINCT cell of a free-text column is its own chunk text:",
      color=INK,
      fontsize=10.2,
      fontweight="600")
  _clause_boxes(
      ax, [
          "CAFE ARBOL*MADRID", "METRO NORTE TRAVEL CH",
          "PAYLINK *BLUE FERN YOGA", "WWW.NUBEBOOKS.EXAMPLE"
      ], [PURPLE] * 4,
      0.015,
      0.05,
      width=0.97,
      line_h=0.054)
  fig.suptitle(
      "GReaT serialization: a row becomes a sentence an embedder can read — "
      "every clause keeps the colour of its column",
      color=INK,
      fontsize=12.5,
      fontweight="600",
      x=0.012,
      ha="left",
      y=0.995)
  fig.savefig(
      ASSETS / "rag-great-serialization.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
def _mds_2d(vectors: np.ndarray) -> np.ndarray:
  """Classical MDS of cosine distances — deterministic, NumPy only."""
  dist = np.sqrt(np.clip(2.0 - 2.0 * (vectors @ vectors.T), 0.0, None))
  n = len(dist)
  centering = np.eye(n) - np.ones((n, n)) / n
  gram = -0.5 * centering @ (dist**2) @ centering
  vals, vecs = np.linalg.eigh(gram)
  top = np.argsort(vals)[::-1][:2]
  coords = vecs[:, top] * np.sqrt(np.clip(vals[top], 0.0, None))
  # eigh leaves the sign of each axis free: pin it so the figure is stable.
  return coords * np.sign(coords[0])


def _neighbour_map(vectors: np.ndarray, k: int = 3, iters: int = 600):
  """2-D layout that keeps NEIGHBOURS together: a Fruchterman-Reingold
    spring layout of the k-nearest-neighbour cosine graph, started from the
    MDS coordinates so it is deterministic. 384 dimensions cannot be drawn
    faithfully in two; what this preserves is who sits next to whom — page
    distances are not cosines. Returns (coords, edges)."""
  sims = vectors @ vectors.T
  np.fill_diagonal(sims, -1.0)
  n = len(sims)
  edges = sorted({(min(i, int(j)), max(i, int(j)))
                  for i in range(n)
                  for j in np.argsort(sims[i])[::-1][:k]})
  pos = _mds_2d(vectors)
  pos = 0.5 * pos / np.abs(pos).max()
  ideal = 1.0 / np.sqrt(n)
  src = np.array([e[0] for e in edges])
  dst = np.array([e[1] for e in edges])
  # Stronger springs between more similar names: a rename hugs its original.
  weight = 0.4 + 1.6 * np.clip(np.array([sims[i, j] for i, j in edges]), 0, 1)
  for step in range(iters):
    delta = pos[:, None, :] - pos[None, :, :]
    dist = np.linalg.norm(delta, axis=2) + 1e-6
    move = ((delta / dist[..., None]) *
            (ideal**2 / dist)[..., None]).sum(axis=1)
    pull = pos[src] - pos[dst]
    length = np.linalg.norm(pull, axis=1, keepdims=True) + 1e-6
    force = pull * (length / ideal) * weight[:, None]
    np.add.at(move, src, -force)
    np.add.at(move, dst, force)
    move -= 0.6 * pos  # weak gravity keeps loose names on the page
    temp = 0.08 * (1.0 - step / iters) + 0.002
    norm = np.linalg.norm(move, axis=1, keepdims=True) + 1e-9
    pos = pos + move / norm * np.minimum(norm, temp)
  pos -= pos.mean(axis=0)
  return pos / np.abs(pos).max(), edges


def _draw_links(ax, xy, edges, n_squad: int) -> None:
  for i, j in edges:
    if i < n_squad and j < n_squad:
      ax.plot(
          *zip(xy[i], xy[j], strict=True), color=GRID, linewidth=0.7, zorder=0)


def _draw_seed_panel(ax, xy_squad, squad, seeds) -> None:
  """Which eight real names the prompt shows, per strategy."""
  for (x, y), label in zip(xy_squad, squad, strict=True):
    in_c, in_k = label in seeds["centroid"], label in seeds["kcenter"]
    if in_c:
      ax.scatter(
          x,
          y,
          s=150,
          color=ORANGE,
          edgecolors=SURFACE,
          linewidths=1.4,
          zorder=3)
    if in_k:
      ax.scatter(
          x,
          y,
          s=46 if in_c else 150,
          color=BLUE,
          marker="s",
          edgecolors=SURFACE,
          linewidths=1.4,
          zorder=4)
    if not (in_c or in_k):
      ax.scatter(x, y, s=14, color=DOT, linewidths=0, zorder=1)
    ax.text(
        x,
        y + 0.03,
        label,
        fontsize=8.6 if (in_c or in_k) else 7.0,
        color=INK if (in_c or in_k) else MUTED,
        ha="center",
        fontweight="600" if (in_c or in_k) else "normal",
        zorder=5)
  ax.scatter([], [],
             s=90,
             color=ORANGE,
             label="centroid — the 8 most typical spellings")
  ax.scatter([], [],
             s=90,
             color=BLUE,
             marker="s",
             label="kcenter — the 8 farthest from each other")
  ax.legend(
      frameon=True,
      facecolor=SURFACE,
      edgecolor=GRID,
      framealpha=1.0,
      fontsize=9,
      loc="lower left",
      labelcolor=INK)
  _map_axes(
      ax, "which eight real names does the prompt show?",
      "fifty names, linked to their 3 nearest neighbours in 384-d; "
      "seeds by select_seed_examples()")


def _candidate_verdict(cand: str, squad: list[str], in_format) -> str:
  """Same order as `_pool_llm_yield`: format gate first, then novelty."""
  if not in_format(cand):
    return "off-format — rejected"
  if cand in squad:
    return "copy — rejected by the wall"
  if cand in walkthrough._RENAMES:
    return "one-letter rename — POOLED"
  return "Brazilian-Irish invention — pooled"


def _draw_candidate_panel(ax, xy_squad, xy_cands, squad, cands) -> None:
  """Where the scripted candidates land, tied to their nearest real name."""
  prof = profile_columns(
      walkthrough.squad_schema(walkthrough.SQUAD_CLAUSE),
      walkthrough.SQUAD_ROWS)["player_name"]
  in_format = b1._format_gate(prof)
  styles = {
      "copy — rejected by the wall": (ORANGE, "X"),
      "one-letter rename — POOLED": (PURPLE, "D"),
      "Brazilian-Irish invention — pooled": (AQUA, "o"),
      "off-format — rejected": (SLATE, "s"),
  }
  ax.scatter(
      xy_squad[:, 0], xy_squad[:, 1], s=14, color=DOT, linewidths=0, zorder=1)
  seen, named = set(), set()
  for (x, y), cand in zip(xy_cands, cands, strict=True):
    verdict = _candidate_verdict(cand, squad, in_format)
    color, marker = styles[verdict]
    nearest, _ = walkthrough.nearest_real_name(cand)
    nx, ny = xy_squad[squad.index(nearest)]
    named.add(nearest)
    ax.plot([x, nx], [y, ny], color=color, linewidth=1.3, alpha=0.85, zorder=2)
    ax.scatter(
        x,
        y,
        s=95,
        color=color,
        marker=marker,
        edgecolors=SURFACE,
        linewidths=1.3,
        zorder=4,
        label=None if verdict in seen else verdict)
    seen.add(verdict)
    ax.text(
        x,
        y + 0.032,
        cand,
        fontsize=8.6,
        color=INK,
        ha="center",
        fontweight="600",
        zorder=5)
  for (x, y), label in zip(xy_squad, squad, strict=True):
    if label in named:
      ax.text(
          x, y - 0.05, label, fontsize=7.2, color=MUTED, ha="center", zorder=3)
  ax.legend(
      frameon=True,
      facecolor=SURFACE,
      edgecolor=GRID,
      framealpha=1.0,
      fontsize=9,
      loc="lower left",
      labelcolor=INK)
  _map_axes(
      ax, "where do 14 scripted candidates land?",
      "each tied to its nearest real name (grey label) — only an EXACT "
      "match hits the wall")


def _draw_vector_strip(ax) -> None:
  """What a vector IS: the 384 numbers of six names, as lit cells."""
  shown = [
      "Ronaldo", "Ronaldinho", "Ronarid", "Roberto Carlos", "Roberto Larcos",
      "Fergalinho"
  ]
  mat = np.abs(np.array(walkthrough.squad_vectors(shown)))
  ax.imshow(
      mat,
      aspect="auto",
      cmap="Blues",
      vmin=0,
      vmax=mat.max(),
      interpolation="nearest")
  ax.set_yticks(range(len(shown)))
  ax.set_yticklabels(shown, fontsize=9, color=INK, family="monospace")
  ax.set_xticks([0, 95, 191, 287, 383])
  ax.set_xticklabels(["dim 1", "96", "192", "288", "384"],
                     fontsize=8.5,
                     color=MUTED)
  ax.tick_params(length=0)
  for side in ax.spines.values():
    side.set_color(GRID)
  ax.set_title(
      "what a vector IS: 384 numbers per name (|value| shown). Spellings "
      "that share letters light the same cells — on the workers bge-small "
      "fills every cell; the geometry is the same",
      color=INK,
      fontsize=10.2,
      fontweight="600",
      loc="left",
      pad=8)


def fig_names_pes_map() -> None:
  squad = list(walkthrough.SQUAD)
  cands = list(walkthrough.SQUAD_CANDIDATES)
  vec_squad = np.array(walkthrough.squad_vectors(squad))
  xy, edges = _neighbour_map(
      np.vstack([vec_squad,
                 np.array(walkthrough.squad_vectors(cands))]))
  xy_squad, xy_cands = xy[:len(squad)], xy[len(squad):]
  seeds = {
      s: set(select_seed_examples(vec_squad.tolist(), squad, K, strategy=s))
      for s in ("centroid", "kcenter")
  }

  fig = plt.figure(figsize=(16.5, 11.6), facecolor=SURFACE)
  grid = fig.add_gridspec(
      2, 2, height_ratios=[4.4, 1.0], hspace=0.13, wspace=0.04)
  ax = fig.add_subplot(grid[0, 0])
  _draw_links(ax, xy, edges, len(squad))
  _draw_seed_panel(ax, xy_squad, squad, seeds)
  ax = fig.add_subplot(grid[0, 1])
  _draw_links(ax, xy, edges, len(squad))
  _draw_candidate_panel(ax, xy_squad, xy_cands, squad, cands)
  _draw_vector_strip(fig.add_subplot(grid[1, :]))

  fig.suptitle(
      "PES mode: fifty real names in embedding space — which eight the "
      "prompt shows, and where copies, renames and inventions land",
      color=INK,
      fontsize=13,
      fontweight="600",
      x=0.012,
      ha="left",
      y=0.995)
  fig.subplots_adjust(left=0.085, right=0.992, bottom=0.035, top=0.915)
  fig.savefig(ASSETS / "rag-names-pes-map.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


def _map_axes(ax, name: str, sub: str) -> None:
  ax.set_title(
      name,
      color=INK,
      fontsize=10.5,
      fontweight="600",
      loc="left",
      pad=24,
      family="monospace")
  ax.text(
      0,
      1.02,
      sub,
      transform=ax.transAxes,
      color=MUTED,
      fontsize=9,
      va="bottom")
  ax.set_xticks([])
  ax.set_yticks([])
  ax.set_facecolor(SURFACE)
  ax.margins(0.09)
  for side in ax.spines.values():
    side.set_color(GRID)


# --------------------------------------------------------------------------
def fig_setup_cost() -> None:
  """EVIDENCE — WS5 MEASURED block (1M-row run 2026-07-26_06_54_25)."""
  n = ws5.N_SETUPS
  items = (
      ("build the exact index\n(b1_index_built)", ws5.INDEX_BUILD / n, AQUA),
      ("read vectors back from rag_chunks\n(b1_chunks_reused)",
       ws5.CHUNK_REUSE / n, BLUE),
      ("build the free-text pools — the LLM ladder\n(b1_pools_built)",
       ws5.POOL_BUILD_WALL / n, ORANGE),
  )
  fig, ax = plt.subplots(figsize=(11.5, 3.9), facecolor=SURFACE)
  ws5._style(ax, grid_axis="x")
  for y, item in enumerate(items):
    secs, color = item[1], item[2]
    ax.plot([0.01, secs], [y, y], color=GRID, linewidth=1.4, zorder=1)
    ax.scatter(
        secs,
        y,
        s=130,
        color=color,
        edgecolors=SURFACE,
        linewidths=1.6,
        zorder=3)
    shown = f"{secs:.2f} s" if secs < 1 else f"{secs:,.1f} s"
    ax.text(
        secs * 1.35,
        y,
        shown,
        color=INK,
        fontsize=10.5,
        va="center",
        fontweight="600")
  ax.set_yticks(range(len(items)))
  ax.set_yticklabels([i[0] for i in items], color=INK, fontsize=9.5)
  ax.set_xscale("log")
  ax.set_xlim(0.01, 6000)
  ax.set_ylim(-0.6, len(items) - 0.4)
  ax.set_xlabel(
      "mean seconds per DoFn.setup()  (log scale)", color=MUTED, fontsize=9.5)
  ws5._title(
      ax, "Retrieval is the cheap part of a worker's setup; the ladder is "
      "the bill",
      f"1M-row run 2026-07-26 · {n} setups · before pools were persisted "
      "(ADR 0020) — milestone totals ÷ setups")
  fig.tight_layout()
  fig.savefig(ASSETS / "rag-setup-cost.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
def fig_prefix_vs_kcenter() -> None:
  rng = np.random.default_rng(7)
  pts, rare = [], []
  for i, (center, n, spread) in enumerate(PREFIX_MODES):
    pts.append(rng.normal(center, spread, size=(n, 2)))
    rare += [i == len(PREFIX_MODES) - 1] * n
  pts_arr, rare_arr = np.vstack(pts), np.array(rare)
  order = rng.permutation(len(pts_arr))  # fingerprint order ≈ uniform
  pts_arr, rare_arr = pts_arr[order], rare_arr[order]
  budget = int(PREFIX_BUDGET * len(pts_arr))
  prefix = np.arange(budget)
  texts = [str(i) for i in range(len(pts_arr))]
  kc = np.array(
      [int(t) for t in retrieve_kcenter_k(pts_arr.tolist(), texts, budget)])
  n_rare = int(rare_arr.sum())
  fig, axes = plt.subplots(1, 3, figsize=(14.5, 5.2), facecolor=SURFACE)
  specs = (
      ("reference sample", None, None),
      ("prefix — what is indexed today", prefix, BLUE),
      ("greedy k-center — candidate, not implemented", kc, ORANGE),
  )
  for ax, (name, picks, color) in zip(axes, specs, strict=True):
    ax.scatter(pts_arr[:, 0], pts_arr[:, 1], s=4, color=DOT, linewidths=0)
    if picks is not None:
      ax.scatter(
          pts_arr[picks, 0],
          pts_arr[picks, 1],
          s=16,
          color=color,
          edgecolors=SURFACE,
          linewidths=0.5,
          zorder=3)
      ax.text(
          0.03,
          0.04,
          f"rare-mode points selected: {int(rare_arr[picks].sum())} / {n_rare}",
          transform=ax.transAxes,
          color=INK,
          fontsize=9.5,
          fontweight="600")
    cx, cy = PREFIX_MODES[-1][0]
    ax.add_patch(
        plt.Circle((cx, cy),
                   0.55,
                   fill=False,
                   color=AQUA,
                   linewidth=1.8,
                   linestyle=(0, (4, 3))))
    ax.text(
        cx, cy + 0.68, "rare mode (~1%)", color=INK, fontsize=9, ha="center")
    ax.set_title(name, color=INK, fontsize=10.5, fontweight="600", loc="left")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor(SURFACE)
    for side in ax.spines.values():
      side.set_color(GRID)
  fig.suptitle(
      f"Which rows get indexed, at a {PREFIX_BUDGET:.0%} illustration budget: "
      "a prefix follows the mass, k-center follows the space",
      color=INK,
      fontsize=12.5,
      fontweight="600",
      x=0.012,
      ha="left",
      y=0.985)
  fig.tight_layout(rect=(0, 0, 1, 0.94))
  fig.savefig(
      ASSETS / "prefix-vs-kcenter-coverage.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


if __name__ == "__main__":
  ASSETS.mkdir(parents=True, exist_ok=True)
  ws5.check_palette()
  assert (K, MAX_ROW_DOC_ROWS) == ARTICLE_CONSTANTS, "article 4 quotes both"
  points, labels = _manifold()
  fig_dense_vectors_3d(points, labels)
  fig_retrieval_gif(points, labels)
  fig_seed_pickers(points, labels)
  fig_seed_budget(points, labels)
  fig_fidelity_originality()
  fig_great_serialization()
  fig_names_pes_map()
  fig_setup_cost()
  fig_prefix_vs_kcenter()
  print(f"\nwrote 8 figures + 1 GIF to {ASSETS}")
