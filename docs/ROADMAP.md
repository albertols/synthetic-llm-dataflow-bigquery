# synthetic-llm-dataflow-bigquery — roadmap

Single source of truth for milestone scope. Locked decisions per milestone live as ADRs in [`adr/`](adr/); operational runbooks live in this directory's other docs; shipped-version history lives in [`releases/`](releases/README.md).

## M1 — laptop-spine + GPU + first Dataflow E2E ✅ shipped

**Goal**: produce schema-conformant synthetic rows for one BigQuery table on real Dataflow with GPU workers, validated in-pipeline (Mode A).

| # | Task | Status | Where |
|---|---|---|---|
| 1 | Bootstrap workspace + harness | ✅ done | laptop |
| 2 | Pydantic contracts + Pandera / BQ-DDL codegen | ✅ done | laptop |
| 3 | DDL extractor `sdfb_beam/ddl/` | ✅ done | laptop |
| 4 | `FakeModelClient` + fixtures | ✅ done | laptop |
| 5 | `GenerationEngine` ABC + 5 contract tests | ✅ done | laptop |
| 6 | B.2 library-wrapper engine (sdgx; bake-off deferred, ADR 0013) | ✅ done | laptop |
| 7 | B.1 RAG engine (FAISS retrieval + distribution inference) | ✅ done | laptop |
| 8 | Beam DAG end-to-end on DirectRunner + `FakeModelClient` | ✅ done | laptop |
| 9 | vLLM `ModelHandler` + `ModelClient` | ✅ done | laptop + Dataflow |
| 10 | `docker/Dockerfile` + CI workflows | ✅ done | CI |
| 11 | E2E Dataflow runs on GPU workers | ✅ done | R-series campaigns on T4 (Qwen; Gemma needs L4 — see `RUN_PLAYBOOK.md` §1), 1M and 10M rows/table measured; evidence in [`releases/`](releases/README.md) |
| 12 | `thresholds.yml` wiring + `validation_runs` BQ table | ✅ done | verified on real runs |

The personal-GCP T4 E2E layer (`public_cloud/deploy/gcp`, ADR 0016) is what executed the run matrix without a corporate landing zone.

**Hard constraints (immutable)** — see [`adr/0001-no-managed-gcp-services.md`](adr/0001-no-managed-gcp-services.md):
- No Vertex AI, Dataplex, Looker.
- No HuggingFace Hub at runtime.
- No external LLM APIs (GPT / Claude / Grok / Deepseek).

(The original "single-table only" M1 constraint was retired when relational generation shipped in v0.1.0.)

History note: the 2026-07 engine-fix/E2E-tooling cycle and the 2026-08 fidelity/relational waves are recorded where they belong — ADRs 0017–0033, the design docs under [`designs/`](designs/), and the release reports under [`releases/`](releases/README.md); this file no longer narrates them.

## M2 — validation breadth + scale

**Goal**: production-grade validation, broader use cases, foundations for managed adoption.

**Shipped early (v0.1.0, 2026-08)** — most of the original multi-table theme landed ahead of schedule: relationships as versioned config ([ADR 0032](adr/0032-relationships-as-config.md), superseding the description contract of [ADR 0021](adr/0021-relational-contract-in-descriptions.md)), minimal-input launch scenarios ([ADR 0029](adr/0029-fk-model-scenarios-and-history-mappings.md)), one Dataflow job for the whole model ([ADR 0030](adr/0030-single-job-relational-generation.md)), referential integrity by construction — joint key tuples, IPF-fitted weights, `fk.orphan` BLOCKER gate ([ADR 0031](adr/0031-joint-fk-key-draws.md)) — plus pool-ladder integrity at 10M scale ([ADR 0033](adr/0033-pool-ladder-integrity-at-scale.md)). R6 acceptance pair: PK 1.0, **0 orphans on 10M child rows**, `copy_fraction` 0.

