#!/usr/bin/env bash
# Submit the model-staging Cloud Build (downloads + validates + uploads).
# Prereq (once): gcloud secrets create kaggle-username / kaggle-key (README).
# Usage: ./05_stage_models.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX

# availableSecrets resolution happens BEFORE any step runs — missing secrets
# hard-fail the build, so gate here with instructions (dry-run: probe says
# absent but we only log, since no build is submitted). Probe BOTH secrets —
# either one missing hard-fails the Cloud Build the same way.
if ! probe gcloud secrets describe kaggle-username --project "${PROJECT_ID}" \
   || ! probe gcloud secrets describe kaggle-key --project "${PROJECT_ID}"; then
  MSG="secrets kaggle-username/kaggle-key missing. Create them first:
  printf '%s' '<kaggle user>' | gcloud secrets create kaggle-username --data-file=- --project ${PROJECT_ID}
  printf '%s' '<kaggle key>'  | gcloud secrets create kaggle-key --data-file=- --project ${PROJECT_ID}"
  if [[ "${DRY_RUN}" == "1" ]]; then log "WOULD REQUIRE: ${MSG}"; else die "${MSG}"; fi
fi

run gcloud builds submit --no-source \
  --config "${SCRIPT_DIR}/cloudbuild/stage_models.yaml" \
  --substitutions "_MODELS_BUCKET=${MODELS_BUCKET}" \
  --service-account "projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
  --project "${PROJECT_ID}" --region "${REGION}"

log "verify: gcloud storage ls -r gs://${MODELS_BUCKET}/synthetic/models/"
