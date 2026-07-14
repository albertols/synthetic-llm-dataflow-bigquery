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

# --- one-time public-table snapshots (spec: snapshot-once cost strategy) -----
# Frozen 50k-row copies; reruns are no-ops via IF NOT EXISTS. citibike has no
# natural PK -> GENERATE_UUID() trip_id (pk_cols/identity_cols in tiers.yaml).
run bq query --use_legacy_sql=false --location="${BQ_LOCATION}" --project_id="${PROJECT_ID}" \
  "CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${SRC_DATASET}.citibike_trips_50k\` AS
   SELECT GENERATE_UUID() AS trip_id,
          tripduration, starttime, stoptime,
          start_station_id, start_station_name,
          end_station_id, end_station_name,
          bikeid, usertype, birth_year, gender
   FROM \`bigquery-public-data.new_york_citibike.citibike_trips\`
   WHERE tripduration IS NOT NULL
   LIMIT 50000"

run bq query --use_legacy_sql=false --location="${BQ_LOCATION}" --project_id="${PROJECT_ID}" \
  "CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${SRC_DATASET}.hacker_news_50k\` AS
   SELECT id, type, \`by\`, title, text, url, score, descendants, \`timestamp\`
   FROM \`bigquery-public-data.hacker_news.full\`
   WHERE type = 'story' AND text IS NOT NULL AND \`by\` IS NOT NULL
   LIMIT 50000"

# --- DDL extraction + landing tables ----------------------------------------
stage_table() {  # <table_name>
  local table="$1"
  local ddl_gcs="gs://${DATAFLOW_BUCKET}/ddl/${table}_ddl.json"
  run uv run --no-sync python3 "${REPO_ROOT}/scripts/extract_ddl.py" \
    --project "${PROJECT_ID}" --dataset "${SRC_DATASET}" --table "${table}" \
    --output_base "${REPO_ROOT}/output"
  local ddl_local
  if [[ "${DRY_RUN}" == "1" ]]; then
    ddl_local="${REPO_ROOT}/output/${table}_ddl.json"
  else
    ddl_local="$(ls "${REPO_ROOT}"/output/*"${table}"*_ddl.json | head -1)"
  fi
  run gsutil cp "${ddl_local}" "${ddl_gcs}"
  run uv run --no-sync python3 "${REPO_ROOT}/scripts/derive_landing_schema.py" \
    "${ddl_local}" -o "${REPO_ROOT}/output/${table}_landing_schema.json"
  if probe bq show "${PROJECT_ID}:${LANDING_DATASET}.${table}"; then
    log "landing ${LANDING_DATASET}.${table} exists"
  else
    run bq mk --table --schema "${REPO_ROOT}/output/${table}_landing_schema.json" \
      "${PROJECT_ID}:${LANDING_DATASET}.${table}"
  fi
}
stage_table "citibike_trips_50k"
stage_table "hacker_news_50k"

log "storage + datasets + DQ tables + snapshots + landing tables done"
