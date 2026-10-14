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
# Build an EVALUATOR-ONLY Dataflow flex template from an existing image.
# This script builds no image.
#
# This repository's deployment does not need it. The deployment builds one
# image for generation and evaluation (docker/Dockerfile at the repository
# root) and one template from it, and that template runs both jobs: an
# evaluation launch passes the parameter sdfb_job=evaluation. Use this script
# only for a second template that declares the evaluator's parameters alone
# (deploy/flex_template_metadata.json), built on
#   - that same shared image, or
#   - the standalone CPU image of this package (docker/Dockerfile here), for
#     the package copied out as a unit of its own.
#
# The template path is
#   gs://${TEMPLATES_BUCKET}/synthetic/sdfb-evaluation-${VERSION}-template.json
#
# Launching it:
#   - Always pass sdfb_job=evaluation. On the shared image the template's
#     entry is a dispatcher (docker/flex_entry.py at the repository root) that
#     runs GENERATION unless the launch says otherwise. On the standalone
#     image the entry is the evaluator itself and the parameter has no effect.
#   - On the shared image also pass disk_size_gb=200: that image is multi-GB
#     and Dataflow's default worker disk overflows while a worker unpacks it.
#   - Workers use the SAME image as the launcher. The image carries its own
#     coordinate and the driver applies it as the workers'
#     sdk_container_image when the launch gives none; an explicit
#     `--sdk_container_image=<IMAGE>` wins (README, "Dataflow").
# This script only builds the template; the launch flags
# (`--experiments=upload_graph` among them) are the CLI's, not this script's.
#
# Environment (no defaults for the first three):
#   IMAGE             the pushed image the template launches, full coordinate
#   PROJECT_ID        GCP project the template build runs in
#   TEMPLATES_BUCKET  GCS bucket (name only) that holds the template file
#   VERSION           optional; default: EVALUATOR_VERSION in
#                     src/sdfb_evaluation/version.py
#
# Run from anywhere on a machine with gcloud; it needs GCP access.
set -euo pipefail

: "${IMAGE:?set IMAGE: the pushed image to build the template from (this script builds none)}"
: "${PROJECT_ID:?set PROJECT_ID}"
: "${TEMPLATES_BUCKET:?set TEMPLATES_BUCKET}"

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "${VERSION:-}" ]]; then
  VERSION="$(sed -n 's/^EVALUATOR_VERSION = "\(.*\)"$/\1/p' \
    "${PACKAGE_DIR}/src/sdfb_evaluation/version.py")"
fi
: "${VERSION:?could not read EVALUATOR_VERSION}"

TEMPLATE="gs://${TEMPLATES_BUCKET}/synthetic/sdfb-evaluation-${VERSION}-template.json"

gcloud dataflow flex-template build "${TEMPLATE}" \
  --project "${PROJECT_ID}" \
  --image "${IMAGE}" \
  --sdk-language PYTHON \
  --metadata-file "${PACKAGE_DIR}/deploy/flex_template_metadata.json"

echo "image:    ${IMAGE}"
echo "template: ${TEMPLATE}"
echo "launch:   pass sdfb_job=evaluation (and disk_size_gb=200 on the shared image)"
