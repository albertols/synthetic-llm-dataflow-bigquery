# Personal GCP E2E deployment (`public_cloud/deploy/gcp/`)

## 1. What this is

A third deployment wrapper alongside the corporate Composer/JFrog path and
the M4-local path: a **personal GCP project**, T4-only, cost-capped, used to
run the E2E test matrix (`docs/E2E_TEST_MATRIX.md`) without corporate LZ
access or an M4. It makes **zero edits to mainline code** — same
`docker/Dockerfile`, same Flex Template metadata, same pipeline package. Only
the build tool differs (Cloud Build instead of GitHub CI; see
[ADR 0016](../../../docs/adr/0016-personal-gcp-cloud-build.md)).

Design spec:
[`docs/superpowers/specs/2026-07-14-personal-gcp-e2e-design.md`](../../../docs/superpowers/specs/2026-07-14-personal-gcp-e2e-design.md).

Everything here supports `--dry-run` (prints the commands it would run
instead of running them) — use it to sanity-check before spending money.

## 2. Credits reality

Google's free trial gives $300 of credit over 90 days, but **GPU quota is 0
on a trial account** — running a T4 job requires upgrading to a paid account
first (the remaining credit survives the upgrade; you are not charged until
the credit is exhausted). There is no other meaningful voucher path for an
individual (no research/startup program applies here).

Expected costs at this project's scale:
- ~$1 per T4 run (n1-standard-8 + T4 + Dataflow service + 200GB PD,
  ~30–45 min at `maxWorkers=1`).
- $2–3/month steady-state storage (image + ~16GB staged models + BQ
  snapshots).
- A full campaign (S0 → R1p → R2p → R3p → N4 → P6 ×2 → P7 on citibike, plus
  one R1p on hacker_news) ≈ $6–10 all-in.

## 3. One-time setup

```bash
export PROJECT_SUFFIX=<suffix> BILLING_ACCOUNT_ID=<id>   # gcloud billing accounts list
cd public_cloud/deploy/gcp
./01_bootstrap_project.sh && ./02_iam.sh && ./03_storage_bq.sh && ./04_budget_killswitch.sh
# MANUAL: upgrade trial -> paid; request T4 quota (script 01 prints URLs).
./05_stage_models.sh && ./06_build_image.sh && ./07_build_flex_template.sh
```

The two manual steps (printed by `01_bootstrap_project.sh`, with the account
and project already filled in):
1. Upgrade the billing account from trial to paid at
   `https://console.cloud.google.com/billing/<BILLING_ACCOUNT_ID>` — GPU
   quota stays 0 until this is done.
2. Request GPU quota at
   `https://console.cloud.google.com/iam-admin/quotas?project=<PROJECT_ID>`:
   `GPUs (all regions) >= 1` and `NVIDIA T4 GPUs (us-central1) >= 1`. Count-1
   requests are usually auto-approved within minutes.

`05_stage_models.sh` needs Kaggle credentials in Secret Manager first (bge
falls back to ModelScope if Kaggle is unavailable, but the secrets must
still exist or the Cloud Build fails loud before any step runs):

```bash
printf '%s' '<kaggle user>' | gcloud secrets create kaggle-username --data-file=- --project $PROJECT_ID
printf '%s' '<kaggle key>'  | gcloud secrets create kaggle-key --data-file=- --project $PROJECT_ID
```

Full recipe (bootstrap, kill-switch recovery, teardown): skill
`.claude/skills/gcp-project-ops.md`. Preflight audit (read-only, reusable
across projects):

```bash
uv run --no-sync python3 scripts/deployment_prerequisites.py --project $PROJECT_ID \
  --source-table $PROJECT_ID.synthetic_source.citibike_trips_50k --model-uri $MODEL_URI \
  --staging-bucket $DATAFLOW_BUCKET --templates-bucket $DATAFLOW_BUCKET
```

## 4. Cost control

Four independent layers, in order of severity:

