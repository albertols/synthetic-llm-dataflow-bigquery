# Changelog

What changed in each tagged release, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) shape. Versions follow [SemVer](https://semver.org/spec/v2.0.0.html), minted from the squash-merge title by [`release_tag_report.yaml`](.github/workflows/release_tag_report.yaml).

**This file is the source of the GitHub Release notes.** At tag time the Action promotes `[Unreleased]` to a dated section with [`scripts/release/make_release_notes.py`](scripts/release/make_release_notes.py) and publishes that exact text — so the page and this file cannot drift. Write bullets under `[Unreleased]` as work lands; categories left empty are dropped on promotion, and repo-relative links are pinned at the tag when published.

Measured numbers behind these releases live in [`docs/releases/`](docs/releases/README.md) (one deterministic before/after report per version); decisions live in [`docs/adr/`](docs/adr/).

## [Unreleased]

### 🚀 Added

### 🔧 Changed

### ⚡ Performance

### 🐛 Fixed

### 🗑️ Removed

### 📗 Docs

## [v0.5.1] — 2026-09-15

### 🚀 Added
- DSG Terraform can launch the generation job (`launch_job = true`, `google_dataflow_flex_template_job`) with the same parameters as `scripts/04_run_dataflow.sh`; `terraform test` fails if the two drift apart.
- DSG Terraform `gpu` variable: `l4` (G2 machines, `vllm_dtype=auto`, both models) or `t4` (N1 machines, `vllm_dtype=float16`, `qwen3-4b` only). It sets the machine type, the accelerator and the dtype for both launches, and plan fails on Gemma with a T4 or on a machine type from the other family. Replaces the `accelerator` variable.
- Model staging downloads from Hugging Face (default) or its ModelScope mirror, with no credentials: Gemma 4 E4B-it, Qwen3-4B-Instruct-2507 and bge-small are public, ungated repositories. `config/models.yml` records each `hf_repo`.

### 🔧 Changed
- Each DSG sync publishes on its own branch, `sync/synthetic-llm-dataflow-bigquery-<ref>`, and closes the older open sync PRs from the fork (with a "Superseded by" comment) and deletes their branches, so one PR stays under review. `dsg/manifest.yaml` takes `branch_prefix` instead of `branch`.
- The public FK example is `config/relationships/gcp_public_fk_example.yaml`. Relationship files named `*_example.yaml` are samples, skipped by directory scans like `example_*.yaml`.
- The DSG launch writes into existing tables (`create_if_not_exists=false`) and uses the RAG, free-text pool and source-stats stores.

### 🐛 Fixed
- DSG Terraform creates every table in `config/bq_schema` with its dataset, name and schema (the `synthetic_rag` tables were missing). The thelook source snapshots keep the public schemas, with GEOGRAPHY values nulled instead of the column dropped, and the landing tables in `synthetic_data` are created `LIKE` the public tables.

### 🗑️ Removed
- Kaggle credentials and Secret Manager from the DSG deployment.

### 📗 Docs
- DSG Terraform README: which tables come from `bigquery-public-data.thelook_ecommerce` and how, the FK model as a diagram, the configuration files the job reads, and how open-weight models reach the workers.

## [v0.5.0] — 2026-09-15

### 🚀 Added
- `dsg/manifest.yaml` `patches`: exact line replacements in DSG files outside the owned paths, each with a reason, listed in the PR body under "Also in this PR". They are a no-op once the fix lands upstream. The first patch fixes pipeline detection in the DSG workflow, which fails with SIGPIPE on large pipelines.

## [v0.4.2] — 2026-09-15

### 🔧 Changed
- CI and the DSG sync gate run pylint on Python 3.14, the interpreter the Dataflow Solution Guides CI uses; findings differ from Python 3.11.

### 🐛 Fixed
- Pylint on Python 3.14 is clean. Modules whose f-strings keep single quotes for Python 3.11 compatibility disable `inconsistent-quotes`, with the reason; two unused-argument and one argument-name findings are fixed.
- `scripts/dsg/sync.py` finds `## [vX.Y.Z]` changelog sections for the PR body, and stops before the long gates when `gh`, `terraform`, `pipenv` or `uv` is missing from PATH.

## [v0.4.1] — 2026-09-14

### 🔧 Changed
- DSG sync commits carry only the maintainer's git identity: `scripts/dsg/sync.py` no longer accepts `--trailer`, and the `dsg-sync` skill says so and discards the dry-run state before publishing.
- Every DSG sync uses one branch, `sync/synthetic-llm-dataflow-bigquery`, so a re-sync updates the open DSG PR instead of opening another.

### 🐛 Fixed
- `scripts/dsg/sync.py` commit no longer fails when an owned path does not exist in the DSG checkout.

### 📗 Docs
- README status and "Measured on Dataflow" sections rewritten for the current state: the five-table relational launch `2026-09-13_06_10_16-12600311608685394436` succeeded on every table. ADR 0036's status now records that acceptance.

## [v0.4.0] — 2026-09-14

### 🚀 Added
- Dataflow Solution Guides donation ([ADR 0040](docs/adr/0040-dsg-donation-golden-source-sync.md)): `dsg/` overlay (launch scripts `01`–`05`, `setup.py`, Cloud Build, Terraform module, use-case page) and `scripts/dsg/sync.py`, which replicates a tagged release into the guide and opens the PR behind DSG-equivalent gates; `/dsg-sync` skill and command.
- Public relational demo model `config/relationships/gcp_public/gcp-public-relationship.yaml` over `bigquery-public-data.thelook_ecommerce` (users → orders → order_items, products as an external catalog parent).

## [v0.3.3] — 2026-09-14

## [v0.3.2] — 2026-09-14

### 🚀 Added
- Sensitive-content gate `scripts/dsg/precheck.py` in CI: forbidden paths, secret patterns, card numbers, IBANs, non-placeholder e-mails and salted-hash matches of known sensitive identifiers.

### 🔧 Changed
- The image builds from public base images and PyPI; the private package-index and registry settings are gone from `pyproject.toml`, `docker/Dockerfile` and the Cloud Build config.
- The Composer DAG takes optional network tags from the `DATAFLOW_NETWORK_TAGS` Variable instead of a hard-coded chain.
- Docs, ADRs and examples no longer describe a specific organisation's environment; worked examples use a fictitious web shop.

### 🗑️ Removed
- `docs/CICD.md`, `scripts/probe_gpu_dataflow.sh`, the v0.1.0 evidence bundle and the internal planning notes; ADRs 0003 and 0012 are withdrawn (0012's GCS-client and driver decisions still hold).

## [v0.3.1] — 2026-09-14

### 🚀 Added
- **Every tag gets published release notes, from this file.** The release Action promotes the `[Unreleased]` block to a dated section and publishes that exact text as the tag's GitHub Release, so the changelog and the releases page cannot drift. Repo-relative links are pinned at the tag when published; a block nobody filled in falls back to the commit subjects, so a release is never published noteless ([`scripts/release/make_release_notes.py`](scripts/release/make_release_notes.py)).
- README badge row: CI, latest release, licence, Python version, Apache Beam / Dataflow, self-hosted vLLM, Beam Summit 2025.

### 📗 Docs
- Curated notes backfilled for v0.1.0 … v0.3.0, which previously showed as bare tags.

## [v0.3.0] — 2026-09-13

Relational generation by construction: a child is no longer a table that *happens* to hold valid foreign keys, it is generated *from* its parents' keys.

### 🚀 Added
- **Parent-driven fan-out.** Children are generated from their parent's landed keys: per key the engine draws the fan-out from the source histogram, draws the PK-completing categorical cells without replacement, copies inherited columns, and fills the rest through the existing samplers. Ratio, PK uniqueness and referential integrity now hold *by construction* ([ADR 0036](docs/adr/0036-parent-driven-fanout-generation.md)).
- **Multi-parent children.** A driven child may carry any number of independent edges (star-schema dimensions, served by a side-input key pool) and conditional edges (diamond branches, served by a co-partitioned `CoGroupByKey` on the shared columns). Star, diamond, tree, forest, 1:1 chain and arbitrary FK DAGs are all satisfiable; the only remaining launch stop in role derivation is two edges both marked `drives: true` ([ADR 0037](docs/adr/0037-multi-parent-children.md)).
- **The launch adjusts a model the source disproves.** Measuring the source can show the declared relationship model wrong; the launch widens the edge (`fk_edge_widened`), says so, and carries on instead of failing or silently obeying ([ADR 0038](docs/adr/0038-measured-conflicts-adjust-the-model.md)). Row projection happens before the graph is walked ([ADR 0039](docs/adr/0039-row-projection-before-the-graph.md)).
- **PK-capacity preflight that understands FK and categorical members.** An enforced FK inside a primary key counts once as the keys the child will see; unconstrained categoricals count their observed domain; random-draw tuples are judged against the run's BLOCKER gate, and the stop message names the largest gate-safe `--num_rows` ([ADR 0035](docs/adr/0035-pk-capacity-fk-bound-members.md)).
- New flags `--fk_candidate_cap`, `--driven_uniqueness_mode`, `--fk_fanout_stats_table`; new fan-out measurement cache `synthetic_data_quality.fk_fanout_stats`; `config/relationships/example_star_diamond.yaml`.
- `/e2e_fk_pk_validator` prompt — registry-derived contract plus read-only `bq` PK-duplicate, orphan and fan-out checks cross-read against `validation_runs`.

### 🔧 Changed
- `uniqueness_mode=streaming` (the default for driven children) now also measures `pk.duplicate`, so a driven child's PK guarantee is a measured fact rather than an argument.
- A child column may be written by exactly one edge. Two in-model edges that both own a column stop the launch and name the remedies; the same overlap between *external* edges only warns, since an external parent offers no remedy.
- Edge roles are derived rather than declared: with no `drives:`, the most-derived candidate parent drives and its edge is widened with the column pairs the child pins — no model edit needed to add a table.

### 🐛 Fixed
- Three false preflight stops: an FK member whose parent is disabled; `1 − exp(−x)` cancelling to a reported 100% duplicate share on a 3.6e27 pattern space; a true 1:1 child whose primary key *is* its driving edge.
- Per-key cell draws are O(k) on precomputed cumulative weights — 746 → 3.2 µs/key at 10k cells.

### 🗑️ Removed
- The 100k/1M FK side-input key cap and the in-DAG FK gate, for in-job driven edges. Both were workarounds for sampling keys a child cannot key by drawing; fan-out generation makes them unnecessary.

## [v0.2.1] — 2026-09-09

### 📗 Docs
- **Article 2 — *Type system & freetext resolution*.** The profiler's exact decision order and thresholds; the five exits a `STRING` column takes before any LLM call (pattern sampler, byte template, identifier masks, shape expansion, the Tier-L ladder); `llm_prompt_constraint` keys and what each drives; the free-text pool ladder with its five gates, escalation and failovers; how vLLM is called (guided JSON, no seed with `n > 1`, prefix caching, the KV-budget wait); and precisely where RAG does and does not participate. 13 figures, three of them new.

## [v0.2.0] — 2026-09-08

Throughput release. Same job shape (10M rows per table, one parent + one FK child, T4 workers), **93.8 min → 50.5 min** — see [ADR 0034](docs/adr/0034-generation-throughput-single-barrier-shared-engines.md).

### ⚡ Performance
- **Multi-process SDK containers** (`sdk_containers=multi`): generation was GIL-bound on one interpreter per worker with eight harness threads. A loopback-port spawn mutex lets one vLLM server per worker serve every SDK process — a 10k-row batch went from 26–29 s to **5.6–6.7 s**.
- **One dedup barrier instead of three** (`uniqueness_mode=exact`): PK/identity resolved from key-only collision groups as side inputs, rows packed as value tuples. Dedup + load per table went from 14.0 / 12.2 min to **3.4 / 3.0 min**, with identical DLQ envelopes and exact counts.
- **Process-shared engine registry**: 32 engine builds per table became one per process. The embedder now loads lazily, so a store-warm setup never opens a CUDA context beside vLLM.
- **Storage Read API for source domains**: a 944k-value identifier domain was being paged through the BigQuery REST iterator inside `DoFn.setup()` and read as a 5.5-min "generation stall".

### 🚀 Added
- `initial_workers`, `autoscaling=auto|throughput|fixed`, `sdk_containers=single|multi`, `rag_embed_shards` — template and DAG parameters, with run tiers `R7`/`R7m`.

### 🐛 Fixed
- The Storage Read API fallback re-iterated a consumed iterator, so on two acceptance runs *every* source-domain fetch failed and the pool rejection sets, identifier/numeric domains and pool-target sizing were silently inactive. The fallback now takes a fresh `QueryJob.result()`, with a per-process kill switch logged once.
- Fixed-width ceiling for mask-gated columns, which had let a 48-char value past a 35-char source ceiling.

### 🔧 Changed
- The worker service account now needs `roles/bigquery.readSessionUser`; without it each process pays one denied Storage attempt and falls back to REST.
- CPU population embeds under `multi` were **reverted** to the GPU (bounded to `rag_embed_shards`, with the pool branch waiting on the population): they had starved the model pull and vLLM init and turned a 2.9-min stage into 15.4 min.

## [v0.1.1] — 2026-09-01

### 📗 Docs
- README rebuilt as the single entry point — architecture figure, relational PK/FK section, grep-verified glossary, measured claims only.
- `docs/articles/` added: a 10-slot series index plus Article 1 in full.
- `RUN_PLAYBOOK_WS8.md` folded into [`RUN_PLAYBOOK.md`](docs/RUN_PLAYBOOK.md); seven design-doc status banners corrected; `docs/superpowers/plans/` retired.

### 🔧 Changed
- Evidence bundles live at `docs/releases/<version>/evidence/`; the release report's artifact discovery reads that layout, keeping the legacy `integration_test/` path discoverable for older tags.
- Local scratch renamed `integration_tests/` → `runs/` across scripts, prompts, agents and skills (203 references).

## [v0.1.0] — 2026-08-28

First tagged release. M1 complete end to end — laptop and Dataflow — with relational generation delivered ahead of plan.

### 🚀 Added
- **Two interchangeable engines** behind one `GenerationEngine` interface: `b1_rag` (embed → exact vector index → retrieve exemplars → LLM infers per-column pools **once** → bulk rows sampled vectorized) and `b2_library` (wraps `sdgx`, LLM patches free-text only). The LLM runs O(1) times per run, never per row ([ADR 0013](docs/adr/0013-distribution-estimator-spine.md)).
- **Self-hosted inference inside the DAG**: vLLM spawned in-worker on L4/T4 GPUs via a custom `ModelHandler`, weights warm-pulled from GCS. No Vertex AI, no model hub at runtime, no external LLM API, no data egress ([ADR 0011](docs/adr/0011-adopt-beam-vllm-model-handler.md), [ADR 0014](docs/adr/0014-vllm-model-client-owns-server.md)).
- **Relational generation**: PK/FK declared in versioned [`config/relationships/*.yaml`](config/relationships/README.md) ([ADR 0032](docs/adr/0032-relationships-as-config.md)), one job per connected component with parent keys reaching children as side inputs ([ADR 0030](docs/adr/0030-single-job-relational-generation.md)), and joint FK key tuples with IPF-fitted weights ([ADR 0031](docs/adr/0031-joint-fk-key-draws.md)) — measured at 0 orphans in 10M rows.
- **Fidelity by construction**: stats-driven generation ([ADR 0022](docs/adr/0022-stats-driven-generation.md)), inverse-CDF numerics, empirical categoricals, positional alphabets ([ADR 0025](docs/adr/0025-marginal-fidelity-by-construction.md)), structured prompt-constraint templates ([ADR 0024](docs/adr/0024-structured-prompt-constraint-templates.md)) and source-domain pool rejection ([ADR 0023](docs/adr/0023-source-domain-pool-rejection.md)).
- **Validation and audit**: three lines of defense with tagged DLQ routing, memorization measured and gated on every run, and per-run quality records in `synthetic_data_quality.*`.
- **Persisted free-text pools** keyed by `(reference_digest, model_uri, column, target)` — a 1M-row run without them rebuilt pools 36×, ≈19.1 GPU-hours of duplicated LLM time ([ADR 0020](docs/adr/0020-freetext-pools-as-persisted-artifact.md)).
- **Deployment layer**: CI-driven image and flex-template builds ([ADR 0008](docs/adr/0008-ci-driven-builds.md)), a personal-GCP E2E campaign layer with a hard budget cap and billing killswitch ([ADR 0016](docs/adr/0016-personal-gcp-cloud-build.md)), the DDL extractor, and the preflight prerequisites checker.
