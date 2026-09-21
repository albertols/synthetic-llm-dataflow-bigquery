# Medium article series — index

The VCS-tracked sources for the Medium series on this project
([medium.com/@serna.alberto.eng](https://medium.com/@serna.alberto.eng)).
Articles live here so they stay **aligned with the implementation**: each
draft carries front-matter (`sources`, `figures`, `medium_url`,
`synced_at_commit`) and a provenance footer that is **dropped when pasting
to Medium**. Bodies are Medium-pastable — **PNG figures only** (one animated GIF in Part 4; Medium
cannot render mermaid; drawio sources + exported PNGs follow the
`visual-first-documentation` skill's convention), reusing
[`docs/designs/assets/`](../designs/assets/) wherever a figure already
exists.

The series is also the discussion channel: every article ends with an
invitation for feedback, corrections, and experiment ideas — a knowledge
base for future work (vLLM tweaks, fraud/anomaly-detection pipelines on
Dataflow, related libraries).

**Status legend:** `Draft` (body in this repo, not yet published) ·
`Published` (live on Medium; `medium_url` set) · `Needs-sync` (implementation
moved since `synced_at_commit`; re-verify before re-publishing) · `Stub`
(scope reserved, no body yet).

| # | Title / file | Scope | Sources | Figures | Status |
|---|---|---|---|---|---|
| 1 | [LLM and statistical synthetic data with Dataflow — intro](01-building-banking-synthetic-data-intro.md) | The problem and constraints (privacy, no egress, self-hosted LLMs), Beam Summit 2025 framing, rapid-fire Q&A hook (LinkedIn-reusable), high-level architecture + three-lines-of-defense guardrail HLA, the two engines at a glance, the `generation_plan` per-type tour incl. the `route:"llm"` override, the flagship 0/10M-orphans teaser, glossary foundation (~12 terms) | `README.md`, `DDL_CONTRACT_GUIDE.md`, ADR 0013/0014/0022/0024/0028–0033 | `architecture-overview.png`, `validation-guardrails.png`, `generation-plan-routing.png`, `stats-inverse-cdf.png`, `fk-orphan-rate.png` | Draft |
| 2 | [Type system & freetext resolution](02-type-system-freetext-resolution.md) | The profiler's decision order and thresholds; the 10k reference sample (scope, DKW arithmetic, knobs and their aftermath); the five exits before the LLM (Tier P pattern sampler, Tier B byte template, identifier masks, shape expansion, Tier L ladder); `llm_prompt_constraint` keys + `route:"llm"`; the pool ladder (target sizing, gates in order, escalation, failovers, `strict_freetext`); vLLM calls at a high level (guided JSON, prefix caching, KV-budget wait); RAG interplay (seeds only); the `freetext_pools` store + taint preflight; `b2_library`'s per-batch hook | ADR 0005/0013/0019/0020/0022/0023/0024/0026/0028/0033/0034, `DDL_CONTRACT_GUIDE.md`, designs 2026-07-24 + 2026-08-05 + 2026-08-10 + 2026-08-22 + 2026-08-29 | new drawio: `freetext-resolution-flow`, `freetext-pool-ladder`, `freetext-components`; reused: `generation-plan-routing`, `sampling-error-dkw`, `constraint-router-*`, `r6-scale-*`, `prompt-constraints-*` | Draft |
| 3 | [Common runtime — CPU/GPU split, vLLM serving](03-common-runtime-cpu-gpu-vllm.md) | One worker pool, split by stage (fleet + two sequence diagrams); vLLM in-worker spawn/ownership (lazy ignition, process + port mutex, VRAM-derived budget and `max-model-len` clamp, unfittable wait, lost-race adoption, refcount / kept-alive); prefill vs decode, prefix-cache hit rate, KV usage and tokens/s measured on a T4; PagedAttention (used, not tuned); the guardrails track (decoder rail + rejection-set rail, nine stations); the GIL ceiling and `sdk_containers=single\|multi`; `initial_workers` + `autoscaling=fixed`; uniqueness modes, DLQ pattern, `FILE_LOADS`; accelerator/model/dtype matrix; optimizations assessed (LMCache, TurboQuant, MPS, AWQ, n-gram spec-decode, FP8 KV); SLO proposal; FDE checklist | ADR 0014/0018/0019/0020/0028/0030/0033/0034, `RUN_PLAYBOOK.md` §1/§3/§6, designs ws5/ws6/2026-08-22/2026-09-07, `config/models.yml`, `vllm_client.py` | new drawio: `runtime-fleet-topology`, `runtime-cpu-gpu-sequence`, `vllm-gpu-sequence`, `vllm-guardrails-track`, `vllm-optimization-map`; new generated: `vllm-serving-{engine-stats,ignition,gpu-minutes,kv-budget}` (`make_vllm_serving_figures.py`); reused: `throughput-*`, `sdk-containers-topology`, `ws5-cost-anatomy`, `ws6-run-timeline` | Draft |
| 4 | [`b1_rag` — retrieval that seeds the prompt and never touches a row](04-b1-rag-deep-dive.md) | Why a RAG engine for tabular free-text; where retrieval sits in the Beam DAG (population / pool / generate branches, side-input barriers); chunk kinds + GReaT serialization (drawn clause by clause) + byte-level identity (digests, chunk ids); the embedder (bge-small, device demotion, the laptop `HashingEmbedder`); FAISS `IndexFlatIP` as data (persisted vectors vs rebuilt index) and its alternatives; the three `--pool_seed_strategy` values next to random / query top-k / MMR; seed budget; augmentation + ladder recap; fidelity vs originality (exact-match wall, head values, tail skew); "PES mode" — the same path on a column of names (categorical-until-routed, seeds per strategy on a labelled map, renames — and fully Irish names — that pass the wall, the whole path animated on a globe); `llm_prompt_constraint` × retrieval; why not `apache_beam.ml.rag`; the engine attach/detach seam (shared with Part 5). Data examples = output of `scripts/doc/b1_rag_walkthrough.py` | ADR 0005/0013/0017/0018/0019/0020/0023/0024/0028/0031/0034, designs 2026-07-07 rag-layer + 2026-07-25 retrieval geometry + 2026-07-26 ws5, `sdfb_core/rag/*`, `engines/b1_rag/engine.py` | new drawio: `rag-end-to-end-flow`, `rag-faiss-data-path`, `rag-chunk-identity`, `engine-attach-detach`; new generated: `rag-great-serialization`, `rag-dense-vectors-3d`, `rag-retrieval-sphere.gif`, `rag-seed-pickers`, `rag-seed-budget`, `rag-fidelity-originality`, `rag-names-pes-map`, `rag-names-pes-3d.gif`, `rag-setup-cost`; reused: `embedding-geometry-topk`, `prefix-vs-kcenter-coverage`, `centroid-vs-perquery`, `embed-cost-evolution`, `freetext-pool-ladder` | Draft |
| 5 | b2_library deep dive | Wrapping `sdgx` (CTGAN + empirical fallback), fit-once-per-worker, LLM freetext patching, parity seams with b1 (source-value store, inverse-CDF) | ADR 0013/0022/0025, B.2 engine code | `marginal-wave3-*` figures | Stub |
| 6 | Relational generation by construction | The flagship: relationships as config, launch scenarios, single-job parents-first propagation, joint FK tuples + IPF, orphan BLOCKER, pool-ladder integrity — 0/10M orphans measured | ADR 0028–0033, designs 2026-08-22/23/24/29, `config/relationships/README.md` | `fk-orphan-rate.png`, `fk-feasible-set.png`, `fk-marginal-fit.png`, `relationships-*.png`, `r6-scale-*.png` | Stub |
| 7 | Integration testing with GH agents | The report-generation prompts as agents; metrics contracts (`gcp`/`offline`/`stats_diff`/`crosscheck`), `_full_report.md`, bundle redaction (`real/` vs `oss/`), evidence promotion to releases | `.github/prompts/*`, `RUN_PLAYBOOK.md` §5/§8, `E2E_TEST_MATRIX.md`, release tooling | `wave4-verified.png` + report screenshots (to make) | Stub |
| 8 | Stats, stress & scale | The fidelity math (entropy, deciles, inverse CDF, DKW, HLL++, null patterns), 1M/10M performance anatomy, pool persistence economics (19.1 GPU-hours), warm vs cold | ADR 0022, design 2026-08-05 source-table-stats, release reports | `stats-*.png`, `sampling-error-dkw.png`, `ws5-cost-anatomy.png`, `r6-scale-where-time-went.png` | Stub |
| 9 | CI/CD & infra | Flex templates, single-image strategy, Artifact Registry runtime pulls (and why a third-party registry cannot serve workers), uv workspace builds, dtype map (bf16/fp16 × T4/L4), G-machine matrix, driver pins, release Action | ADR 0008/0009/0015/0016, `public_cloud/deploy/gcp/`, `docker/Dockerfile` | new CI/CD drawio | Stub |
| 10 | Evaluation framework | Tier-1/2/3 metrics, DCR/NNDR, SDMetrics scoring, DirectRunner harness — **deferred until `ws3-eval-framework` merges** | design 2026-07-07 evaluation framework, branch code | `eval-ks-vs-wasserstein.png`, `eval-dcr-nndr.png` | Stub (deferred) |

## Workflow

1. Draft/update the article here; keep `sources` + `figures` front-matter
   honest and set `synced_at_commit` to the commit the claims were verified
   against.
2. Paste the body to Medium (drop the front-matter and the provenance
   footer; upload the referenced PNGs).
3. Set `medium_url` + status `Published` in the front-matter and this table.
4. When implementation moves under an article (an ADR supersedes a cited
   decision, a flag changes), flip its status to `Needs-sync` — the release
   checklist is the natural moment to sweep this table.
