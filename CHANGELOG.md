# Changelog

What changed in each tagged release, in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) shape. Versions follow [SemVer](https://semver.org/spec/v2.0.0.html), minted from the squash-merge title by [`release_tag_report.yaml`](.github/workflows/release_tag_report.yaml).

**This file is the source of the GitHub Release notes.** At tag time the Action promotes `[Unreleased]` to a dated section with [`scripts/release/make_release_notes.py`](scripts/release/make_release_notes.py) and publishes that exact text — so the page and this file cannot drift. Write bullets under `[Unreleased]` as work lands; categories left empty are dropped on promotion, and repo-relative links are pinned at the tag when published.

Measured numbers behind these releases live in [`docs/releases/`](docs/releases/README.md) (one deterministic before/after report per version); decisions live in [`docs/adr/`](docs/adr/).

## [Unreleased]

### 🚀 Added
- Planning says where its time goes: `sdfb-eval` (and the flex entry) print one line per planning step and table with its seconds (`planning <id> <table>: locate 0.42s`, then source pin, planning tables, planning statistics, scope verification, reference panel, sampling; dry runs and census for all tables; read-only parents) and a summary with the number of BigQuery statements by kind; the driver adds the target check, the launch lookup, the relationship models, the RUNNING row, the prepare statements and the submission. On a Flex Template launch everything before the Dataflow job exists runs on the launcher VM, which Google limits to 12 minutes including the image pull, so these lines are how a slow launch is diagnosed. Table names only: no SQL, no value.
- `sdfb-eval --seed_table T` evaluates what a launch of table T generated: T's enabled component in the relationship model (`--relationships_uri`), parents first, or T alone when no model names it, none is given, or the URI holds no model file (a warning says so). With `--relationships_uri` alone the evaluator names all the model's enabled tables. Each landing table is read whole (`scope=manual`; with `write_disposition` append that includes earlier launches' rows), the generation's `reference_rows_limit` and `validation_runs_table` are passed (new flags `--reference_rows_limit` and `--validation_runs_table`, both shared with the generator in the template): with the job's window, this launch's `validation_runs` rows (one query) verify the rebuilt reference sample, so the reference-based privacy metrics (the nearest-neighbour ones and the panel-based match rates and lifts) are evaluated; without the table or the window they are not evaluated, with the reason, a table that cannot be read is skipped with a warning and only a target with no readable table fails. The generation DAG's chained evaluation uses it: it passes `seed_table`, `relationships_uri` (empty when `generate_fk_relationships` is false), the landing dataset and a new `source_dataset` param and the generation job id as the launch's identity (`--generation_job_id` may accompany `--seed_table`: the evaluator reads the Dataflow job resource for its window, never the log or BigQuery job labels), so it reads no job log and its service account needs `roles/dataflow.viewer` (without it a warning and no window) but neither `roles/logging.viewer` nor `roles/bigquery.resourceViewer`; the row carries the job id and window and is listed in `evaluation_latest_per_job`. Cost: a model the launch adjusted is not seen, and the table list is the seed's; the window pins the source as of the job's create time ([ADR 0041](docs/adr/0041-evaluation-standalone-package.md), note of 2026-10-08).
- `packages/sdfb-evaluation` ([ADR 0041](docs/adr/0041-evaluation-standalone-package.md)): a standalone evaluator (its own lock and Python 3.11 pin, excluded from the uv workspace) that scores landed tables against the full source in a separate CPU job and writes `evaluation_data_history`, `evaluation_metrics`, `evaluation_profiles` and `evaluation_row_flags` plus two views to `synthetic_data_quality`. The `sdfb-eval` command (`plan`, `run`, `report`, `compare`, `catalogue`, `schemas`), a flex-template entry, a Composer DAG (`composer/evaluation_framework.py`, chaining from the generation DAG opt-in) and one metric catalogue as the single source of truth. A sampled run never stores a sample's number as exact: a row computed from a sampled side says `method = sample` with its rate, and a metric that needs every row of a side is `not_evaluated` with the reason. A profile's histogram edges are at least 10 source records apart and its quantiles at least 10 of that side's records apart. The registry holds one terminal row per evaluation, whoever writes it (the pipeline, the command or the Composer callback). Built and reviewed on a laptop with invented data; **not yet run on Google Cloud**.
- `.claude/skills/evaluation-framework/SKILL.md`: when to run the evaluator, reading baselines, noise floors and lifts, extending the catalogue.
- **One image, one template, one DAG import for generation and evaluation** ([ADR 0041, amendment of 2026-10-06](docs/adr/0041-evaluation-standalone-package.md#amendment-2026-10-06-one-image-one-template)). `docker/Dockerfile` also carries the evaluator's source (through a build stage that tolerates its absence, so a tree without `packages/sdfb-evaluation` still builds), on the generator's environment; a build step imports the evaluator's entry modules so a missing dependency fails the build. The template's entry is a dispatcher, [`docker/flex_entry.py`](docker/flex_entry.py): the template parameter `sdfb_job=evaluation` runs the evaluator, and a launch without it is a generation launch, as before. Not built or launched yet.
- The generation DAG evaluates its own run when `run_evaluation` is true: `start_sdfb >> run_evaluation_gate >> wait_for_generation >> trigger_evaluation`. The wait is a reschedule-mode sensor (no triggerer needed); the evaluation is a second, CPU-only launch of the same template. New params `evaluation_mode`, `evaluation_machine_type`, `evaluation_max_workers`, `evaluation_output_dataset`; no new marker and no new Airflow Variable. With `run_evaluation` false the DAG does what it did.
- The standalone evaluation DAG accepts `generation_job_ids`, a list: it starts one run of itself per job id (one Dataflow job each, one after another) and leaves the per-job path as it was.
- `disk_size_gb`, Beam's own worker boot disk, is a declared template parameter: an evaluation launch on the shared multi-GB image passes 200, the size the generator pins for itself.
- **Synthetic Platform**, a local-first GUI in [`gui/`](gui/README.md) ([ADR 0042](docs/adr/0042-self-hosted-platform-gui.md)): a React app and a Fastify backend bound to `127.0.0.1` that read and explain the project's data in four tabs. INTRO tours the pipeline; EVALUATION lists evaluations, drills into one run and compares runs; RAG shows the 384-d space, the embedder lab, the FAISS index, the seed strategies and the free-text pools; CONFIG holds the Pipeline amp (every knob with its value from the code), the scenario calculator, the source-stats explorer and the guardrails. It runs on a seeded mock with no GCP access, or reads `synthetic_data_quality.*` and `synthetic_rag.*` through named, read-only queries that are dry-run first and capped by `MAX_BYTES_BILLED` (10 GiB by default); fetched rows stay in memory.
- The GUI's types are generated from the Python side: the BigQuery schemas, the metric catalogue, [`scripts/gui/export_knobs.py`](scripts/gui/export_knobs.py) (knobs, the evaluator's `sdfb-eval` flags read from its CLI, sample relationship models, DLQ rules) and [`scripts/gui/export_golden_fixtures.py`](scripts/gui/export_golden_fixtures.py), whose golden files pin the TypeScript ports of the hashing embedder, GReaT serialization, retrieval, the evaluator's scorer and the run-to-run verdicts of `sdfb-eval compare` (the compare view reads "≈" by the same rule). `npm run contracts:check` and the exporters' `--check` fail CI on drift. A golden file is the same on every machine (its `python` stamp is the minor version, not the patch).
- [`.github/workflows/gui.yml`](.github/workflows/gui.yml): typecheck, lint, unit tests, build, bundle budgets, contract drift, the tab-ownership gate, and Playwright + axe at 1440 and 390 px, plus an `exports` job for the Python exporters.

### 🔧 Changed
- `docker/flex_template_metadata.json` declares the generator's parameters, `sdfb_job` and the evaluator's, and **every parameter is optional**: a launch for one job cannot be made to supply the other's. A generation launch that lacks a required flag is now refused by the generator's own argument parser in the launcher, not by the Dataflow API. `run_id` no longer carries a pattern (both jobs take any text there).
- `composer/evaluation_framework.py` launches from the generation template and reads the marker the import workflow already substitutes for the generation DAG; it needs no marker of its own.
- `packages/sdfb-evaluation/deploy/build_flex_template.sh` builds only an evaluator-only template from an existing image (`IMAGE=…`); it builds no image. The package's CPU `Dockerfile` stays for the package as a unit of its own.
- Preflight step 13j looks for the deployment's one template (`sdfb-<version>-template.json` or `sdfb-latest-template.json`), an evaluator-only template, or any `sdfb-*-template.json`, and names what it found.
- The generation launcher's log lines carry the logger name `sdfb_beam.cli.run_pipeline` where they carried `__main__`: the launch now enters through the dispatcher.
- CLAUDE.md constraint 3 and ADR 0001 are narrowed to managed dashboard services (Looker, Looker Studio / Data Studio, Dataplex): a self-hosted GUI may read the project's tables, read-only ([ADR 0042](docs/adr/0042-self-hosted-platform-gui.md)). `gui/**` and `scripts/gui/**` stay out of the Dataflow Solution Guides copy.

### ⚡ Performance

### 🐛 Fixed
- A chained or standalone evaluation launch reached the evaluator without its target: the Flex Template launcher drops a template parameter named `job_id`. The template parameter is now `generation_job_id` (`--job_id` still works on a direct command line); a test keeps template parameters clear of the names the launcher owns ([ADR 0041](docs/adr/0041-evaluation-standalone-package.md), note of 2026-10-08).

### 🗑️ Removed
- The generation DAG's `TriggerDagRunOperator` on the evaluation DAG, and the evaluator-version marker of the evaluation DAG: nothing substitutes it any more.

### 📗 Docs
- One image, one template: the amendment to ADR 0041, the design document's §8.2 and §8.3, `docs/DESIGN.md` §1 and §11, `docs/DEPLOYMENT_PREREQUISITES.md` (Evaluator, step 13j), `docs/RUN_PLAYBOOK.md` ("Evaluate a run": the three launch paths), the evaluator README (sections 5 and 6) and the evaluation skill card.
- ADR 0041 and `docs/DESIGN.md` §11 (Evaluation); the evaluation design document rewritten for the code as built; `README.md` (quickstart, glossary: baseline, noise floor, memorization lift, exposure set, matched n), `docs/ROADMAP.md`, `docs/DEPLOYMENT_PREREQUISITES.md` (evaluator IAM, image, template), `docs/RUN_PLAYBOOK.md` ("Evaluate a run") and article 10 unblocked. The old in-job evaluation proposal (`ws3-eval-framework`) is superseded.
- The end-to-end validation prompt gains an optional Step 3.6, statistical evaluation, folded into the evidence bundle.
- [`gui/README.md`](gui/README.md): quickstart in mock and live mode, the environment variables, the four tabs with screenshots, the developer loop and what a future Cloud Run deployment needs. [`gui/docs/ARCHITECTURE.md`](gui/docs/ARCHITECTURE.md) gains the named-query registry, measured bundle sizes against their budgets, the BFF guards in order and the scoring parity with the Python evaluator; [`gui/docs/DATA_CONTRACTS.md`](gui/docs/DATA_CONTRACTS.md) gains what generates which type, the `metrics_info` identity and how to add a metric. Screenshots in [`gui/docs/assets/`](gui/docs/assets/README.md) carry their provenance.
- `docs/DESIGN.md` §12 Platform GUI; the root README's Platform GUI section; the articles index names the CONFIG tab as the groundwork for Part 8.

## [v0.5.3] — 2026-09-21

### 📗 Docs
- Article 3 of the Medium series (`docs/articles/03-common-runtime-cpu-gpu-vllm.md`, Draft): the common runtime — one worker pool split by stage, vLLM inside the DoFn lifecycle (lazy ignition, process + port mutex, VRAM-derived budget, unfittable wait, lost-race adoption, refcount), prefill/decode and the measured prefix-cache hit rate on a T4, the guardrails track, the GIL ceiling and `sdk_containers`, `initial_workers` + `autoscaling=fixed`, uniqueness modes / DLQ / `FILE_LOADS`, the accelerator–model–dtype matrix, and the optimizations assessed (LMCache, TurboQuant, MPS, AWQ, n-gram speculative decoding, FP8 KV). Five new drawio+PNG pairs in `docs/articles/assets/` and four generated figures from the new `scripts/doc/make_vllm_serving_figures.py` (vLLM engine stats from the 2026-08-22 run, ignition across five launches, GPU busy vs billed, the KV-budget sweep through `vllm_client._fit_max_model_len`).
- Article 4 of the Medium series, [`b1_rag` — retrieval that seeds the prompt and never touches a row](docs/articles/04-b1-rag-deep-dive.md): chunking, the embedder, the exact FAISS index, the three `--pool_seed_strategy` values, the clause/retrieval interplay, why the layer is not `apache_beam.ml.rag`, and the engine seam (a figure shared with Part 5). Every data example is the output of [`scripts/doc/b1_rag_walkthrough.py`](scripts/doc/b1_rag_walkthrough.py), which calls the pipeline's own functions on a toy table and is pinned by unit tests.
- [`scripts/doc/make_rag_geometry_figures.py`](scripts/doc/make_rag_geometry_figures.py) generates the article's concept figures — the GReaT row-to-sentence drawing, the 3-D sphere and its GIF, the seed pickers, and a labelled map of fifty names with seeds, candidates and their vectors (every selection drawn is computed by `sdfb_core.rag.retrieval`) — one evidence figure from the WS5 `MEASURED` block, and takes over `prefix-vs-kcenter-coverage.png`, which had no committed generator and a clipped title.

## [v0.5.2] — 2026-09-19

Addresses the first review of the Dataflow Solution Guides PR ([#289](https://github.com/GoogleCloudPlatform/dataflow-solution-guides/pull/289)); recorded as the amendment to [ADR 0040](docs/adr/0040-dsg-donation-golden-source-sync.md).

### 🚀 Added
- `scripts/dsg/sync.py --onto-branch BRANCH` publishes a ref onto a sync PR that is under review: it starts from the fork branch's tip, lands the ref as one commit on top, pushes without force, and updates that PR instead of superseding it. A reviewer's commits and review threads survive; a push that is not a fast-forward stops the sync.
- `scripts/dsg/headers.py`: one Apache-2.0 licence header on every source file, checked in CI (`--fix` inserts it). The DSG sync rewrites only the holder line to `Google LLC`, so a file has the same line numbers in both repositories; the `headers` gate enforces it on the staged tree.
- `python-version` sync gate, and a test over this repository: every Python pin (Beam SDK and launcher images, site-packages paths, Cloud Build images, ruff and mypy targets, the supported range) must agree with `.python-version`.
- `docs/DESIGN.md`: the design in one visual document, with a map of every ADR the code cites. `scripts/doc/sync_design_refs.py` (in CI) keeps a `Design: docs/DESIGN.md §…` line in the docstring of each shipped module that cites an ADR, derived from that map.

### 🔧 Changed
- Python is 3.11 everywhere: `requires-python` is `>=3.11,<3.12` (the lock kept every package version and dropped the 3.12 forks), and the guide ships this repository's `.python-version` instead of a second copy in the `dsg/` overlay.
- The DSG copy ships `docs/DESIGN.md` and the figures it embeds instead of `docs/adr/**` and `docs/designs/**` (121 files down to 13). Decision records stay here; links to them are pinned to this repository.
- The guide's `setup.py` version and Terraform image tag are the static guide version, and the image build stamps the checkout's commit. The README header names the release and commit the copy corresponds to.
- README, PR template and the DSG index block no longer describe an automated synchronization.
- Project metadata names the author.

### 🐛 Fixed
- The shipped `.python-version` said 3.12 while the container, the lock and the tooling were 3.11; the model-staging Cloud Build step had drifted to `python:3.12-slim`.
- The sync's link gate could not see a link target behind a badge image (`[![License](…)](LICENSE)`), so an unshipped target went unreported.
- Documentation and docstrings that still said the pipeline serves through Beam's `VLLMCompletionsModelHandler` / `RunInference`. It has not since ADR 0014: the engine's `ModelClient` owns the vLLM server. `docs/MODEL_LAYOUT.md` shows the real lifecycle, and the `model-handler` skill no longer teaches the `guided_json` request shape that vLLM ignores.

### 🗑️ Removed
- `LICENSE` and `.sync-source.json` from the DSG copy. Without the latter, Terraform had silently tagged images `dev`; its five readers now have other sources.

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
