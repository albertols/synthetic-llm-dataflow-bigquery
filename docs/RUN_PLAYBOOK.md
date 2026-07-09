# Run playbook — GPU verdict, run matrix, Dataflow options, report recipe

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

## 2. Run matrix — the four M1 §11 validation runs

All four runs launch through the same DAG
(`composer/synthetic_beam_bigquery.py`), whose `default_dag_params` are:
`table_fqn`, `num_rows`, `engine` (`b1_rag` | `b2_library`), `batch_size`,
`similarity`, `identity_cols`, `client_type` (`vllm` | `fake`), and `gpu`
(`l4` | `t4`, only consulted when `client_type=vllm`). `identity_cols`
defaults to empty (no identity-column synthesis); every fidelity run
(R1/R2/R3a-c) should set it to the target table's PK/UUID column(s) —
e.g. `customer_id` — so that column is synthesized fresh per row instead of
copied from the reference sample (see `packages/sdfb-core/src/sdfb_core/engines/identity.py`).
**There is no `seed` Airflow param and no
`--seed` CLI flag** — `PipelineConfig.seed` defaults to `None` and stays that
way on every real run. That is deliberate: with no explicit seed,
`GenerateRecordsDoFn` derives a per-batch seed from `(run_id, batch_id)` via
`sdfb_core.seeding.derive_batch_seed()`
(`packages/sdfb-beam/src/sdfb_beam/dofns/generate.py`), so batches never
replay each other within a run, and re-running the identical `run_id`
reproduces the identical output byte-for-byte. The "seed" column below is
therefore always "derived (no override)" — this is the fixed, intended
behavior, not a gap to fill in later.

| # | engine | client_type | gpu | num_rows | batch_size | similarity | seed | Expected insight |
|---|---|---|---|---|---|---|---|---|
| R1 | `b1_rag` | `vllm` | `l4` | `1000` | `16` | `0.5` | derived (no override) | B.1 (RAG) fidelity on the real Gemma-on-L4 path: retrieval-grounded free text, schema-conformant rows, no exemplar fallback. |
| R2 | `b2_library` | `vllm` | `l4` | `1000` | `16` | `0.5` | derived (no override) | B.2 (library-wrapper) fidelity on the same Gemma-on-L4 path: `sdgx`-fitted tabular sampling + LLM-patched free text, for direct comparison against R1 on identical `num_rows`/`batch_size`. |
| R3a/b/c | `b2_library` | `vllm` | `l4` | `300` | `16` | `0.0` / `0.5` / `0.9` | derived (no override) | Similarity sweep isolating `_blend_pools()` (`packages/sdfb-core/src/sdfb_core/engines/b2_library/freetext.py`): mass `similarity` goes to the reference pool, `1 - similarity` to the LLM pool. `0.0` should read as ~all-LLM free text, `0.9` as ~all-reference-copied, `0.5` as the midpoint — quantify with the probe's `copy_ratio` (§5) across the three runs. |
| R4 | `b1_rag` or `b2_library` | `vllm` | `t4` | `200` | `16` | `0.5` | derived (no override) | Plumbing-only: confirms the real vLLM server, GCS model pull, and BigQuery write path all work on T4 when `SDFB_MODEL_URI` points at `qwen3_4b_instruct_2507` (never Gemma — §1) **and `vllm_dtype=float16` is set** — Qwen ships bf16 checkpoints (SM>=8.0) but is fp16-safe; without the explicit downcast the worker fails fast with `ModelGpuIncompatibleError`. Not a fidelity run; keep `num_rows` small. |

