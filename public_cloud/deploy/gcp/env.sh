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

# ---------------------------------------------------------------------------
# THE manifest for the personal GCP E2E project. Every resource name derives
# from here — edit PROJECT_SUFFIX + BILLING_ACCOUNT_ID (or export them),
# everything else is convention. Keep names Terraform-friendly (future port).
# Decision: docs/adr/0016-personal-gcp-cloud-build.md
# ---------------------------------------------------------------------------
_GCP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- user-provided (export or edit) ---------------------------------------
PROJECT_SUFFIX="${PROJECT_SUFFIX:-}"           # e.g. "myname-01"
BILLING_ACCOUNT_ID="${BILLING_ACCOUNT_ID:-}"   # gcloud billing accounts list
BUDGET_AMOUNT="${BUDGET_AMOUNT:-25}"           # per month, billing-account currency

# --- fixed decisions --------------------------------------------------------
REGION="us-central1"                           # T4 depth + US public datasets (spec §Region)
BQ_LOCATION="US"
KILL_THRESHOLD="0.9"                           # billing-detach at 90% of budget

# --- derived names ----------------------------------------------------------
PROJECT_ID="sdfb-e2e-${PROJECT_SUFFIX}"
MODELS_BUCKET="${PROJECT_ID}-models"
DATAFLOW_BUCKET="${PROJECT_ID}-dataflow"
GAR_REPO="sdfb"
IMAGE_NAME="sdfb-python"
# Prefer a persisted tag (written by 06_build_image.sh after a real build) so
# IMAGE_TAG can't drift with HEAD between "build the image" and "run the job"
# — falls back to deriving from HEAD only if nothing was ever persisted.
IMAGE_TAG="${IMAGE_TAG:-$( [ -f "${_GCP_DIR}/journal/image_tag" ] && cat "${_GCP_DIR}/journal/image_tag" || echo "personal-$(git rev-parse --short HEAD 2>/dev/null || echo dev)" )}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${GAR_REPO}/${IMAGE_NAME}:${IMAGE_TAG}"

WORKER_SA_NAME="sdfb-dataflow-worker"
WORKER_SA="${WORKER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD_SA_NAME="sdfb-build"
BUILD_SA="${BUILD_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KILL_SA_NAME="sdfb-killswitch"
KILL_SA="${KILL_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

SRC_DATASET="synthetic_source"
LANDING_DATASET="synthetic_data"
QUALITY_DATASET="synthetic_data_quality"

BUDGET_TOPIC="budget-alerts"
KILL_FUNCTION="billing-killswitch"

STAGING_LOCATION="gs://${DATAFLOW_BUCKET}/staging"
TEMP_LOCATION="gs://${DATAFLOW_BUCKET}/temp"
TEMPLATE_PATH="gs://${DATAFLOW_BUCKET}/templates/sdfb-${IMAGE_TAG}-template.json"

# Model layout per docs/MODEL_LAYOUT.md — the only two staged models (spec §Models).
MODEL_URI="gs://${MODELS_BUCKET}/synthetic/models/qwen3/4b-instruct-2507/v1/"
EMBEDDER_URI="gs://${MODELS_BUCKET}/synthetic/models/embedders/bge-small-en-v1.5/v1/"
