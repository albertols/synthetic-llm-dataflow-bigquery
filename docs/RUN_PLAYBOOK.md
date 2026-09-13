# Run playbook — GPU verdict, defaults, Dataflow options, recipes, pass criteria

The operational companion to [`DEPLOYMENT_PREREQUISITES.md`](DEPLOYMENT_PREREQUISITES.md)
(what must exist before a run) — this doc is about the run itself: which GPU to
pick, the current flag posture, which Dataflow launch knobs matter, how to hold
L4 capacity when `europe-west3` is stocked out, how to read worker logs, what
passing looks like, and how to turn a finished job into a report. Everything
here reflects the `composer/synthetic_beam_bigquery.py` DAG and the
`packages/sdfb-beam` CLI on `master` — read those files if a flag looks stale.
Campaign history (the WS8 R-series verdicts and remediation waves) lives in
ADRs 0023–0033 and the design docs they cite, not here.

---

## 0. From-scratch reset (before a cold campaign)

Deploy/align the stats table first (the schema carries
`sample_rows`/`stats_tier`/`profiler_version`):

```bash
# new table:
bq mk --table "${PROJECT}:synthetic_rag.source_table_stats" \
  config/bq_schema/synthetic_rag/source_table_stats.schema.json
# pre-existing table (additive, no data rewrite):
bq update "${PROJECT}:synthetic_rag.source_table_stats" \
  config/bq_schema/synthetic_rag/source_table_stats.schema.json
```

Then wipe state so every store path is exercised cold:

```bash
for t in \
  "synthetic_data.<LANDING_A>" "synthetic_data.<LANDING_B>" \
  "synthetic_data_quality.dlq" "synthetic_data_quality.validation_runs" \
  "synthetic_rag.freetext_pools" "synthetic_rag.rag_chunks" \
  "synthetic_rag.source_table_stats"; do
  bq query --use_legacy_sql=false "TRUNCATE TABLE \`${PROJECT}.${t}\`"
done
```

Truncating `freetext_pools`/`rag_chunks`/`source_table_stats` resets every
digest — **all first runs are cold** (full pool ladder + embed + stats
write). That is the expensive part: sequence warm runs immediately after
their cold twin.

Preflight (the stats-table step must report OK, not ACTION):

```bash
python scripts/deployment_prerequisites.py --project ${PROJECT} \
  --source-stats-table ${PROJECT}.synthetic_rag.source_table_stats
```

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

**Conclusion: there is no Gemma-on-T4 configuration.** T4 runs pair with an
fp16-safe model; Gemma fidelity runs require an L4.

**L4 availability.** `europe-west3-a` and `europe-west3-b` both carry L4
(`g2-standard-*`) capacity; `g2-standard-4`, `-8`, `-12`, and `-16` all ship
exactly **one** L4 — the accelerator count doesn't scale with machine size
inside the `g2-standard` family, only vCPU/RAM does. `europe-west4` is the
capacity fallback region when both `europe-west3` zones are stocked out (see
§4 for what to do about a stockout instead of switching region, which trades
away the EU-residency alignment in
[ADR 0004](adr/0004-europe-west3-region.md)).

**T4-safe registry model.** For T4 runs, pair `gpu=t4` with a model that is
fp16-safe on Turing, not a Gemma checkpoint. The registry entry is
`qwen3_4b_instruct_2507` in [`config/models.yml`](../config/models.yml) —
Apache-2.0, `min_compute_capability: "7.5"`, FP16, guided-JSON clean. Point
the `SDFB_MODEL_URI` Composer Variable at its `gcs_uri` before a `gpu=t4`
run. If staging a qwen2.5 build: remember the local-dir rename before GCS
staging, and a missing `special_tokens_map.json` is expected and handled.

---

## 2. Run defaults + universal pass criteria

Common to every vLLM run: `client_type=vllm`, qwen + `vllm_dtype=float16`
(the DAG default — qwen ships bf16, T4 is CC 7.5), `vllm_max_model_len=8192`
(it caps the KV-cache allocation; uncapped Qwen3-2507 at native 262K context
needs a 36GiB KV cache and the T4 EngineCore exits 1 at startup),
`identity_cols`/`pk_cols` set to the table's real columns via
`config/relationships/`, fresh Airflow trigger per run (salted `run_id` is
derived). `batch_size=1000` for 1M/10M rows; the DAG's `16` default is sized
for smokes. Params not listed = DAG defaults, which already carry the current
posture: `source_stats=sample`, `freetext_expansion=identifiers`,
`prompt_constraints=on`, `build_pool_layer=true`, `build_rag_layer=true`,
`uniqueness_mode=exact`, `pool_seed_strategy=centroid`.

**Universal pass criteria — every vLLM run:**

1. `model_client_setup_start/done`, `model_pull_*`, `vllm_spawn`, `vllm_ready` all present in worker logs.
2. Zero `freetext_llm_fallback` milestones (a vLLM run that falls back now crashes instead).
3. `copy_ratio < 0.3` on every non-constant STRING column with `source_distinct > 100` (probe step 3).
4. `validation_runs.run_id` unique per Dataflow job (salted suffix visible).
5. `pk.duplicate` present in `dlq_by_rule` if and only if PK collisions occurred; PASSED requires ~0.
6. `b1_embed_done rows=` equals `reference_rows_limit` (10000), not the full source count.
7. Check the Dataflow job state directly (`gcloud dataflow jobs describe <job_id>` or the Dataflow console) — the Composer DAG launches the job with `wait_until_finished=False`, so a strict-mode worker crash fails the Dataflow job itself while the Airflow task still shows success. Airflow green is not proof of a healthy run.
8. `validation_runs.valid_count` must equal `num_rows` requested. A shortfall — even on a `PASSED` row, since the BLOCKER gate only checks a *ratio* — means whole batches were lost to `engine_failure` and never replaced; PASSED is not proof the run actually produced the row count it was asked for.

The extended gates (stats contract, privacy, FK integrity, marginals) are §8.

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
  DAG's `machineType` ternary, and `gpu=t4` maps to `n1-standard-8`. The `-4`
  vs `-8` trade-off is therefore informational, relevant only if someone
  edits that ternary: both sizes carry exactly one L4, so the choice is pure
  headroom, not GPU count — `-8` gives the CPU-side steps (BQ read, Pandera
  validation, `sdgx` fit for B.2, the Beam harness alongside vLLM) more
  vCPU/RAM to avoid becoming the bottleneck next to the GPU, at roughly
  double the non-GPU cost. The hardcoded `-8` is the right default for
  fidelity runs; don't downgrade it without a measured reason.
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
- **Initial worker count — `initial_workers` (ADR 0034).** Dataflow
  starts a batch job below `maxWorkers` and scales up on backlog: the
  2026-08-29 R6 pair launched on 2 workers and reached 4 only ~4 min into
  the first generate stage (3 harness boots at 15:01 / 17:08), so
  C_TABLE ran its first 8 minutes at ~2.5k rows/s against a 10.5k rows/s
  steady state. Pass `initial_workers=4` (= `maxWorkers`) on every
  10M-row trigger; leave it empty for smoke runs. The launcher pins it
  through `WorkerOptions.num_workers` (`run_pipeline.configure_pipeline_options`),
  the same channel as `disk_size_gb`; `run_e2e.sh` tiers carry it as
  `job.num_workers` (`R7`) and as the template param `initial_workers`.
- **Autoscaling mode — `autoscaling=auto|throughput|fixed` (ADR 0034
  D9).** A fleet you sized by hand should stay that size: `auto`
  (default) pins Dataflow's `autoscaling_algorithm=NONE` exactly when
  `initial_workers` is given, `fixed` pins it and refuses to launch
  without `initial_workers`, `throughput` keeps THROUGHPUT_BASED. The
  2026-09-07 15:53 and 09-08 multi runs scaled 1 → 4 for the parent, fell
  to 1–2 workers through the parent's load and the child's pool phase,
  and re-provisioned VMs for the child — ≈ 4 min per job. `R7`/`R7m`
  now carry `initial_workers=4` + `autoscaling=fixed`; an explicit Beam
  `--autoscaling_algorithm` always wins.
