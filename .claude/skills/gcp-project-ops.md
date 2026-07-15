---
name: gcp-project-ops
description: Recipes for bootstrapping, verifying, recovering, and tearing down the personal GCP E2E project (public_cloud/deploy/gcp). Load when setting up the project, after a killswitch firing, or when infra drifts from env.sh.
---

# Skill — personal GCP project ops

> Source of truth is the scripts in `public_cloud/deploy/gcp/` — this skill is
> the operating recipe, not a copy. Everything supports `--dry-run`.

## Bootstrap (once)

```bash
export PROJECT_SUFFIX=<suffix> BILLING_ACCOUNT_ID=<id>   # gcloud billing accounts list
cd public_cloud/deploy/gcp
./01_bootstrap_project.sh && ./02_iam.sh && ./03_storage_bq.sh && ./04_budget_killswitch.sh
# MANUAL: upgrade trial -> paid; request T4 quota (script 01 prints URLs).
./05_stage_models.sh && ./06_build_image.sh && ./07_build_flex_template.sh
```

Preflight audit (read-only, reusable): `uv run --no-sync python3 scripts/deployment_prerequisites.py --project $PROJECT_ID --source-table $PROJECT_ID.synthetic_source.citibike_trips_50k --model-uri $MODEL_URI --staging-bucket $DATAFLOW_BUCKET --templates-bucket $DATAFLOW_BUCKET`

## Kill-switch recovery (billing was detached at 90% budget)

1. Confirm what fired: function logs — `gcloud functions logs read billing-killswitch --gen2 --region us-central1 --project $PROJECT_ID --limit 20`.
2. Understand the spend first (gcp-cost-audit skill) — do NOT relink blindly.
3. Relink: `gcloud billing projects link $PROJECT_ID --billing-account $BILLING_ACCOUNT_ID`.
4. Re-verify: `./01_bootstrap_project.sh` (idempotent) then resume.

## Teardown

`./teardown.sh` (granular, keeps project) or `./teardown.sh --full` (deletes project, 30-day recovery window).
