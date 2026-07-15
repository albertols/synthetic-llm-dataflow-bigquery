#!/usr/bin/env bash
# Hard cost cap: budget -> Pub/Sub -> Cloud Function detaches billing at 90%.
# Idempotent. Usage: ./04_budget_killswitch.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX BILLING_ACCOUNT_ID

if probe gcloud pubsub topics describe "${BUDGET_TOPIC}" --project "${PROJECT_ID}"; then
  log "topic ${BUDGET_TOPIC} exists"
else
  run gcloud pubsub topics create "${BUDGET_TOPIC}" --project "${PROJECT_ID}"
fi

# Budget: idempotency via display-name lookup (budgets have server-side ids).
# When it already exists, update the amount instead of just logging — budget
# costAmount is calendar-month cumulative, so raising BUDGET_AMOUNT and
# re-running this script is how mid-month kill-switch recovery works (README
# §6): without the update, rerunning would silently no-op against the OLD
# (already-exceeded) amount and the function would re-detach within hours.
BUDGET_DISPLAY="sdfb-e2e-budget"
create_budget() {
  run gcloud billing budgets create \
    --billing-account "${BILLING_ACCOUNT_ID}" \
    --display-name "${BUDGET_DISPLAY}" \
    --budget-amount "${BUDGET_AMOUNT}" \
    --filter-projects "projects/${PROJECT_ID}" \
    --threshold-rule percent=0.5 \
    --threshold-rule percent=0.8 \
    --threshold-rule percent=0.9 \
    --notifications-rule-pubsub-topic "projects/${PROJECT_ID}/topics/${BUDGET_TOPIC}"
}

if [[ "${DRY_RUN}" != "1" ]]; then
  BUDGET_ID="$(gcloud billing budgets list --billing-account "${BILLING_ACCOUNT_ID}" \
    --filter "displayName=${BUDGET_DISPLAY}" --format 'value(name)' | head -1)"
  if [[ -n "${BUDGET_ID}" ]]; then
    log "budget ${BUDGET_DISPLAY} exists (${BUDGET_ID}) — updating amount to ${BUDGET_AMOUNT}"
    run gcloud billing budgets update "${BUDGET_ID}" --budget-amount "${BUDGET_AMOUNT}"
  else
    create_budget
  fi
else
  # Can't probe existing budgets without creds in dry-run; print both the
  # create form and a placeholder for the update form (mid-month recovery).
  create_budget
  echo "+ gcloud billing budgets update <looked-up ${BUDGET_DISPLAY} id> --budget-amount ${BUDGET_AMOUNT}  # if it already exists"
fi

run gcloud functions deploy "${KILL_FUNCTION}" \
  --gen2 --project "${PROJECT_ID}" --region "${REGION}" \
  --runtime python311 --source "${SCRIPT_DIR}/killswitch" \
  --entry-point on_budget_alert \
  --trigger-topic "${BUDGET_TOPIC}" \
  --service-account "${KILL_SA}" \
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID},KILL_THRESHOLD=${KILL_THRESHOLD}" \
  --max-instances 1 --memory 256Mi

log "cost cap armed: ${BUDGET_AMOUNT}/month, detach at ${KILL_THRESHOLD}"
