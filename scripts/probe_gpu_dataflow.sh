#!/usr/bin/env bash
# 1-row Dataflow GPU probe — confirms a GPU worker (L4 by default, T4
# selectable via GPU_MACHINE_TYPE/GPU_ACCELERATOR) boots from the
# CI-published image and a single Beam bundle runs to completion.
#
# IMPORTANT: this script does NOT build anything. The image must already
# be pushed to JFrog by `.github/workflows/1_build_python_beam.yaml`
# (see ADR-0008). The script just submits a Dataflow job pointing at the
# existing image.
#
# Acceptance gate for M1 §10 (see .claude/agents/gpu-image-builder.md):
#   1. Image pulls without auth errors.
#   2. NVIDIA driver installs (nvidia-smi succeeds in worker startup logs).
#   3. GPU metric is non-zero in Cloud Monitoring.
#   4. Cold start < 3 min.
#
# Required env (typically from .envrc on the M4):
#   ARTIFACTORY_HOSTNAME, ARTIFACTORY_NAMESPACE, GCP_REGION, GCP_ZONE
#
# Usage:
#   ./scripts/probe_gpu_dataflow.sh                # tag = current short SHA
#   ./scripts/probe_gpu_dataflow.sh v0.1.0         # tag = v0.1.0
set -euo pipefail

: "${ARTIFACTORY_HOSTNAME:?Set ARTIFACTORY_HOSTNAME in .envrc}"
: "${ARTIFACTORY_NAMESPACE:?Set ARTIFACTORY_NAMESPACE in .envrc}"
: "${GCP_REGION:?Set GCP_REGION in .envrc (e.g. europe-west3)}"
: "${GCP_ZONE:?Set GCP_ZONE in .envrc (e.g. europe-west3-b)}"

# GPU target — defaults to L4/g2 (the only europe-west3 GPU that runs Gemma 4).
# For a real-GPU PLUMBING smoke on abundant N1/T4 capacity (Gemma 4 CANNOT run
# on a T4 — Turing SM 7.5; pair with a small Turing-compatible model), override:
#   GPU_MACHINE_TYPE=n1-standard-8 \
#   GPU_ACCELERATOR='type:nvidia-tesla-t4;count:1;install-nvidia-driver:5xx' \
#   GCP_ZONE=europe-west3-b ./scripts/probe_gpu_dataflow.sh
GPU_MACHINE_TYPE="${GPU_MACHINE_TYPE:-g2-standard-8}"
GPU_ACCELERATOR="${GPU_ACCELERATOR:-type:nvidia-l4;count:1;install-nvidia-driver}"

VERSION="${1:-$(git rev-parse --short HEAD)}"
PROJECT="$(gcloud config get-value project)"
JOB_NAME="sdfb-probe-$(date +%s)"
IMAGE="${ARTIFACTORY_HOSTNAME}/dkr-public-local/${ARTIFACTORY_NAMESPACE}/sdfb-python:${VERSION}"

echo "==> Probe target"
echo "    Project: ${PROJECT}"
echo "    Region:  ${GCP_REGION}"
echo "    Zone:    ${GCP_ZONE}"
echo "    Machine: ${GPU_MACHINE_TYPE}"
echo "    GPU:     ${GPU_ACCELERATOR}"
echo "    Image:   ${IMAGE}"
echo "    Job:     ${JOB_NAME}"

# Verify both Secret Manager entries exist; either missing is fatal for image pull.
for secret in ARTIFACTORY_RELEASER_USERNAME ARTIFACTORY_RELEASER_PASSWORD; do
    if ! gcloud secrets describe "${secret}" --project="${PROJECT}" >/dev/null 2>&1; then
        echo "ERROR: Secret ${secret} missing in project ${PROJECT}." >&2
        echo "       Bank pattern is to manage these via GSM; ask platform team." >&2
        exit 1
    fi
done

# Ensure temp/staging buckets exist (idempotent).
gsutil ls "gs://${PROJECT}-dataflow" >/dev/null 2>&1 \
    || gsutil mb -l "${GCP_REGION}" "gs://${PROJECT}-dataflow"

echo "==> Submitting probe pipeline"
uv run python - <<PY
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions

options = PipelineOptions([
    "--runner=DataflowRunner",
    "--project=${PROJECT}",
    "--region=${GCP_REGION}",
    "--worker_zone=${GCP_ZONE}",
    "--worker_machine_type=${GPU_MACHINE_TYPE}",
    "--sdk_container_image=${IMAGE}",
    "--experiments=use_runner_v2",
    "--dataflow_service_options=worker_accelerator=${GPU_ACCELERATOR}",
    # Consume a matching L4 reservation if one exists (capacity guarantee vs the
    # europe-west3 g2 STOCKOUT). ANY-reservation affinity → on-demand fallback
    # when none matches, so it's inert until a reservation is created. Beam
    # accumulates repeated --dataflow_service_options into a list.
    "--dataflow_service_options=automatically_use_created_reservation",
    "--worker_disk_type=compute.googleapis.com/projects/${PROJECT}/regions/${GCP_REGION}/diskTypes/pd-ssd",
    "--worker_disk_size_gb=200",
    "--temp_location=gs://${PROJECT}-dataflow/temp",
    "--staging_location=gs://${PROJECT}-dataflow/staging",
    "--job_name=${JOB_NAME}",
    "--num_workers=1",
    "--max_num_workers=1",
    "--image-repository-username-secret-id=projects/${PROJECT}/secrets/ARTIFACTORY_RELEASER_USERNAME",
    "--image-repository-password-secret-id=projects/${PROJECT}/secrets/ARTIFACTORY_RELEASER_PASSWORD",
])

with beam.Pipeline(options=options) as p:
    (
        p
        | "OneRow" >> beam.Create(["hello-gpu"])
        | "LogIt" >> beam.Map(lambda x: print(f"probe got: {x}"))
    )
PY

echo
echo "==> Probe submitted. Follow at:"
echo "    https://console.cloud.google.com/dataflow/jobs?project=${PROJECT}&region=${GCP_REGION}"
echo "    Job name: ${JOB_NAME}"