1. **Kill-switch (hard cap).** A billing budget (`04_budget_killswitch.sh`)
   fires a Pub/Sub message at 50/80/90% of `BUDGET_AMOUNT` (default
   $25/month); at 90% a Cloud Function (`killswitch/main.py`) calls
   `CloudBillingClient.update_project_billing_info` with an empty billing
   account name, which **detaches billing from the project**. "Billing
   detached" means every billable API call in the project starts failing —
   Dataflow jobs get killed, BQ/GCS writes error out — until it is manually
   relinked (§6). The function is idempotent (no-ops if billing is already
   detached) and logs `BILLING DETACHED for <project> at <cost>/<budget>`.
2. **Alert emails.** The budget's threshold rules (50/80/90%) also trigger
   GCP's standard billing-alert emails to the billing account's admins,
   independent of the kill-switch — an early warning before the hard cap
   fires.
3. **Structural caps.** `maxWorkers=1` on every tier (cost cap baked into
   `tiers.yaml` defaults, override only if a tier truly needs more); GAR
   keep-newest-1 cleanup policy (`gar_cleanup_policy.json`, applied in
   `01_bootstrap_project.sh`) so old images don't accumulate storage; a GCS
   lifecycle rule on the Dataflow bucket (`dataflow_bucket_lifecycle.json`)
   expires `staging/`/`temp/` objects; BQ source tables are **snapshotted
   once** (`CREATE TABLE IF NOT EXISTS`, `03_storage_bq.sh`) instead of
   re-querying the public datasets on every run.
4. **PO-auditor reports.** The `gcp-po-auditor` agent (read-only; see
   `.claude/agents/gcp-po-auditor.md` and skill `gcp-cost-audit.md`) reports
   burn-down vs. budget, free-tier consumption, and quota state on demand —
   run it before and during a campaign, not just after.

Console quick links (substitute `$PROJECT_ID`):
- Billing report: https://console.cloud.google.com/billing/reports?project=$PROJECT_ID
- Budgets: https://console.cloud.google.com/billing/budgets
- Quotas: https://console.cloud.google.com/iam-admin/quotas?project=$PROJECT_ID

## 5. Running the matrix

```bash
cd public_cloud/deploy/gcp
./run_e2e.sh <tier> <table>     # tiers: S0 R1p R2p R3p N4 P6 P7; tables: citibike hacker_news
```

`run_e2e.sh` renders the tier/table preset from `tiers.yaml`, submits the
Flex Template job, polls to a terminal state, journals every run (pass or
fail) to `journal/runs.jsonl`, and enforces `EXPECT` semantics — a tier
marked `expect: FAIL` (N4) that comes back `JOB_STATE_DONE` is treated as a
broken negative guard, not a pass.

Tier semantics (shell-safe names; `R1p` == `R1'` etc., per
`docs/E2E_TEST_MATRIX.md`):

| Tier | Semantics |
|---|---|
| S0 | CPU smoke: fake client, full Dataflow→BigQuery write path, no GPU cost. Run first. |
| R1p | The workhorse: `b1_rag` engine, qwen3-4b on T4, `vllm_dtype=float16`. Real vLLM lifecycle. |
| R2p | Identical rerun of R1p — anti-replay guard (fresh salted `run_id` must produce different output). |
| R3p | `b2_library` engine (sdgx fit + LLM only patches free text). |
| N4 | Negative guard: bad `model_uri` — strict mode must kill the job; a green N4 is a broken guard. |
| P6 | Explicit-seed reproducibility — run **twice**, compare (non-identity columns identical, identity columns still unique, `run_id` still differs). |
| P7 | PK gate isolated from identity synthesis (`identity_cols=""`) — collisions must land in DLQ as `pk.duplicate`. |

Campaign order: `S0 → R1p → R2p → R3p → N4 → P6 ×2 → P7` on citibike, then
one `R1p` on hacker_news. Stop at the first unexpected FAIL and hand the
landed artifacts to the `e2e-interpreter` agent before continuing.

