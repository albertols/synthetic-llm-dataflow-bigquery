#!/usr/bin/env bash
#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

# Full cleanup in reverse dependency order. --full also deletes the project.
# Usage: ./teardown.sh [--full] [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX BILLING_ACCOUNT_ID

FULL=0
for arg in "$@"; do [[ "${arg}" == "--full" ]] && FULL=1; done

if [[ "${DRY_RUN}" != "1" ]]; then
  read -r -p "Type the project id (${PROJECT_ID}) to confirm teardown: " CONFIRM
  [[ "${CONFIRM}" == "${PROJECT_ID}" ]] || die "confirmation mismatch — aborting"
fi

run bq rm -r -f --dataset "${PROJECT_ID}:${QUALITY_DATASET}"
run bq rm -r -f --dataset "${PROJECT_ID}:${LANDING_DATASET}"
run bq rm -r -f --dataset "${PROJECT_ID}:${SRC_DATASET}"

run gcloud storage rm --recursive "gs://${DATAFLOW_BUCKET}" --project "${PROJECT_ID}"
run gcloud storage rm --recursive "gs://${MODELS_BUCKET}" --project "${PROJECT_ID}"

run gcloud artifacts repositories delete "${GAR_REPO}" --location "${REGION}" --project "${PROJECT_ID}" --quiet

# Kill-switch function + topic go LAST — keep the cost cap armed while the
# buckets/GAR/BQ deletes above run, in case teardown itself misbehaves.
run gcloud functions delete "${KILL_FUNCTION}" --gen2 --region "${REGION}" --project "${PROJECT_ID}" --quiet
run gcloud pubsub topics delete "${BUDGET_TOPIC}" --project "${PROJECT_ID}" --quiet
# Budget lives on the billing account — delete by looked-up id (skip in dry-run).
if [[ "${DRY_RUN}" != "1" ]]; then
  BUDGET_ID="$(gcloud billing budgets list --billing-account "${BILLING_ACCOUNT_ID}" \
    --filter 'displayName=sdfb-e2e-budget' --format 'value(name)' | head -1)"
  [[ -n "${BUDGET_ID}" ]] && run gcloud billing budgets delete "${BUDGET_ID}" --quiet
else
  echo "+ gcloud billing budgets delete <looked-up sdfb-e2e-budget id> --quiet"
fi

if [[ "${FULL}" == "1" ]]; then
  run gcloud projects delete "${PROJECT_ID}" --quiet
  log "project deletion requested — 30-day recovery window applies"
else
  log "granular teardown done (project kept; rerun scripts 01-07 to rebuild)"
fi
