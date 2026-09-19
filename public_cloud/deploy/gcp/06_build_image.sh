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
  --substitutions "_IMAGE_URI=${IMAGE_URI},_GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  --service-account "projects/${PROJECT_ID}/serviceAccounts/${BUILD_SA}" \
  --project "${PROJECT_ID}" --region "${REGION}"

# Persist the tag so 07/run_e2e.sh pick up THIS build, not whatever HEAD is
# at their invocation time (env.sh reads this file back).
[[ "${DRY_RUN}" == "1" ]] || printf '%s' "${IMAGE_TAG}" > "${SCRIPT_DIR}/journal/image_tag"

log "image: ${IMAGE_URI}"
