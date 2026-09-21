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
"""Regenerate the vLLM serving figures (article 3 — the common runtime).

    uv run --no-sync python3 scripts/doc/make_vllm_serving_figures.py

Writes PNGs into docs/designs/assets/vllm-serving-*.png. Every measured
number below is typed once, in the MEASURED blocks, and names the run it
came from; the engine-stats series is the vLLM API server's own periodic
stats line (`Engine 000: Avg prompt throughput … Prefix cache hit rate`)
extracted from `runs/<JOB_ID>/worker_logs.jsonl` with:

    grep -o 'Engine 000: Avg prompt throughput: [0-9.]* tokens/s, Avg
    generation throughput: [0-9.]* tokens/s, Running: [0-9]* reqs,
    Waiting: [0-9]* reqs, GPU KV cache usage: [0-9.]*%, Prefix cache hit
    rate: [0-9.]*%' worker_logs.jsonl | sort -u

The ignition and GPU-minute constants are imported from
`make_throughput_figures.py` (ADR 0034's figure script) so a superseding
run stays a one-place edit. The KV-budget panel is a CONCEPT figure: it
runs the pipeline's own sizing arithmetic
(`sdfb_beam.handlers.vllm_client._fit_max_model_len`) over a sweep of free
VRAM values; its only measured marker is the R6 clamp.

Palette matches the design-doc asset set (BLUE / ORANGE / AQUA) with the
OKLab separation check on every regeneration.
"""

# pyplot must be imported after matplotlib.use("Agg") selects the headless
# backend, and the sibling figure script after sys.path knows its directory.
# pylint: disable=wrong-import-position

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_throughput_figures as tp
from sdfb_beam.handlers import vllm_client as vc

ASSETS = tp.ASSETS
BLUE, ORANGE, AQUA = tp.BLUE, tp.ORANGE, tp.AQUA
INK, MUTED, GRID, SURFACE = tp.INK, tp.MUTED, tp.GRID, tp.SURFACE
PURPLE = "#7a3fd1"  # the GPU class colour of the mermaid/drawio house style

# --- MEASURED (2026-08-22_14_48_07-610462171680113414) ----------------------
# Three-table relational run (C_TABLE, A_TABLE, B_TABLE), n1-standard-8 +
# T4, qwen3/4b-instruct-2507 fp16, one vLLM server per worker. The series
# is the API server's 10-second stats line over the pool-ladder phase of
# one worker, 22:20:26 → 22:30:16 UTC (60 samples): prompt tokens/s,
# generation tokens/s, running requests, GPU KV cache usage %, cumulative
# prefix cache hit rate %. Waiting requests were 0 on every sample.
ENGINE_RUN = "2026-08-22_14_48_07-610462171680113414"
ENGINE_T0 = "22:20:26Z"
ENGINE_PROMPT_TPS = (227.6, 0.0, 4.4, 1.2, 21.2, 0.0, 6.8, 6.8, 4.4, 4.8, 7.6,
                     11.2, 0.0, 6.8, 6.0, 5.6, 0.0, 1.2, 4.8, 5.6, 3.6, 1.2,
                     0.0, 1.2, 0.0, 2.4, 0.0, 0.0, 1.2, 0.0, 2.4, 0.0, 0.0, 0.0,
                     2.4, 1.2, 0.0, 0.0, 0.0, 0.0, 0.0, 1.2, 0.0, 0.0, 0.0, 0.0,
                     0.0, 1.2, 0.0, 0.0, 0.0, 0.0, 0.0, 1.2, 0.0, 0.0, 0.0, 0.0,
                     0.0, 0.0)
ENGINE_GEN_TPS = (224.9, 294.0, 272.2, 245.0, 236.9, 257.6, 235.7, 245.0, 238.5,
                  225.3, 230.7, 258.3, 265.2, 240.5, 264.2, 234.0, 210.0, 193.3,
                  192.0, 179.7, 177.7, 195.2, 201.6, 171.1, 165.6, 139.3, 123.2,
                  115.2, 100.5, 118.4, 123.9, 126.4, 119.2, 110.4, 109.6, 112.2,
                  132.0, 122.4, 82.0, 72.4, 68.8, 44.7, 82.8, 78.0, 74.0, 70.8,
                  66.4, 66.9, 80.8, 76.4, 72.8, 69.2, 50.2, 80.9, 79.2, 75.2,
                  72.0, 68.4, 23.7, 0.0)
