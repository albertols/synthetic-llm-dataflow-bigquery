---
name: gcp-cost-audit
description: Read-only cost/quota/free-tier audit recipes for the personal GCP project — burn-down vs budget, per-SKU spend, BQ/GCS/Cloud Build free-tier consumption, T4 quota. Load for any "what is this costing?" question.
---

# Skill — personal GCP cost audit (read-only)

Console quick links (fastest human view — substitute $PROJECT_ID):
- Billing report: https://console.cloud.google.com/billing/reports?project=$PROJECT_ID
- Budgets: https://console.cloud.google.com/billing/budgets
- Quotas: https://console.cloud.google.com/iam-admin/quotas?project=$PROJECT_ID

## Burn-down + budget state

```bash
gcloud billing budgets list --billing-account $BILLING_ACCOUNT_ID \
  --format 'table(displayName, amount.specifiedAmount.units, thresholdRules[].thresholdPercent)'
gcloud functions logs read billing-killswitch --gen2 --region us-central1 \
  --project $PROJECT_ID --limit 5   # 'below threshold: X/Y' lines = live spend ratio
```

## Free-tier trackers (monthly)

```bash
# BQ analysis bytes this month (free: 1 TiB)
bq query --use_legacy_sql=false --project_id=$PROJECT_ID 'SELECT
  ROUND(SUM(total_bytes_billed)/POW(1024,4), 4) AS tib_billed
FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
WHERE EXTRACT(MONTH FROM creation_time)=EXTRACT(MONTH FROM CURRENT_TIMESTAMP())'
# BQ storage (free: 10 GB): bq show --format=prettyjson <dataset> | numBytes per table
# GCS (free: 5 GB US): gcloud storage du -s gs://$PROJECT_ID-models gs://$PROJECT_ID-dataflow
# GAR stored images (policy keeps 1): gcloud artifacts docker images list \
#   us-central1-docker.pkg.dev/$PROJECT_ID/sdfb --format 'value(package,version)'
# Cloud Build minutes: gcloud builds list --project $PROJECT_ID --region us-central1 \
#   --format 'table(id,createTime,duration,status)' --limit 20
```

## Quota

```bash
gcloud compute regions describe us-central1 --project $PROJECT_ID \
  --flatten 'quotas' --filter 'quotas.metric=NVIDIA_T4_GPUS' \
  --format 'table(quotas.metric,quotas.usage,quotas.limit)'
gcloud compute project-info describe --project $PROJECT_ID \
  --flatten 'quotas' --filter 'quotas.metric=GPUS_ALL_REGIONS' \
  --format 'table(quotas.metric,quotas.usage,quotas.limit)'
```

## Cost model cheat-sheet (us-central1, batch)

~$1/worker-hour all-in for the T4 tier (n1-standard-8 + T4 + Dataflow service
+ 200GB PD); a 1000-row run ≈ 30–45 min at maxWorkers=1. Steady-state storage
≈ $2–3/month (image + 16 GB models + BQ snapshots).