Report recipe (after every landed job) and full campaign detail: skill
`.claude/skills/gcp-e2e-run.md`. Runs are journaled at
`public_cloud/deploy/gcp/journal/runs.jsonl` — one JSON line per submission,
pass or fail, with the exact `gcloud` command used.

## 6. Kill-switch recovery

If the kill-switch fires (billing detaches at 90% of budget):

1. **Confirm** what fired: `gcloud functions logs read billing-killswitch --gen2 --region us-central1 --project $PROJECT_ID --limit 20`.
2. **Audit** the spend first — do NOT relink blindly. Use the `gcp-po-auditor`
   agent or skill `.claude/skills/gcp-cost-audit.md` to understand what
   burned the budget before spending more.
3. **Relink**: `gcloud billing projects link $PROJECT_ID --billing-account $BILLING_ACCOUNT_ID`.
4. **Re-verify**: `./01_bootstrap_project.sh` (idempotent) then resume.

## 7. Teardown

```bash
./teardown.sh            # granular: deletes datasets, buckets, GAR repo, budget, kill-switch function; keeps the project
./teardown.sh --full     # also deletes the project (30-day recovery window)
```

Granular teardown (no `--full`) leaves the **3 service accounts**
(`sdfb-dataflow-worker`, `sdfb-build`, `sdfb-killswitch`) and the
killswitch SA's **billing-account IAM binding** (`roles/billing.admin`, added
by `02_iam.sh` directly on the billing account) in place — rerunning scripts
01–07 rebuilds on top of them. `--full` deletes the project, which removes
the project-level IAM bindings, but the billing-account binding is **not**
project-scoped and survives project deletion. Remove it manually:

```bash
gcloud billing accounts remove-iam-policy-binding $BILLING_ACCOUNT_ID \
  --member "serviceAccount:sdfb-killswitch@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role roles/billing.admin
```

## 8. Lessons learned

Seeded from the build of this layer; append as discovered.

- **T4 needs `vllm_dtype=float16` + `vllm_max_model_len=8192`.** The staged
  qwen3-4b checkpoint ships bf16, which Turing (T4) can't run natively —
  `vllm_dtype=float16` downcasts it. `vllm_max_model_len=8192` caps the KV
  cache so it fits the T4's 16GB VRAM; both are baked into `tiers.yaml`
  defaults, not optional.
- **citibike has no natural primary key.** The public
  `bigquery-public-data.new_york_citibike.citibike_trips` table has no row
  id, so the one-time snapshot in `03_storage_bq.sh` synthesizes one with
  `GENERATE_UUID() AS trip_id` at snapshot time (`pk_cols`/`identity_cols`
  in `tiers.yaml` both point at it).
- **US colocation constraint.** BigQuery cannot JOIN across regions, and the
  `bigquery-public-data` source tables live in `US` multi-region — so
  `BQ_LOCATION=US` and `REGION=us-central1` are fixed in `env.sh`. GCS's free
  tier is also US-region-only, which is a second reason to stay there.
- **bge upstream listings may miss `model.safetensors`.** Some Kaggle/
  ModelScope listings for `bge-small-en-v1.5` omit the actual weights file
  from the visible file list even though the source is otherwise complete
  — the manifest gate in `cloudbuild/stage_models.yaml` (checks every
  required file is present and non-empty) exists specifically to catch this
  before it silently ships a broken embedder.
- **`extract_ddl.py` output path convention.** It writes to
  `{output_base}/{dataset}/ddl_metadata_{dataset}_{table}.json`, not
  `{output_base}/{table}_ddl.json` as the naming might suggest — a
  path-convention bug caught in review of `03_storage_bq.sh`, which now
  computes `ddl_local` explicitly to match.
- **Cloud Build's sed retarget is fail-loud by design.** The pip-index
  retarget in `cloudbuild/build_image.yaml` greps for its own result and
  exits nonzero if nothing matched — if `docker/Dockerfile`'s uv-bootstrap
  line changes upstream, the personal build fails immediately instead of
  silently reaching for the corporate JFrog mirror it can't authenticate to.
