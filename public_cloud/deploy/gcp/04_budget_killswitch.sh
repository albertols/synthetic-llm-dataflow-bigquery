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
BUDGET_DISPLAY="sdfb-e2e-budget"
if [[ "${DRY_RUN}" != "1" ]] && gcloud billing budgets list --billing-account "${BILLING_ACCOUNT_ID}" \
     --format 'value(displayName)' 2>/dev/null | grep -qx "${BUDGET_DISPLAY}"; then
  log "budget ${BUDGET_DISPLAY} exists"
else
  run gcloud billing budgets create \
    --billing-account "${BILLING_ACCOUNT_ID}" \
    --display-name "${BUDGET_DISPLAY}" \
    --budget-amount "${BUDGET_AMOUNT}" \
    --filter-projects "projects/${PROJECT_ID}" \
    --threshold-rule percent=0.5 \
    --threshold-rule percent=0.8 \
    --threshold-rule percent=0.9 \
    --notifications-rule-pubsub-topic "projects/${PROJECT_ID}/topics/${BUDGET_TOPIC}"
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
