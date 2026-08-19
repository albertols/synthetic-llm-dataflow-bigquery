---
mode: agent
description: >
  Generate a Claude-Code-ready end-to-end integration validation report for a
  synthetic-dataflow-bigquery deployment in ANY GCP environment / table.
  Cross-validates the generation-engine output samples against the live source
  + landing BigQuery tables AND against the packages/ engine codebase,
  quantifies duplication / repetition / singularity / sparsity / memorization /
  schema defects, chains a free-text pattern crosscheck + source-vs-synthetic
  statistics diff, traces each finding to concrete engine code, and mines
  Dataflow job metrics + worker logs for execution milestones (startup,
  model / vLLM ignition, embedder load, generation stall, BigQuery load). ADC
  access to the target GCP project is a PREREQUISITE and is verified first.
  Inputs: engine CSVs (under integration_test/<job_id>/, auto-fetched from
  BigQuery via ADC when a file is missing), project, source/landing/quality
  FQNs, Dataflow job_ids, region, PK + identity columns. Output:
  output/end_to_end_validation_report_YYYY_MM_DD_HH_MM.md +
  the integration_test/<job_id>/{real,oss}/ bundle (real/ is the canonical,
  duplicate-free artifact set; the parent folder keeps only the sample CSVs).
  Optionally chains /llm_prompt_constraint_recommender at the end to turn the
  free-text evidence into per-column llm_prompt_constraint recommendations.
---

# /end_to_end_validation_report_generation — E2E Engine Validation Report

## Goal

Produce a **complete, concise, Claude-Code-ready**
`output/end_to_end_validation_report_YYYY_MM_DD_HH_MM.md` assessing the state of
the generation engines after a GCP Dataflow deployment. Integrate **three**
evidence sources:

1. **Generated data** — the engine CSV samples (offline, table-agnostic).
2. **Live GCP** — the source + landing BigQuery tables, the
   `synthetic_data_quality` tables, and Dataflow job metrics + worker logs.
3. **The engine codebase** — `packages/` — so every defect is traced to the
   exact file/symbol that causes it and comes with a concrete fix.

This is a **repeated action** run after every deployment. Be deterministic,
show real numbers, never hand-wave, and keep everything **generic**: no table
column names, dataset names, or project ids are hard-coded — they arrive as
inputs.

---

## Inputs (ask the user if not provided; nothing is hard-coded)

| Param | Example | Notes |
|---|---|---|
| `CSVS` | `b1_rag=integration_test/<JOB_ID>/b1_rag_sample.csv …` | `engine_label=path`, repeatable; sample CSVs live under `integration_test/<JOB_ID>/`; optional: when omitted (or a file is missing), Step 1.5 fetches the samples from `LANDING_FQN` via ADC |
| `PROJECT` | `db-<env>-…-pwcclake-es` | GCP project id |
| `SOURCE_FQN` | `<project>.<dataset>.<TABLE>` | live source table |
| `LANDING_FQN` | `<project>.synthetic_data.<TABLE>` | synthetic landing table |
| `QUALITY_DATASET` | `<project>.synthetic_data_quality` | validation_runs + dlq |
| `SCHEMA` | `config/bq_schema/<dataset>/<TABLE>.schema.json` | BQ schema JSON |
| `REGION` | `europe-west3` | Dataflow region |
| `JOB_IDS` | `2026-…-…` (one per engine run) | Dataflow job ids |
| `PK` | `<PK_COL[,PK_COL2]>` | primary-key column(s) |
| `IDENTITY_COLS` | `<UUID_COL[,…]>` | per-row-unique columns |
| `BATCH_SIZE` | `500` | Beam/RunInference batch size (enables the `equals_batch_size` run-length flag) |
| `RUN_IDS` | `<run_id>` (optional, one per engine run) | scopes `validation_runs`/`dlq` lookups |
| `ENGINE_LABEL=JOB_ID` | `b1_rag=<job_id>` (optional, repeatable) | stamps a readable engine name on the matching Dataflow result |
| `ENGINE_LABEL=RUN_ID` | `b1_rag=<run_id>` (optional, repeatable) | pairs an engine label with its `run_id` so Step 1.5 fetches only that engine's rows from `LANDING_FQN` |
| `RUN_ID_COL` | `run_id` | column in `LANDING_FQN` holding the salted run id; **required** by Step 1.5 whenever any `ENGINE_LABEL=RUN_ID` pair is given — without it the fetch is unfiltered and every engine's CSV would silently contain the same rows despite the per-engine labels |
| `FREETEXT_COLS` | `COL_A,COL_B,COL_C` | comma-separated free-text/STRING columns for Step 3.5's crosscheck (optional — discovered from the free-text subset found in Steps 2–3 if omitted) |

