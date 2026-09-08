#!/usr/bin/env bash
# Service accounts + minimal roles. Bucket-level grants live in 03 (need buckets).
# Idempotent. Usage: ./02_iam.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX BILLING_ACCOUNT_ID

create_sa() {  # <name> <display>
  if probe gcloud iam service-accounts describe "$1@${PROJECT_ID}.iam.gserviceaccount.com" --project "${PROJECT_ID}"; then
    log "SA $1 already exists"
  else
    run gcloud iam service-accounts create "$1" --display-name "$2" --project "${PROJECT_ID}"
  fi
}

grant() {  # <member> <role>
  run gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:$1" --role "$2" --condition None --quiet
}

create_sa "${WORKER_SA_NAME}" "sdfb Dataflow worker+launcher"
create_sa "${BUILD_SA_NAME}"  "sdfb Cloud Build"
create_sa "${KILL_SA_NAME}"   "sdfb billing killswitch"

# Worker/launcher: run flex-template jobs, read/write BQ, pull GAR image.
# (docs/DEPLOYMENT_PREREQUISITES.md minimal set + dataflow roles.)
grant "${WORKER_SA}" roles/dataflow.worker
grant "${WORKER_SA}" roles/dataflow.developer
grant "${WORKER_SA}" roles/bigquery.dataEditor
grant "${WORKER_SA}" roles/bigquery.jobUser
# ADR 0034 D4: source-domain fetches stream through the BigQuery Storage
# Read API (bigquery.readsessions.create). Without this the worker logs
# `source_values_storage_api_disabled` once and pages via REST.
grant "${WORKER_SA}" roles/bigquery.readSessionUser
grant "${WORKER_SA}" roles/artifactregistry.reader

# Cloud Build (custom SA => CLOUD_LOGGING_ONLY in both cloudbuild YAMLs).
grant "${BUILD_SA}" roles/logging.logWriter
grant "${BUILD_SA}" roles/artifactregistry.writer
grant "${BUILD_SA}" roles/storage.objectAdmin
grant "${BUILD_SA}" roles/secretmanager.secretAccessor

# Killswitch: needs billing.projectManager on the PROJECT to read/describe
# the project's billing info, plus billing.admin ON THE BILLING ACCOUNT to
# actually detach it (CloudBillingClient.update_project_billing_info).
grant "${KILL_SA}" roles/billing.projectManager
run gcloud billing accounts add-iam-policy-binding "${BILLING_ACCOUNT_ID}" \
  --member "serviceAccount:${KILL_SA}" --role roles/billing.admin

log "IAM done. Worker SA: ${WORKER_SA}"
