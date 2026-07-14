#!/usr/bin/env bash
# Build + push the sdfb image via Cloud Build (no local Docker; ADR 0016).
# GAR keep-newest-1 cleanup policy (01) caps stored images automatically.
# Usage: ./06_build_image.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX

cd "${REPO_ROOT}"
run gcloud builds submit . \
  --config "${SCRIPT_DIR}/cloudbuild/build_image.yaml" \
  --substitutions "_IMAGE_URI=${IMAGE_URI}" \
  --service-account "projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
  --project "${PROJECT_ID}" --region "${REGION}"

log "image: ${IMAGE_URI}"
