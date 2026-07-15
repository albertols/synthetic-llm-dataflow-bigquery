#!/usr/bin/env bash
# Submit one E2E tier, poll to terminal state, journal the run.
# Usage: ./run_e2e.sh <tier> <table> [--dry-run]
#   tiers:  S0 R1p R2p R3p N4 P6 P7   (R1p == R1' — shell-safe names)
#   tables: citibike hacker_news
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

TIER="${1:-}"; TABLE="${2:-}"
[[ -n "${TIER}" && -n "${TABLE}" ]] || die "usage: run_e2e.sh <tier> <table> [--dry-run]"
require_env PROJECT_SUFFIX

# Resolve the preset. Capture-then-eval so a renderer failure (unknown
# tier/table) dies here — eval "$( )" would swallow the exit status.
RENDERED="$(uv run --no-sync python3 "${SCRIPT_DIR}/lib/render_tier.py" \
  --tiers "${SCRIPT_DIR}/tiers.yaml" --tier "${TIER}" --table "${TABLE}" \
  --var "PROJECT_ID=${PROJECT_ID}" --var "MODELS_BUCKET=${MODELS_BUCKET}" \
  --var "DATAFLOW_BUCKET=${DATAFLOW_BUCKET}")" || die "tier/table rejected by render_tier.py"
eval "${RENDERED}"

TIER_LC="$(echo "${TIER}" | tr '[:upper:]' '[:lower:]')"
RUN_ID="${TIER_LC}-${TABLE//_/-}-$(date -u +%Y%m%d-%H%M%S)"
JOB_NAME="sdfb-${RUN_ID}"

CMD=(gcloud dataflow flex-template run "${JOB_NAME}"
  --template-file-gcs-location "${TEMPLATE_PATH}"
  --project "${PROJECT_ID}" --region "${REGION}"
  --service-account-email "${WORKER_SA}"
  --temp-location "${TEMP_LOCATION}" --staging-location "${STAGING_LOCATION}"
  --worker-machine-type "${MACHINE_TYPE}" --max-workers "${MAX_WORKERS}"
  --additional-user-labels "tier=${TIER_LC},table=${TABLE}"
  --parameters "${PARAMS},run_id=${RUN_ID}"
  --format 'value(job.id)')
[[ -n "${ACCELERATOR}" ]] && CMD+=(--additional-experiments "worker_accelerator=${ACCELERATOR}")

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "+ ${CMD[*]}"
  JOB_ID="DRY-RUN"
else
  JOB_ID="$("${CMD[@]}")"
  log "submitted job ${JOB_ID} (run_id=${RUN_ID})"
fi

STATE="JOB_STATE_UNKNOWN"
if [[ "${DRY_RUN}" != "1" ]]; then
  # Poll to a terminal state (image+model pull can take ~10 min; cap 90 min).
  for _ in $(seq 1 90); do
    STATE="$(gcloud dataflow jobs describe "${JOB_ID}" --project "${PROJECT_ID}" \
      --region "${REGION}" --format 'value(currentState)' || echo JOB_STATE_UNKNOWN)"
    case "${STATE}" in
      JOB_STATE_DONE|JOB_STATE_FAILED|JOB_STATE_CANCELLED|JOB_STATE_DRAINED) break ;;
      *) sleep 60 ;;
    esac
  done
  log "terminal state: ${STATE}"

  # A non-terminal state here means the poll loop exhausted its 90 iterations
  # with the job still running — cancel it (best-effort) so it doesn't burn
  # money unattended, then journal and die.
  TIMED_OUT=0
  case "${STATE}" in
    JOB_STATE_DONE|JOB_STATE_FAILED|JOB_STATE_CANCELLED|JOB_STATE_DRAINED) ;;
    *)
      TIMED_OUT=1
      run gcloud dataflow jobs cancel "${JOB_ID}" --project "${PROJECT_ID}" --region "${REGION}" || true
      ;;
  esac

  # Journal every run, pass or fail (the deployed-commands ledger) — before verdict.
  printf '{"ts":"%s","tier":"%s","table":"%s","run_id":"%s","job_id":"%s","state":"%s","command":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${TIER}" "${TABLE}" "${RUN_ID}" "${JOB_ID}" "${STATE}" \
    "$(echo "${CMD[*]}" | sed 's/"/\\"/g')" >> "${SCRIPT_DIR}/journal/runs.jsonl"

  [[ "${TIMED_OUT}" == "1" ]] && die "poll timed out; job ${JOB_ID} cancel requested (state ${STATE})"

  if [[ "${EXPECT}" == "SUCCESS" && "${STATE}" != "JOB_STATE_DONE" ]]; then
    log "FAILED — last 50 error log lines (full mining belongs to e2e_gcp_probe.py):"
    gcloud logging read \
      "resource.type=dataflow_step AND resource.labels.job_id=${JOB_ID} AND severity>=ERROR" \
      --project "${PROJECT_ID}" --limit 50 --format 'value(timestamp,textPayload)' || true
    die "tier ${TIER} expected SUCCESS, got ${STATE} (job ${JOB_ID})"
  fi
  if [[ "${EXPECT}" == "FAIL" && "${STATE}" != "JOB_STATE_FAILED" ]]; then
    die "tier ${TIER} expected FAILURE, got ${STATE} (job ${JOB_ID})"
  fi
fi

log "verdict: ${TIER}/${TABLE} matched EXPECT=${EXPECT}"
log "next — report recipe (RUN_PLAYBOOK §5):"
log "  uv run --no-sync python3 scripts/e2e_gcp_probe.py --project ${PROJECT_ID} \\"
log "    --source-fqn <SOURCE_FQN> --landing-fqn <LANDING_FQN> --quality-dataset ${PROJECT_ID}.${QUALITY_DATASET} \\"
log "    --region ${REGION} --pk <pk_cols> --job-id ${JOB_ID} --run-id ${RUN_ID} --out integration_test/${JOB_ID}/e2e_gcp_metrics.json"
log "  then e2e_validation_analysis.py + e2e_bundle_export.py -> integration_test/${JOB_ID}/"