If a param is unknown, discover it: `SCHEMA`/columns via the schema JSON or
`INFORMATION_SCHEMA`; `LANDING_FQN` via the `synthetic_data` dataset; `JOB_IDS`
from the user; `BATCH_SIZE` from the pipeline launch params (composer /
`3_import_dag.yaml`); `RUN_IDS` from `validation_runs` or the pipeline launch
logs; `RUN_ID_COL` from the landing table schema or the pipeline launch params
(the composer/DAG's `run_id` output column — usually named `run_id`);
`FREETEXT_COLS` from the free-text subset discovered in Steps 2–3. If
only one engine was deployed, run the single-engine subset.

**Per-deployment artifact folder**: every deployment's artifacts share one
folder named after the primary Dataflow job id (`<JOB_ID>` = first of
`JOB_IDS`), e.g. `integration_test/2026-07-09_11_32_56-17188177770294375504/`.
Steps 2–3.5 write their outputs there as **transient working files**; Step 6
folds every one of them into the `real/` + `oss/` bundles and prunes the
parent-level duplicates, so a **finished** deployment folder is exactly:

```
integration_test/<JOB_ID>/
  <engine>_sample.csv           # one per engine — the ONLY copy (never
                                # duplicated into real/ or oss/)
  real/                         # canonical, verbatim — internal use
    gcp_metrics.json            offline_metrics.json
    stats_diff_metrics.json     freetext_crosscheck_metrics.json
    stats_diff.md               freetext_crosscheck_report.md
    report.md                   mapping.json   # decode key, real/ only
  oss/                          # same artifacts, de-identified, shareable
    gcp_metrics.json            offline_metrics.json
    stats_diff_metrics.json     freetext_crosscheck_metrics.json
    stats_diff.md               freetext_crosscheck_report.md
    report.md
```

No metrics JSON or crosscheck/stats markdown may survive at the parent level
once Step 6 has run — `real/` is the single source of truth (the historic
parent-level `e2e_validation_metrics.json`, `e2e_gcp_metrics.json`,
`stats_diff.json` and `freetext_crosscheck_metrics.json` were always
byte-identical to their `real/` twins; that duplication is retired). Only the
report itself stays under `output/`. (Some environments land runs under the
gitignored plural `integration_tests/` — the layout is identical there.)

---

## Step 0 — PREREQUISITE: verify GCP access (ADC), fail fast otherwise

ADC access to `PROJECT` is **mandatory** — the report is incomplete without the
live BQ + Dataflow sections. Verify first:

```bash
python - <<'PY'
import google.auth, google.auth.transport.requests as t
c,_=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
c.refresh(t.Request()); print("ADC OK")
PY
```

If this fails, STOP and instruct the user to run (this is a hard requirement):

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project <PROJECT>
```

Note: the `bq`/`gcloud` CLIs may require interactive re-auth under org policy;
the Python clients use ADC directly, so `scripts/e2e/e2e_gcp_probe.py` drives all
live access (BigQuery client + Dataflow/Logging REST) and sends an
`x-goog-user-project` quota header. Prefer it over the CLIs.

---

## Step 1 — Read the codebase to know EXPECTED behaviour

Read these and summarise what each engine is *designed* to do (ground
"expected vs reality"; verify line refs are still current):

1. `packages/sdfb-beam/src/sdfb_beam/pipeline.py` — DAG wiring, `PipelineConfig`
   (`seed` default), batch specs, the validation-run gate.
2. `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` — per-batch `seed`
   threading (`base_seed + batch_id`), engine lifecycle, embedder warm-pull.
3. `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` +
   `profile.py` — `_mix_seed` (None→fixed default), free-text pool bound,
   column-kind thresholds.
4. `packages/sdfb-core/src/sdfb_core/engines/b2_library/engine.py` +
   `backends.py` + `freetext.py` — empirical sampling, similarity→temperature,
   the `_blend_pools` reference-copy mass, and the LLM-failure fallback to
   observed exemplars (the memorization path).
5. `config/thresholds.yml` — BLOCKER/CRITICAL rules; whether a PK is registered.
6. `.github/workflows/3_import_dag.yaml` + `composer/synthetic_beam_bigquery.py`
   — the **actual default params** (`num_rows`, `batch_size`, `similarity`,
   `seed`, `client_type`, `gpu`) the runs used. Note the **`gpu` default**:
   a T4 cannot run Gemma 4, so an LLM run on T4 silently falls back to copying
   reference exemplars.
7. `packages/sdfb-core/src/sdfb_core/observability.py` — the `SDFB_MILESTONE
   name=<x> k=v` log-mining contract every worker milestone conforms to.
8. `docs/RUN_PLAYBOOK.md` — the operational run recipe (GPU verdict, run
   matrix, Dataflow options) this report cross-checks against.

Write a short "Expected behaviour" note per engine.

---

## Step 1.5 — Materialize missing sample CSVs from BigQuery (no manual export)

For every engine whose CSV under `integration_test/<JOB_ID>/` is missing:

```bash
python scripts/e2e/e2e_fetch_samples.py \
  --project <PROJECT> --landing-fqn <LANDING_FQN> --job-id <JOB_ID> \
  --run-id-col <RUN_ID_COL> \
  $(for e in <ENGINE_LABEL=RUN_ID>; do echo --engine-label $e; done) \
  --rows 10000
```

Deterministic (hash-ordered) — re-runs fetch the same rows. `--run-id-col` is
**mandatory** whenever any `--engine-label` pairs a label with a `run_id` —
omitting it means every engine's CSV would come back unfiltered (the same
rows for every engine label); the script now refuses to run in that
configuration rather than silently producing misleading per-engine samples.
Only stop if the fetch itself fails; never hand-copy CSVs again.

---

## Step 2 — Offline data analysis (table-agnostic)

```bash
python scripts/e2e/e2e_validation_analysis.py \
  $(for c in <CSVS>; do echo --csv $c; done) \
  --schema <SCHEMA> --pk <PK> --identity-cols <IDENTITY_COLS> \
  --batch-size <BATCH_SIZE> \
  --out integration_test/<JOB_ID>/e2e_validation_metrics.json
```

Per engine it computes: full-row duplicate ratio + distinct rows; **REPETITION**
(per-column top-value share + PK run-length histogram — runs equal to the batch
size ⇒ batch replay); **SINGULARITY** (constant / near-constant columns);
**SPARSITY** (null / zero / date-sentinel fractions); identity-column
uniqueness; integer type conformance; and cross-sample Jaccard overlap. Pull
the headline numbers from the JSON — never eyeball the CSV.

---

## Step 3 — Live GCP cross-validation + Dataflow observability

```bash
python scripts/e2e/e2e_gcp_probe.py \
  --project <PROJECT> \
  --source-fqn <SOURCE_FQN> --landing-fqn <LANDING_FQN> \
  --quality-dataset <QUALITY_DATASET> \
  --region <REGION> --pk <PK> \
  $(for j in <JOB_IDS>; do echo --job-id $j; done) \
  $(for r in <RUN_IDS>; do echo --run-id $r; done) \
  $(for e in <ENGINE_LABEL=JOB_ID>; do echo --engine-label $e; done) \
  --out integration_test/<JOB_ID>/e2e_gcp_metrics.json
```

`--run-id` (repeatable) scopes the `validation_runs`/`dlq` query to this
deployment's runs; `--engine-label label=job_id` (repeatable) stamps a
human-readable engine name onto the matching Dataflow result so Step 5's
scorecard doesn't have to cross-reference job ids by hand.

It uses ADC to compute, generically (schema introspected from
`INFORMATION_SCHEMA`, no column names hard-coded):

- **Memorization** — per column, `copy_ratio` = fraction of landing values that
  already exist in the source column (+ `novelty_ratio`, `source_distinct`).
  Flag `copy_ratio≈1.0` on identity / PK / free-text columns as a **privacy
  leak** (real values emitted).
- **Repetition / singularity / sparsity vs source** — `top_value_share`,
  `is_constant`, `null_fraction`, `zero_fraction`; PK distinct/dup/max-repeat.
- **Quality tables** — `validation_runs` (status, `observed_blocker_ratio`,
  `model_uri`) and `dlq` rule counts.
- **Dataflow per job** — wall-clock `execution_seconds`; accelerator (**check
  for T4 vs L4**); resource totals (`TotalVcpuTime`, `TotalGpuTime`,
  `TotalMemoryUsage`); custom counters (`generation.yielded`,
  `validation.pydantic_valid`); job-message **phase markers** (launcher →
  workers-ready → cleanup) + dominant fused stage; and **worker-log engine
  milestones**: setup packages, **embedder warm-pull + load time**, **model
  weights loading**, **vLLM ignition**, **sdgx/CTGAN fit**, FAISS load, and the
  **generation-stall max seconds** — with chronological durations between them,
  plus the worker image package versions (vllm/torch/transformers/faiss/sdgx).

### LLM-lifecycle forensics (mandatory)

For every run under test, extract and report:

1. **The `error=` field of every `freetext_llm_fallback` milestone** — the
   exception class name is logged (`error=RuntimeError`, `error=TimeoutError`,
   …). Never report a fallback without its error type; the 2026-07-10 cycle
   burned three runs because the truncated milestone hid
   `error=RuntimeError` (= client setup never ran).
2. **Presence/absence of the client lifecycle milestones**, in order:
   `model_client_setup_start` → `model_pull_start` → `model_pull_done` →
   `vllm_spawn` → `vllm_ready` → `model_client_setup_done`. Absence of
   `vllm_ready` means NO LLM inference happened — do not infer LLM activity
   from TotalGpuTime (GPU seconds accrue while allocated, even idle).
3. **B.1 phase timings** from `b1_embed_done` (with `rows=`),
   `b1_index_built`, `b1_pools_built` — attribute setup wall-time to the
   correct phase instead of "FAISS build".
4. **Effective launch parameters from the Dataflow job's `parameters` dict**
   (job describe), not from DAG defaults — include `reference_rows_limit`,
   `pk_cols`, `identity_cols`, `seed`, and the salted `run_id`.

Milestones from images built on the `SDFB_MILESTONE` contract
(`sdfb_core/observability.py`) arrive pre-parsed as `sdfb.<name>` keys (e.g.
`sdfb.batch_start`, `sdfb.freetext_llm_fallback`) — the legacy wording regexes
above only matter for jobs from a pre-contract image. Prefer the `sdfb.*` keys
when both are present.

Note which engine's data the landing table currently holds (match on
`IDENTITY_COLS` distinct count) and say so.

---

## Step 3.5 — Free-text crosscheck + source-vs-synthetic stats diff (mandatory)

Chain the free-text pattern crosscheck (its own prompt:
`freetext_crosscheck_report_generation.prompt.md`) and the stats diff — both
write into the per-deployment folder when run from E2E:

```bash
python scripts/e2e/freetext_crosscheck.py \
  --source-fqn <SOURCE_FQN> --synthetic-fqn <LANDING_FQN> \
  --columns "<FREETEXT_COLS>" \
  --out-json integration_test/<JOB_ID>/freetext_crosscheck_metrics.json \
  --out-md   integration_test/<JOB_ID>/freetext_crosscheck_report.md

python scripts/e2e/source_synthetic_stats_diff.py \
  --source-fqn <SOURCE_FQN> --synthetic-fqn <LANDING_FQN> --project <PROJECT> \
  --out-json integration_test/<JOB_ID>/stats_diff.json \
  --out-md   integration_test/<JOB_ID>/stats_diff.md
```

`FREETEXT_COLS` defaults to the free-text subset discovered in Steps 2–3.

These parent-level outputs (like the Step 2/3 metrics JSONs) are **working
files**: Step 6 folds them into `real/` + `oss/` and prunes them.

---

## Step 4 — Diagnose defects and trace each to code

For every anomaly: **evidence → root cause (file:symbol) → fix**. Check for:

- **Block-replay duplication**: seed replay is fixed by
  `derive_batch_seed(run_id, batch_id)` (`sdfb_core/seeding.py`) — if PK
  run-lengths still equal the batch size (`equals_batch_size` in the offline
  metrics), check the `sdfb.batch_start` seeds in the worker logs for repeats
  across batches rather than assuming a constant-seed regression.
- **Memorization / privacy leak**: `copy_ratio≈1.0` on non-numeric columns,
  especially identity/PK. Cross-check the **GPU** — a T4 run means Gemma 4 never
  loaded and free text is 100 % reference-copied via the exemplar fallback.
- **Silent LLM fallback**: check the `sdfb.freetext_llm_fallback` milestone
  count — any occurrence means the LLM contributed nothing for that column on
  that batch (copied an observed exemplar instead); a nonzero count on a run
  that claims LLM generation is itself a finding.
- **GPU/dtype guard regression**: a `vllm_error` / `ModelGpuIncompatibleError`
  worker-log line means the job **should have died** at vLLM init
  (bf16-vs-Turing mismatch). If the job instead shows `validation_runs.status
  = PASSED`, the guard in `sdfb_beam/handlers/vllm_client.py` regressed —
  treat this as a BLOCKER-severity finding, not a perf note.
- **Gate blind spots**: duplication / memorization not scored; PK not registered
  so `pk.duplicate` never fires; runs `PASSED` despite the above.
- **Perf**: unnecessary embedder warm-pull for the library engine; long
  generation stall; startup-bound wall time.

> **Forward-looking**: this step hand-computes fidelity from the offline CSV
> (Step 2) and live BQ (Step 3). Once `--enable-evaluation` lands (see
> `docs/designs/2026-07-07-evaluation-framework-design.md`), pull the fidelity
> numbers from `synthetic_data_quality.validation_data_history` instead of
> recomputing them here.

---

## Step 5 — Write the report

Create `output/end_to_end_validation_report_YYYY_MM_DD_HH_MM.md`
(`date +%Y_%m_%d_%H_%M`). Sections, in order:

0. **Runs under test** — engine / CSV / rows / job_id / wall time / launch
   params + which engine the landing table holds + caller identity.
1. **Executive verdict** — side-by-side scorecard 🟢/🟡/🔴 + a 3-point bottom line.
2. **Per-engine findings** — evidence tables from Steps 2–3.
3. **Memorization** — the source-copy table (copy_ratio) + the GPU/LLM-fallback
   root cause + fixes.
3.5. **Source vs synthetic statistics** — stats-diff + crosscheck headline
   numbers, each finding traced to a generation code area, feeding the
   backlog.
4. **Schema, gates & quality tables** — conformance, gate blind spots, `validation_runs`/`dlq`.
5. **Dataflow execution insights** — per-job phase timings, engine milestones,
   resource/GPU seconds, and cost/value commentary.
6. **Reproduce** — the exact commands, in order (incl. the ADC login).
7. **Prioritized backlog** — severity-ranked, area-tagged, one-line fix + §ref.

Rules: relative links; every claim backed by a JSON number or a `file:symbol`
code ref; keep it tight; no dashboards / Vertex / external LLM suggestions
(out of scope per `CLAUDE.md`).

---

## Step 6 — Export a shareable bundle (internal `real/` + de-identified `oss/`) and prune the duplicates

The report + metrics + sample CSVs contain the real project / dataset / table
/ column names and sampled data values. Fold **everything** (four metrics
JSONs, both crosscheck/stats markdown reports, the report) into the two
sibling folders with `scripts/e2e/e2e_bundle_export.py` (generic — the
mapping is derived from the artifacts, so it works for any table /
environment):

```bash
python scripts/e2e/e2e_bundle_export.py \
  --metrics gcp=integration_test/<JOB_ID>/e2e_gcp_metrics.json \
  --metrics offline=integration_test/<JOB_ID>/e2e_validation_metrics.json \
  --metrics stats_diff=integration_test/<JOB_ID>/stats_diff.json \
  --metrics freetext_crosscheck=integration_test/<JOB_ID>/freetext_crosscheck_metrics.json \
  --doc stats_diff=integration_test/<JOB_ID>/stats_diff.md \
  --doc freetext_crosscheck_report=integration_test/<JOB_ID>/freetext_crosscheck_report.md \
  $(for c in <CSVS>; do echo --csv $c; done) \
  --report output/end_to_end_validation_report_YYYY_MM_DD_HH_MM.md \
  --out-root integration_test \
  --no-redact-values \
  --prune-inputs
  # writes integration_test/<JOB_ID>/{real,oss}/ and, after a CLEAN leak
  # scan, deletes the parent-level metrics/markdown duplicates it ingested
```

The `--metrics` labels name the `real/`+`oss/` files (`gcp=` →
`gcp_metrics.json`, `offline=` → `offline_metrics.json`, `stats_diff=` →
`stats_diff_metrics.json` …) — keep all four labels exactly as above or the
release pipeline's artifact discovery will not find them. `--doc` moves the
two markdown reports: verbatim into `real/<label>.md`, redacted into
`oss/<label>.md` (`stats_diff=` → `stats_diff.md`,
`freetext_crosscheck_report=` → `freetext_crosscheck_report.md`).

`--csv` **registers** each sample CSV's header + values in the redaction
mapping (so the report/doc redaction stays complete) but the CSV is **not**
copied into `real/` or `oss/` — the parent-level CSV is the single copy, and
`--prune-inputs` never touches it (nor the report under `output/`).

The bundle folder name defaults to the first Dataflow job id in the gcp
metrics (`--job-id` overrides), so everything for one deployment sits under
`integration_test/<JOB_ID>/` next to the sample CSVs.

- `real/` — verbatim `*_metrics.json` + `stats_diff.md` +
  `freetext_crosscheck_report.md` + `report.md` **and** `mapping.json` (the
  decode key) for internal use.
- `oss/` — the same artifacts with IDENTIFIERS (project/dataset/table/bucket/
  caller email/reference digests/file paths), COLUMN NAMES
  (`COL_NNN`; PK→`PK_COL`, identity→`ID_COL`), and DATA VALUES (`VAL_NNNN`)
  deterministically redacted. A generic email regex catches any caller PII even
  when the metrics captured it as `unknown`. **Dataflow job ids and job names
  are kept as-is** (never redacted) — they name the bundle folder and keep the
  oss/ artifacts correlatable with the job.

Metadata (project / dataset / table / column names + identifiers) is **always**
hidden. Data-value redaction is toggleable: pass `--no-redact-values` to keep
the real dev data-sample values in `oss/` (e.g. when the source is non-sensitive
dev data) — metadata stays hidden either way. Default is to redact values.

The tool runs a **leak scan** over `oss/` and exits non-zero if any real token
survived — the export is only shareable when it prints `leak scan: clean ✅`.
Hand the OSS team the `oss/` folder + the `scripts/e2e/` toolchain; keep
`real/` local.

---

## Step 7 — Verify

1. Report opens; relative links resolve.
2. Every headline number matches `real/offline_metrics.json` /
   `real/gcp_metrics.json` / `real/stats_diff_metrics.json` /
   `real/freetext_crosscheck_metrics.json`.
3. Each defect has a code-level root cause + fix.
4. The bundle export printed `leak scan: clean ✅` and `oss/` is free of the
   real project / dataset / table / column names (Dataflow job ids and job
   names are the deliberate exception — they stay verbatim).
5. `integration_test/<JOB_ID>/` matches the finished-folder tree exactly:
   the sample CSVs at the parent level, `real/` with the four metrics JSONs +
   `stats_diff.md` + `freetext_crosscheck_report.md` + `report.md` +
   `mapping.json`, and `oss/` with the same set minus `mapping.json`.
6. **No parent-level duplicates survive**: `e2e_validation_metrics.json`,
   `e2e_gcp_metrics.json`, `stats_diff.json`/`.md`,
   `freetext_crosscheck_metrics.json`/`_report.md` are gone from the parent
   (pruned by Step 6) and no `*_sample.csv` exists inside `real/` or `oss/`.
7. Print a one-line summary: report path + the single most important finding.

---

## Step 8 — OPTIONAL: chain the prompt-constraint recommender

When the free-text evidence shows steerable gaps — `shape_recall < 0.9`,
spurious shapes, prefix/affix loss, vocabulary or locale drift, or a column
stuck on the wrong route — offer the user to chain
`llm_prompt_constraint_recommender.prompt.md` (ask; it edits schema files):

```
/llm_prompt_constraint_recommender JOB_ID=<JOB_ID> \
  SCHEMA=<SCHEMA> SOURCE_FQN=<SOURCE_FQN>
```

It reads this deployment's `integration_test/<JOB_ID>/real/` evidence
(crosscheck, stats diff, offline + GCP metrics, reports) and writes
evidence-backed `{"llm_prompt_constraint": …}` objects into the schema
file's column descriptions (DDL_CONTRACT_GUIDE §4 / ADR 0024), so the next
run's pool prompts + guided decoding close the observed gaps. Skip it when
Step 3.5 shows no free-text finding worth steering.

---

## Consistency rules (always enforce)

- **ADC access is a prerequisite** (Step 0). If missing, stop with the exact
  `gcloud auth application-default …` commands — never fabricate live results.
- **Numbers come from the analyzer/probe JSON**, never from eyeballing a CSV.
- **Every defect traces to `packages/` code** (file + symbol).
- **Generic always**: table columns, datasets, project ids arrive as inputs;
  the scripts introspect the schema — nothing is hard-coded.
- **De-identify before sharing**: never hand the OSS team `output/` or `real/`;
  share only the `oss/` bundle after the leak scan passes (Step 6).
- **Expected-vs-reality** framing throughout.
- Report filename is always `end_to_end_validation_report_YYYY_MM_DD_HH_MM.md`
  under `output/`.
- **Per-deployment artifacts live under `integration_test/<JOB_ID>/`** — the
  sample CSVs at the parent level plus the `real/` + `oss/` bundles holding
  everything else (four metrics JSONs, crosscheck + stats-diff markdown,
  report, mapping). Parent-level metrics/markdown files are working copies
  that Step 6 prunes; **never leave a duplicate behind, never copy a CSV into
  a bundle**.
- **Dataflow job ids and job names are never redacted** — they stay verbatim
  in the `oss/` bundle.