ENGINE_RUNNING = (28, 28, 28, 22, 28, 28, 25, 28, 27, 28, 25, 28, 26, 25, 28,
                  20, 20, 17, 17, 19, 20, 16, 12, 12, 12, 8, 8, 8, 8, 5, 8, 8,
                  8, 8, 8, 8, 8, 8, 4, 4, 4, 4, 4, 4, 4, 4, 3, 4, 4, 4, 4, 4, 1,
                  4, 4, 4, 4, 4, 0, 0)
ENGINE_KV_PCT = (16.0, 26.1, 32.6, 29.0, 25.6, 34.4, 27.4, 30.4, 34.5, 38.3,
                 25.0, 23.0, 30.0, 25.3, 28.7, 21.1, 27.6, 26.4, 28.3, 31.2,
                 25.6, 13.0, 13.9, 15.2, 21.1, 12.2, 16.4, 20.3, 10.0, 8.0,
                 10.0, 14.2, 18.3, 22.0, 17.7, 7.9, 12.4, 16.4, 9.5, 11.9, 14.3,
                 2.6, 5.4, 8.0, 10.4, 12.8, 11.8, 3.7, 6.5, 9.1, 11.5, 13.9,
                 5.4, 4.5, 7.4, 9.8, 12.4, 14.6, 0.0, 0.0)
ENGINE_HIT_PCT = (73.9, 73.9, 75.1, 76.8, 82.3, 82.3, 84.3, 85.8, 86.0, 86.3,
                  87.3, 88.8, 88.8, 89.5, 89.8, 90.1, 90.1, 90.3, 90.4, 90.6,
                  91.1, 91.6, 91.6, 91.7, 91.7, 92.0, 92.0, 92.0, 92.3, 92.3,
                  92.5, 92.5, 92.5, 92.5, 92.8, 93.0, 93.0, 93.0, 93.0, 93.0,
                  93.0, 93.3, 93.3, 93.3, 93.3, 93.3, 93.3, 93.5, 93.5, 93.5,
                  93.5, 93.5, 93.5, 93.7, 93.7, 93.7, 93.7, 93.7, 93.7, 93.7)
ENGINE_SAMPLE_S = 10  # the API server logs its stats every 10 s
LADDER_CHOICES = 4  # n=4 completions per request → 4 running per ladder
# The same worker's vLLM startup lines on a free card (dtype=torch.float16,
# max_seq_len=8192, enable_prefix_caching=True, TRITON_ATTN backend):
# "Model loading took 7.56 GiB", "Graph capturing … took 0.56 GiB",
# "GPU KV cache size: 29,344 tokens", "Maximum concurrency for 8,192
# tokens per request: 3.58x".
STARTUP_MODEL_GIB = 7.56
STARTUP_GRAPHS_GIB = 0.56
STARTUP_KV_TOKENS = 29_344
STARTUP_CONCURRENCY_8K = 3.58

# --- MEASURED (R6 cold, 2026-08-29_07_33_36-13355700596190055276) -----------
# vLLM ignition on the cold R6 run (ADR 0034 § Acceptance evidence, "model
# pull / vLLM ignition" row); the R7-era values live in make_throughput_figures.
VLLM_READY_S_R6_COLD = 226.0
# `vllm_max_model_len_clamped requested=8192 fitted=4288` on the same run
# (design doc 2026-09-07 §6): the card held embedders at ignition time.
MAX_MODEL_LEN_CLAMPED_R6 = 4288

# --- CONCEPT (KV budget sweep; parameters annotated) -------------------------
# T4 as torch reports it (14.56 GiB total — the OOM messages of WS6) and the
# Qwen3-4B-Instruct-2507 fp16 checkpoint as staged (3 shards, 7.5 GB on
# disk); geometry 36 layers x 8 KV heads x 128 head-dim → 144 KiB/token.
# These values illustrate the mechanism on the accelerator the runs used;
# the arithmetic is the client's own (`_fit_max_model_len`).
T4_TOTAL_BYTES = int(14.56 * 1024**3)
QWEN3_4B_WEIGHTS_BYTES = int(7.5 * 1000**3)
QWEN3_4B_CONFIG = {
    "num_hidden_layers": 36,
    "num_key_value_heads": 8,
    "head_dim": 128
}
REQUESTED_MAX_MODEL_LEN = 8192  # the DAG's vllm_max_model_len default


