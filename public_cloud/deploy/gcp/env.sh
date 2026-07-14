#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# THE manifest for the personal GCP E2E project. Every resource name derives
# from here — edit PROJECT_SUFFIX + BILLING_ACCOUNT_ID (or export them),
# everything else is convention. Keep names Terraform-friendly (future port).
# Spec: docs/superpowers/specs/2026-07-14-personal-gcp-e2e-design.md
# ---------------------------------------------------------------------------

# --- user-provided (export or edit) ---------------------------------------
PROJECT_SUFFIX="${PROJECT_SUFFIX:-}"           # e.g. "serna-01"
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
IMAGE_TAG="${IMAGE_TAG:-personal-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}"
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