**Reproducibility check (run alongside R1 or R2, not a fifth row):** trigger
the DAG twice with the **same** explicit Airflow `run_id` (e.g.
`gcloud composer environments run <env> --location <region> dags trigger --
<dag_id> --run-id repro-check-001`, invoked twice) and no seed override. Both
runs must produce byte-identical landing rows for every `batch_id`, because
`derive_batch_seed(run_id, batch_id)` is a pure function of those two inputs.
A mismatch means either the DAG stopped passing a stable `run_id` through to
`--run_id` on the Flex Template, or something upstream (retrieval order,
`sdgx` fit) is not deterministic given a fixed seed — treat it as a bug, not
as expected LLM sampling noise.

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
  plumbing run (R4) never touches the `g2-standard` family. The `-4` vs `-8`
  trade-off is therefore informational, relevant only if someone edits that
  ternary: both sizes carry exactly one L4, so the choice is pure headroom,
  not GPU count — `-8` gives the CPU-side steps (BQ read, Pandera validation,
  `sdgx` fit for B.2, the Beam harness alongside vLLM) more vCPU/RAM to avoid
  becoming the bottleneck next to the GPU, at roughly double the non-GPU
  cost. The hardcoded `-8` is the right default for the fidelity runs
  (R1–R3); don't downgrade it without a measured reason.
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
- **Zone pinning.** The DAG sets `workerRegion` to the `REGION` Composer
  Variable (`europe-west3` per [ADR 0004](adr/0004-europe-west3-region.md))
  but does not pin a specific zone — Dataflow picks within the region. For L4
  fidelity runs, prefer explicitly pinning to `europe-west3-a` or `-b` (both
  carry L4 capacity, §1) via a `workerZone` override or by relying on the
  reservation-affinity ladder in §4, rather than letting Dataflow's own
  zone-spread retry logic hunt across all of `europe-west3`.
- **`maxWorkers` for 1000-row runs.** The DAG currently hardcodes
  `maxWorkers: 4`. For the R1/R2 fidelity runs at `num_rows=1000` with
  `batch_size=16` (~63 batches), **1–2 workers is the right target** — the
  per-batch LLM call dominates wall time and more GPU workers just means more
  idle vLLM cold-starts and more L4 capacity contended for no throughput
  gain; `num_rows` at this scale doesn't need horizontal scale-out. Treat the
  hardcoded `4` as a ceiling, not a target — Dataflow won't launch more
  workers than the graph can use, but requesting fewer up front reduces
  contention against the L4 stockout (§4).

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

**1. Offline analysis** (table-agnostic; computes duplicate ratio, repetition,
singularity, sparsity, identity-column uniqueness, cross-sample Jaccard —
`--batch-size` enables the run-length-equals-batch-size replay flag,
`--identity-cols` scopes the per-row-uniqueness check to PK/UUID columns):

```bash
python scripts/e2e_validation_analysis.py \
  $(for c in <CSVS>; do echo --csv $c; done) \
  --schema <SCHEMA> --pk <PK> --identity-cols <IDENTITY_COLS> \
  --batch-size <BATCH_SIZE> \
  --out output/e2e_validation_metrics.json
```

**2. Live GCP cross-validation + Dataflow observability** (ADC-authenticated;
`--run-id` lets the probe correlate a job against the seed/reproducibility
check in §2, `--engine-label` stamps a human-readable label onto each job_id
so the report can say "b1_rag" instead of a raw Dataflow job id):

```bash
python scripts/e2e_gcp_probe.py \
  --project <PROJECT> \
  --source-fqn <SOURCE_FQN> --landing-fqn <LANDING_FQN> \
  --quality-dataset <QUALITY_DATASET> \
  --region <REGION> --pk <PK> \
  $(for j in <JOB_IDS>; do echo --job-id $j; done) \
  --run-id <RUN_ID> \
  --engine-label b1_rag=<JOB_ID_1> --engine-label b2_library=<JOB_ID_2> \
  --out output/e2e_gcp_metrics.json
```

**3. Bundle export** (splits the report + metrics into an internal `real/`
folder and a de-identified `oss/` folder safe to hand to the OSS team; exits
non-zero unless the leak scan is clean):

```bash
python scripts/e2e_bundle_export.py \
  --metrics gcp=output/e2e_gcp_metrics.json \
  --metrics offline=output/e2e_validation_metrics.json \
  --report output/end_to_end_validation_report_YYYY_MM_DD_HH_MM.md \
  --out-root integration_tests \
  --no-redact-values
```

Only the `oss/` folder produced by step 3 is shareable outside the team; keep
`real/` (and its `mapping.json` decode key) local.
