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
#
# Build the evaluator's CPU image with Cloud Build, push it to Artifact
# Registry and build the Dataflow flex template that points at it.
#
# The template path is
#   gs://${TEMPLATES_BUCKET}/synthetic/sdfb-evaluation-${VERSION}-template.json
#
# Workers use the SAME image: launch the template with the Beam argument
# `--sdk_container_image=<IMAGE>` (README, "Dataflow"). This script only
# builds; the launch flags (`--experiments=upload_graph` among them) are the
# CLI's, not this script's.
#
# The image bakes its own coordinate (build argument
# SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG), which the driver applies as the workers'
# sdk_container_image when the launch gives none.
#
# `gcloud builds submit` uploads the repository root; there is no
# .gcloudignore or .dockerignore there, so gcloud falls back to .gitignore.
#
# Environment (no defaults for the first four):
#   PROJECT_ID        GCP project that builds and owns the image
#   REGION            Artifact Registry and Cloud Build region
#   REPOSITORY        Artifact Registry Docker repository
#   TEMPLATES_BUCKET  GCS bucket (name only) that holds the template file
#   VERSION           optional; default: EVALUATOR_VERSION in
#                     src/sdfb_evaluation/version.py
#
# Run from anywhere on a machine with gcloud; it needs GCP access.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION}"
: "${REPOSITORY:?set REPOSITORY}"
: "${TEMPLATES_BUCKET:?set TEMPLATES_BUCKET}"

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${PACKAGE_DIR}/../.." && pwd)"

if [[ -z "${VERSION:-}" ]]; then
  VERSION="$(sed -n 's/^EVALUATOR_VERSION = "\(.*\)"$/\1/p' \
    "${PACKAGE_DIR}/src/sdfb_evaluation/version.py")"
fi
: "${VERSION:?could not read EVALUATOR_VERSION}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/sdfb-evaluation:${VERSION}"
TEMPLATE="gs://${TEMPLATES_BUCKET}/synthetic/sdfb-evaluation-${VERSION}-template.json"

# The Docker build context is the repository root; the Dockerfile lives in
# the package, so Cloud Build reads a cloudbuild config naming it.
CONFIG="$(mktemp)"
trap 'rm -f "${CONFIG}"' EXIT
cat > "${CONFIG}" <<YAML
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "packages/sdfb-evaluation/docker/Dockerfile", "--build-arg", "SDFB_EVAL_SDK_CONTAINER_IMAGE_ARG=${IMAGE}", "-t", "${IMAGE}", "."]
images: ["${IMAGE}"]
YAML

gcloud builds submit "${REPO_ROOT}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --config "${CONFIG}"

gcloud dataflow flex-template build "${TEMPLATE}" \
  --project "${PROJECT_ID}" \
  --image "${IMAGE}" \
  --sdk-language PYTHON \
  --metadata-file "${PACKAGE_DIR}/deploy/flex_template_metadata.json"

echo "image:    ${IMAGE}"
echo "template: ${TEMPLATE}"