- **SDK-container topology — `sdk_containers=single|multi` (ADR 0034).**
  `single` (default) is the pin described in the next bullet. `multi`
  lifts it: Runner v2 starts one SDK process per vCPU, so the generate
  stages — pure-Python DoFns that shared ONE interpreter per worker on
  the R6 pair (8 harness threads, ~1 of 8 vCPUs busy, ~10.5k rows/s
  fleet-wide) — get eight interpreters per worker. The vLLM client makes
  that safe: a cross-process spawn mutex (a bound loopback port,
  `spawn_lock_port` = 8001) serializes the pull → spawn → ready window
  across processes, every other process binds to the one server through
  the existing reuse probe, and teardown keeps the server alive
  (`vllm_server_kept_alive`). The embedder loads lazily, so the seven
  non-spawning processes hold no CUDA context. `multi` is an
  **acceptance experiment**, not yet the default: run `R7m` (or trigger
  the DAG with `sdk_containers=multi`) and read `sdk_container_topology`,
  `vllm_spawn_lock_acquired` / `vllm_spawn_lock_wait` (exactly one
  acquired per worker), `engine_shared holders=`, and the generate
  stage's `batch_done` rate before promoting it. **Measured 2026-09-07
  (R7 pair, 10M rows/table):** `single` ≈ 76.5 min, `multi` ≈ 50.5 min
  — 10k-row batches in 5.6–6.7 s instead of 26–29 s, one spawn lock per
  worker, no CUDA OOM, the fleet on 1–2 workers for most of the job
  because `initial_workers` was left empty. Pass `sdk_containers=multi`
  **and** `initial_workers=4` (+ `autoscaling=fixed`) together for a
  10M run. Under `multi` the cold RAG population embed stays on the GPU
  but is bounded to `rag_embed_shards` (2) processes, and the pool
  branch waits for it before its first LLM call spawns vLLM (ADR 0034
  D8 rev. 2 — rev. 1's CPU embeds starved the model pull and the vLLM
  init on 2026-09-08: 598 s ignition, a 15-min population stage). The
  one-figure comparison of both topologies inside a worker is
  `docs/designs/assets/sdk-containers-topology.png` (design doc §5).
