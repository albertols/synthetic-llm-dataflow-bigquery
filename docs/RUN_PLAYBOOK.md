# Run playbook — GPU verdict, run matrix, Dataflow options, report recipe

> **Running the WS8 validation campaign** (relational contract, tiered
> source stats, inverse-CDF, expansion arms, 10M)? The run matrix lives in
> [`RUN_PLAYBOOK_WS8.md`](RUN_PLAYBOOK_WS8.md); this doc keeps the GPU
> verdict, Dataflow options, capacity ladder, and report recipe it builds on.

The operational companion to [`DEPLOYMENT_PREREQUISITES.md`](DEPLOYMENT_PREREQUISITES.md)
(what must exist before a run) — this doc is about the run itself: which GPU to
pick, what the four M1 §11 validation runs are, which Dataflow launch knobs
matter, how to hold L4 capacity when `europe-west3` is stocked out, and how to
turn a finished job into a report. Everything here reflects the
`composer/synthetic_beam_bigquery.py` DAG and the `packages/sdfb-beam` CLI as
they exist on this branch — read those files if a flag looks stale.

---

## 1. GPU verdict — Gemma cannot run on T4

**Gemma (2/3/3n/4, including AWQ/GPTQ-quantized variants) does not run on
NVIDIA T4 under vLLM.** This was verified, not assumed, and it is now enforced
in code: `ModelGpuIncompatibleError` in
[`packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py`](../packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py)
raises at `VLLMModelClient.setup()` — before the vLLM server subprocess spawns
— whenever the pulled model's `config.json` says `torch_dtype: bfloat16` and
the worker's reported CUDA compute capability is below `8.0`. That guard
exists because, before it, a Gemma-on-T4 job didn't fail — it silently sailed
past a ~24-minute stall and let the engines fall back to copying reference
exemplars into the batch (a privacy leak, not a crash), which is worse than a
loud failure.

Three blockers stack, and quantization does not rescue any of them:

1. **bf16 needs compute capability ≥ 8.0; T4 is 7.5 (Turing).** Gemma ships
   bf16-trained. `--dtype=half` bypasses vLLM's own capability check, but
   Gemma run in fp16 silently emits empty or pad-token output rather than an
   error — reported against `google/gemma-3-4b-it` in HF discussion #33, and
   in vLLM issues [#40290](https://github.com/vllm-project/vllm/issues/40290)
   and [#17123](https://github.com/vllm-project/vllm/issues/17123). There is
   no flag that makes bf16-trained Gemma correct on sub-Ampere hardware.
2. **No working attention backend on Turing for Gemma's shapes.**
   FlashAttention-2 requires SM ≥ 8.0 outright. `FLASHINFER` only supports
   head sizes `{64, 128, 256}`, while Gemma 4's global attention head dim is
   512. The remaining fallbacks, `TRITON` and `FLEX`, crash with shared-memory
   allocation failures on Turing
   ([vllm#38918](https://github.com/vllm-project/vllm/issues/38918),
   [vllm#38887](https://github.com/vllm-project/vllm/issues/38887)) — there is
   no attention backend left to fall back to.
3. **Gemma's `head_dim=256` attention tiles need ~96KB of shared memory per
   block; Turing's hard limit is 64KB per SM.** This is a hardware ceiling,
   not a tuning knob. Critically, the model in the failing report for
   [vllm#38918](https://github.com/vllm-project/vllm/issues/38918) was
   **already AWQ 4-bit quantized** — quantization shrinks the weights, not the
   attention working set, so it does not move this number.

**Conclusion: there is no Gemma-on-T4 configuration.** T4 is a plumbing-only
profile in this pipeline (real vLLM server, real BigQuery write, no Gemma).
Fidelity runs require an L4.

**L4 availability.** `europe-west3-a` and `europe-west3-b` both carry L4
(`g2-standard-*`) capacity; `g2-standard-4`, `-8`, `-12`, and `-16` all ship
exactly **one** L4 — the accelerator count doesn't scale with machine size
inside the `g2-standard` family, only vCPU/RAM does. `europe-west4` is the
capacity fallback region when both `europe-west3` zones are stocked out (see
§4 for what to do about a stockout instead of switching region, which trades
away the EU-residency alignment in
[ADR 0004](adr/0004-europe-west3-region.md)).

**T4-safe registry model.** For plumbing runs, `gpu=t4` must be paired with a
model that is fp16-safe on Turing, not a Gemma checkpoint. The registry entry
is `qwen3_4b_instruct_2507` in
[`config/models.yml`](../config/models.yml) — Apache-2.0, `min_compute_capability:
"7.5"`, FP16, guided-JSON clean. Point the `SDFB_MODEL_URI` Composer Variable
at its `gcs_uri` before a `gpu=t4` run; the DAG's `gpu` param docstring
(`composer/synthetic_beam_bigquery.py`) says this explicitly.

---

## 2. Post-remediation run matrix (branch e2e-hardening, after Tasks 1-5)

Common params: `num_rows=1000`, `identity_cols=<ID_COL>`,
`pk_cols=<PK_COL>,<PK_COL_2>` (substitute the target table's real
identity/PK columns), `seed=""` (derived), landing table truncated
between runs (or fresh run_id verified in validation_runs).
Leave `vllm_max_model_len` at its `8192` default for every vLLM run: it caps
the KV-cache allocation, and uncapped Qwen3-2507 (native 262K context) needs
a 36GiB KV cache — the T4 EngineCore exits 1 at startup (observed 2026-07-14,
4 bundle retries then job failure, each retry re-pulling ~7.5GB of weights).

| Run | Engine | Model | GPU | Expect |
|---|---|---|---|---|
| R1' | b1_rag | qwen3-4b (`vllm_dtype=float16`) | t4 | `vllm_ready` present; NO `freetext_llm_fallback`; job FAILS if vLLM can't start (strict) |
| R2' | b1_rag | qwen3-4b | t4 (repeat of R1') | output DIFFERS from R1' (salted run_id → new seeds) |
| R3' | b2_library | qwen3-4b (`vllm_dtype=float16`) | t4 | same as R1' plus pk.duplicate rule live |
| R4' | b1_rag | gemma4-e4b-it | l4 | bf16 path; guard must NOT fire on L4 |

Pass criteria per run:
1. `model_client_setup_start/done`, `model_pull_*`, `vllm_spawn`, `vllm_ready` all present in worker logs.
2. Zero `freetext_llm_fallback` milestones (a vLLM run that falls back now crashes instead).
3. `copy_ratio < 0.3` on every non-constant STRING column with `source_distinct > 100` (probe step 3).
4. `validation_runs.run_id` unique per Dataflow job (salted suffix visible).
5. `pk.duplicate` present in `dlq_by_rule` if and only if PK collisions occurred; PASSED requires ~0.
6. `b1_embed_done rows=` equals `reference_rows_limit` (10000), not the full source count.
7. Check the Dataflow job state directly (`gcloud dataflow jobs describe <job_id>` or the Dataflow console) — the Composer DAG launches the job with `wait_until_finished=False`, so a strict-mode worker crash fails the Dataflow job itself while the Airflow task still shows success. Airflow green is not proof of a healthy run.
8. `validation_runs.valid_count` must equal `num_rows` requested. A shortfall — even on a `PASSED` row, since the BLOCKER gate only checks a *ratio* — means whole batches were lost to `engine_failure` and never replaced; PASSED is not proof the run actually produced the row count it was asked for.

---

## 3. Dataflow options

- **Accelerator selection.** The DAG's `additionalExperiments` list templates
  `worker_accelerator=type:nvidia-l4;count:1;install-nvidia-driver` for
  `gpu=l4` and `worker_accelerator=type:nvidia-tesla-t4;count:1;install-nvidia-driver:5xx`
  for `gpu=t4` — only when `client_type=vllm`; the `fake`/CPU-smoke path
  requests no accelerator at all so the BQ-write-path smoke can run on
  abundant CPU capacity while L4s are short. The `:5xx` driver pin on T4 is
  required by the Dataflow vLLM notebook's own driver guidance — don't drop
  it if you ever touch that experiment string.
- **Machine type / `g2-standard-4` vs `-8`.** Today the DAG offers no
  `g2-standard-4` toggle at all: `gpu=l4` hardcodes `g2-standard-8` in the
  DAG's `machineType` ternary, and `gpu=t4` maps to `n1-standard-8` — the T4
  plumbing runs (R1'–R3') never touch the `g2-standard` family. The `-4` vs `-8`
  trade-off is therefore informational, relevant only if someone edits that
  ternary: both sizes carry exactly one L4, so the choice is pure headroom,
  not GPU count — `-8` gives the CPU-side steps (BQ read, Pandera validation,
  `sdgx` fit for B.2, the Beam harness alongside vLLM) more vCPU/RAM to avoid
  becoming the bottleneck next to the GPU, at roughly double the non-GPU
  cost. The hardcoded `-8` is the right default for the fidelity run
  (R4'); don't downgrade it without a measured reason.
- **Worker disk.** The Flex Template's `environment.diskSizeGb` field does
  **not** propagate to the worker harness — it's set on the launch request
  but ignored (confirmed at `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py:57`,
  next to `_DEFAULT_WORKER_DISK_GB = 200` at line 59). The actual worker boot
  disk is pinned inside `run_pipeline.configure_pipeline_options()`, which
  sets `worker_options.disk_size_gb = 200` unless the caller already set it
  explicitly. 200GB is sized for the GPU container image plus the warm-pulled
  model weights (Gemma 4 E4B ≈ 9GB, the 26B-A4B AWQ checkpoint ≈ 16GB) with
  headroom — don't rely on the Flex Template's `diskSizeGb` to change this;
  change the CLI default or pass `--disk_size_gb` through
  `run_pipeline.py` instead.
- **Runner v2.** `use_runner_v2` is already in `additionalExperiments`
  unconditionally — required for the custom `ModelHandler`/DoFn `setup()`
  lifecycle this pipeline relies on to build the vLLM client once per worker
  rather than per bundle. Don't remove it.
- **ONE SDK process per GPU worker — add `no_use_multiple_sdk_containers`.**
  Runner v2's default spawns one sibling SDK process per vCPU (8 on
  `n1-standard-8` / `g2-standard-8`), and **every sibling runs the full DoFn
  `setup()`**: its own 7.5 GB weight pull, its own vLLM server spawn into the
  single GPU (all but the first crash with CUDA OOM — `Free memory on device
  cuda:0 (0.66/14.56 GiB)`), and its own CPU embedding pass fighting the
  other seven for the same 8 vCPUs. The 2026-07-16 corp T4 run
  (`..._13_23_14-11053114042412770609`) paid 26–92 min *per bundle attempt*
  in `embedder.embed` because of exactly this contention; four Dataflow
  bundle retries — each landing on a *different* sibling and repeating the
  whole setup — turned one strict-mode failure into a 5.4 h job. Add
  `no_use_multiple_sdk_containers` to `additionalExperiments` whenever
  `client_type=vllm` (any GPU tier). The `sdfb-beam` code side is hardened
  too (vLLM server reuse + bounded embed sample), but a single SDK process
  is the correct topology for a one-GPU worker regardless.
- **Zone pinning.** The DAG sets `workerRegion` to the `REGION` Composer
  Variable (`europe-west3` per [ADR 0004](adr/0004-europe-west3-region.md))
  but does not pin a specific zone — Dataflow picks within the region. For L4
  fidelity runs, prefer explicitly pinning to `europe-west3-a` or `-b` (both
  carry L4 capacity, §1) via a `workerZone` override or by relying on the
  reservation-affinity ladder in §4, rather than letting Dataflow's own
  zone-spread retry logic hunt across all of `europe-west3`.
- **`maxWorkers` for 1000-row runs.** The DAG currently hardcodes
  `maxWorkers: 4`. For the R1'/R2' replay-check runs at `num_rows=1000` with
  `batch_size=16` (~63 batches), **1–2 workers is the right target** — the
  per-batch LLM call dominates wall time and more GPU workers just means
  more idle vLLM cold-starts and more GPU spend for no throughput gain;
  `num_rows` at this scale doesn't need horizontal scale-out. Treat the
  hardcoded `4` as a ceiling, not a target — Dataflow won't launch more
  workers than the graph can use. For the T4 runs (R1'–R3') the cap is
  purely a cold-start/cost matter; for the L4 run (R4') requesting fewer
  workers up front additionally reduces contention against the L4 stockout
  (§4).

---

## 4. L4 capacity strategy (`europe-west3` g2/L4 stockout)

`europe-west3` g2-standard / L4 capacity is intermittently stocked out. There
is a three-rung ladder here, weakest to strongest — use the weakest rung that
gets a run through, and only reach for the reservation when a *campaign* of
runs (not a single one-off) needs guaranteed capacity.

**(a) On-demand + zone spread across `europe-west3-a`/`-b` — the default.**
This is what happens with no extra setup: Dataflow's own on-demand allocation
retries across the zones in the region. It's free when it works and requires
no coordination, but under a real stockout it can fail outright or queue for
an unpredictable time. Use this for one-off runs and for anything where a
delayed start is tolerable.

**(b) The `automatically_use_created_reservation` experiment — already in the
DAG, inert by default.** `composer/synthetic_beam_bigquery.py:183` already
templates this experiment onto every `client_type=vllm` launch:

```python
"{{ 'automatically_use_created_reservation' if params.client_type == 'vllm' else 'upload_graph' }}",
```

This tells Dataflow to consume a matching Compute Engine reservation **if one
exists**, using ANY-reservation affinity — meaning it opportunistically grabs
capacity from any reservation that matches the worker shape in that
project/region, and falls back to on-demand if none matches. It rides the
same `additionalExperiments` channel as `worker_accelerator`, so it's proven
to work; it is completely harmless when no reservation exists (dedupes to a
no-op) and needs zero code changes to activate — you only need to create the
reservation described in rung (c) for it to start doing anything.

**(c) Strongest: a Compute Engine reservation for the exact worker shape, in
`europe-west3-a` or `-b`, combined with the experiment above.** The
reservation holds the capacity between runs; the experiment in rung (b) is
what makes Dataflow actually consume it. Create one sized to match the L4
fidelity shape (`g2-standard-8`, 1× `nvidia-l4`) before a run campaign:

```bash
gcloud compute reservations create sdfb-l4 \
    --machine-type=g2-standard-8 \
    --accelerator=count=1,type=nvidia-l4 \
    --zone=europe-west3-b \
    --vm-count=1
```

Two things to know before doing this:

- **Prerequisite: platform-team allowlist.** GPU-targeted Dataflow
  reservations require an allowlist grant from the platform team — this is
  called out directly in the DAG comment next to the
  `automatically_use_created_reservation` line
  (`composer/synthetic_beam_bigquery.py`, the comment block at lines 175–182,
  immediately above the experiment string on line 183). Request the allowlist
  before assuming the reservation will actually
  be consumed; without it, Dataflow will silently fall back to on-demand even
  with a reservation sitting idle.
- **Idle-billing caveat.** A Compute Engine reservation bills for the
  reserved capacity whether or not a job is using it. Create it right before
  a run campaign starts and **delete it as soon as the campaign is done** —
  don't leave `sdfb-l4` standing between ad-hoc runs.

  ```bash
  gcloud compute reservations delete sdfb-l4 --zone=europe-west3-b
  ```

- **Only ANY-affinity is supported through this channel.**
  `SPECIFIC_RESERVATION` targeting — pinning a job to consume *only* a named
  reservation and fail otherwise — is **not** used here and is not available
  through the `additionalExperiments` channel Dataflow exposes for this. The
  reservation created in rung (c) is a capacity pool that
  `automatically_use_created_reservation` opportunistically draws from; it is
  not a hard binding between the job and that specific reservation. Don't
  design a run around the assumption that the job will refuse to start
  without it — it will always fall back to on-demand instead.

---

## 5. After every run — the three-command report recipe

Run these against the finished Dataflow job(s) to produce a de-identifiable
end-to-end validation report. This is the same recipe the
`/end_to_end_validation_report_generation` prompt
(`.github/prompts/end_to_end_validation_report_generation.prompt.md`) drives;
copy the values for `<CSVS>`, `<SCHEMA>`, `<PK>`, `<IDENTITY_COLS>`,
`<PROJECT>`, `<SOURCE_FQN>`, `<LANDING_FQN>`, `<QUALITY_DATASET>`, `<REGION>`,
`<JOB_IDS>` from the run you just launched.

All per-deployment artifacts share one folder named after the primary
Dataflow job id: put the sample CSVs at `integration_test/<JOB_ID>/*.csv` and
write the metrics JSONs there as **working files** — the bundle export folds
them (plus any crosscheck/stats-diff markdown) into `real/` + `oss/` and
prunes the parent-level duplicates, leaving the CSVs as the only
parent-level artifacts (the finished-folder tree is drawn in the E2E
prompt's "Per-deployment artifact folder" section).

**1. Offline analysis** (table-agnostic; computes duplicate ratio, repetition,
singularity, sparsity, identity-column uniqueness, cross-sample Jaccard —
`--batch-size` enables the run-length-equals-batch-size replay flag,
`--identity-cols` scopes the per-row-uniqueness check to PK/UUID columns):

```bash
python scripts/e2e/e2e_validation_analysis.py \
  $(for c in <CSVS>; do echo --csv $c; done) \
  --schema <SCHEMA> --pk <PK> --identity-cols <IDENTITY_COLS> \
  --batch-size <BATCH_SIZE> \
  --out integration_test/<JOB_ID>/e2e_validation_metrics.json
```

**2. Live GCP cross-validation + Dataflow observability** (ADC-authenticated;
`--run-id` lets the probe correlate a job against the R1'/R2' salted-run-id
replay-difference check in §2, `--engine-label` stamps a human-readable
label onto each job_id so the report can say "b1_rag" instead of a raw
Dataflow job id):

```bash
python scripts/e2e/e2e_gcp_probe.py \
  --project <PROJECT> \
  --source-fqn <SOURCE_FQN> --landing-fqn <LANDING_FQN> \
  --quality-dataset <QUALITY_DATASET> \
  --region <REGION> --pk <PK> \
  $(for j in <JOB_IDS>; do echo --job-id $j; done) \
  --run-id <RUN_ID> \
  --engine-label b1_rag=<JOB_ID_1> --engine-label b2_library=<JOB_ID_2> \
  --out integration_test/<JOB_ID>/e2e_gcp_metrics.json
```

**3. Bundle export** (folds the report + metrics + markdown docs into an
internal `real/` folder and a de-identified `oss/` folder safe to hand to the
OSS team; exits non-zero unless the leak scan is clean, then prunes the
parent-level duplicates it ingested. Sample CSVs are registered in the
redaction mapping but NOT copied — the parent-level CSV stays the single
copy. Dataflow job ids and job names are kept verbatim in `oss/` — the
bundle folder is named after the primary job id):

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
  # writes integration_test/<JOB_ID>/{real,oss}/ and deletes the ingested
  # parent-level metrics/markdown after a clean leak scan
```

Keep the four `--metrics` labels exactly as written — they name the
`real/`+`oss/` files (`gcp_metrics.json`, `offline_metrics.json`,
`stats_diff_metrics.json`, `freetext_crosscheck_metrics.json`) that the
release pipeline's artifact discovery expects. When a run skipped the
crosscheck/stats-diff step, drop the matching `--metrics`/`--doc` pairs.

Then recompile each bundle into its one-file recap (`_full_report.md`: ToC +
every `.md` verbatim + every metrics `.json` as a ```json annex;
`mapping.json` excluded; idempotent — rerun after any doc lands later):

```bash
python scripts/e2e/build_full_report.py \
  --dir integration_test/<JOB_ID>/real \
  --dir integration_test/<JOB_ID>/oss
```

Only the `oss/` folder produced by step 3 is shareable outside the team; keep
`real/` (and its `mapping.json` decode key) local.

---

## 6. WS5 — free-text pool store and the seeding experiment

Landed 2026-07-26 (ADR 0020, [design](designs/2026-07-26-ws5-generation-throughput.md)).
Not yet measured on real hardware — these are the runs that measure it.

### 6a. Provision the pool table (once)

**The pool store is OPTIONAL.** Without it the pipeline behaves exactly as it
did before WS5 — every worker process rebuilds its pools in `setup()`, which is
the 19.1 GPU-hour / 68-minute behaviour WS5 exists to remove. It is a
performance opt-in, not a prerequisite: `deployment_prerequisites.py` step 10
reports a missing table as **SKIP, never ACTION**.

To enable it, create the table once from the committed schema (never
auto-created — the `CREATE_IF_NEEDED` blast-radius rule confines auto-create to
the landing sink):

```bash
bq mk --table \
  "${PROJECT}:synthetic_rag.freetext_pools" \
  config/bq_schema/synthetic_rag/freetext_pools.schema.json
```

No partitioning: the table holds one row per
`(reference_digest, model_uri, column)` — a handful per run — so a partition
would add a required column and buy nothing.

`--build_pool_layer=true` against a missing table fails at launch with the
`bq mk` line above rather than a cryptic `NotFound`. The read path degrades
silently and correctly on its own.

### 6b. Target-table bootstrap (TEST_1 follow-up)

Two independent things bit the 2026-07-25 16:38 run:

- **A missing `--ddl_uri` is no longer fatal** (WS5 T1) — it falls through to
  live `INFORMATION_SCHEMA` extraction and logs `ddl_uri_miss_fallback`. A
  *corrupt* pin still fails loudly, by design.
- **The landing table is still not created unless you ask.** Pass
  `--create_if_not_exists=true`; the machinery (`derive_bq_load_schema` +
  `CREATE_IF_NEEDED`) has been complete since WS4. TEST_1 passed `false`, so
  it would have hit `CREATE_NEVER` even past the DDL step. The default stays
  `false` deliberately: flipping it widens blast radius on every run.

### 6c. Sequencing — do not confound the experiment

Run these **in order**. Phase 1 changes throughput; the seeding arms change
pool composition. Measuring them together tells you nothing about either.

| # | Flags | What it measures |
|---|---|---|
| 1 | (Phase 1 only, no pool store) | sampler hoisting + batch sizing vs the 68-min baseline |
| 2 | `--build_pool_layer=true --freetext_pools_table=…` | cold run that *populates* the store |
| 3 | `--freetext_pools_table=…` (no build flag) | **warm** run — the ≤5 min target |
| 4 | run 2 with `--pool_seed_strategy=kcenter` | arm B |
| 5 | run 2 with `--pool_seed_strategy=kcenter_rotate` | arm A |

Runs 2, 4 and 5 must each start from an **empty** `freetext_pools` for their
digest, or the ladder is skipped and the arm measures nothing. Clear with:

```bash
bq query --use_legacy_sql=false \
  "DELETE FROM \`${PROJECT}.synthetic_rag.freetext_pools\`
   WHERE reference_digest = '<digest>'"
```

### 6d. What to read out of the logs

```bash
grep -o 'name=[a-z_]*' worker_logs.jsonl | sort | uniq -c | sort -rn
```

| Milestone | Reads as |
|---|---|
| `freetext_pool_store_hit` | pool read, ladder skipped — the point of WS5 |
| `freetext_pool_store_miss` | store attached but empty for that column |
| `freetext_pool_store_error` | store unreachable; run degraded to building (not fatal) |
| `pool_build_skipped` | driver found this digest+model already populated |
| `pool_branch_setup_done` / `pool_branch_emitted` | the build branch ran |
| `ddl_uri_miss_fallback` | the DDL pin 404'd and live extraction took over |
| `vllm_ready` | should appear **once** on a cold run, **never** on a warm one |

Per arm, report novel-yield per LLM call, final pool size per column, and
ladder attempts to target — `freetext_pool_built` carries all three.

**Exclude `COL_047` / `COL_048` from every comparison.** It carries
binary characters and is a known special case.

---

## 7. WS6 — pipeline shape (landed 2026-07-27, not yet measured)

### 7a. The flag that matters most is still WS5's

The 2026-07-26_17_10_37 run spent **26 of its 53 minutes** rebuilding
free-text pools because `--freetext_pools_table` was never passed. b1_rag now
emits `freetext_pool_store_absent` (WARNING) when that happens — grep for it
before blaming anything else:

```bash
grep -c 'name=freetext_pool_store_absent' worker_logs.jsonl   # expect 0
```

### 7b. `--uniqueness_mode`

| Value | Landing | Duplicates | Use when |
|---|---|---|---|
| `exact` (default) | after up to 3 shuffle barriers | diverted to the DLQ | you need duplicates removed |
| `streaming` | **incremental, as generated** | **land**, rate measured and gated | you want rows visible early and will re-run on a gate failure |

In `streaming`, duplicate rows land. The run is still marked
`FAILED_BLOCKER` in `validation_runs`, so recover with
`--write_disposition=overwrite`. The gate ratio is computed identically in
both modes (the transform publishes `distinct_count` so
`total = valid + dlq` stays equal to the rows generated).

### 7c. New milestones to read out

| Milestone | Reads as |
|---|---|
| `freetext_pool_store_absent` | this run will rebuild pools per worker process |
| `vllm_spawn_lost_race` | a duplicate spawn adopted the healthy server — **benign**, and previously fatal |
| `embedder_cuda_no_room` | the GPU was too full for the embedder; it used CPU instead of OOMing |
| `embedder_cuda_oom_fallback` | the move to CUDA OOMed and degraded to CPU rather than failing the bundle |

Expect `dofn_setup_retry` and CUDA OOM occurrences to be **0** now. If either
is non-zero, the cascade is back and the run is worth a postmortem.