**Shipped next (v0.3.0, 2026-09)** — the relational follow-through became its own wave: a PK that contains an FK cannot be drawn at random ([ADR 0035](adr/0035-pk-capacity-fk-bound-members.md)), so a child is generated FROM its parent's landed keys with the source's measured fan-out — ratio, key uniqueness and referential integrity by construction ([ADR 0036](adr/0036-parent-driven-fanout-generation.md)); a child may have several parents, each enforced edge taking one of five roles and its own DAG path, so star, diamond, tree, chain, forest and grandparent shapes all generate from one model ([ADR 0037](adr/0037-multi-parent-children.md)); and a declared `pk:` the full source disproves adjusts the effective model with a loud banner instead of refusing the launch ([ADR 0038](adr/0038-measured-conflicts-adjust-the-model.md)). Laptop-proven (unit + DirectRunner); **Dataflow acceptance for this path is still open** — it is the open box in ADRs 0036–0038.

**Remaining themes**:
- **Relational follow-through** — the M4 Dataflow acceptance launch for ADRs 0036–0038; transcribe the corp model into `config/relationships/`; level-parallel scheduling; ADR 0033's next cold-launch gate; CPU/GPU worker split for the generate stage (10M: ~9 busy GPU-minutes of 272 billed). The co-partitioned join for parents beyond the 100k key cap is no longer the plan for a **driven** child — the fan-out keys it instead (ADR 0036), and ADR 0037 uses a co-partitioned `CoGroupByKey` for conditional edges; the join survives only as the documented escape hatch for a shape the fan-out cannot key.
- **Evaluation framework merge** — Tier 1/2/3 metrics (`ws3-eval-framework` branch): KS/Wasserstein, TV, PSI/JSD, DCR/NNDR, SDMetrics reports, memorization identifiers — into the run contract.
- **Mode B validation pipeline** — GX 1.x Checkpoint + Soda Core scan + SDMetrics fidelity + Evidently drift report; results to `synthetic_data_quality.*`, artifacts to GCS.
- **Constrained-decoding fallback chain** — `outlines` / `lm-format-enforcer` for schema edge cases that beat vLLM's guided JSON.
- **Reference snapshot pattern** — cached parquet under `gs://{project}-dataflow/reference/{table}/sample.parquet` as a deterministic alternative to live SELECT.
- **PII allow-list** — mask / format-template sensitive columns before they touch embeddings or validation reports.
- **B.2 routing parity** — B.2 through the relational/constraint seams B.1 already has; head-to-head fidelity numbers to finalize the library lock.
- **Apple Silicon MLX `ModelClient`** — drop-in for `VLLMModelClient` so M4-local runs become a full alternative to the FakeClient.
- **CI gating** — Mode B checkpoint blocks PR merges; thresholds.yml is the source of truth.

**Explicit non-goals for M2**:
- Dataplex / Looker dashboards (ADR-0001 still applies).
- OpenLineage / Marquez / Dagster.
- External LLM APIs.

## M3 — production hardening

**Goal**: operations team can adopt without us.

- **IaC / Terraform** — buckets, datasets, secrets, IAM, AR repos.
- **Run metadata schema v2** — full audit trail, signed-URL report links, OpenLineage-compatible structure (without taking the OpenLineage dependency).
- **Multi-region** — at least `us-central1` parity with `europe-west3`.
- **Quota-aware autoscaling** — pipeline reads quota before requesting workers, fails gracefully.
- **B.1 / B.2 head-to-head report** — fidelity (SDMetrics), cost-per-1k-rows, throughput, suitability matrix per column shape.
- **B.3 candidates** — diffusion-based tabular or GAN-only engines if research warrants.
- **Documentation site** — render `docs/` as a static site (mdBook / Docusaurus); not in lieu of the markdown source.

## How milestones evolve

- Add new tasks to the relevant milestone's table; never silently move tasks between milestones.
- Add new locked decisions as ADRs under [`adr/`](adr/); don't sneak them into a memory file or a skill.
- When an "explicit non-goal" feels like a goal, open a discussion before changing the roadmap — and update the relevant ADR.
