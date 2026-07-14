#!/usr/bin/env bash
# Buckets + BQ datasets + DQ tables + bucket IAM + source snapshots + landing
# tables. Idempotent. Usage: ./03_storage_bq.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX

# --- buckets ---------------------------------------------------------------
make_bucket() {  # <name>
  if probe gcloud storage buckets describe "gs://$1"; then
    log "bucket $1 exists"
  else
    run gcloud storage buckets create "gs://$1" \
      --project "${PROJECT_ID}" --location "${REGION}" --uniform-bucket-level-access
  fi
}
make_bucket "${MODELS_BUCKET}"
make_bucket "${DATAFLOW_BUCKET}"
run gcloud storage buckets update "gs://${DATAFLOW_BUCKET}" \
  --lifecycle-file "${SCRIPT_DIR}/dataflow_bucket_lifecycle.json"

# --- bucket-level IAM (deferred from 02: buckets must exist) ----------------
run gcloud storage buckets add-iam-policy-binding "gs://${MODELS_BUCKET}" \
  --member "serviceAccount:${WORKER_SA}" --role roles/storage.objectViewer
run gcloud storage buckets add-iam-policy-binding "gs://${DATAFLOW_BUCKET}" \
  --member "serviceAccount:${WORKER_SA}" --role roles/storage.objectAdmin
run gcloud storage buckets add-iam-policy-binding "gs://${MODELS_BUCKET}" \
  --member "serviceAccount:${BUILD_SA}" --role roles/storage.objectAdmin

# --- datasets ----------------------------------------------------------------
make_dataset() {  # <name>
  if probe bq show --dataset "${PROJECT_ID}:$1"; then
    log "dataset $1 exists"
  else
    run bq --location="${BQ_LOCATION}" mk --dataset "${PROJECT_ID}:$1"
  fi
}
make_dataset "${SRC_DATASET}"
make_dataset "${LANDING_DATASET}"
make_dataset "${QUALITY_DATASET}"

# --- DQ tables (committed schemas; DAY partitions per DEPLOYMENT_PREREQUISITES) --
if probe bq show "${PROJECT_ID}:${QUALITY_DATASET}.dlq"; then
  log "dlq table exists"
else
  run bq mk --table \
    --schema "${REPO_ROOT}/config/bq_schema/synthetic_data_quality/dlq.schema.json" \
    --time_partitioning_type DAY --time_partitioning_field dlq_inserted_at \
    "${PROJECT_ID}:${QUALITY_DATASET}.dlq"
fi
if probe bq show "${PROJECT_ID}:${QUALITY_DATASET}.validation_runs"; then
  log "validation_runs table exists"
else
  run bq mk --table \
    --schema "${REPO_ROOT}/config/bq_schema/synthetic_data_quality/validation_runs.schema.json" \
    --time_partitioning_type DAY --time_partitioning_field created_at \
    "${PROJECT_ID}:${QUALITY_DATASET}.validation_runs"
fi

log "storage + datasets + DQ tables done"