def _pull_pct(free_bytes: int) -> float:
  return free_bytes / T4_TOTAL_BYTES * 100


def fitted_len_for_free(free_bytes: int) -> int:
  """The client's clamp for one free-VRAM reading (mirror of
    `_clamp_max_model_len`: budget = min(free - margin, cap x total))."""
  budget = int(
      min(
          free_bytes - vc._VLLM_VRAM_MARGIN_BYTES,  # pylint: disable=protected-access
          vc._VLLM_MAX_DYNAMIC_UTILIZATION * T4_TOTAL_BYTES))  # pylint: disable=protected-access
  kv_bpt = vc._kv_bytes_per_token(QWEN3_4B_CONFIG)  # pylint: disable=protected-access
  assert kv_bpt is not None
  return vc._fit_max_model_len(  # pylint: disable=protected-access
      REQUESTED_MAX_MODEL_LEN,
      budget_bytes=budget,
      weights_bytes=QWEN3_4B_WEIGHTS_BYTES,
      kv_bytes_per_token=kv_bpt)


# --------------------------------------------------------------------------
def fig_engine_stats():
  """Claim: the pool ladders are decode-bound and batched — with 28
    requests in flight one T4 sustained 225-294 generated tokens/s while
    the KV cache never passed 38 %, prompt tokens were paid only at round
    boundaries after the first window, and the prefix cache hit rate
    climbed from 74 % to 94 % over ten minutes."""
  t = np.arange(len(ENGINE_GEN_TPS)) * ENGINE_SAMPLE_S / 60.0
  fig, axes = plt.subplots(
      3,
      1,
      figsize=(13.0, 9.2),
      facecolor=SURFACE,
      sharex=True,
      gridspec_kw={"height_ratios": (1.35, 1.0, 0.85)})
  ax_tok, ax_req, ax_hit = axes

  ax_tok.fill_between(
      t, ENGINE_GEN_TPS, color=PURPLE, alpha=0.18, zorder=2, linewidth=0)
  ax_tok.plot(
      t,
      ENGINE_GEN_TPS,
      color=PURPLE,
      linewidth=2.0,
      zorder=3,
      label="generation (decode) tokens/s")
  ax_tok.plot(
      t,
      ENGINE_PROMPT_TPS,
      color=ORANGE,
      linewidth=1.6,
      zorder=4,
      label="prompt (prefill) tokens/s")
  ax_tok.set_ylabel("tokens / s (10-s windows)", color=MUTED, fontsize=9)
  ax_tok.set_ylim(0, 320)
  peak = max(ENGINE_GEN_TPS)
  ax_tok.annotate(
      f"peak {peak:.0f} tok/s with {max(ENGINE_RUNNING)} requests in flight\n"
      "(seven ladders x n=4 completions on one T4)",
      xy=(t[ENGINE_GEN_TPS.index(peak)], peak),
      xytext=(2.6, 300),
      color=INK,
      fontsize=8.8,
      arrowprops={
          "arrowstyle": "-|>",
          "color": INK,
          "lw": 1.0
      })
  ax_tok.annotate(
      f"first window: {ENGINE_PROMPT_TPS[0]:.0f} prompt tok/s — the prefixes "
      "are computed once;\nevery later round pays only its un-cached "
      f"suffix (≤ {max(ENGINE_PROMPT_TPS[1:]):.0f} tok/s)",
      xy=(t[0], ENGINE_PROMPT_TPS[0]),
      xytext=(0.55, 200),
      color=ORANGE,
      fontsize=8.8,
      arrowprops={
          "arrowstyle": "-|>",
          "color": ORANGE,
          "lw": 1.0
      })
  ax_tok.legend(frameon=False, fontsize=8.8, labelcolor=INK, loc="upper right")
  tp._style(ax_tok)  # pylint: disable=protected-access
  tp._title(  # pylint: disable=protected-access
      ax_tok, "One T4, ten minutes of pool ladders: decode-bound, batched, "
      "under 40 % of the KV budget",
      f"vLLM API-server stats of one worker, run {ENGINE_RUN} "
      "(three-table relational, Qwen3-4B fp16); 10-second windows from "
      f"{ENGINE_T0}")

  ax_req.bar(
      t,
      ENGINE_RUNNING,
      width=ENGINE_SAMPLE_S / 60.0 * 0.9,
      color=BLUE,
      alpha=0.85,
      zorder=3,
      label="running requests")
  ax_req.set_ylabel("requests in flight", color=MUTED, fontsize=9)
  ax_req.set_ylim(0, 32)
  ax_kv = ax_req.twinx()
  ax_kv.plot(
      t,
      ENGINE_KV_PCT,
      color=AQUA,
      linewidth=2.0,
      zorder=4,
      label="GPU KV cache usage %")
  ax_kv.set_ylim(0, 100)
  ax_kv.set_ylabel("KV cache usage (%)", color=MUTED, fontsize=9)
  ax_kv.tick_params(colors=MUTED, labelsize=9, length=0)
  for side in ("top", "right", "left", "bottom"):
    ax_kv.spines[side].set_visible(False)
  kv_peak = max(ENGINE_KV_PCT)
  ax_kv.annotate(
      f"KV peak {kv_peak:.0f} % — the T4 budget is not the binding "
      "constraint at this concurrency",
      xy=(t[ENGINE_KV_PCT.index(kv_peak)], kv_peak),
      xytext=(1.9, 72),
      color=AQUA,
      fontsize=8.8,
      arrowprops={
          "arrowstyle": "-|>",
          "color": AQUA,
          "lw": 1.0
      })
  ax_req.text(
      t[-14],
      6.5, f"one ladder left: {LADDER_CHOICES} requests\n"
      "(n = 4 completions per round)",
      color=BLUE,
      fontsize=8.6,
      ha="center")
  handles = ax_req.get_legend_handles_labels(
  )[0] + ax_kv.get_legend_handles_labels()[0]
  labels = ax_req.get_legend_handles_labels(
  )[1] + ax_kv.get_legend_handles_labels()[1]
  ax_req.legend(
      handles,
      labels,
      frameon=False,
      fontsize=8.8,
      labelcolor=INK,
      loc="upper right")
  tp._style(ax_req)  # pylint: disable=protected-access

  ax_hit.plot(t, ENGINE_HIT_PCT, color=ORANGE, linewidth=2.2, zorder=3)
  ax_hit.fill_between(
      t, ENGINE_HIT_PCT, color=ORANGE, alpha=0.12, zorder=2, linewidth=0)
  ax_hit.set_ylim(60, 100)
  ax_hit.set_ylabel("prefix cache hit rate (%)", color=MUTED, fontsize=9)
  ax_hit.set_xlabel("minutes into the pool phase", color=MUTED, fontsize=9)
  ax_hit.text(
      t[0] + 0.1,
      ENGINE_HIT_PCT[0] - 4.5,
      f"{ENGINE_HIT_PCT[0]:.0f} % after round 1",
      color=INK,
      fontsize=8.8)
  ax_hit.text(
      t[-1],
      ENGINE_HIT_PCT[-1] + 1.5,
      f"{ENGINE_HIT_PCT[-1]:.1f} % cumulative — every round after the "
      "first reuses its column's KV prefix",
      color=INK,
      fontsize=8.8,
      ha="right")
  tp._style(ax_hit)  # pylint: disable=protected-access

  fig.tight_layout()
  fig.savefig(
      ASSETS / "vllm-serving-engine-stats.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
_IGNITION_TARGET = 3  # the "free card" launch the next runs must match
_IGNITION_RUNS = (
    ("R6 cold\n08-29 · single", VLLM_READY_S_R6_COLD, None,
     "one process, embedders demoted first"),
    ("R7 single\n09-07", tp.VLLM_READY_S["single"], None,
     "one process, same shape"),
    ("R7 multi\n09-07", tp.VLLM_READY_S["multi"], None,
     "8 GPU embedders beside the spawn\n→ 8 x vllm_unfittable_wait"),
    ("multi, pools rebuilt\n09-07 15:53", tp.VLLM_READY_S["multi warm"],
     tp.MODEL_PULL_S["multi warm"], "RAG store warm, free card — the target"),
    ("multi cold, CPU embeds\n09-08", tp.VLLM_READY_S["multi cold cpu"],
     tp.MODEL_PULL_S["multi cold cpu"],
     "56 CPU embedders starve the VM:\npull 3.4x, engine init 3.3x"),
)


def fig_ignition():
  """Claim: the same 7.5 GB checkpoint ignited in 183 s on a free card and
    599 s beside fifty-six CPU embedders — ignition time is set by the
    card's and the VM's other tenants, not by the model."""
  fig, ax = plt.subplots(figsize=(13.0, 5.6), facecolor=SURFACE)
  n = len(_IGNITION_RUNS)
  ys = np.arange(n)
  for i, (_, ready, pull, note) in enumerate(_IGNITION_RUNS):
    colour = AQUA if i == _IGNITION_TARGET else PURPLE
    ax.barh(i, ready, height=0.55, color=colour, zorder=3)
    if pull is not None:
      ax.barh(
          i,
          pull,
          height=0.55,
          color=SURFACE,
          alpha=0.45,
          zorder=4,
          edgecolor="none")
      ax.text(
          pull / 2,
          i,
          f"pull {pull:.0f} s",
          ha="center",
          va="center",
          color=INK,
          fontsize=8.4)
    ax.text(
        ready + 6,
        i,
        f"{ready:.0f} s  ·  {note}",
        va="center",
        color=INK,
        fontsize=8.8)
  ax.set_yticks(ys)
  ax.set_yticklabels([r[0] for r in _IGNITION_RUNS], fontsize=9.5)
  ax.invert_yaxis()
  ax.set_xlim(0, 900)
  ax.set_xlabel(
      "seconds from model_client_setup_start to vllm_ready (weight pull "
      "shown where the log carries it)",
      color=MUTED,
      fontsize=9)
  tp._style(ax, grid_axis="x")  # pylint: disable=protected-access
  tp._title(  # pylint: disable=protected-access
      ax, "vLLM ignition across five launches: 226 → 190 → 344 → 183 → 599 s "
      "for the same 7.5 GB checkpoint",
      "n1-standard-8 + T4, Qwen3-4B fp16; what changed between bars is who "
      "else held the card and the vCPUs while vLLM sized its KV budget "
      "(ADR 0034 D8 rev. 1 → rev. 2)")
  fig.tight_layout()
  fig.savefig(ASSETS / "vllm-serving-ignition.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
def fig_gpu_minutes():
  """Claim: the GPU was busy for 11 of the 274 minutes it was billed on
    the cold run and for 0 of 272 on the warm one — the LLM is O(1) per
    column, the accelerator bill is O(wall time)."""
  busy_embed = sum(hi - lo for lo, hi in tp.GPU_BUSY_COLD[:1])
  busy_llm = sum(hi - lo for lo, hi in tp.GPU_BUSY_COLD[1:])
  busy_cold = busy_embed + busy_llm
  fig, ax = plt.subplots(figsize=(11.0, 4.6), facecolor=SURFACE)
  runs = ("R6 cold (08-29)", "R6 warm re-trigger (08-29)")
  billed = (tp.GPU_BILLED_MIN_COLD, tp.GPU_BILLED_MIN_WARM)
  busy = ((busy_embed, busy_llm), (0.0, 0.0))
  for i, (b, (be, bl)) in enumerate(zip(billed, busy, strict=True)):
    ax.barh(
        i,
        b,
        height=0.5,
        color=GRID,
        zorder=2,
        label="GPU-minutes billed (4 x T4 x wall time)" if i == 0 else None)
    ax.barh(
        i,
        be,
        height=0.5,
        color=AQUA,
        zorder=3,
        label="busy: population embeds (bge-small on CUDA)" if i == 0 else None)
    ax.barh(
        i,
        bl,
        left=be,
        height=0.5,
        color=PURPLE,
        zorder=3,
        label="busy: vLLM spawn → last pool ladder" if i == 0 else None)
    total_busy = be + bl
    ax.text(
        b + 3,
        i, f"{total_busy:.1f} busy of {b:.0f} billed "
        f"({total_busy / b * 100:.0f} %)",
        va="center",
        color=INK,
        fontsize=9.2,
        fontweight="600")
  ax.set_yticks(range(2))
  ax.set_yticklabels(runs, fontsize=9.5)
  ax.invert_yaxis()
  ax.set_xlim(0, 360)
  ax.set_xlabel("GPU-minutes", color=MUTED, fontsize=9)
  ax.legend(
      frameon=False,
      fontsize=8.8,
      labelcolor=INK,
      loc="upper center",
      bbox_to_anchor=(0.5, -0.22),
      ncol=3)
  tp._style(ax, grid_axis="x")  # pylint: disable=protected-access
  tp._title(  # pylint: disable=protected-access
      ax, f"The GPU was busy {busy_cold:.0f} of {tp.GPU_BILLED_MIN_COLD:.0f} "
      "billed minutes cold, and 0 of "
      f"{tp.GPU_BILLED_MIN_WARM:.0f} warm",
      "10M rows/table pair, 2026-08-29 — billed: TotalGpuTime; busy: the "
      "embed span and vllm_spawn → last freetext_pool_built")
  fig.tight_layout()
  fig.savefig(
      ASSETS / "vllm-serving-gpu-minutes.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


# --------------------------------------------------------------------------
def fig_kv_budget():
  """Claim (concept): the KV budget is what is left of the card after the
    utilization cap, the weights and vLLM's own overhead — on a free T4 a
    Qwen3-4B fp16 server fits its 8,192-token request comfortably, and
    every gibibyte a sibling CUDA context holds at ignition removes
    ~7,300 tokens of context until the 4,096 floor refuses the spawn."""
  free_gib = np.linspace(8.0, T4_TOTAL_BYTES / 1024**3, 400)
  fitted = np.array([fitted_len_for_free(int(f * 1024**3)) for f in free_gib])
  floor = vc._VLLM_MIN_MODEL_LEN  # pylint: disable=protected-access
  fig, (ax_bar, ax_line) = plt.subplots(
      1,
      2,
      figsize=(13.0, 5.2),
      facecolor=SURFACE,
      gridspec_kw={"width_ratios": (1.0, 1.35)})

  # left: where a free T4 goes at ignition (GiB)
  total = T4_TOTAL_BYTES / 1024**3
  cap = vc._VLLM_MAX_DYNAMIC_UTILIZATION * total  # pylint: disable=protected-access
  weights = QWEN3_4B_WEIGHTS_BYTES / 1024**3
  overhead = vc._VLLM_NON_KV_OVERHEAD_BYTES / 1024**3  # pylint: disable=protected-access
  kv = cap - weights - overhead
  segs = (
      ("fp16 weights", weights, PURPLE),
      ("vLLM non-KV overhead", overhead, ORANGE),
      ("KV cache budget", kv, AQUA),
      ("headroom above the 0.85 cap", total - cap, GRID),
  )
  left = 0.0
  for i, (label, v, colour) in enumerate(segs):
    ax_bar.barh(0, v, left=left, height=0.5, color=colour, zorder=3)
    if colour == GRID:
      ax_bar.text(
          left + v + 0.2,
          0,
          f"{label}\n{v:.2f} GiB",
          ha="left",
          va="center",
          color=MUTED,
          fontsize=8.6)
    else:
      above = i % 2 == 0
      ax_bar.text(
          left + v / 2,
          0.32 if above else -0.32,
          f"{label}\n{v:.2f} GiB",
          ha="center",
          va="bottom" if above else "top",
          color=INK,
          fontsize=8.6)
    left += v
  ax_bar.set_xlim(0, total + 5.5)
  ax_bar.set_ylim(-1.5, 1.0)
  ax_bar.set_yticks([])
  ax_bar.set_xlabel(
      f"GiB of a free T4 ({total:.2f} GiB as torch reports it)",
      color=MUTED,
      fontsize=9)
  kv_bpt = vc._kv_bytes_per_token(QWEN3_4B_CONFIG)  # pylint: disable=protected-access
  assert kv_bpt is not None
  ax_bar.text(
      0,
      -0.62, f"KV cost {kv_bpt / 1024:.0f} KiB/token (36 layers x 8 KV heads x "
      f"128 dims x 2 B x K,V)\n→ {kv * 1024**3 / kv_bpt:,.0f} tokens of "
      f"budget on a free card; the DAG asks for {REQUESTED_MAX_MODEL_LEN:,}\n"
      f"vLLM's own startup line on a free card ({ENGINE_RUN[:10]}): model "
      f"{STARTUP_MODEL_GIB} GiB, CUDA graphs {STARTUP_GRAPHS_GIB} GiB,\n"
      f"KV cache {STARTUP_KV_TOKENS:,} tokens = {STARTUP_CONCURRENCY_8K}x an "
      "8,192-token request",
      color=MUTED,
      fontsize=8.4,
      va="top")
  tp._style(ax_bar, grid_axis="x")  # pylint: disable=protected-access
  tp._title(  # pylint: disable=protected-access
      ax_bar, "Where a free T4 goes at ignition",
      "gpu_memory_utilization derived from FREE memory, capped at 0.85")

  # right: fitted max_model_len vs free VRAM at ignition
  ax_line.plot(free_gib, fitted, color=BLUE, linewidth=2.2, zorder=3)
  ax_line.axhline(
      REQUESTED_MAX_MODEL_LEN, color=MUTED, linewidth=1.0, linestyle="--")
  ax_line.axhline(floor, color=ORANGE, linewidth=1.2, linestyle="--")
  ax_line.text(
      free_gib[0] + 0.1,
      REQUESTED_MAX_MODEL_LEN + 150,
      "requested 8,192 — fits when the card is free",
      color=MUTED,
      fontsize=8.6)
  ax_line.text(
      free_gib[-1] - 0.1,
      floor - 420,
      "floor 4,096: below it the spawn is refused and the card re-measured\n"
      "20 s later (vllm_max_model_len_unfittable → vllm_unfittable_wait)",
      color=ORANGE,
      fontsize=8.6,
      ha="right",
      va="top")
  # the R6 measured clamp, placed at the free VRAM that produces it
  idx = int(np.argmin(np.abs(fitted - MAX_MODEL_LEN_CLAMPED_R6)))
  ax_line.scatter([free_gib[idx]], [fitted[idx]], color=ORANGE, s=48, zorder=5)
  ax_line.annotate(
      f"R6 cold measured: clamped to {MAX_MODEL_LEN_CLAMPED_R6:,}\n"
      f"(≈ {free_gib[idx]:.1f} GiB free — sibling embedders still on the card)",
      xy=(free_gib[idx], fitted[idx]),
      xytext=(free_gib[idx] + 1.2, fitted[idx] + 1900),
      color=INK,
      fontsize=8.6,
      arrowprops={
          "arrowstyle": "-|>",
          "color": INK,
          "lw": 1.0
      })
  ax_line.set_xlabel("free VRAM at ignition (GiB)", color=MUTED, fontsize=9)
  ax_line.set_ylabel("fitted --max-model-len (tokens)", color=MUTED, fontsize=9)
  ax_line.set_ylim(0, 9000)
  tp._style(ax_line)  # pylint: disable=protected-access
  tp._title(  # pylint: disable=protected-access
      ax_line, "Every GiB a sibling holds costs ~7,300 tokens of context",
      "the client's own _fit_max_model_len over a sweep of free VRAM; "
      "the clamp only ever lowers the flag")
  fig.tight_layout()
  fig.savefig(ASSETS / "vllm-serving-kv-budget.png", dpi=160, facecolor=SURFACE)
  plt.close(fig)


if __name__ == "__main__":
  ASSETS.mkdir(parents=True, exist_ok=True)
  tp.check_palette()
  fig_engine_stats()
  fig_ignition()
  fig_gpu_minutes()
  fig_kv_budget()
  for name in ("vllm-serving-engine-stats", "vllm-serving-ignition",
               "vllm-serving-gpu-minutes", "vllm-serving-kv-budget"):
    png = ASSETS / f"{name}.png"
    print(f"wrote {png}")