- **NVIDIA MPS — evaluated, not enabled (ADR 0034).** Dataflow's
  `worker_accelerator=…;use_nvidia_mps` shares one CUDA context across
  SDK processes and is meant for `RunInference` with `model_copies > 1`
  on one GPU. This pipeline runs ONE vLLM server per worker reached over
  HTTP from every process; the only other GPU tenant is the cold
  population embed, bounded to two processes that finish before vLLM
  spawns, so MPS has no work to schedule and would only add a daemon in
  front of the driver. Do not
  add it without a design change that puts a second model process on the
  card.
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
- **`maxWorkers` for smoke-scale runs.** The DAG currently hardcodes
  `maxWorkers: 4`. For 1000-row replay checks with `batch_size=16`
  (~63 batches), **1–2 workers is the right target** — the per-batch LLM call
  dominates wall time and more GPU workers just means more idle vLLM
  cold-starts and more GPU spend for no throughput gain. Treat the hardcoded
  `4` as a ceiling, not a target — Dataflow won't launch more workers than
  the graph can use; requesting fewer workers up front additionally reduces
  contention under an L4 stockout (§4).

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
Dataflow job id, under the **local, gitignored `runs/` dir**: put the sample
CSVs at `runs/<JOB_ID>/*.csv` and write the metrics JSONs there as **working
files** — the bundle export folds them (plus any crosscheck/stats-diff
markdown) into `real/` + `oss/` and prunes the parent-level duplicates,
leaving the CSVs as the only parent-level artifacts (the finished-folder tree
is drawn in the E2E prompt's "Per-deployment artifact folder" section).

**1. Offline analysis** (table-agnostic; computes duplicate ratio, repetition,
singularity, sparsity, identity-column uniqueness, cross-sample Jaccard —
`--batch-size` enables the run-length-equals-batch-size replay flag,
`--identity-cols` scopes the per-row-uniqueness check to PK/UUID columns):

```bash
python scripts/e2e/e2e_validation_analysis.py \
  $(for c in <CSVS>; do echo --csv $c; done) \
  --schema <SCHEMA> --pk <PK> --identity-cols <IDENTITY_COLS> \
  --batch-size <BATCH_SIZE> \
  --out runs/<JOB_ID>/e2e_validation_metrics.json
```

**2. Live GCP cross-validation + Dataflow observability** (ADC-authenticated;
`--run-id` lets the probe correlate a job against the salted-run-id
replay-difference check, `--engine-label` stamps a human-readable
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
  --out runs/<JOB_ID>/e2e_gcp_metrics.json
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
  --metrics gcp=runs/<JOB_ID>/e2e_gcp_metrics.json \
  --metrics offline=runs/<JOB_ID>/e2e_validation_metrics.json \
  --metrics stats_diff=runs/<JOB_ID>/stats_diff.json \
  --metrics freetext_crosscheck=runs/<JOB_ID>/freetext_crosscheck_metrics.json \
  --doc stats_diff=runs/<JOB_ID>/stats_diff.md \
  --doc freetext_crosscheck_report=runs/<JOB_ID>/freetext_crosscheck_report.md \
  $(for c in <CSVS>; do echo --csv $c; done) \
  --report output/end_to_end_validation_report_YYYY_MM_DD_HH_MM.md \
  --out-root runs \
  --no-redact-values \
  --prune-inputs
  # writes runs/<JOB_ID>/{real,oss}/ and deletes the ingested
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
  --dir runs/<JOB_ID>/real \
  --dir runs/<JOB_ID>/oss
```

Only the `oss/` folder produced by step 3 is shareable outside the team; keep
`real/` (and its `mapping.json` decode key) local.

**Promotion.** When a release will cite the run, copy the finished bundle
into the committed evidence layer the release Action discovers:

```bash
cp -R runs/<JOB_ID> docs/releases/<version>/evidence/<JOB_ID>
```

---

## 6. Stores, flags, and experiment hygiene

### 6a. Free-text pool store (optional, provision once)

**The pool store is OPTIONAL.** Without it the pipeline rebuilds pools in
every worker process's `setup()` — the 19.1 GPU-hour / 68-minute behaviour
the store exists to remove ([ADR 0020](adr/0020-freetext-pools-as-persisted-artifact.md)).
It is a performance opt-in, not a prerequisite: `deployment_prerequisites.py`
step 10 reports a missing table as **SKIP, never ACTION**.

```bash
bq mk --table \
  "${PROJECT}:synthetic_rag.freetext_pools" \
  config/bq_schema/synthetic_rag/freetext_pools.schema.json
```

No partitioning: the table holds one row per
`(reference_digest, model_uri, column)` — a handful per run.
`--build_pool_layer=true` against a missing table fails at launch with the
`bq mk` line above rather than a cryptic `NotFound`; the read path degrades
silently and correctly on its own.

A run that rebuilds pools because `--freetext_pools_table` was never passed
announces itself: grep for `freetext_pool_store_absent` (WARNING) before
blaming anything else — a 2026-07-26 run spent 26 of its 53 minutes on
exactly this.

### 6b. Target-table bootstrap flags

- **A missing `--ddl_uri` is not fatal** — the launch falls through to live
  `INFORMATION_SCHEMA` extraction (live-first resolution, ADR 0027 D2) and
  logs `ddl_uri_miss_fallback`. A *corrupt* pin still fails loudly, by
  design.
- **The landing table is not created unless you ask.** Pass
  `--create_if_not_exists=true` (`derive_bq_load_schema` + `CREATE_IF_NEEDED`).
  The default stays `false` deliberately: flipping it widens blast radius on
  every run.

### 6c. `--uniqueness_mode`

| Value | Landing | Duplicates | Use when |
|---|---|---|---|
| `exact` (default) | after ONE full-row shuffle barrier (PK/identity resolved from key-only groups, [ADR 0034](adr/0034-generation-throughput-single-barrier-shared-engines.md)) | diverted to the DLQ | you need duplicates removed |
| `exact_chained` | after 3 chained full-row barriers (row digest → PK → identity, the pre-ADR-0034 path) | diverted to the DLQ | A/B against `exact` only — same envelopes and counts, ~3x the shuffle bytes |
| `streaming` | **incremental, as generated** | **land**, rate measured and gated | you want rows visible early and will re-run on a gate failure |

`exact` and `exact_chained` divert the same rows with the same `rule_id`s
and exact counts; `exact` keeps the row with the smallest digest per
collision (deterministic) where the chain kept an arbitrary one. The
2026-08-29 R6 pair measured the chain at ~26 of 94 minutes per job
(123 GB through Dataflow Shuffle, `resource exhausted` retry storms).

In `streaming`, duplicate rows land. The run is still marked
`FAILED_BLOCKER` in `validation_runs`, so recover with
`--write_disposition=overwrite`. The gate ratio is computed identically in
both modes (the transform publishes `distinct_count` so
`total = valid + dlq` stays equal to the rows generated).

### 6d. Do not confound experiments

When measuring any pool/seeding/stats arm: arms that *build* pools must each
start from an **empty** `freetext_pools` for their digest, or the persisted
pool masks the effect being measured. Clear with:

```bash
bq query --use_legacy_sql=false \
  "DELETE FROM \`${PROJECT}.synthetic_rag.freetext_pools\`
   WHERE reference_digest = '<digest>'"
```

The same rule applies to prompt/constraint edits: the pool skip-key is the
**reference digest** (row content), which a description-only edit does NOT
change — a warm store hit would replay pools built with the OLD prompts and
mask the edit. Exclude `COL_047`/`COL_048` (binary-character columns, known
special case) from every comparison.

---

## 7. Milestone log dictionary — reading worker logs

```bash
grep -o 'name=[a-z_]*' worker_logs.jsonl | sort | uniq -c | sort -rn
```

Store / pool lifecycle:

| Milestone | Reads as |
|---|---|
| `freetext_pool_store_hit` | pool read, ladder skipped — the point of the store |
| `freetext_pool_store_miss` | store attached but empty for that column |
| `freetext_pool_store_error` | store unreachable; run degraded to building (not fatal) |
| `freetext_pool_store_absent` (WARNING) | no `--freetext_pools_table` passed — this run rebuilds pools per worker process |
| `pool_build_skipped` | driver found this digest+model already populated |
| `pool_branch_setup_done` / `pool_branch_emitted` | the build branch ran |
| `freetext_pool_built target=` | per-column pool landed; compare targets across stats tiers for the exact-distinct lift |
| `freetext_pools_warm columns=` | every pool came from the persisted store / process cache — the designed warm path (ADR 0020/0033), not idle hardware |
| `engine_shared holders=N` | this generate DoFn reused the process's engine (ADR 0034 D2): N holders share one build — 4 builds per table on the R7 single run instead of 32 |
| `source_values_arrow_fallback error=` / `source_values_storage_api_disabled` | the Storage Read API attempt failed (`PermissionDenied` = grant `roles/bigquery.readSessionUser`); REST paging serves the process. A following `*_source_filter_error` means the fetch itself failed — the ADR 0023 filters are INACTIVE, treat the run's pools as tainted |
| `vllm_spawn_lock_acquired` / `vllm_spawn_lock_wait` / `vllm_server_kept_alive` | `sdk_containers=multi` (ADR 0034 D6): exactly one process per worker acquires the spawn window; waiters bind to its server via the reuse probe; the server outlives the client that spawned it |
| `sdk_container_topology topology=` | launcher: `single` (one SDK process per worker) or `multi` (one per vCPU) — decides the cross-process vLLM mode and the CPU population embed |
| `llm_route_unused` (WARNING) | this setup has NO LLM-derived pool at all (every column expandable / typed / binary) — GPU workers idle; plan a CPU-only rerun (ADR 0027/0033) |
| `freetext_pool_skipped_expandable` | the column draws from its shape mix — no pool built, by design (ADR 0026) |
| `freetext_pool_ladder_retried column= error=` (WARNING) | a ladder thread hit a transient client condition and was rebuilt in-process; expect `freetext_pool_built` right after — a second failure raises (ADR 0033) |
| `freetext_pool_format_collapse column= attempts= parsed= format_rejected=` (WARNING) | two consecutive full-yield rounds with zero in-format values — structural mismatch; the shape fallback follows; check `prompt_constraint_example_off_format` for the cause (ADR 0033) |
| `prompt_constraint_example_off_format column= example_len= gate_lengths=` (WARNING) | a clause `examples` entry fails the column's own format gate — the model WILL echo it; fix the example in the DDL description (ADR 0033) |
| `freetext_pool_length_clamped column= clamped= max_len=` | prose candidates past a fixed-width source ceiling were truncated before novelty rejection (ADR 0033) |
| `freetext_pool_binary_fallback` | control-char column skipped the LLM ladder for the template fallback (ADR 0027) |
| `freetext_pool_source_filter size=` (+ `_absent`/`_error`) | the column's FULL source domain is in the pool rejection set (ADR 0023); absent/error = sample-only rejection — check copy_fraction post-run |
| `pool_taint_rebuild` (WARNING, launcher) | warm pools overlapped the live source → deleted + rebuilt clean (expected ONCE per tainted pre-ADR-0023 digest) |
| `pool_taint_check_error` / `pool_taint_delete_error` | preflight could not verify/clear — warm pools kept, verify copy_fraction post-run |

Stats / DDL / build provenance:

| Milestone | Reads as |
|---|---|
| `source_stats_written` / `source_stats_skipped` | stats landed / versioned skip key hit (digest + `profiler_version` + achieved tier) |
| `source_stats_exact` | Tier-2 aggregate scan merged; `source_distinct` threaded to pool sizing |
| `source_stats_exact_failed` (WARNING) | exact scan failed; run degraded to sample tier and stays retryable — investigate, not fatal |
| `temporal_range_clamped` | a temporal column's floor hit now−10y; its decile vector was clamped too |
| `generation_plan` (detail) | per-column route + null/empty/shapes/constraint/expandable — the first thing to check when a column misbehaves |
| `prompt_constraints_found` (launcher + worker) | the `llm_prompt_constraint` clauses actually fetched from the DDL: rendered clause + `clause_sha12` per column; diff `clause_sha12` across launches to verify a Terraform edit landed |
| `prompt_constraint_unknown_keys` (WARNING) | a description carries a typo'd/newer constraint key — names the `column=` |
| `build_info commit=` (launcher + workers) | the image's git commit — compare builds BEFORE comparing runs (ADR 0027) |
| `ddl_live_extracted` | the LIVE `INFORMATION_SCHEMA` schema is what this launch generates from — BigQuery metadata edits always take effect |
| `target_metadata_overlaid constraint_columns=` / `target_metadata_unavailable` (WARNING) | constraints + contract come from the LANDING table's descriptions; unavailable = NO constraints this run |
| `ddl_live_extract_failed` (WARNING) → `ddl_loaded_from_uri fallback=True` | live extraction unreachable — OFFLINE mode, the pin's (possibly stale) constraints/contract apply |
| `ddl_pin_drift` (WARNING) / `ddl_pin_fresh` / `ddl_pin_check_error` | the `--ddl_uri` pin vs live — drift means the OFFLINE FALLBACK is stale; re-extract before the next air-gapped day |

Engine / GPU lifecycle:

| Milestone | Reads as |
|---|---|
| `vllm_ready` | should appear **once** on a cold run, **never** on a warm one |
| `vllm_spawn_lost_race` | a duplicate spawn adopted the healthy server — **benign** |
| `vllm_unfittable_wait` (WARNING) | VRAM transiently short — in-process re-measure instead of a bundle retry (window 12 × 20 s since ADR 0033) |
| `embedder_cuda_no_room` | the GPU was too full for the embedder; it used CPU instead of OOMing |
| `embedder_cuda_oom_fallback` | the move to CUDA OOMed and degraded to CPU rather than failing the bundle |
| `identifier_source_filter size=` (+ `_absent`/`_error`) | an identifier column's FULL source domain feeds its mask table + novelty rejection (ADR 0025/0026) |
| `numeric_source_filter size=` (+ `_absent`/`_error`) | an identity-like INT64 column's full domain feeds the draw-time collision scrub (ADR 0026) |
| `numeric_source_rejected collisions= nudged= redrawn= unresolved=` | per-column scrub outcome (first batch); `unresolved > 0` = fully dense neighborhood, read with the k-anon exemption in mind |
| `numeric_kanon_filter size=` (+ `_absent`/`_error`) | the scrub's keep-set from SOURCE frequencies (HAVING COUNT ≥ 10); absent = sample-heuristic fallback (ADR 0027) |
| `dofn_setup_retry` | expect **0**; non-zero means the setup cascade is back and worth a postmortem |

Relational:

| Milestone | Reads as |
|---|---|
| `relationships_example_skipped uri= files=` (launcher) | documentation samples (`example_*.yaml`, `*.example.yaml`) found next to the real models and left out of the scan — name a sample file directly to load it |
| `relationships_loaded` / `relationships_absent` (launcher) | which model FILES this launch read, their models, table count and sha (ADR 0032); absent = every table generates alone |
| `relationship_model` (launcher AND every worker; **WARNING** when a relational launch enforces 0 edges) | the whole model at a glance — tables with PK/identity, every edge as `-->` enforced / `..>` documented, `[DISABLED — detached]` tables, the generation waves, and the FILE it came from. Pipes and arrows only, launcher and worker alike — no mermaid in any log (2026-09-10); `scripts/relationships/card.py --mermaid` renders the diagram |
| `relational_e2e landing= pk= identity= fk_edges= constraints=` + one `relational_fk_edge cols= ref= parent_landing= key_tuples= active=` per edge (worker, once per plan) | did the whole relational contract reach this run — one line each, replacing the indent-2 JSON blocks that were 40 % of the 2026-09-09 worker logs |
| `fk_key_pool_bound columns= key_tuples= weighting= null_fraction=` | one per enforced edge (ADR 0031). `weighting=child_marginal` = the IPF fit ran; `uniform` = no overlap between the child's sample and the parent's keys — check the edge is the one you meant |
| `fk_key_pool_capped cap=` (WARNING) | the parent holds ≥ the edge's side-input cap of distinct keys — the child references a uniform sample of them. The cap is 100k unless the child's PK contains the FK, then preflight sizes it up to 1M (ADR 0035) |
| `pk_capacity_tight capacity= num_rows= expected_pk_duplicate_share=` (WARNING, launcher) | the PK tuple draws at random from a bounded space (FK-bound / categorical members) and 1–20 % of rows are expected to divert as `pk.duplicate` — under the gate, but the table lands fewer rows than requested (ADR 0035). Over the gate the launch stops at preflight P4 naming the largest gate-safe `--num_rows` |
| `identity_constraint_owned` | the named identity columns are generated from their DECLARED CLAUSE (Tier P/B), not UUID synthesis (ADR 0028 amendment) |
| `fk.orphan` in `validation_runs.dlq_by_rule` | rows that referenced a non-existent parent. Non-zero = a generator regression (the draw is joint by construction) — a BLOCKER, not a tolerance |
| `fk_edge_widened table= ref= via= added=` (launcher) | the registry widened a parent's edge with inherited columns pinned by a child that references the same columns in both tables — no model edit needed (ADR 0036 rev) |
| `fk_edge_role edge= role=driving\|implied\|independent\|conditional\|external overlap=` (launcher + worker preflight) | the role of EVERY enforced edge: which edge a child is generated FROM (`driving`), which are satisfied by construction (`implied`), which ride the side-input key pool (`independent` — a star-schema dimension, no column shared with the driving edge), which are joined on their shared columns (`conditional` — `overlap=` names them), and which point outside the launch (`external`). ADR 0036/0037 |
| `fk_driving_edge_defaulted table= edge= hint='mark drives: true to choose'` (launcher, **WARNING**) | the child has several enforced parents, none marked `drives: true` and no ancestry between them, so the FIRST DECLARED edge drives (ADR 0037 ruling A). The launch is correct either way — mark the edge you meant, or reorder the `fk:` list, if it is not this one |
| `fk_edge_overlap_external table= edge= other= overlap= note=` (launcher, **WARNING**) | fix wave F3: one line per overlapping PAIR with an EXTERNAL end — driving∩external, external∩external, or external∩any non-driving edge (`edge=` is always the external one, `other=` what it clashes with). The launch does NOT stop: the last edge written keeps the shared column, so `other`'s tuple may not exist in its own parent — bring the parent into the launch to resolve it. Fix wave G3: the report reads the MODEL, not the resolved edge roles, so it fires in a SINGLE-TABLE launch too — the shape where it matters most, since a denormalised child's parents are all external |
| `fk_nullable_schema_mismatch table= edge= landing= generation= reason= pk=` (launcher, **WARNING**, once per conditional edge) | fix waves A4 + F2 + G2: `reason=` is `declared_pk`, `schema_mode_mismatch`, or both comma-joined — the landing/generation schemas disagree on this edge's `rest` nullability (comma-joined per-column modes), and/or a `rest` column named in `pk=` sits in the PRIMARY KEY the run ENFORCES, which rejects a NULL regardless of mode. That PK is the UNION of the relationship model's `pk:` (ADR 0032, what the run enforces) and `TableSchema.primary_keys` (the BQ table constraint in `_ddl.json`, what the record model validates against) — a NULL in either is fatal, so reading only one leaves a window where the edge is called nullable and every NULL-filled row is dropped inside the engine's `except Exception: continue`. Either way the edge is treated as NON-nullable, so an unmatched key diverts as `fk.unmatched` instead of NULL-filling a row the record model would silently reject |
| `fk_candidate_request_unbounded table= candidate_cap= conditional_edges= tuples_per_request= ceiling=` (launcher, **WARNING**, per table) | fix wave G5: `--fk_candidate_cap × conditional edges` exceeds the 100k per-request candidate ceiling ON ITS OWN, so the `keys_per_batch` bound has floored at ONE key per request and bounds the request no further. The cap is deliberately NOT clamped (fix wave F1 reverted that — clamping collapsed the co-parent choice to a point mass), so this is the signal to lower `--fk_candidate_cap`: past the candidates a co-parent actually holds per shared value it buys no per-key variety, only request size |
| `relational_fk_edge … mode=fanout\|implied\|side_input\|conditional overlap=` (worker, per edge) | the DAG path EVERY edge actually took this run — one line per edge, including the driving one (`mode=fanout`) and an `implied` one. `side_input` = the ADR 0031 key pool: an `independent` edge, the pre-ADR-0037 default for legacy metadata, OR an `external` edge — its parent is outside the launch, so `fk_edge_metadata` gives it no `mode` key at all and the worker falls back to `side_input` by design, not a defect. `conditional` = the co-partitioned join, with `overlap=` naming the shared columns. Compare each line's `mode=` with the launcher's `fk_edge_role role=` for that edge: `role=independent`, legacy metadata, and `role=external` all legitimately show `mode=side_input`; any OTHER role/mode pairing (e.g. `role=driving` without `mode=fanout`) is a wiring defect (ADR 0037) |
| `fk_fanout_cache_unavailable table= op= error=` (launcher, WARNING) | the optional `fk_fanout_stats` cache could not be read/written (missing table, permission, transient) — the launch measured without it (ADR 0036) |
| `fk_fanout_measured edge= parents= children= key_values= orphan_keys= mean= p50= p95= max= zero_share= source=measured\|cache` (launcher) | the SOURCE ratio the driven child reproduces; `cache` = read from `fk_fanout_stats` instead of re-scanning. Every figure is over the histogram, which counts rows per child KEY VALUE, so `mean` can never exceed `max`; `key_values` and `orphan_keys` say how much of the child's key space the parent actually covers |
| `fk_fanout_source_orphans edge= child_tuples= parent_tuples= orphan_keys= matched_share=` (launcher, **WARNING**) | the source child holds key values its parent does not, so the zero bucket is unmeasurable and the child generates from the matched share of its key space only. A low `matched_share` means the driving edge is suspect — check the edge names the columns you meant |
| `relational_single_job … rows_detail=<name>:<rows>,…` (launcher) | derived row count per table — roots take `--num_rows`, driven children derive from their parent's landed keys and the measured fan-out |
| `fanout_bound driving_cols= cells= exact_cells= mean_fanout= conditional= candidate_cap=` (worker, once per engine build) | the engine bound the driven child's recipe; `exact_cells=True` = the PK-completing cells alone must key the child (drawn without replacement, a weighted permutation) — `False` = an unbounded PK member keys it instead, cells follow their measured weights WITH replacement and are NOT part of the joint walk; `conditional=<n>` = how many conditional edges ride with the keys and `candidate_cap=` is `--fk_candidate_cap` VERBATIM (fix wave F1 reverted an A3 clamp to the measured max fan-out) |
| `fanout_rows_capped requested= emitted= capacity=` (worker, **WARNING**, once per worker process PER DRIVEN TABLE) | fix waves A1/E2/E3/G1/G4: `capacity` is the product of the dimensions that genuinely bound the PRIMARY KEY — the cell count ONLY when `exact_cells`, times the actual candidate count (or 1, a NULL fill) of each conditional edge whose `rest` supplies a PK MEMBER. An edge outside the PK distinguishes no child, so it multiplies nothing (before G1 it did, and the extra children landed as PK duplicates with `shortfall == 0`). Capping applies IF AND ONLY IF `exact_cells`; an inexact PK never caps, and its candidate digits wrap instead while its cells keep drawing independently from their weights. The guard is keyed on the landing table (`table=` comes from the ambient milestone scope), so every driven table of a single-job relational run reports its own capping — not just the first one to cap (both engines' call sites are pinned, fix wave G4). Raise `--fk_candidate_cap` or fix the `pk:` |
| `batch_unmatched batch_id= keys_dropped=` (worker) | driving keys removed from a batch BEFORE generation because a NON-nullable conditional edge had no candidate for them (ADR 0037 ruling B) — each one is also a `fk.unmatched` DLQ envelope |
| `fanout / candidates_dropped_null` (Beam counter) | conditional-parent rows discarded because a SHARED (join-key) column was NULL. Non-zero means the parent landed NULLs in the columns the join keys on — expect 0 on a PK-declared parent |
| `fanout / keys_unmatched` (Beam counter) | driving keys dropped for a non-nullable conditional edge with no candidate. Read it next to `fk.unmatched` in `dlq_by_rule`: the counter counts KEYS, the rule counts their expected ROWS |
| `fk.unmatched` in `validation_runs.dlq_by_rule` | rows never generated because the conditional parent held no candidate for their shared value, weighted by each dropped key's expected rows (ADR 0037 ruling B). Unlike `fk.orphan` this is an INPUT fact, not a generator regression — a branch the source genuinely lacks. Non-zero with a source you expect to be complete = check the edge's columns and `fanout / candidates_dropped_null` |
| `model_adjustments count= tables=` + the multi-line `MODEL ADJUSTED` block (launcher, **WARNING**, once per launch) | ADR 0038: this run did NOT generate with the model on disk. The block states, per table, what the model declared, what the full source measured, what was dropped, and the consequence — plus the SOURCE key-repeat share the landing table has to match. A run with this block is never a clean run; read it before reading anything else |
| `model_adjusted table= change=pk_dropped declared= measured= consequence= source_repeat_share=` (launcher, **WARNING**, one per adjustment) | the greppable one-line form of the same fact. `declared` is the `pk:` the model carried; `measured` is what the full source proved — since fix J, the DECLARED PK's own measurement (distinct key tuples over rows, the largest group) and why the key cannot tell those rows apart; `source_repeat_share` is `1 - key_tuples/rows` over **the declared PK**, the same columns `pk.duplicate` is counted over. It is absent only when the conflict was proven by the cell-capacity ladder instead, with no measurement of the key itself — then the banner says "not measured" rather than printing a share over other columns. Fix it permanently by pasting the emitted YAML into `config/relationships/` — or re-launch with `--on_model_conflict=stop` to refuse instead |
| `source_pk_measured table= pk= rows= key_tuples= max_rows_per_key= repeat_share= source=measured\|cache\|histogram` (launcher, one per driven child with a `pk:`) | fix J: the DECLARED PK, measured on the SOURCE child — the evidence P4's verdict rests on. `source=histogram` means the declared PK IS the driving edge, so the fan-out already measured that tuple and **no second scan was paid for**; `measured` is one extra GROUP BY over the PK columns; `cache` is the `fk_fanout_stats` row, which holds both measurements under one key. Compare `repeat_share` with the run's `blocker_failure_ratio`: above it the model is ADJUSTED, at or below it the key is KEPT |
| `preflight_pk_source_repeats table= pk= rows= key_tuples= max_rows_per_key= repeat_share= gate= note=` (launcher, **WARNING** when `repeat_share>0`) | fix J: the declared PK IS a key of this source within the run's BLOCKER gate, so it is KEPT and the repeats that remain divert as `pk.duplicate` like any other table's. A source that is 0.4% dirty must not lose its key. Expect `pk.duplicate` on this table at roughly `repeat_share × rows` — read it against the gate, not against zero |
| `preflight_pk_capacity_below_fanout table= pk= max_fanout= capacity= candidate_cap= note=` (launcher, **WARNING**) | fix J: the ADR 0037 per-key capacity model (cells × `--fk_candidate_cap` per conditional edge) sits below the source's largest fan-out, but the declared PK's own measurement KEPT the key. Reported, never acted on — capacity estimates what a key can represent, the measurement measured it. If `pk.duplicate` overshoots the measured share, raise `--fk_candidate_cap` |
| `model_adjustment_model model= source= uri= tables=` + the YAML body (launcher, **WARNING**, one per adjusted model FILE) | the EFFECTIVE relationship model this run generated with: the declared model with the adjusted tables' `pk:` removed and a comment naming the measurement that removed it. `uri=` is where it was also written (`--staging_location`/`--temp_location` + `/model_adjustments/`), or `(not written)` — the log copy always exists. `model_adjustment_model_unwritten` (WARNING) precedes it when the artifact write failed; the launch continues by design |
| `model_adjustment_repeat_share table= source= landing= delta= tolerance= within_tolerance= excluded_blocker_rules=` (worker, end of run; **WARNING** unless `within_tolerance=True`) | ADR 0038's proof that the copy is faithful: the SOURCE key-repeat share measured at launch against the one the landing table actually reached (`pk.duplicate` over `valid_count + row.duplicate`). Both describe the DECLARED PK since fix J, so the verdict is like-for-like in every case (fix H4's `comparable=`/`note=` are gone with the mismatch that needed them). `within_tolerance=False` means the adjusted table did NOT reproduce its source — a fan-out capped to one child per key lands ≈0.00 against a source ≈0.50. The line is absent when the launch measured no source share for the table. The same figures land in `validation_runs` (`source_repeat_share`, `landing_repeat_share`, `repeat_share_delta`, `repeat_share_within_tolerance`, `excluded_blocker_rules`) |
| `preflight_pk_sample_stop_deferred table= pk= distinct= sample_rows= duplicate_ratio= note=` (launcher, **WARNING**) | fix H2: the 10,000-row sample showed ≥50% duplicate PK tuples, and P5's stop DEFERRED because a full-source measurement is in scope for this table. The verdict is P4's, on the DECLARED PK's own measurement (fix J, `source_pk_measured` above) — it either adjusts the model (the `model_adjusted` row above) or, with `--on_model_conflict=stop`, refuses. Seeing this line with NO `model_adjusted` line after it means the full source repeats the key less often than the run's gate allows, and the sample was the weaker signal |
| `model_adjustment_descendant_rows table= parent= parent_rows= parent_distinct_keys= mean_fanout= derived_rows= unadjusted_rows= note=` (launcher, **WARNING**) | fix H3: this table is driven by a parent whose own `pk:` was ADJUSTED away, so the parent lands REPEATED keys and this table fans out from its DISTINCT ones. `derived_rows` is `parent_distinct_keys × mean_fanout`; `unadjusted_rows` is what sizing it off the parent's rows would have requested — and could not have produced (the 2026-09-12 shape: 220,215 requested against ~109,556 producible). Expect `num_rows_requested` in `validation_runs` to match `derived_rows`, not `unadjusted_rows` |
| `batch_start batch_id= keys=` / `batch_done batch_id= keys= rows= seconds=` (worker) | a key batch: parent keys in, children out (ADR 0036) — replaces `n=`/`rows=` for a driven child's batches |

---

## 8. Extended pass criteria (stats, privacy, FK, marginals)

On top of the §2 universal list:

1. **Stats contract** — every row a campaign wrote:

   ```sql
   SELECT stats_tier, profiler_version, COUNT(*) n,
          COUNTIF(sample_rows IS NULL) missing_sample_rows
   FROM `${PROJECT}.synthetic_rag.source_table_stats`
   GROUP BY 1, 2;
   -- expect the tiers you ran; missing_sample_rows = 0 everywhere
   ```

2. **Privacy gate** — no literal values persisted for high-cardinality
   columns:

   ```sql
   SELECT `column` FROM `${PROJECT}.synthetic_rag.source_table_stats`
   WHERE `distinct` > 50
     AND JSON_VALUE(stats, '$.top_values[0][0]') IS NOT NULL;
   -- expect: zero rows
   ```

3. **Skip-key tiers** — after an exact-tier run over a digest that already
   holds sample-tier rows, the same `(table_fqn, reference_digest)` must
   hold BOTH tiers: the tier-aware `exists()` worked; a single-tier result
   means the earlier rows blocked the exact write.
4. **FK integrity** (enforced-edge runs) — orphan target is zero. Composite
   edges join on the WHOLE tuple (ADR 0031); NULL FK tuples are legitimately
   parentless and excluded:

   ```sql
   -- single-column edge
   SELECT COUNT(*) FROM `${PROJECT}.synthetic_data.<LANDING_B>` c
   LEFT JOIN `${PROJECT}.synthetic_data.<LANDING_A>` p
     ON c.<FK_COL> = p.<PK_COL>
   WHERE p.<PK_COL> IS NULL AND c.<FK_COL> IS NOT NULL;

   -- composite edge: DISTINCT projection of the parent's ref columns
   SELECT COUNT(*) AS orphans
   FROM `${PROJECT}.synthetic_data.<LANDING_B>` c
   LEFT JOIN (SELECT DISTINCT <REF_COLS>
              FROM `${PROJECT}.synthetic_data.<LANDING_A>`) p
     USING (<REF_COLS>)
   WHERE p.<FIRST_REF_COL> IS NULL
     AND c.<FIRST_FK_COL> IS NOT NULL;
   ```

   **Read the launcher first, before the money is spent** (ADR 0032 D6):
   the `relationship_model` card states `N enforced + M documented edges`
   for the whole model. `0 enforced` on a relational launch means every
   edge is `enforced: false` or a parent is `[DISABLED]` — fix
   `config/relationships/<model>.yaml` and relaunch; nothing downstream
   can produce integrity from an edge that draws no keys.
   Cross-check on the worker side: `fk_key_pool_bound` (one per enforced
   edge, `weighting=child_marginal`, `key_tuples=N`) and the absence of
   `fk.orphan` in `validation_runs.dlq_by_rule` — the in-DAG BLOCKER
   measures what the SQL above verifies independently.

5. **Marginals** — numeric and temporal columns' decile overlap vs source
   holds (`stats_diff` `numeric.decile_ks` ≤ 0.2 class); categorical
   entropy_gap ≈ 0 / top1_delta ≈ 0.
6. **Diversity** — freetext `distinct` is not pinned at pool size; shapes
   still conform to the observed `shape_mix`.
7. **thresholds.yml freetext rules** — `freetext.empty_parity`,
   `freetext.distinct_floor` pass; `freetext.copy_fraction` (BLOCKER) at 0.

After each run, the §5 report recipe plus
`scripts/e2e/freetext_crosscheck.py` for the per-column fidelity readout;
interpretation is `e2e-interpreter`'s job as usual.

---

## 9. Launch recipes

### 9a. Constraint acceptance rerun (after `llm_prompt_constraint` edits)

Verifies operator edits to per-column constraint clauses:

```
1. terraform apply           # description edits on the LANDING tables
   -- (synthetic_data.*, the tables YOU own — never the source/lake
   -- tables, whose descriptions are stripped by design) — and that is
   -- ENOUGH to reach the next launch: live-first DDL resolution
   -- (ADR 0027 D2) overlays the target table's descriptions from
   -- INFORMATION_SCHEMA every time.
2. OPTIONAL housekeeping: python scripts/extract_ddl.py … + re-upload the
   ddl_uri pin — only to keep the OFFLINE fallback fresh for air-gapped
   days; `ddl_pin_drift` reminds you when it drifts.
3. DELETE FROM `${PROJECT}.synthetic_rag.freetext_pools` WHERE reference_digest IN (
     SELECT DISTINCT reference_digest
     FROM `${PROJECT}.synthetic_rag.source_table_stats`
     WHERE table_fqn IN ('<A_FQN>','<B_FQN>'))
   -- REQUIRED: the pool skip-key is the REFERENCE digest (row content),
   -- which a description-only edit does NOT change — a warm store hit
   -- would replay pools built with the OLD prompts and mask the edit
   -- (§6d do-not-confound rule). rag_chunks/source_table_stats can stay.
4. Trigger per table: {"num_rows":"1000000","batch_size":"1000"}
5. Verify BEFORE reading any metric: `build_info commit=` matches the
   image you built; `ddl_live_extracted` + `target_metadata_overlaid
   constraint_columns=N` (a `target_metadata_unavailable` launch ran with
   NO constraints; an `ddl_live_extract_failed` → pin-fallback launch does
   NOT carry fresh edits); then `prompt_constraints_found` with the
   expected clause_sha12 per column.
```

Readout ladder (in order, before any crosscheck): launcher
`prompt_constraints_found` (`clause_sha12` changed/newly present per edited
column) → worker `generation_plan.columns_detail` (edited columns on the
expected route) → crosscheck per column (`shape_head_tv` ≈ 0 on
identifier-mask columns — judge mask columns by head TV, not recall;
`shape_recall` lift where a `format`/`examples` edit targeted named
templates).

### 9b. Relational run — parent + FK child (full config recipe)

**Contract (Terraform).** Declare relationships on BOTH tables' **LANDING
twins** (`synthetic_data.a_table` / `synthetic_data.b_table` — ADR 0027
D2: the pipeline reads description surfaces from `--landing_table`, never
the source) — the parent needs its `pk` so `--uniqueness_mode=exact`
gives the child a duplicate-free key pool:

```hcl
locals {
  a_table_contract = jsonencode({
    sdfb = 1
    pk   = ["ACCOUNT_ID"]              # activates pk.duplicate gate + clean parent keys
  })
  b_table_contract = jsonencode({
    sdfb = 1
    pk   = ["MOVEMENT_ID"]
    fk = [{
      cols     = ["ACCOUNT_ID"]
      ref      = "core_banking.a_table"   # dataset-qualified, ALWAYS (P1 stops otherwise)
      ref_cols = ["ACCOUNT_ID"]
    }]
  })
}
```

`ref` names the SOURCE-world `dataset.table`; at run time only the table
name is reused — the read targets `{fk_parent_landing}.{a_table}`
(`io/fk_pools.py::parent_landing_fqn`). Full worked examples + sequence
diagram: [`DDL_CONTRACT_GUIDE.md`](DDL_CONTRACT_GUIDE.md) §6–§8.

**Trigger config (child run — overrides only):**

```json
{"table_fqn": "<TABLE_B>", "num_rows": "1000000", "batch_size": "1000",
 "fk_parent_landing": "${PROJECT}.synthetic_data"}
```

`fk_parent_landing` is `project.dataset` (no table) — the landing dataset
holding the parent's already-landed synthetic rows. It is the activation
switch: contract `fk` **and** this flag must both be present
(`run_pipeline.py::_load_reference_and_preflight`), otherwise the FK
column silently keeps its profiled marginal.

**Preconditions checklist (in order):**

1. The model declares the edge — check it WITHOUT launching:
   `uv run --no-sync python3 scripts/relationships/card.py --table <TABLE_B>`
   prints exactly what the launch will plan (ADR 0032). A broken model
   file is a loud stop; P2 then checks the columns against the real
   schema. Column CONSTRAINTS still come from the landing table's column
   descriptions — expect `target_metadata_overlaid` in the launcher log.
2. OPTIONAL: `extract_ddl.py` re-run against the LANDING table to refresh
   the `ddl_uri` offline-fallback pin (live overlay is authoritative).
3. **Parent landed and non-empty**: `SELECT COUNT(*), COUNT(DISTINCT
   ACCOUNT_ID) FROM ${PROJECT}.synthetic_data.a_table` — run the parent
   first and do NOT truncate its landing table between the parent run and
   the child run. An empty parent = empty `fk_pools` = the FK override
   silently NOT applied (engine skips empty pools) → orphans.
4. Parent-key cap awareness: `io/fk_pools.py` loads ≤ 100k DISTINCT
   parent keys per edge. A 1M-row parent with > 100k distinct keys is
   fine — the child samples a 100k subset, referential integrity holds,
   the orphan query stays 0.
5. Keep DAG defaults: `uniqueness_mode=exact`, `prompt_constraints=on`.

**Milestones to grep (on top of §7):** `relational_contract_loaded
pk=MOVEMENT_ID fk_count=1`, `fk_pool_loaded
parent=${PROJECT}.synthetic_data.a_table values=N` (N ≤ 100k),
`preflight_pk_not_unique_in_sample` (WARNING-only — real sources may
violate an undeclared PK).

**Pass criteria:** §8.4 orphan query = 0 rows, plus `validation_runs`
gate with `pk.duplicate` ACTIVE. **Expected side-effect, not a defect:**
the FK column's marginal is restricted to the parent pool (integrity
beats the child marginal), so `stats_diff` on the FK column may show
entropy/top1 drift — the documented trade-off.

Multi-table alternative: `scripts/run_tableset.py` (parent-first
ordering, dry-run first).

**Driven fan-out (ADR 0036) — three-table chain, `B_TABLE → C_TABLE →
A_TABLE`.** When a child's PK contains its parent's FK (C_TABLE's
`(D_COL_001, C_COL_002, D_COL_018)` inside each B_TABLE account), the
`9b` recipe above does not apply — a random FK-pool draw collides
(ADR 0035); the child must generate FROM its parent's landed keys.
Model edit (`config/relationships/<model>.yaml`, before the launch):
widen C_TABLE's edge to B_TABLE so it carries every column A_TABLE will
need inherited (`cols: [D_COL_001, D_COL_024, D_COL_025, C_COL_009] →
B_TABLE`), and mark A_TABLE's edge to C_TABLE `drives: true` — A_TABLE
also has an enforced edge straight to B_TABLE, which then resolves
`implied` (satisfied by construction through C_TABLE) instead of
stopping the launch as ambiguous. New flags on the launch trigger:
`--fk_fanout_stats_table=${PROJECT}.synthetic_data_quality.fk_fanout_stats`
(empty = measure the source fan-out fresh every launch; set it once the
model is stable so a re-launch pays no BigQuery scan) and
`--driven_uniqueness_mode=streaming` (default — C_TABLE and A_TABLE
skip the ADR 0034 landing-path barrier because their PK is unique by
construction; an identity-bearing driven child keeps `exact` regardless
of this flag). `--num_rows` is passed on B_TABLE only — C_TABLE and
A_TABLE derive theirs (`relational_single_job rows_detail=`).
Milestones and pass criteria: §7's new rows above, plus the same §8.4
orphan query per driven edge and `pk.duplicate` expected **0** (not
just under the gate) on both C_TABLE and A_TABLE. `streaming` mode
MEASURES `pk.duplicate` on its own digest branch (ADR 0036 D6), so that
0 is a reading, not a blank — but read it next to the independent
post-run check below, which counts the landed table rather than the
generated stream.

**`--on_model_conflict=adjust|stop` (ADR 0038, default `adjust`).** A
driven child whose declared `pk:` the FULL SOURCE proves is not a key
(the 2026-09-12 `E_TABLE` stop: its `pk:` IS its driving edge, and the
source carries a median of 2 rows per key value) no longer stops the
launch. **What "proves" means, since fix J:** the DECLARED PK is
measured on the source child — distinct key tuples over rows — and that
share is compared with THIS RUN'S `blocker_failure_ratio`. Above the
gate the key cannot survive generation and is dropped; at or below it
the key is KEPT and `preflight_pk_source_repeats` reports the share. So
the same table adjusts under a 0.2 gate and keeps its key under a 0.3
one: the operator's own threshold decides. The measurement covers a PK
with members outside the driving edge — `F_TABLE` on launch
`…-12600311608685394436`, whose 0.2908 source share reached the BLOCKER
gate because nothing had measured that key at launch time. The launcher DROPS that key from the effective model, prints the
`MODEL ADJUSTED` banner, emits the effective model as YAML, and
generates with the measured fan-out untouched — so the landing table
reproduces the source's key-repeat share. Consequences on the run:

- that table is FORCED to `uniqueness_mode=streaming` regardless of
  `--driven_uniqueness_mode` and of identity columns (nothing may be
  removed), so its `identity.unique` is not measured this run;
- `pk.duplicate` on that table is still MEASURED but **excluded from the
  BLOCKER gate** (`validation_runs.excluded_blocker_rules`). Its
  expected value is NOT 0 — it is the source's repeat share, and
  `repeat_share_within_tolerance` is the criterion instead. Every OTHER
  table's `pk.duplicate` is unchanged and still expected 0;
- the exclusion leaves **both sides** of the ratio (fix H1):
  `observed_blocker_ratio` is `blocker_count` over the rows the run
  GENERATED — `valid_count + dlq_count` minus the excluded rules'
  counts — so every other blocker rule on that table still fails at the
  configured threshold. Read the ratio against `num_rows_requested`,
  not against `valid_count + dlq_count`, when
  `excluded_blocker_rules` is non-empty;
- `repeat_share_within_tolerance` is NULL when the launch measured no
  source share for the table — the conflict was proven by the cell
  capacity, not by a measurement of the key itself. That is the absence
  of a verdict, not a failure. Fix J made every measured share
  comparable (both sides describe the declared PK), so fix H4's
  `repeat_share_note` column is no longer written;
- a driven child of that table is sized off its parent's **distinct**
  landed keys (fix H3, `model_adjustment_descendant_rows`), so its
  `num_rows_requested` is roughly `(1 - source_repeat_share)` of what
  the pre-H3 launcher would have asked for. A missed request on such a
  table is a defect again, not arithmetic;
- the post-run PK check below, run on an ADJUSTED table, is expected to
  return the source's duplicate count, not 0. Compare it with
  `validation_runs.landing_repeat_share`, not with zero.

Pass `--on_model_conflict=stop` to get the pre-0038 refusal back, word
for word. Model SELF-contradictions (unknown columns, two `drives: true`
edges, an ambiguous role) and the ADR 0035 capacity gate stop under
BOTH settings.

**P5 defers to the measurement (fix H2).** The sample-based
"the declared PK is not a key of this data" stop (§"PK capacity" above)
does NOT fire on a table whose FULL source was measured: it logs
`preflight_pk_sample_stop_deferred` and hands the verdict to P4, which
adjusts (default) or refuses (`stop`). Before the fix a driven child
whose 10k sample showed ≥50% duplicate keys exited the whole launch at
`[preflight P5]` — zero rows for every planned table — with
`--on_model_conflict` never read. A table with NO measurement keeps the
P5 stop exactly as documented.

**Post-run independent PK check (per driven table, expect 0 rows):**

```sql
SELECT COUNT(*) AS duplicated_pk_tuples FROM (
  SELECT D_COL_001, C_COL_002, D_COL_018, COUNT(*) AS c
  FROM `${PROJECT}.synthetic_data.c_table`
  GROUP BY D_COL_001, C_COL_002, D_COL_018
  HAVING c > 1
)
```

Non-zero here with `pk.duplicate = 0` in `validation_runs` means the
in-DAG measurement missed rows (a wiring defect); non-zero in both is a
generator regression — start at `fanout_bound` and the `[preflight P4]`
cell counts.

**Dropped parent keys.** `fanout / keys_dropped_null` (Beam counter) is
the number of parent key tuples the fan-out projection discarded because
a JOIN-KEY column was NULL. Inherited (non-join) NULLs ride through and
are copied verbatim, so a non-zero counter means the parent landed NULLs
in the driving edge's own columns — expect 0 on a PK-declared parent.

**Multi-parent children (ADR 0037) — which parent drives, and the
candidate cap.** A driven child may reference several parents. You do
not declare roles; the registry derives them from the columns, and
`scripts/relationships/card.py --table <CHILD>` prints them before you
launch:

- an edge that shares **no** column with the driving edge is
  `independent` — it keeps the ADR 0030/0031 side-input key pool and the
  `fk.orphan` gate, exactly as a root's edge would (a star-schema
  dimension);
- an edge that shares **at least one** column is `conditional` — the
  shared columns come from the driving key, and the rest is joined in
  from the parent rows that carry that shared value (a diamond branch).
  `relational_fk_edge mode=conditional overlap=` (worker) is the
  confirmation.

**Which parent drives** is a pure function of the model file, in order:
a lone enforced in-model edge drives; else exactly one edge marked
`drives: true`; else the parent that descends from every other candidate
parent (its edge to the other parent is widened, `fk_edge_widened`);
else — no marker and no ancestry between the parents — the **first
declared** enforced edge drives and the launcher logs
`fk_driving_edge_defaulted table= edge= hint=` at WARNING. That WARNING
is not a failure: the launch is correct either way, and the fix if it
picked the wrong parent is one `drives: true` in the model file (two
markers on one table is still the one `RelationshipError` left). Every
overlapping pair with an EXTERNAL end — driving∩external,
external∩external, or external∩any non-driving edge — cannot be
resolved by any model edit (fix wave F3: an external edge never becomes
`implied`, `drives: true` is inert for it, and it has no `tables:` entry
to disable or document), so none of them stop the launch: each logs
`fk_edge_overlap_external table= edge= other= overlap= note=` (WARNING,
`edge=` always the external one) instead. Fix wave G3: that report comes
from the MODEL, not from resolved edge roles, so a single-table launch
of a denormalised child (every parent external) warns too — it used to
launch with no stop and no signal, its rows diverting as `fk.orphan`. The last edge written keeps
the shared column, so `other`'s tuple may not exist in its own parent —
bring the parent inside the launch to resolve it.

`--fk_candidate_cap` (default `64`) is `M`, the operator's flag, used
VERBATIM: the composer's Top-M combine and every request payload carry
it unclamped (fix wave F1 reverted an A3 attempt to clamp it to the
measured max fan-out — `M` sizes the candidate SAMPLE per shared JOIN
VALUE, reused by every driving key carrying that value, not a per-key
allotment, so clamping it collapsed a 1:1 driving edge's shared value to
one candidate, a point mass, on the default flag). A key whose fan-out
exceeds its candidate list is CAPPED at the joint capacity — the cell
count ONLY when the PK's completing members are exact, times the actual
candidate count (or **1**, a NULL fill) of each conditional edge whose
`rest` supplies a PK MEMBER (fix waves E2 + G1: an edge outside the PK
distinguishes no child, so it multiplies nothing — counting it emitted
rows that landed as PK duplicates with no warning) — if, and only if,
the PK's completing members are exact; an INEXACT PK never caps, and
its candidate digits wrap instead while its cells keep drawing
independently from their measured weights (fix wave E1, `joint_key_draw`;
ADR 0037 D3). A capped key's shortfall is reported once per worker
process **per driven table** (fix wave E3) as
`fanout_rows_capped requested= emitted= capacity=` (WARNING) — raise the
cap when a branch's within-key variety matters and the shuffle can
afford it, lower it when a request gets too wide. `keys_per_batch` is
lowered at launch so candidate TUPLES per request (`keys_per_batch × cap
×` number of conditional edges) never exceed 100k — that bounds tuples,
not the raw per-request VALUE count, which is
`keys_per_batch × cap × Σ|rest|` and can run higher when a conditional
edge's `rest` spans more than one column (ADR 0037 D4). Once `cap ×`
the number of conditional edges passes 100k on its own, that bound
floors at ONE key per request and stops bounding: the launcher logs
`fk_candidate_request_unbounded table= candidate_cap= conditional_edges=
tuples_per_request= ceiling=` (WARNING) instead of clamping the cap —
lower `--fk_candidate_cap` (fix wave G5). A shared value
the parent simply has too few distinct candidates for is capped or
wrapped (per the rule above) the same way at ANY setting of the cap; the
figure in [ADR 0037](adr/0037-multi-parent-children.md) D4 shows the
candidate-list split that feeds it.

Preflight P4 folds ONE new member into the per-key PK capacity, not two
(fix wave A2 corrected this): a conditional edge's `rest` in the PK
contributes at most `--fk_candidate_cap` (the operator's flag, used
as-is) — a stop there names the edge or the flag. An INDEPENDENT edge in
the PK contributes NOTHING: its pool is drawn per ROW WITH REPLACEMENT,
so two children of one parent key can draw the same parent tuple, and
counting its pool cap here used to declare PKs safe that the engine then
duplicated. The conditional factor itself is an UPPER bound — a model
whose co-parent is thin still passes P4 and caps at run time, visibly,
via `fanout_rows_capped`.

**Unmatched conditional keys (`fk.unmatched`).** When the conditional
parent holds NO row for a driving key's shared value, the engine writes
`NULL` on the branch columns if every one of them is NULLABLE in BOTH
the landing schema AND the generation schema (fix wave A4 — landing
alone let a REQUIRED generation column silently reject the row inside
`model_validate`, with no envelope, counter or milestone) AND absent
from the PRIMARY KEY THE RUN ENFORCES — the relationship model's `pk:`
(ADR 0032), with the DDL constraint standing in only when the model
declares none (fix waves F2 + G2 — the record model rejects a NULL on a
declared PK column whatever its mode says, and reading the BQ constraint
alone never fired on the canonical setup, where it is None; on ADR
0037's own diamond the child PK's last member IS the co-parent's column,
so this is the default shape, not a corner). Either failure is treated
as NON-nullable and logged once per edge as
`fk_nullable_schema_mismatch table= edge= landing= generation= reason=
pk=` (WARNING; `reason=` is `declared_pk`, `schema_mode_mismatch`, or
both, and `pk=` names the offending `rest` columns). Otherwise the key
is dropped before generation (`batch_unmatched`, `fanout /
keys_unmatched`) and reported as `fk.unmatched` in
`validation_runs.dlq_by_rule`, weighted by that key's expected rows.
Expect non-zero only when the SOURCE genuinely lacks that branch — it is
an input fact, not a generator regression the way `fk.orphan` is. The
§8.4 orphan query is unchanged for a conditional edge: the same
whole-tuple LEFT JOIN, and the ruling-B NULL rows are excluded from it
exactly as every NULL tuple already is.

**The cache table is optional.** If `--fk_fanout_stats_table` names a table that does not exist (or cannot be read or written), the launcher logs `fk_fanout_cache_unavailable` (WARNING) once per table and measures the fan-out from the source instead; nothing else changes.

**Cache invalidation.** The `fk_fanout_stats` row is keyed by
`(source_table, edge_cols, model_sha)` ONLY — nothing in the key tracks
the source's content. If the source table changes shape (rows added, the
fan-out ratio moves), a cached payload keeps replaying the OLD ratio.
Re-measure by editing the model file (any field — `sha12()` covers all of
them) or by deleting the cached row. Gaining or losing a driving,
conditional or independent `fk:` entry (ADR 0037) already forces this on
its own: `sha12()`'s canonical string covers every `fk:` entry (`cols`,
`ref`, `ref_cols`, `enforced`, `drives`), so the sha changes and the next
launch is a cache miss — no manual step needed. What the key does NOT
cover is the PK-completing `cell_cols` measured alongside the histogram:
those come from `pk_cell_columns` over the reference-data column
profiles (categorical vs. identifier), not from the `fk:` list. A child
whose cell columns change WITHOUT any `fk:` entry changing — for example
a source column's profile flipping between categorical and identifier as
the table grows — keeps a stale cell table under the unchanged sha.
Since nothing in the model file moved, editing it won't help; delete
that table's `fk_fanout_stats` row directly to force a re-measurement.

**Did PK/FK enforcement actually happen?** After the job lands, run the
`/e2e_fk_pk_validator` prompt
(`.github/prompts/e2e_fk_pk_validator.prompt.md`) with the model file and
the `JOB_ID`: it derives the contract from the registry (widened edges,
roles, disabled tables), reads the launch's own milestones, and runs the
PK-duplicate, identity, whole-tuple orphan and fan-out queries through the
local `bq` CLI (read-only), cross-checking each count against
`validation_runs.dlq_by_rule`. One PASS/FAIL per (table, check).

### 9c. 10M scale (warm everything)

**Trigger config:** `{"num_rows":"10000000","batch_size":"1000"}` — same
table(s), same digest.

**Fleet:** `initial_workers=4`, `autoscaling=fixed`, `sdk_containers=multi`
(tier `R7m`, ADR 0034 D5/D6/D9) — the 09-07/09-08 multi runs lost ≈ 4 min
per job to autoscaler dips between the parent and child stages.

**Preconditions:** all stores populated and NOT truncated since the last
cold run (`freetext_pools`, `rag_chunks`, `source_table_stats`); no
source-table content change (a content change moves the reference digest
and silently makes this a cold 10M run — cold pool build measured at
41–53% of wall time would dominate). Verify warmth first: `pool_build_skipped`,
`freetext_pool_store_hit`, `b1_chunks_reused`, `source_stats_skipped`
must all fire.

**Expect:** ≥ 6k rows/s class; diversity ceiling gone (`freetext_expansion`
default); `batch_done seconds=` p99 in the p50 class; a fully-warm run may
never ignite vLLM (the GPU pool can be dropped for warm replays — CPU-only
`n1-highmem-8` runs the same DAG; the embedder already demotes). Re-score
memorization at 10M: collision metrics scale with row count, so
`copy_ratio_substantive` on identity-like numeric columns is THE number to
re-read at scale.
