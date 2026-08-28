# Release v0.1.0

**Date:** 2026-08-28

## Change summary

### Features
- feat(ws8): relational generation by construction — relationships as config, joint FK draws, prompt constraints, marginal fidelity, ladder integrity (ADR 0022–0033)
- feat(preflight): step 10 free-text pool store + move schema to config/bq_schema (#10)
- feat: distinct/prompt_echoes yield diagnostics (echo vs saturated keyspace)
- feat: personal-GCP agents (deploy/interpret/audit) + ops/run/cost skills
- feat: teardown script (reverse-order cleanup, --full project delete)
- feat: run_e2e.sh — tier submit/poll/journal with EXPECT semantics
- feat: tiers.yaml run matrix + renderer with template drift guard
- feat: Cloud Build image + flex-template build scripts (mainline Dockerfile reuse)
- feat: model staging via Cloud Build (ModelScope+Kaggle -> GCS, manifest-gated)
- feat: budget + killswitch deploy script (hard cost cap at 90%)
- feat: billing killswitch function (budget alert -> detach billing)
- feat: snapshot public tables, extract DDL, create landing tables
- feat: personal-GCP storage/BQ script (buckets, datasets, DQ tables, bucket IAM)
- feat: personal-GCP IAM script (worker/build/killswitch SAs, minimal roles)
- feat: personal-GCP bootstrap script (project, billing, APIs, GAR keep-1)
- feat: scaffold personal-GCP E2E layer (env manifest + dry-run harness)
- feat: B.1 setup phase milestones — embed/index/pool timings to localize the 22-min stall
- feat: enforce pk.duplicate — PK dedup stage, PipelineConfig.pk_columns, --pk_cols, DAG param
- feat: strict_freetext fails loudly on LLM errors for vllm runs; engine_failure gates
- feat: Dataflow jobName carries a model slug derived from SDFB_MODEL_URI
- feat: vllm_dtype override enables bf16 Qwen on T4; Qwen-aware preflight + registry
- feat: bundle export scans recursively and redacts worker-image URIs
- feat: analysis gains batch-size-aware run flags + normalized entropy
- feat: probe mines SDFB_MILESTONE contract, vllm failure lines, run-id filter, engine labels
- feat: vendor e2e validation/probe/bundle scripts + report prompt from M4
- feat: row.duplicate + identity.unique BLOCKER gate rules with DLQ diversion
- feat: milestone instrumentation for DoFn setup/batches and vLLM client
- feat: SDFB_MILESTONE structured log contract in sdfb-core

### Fixes
- fix: fill free-text pools to target, route shaped strings off the LLM (2026-07-17 b1+b2 runs)
- fix: serialize vLLM spawn, drop free-text exemplar folding, probe memorization flags (2026-07-16 b1+b2 runs)
- fix: B.1 pool = one values-array completion, not n=32 blind choices
- fix: unclamp top_p/top_k on pool retries (Qwen generation_config pin)
- fix: DAG requests no_use_multiple_sdk_containers for vllm GPU workers
- fix: pool temperature retries, embed cap, vLLM server reuse (2026-07-16 5.4h run)
- fix: generic sampler fidelity + novel-only free-text pools (2026-07-15 E2E remediation)
- fix: vLLM 0.24 structured outputs, loud empty-yield fallback, deterministic reference sampling
- fix: final-review wave -- job-name sanitization, cost-cap hardening, report-recipe alignment
- fix: executable bit on 05_stage_models.sh and teardown.sh
- fix: run_e2e poll survives transient describe failures
- fix: resolve extract_ddl.py output at its real deterministic path
- fix: plumb vllm_max_model_len (default 8192) so the KV cache fits the GPU
- fix: strict_freetext covers mlx; weight engine_failure by lost batch size in gate
- fix: salt run_id per trigger (no cross-run seed replay); expose explicit seed param
- fix: declare pk_cols in flex-template metadata; pk_cols validation + CLI regression tests
- fix: GenerateRecordsDoFn drives ModelClient setup/teardown lifecycle
- fix: e2e_gcp_probe SAFE_OFFSET/ratio semantics, milestone name validation, eval-design caveat
- fix: correct uniqueness gate blind spots around synthesized identity values
- fix: reach identity_cols from the production Flex Template launch path
- fix: GPU dtype guard inspects the served model dir, not local_model_dir
- fix: loud freetext LLM fallback + fatal bf16-on-Turing guard in vLLM client
- fix: synthesize identity columns per row; never sample from reference (privacy)
- fix: derive per-batch seed from (run_id, batch_id) when --seed absent

### Docs
- docs: visual-first documentation convention for design docs (#8)
- docs: gcp-e2e-run recipe nits — report placeholder, bq --max_rows/--project_id
- docs: ADR 0016 + personal-GCP README + index updates
- docs: implementation plan for personal-GCP E2E layer (14 tasks, TDD)
- docs: E2E integration test matrix - tiered run configs beyond R1'-R4'
- docs: RUN_PLAYBOOK pass criteria - Dataflow job state + valid_count shortfall
- docs: scope L4-stockout maxWorkers reasoning to R4'; tier-agnostic rationale for T4 runs
- docs: consistent angle-bracket placeholders in RUN_PLAYBOOK run matrix
- docs: fix backwards R'-run GPU-tier references in RUN_PLAYBOOK prose
- docs: post-remediation M4 run matrix R1'-R4' with strict pass criteria
- docs: report prompt must extract fallback error=, vLLM lifecycle milestones, B.1 phase timings
- docs: refresh e2e report prompt for milestone contract; cross-link playbook + specs
- docs: fix misattributed fidelity citation; pin DCR/NNDR to tree-based kNN
- docs: evaluation framework design spec (tiered metrics, validation_data_history)
- docs: RAG layer design spec (BQ rag_chunks + VECTOR_SEARCH, idempotent embed job)
- docs: fix RUN_PLAYBOOK machine-type guidance contradiction
- docs: RUN_PLAYBOOK — GPU verdict, run matrix, Dataflow options, report recipe
- docs: register T4-safe Qwen3-4B model; document t4/Gemma incompatibility

### Tests
- test: lock _is_sequential edge branches; tighten entropy assertion to 1.0
- test: uniqueness gate edge cases + uniqueness DLQ pipeline_step mapping

### Other
- WS6: pipeline shape — pool store gate, VRAM-fit ignition, uniqueness modes (E2E-measured) (#11)
- WS5: free-text pools as a persisted artifact, generation-stage perf, and the RAG seeding experiment (#9)
- WS2 Phase A: standalone RAG package, rag_chunks persistence, pool scaling + per-column retrieval (#4)
- WS1: fix b2_library memorization (cardinality caps + TEMPORAL jitter) and lazy vLLM ignition (#3)
- default=ENGINE_DEFAULT
- job_id and intergration/test/job_id dir
- last settings.local.json
- adds DEPLOYMENT_PREREQUISITES.md * 2
- adds deployment_prerequisites.py
- adds DEPLOYMENT_PREREQUISITES.md
- gang of 5 for embedders: config.json, model.safetensors,tokenizer.json,tokenizer_config.json, special_tokens_map.json
- fix for embders gs://uri with localize_gcs_prefix
- Welcome synth for DB infra


## Before / after

- **Base:** no integration run landed for this release.

Base job: `n/a` · Head job: `2026-08-26_05_01_16-3186876581127148459`

### Performance

| Metric | Base | Head | Delta |
|---|---|---|---|
| execution_seconds | not measured | 5574 | not measured |
| dominant_stage_seconds | not measured | 1442 | not measured |
| counter_yielded | not measured | 10000000 | not measured |
| counter_failed | not measured | not measured | not measured |

### vLLM

| Metric | Base | Head | Delta |
|---|---|---|---|
| vllm_ignition_seconds | not measured | 25.81 | not measured |
| tokens_per_s | not measured | not measured | not measured |

### Quality

| Metric | Base | Head | Delta |
|---|---|---|---|
| dup_ratio_max | not measured | 0 | not measured |
| top_value_share_max | not measured | 1 | not measured |
| shape_recall_min | not measured | 0 | not measured |
| shape_precision_min | not measured | 0 | not measured |
| copy_fraction_max | not measured | 0.0022 | not measured |
| entropy_gap_max | not measured | 0.0391 | not measured |
| decile_ks_max | not measured | 0.1436 | not measured |


## Charts

![Step time — before vs after](assets/step_time_before_after.png)

![Metric evolution across releases](assets/metric_evolution.png)

_Evolution chart spans 0 prior tagged release(s) plus this one (execution_seconds, lower is better)._

---
_Generated deterministically by `scripts/release/make_release_report.py` — insights layer: `.claude/skills/release-report/SKILL.md`._
