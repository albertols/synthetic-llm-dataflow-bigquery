#!/usr/bin/env bash
# Bootstrap the personal E2E project: project, billing link, APIs, GAR repo.
# Idempotent. Usage: ./01_bootstrap_project.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX BILLING_ACCOUNT_ID

APIS=(
  dataflow.googleapis.com compute.googleapis.com bigquery.googleapis.com
  storage.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
  cloudfunctions.googleapis.com run.googleapis.com eventarc.googleapis.com
  pubsub.googleapis.com billingbudgets.googleapis.com secretmanager.googleapis.com
)

if probe gcloud projects describe "${PROJECT_ID}"; then
  log "project ${PROJECT_ID} already exists"
else
  run gcloud projects create "${PROJECT_ID}" --name "${PROJECT_ID}"
fi

run gcloud billing projects link "${PROJECT_ID}" --billing-account "${BILLING_ACCOUNT_ID}"
run gcloud services enable "${APIS[@]}" --project "${PROJECT_ID}"

if probe gcloud artifacts repositories describe "${GAR_REPO}" --location "${REGION}" --project "${PROJECT_ID}"; then
  log "GAR repo ${GAR_REPO} already exists"
else
  run gcloud artifacts repositories create "${GAR_REPO}" \
    --repository-format docker --location "${REGION}" --project "${PROJECT_ID}" \
    --description "sdfb personal E2E images (keep-newest-1)"
fi
# Enforce the minimum-stored-images requirement by policy, not discipline.
run gcloud artifacts repositories set-cleanup-policies "${GAR_REPO}" \
  --location "${REGION}" --project "${PROJECT_ID}" \
  --policy "${SCRIPT_DIR}/gar_cleanup_policy.json" --no-dry-run

log "=========================================================================="
log "MANUAL STEP 1 — upgrade the free trial to a paid account (GPU quota stays 0"
log "  on trial; the remaining \$300 credit survives the upgrade):"
log "  https://console.cloud.google.com/billing/${BILLING_ACCOUNT_ID}"
log "MANUAL STEP 2 — request GPU quota for ${PROJECT_ID} (count 1 is usually"
log "  auto-approved in minutes): 'GPUs (all regions)' >= 1 AND"
log "  'NVIDIA T4 GPUs' (${REGION}) >= 1 at"
log "  https://console.cloud.google.com/iam-admin/quotas?project=${PROJECT_ID}"
log "=========================================================================="
