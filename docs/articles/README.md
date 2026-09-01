# Medium article series — index

The VCS-tracked sources for the Medium series on this project
([medium.com/@serna.alberto.eng](https://medium.com/@serna.alberto.eng)).
Articles live here so they stay **aligned with the implementation**: each
draft carries front-matter (`sources`, `figures`, `medium_url`,
`synced_at_commit`) and a provenance footer that is **dropped when pasting
to Medium**. Bodies are Medium-pastable — **PNG figures only** (Medium
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
| 2 | Type system & freetext resolution | The string router in depth; how freetext is resolved end-to-end: pool ladder, thresholds, format gates, failovers (shape fallback, binary fallback), prompt constraints (`route:llm`), RAG interplay, `strict_freetext` | ADR 0020/0023/0024/0026/0028/0033, `DDL_CONTRACT_GUIDE.md`, designs 2026-08-10 + 2026-08-29 | pool-ladder + router figures (`constraint-router-*`, `r6-scale-*`); new freetext-resolution drawio | Stub |
| 3 | Common runtime — CPU/GPU split, vLLM serving | One worker pool, split by stage; vLLM in-worker spawn/ownership (VRAM fit, refcount, lost-race adoption), uniqueness enforcement modes, DLQ pattern, capped autoscaling, FILE_LOADS | ADR 0014/0019, `RUN_PLAYBOOK.md` §3/§6, designs ws5/ws6 | `ws6-run-timeline.png`, `ws5-cost-anatomy.png` | Stub |
| 4 | b1_rag deep dive | Embedders (bge-small, device demotion), GReaT serialization, chunk identity, FAISS exact retrieval, centroid vs k-center geometry, byte-level decisions (digests, prefix caching) | ADR 0013/0018/0020, design 2026-07-25 retrieval geometry, `rag-layer-design` | `embedding-geometry-topk.png`, `prefix-vs-kcenter-coverage.png`, `centroid-vs-perquery.png` | Stub |
| 5 | b2_library deep dive | Wrapping `sdgx` (CTGAN + empirical fallback), fit-once-per-worker, LLM freetext patching, parity seams with b1 (source-value store, inverse-CDF) | ADR 0013/0022/0025, B.2 engine code | `marginal-wave3-*` figures | Stub |
| 6 | Relational generation by construction | The flagship: relationships as config, launch scenarios, single-job parents-first propagation, joint FK tuples + IPF, orphan BLOCKER, pool-ladder integrity — 0/10M orphans measured | ADR 0028–0033, designs 2026-08-22/23/24/29, `config/relationships/README.md` | `fk-orphan-rate.png`, `fk-feasible-set.png`, `fk-marginal-fit.png`, `relationships-*.png`, `r6-scale-*.png` | Stub |
| 7 | Integration testing with GH agents | The report-generation prompts as agents; metrics contracts (`gcp`/`offline`/`stats_diff`/`crosscheck`), `_full_report.md`, bundle redaction (`real/` vs `oss/`), evidence promotion to releases | `.github/prompts/*`, `RUN_PLAYBOOK.md` §5/§8, `E2E_TEST_MATRIX.md`, release tooling | `wave4-verified.png` + report screenshots (to make) | Stub |
| 8 | Stats, stress & scale | The fidelity math (entropy, deciles, inverse CDF, DKW, HLL++, null patterns), 1M/10M performance anatomy, pool persistence economics (19.1 GPU-hours), warm vs cold | ADR 0022, design 2026-08-05 source-table-stats, release reports | `stats-*.png`, `sampling-error-dkw.png`, `ws5-cost-anatomy.png`, `r6-scale-where-time-went.png` | Stub |
| 9 | CI/CD & infra | Flex templates, single-image strategy, GAR vs JFrog dual push, uv workspace builds, dtype map (bf16/fp16 × T4/L4), G-machine matrix, driver pins, release Action | ADR 0008/0009/0012/0015, `CICD.md`, `docker/Dockerfile` | new CI/CD drawio | Stub |
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
