#!/usr/bin/env bash
# Build the Flex Template spec from the pushed image + mainline metadata.
# Usage: ./07_build_flex_template.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX

run gcloud dataflow flex-template build "${TEMPLATE_PATH}" \
  --image "${IMAGE_URI}" \
  --sdk-language PYTHON \
  --metadata-file "${REPO_ROOT}/docker/flex_template_metadata.json" \
  --project "${PROJECT_ID}"

log "template: ${TEMPLATE_PATH}"
