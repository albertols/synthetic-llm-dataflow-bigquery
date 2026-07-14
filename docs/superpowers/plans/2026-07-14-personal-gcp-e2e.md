# Personal GCP E2E Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cost-hard-capped personal-GCP deployment layer (`public_cloud/deploy/gcp/`) that runs the T4 E2E integration-test matrix (S0 · R1' · R2' · R3' · N4 · P6 · P7) via Cloud Build + `gcloud dataflow flex-template run`, with zero mainline code edits.

**Architecture:** Reference overlay — new bash scripts/YAMLs reference mainline artifacts (`docker/Dockerfile`, `docker/flex_template_metadata.json`, `scripts/e2e_*.py`, `scripts/deployment_prerequisites.py`, `scripts/extract_ddl.py`, `scripts/derive_landing_schema.py`) by path. All infra via idempotent numbered gcloud scripts sourcing one `env.sh` manifest. Cost ceiling enforced by Budget → Pub/Sub → Cloud Function billing-detach.

**Tech Stack:** bash (`set -euo pipefail`), gcloud/bq/gsutil, Cloud Build, Python 3.11 (killswitch fn, tier renderer), PyYAML, pytest (laptop-safe tests in `packages/sdfb-tests`).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-14-personal-gcp-e2e-design.md` — locked decisions table governs.
- **Zero mainline edits** except: new test files under `packages/sdfb-tests/tests/unit/public_cloud/`, index updates to `CLAUDE.md` / `docs/ROADMAP.md` / `docs/adr/README.md`, one `.gitignore` line, new ADR 0016.
- Region `us-central1`, BQ location `US`. No Vertex AI, no HF Hub at runtime, no external LLM APIs, no Airflow.
- Models restricted to `qwen3/4b-instruct-2507/v1` (ModelScope) + `embedders/bge-small-en-v1.5/v1` (Kaggle, ModelScope fallback).
- Every script: `set -euo pipefail`, idempotent, supports `--dry-run` (prints commands, never calls gcloud/bq/gsutil).
- Laptop test suite must stay green after every task: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q` (353+ tests).
- Run all commands from the repo root: `/Users/serna/IdeaProjects/synthetic-llm-dataflow-bigquery`.
- Commit messages: `feat:`/`docs:`/`test:` prefixes, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

**Ground truth discovered during planning (do not re-derive):**
- `uv.lock` contains **zero** JFrog URLs (locked to `files.pythonhosted.org`) → `uv sync --frozen` works from Cloud Build unchanged. The ONLY corporate network dependency in `docker/Dockerfile` is line 77-79: `pip install uv --index-url "https://${ARTIFACTORY_HOSTNAME_ARG}/artifactory/api/pypi/pypi-all/simple"`. A Cloud Build workspace `sed` retargets it to `https://pypi.org/simple` (fail-loud grep after).
- Dockerfile build args: `BEAM_SDK_IMAGE` (default JFrog-prefixed; override to `docker.io/apache/beam_python3.11_sdk:2.71.0`), `LAUNCHER_IMAGE` (default `gcr.io/dataflow-templates-base/python311-template-launcher-base`, keep), `SDFB_SDK_CONTAINER_IMAGE_ARG` (must be the pushed GAR coordinate), `ENV_NAME_ARG`, `ARTIFACTORY_*` (leave at defaults).
- Flex template has exactly these 20 params (from `docker/flex_template_metadata.json`): `ddl_uri, reference_table, reference_rows_limit, landing_table, dlq_table, num_rows, batch_size, similarity, run_id, identity_cols, pk_cols, engine, model_uri, embedder_uri, validation_runs_table, env, client_type, vllm_dtype, vllm_max_model_len, seed`. Required (no `isOptional`): `ddl_uri, reference_table, landing_table, dlq_table, num_rows, run_id, engine, model_uri`.
- GPU/machine config is NOT a template param — passed at `flex-template run` time: `--worker-machine-type`, `--additional-experiments "worker_accelerator=type:nvidia-tesla-t4;count:1;install-nvidia-driver:5xx"` (T4 driver string from `composer/synthetic_beam_bigquery.py:244`).
- Worker disk is code-pinned at 200 GB (`_DEFAULT_WORKER_DISK_GB` in `run_pipeline.py`); Flex `diskSizeGb` does not propagate. Do nothing about it.
- DDL extractor: `uv run --no-sync python3 scripts/extract_ddl.py --project P --dataset D --table T --output_base ./output` (DirectRunner default) → writes `output/*_ddl.json` locally.
- Landing schema: `uv run --no-sync python3 scripts/derive_landing_schema.py <ddl.json> -o <out.json>` → bare-array schema for `bq mk`.
- DQ tables: `bq mk --schema config/bq_schema/synthetic_data_quality/dlq.schema.json --time_partitioning_type DAY --time_partitioning_field dlq_inserted_at` (and `validation_runs.schema.json` / `created_at`).
- Tier definitions (docs/E2E_TEST_MATRIX.md): P6 = `seed=42` run twice (repro check); P7 = `identity_cols=""` with `pk_cols` set (pk gate isolated); N4 = nonexistent `model_uri`, expect job FAILURE with zero fallback rows.
- `bigquery-public-data.new_york_citibike.citibike_trips` has **no PK column** → snapshot adds `GENERATE_UUID() AS trip_id`. `hacker_news.full` has integer `id` PK.
- `docs/superpowers/specs` is gitignored (intentional); `docs/superpowers/plans` is not.

## File Structure (final state)

```
public_cloud/deploy/gcp/
├── README.md                        # Task 14
├── env.sh                           # Task 1
├── lib/common.sh                    # Task 1
├── lib/render_tier.py               # Task 10
├── 01_bootstrap_project.sh          # Task 2
├── gar_cleanup_policy.json          # Task 2
├── 02_iam.sh                        # Task 3
├── 03_storage_bq.sh                 # Tasks 4 (infra) + 5 (data prep)
├── dataflow_bucket_lifecycle.json   # Task 4
├── 04_budget_killswitch.sh          # Task 7
├── 05_stage_models.sh               # Task 8
├── 06_build_image.sh                # Task 9
├── 07_build_flex_template.sh        # Task 9
├── run_e2e.sh                       # Task 11
├── teardown.sh                      # Task 12
├── tiers.yaml                       # Task 10
├── cloudbuild/build_image.yaml      # Task 9
├── cloudbuild/stage_models.yaml     # Task 8
├── killswitch/logic.py              # Task 6
├── killswitch/main.py               # Task 6
├── killswitch/requirements.txt      # Task 6
└── journal/.gitkeep                 # Task 1 (journal/*.jsonl gitignored)

packages/sdfb-tests/tests/unit/public_cloud/
├── test_scripts_dry_run.py          # Tasks 1-5, 7-9, 11, 12 (grows per task)
├── test_killswitch_logic.py         # Task 6
├── test_render_tier.py              # Task 10
└── test_tiers_matrix.py             # Task 10 (drift guard)

.claude/agents/{gcp-deploy-runner,e2e-interpreter,gcp-po-auditor}.md   # Task 13
.claude/skills/{gcp-project-ops,gcp-e2e-run,gcp-cost-audit}.md         # Task 13
docs/adr/0016-personal-gcp-cloud-build.md                              # Task 14
```

---

### Task 1: Scaffolding — `env.sh`, `lib/common.sh`, journal, gitignore, test harness

**Files:**
- Create: `public_cloud/deploy/gcp/env.sh`
- Create: `public_cloud/deploy/gcp/lib/common.sh`
- Create: `public_cloud/deploy/gcp/journal/.gitkeep`
- Modify: `.gitignore` (append 1 line)
- Test: `packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py`

**Interfaces:**
- Produces (consumed by every later task):
  - `env.sh` exports: `PROJECT_SUFFIX, BILLING_ACCOUNT_ID, PROJECT_ID, REGION, BQ_LOCATION, BUDGET_AMOUNT, KILL_THRESHOLD, MODELS_BUCKET, DATAFLOW_BUCKET, GAR_REPO, IMAGE_NAME, IMAGE_TAG, IMAGE_URI, WORKER_SA_NAME, WORKER_SA, BUILD_SA_NAME, BUILD_SA, KILL_SA_NAME, KILL_SA, SRC_DATASET, LANDING_DATASET, QUALITY_DATASET, BUDGET_TOPIC, KILL_FUNCTION, TEMPLATE_PATH, STAGING_LOCATION, TEMP_LOCATION, MODEL_URI, EMBEDDER_URI`
  - `common.sh` functions: `log <msg>`, `die <msg>` (exit 1), `run <cmd...>` (echo `+ cmd` when `DRY_RUN=1`, else exec), `probe <cmd...>` (returns 1 in dry-run = "absent", else silent exec), `require_env <VAR...>`
  - Script preamble convention (every later script starts):
    ```bash
    #!/usr/bin/env bash
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    source "${SCRIPT_DIR}/env.sh"
    source "${SCRIPT_DIR}/lib/common.sh" "$@"
    ```
  - Test helper `run_script(name, *args) -> CompletedProcess` with the standard fake env.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py`:

```python
"""Dry-run harness for public_cloud/deploy/gcp scripts.

Laptop-safe: --dry-run never invokes gcloud/bq/gsutil, so no creds or SDK
installs are needed. Each task appends assertions for its script here.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
GCP_DIR = REPO_ROOT / "public_cloud" / "deploy" / "gcp"

# Real PATH: run_e2e.sh --dry-run legitimately executes uv/python3 to render
# tiers.yaml. Dry-run never calls gcloud/bq/gsutil BY CONSTRUCTION (run/probe
# guards), which is what these tests enforce via output assertions.
FAKE_ENV = {
    "PROJECT_SUFFIX": "test123",
    "BILLING_ACCOUNT_ID": "000000-AAAAAA-BBBBBB",
    "IMAGE_TAG": "testtag",
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
}


def run_script(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GCP_DIR / name), *args, "--dry-run"],
        capture_output=True, text=True, env=FAKE_ENV, cwd=REPO_ROOT,
    )


def test_env_sh_derives_names():
    out = subprocess.run(
        ["bash", "-c", f'source "{GCP_DIR}/env.sh" && echo "$PROJECT_ID|$IMAGE_URI|$TEMPLATE_PATH|$MODEL_URI"'],
        capture_output=True, text=True, env=FAKE_ENV,
    )
    assert out.returncode == 0, out.stderr
    project, image, template, model = out.stdout.strip().split("|")
    assert project == "sdfb-e2e-test123"
    assert image == "us-central1-docker.pkg.dev/sdfb-e2e-test123/sdfb/sdfb-python:testtag"
    assert template == "gs://sdfb-e2e-test123-dataflow/templates/sdfb-testtag-template.json"
    assert model == "gs://sdfb-e2e-test123-models/synthetic/models/qwen3/4b-instruct-2507/v1/"


def test_common_sh_run_and_probe_honor_dry_run():
    snippet = (
        f'source "{GCP_DIR}/env.sh" && source "{GCP_DIR}/lib/common.sh" --dry-run && '
        'run gcloud projects create x && (probe gcloud projects describe x && echo FOUND || echo ABSENT)'
    )
    out = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, env=FAKE_ENV)
    assert out.returncode == 0, out.stderr
    assert "+ gcloud projects create x" in out.stdout
    assert "ABSENT" in out.stdout  # probe pretends resources are absent in dry-run


def test_all_scripts_pass_bash_syntax_check():
    scripts = sorted(GCP_DIR.glob("*.sh")) + sorted((GCP_DIR / "lib").glob("*.sh"))
    assert scripts, "no scripts found"
    for s in scripts:
        r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
        assert r.returncode == 0, f"{s.name}: {r.stderr}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py -v`
Expected: FAIL (env.sh does not exist).

- [ ] **Step 3: Create `public_cloud/deploy/gcp/env.sh`**

```bash
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
```

- [ ] **Step 4: Create `public_cloud/deploy/gcp/lib/common.sh`**

```bash
#!/usr/bin/env bash
# Shared helpers. Source AFTER env.sh, forwarding "$@" so --dry-run is seen:
#   source "${SCRIPT_DIR}/lib/common.sh" "$@"
set -euo pipefail

DRY_RUN="${DRY_RUN:-0}"
for _arg in "$@"; do
  [[ "${_arg}" == "--dry-run" ]] && DRY_RUN=1
done

log() { printf '[%s] %s\n' "$(basename "${BASH_SOURCE[1]:-script}")" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Mutating command: print in dry-run, execute otherwise.
run() {
  if [[ "${DRY_RUN}" == "1" ]]; then echo "+ $*"; else "$@"; fi
}

# Existence probe: in dry-run pretend ABSENT (return 1) so create paths print.
probe() {
  if [[ "${DRY_RUN}" == "1" ]]; then return 1; else "$@" >/dev/null 2>&1; fi
}

require_env() {
  local v
  for v in "$@"; do
    [[ -n "${!v:-}" ]] || die "${v} is not set — export it or edit env.sh (see public_cloud/deploy/gcp/README.md)"
  done
}
```

- [ ] **Step 5: journal dir + gitignore**

```bash
mkdir -p public_cloud/deploy/gcp/journal && touch public_cloud/deploy/gcp/journal/.gitkeep
```

Append to `.gitignore` (after the `docs/superpowers/specs` line):

```
public_cloud/deploy/gcp/journal/*.jsonl
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`
Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add public_cloud/deploy/gcp .gitignore packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: scaffold personal-GCP E2E layer (env manifest + dry-run harness)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `01_bootstrap_project.sh` + GAR cleanup policy

**Files:**
- Create: `public_cloud/deploy/gcp/01_bootstrap_project.sh`
- Create: `public_cloud/deploy/gcp/gar_cleanup_policy.json`
- Test: append to `packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py`

**Interfaces:**
- Consumes: Task 1 preamble convention, `run/probe/require_env`.
- Produces: project exists, billing linked, 12 APIs enabled, GAR repo `sdfb` with keep-newest-1 cleanup policy. Prints the two MANUAL steps (trial upgrade, T4 quota).

- [ ] **Step 1: Write the failing test** (append to `test_scripts_dry_run.py`)

```python
def test_bootstrap_dry_run_prints_expected_commands():
    r = run_script("01_bootstrap_project.sh")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "+ gcloud projects create sdfb-e2e-test123" in out
    assert "+ gcloud billing projects link sdfb-e2e-test123 --billing-account 000000-AAAAAA-BBBBBB" in out
    assert "dataflow.googleapis.com" in out and "billingbudgets.googleapis.com" in out
    assert "+ gcloud artifacts repositories create sdfb" in out
    assert "set-cleanup-policies" in out
    assert "MANUAL STEP" in out  # trial upgrade + T4 quota reminder
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py::test_bootstrap_dry_run_prints_expected_commands -v`
Expected: FAIL (script missing).

- [ ] **Step 3: Create `public_cloud/deploy/gcp/gar_cleanup_policy.json`**

```json
[
  {
    "name": "keep-newest-1",
    "action": {"type": "Keep"},
    "mostRecentVersions": {"keepCount": 1}
  },
  {
    "name": "delete-older-than-30d",
    "action": {"type": "Delete"},
    "condition": {"olderThan": "2592000s"}
  }
]
```

- [ ] **Step 4: Create `public_cloud/deploy/gcp/01_bootstrap_project.sh`**

```bash
#!/usr/bin/env bash
# Bootstrap the personal E2E project: project, billing link, APIs, GAR repo.
# Idempotent. Usage: ./01_bootstrap_project.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX BILLING_ACCOUNT_ID

APIS=(
  dataflow.googleapis.com compute.googleapis.com bigquery.googleapis.com
  storage.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
  cloudfunctions.googleapis.com run.googleapis.com eventarc.googleapis.com
  pubsub.googleapis.com billingbudgets.googleapis.com secretmanager.googleapis.com
)

if probe gcloud projects describe "${PROJECT_ID}"; then
  log "project ${PROJECT_ID} already exists"
else
  run gcloud projects create "${PROJECT_ID}" --name "${PROJECT_ID}"
fi

run gcloud billing projects link "${PROJECT_ID}" --billing-account "${BILLING_ACCOUNT_ID}"
run gcloud services enable "${APIS[@]}" --project "${PROJECT_ID}"

if probe gcloud artifacts repositories describe "${GAR_REPO}" --location "${REGION}" --project "${PROJECT_ID}"; then
  log "GAR repo ${GAR_REPO} already exists"
else
  run gcloud artifacts repositories create "${GAR_REPO}" \
    --repository-format docker --location "${REGION}" --project "${PROJECT_ID}" \
    --description "sdfb personal E2E images (keep-newest-1)"
fi
# Enforce the minimum-stored-images requirement by policy, not discipline.
run gcloud artifacts repositories set-cleanup-policies "${GAR_REPO}" \
  --location "${REGION}" --project "${PROJECT_ID}" \
  --policy "${SCRIPT_DIR}/gar_cleanup_policy.json" --no-dry-run

log "=========================================================================="
log "MANUAL STEP 1 — upgrade the free trial to a paid account (GPU quota stays 0"
log "  on trial; the remaining \$300 credit survives the upgrade):"
log "  https://console.cloud.google.com/billing/${BILLING_ACCOUNT_ID}"
log "MANUAL STEP 2 — request GPU quota for ${PROJECT_ID} (count 1 is usually"
log "  auto-approved in minutes): 'GPUs (all regions)' >= 1 AND"
log "  'NVIDIA T4 GPUs' (${REGION}) >= 1 at"
log "  https://console.cloud.google.com/iam-admin/quotas?project=${PROJECT_ID}"
log "=========================================================================="
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`
Expected: all PASS (syntax check now also covers the new script).

- [ ] **Step 6: Commit**

```bash
git add public_cloud/deploy/gcp packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: personal-GCP bootstrap script (project, billing, APIs, GAR keep-1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `02_iam.sh` — service accounts + project-level roles

**Files:**
- Create: `public_cloud/deploy/gcp/02_iam.sh`
- Test: append to `test_scripts_dry_run.py`

**Interfaces:**
- Consumes: `WORKER_SA/BUILD_SA/KILL_SA` names from `env.sh`.
- Produces: three SAs; worker SA project roles (`dataflow.worker`, `dataflow.developer`, `bigquery.dataEditor`, `bigquery.jobUser`, `artifactregistry.reader`); build SA project roles (`logging.logWriter`, `artifactregistry.writer`, `storage.objectAdmin`, `secretmanager.secretAccessor`); kill SA gets `roles/billing.admin` **on the billing account**. Bucket-level bindings happen in Task 4 (buckets must exist first).

- [ ] **Step 1: Write the failing test** (append)

```python
def test_iam_dry_run_grants_expected_roles():
    r = run_script("02_iam.sh")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for sa in ("sdfb-dataflow-worker", "sdfb-build", "sdfb-killswitch"):
        assert f"+ gcloud iam service-accounts create {sa}" in out
    for role in ("roles/dataflow.worker", "roles/dataflow.developer",
                 "roles/bigquery.dataEditor", "roles/bigquery.jobUser",
                 "roles/artifactregistry.reader"):
        assert role in out
    assert "roles/billing.admin" in out  # killswitch SA on the billing account
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py::test_iam_dry_run_grants_expected_roles -v`
Expected: FAIL.

- [ ] **Step 3: Create `public_cloud/deploy/gcp/02_iam.sh`**

```bash
#!/usr/bin/env bash
# Service accounts + minimal roles. Bucket-level grants live in 03 (need buckets).
# Idempotent. Usage: ./02_iam.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX BILLING_ACCOUNT_ID

create_sa() {  # <name> <display>
  if probe gcloud iam service-accounts describe "$1@${PROJECT_ID}.iam.gserviceaccount.com" --project "${PROJECT_ID}"; then
    log "SA $1 already exists"
  else
    run gcloud iam service-accounts create "$1" --display-name "$2" --project "${PROJECT_ID}"
  fi
}

grant() {  # <member> <role>
  run gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member "serviceAccount:$1" --role "$2" --condition None --quiet
}

create_sa "${WORKER_SA_NAME}" "sdfb Dataflow worker+launcher"
create_sa "${BUILD_SA_NAME}"  "sdfb Cloud Build"
create_sa "${KILL_SA_NAME}"   "sdfb billing killswitch"

# Worker/launcher: run flex-template jobs, read/write BQ, pull GAR image.
# (docs/DEPLOYMENT_PREREQUISITES.md minimal set + dataflow roles.)
grant "${WORKER_SA}" roles/dataflow.worker
grant "${WORKER_SA}" roles/dataflow.developer
grant "${WORKER_SA}" roles/bigquery.dataEditor
grant "${WORKER_SA}" roles/bigquery.jobUser
grant "${WORKER_SA}" roles/artifactregistry.reader

# Cloud Build (custom SA => CLOUD_LOGGING_ONLY in both cloudbuild YAMLs).
grant "${BUILD_SA}" roles/logging.logWriter
grant "${BUILD_SA}" roles/artifactregistry.writer
grant "${BUILD_SA}" roles/storage.objectAdmin
grant "${BUILD_SA}" roles/secretmanager.secretAccessor

# Killswitch: detach billing — needs billing.admin ON THE BILLING ACCOUNT.
run gcloud billing accounts add-iam-policy-binding "${BILLING_ACCOUNT_ID}" \
  --member "serviceAccount:${KILL_SA}" --role roles/billing.admin

log "IAM done. Worker SA: ${WORKER_SA}"
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`

- [ ] **Step 5: Commit**

```bash
git add public_cloud/deploy/gcp/02_iam.sh packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: personal-GCP IAM script (worker/build/killswitch SAs, minimal roles)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `03_storage_bq.sh` part 1 — buckets, datasets, DQ tables, bucket IAM

**Files:**
- Create: `public_cloud/deploy/gcp/03_storage_bq.sh`
- Create: `public_cloud/deploy/gcp/dataflow_bucket_lifecycle.json`
- Test: append to `test_scripts_dry_run.py`

**Interfaces:**
- Consumes: bucket/dataset names from `env.sh`; committed schemas `config/bq_schema/synthetic_data_quality/{dlq,validation_runs}.schema.json`.
- Produces: 2 buckets (dataflow bucket with temp/ 7-day lifecycle), 3 datasets, 2 DQ tables, bucket-level SA bindings. Data-prep functions arrive in Task 5 inside the same script (`stage_source_table` called at the bottom).

- [ ] **Step 1: Write the failing test** (append)

```python
def test_storage_bq_dry_run_creates_buckets_datasets_dq():
    r = run_script("03_storage_bq.sh")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "+ gcloud storage buckets create gs://sdfb-e2e-test123-models" in out
    assert "+ gcloud storage buckets create gs://sdfb-e2e-test123-dataflow" in out
    assert "lifecycle" in out
    for ds in ("synthetic_source", "synthetic_data", "synthetic_data_quality"):
        assert f"mk --dataset sdfb-e2e-test123:{ds}" in out
    assert "dlq_inserted_at" in out and "created_at" in out  # DQ partition fields
    assert "objectViewer" in out and "objectAdmin" in out    # bucket-level SA grants
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py::test_storage_bq_dry_run_creates_buckets_datasets_dq -v`
Expected: FAIL.

- [ ] **Step 3: Create `public_cloud/deploy/gcp/dataflow_bucket_lifecycle.json`**

```json
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"age": 7, "matchesPrefix": ["temp/", "staging/"]}
    }
  ]
}
```

- [ ] **Step 4: Create `public_cloud/deploy/gcp/03_storage_bq.sh`**

```bash
#!/usr/bin/env bash
# Buckets + BQ datasets + DQ tables + bucket IAM + source snapshots + landing
# tables. Idempotent. Usage: ./03_storage_bq.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX

# --- buckets ---------------------------------------------------------------
make_bucket() {  # <name>
  if probe gcloud storage buckets describe "gs://$1"; then
    log "bucket $1 exists"
  else
    run gcloud storage buckets create "gs://$1" \
      --project "${PROJECT_ID}" --location "${REGION}" --uniform-bucket-level-access
  fi
}
make_bucket "${MODELS_BUCKET}"
make_bucket "${DATAFLOW_BUCKET}"
run gcloud storage buckets update "gs://${DATAFLOW_BUCKET}" \
  --lifecycle-file "${SCRIPT_DIR}/dataflow_bucket_lifecycle.json"

# --- bucket-level IAM (deferred from 02: buckets must exist) ----------------
run gcloud storage buckets add-iam-policy-binding "gs://${MODELS_BUCKET}" \
  --member "serviceAccount:${WORKER_SA}" --role roles/storage.objectViewer
run gcloud storage buckets add-iam-policy-binding "gs://${DATAFLOW_BUCKET}" \
  --member "serviceAccount:${WORKER_SA}" --role roles/storage.objectAdmin
run gcloud storage buckets add-iam-policy-binding "gs://${MODELS_BUCKET}" \
  --member "serviceAccount:${BUILD_SA}" --role roles/storage.objectAdmin

# --- datasets ----------------------------------------------------------------
make_dataset() {  # <name>
  if probe bq show --dataset "${PROJECT_ID}:$1"; then
    log "dataset $1 exists"
  else
    run bq --location="${BQ_LOCATION}" mk --dataset "${PROJECT_ID}:$1"
  fi
}
make_dataset "${SRC_DATASET}"
make_dataset "${LANDING_DATASET}"
make_dataset "${QUALITY_DATASET}"

# --- DQ tables (committed schemas; DAY partitions per DEPLOYMENT_PREREQUISITES) --
if probe bq show "${PROJECT_ID}:${QUALITY_DATASET}.dlq"; then
  log "dlq table exists"
else
  run bq mk --table \
    --schema "${REPO_ROOT}/config/bq_schema/synthetic_data_quality/dlq.schema.json" \
    --time_partitioning_type DAY --time_partitioning_field dlq_inserted_at \
    "${PROJECT_ID}:${QUALITY_DATASET}.dlq"
fi
if probe bq show "${PROJECT_ID}:${QUALITY_DATASET}.validation_runs"; then
  log "validation_runs table exists"
else
  run bq mk --table \
    --schema "${REPO_ROOT}/config/bq_schema/synthetic_data_quality/validation_runs.schema.json" \
    --time_partitioning_type DAY --time_partitioning_field created_at \
    "${PROJECT_ID}:${QUALITY_DATASET}.validation_runs"
fi

log "storage + datasets + DQ tables done"
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`

- [ ] **Step 6: Commit**

```bash
git add public_cloud/deploy/gcp packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: personal-GCP storage/BQ script (buckets, datasets, DQ tables, bucket IAM)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `03_storage_bq.sh` part 2 — public-table snapshots, DDL extraction, landing tables

**Files:**
- Modify: `public_cloud/deploy/gcp/03_storage_bq.sh` (append data-prep section)
- Test: append to `test_scripts_dry_run.py`

**Interfaces:**
- Consumes: `scripts/extract_ddl.py --project P --dataset D --table T --output_base ./output` → `output/*_ddl.json`; `scripts/derive_landing_schema.py <ddl.json> -o <schema.json>`.
- Produces (Task 10's `tiers.yaml` depends on these EXACT names):
  - `synthetic_source.citibike_trips_50k` (with `trip_id` = GENERATE_UUID) and `synthetic_source.hacker_news_50k` (`id` PK)
  - DDL JSONs at `gs://${DATAFLOW_BUCKET}/ddl/citibike_trips_50k_ddl.json` and `gs://${DATAFLOW_BUCKET}/ddl/hacker_news_50k_ddl.json`
  - Landing tables `synthetic_data.citibike_trips_50k`, `synthetic_data.hacker_news_50k`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_storage_bq_dry_run_stages_snapshots_ddl_landing():
    r = run_script("03_storage_bq.sh")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "GENERATE_UUID() AS trip_id" in out
    assert "bigquery-public-data.new_york_citibike.citibike_trips" in out
    assert "bigquery-public-data.hacker_news.full" in out
    assert "LIMIT 50000" in out
    assert "extract_ddl.py" in out
    assert "gs://sdfb-e2e-test123-dataflow/ddl/citibike_trips_50k_ddl.json" in out
    assert "gs://sdfb-e2e-test123-dataflow/ddl/hacker_news_50k_ddl.json" in out
    assert "derive_landing_schema.py" in out
    assert "synthetic_data.citibike_trips_50k" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py::test_storage_bq_dry_run_stages_snapshots_ddl_landing -v`
Expected: FAIL.

- [ ] **Step 3: Append the data-prep section to `03_storage_bq.sh`** (before the final `log` line; replace that final log with the one below)

```bash
# --- one-time public-table snapshots (spec: snapshot-once cost strategy) -----
# Frozen 50k-row copies; reruns are no-ops via IF NOT EXISTS. citibike has no
# natural PK -> GENERATE_UUID() trip_id (pk_cols/identity_cols in tiers.yaml).
run bq query --use_legacy_sql=false --location="${BQ_LOCATION}" --project_id="${PROJECT_ID}" \
  "CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${SRC_DATASET}.citibike_trips_50k\` AS
   SELECT GENERATE_UUID() AS trip_id,
          tripduration, starttime, stoptime,
          start_station_id, start_station_name,
          end_station_id, end_station_name,
          bikeid, usertype, birth_year, gender
   FROM \`bigquery-public-data.new_york_citibike.citibike_trips\`
   WHERE tripduration IS NOT NULL
   LIMIT 50000"

run bq query --use_legacy_sql=false --location="${BQ_LOCATION}" --project_id="${PROJECT_ID}" \
  "CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${SRC_DATASET}.hacker_news_50k\` AS
   SELECT id, type, \`by\`, title, text, url, score, descendants, \`timestamp\`
   FROM \`bigquery-public-data.hacker_news.full\`
   WHERE type = 'story' AND text IS NOT NULL AND \`by\` IS NOT NULL
   LIMIT 50000"

# --- DDL extraction + landing tables ----------------------------------------
stage_table() {  # <table_name>
  local table="$1"
  local ddl_gcs="gs://${DATAFLOW_BUCKET}/ddl/${table}_ddl.json"
  run uv run --no-sync python3 "${REPO_ROOT}/scripts/extract_ddl.py" \
    --project "${PROJECT_ID}" --dataset "${SRC_DATASET}" --table "${table}" \
    --output_base "${REPO_ROOT}/output"
  local ddl_local
  if [[ "${DRY_RUN}" == "1" ]]; then
    ddl_local="${REPO_ROOT}/output/${table}_ddl.json"
  else
    ddl_local="$(ls "${REPO_ROOT}"/output/*"${table}"*_ddl.json | head -1)"
  fi
  run gsutil cp "${ddl_local}" "${ddl_gcs}"
  run uv run --no-sync python3 "${REPO_ROOT}/scripts/derive_landing_schema.py" \
    "${ddl_local}" -o "${REPO_ROOT}/output/${table}_landing_schema.json"
  if probe bq show "${PROJECT_ID}:${LANDING_DATASET}.${table}"; then
    log "landing ${LANDING_DATASET}.${table} exists"
  else
    run bq mk --table --schema "${REPO_ROOT}/output/${table}_landing_schema.json" \
      "${PROJECT_ID}:${LANDING_DATASET}.${table}"
  fi
}
stage_table "citibike_trips_50k"
stage_table "hacker_news_50k"

log "storage + datasets + DQ tables + snapshots + landing tables done"
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`

- [ ] **Step 5: Commit**

```bash
git add public_cloud/deploy/gcp/03_storage_bq.sh packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: snapshot public tables, extract DDL, create landing tables

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Killswitch function — `logic.py` (TDD), `main.py`, `requirements.txt`

**Files:**
- Create: `public_cloud/deploy/gcp/killswitch/logic.py`
- Create: `public_cloud/deploy/gcp/killswitch/main.py`
- Create: `public_cloud/deploy/gcp/killswitch/requirements.txt`
- Test: `packages/sdfb-tests/tests/unit/public_cloud/test_killswitch_logic.py`

**Interfaces:**
- Produces: `logic.should_kill(payload: dict, threshold: float) -> bool` (pure, no cloud imports — laptop-testable); `main.on_budget_alert(cloud_event)` entry point deployed by Task 7 with env `GCP_PROJECT_ID`, `KILL_THRESHOLD`.
- Budget Pub/Sub payload fields (Cloud Billing budget notification format): `costAmount` (number), `budgetAmount` (number).

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/public_cloud/test_killswitch_logic.py`:

```python
"""Unit tests for the billing killswitch decision logic (pure python)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
LOGIC = REPO_ROOT / "public_cloud" / "deploy" / "gcp" / "killswitch" / "logic.py"

spec = importlib.util.spec_from_file_location("killswitch_logic", LOGIC)
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)


def test_kills_at_or_above_threshold():
    assert logic.should_kill({"costAmount": 22.5, "budgetAmount": 25.0}, 0.9) is True
    assert logic.should_kill({"costAmount": 25.0, "budgetAmount": 25.0}, 0.9) is True


def test_does_not_kill_below_threshold():
    assert logic.should_kill({"costAmount": 12.0, "budgetAmount": 25.0}, 0.9) is False


def test_defensive_on_malformed_payload():
    assert logic.should_kill({}, 0.9) is False
    assert logic.should_kill({"budgetAmount": 0}, 0.9) is False       # div-by-zero guard
    assert logic.should_kill({"costAmount": None, "budgetAmount": 25.0}, 0.9) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_killswitch_logic.py -v`
Expected: FAIL (logic.py missing).

- [ ] **Step 3: Create `killswitch/logic.py`**

```python
"""Pure decision logic for the billing killswitch. No cloud imports."""
from __future__ import annotations


def should_kill(payload: dict, threshold: float) -> bool:
    """True when spend has reached `threshold` fraction of the budget."""
    budget = payload.get("budgetAmount")
    cost = payload.get("costAmount")
    if not budget or cost is None:
        return False
    return (cost / budget) >= threshold
```

- [ ] **Step 4: Create `killswitch/main.py`**

```python
"""Budget alert -> detach billing account (the hard cost cap).

Deployed by 04_budget_killswitch.sh (gen2, python311, trigger: budget-alerts
topic). Recovery after it fires: README.md 'Kill-switch recovery'.
"""
from __future__ import annotations

import base64
import json
import os

import functions_framework
from google.cloud import billing_v1

from logic import should_kill

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
KILL_THRESHOLD = float(os.environ.get("KILL_THRESHOLD", "0.9"))


@functions_framework.cloud_event
def on_budget_alert(cloud_event):
    payload = json.loads(base64.b64decode(cloud_event.data["message"]["data"]).decode())
    cost, budget = payload.get("costAmount"), payload.get("budgetAmount")
    if not should_kill(payload, KILL_THRESHOLD):
        print(f"below threshold: {cost}/{budget}")
        return
    client = billing_v1.CloudBillingClient()
    name = f"projects/{PROJECT_ID}"
    info = client.get_project_billing_info(name=name)
    if not info.billing_enabled:
        print("billing already disabled — nothing to do")
        return
    client.update_project_billing_info(
        name=name,
        project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name=""),
    )
    print(f"BILLING DETACHED for {PROJECT_ID} at {cost}/{budget}")
```

- [ ] **Step 5: Create `killswitch/requirements.txt`**

```
functions-framework==3.*
google-cloud-billing>=1.12
```

- [ ] **Step 6: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_killswitch_logic.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add public_cloud/deploy/gcp/killswitch packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: billing killswitch function (budget alert -> detach billing)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: `04_budget_killswitch.sh` — budget, topic, function deploy

**Files:**
- Create: `public_cloud/deploy/gcp/04_budget_killswitch.sh`
- Test: append to `test_scripts_dry_run.py`

**Interfaces:**
- Consumes: `killswitch/` source dir (Task 6), `BUDGET_TOPIC/KILL_FUNCTION/KILL_SA/BUDGET_AMOUNT/KILL_THRESHOLD` from `env.sh`.
- Produces: Pub/Sub topic, budget with 50/80/90% thresholds + topic notifications, deployed gen2 function.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_budget_killswitch_dry_run():
    r = run_script("04_budget_killswitch.sh")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "+ gcloud pubsub topics create budget-alerts" in out
    assert "billing budgets create" in out
    assert "percent=0.5" in out and "percent=0.8" in out and "percent=0.9" in out
    assert "--budget-amount 25" in out
    assert "functions deploy billing-killswitch" in out
    assert "--trigger-topic budget-alerts" in out
    assert "GCP_PROJECT_ID=sdfb-e2e-test123" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py::test_budget_killswitch_dry_run -v`
Expected: FAIL.

- [ ] **Step 3: Create `public_cloud/deploy/gcp/04_budget_killswitch.sh`**

```bash
#!/usr/bin/env bash
# Hard cost cap: budget -> Pub/Sub -> Cloud Function detaches billing at 90%.
# Idempotent. Usage: ./04_budget_killswitch.sh [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX BILLING_ACCOUNT_ID

if probe gcloud pubsub topics describe "${BUDGET_TOPIC}" --project "${PROJECT_ID}"; then
  log "topic ${BUDGET_TOPIC} exists"
else
  run gcloud pubsub topics create "${BUDGET_TOPIC}" --project "${PROJECT_ID}"
fi

# Budget: idempotency via display-name lookup (budgets have server-side ids).
BUDGET_DISPLAY="sdfb-e2e-budget"
if [[ "${DRY_RUN}" != "1" ]] && gcloud billing budgets list --billing-account "${BILLING_ACCOUNT_ID}" \
     --format 'value(displayName)' 2>/dev/null | grep -qx "${BUDGET_DISPLAY}"; then
  log "budget ${BUDGET_DISPLAY} exists"
else
  run gcloud billing budgets create \
    --billing-account "${BILLING_ACCOUNT_ID}" \
    --display-name "${BUDGET_DISPLAY}" \
    --budget-amount "${BUDGET_AMOUNT}" \
    --filter-projects "projects/${PROJECT_ID}" \
    --threshold-rule percent=0.5 \
    --threshold-rule percent=0.8 \
    --threshold-rule percent=0.9 \
    --notifications-rule-pubsub-topic "projects/${PROJECT_ID}/topics/${BUDGET_TOPIC}"
fi

run gcloud functions deploy "${KILL_FUNCTION}" \
  --gen2 --project "${PROJECT_ID}" --region "${REGION}" \
  --runtime python311 --source "${SCRIPT_DIR}/killswitch" \
  --entry-point on_budget_alert \
  --trigger-topic "${BUDGET_TOPIC}" \
  --service-account "${KILL_SA}" \
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID},KILL_THRESHOLD=${KILL_THRESHOLD}" \
  --max-instances 1 --memory 256Mi

log "cost cap armed: ${BUDGET_AMOUNT}/month, detach at ${KILL_THRESHOLD}"
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`

- [ ] **Step 5: Commit**

```bash
git add public_cloud/deploy/gcp/04_budget_killswitch.sh packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: budget + killswitch deploy script (hard cost cap at 90%)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Model staging — `cloudbuild/stage_models.yaml` + `05_stage_models.sh`

**Files:**
- Create: `public_cloud/deploy/gcp/cloudbuild/stage_models.yaml`
- Create: `public_cloud/deploy/gcp/05_stage_models.sh`
- Test: append to `test_scripts_dry_run.py`

**Interfaces:**
- Consumes: Secret Manager secrets `kaggle-username`, `kaggle-key` (created manually per README; script prints reminder), `BUILD_SA`, `MODELS_BUCKET`.
- Produces: complete model trees at `gs://${MODELS_BUCKET}/synthetic/models/qwen3/4b-instruct-2507/v1/` and `.../embedders/bge-small-en-v1.5/v1/`, validated against explicit manifests (bge MUST include `model.safetensors` — the user's local copy was missing it).

- [ ] **Step 1: Write the failing test** (append)

```python
import yaml


def test_stage_models_cloudbuild_yaml_is_valid_and_complete():
    p = GCP_DIR / "cloudbuild" / "stage_models.yaml"
    cfg = yaml.safe_load(p.read_text())
    scripts = " ".join(s.get("script", "") for s in cfg["steps"])
    assert "Qwen/Qwen3-4B-Instruct-2507" in scripts          # ModelScope id
    assert "bge-small-en-v1.5" in scripts
    assert "model.safetensors.index.json" in scripts          # qwen manifest
    assert '"model.safetensors"' in scripts                   # bge manifest (the missing-file bug)
    assert "special_tokens_map.json" in scripts               # bge yes / qwen no (Qwen convention)
    assert cfg["options"]["logging"] == "CLOUD_LOGGING_ONLY"  # custom build SA requirement


def test_stage_models_dry_run_submits_build():
    r = run_script("05_stage_models.sh")
    assert r.returncode == 0, r.stderr
    assert "builds submit" in r.stdout
    assert "stage_models.yaml" in r.stdout
    assert "_MODELS_BUCKET=sdfb-e2e-test123-models" in r.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py -v -k stage_models`
Expected: FAIL.

- [ ] **Step 3: Create `public_cloud/deploy/gcp/cloudbuild/stage_models.yaml`**

```yaml
# One-shot model staging: ModelScope (qwen3) + Kaggle (bge, ModelScope
# fallback) -> manifest validation -> gsutil to the models bucket.
# Weights never touch a laptop. Submit via 05_stage_models.sh.
steps:
  - id: download-and-validate
    name: python:3.11-slim
    secretEnv: ["KAGGLE_USERNAME", "KAGGLE_KEY"]
    script: |
      #!/usr/bin/env bash
      set -euo pipefail
      pip install --quiet modelscope kagglehub
      python3 - <<'PYEOF'
      import os, shutil, sys
      from pathlib import Path

      QWEN_DIR = Path("/workspace/models/qwen3/4b-instruct-2507/v1")
      BGE_DIR = Path("/workspace/models/embedders/bge-small-en-v1.5/v1")
      QWEN_REQUIRED = [
          "config.json", "generation_config.json", "merges.txt", "vocab.json",
          "tokenizer.json", "tokenizer_config.json", "model.safetensors.index.json",
          "model-00001-of-00003.safetensors", "model-00002-of-00003.safetensors",
          "model-00003-of-00003.safetensors",
      ]  # NO special_tokens_map.json — Qwen convention (config/models.yml)
      BGE_REQUIRED = [
          "config.json", "tokenizer.json", "tokenizer_config.json",
          "special_tokens_map.json", "model.safetensors",
      ]  # docs/MODEL_LAYOUT.md 5-file checklist

      from modelscope import snapshot_download
      snapshot_download("Qwen/Qwen3-4B-Instruct-2507", local_dir=str(QWEN_DIR))

      try:
          import kagglehub  # uses KAGGLE_USERNAME/KAGGLE_KEY env
          src = kagglehub.model_download("baai/bge-small-en-v1.5/transformers/default")
          BGE_DIR.mkdir(parents=True, exist_ok=True)
          for f in Path(src).iterdir():
              shutil.copy2(f, BGE_DIR / f.name)
          print("bge source: kaggle")
      except Exception as exc:  # documented fallback (spec §Models)
          print(f"kaggle download failed ({exc}); falling back to ModelScope")
          snapshot_download("BAAI/bge-small-en-v1.5", local_dir=str(BGE_DIR))
          print("bge source: modelscope")

      failures = []
      for d, req in ((QWEN_DIR, QWEN_REQUIRED), (BGE_DIR, BGE_REQUIRED)):
          for name in req:
              f = d / name
              if not f.is_file() or f.stat().st_size == 0:
                  failures.append(str(f))
      if failures:
          print("MANIFEST VALIDATION FAILED — missing/empty:", *failures, sep="\n  ")
          sys.exit(1)
      print("manifest OK: both models complete")
      PYEOF

  - id: upload
    name: gcr.io/cloud-builders/gsutil
    args: ["-m", "cp", "-r", "/workspace/models/qwen3", "/workspace/models/embedders",
           "gs://${_MODELS_BUCKET}/synthetic/models/"]

options:
  logging: CLOUD_LOGGING_ONLY
  diskSizeGb: 100
timeout: 3600s
substitutions:
  _MODELS_BUCKET: unset
availableSecrets:
  secretManager:
    - versionName: projects/${PROJECT_ID}/secrets/kaggle-username/versions/latest
      env: KAGGLE_USERNAME
    - versionName: projects/${PROJECT_ID}/secrets/kaggle-key/versions/latest
      env: KAGGLE_KEY
```

- [ ] **Step 4: Create `public_cloud/deploy/gcp/05_stage_models.sh`**

```bash
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
# absent but we only log, since no build is submitted).
if ! probe gcloud secrets describe kaggle-username --project "${PROJECT_ID}"; then
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
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`

- [ ] **Step 6: Commit**

```bash
git add public_cloud/deploy/gcp packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: model staging via Cloud Build (ModelScope+Kaggle -> GCS, manifest-gated)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Image + template builds — `cloudbuild/build_image.yaml`, `06_build_image.sh`, `07_build_flex_template.sh`

**Files:**
- Create: `public_cloud/deploy/gcp/cloudbuild/build_image.yaml`
- Create: `public_cloud/deploy/gcp/06_build_image.sh`
- Create: `public_cloud/deploy/gcp/07_build_flex_template.sh`
- Test: append to `test_scripts_dry_run.py`

**Interfaces:**
- Consumes: mainline `docker/Dockerfile` (unmodified — the pip-index retarget happens via `sed` in the ephemeral Cloud Build workspace, fail-loud), `docker/flex_template_metadata.json`, `IMAGE_URI/TEMPLATE_PATH/BUILD_SA`.
- Produces: image at `${IMAGE_URI}`; Flex Template spec at `${TEMPLATE_PATH}` (consumed by Task 11).

- [ ] **Step 1: Write the failing test** (append)

```python
def test_build_image_cloudbuild_yaml_retargets_pip_index_failloud():
    cfg = yaml.safe_load((GCP_DIR / "cloudbuild" / "build_image.yaml").read_text())
    sed_step = cfg["steps"][0]
    assert "artifactory/api/pypi/pypi-all/simple" in sed_step["script"]
    assert "pypi.org/simple" in sed_step["script"]
    assert "grep -q" in sed_step["script"]  # fail-loud if upstream line changes
    build_args = " ".join(cfg["steps"][1]["args"])
    assert "BEAM_SDK_IMAGE=docker.io/apache/beam_python3.11_sdk:2.71.0" in build_args
    assert "SDFB_SDK_CONTAINER_IMAGE_ARG=${_IMAGE_URI}" in build_args
    assert cfg["images"] == ["${_IMAGE_URI}"]


def test_build_and_template_scripts_dry_run():
    r = run_script("06_build_image.sh")
    assert r.returncode == 0, r.stderr
    assert "builds submit" in r.stdout and "build_image.yaml" in r.stdout
    assert "_IMAGE_URI=us-central1-docker.pkg.dev/sdfb-e2e-test123/sdfb/sdfb-python:testtag" in r.stdout

    r = run_script("07_build_flex_template.sh")
    assert r.returncode == 0, r.stderr
    assert "flex-template build gs://sdfb-e2e-test123-dataflow/templates/sdfb-testtag-template.json" in r.stdout
    assert "docker/flex_template_metadata.json" in r.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py -v -k "build_image or template"`
Expected: FAIL.

- [ ] **Step 3: Create `public_cloud/deploy/gcp/cloudbuild/build_image.yaml`**

```yaml
# Personal-path image build (ADR 0016 carve-out from ADR 0008).
# Reuses mainline docker/Dockerfile UNMODIFIED: the only corporate dependency
# (pip-install-uv from the JFrog mirror, Dockerfile lines 77-79) is retargeted
# to pypi.org by sed in this ephemeral workspace — never in the repo. uv.lock
# is pinned to files.pythonhosted.org, so `uv sync --frozen` needs no changes.
steps:
  - id: retarget-pip-index
    name: bash
    script: |
      set -euo pipefail
      sed -i 's|--index-url "https://.*artifactory/api/pypi/pypi-all/simple"|--index-url "https://pypi.org/simple"|' docker/Dockerfile
      grep -q 'pypi.org/simple' docker/Dockerfile || {
        echo "FATAL: pip-index retarget matched nothing — the Dockerfile uv-bootstrap line changed upstream; update this sed."; exit 1; }

  - id: build
    name: gcr.io/cloud-builders/docker
    args:
      - build
      - --platform=linux/amd64
      - -f=docker/Dockerfile
      - -t=${_IMAGE_URI}
      - --build-arg=ENV_NAME_ARG=dev
      - --build-arg=BEAM_SDK_IMAGE=docker.io/apache/beam_python3.11_sdk:2.71.0
      - --build-arg=SDFB_SDK_CONTAINER_IMAGE_ARG=${_IMAGE_URI}
      - .

images: ["${_IMAGE_URI}"]
options:
  logging: CLOUD_LOGGING_ONLY
  diskSizeGb: 200
timeout: 7200s
substitutions:
  _IMAGE_URI: unset
```

- [ ] **Step 4: Create `public_cloud/deploy/gcp/06_build_image.sh`**

```bash
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
```

- [ ] **Step 5: Create `public_cloud/deploy/gcp/07_build_flex_template.sh`**

```bash
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
```

- [ ] **Step 6: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`

- [ ] **Step 7: Commit**

```bash
git add public_cloud/deploy/gcp packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: Cloud Build image + flex-template build scripts (mainline Dockerfile reuse)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: `tiers.yaml` + `lib/render_tier.py` (TDD) + drift-guard test

**Files:**
- Create: `public_cloud/deploy/gcp/tiers.yaml`
- Create: `public_cloud/deploy/gcp/lib/render_tier.py`
- Test: `packages/sdfb-tests/tests/unit/public_cloud/test_render_tier.py`
- Test: `packages/sdfb-tests/tests/unit/public_cloud/test_tiers_matrix.py`

**Interfaces:**
- Consumes: table names/ddl URIs exactly as produced by Task 5.
- Produces: CLI `python3 lib/render_tier.py --tiers tiers.yaml --tier R1p --table citibike --var PROJECT_ID=... --var MODELS_BUCKET=... --var DATAFLOW_BUCKET=...` printing eval-able shell lines: `PARAMS='k=v,...'` (sorted, empty values dropped), `MACHINE_TYPE=...`, `ACCELERATOR=...` (may be empty), `MAX_WORKERS=...`, `EXPECT=SUCCESS|FAIL`. Task 11 evals this.
- Tier names are shell-safe: `S0, R1p, R2p, R3p, N4, P6, P7`; tables: `citibike, hacker_news`.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/public_cloud/test_render_tier.py`:

```python
"""Tests for the tier -> flex-template-parameters renderer."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
GCP_DIR = REPO_ROOT / "public_cloud" / "deploy" / "gcp"

spec = importlib.util.spec_from_file_location("render_tier", GCP_DIR / "lib" / "render_tier.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

VARS = {"PROJECT_ID": "p1", "MODELS_BUCKET": "mb", "DATAFLOW_BUCKET": "db"}


def render(tier, table):
    return rt.render(GCP_DIR / "tiers.yaml", tier, table, VARS)


def test_r1p_citibike_is_t4_vllm_b1_rag():
    params, job = render("R1p", "citibike")
    assert params["engine"] == "b1_rag"
    assert params["client_type"] == "vllm"
    assert params["vllm_dtype"] == "float16"
    assert params["vllm_max_model_len"] == "8192"
    assert params["reference_table"] == "p1.synthetic_source.citibike_trips_50k"
    assert params["ddl_uri"] == "gs://db/ddl/citibike_trips_50k_ddl.json"
    assert params["pk_cols"] == "trip_id" and params["identity_cols"] == "trip_id"
    assert job["machine_type"] == "n1-standard-8"
    assert job["accelerator"].startswith("type:nvidia-tesla-t4;count:1;install-nvidia-driver")
    assert job["max_workers"] == "1" and job["expect"] == "SUCCESS"


def test_s0_is_fake_cpu_no_accelerator():
    params, job = render("S0", "citibike")
    assert params["client_type"] == "fake"
    assert "vllm_dtype" not in params          # empty values dropped
    assert "embedder_uri" not in params        # HashingEmbedder path
    assert job["machine_type"] == "e2-standard-8" and job["accelerator"] == ""


def test_n4_expects_failure_with_bad_model_uri():
    params, job = render("N4", "citibike")
    assert "DOES-NOT-EXIST" in params["model_uri"]
    assert job["expect"] == "FAIL"


def test_p6_seed_and_p7_identity_off():
    p6, _ = render("P6", "citibike")
    assert p6["seed"] == "42"
    p7, _ = render("P7", "citibike")
    assert "identity_cols" not in p7           # disabled (empty -> dropped)
    assert p7["pk_cols"] == "trip_id"          # pk gate stays armed


def test_r3p_hacker_news_b2_library():
    params, _ = render("R3p", "hacker_news")
    assert params["engine"] == "b2_library"
    assert params["pk_cols"] == "id"


def test_shell_output_is_evalable():
    out = rt.to_shell(*render("R1p", "citibike"))
    assert out.splitlines()[0].startswith("PARAMS='")
    assert "MACHINE_TYPE='n1-standard-8'" in out
    assert "EXPECT='SUCCESS'" in out
```

Create `packages/sdfb-tests/tests/unit/public_cloud/test_tiers_matrix.py`:

```python
"""Drift guard: every tiers.yaml parameter must exist in the mainline Flex
Template metadata, and all required template params must be provided.
Catches the 'template gained a param, personal layer did not' bug class
(vllm_max_model_len, 2026-07-14)."""
from __future__ import annotations

import importlib.util
import itertools
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
GCP_DIR = REPO_ROOT / "public_cloud" / "deploy" / "gcp"
METADATA = REPO_ROOT / "docker" / "flex_template_metadata.json"

spec = importlib.util.spec_from_file_location("render_tier", GCP_DIR / "lib" / "render_tier.py")
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

VARS = {"PROJECT_ID": "p1", "MODELS_BUCKET": "mb", "DATAFLOW_BUCKET": "db"}
TIERS = ["S0", "R1p", "R2p", "R3p", "N4", "P6", "P7"]
TABLES = ["citibike", "hacker_news"]

meta = json.loads(METADATA.read_text())
TEMPLATE_PARAMS = {p["name"] for p in meta["parameters"]}
REQUIRED = {p["name"] for p in meta["parameters"] if not p.get("isOptional")}
RUNTIME_ADDED = {"run_id"}  # run_e2e.sh generates and appends run_id


def test_every_tier_param_exists_in_template_metadata():
    for tier, table in itertools.product(TIERS, TABLES):
        params, _ = rt.render(GCP_DIR / "tiers.yaml", tier, table, VARS)
        unknown = set(params) - TEMPLATE_PARAMS
        assert not unknown, f"{tier}/{table}: params not in flex_template_metadata.json: {unknown}"


def test_every_required_template_param_is_provided():
    for tier, table in itertools.product(TIERS, TABLES):
        params, _ = rt.render(GCP_DIR / "tiers.yaml", tier, table, VARS)
        missing = REQUIRED - set(params) - RUNTIME_ADDED
        assert not missing, f"{tier}/{table}: required template params missing: {missing}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_render_tier.py packages/sdfb-tests/tests/unit/public_cloud/test_tiers_matrix.py -v`
Expected: FAIL (files missing).

- [ ] **Step 3: Create `public_cloud/deploy/gcp/tiers.yaml`**

```yaml
# Run-matrix presets: tier x table -> full Flex Template parameter set.
# Rendered by lib/render_tier.py ({VAR} placeholders come from env.sh).
# Tier names are shell-safe: R1p == R1' etc. (docs/E2E_TEST_MATRIX.md).
# Empty-string values mean "omit the parameter" (template optionals).
# job.expect FAIL marks negative tiers (run_e2e.sh inverts the verdict).

defaults:
  job:
    machine_type: n1-standard-8
    accelerator: "type:nvidia-tesla-t4;count:1;install-nvidia-driver:5xx"
    max_workers: "1"          # cost cap: override per tier only if needed
    expect: SUCCESS
  params:
    reference_rows_limit: "10000"
    num_rows: "1000"
    batch_size: "16"
    similarity: "0.5"
    env: dev
    engine: b1_rag
    client_type: vllm
    vllm_dtype: float16        # T4 requirement for the bf16 qwen3 checkpoint
    vllm_max_model_len: "8192" # KV-cache cap — REQUIRED on T4 (RUN_PLAYBOOK)
    seed: ""
    model_uri: "gs://{MODELS_BUCKET}/synthetic/models/qwen3/4b-instruct-2507/v1/"
    embedder_uri: "gs://{MODELS_BUCKET}/synthetic/models/embedders/bge-small-en-v1.5/v1/"
    dlq_table: "{PROJECT_ID}.synthetic_data_quality.dlq"
    validation_runs_table: "{PROJECT_ID}.synthetic_data_quality.validation_runs"

tables:
  citibike:
    params:
      reference_table: "{PROJECT_ID}.synthetic_source.citibike_trips_50k"
      landing_table: "{PROJECT_ID}.synthetic_data.citibike_trips_50k"
      ddl_uri: "gs://{DATAFLOW_BUCKET}/ddl/citibike_trips_50k_ddl.json"
      pk_cols: trip_id
      identity_cols: trip_id
  hacker_news:
    params:
      reference_table: "{PROJECT_ID}.synthetic_source.hacker_news_50k"
      landing_table: "{PROJECT_ID}.synthetic_data.hacker_news_50k"
      ddl_uri: "gs://{DATAFLOW_BUCKET}/ddl/hacker_news_50k_ddl.json"
      pk_cols: id
      identity_cols: id

tiers:
  S0:      # CPU smoke: fake client, full BQ write path, always run first
    params: {client_type: fake, vllm_dtype: "", vllm_max_model_len: "", embedder_uri: ""}
    job: {machine_type: e2-standard-8, accelerator: ""}
  R1p: {}  # b1_rag / qwen3-4b / T4 / fp16 — the workhorse
  R2p: {}  # identical rerun of R1p: anti-replay (fresh salted run_id)
  R3p:     # b2_library engine; LLM only patches free text
    params: {engine: b2_library, embedder_uri: ""}
  N4:      # negative guard: bad model_uri -> strict mode must kill the job
    params: {model_uri: "gs://{MODELS_BUCKET}/synthetic/models/qwen3/DOES-NOT-EXIST/v1/"}
    job: {expect: FAIL}
  P6:      # explicit-seed reproducibility — run TWICE, compare
    params: {seed: "42"}
  P7:      # pk gate isolated from identity synthesis
    params: {identity_cols: ""}
```

- [ ] **Step 4: Create `public_cloud/deploy/gcp/lib/render_tier.py`**

```python
#!/usr/bin/env python3
"""Render a tiers.yaml (tier, table) preset into flex-template-run inputs.

Prints eval-able shell lines (PARAMS / MACHINE_TYPE / ACCELERATOR /
MAX_WORKERS / EXPECT). Pure python + PyYAML; unit-tested laptop-side.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

JOB_KEYS = ("machine_type", "accelerator", "max_workers", "expect")


def _expand(value: str, variables: dict[str, str]) -> str:
    for k, v in variables.items():
        value = value.replace("{%s}" % k, v)
    return value


def render(tiers_path: Path, tier: str, table: str, variables: dict[str, str]):
    cfg = yaml.safe_load(Path(tiers_path).read_text())
    if tier not in cfg["tiers"]:
        raise SystemExit(f"unknown tier {tier!r}; known: {sorted(cfg['tiers'])}")
    if table not in cfg["tables"]:
        raise SystemExit(f"unknown table {table!r}; known: {sorted(cfg['tables'])}")

    tier_cfg = cfg["tiers"][tier] or {}
    params: dict[str, str] = {}
    params.update(cfg["defaults"].get("params", {}))
    params.update(cfg["tables"][table].get("params", {}))
    params.update(tier_cfg.get("params", {}))

    job: dict[str, str] = dict(cfg["defaults"].get("job", {}))
    job.update(tier_cfg.get("job", {}))

    params = {k: _expand(str(v), variables) for k, v in params.items() if str(v) != ""}
    job = {k: _expand(str(v), variables) for k, v in job.items()}
    for key in JOB_KEYS:
        job.setdefault(key, "")
    return params, job


def to_shell(params: dict[str, str], job: dict[str, str]) -> str:
    joined = ",".join(f"{k}={params[k]}" for k in sorted(params))
    lines = [
        f"PARAMS='{joined}'",
        f"MACHINE_TYPE='{job['machine_type']}'",
        f"ACCELERATOR='{job['accelerator']}'",
        f"MAX_WORKERS='{job['max_workers']}'",
        f"EXPECT='{job['expect']}'",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", required=True, type=Path)
    ap.add_argument("--tier", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--var", action="append", default=[], metavar="K=V")
    args = ap.parse_args()
    variables = dict(kv.split("=", 1) for kv in args.var)
    print(to_shell(*render(args.tiers, args.tier, args.table, variables)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`
Expected: all PASS (render + both drift-guard tests).

- [ ] **Step 6: Commit**

```bash
git add public_cloud/deploy/gcp packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: tiers.yaml run matrix + renderer with template drift guard

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: `run_e2e.sh` — submit, poll, journal, bounded failure logs

**Files:**
- Create: `public_cloud/deploy/gcp/run_e2e.sh`
- Test: append to `test_scripts_dry_run.py`

**Interfaces:**
- Consumes: `render_tier.py` shell output (Task 10), `TEMPLATE_PATH/WORKER_SA/...` from `env.sh`.
- Produces: submitted Dataflow job; journal line appended to `journal/runs.jsonl` (`{"ts","tier","table","run_id","job_id","state","command"}`); exit 0 iff final state matches `EXPECT`; prints the 3-command report recipe (probe/analysis/bundle) for the landed job.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_run_e2e_dry_run_assembles_submit_command():
    r = run_script("run_e2e.sh", "R1p", "citibike")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "dataflow flex-template run" in out
    assert "--template-file-gcs-location gs://sdfb-e2e-test123-dataflow/templates/sdfb-testtag-template.json" in out
    assert "--worker-machine-type n1-standard-8" in out
    assert "worker_accelerator=type:nvidia-tesla-t4;count:1;install-nvidia-driver:5xx" in out
    assert "--max-workers 1" in out
    assert "engine=b1_rag" in out and "vllm_dtype=float16" in out
    assert "run_id=r1p-citibike-" in out
    assert "e2e_gcp_probe.py" in out  # report recipe printed


def test_run_e2e_s0_has_no_accelerator():
    r = run_script("run_e2e.sh", "S0", "citibike")
    assert r.returncode == 0, r.stderr
    assert "worker_accelerator" not in r.stdout
    assert "--worker-machine-type e2-standard-8" in r.stdout


def test_run_e2e_rejects_unknown_tier():
    r = run_script("run_e2e.sh", "R99", "citibike")
    assert r.returncode != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py -v -k run_e2e`
Expected: FAIL.

- [ ] **Step 3: Create `public_cloud/deploy/gcp/run_e2e.sh`**

```bash
#!/usr/bin/env bash
# Submit one E2E tier, poll to terminal state, journal the run.
# Usage: ./run_e2e.sh <tier> <table> [--dry-run]
#   tiers:  S0 R1p R2p R3p N4 P6 P7   (R1p == R1' — shell-safe names)
#   tables: citibike hacker_news
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
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
RUN_ID="${TIER_LC}-${TABLE}-$(date +%Y%m%dT%H%M%S)"
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
      --region "${REGION}" --format 'value(currentState)')"
    case "${STATE}" in
      JOB_STATE_DONE|JOB_STATE_FAILED|JOB_STATE_CANCELLED|JOB_STATE_DRAINED) break ;;
      *) sleep 60 ;;
    esac
  done
  log "terminal state: ${STATE}"
  # Journal every run, pass or fail (the deployed-commands ledger).
  printf '{"ts":"%s","tier":"%s","table":"%s","run_id":"%s","job_id":"%s","state":"%s","command":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${TIER}" "${TABLE}" "${RUN_ID}" "${JOB_ID}" "${STATE}" \
    "$(echo "${CMD[*]}" | sed 's/"/\\"/g')" >> "${SCRIPT_DIR}/journal/runs.jsonl"
  if [[ "${STATE}" != "JOB_STATE_DONE" && "${EXPECT}" == "SUCCESS" ]]; then
    log "FAILED — last 50 error log lines (full mining belongs to e2e_gcp_probe.py):"
    gcloud logging read \
      "resource.type=dataflow_step AND resource.labels.job_id=${JOB_ID} AND severity>=ERROR" \
      --project "${PROJECT_ID}" --limit 50 --format 'value(timestamp,textPayload)' || true
    die "tier ${TIER} expected SUCCESS, got ${STATE} (job ${JOB_ID})"
  fi
  if [[ "${EXPECT}" == "FAIL" && "${STATE}" == "JOB_STATE_DONE" ]]; then
    die "tier ${TIER} expected FAILURE but the job succeeded — negative guard broken"
  fi
fi

log "verdict: ${TIER}/${TABLE} matched EXPECT=${EXPECT}"
log "next — report recipe (RUN_PLAYBOOK §5):"
log "  uv run --no-sync python3 scripts/e2e_gcp_probe.py --project ${PROJECT_ID} \\"
log "    --source-fqn \$(SOURCE_FQN) --landing-fqn \$(LANDING_FQN) --quality-dataset ${QUALITY_DATASET} \\"
log "    --region ${REGION} --job-id ${JOB_ID} --run-id ${RUN_ID} --out integration_test/${JOB_ID}/e2e_gcp_metrics.json"
log "  then e2e_validation_analysis.py + e2e_bundle_export.py -> integration_test/${JOB_ID}/"
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`

- [ ] **Step 5: Commit**

```bash
git add public_cloud/deploy/gcp/run_e2e.sh packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: run_e2e.sh — tier submit/poll/journal with EXPECT semantics

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: `teardown.sh`

**Files:**
- Create: `public_cloud/deploy/gcp/teardown.sh`
- Test: append to `test_scripts_dry_run.py`

**Interfaces:**
- Consumes: all `env.sh` names.
- Produces: full cleanup in reverse dependency order; `--full` additionally deletes the whole project. Interactive confirm (type the project id) — skipped in dry-run.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_teardown_dry_run_deletes_in_reverse_order():
    r = run_script("teardown.sh")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "functions delete billing-killswitch" in out
    assert "pubsub topics delete budget-alerts" in out
    assert "bq rm -r -f" in out
    assert "buckets delete" in out or "rm --recursive" in out
    assert "artifacts repositories delete sdfb" in out
    assert "projects delete" not in out  # only with --full


def test_teardown_full_dry_run_deletes_project():
    r = run_script("teardown.sh", "--full")
    assert r.returncode == 0, r.stderr
    assert "+ gcloud projects delete sdfb-e2e-test123" in r.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/test_scripts_dry_run.py -v -k teardown`
Expected: FAIL.

- [ ] **Step 3: Create `public_cloud/deploy/gcp/teardown.sh`**

```bash
#!/usr/bin/env bash
# Full cleanup in reverse dependency order. --full also deletes the project.
# Usage: ./teardown.sh [--full] [--dry-run]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env.sh"
source "${SCRIPT_DIR}/lib/common.sh" "$@"

require_env PROJECT_SUFFIX

FULL=0
for arg in "$@"; do [[ "${arg}" == "--full" ]] && FULL=1; done

if [[ "${DRY_RUN}" != "1" ]]; then
  read -r -p "Type the project id (${PROJECT_ID}) to confirm teardown: " CONFIRM
  [[ "${CONFIRM}" == "${PROJECT_ID}" ]] || die "confirmation mismatch — aborting"
fi

run gcloud functions delete "${KILL_FUNCTION}" --gen2 --region "${REGION}" --project "${PROJECT_ID}" --quiet
run gcloud pubsub topics delete "${BUDGET_TOPIC}" --project "${PROJECT_ID}" --quiet
# Budget lives on the billing account — delete by looked-up id (skip in dry-run).
if [[ "${DRY_RUN}" != "1" ]]; then
  BUDGET_ID="$(gcloud billing budgets list --billing-account "${BILLING_ACCOUNT_ID}" \
    --filter 'displayName=sdfb-e2e-budget' --format 'value(name)' | head -1)"
  [[ -n "${BUDGET_ID}" ]] && run gcloud billing budgets delete "${BUDGET_ID}" --quiet
else
  echo "+ gcloud billing budgets delete <looked-up sdfb-e2e-budget id> --quiet"
fi

run bq rm -r -f --dataset "${PROJECT_ID}:${QUALITY_DATASET}"
run bq rm -r -f --dataset "${PROJECT_ID}:${LANDING_DATASET}"
run bq rm -r -f --dataset "${PROJECT_ID}:${SRC_DATASET}"

run gcloud storage rm --recursive "gs://${DATAFLOW_BUCKET}" --project "${PROJECT_ID}"
run gcloud storage rm --recursive "gs://${MODELS_BUCKET}" --project "${PROJECT_ID}"

run gcloud artifacts repositories delete "${GAR_REPO}" --location "${REGION}" --project "${PROJECT_ID}" --quiet

if [[ "${FULL}" == "1" ]]; then
  run gcloud projects delete "${PROJECT_ID}" --quiet
  log "project deletion requested — 30-day recovery window applies"
else
  log "granular teardown done (project kept; rerun scripts 01-07 to rebuild)"
fi
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/public_cloud/ -v`

- [ ] **Step 5: Commit**

```bash
git add public_cloud/deploy/gcp/teardown.sh packages/sdfb-tests/tests/unit/public_cloud
git commit -m "feat: teardown script (reverse-order cleanup, --full project delete)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: Agents ×3 + skills ×3

**Files:**
- Create: `.claude/agents/gcp-deploy-runner.md`
- Create: `.claude/agents/e2e-interpreter.md`
- Create: `.claude/agents/gcp-po-auditor.md`
- Create: `.claude/skills/gcp-project-ops.md`
- Create: `.claude/skills/gcp-e2e-run.md`
- Create: `.claude/skills/gcp-cost-audit.md`

No unit tests (markdown); verification = frontmatter matches the existing agent/skill format (compare with `.claude/agents/gpu-image-builder.md`).

- [ ] **Step 1: Create `.claude/agents/gcp-deploy-runner.md`**

```markdown
---
name: gcp-deploy-runner
description: Subagent that deploys and runs personal-GCP E2E campaigns — submits Cloud Builds (05/06), builds templates (07), runs tiers (run_e2e.sh), journals every command. Invoke for any personal-project deployment or run-matrix execution. Does NOT interpret metrics (that is e2e-interpreter's job).
model: sonnet
---

# Subagent — GCP deploy runner (personal project)

## Scope

- Execute `public_cloud/deploy/gcp/{05_stage_models,06_build_image,07_build_flex_template,run_e2e}.sh` and monitor their jobs.
- Journal discipline: every submission MUST land in `public_cloud/deploy/gcp/journal/runs.jsonl` (run_e2e.sh does this automatically — never bypass it with raw `gcloud dataflow flex-template run`).
- Campaign order (spec §Run flow): S0 → R1p → R2p → R3p → N4 → P6 (twice) → P7 on citibike, then one R1p on hacker_news.
- After each landed job, run the 3-command report recipe (probe → analysis → bundle) into `integration_test/<JOB_ID>/`, then hand off to `e2e-interpreter`.

## Token-efficiency rules (hard)

- Poll ONLY with `--format 'value(currentState)'`; never `describe` without a format.
- NEVER dump raw worker logs into context. `scripts/e2e_gcp_probe.py` owns log mining; run_e2e.sh already caps failure output at 50 error lines.
- Read `journal/runs.jsonl` with `tail`, not whole-file.

## NOT in scope

- Interpreting metrics/verdicts (e2e-interpreter), cost audits (gcp-po-auditor), infra bootstrap changes (skill gcp-project-ops covers; escalate to the user), any corporate-path (Composer/JFrog) deployment.

## What to load before working

- `.claude/skills/gcp-e2e-run.md`
- `public_cloud/deploy/gcp/README.md`
```

- [ ] **Step 2: Create `.claude/agents/e2e-interpreter.md`**

```markdown
---
name: e2e-interpreter
description: Subagent that interprets landed E2E runs — reads the two metrics JSONs in integration_test/<JOB_ID>/ against the 8 RUN_PLAYBOOK pass criteria and writes verdict + postmortem + remediation handoff. No cloud access; extreme reasoning. Invoke after gcp-deploy-runner lands a run.
model: fable
tools: Read, Grep, Glob, Write
---

# Subagent — E2E result interpreter

## Scope

- Inputs (ONLY these — never raw GCP logs): `integration_test/<JOB_ID>/e2e_gcp_metrics.json`, `integration_test/<JOB_ID>/e2e_validation_metrics.json`, `public_cloud/deploy/gcp/journal/runs.jsonl` (tail), `docs/E2E_TEST_MATRIX.md` tier expectations.
- Evaluate the 8 pass criteria (docs/RUN_PLAYBOOK.md): lifecycle milestones present (`model_client_setup_*`, `model_pull_*`, `vllm_spawn`, `vllm_ready`); zero `freetext_llm_fallback`; `copy_ratio < 0.3`; unique salted `run_id`; `pk.duplicate ≈ 0`; `b1_embed_done rows=10000`; Dataflow state checked directly; `valid_count == num_rows`.
- Output per run: verdict table (criterion × PASS/FAIL/N-A × evidence), postmortem for failures (root-cause hypothesis ranked by evidence), remediation plan handoff (file:line pointers into `packages/`) for a bug-fixing session.
- For P6: diff the two seeded runs — non-identity columns identical, identity columns unique, run_ids differ.

## NOT in scope

- Running anything on GCP (no Bash by design), fixing code, cost analysis.
```

- [ ] **Step 3: Create `.claude/agents/gcp-po-auditor.md`**

```markdown
---
name: gcp-po-auditor
description: Read-only PO/auditor for the personal GCP project — reports infra inventory vs env.sh manifest, budget/credit burn-down, free-tier consumption, quota state. NEVER deploys, creates, mutates, or deletes anything. Invoke for "what is the state/cost of the personal project?".
model: haiku
tools: Bash, Read, Grep, Glob
---

# Subagent — GCP PO auditor (STRICTLY read-only)

## Hard rule

Allowed gcloud/bq verbs: `list`, `describe`, `show`, `query` (SELECT only on
INFORMATION_SCHEMA), `budgets list/describe`. FORBIDDEN verbs: create, update,
delete, deploy, submit, run, mk, rm, cp, import, cancel. If a finding needs a
fix, REPORT the command — do not run it.

## Reports (recipes in .claude/skills/gcp-cost-audit.md)

1. **Inventory diff** — resources found vs. names derived from `public_cloud/deploy/gcp/env.sh` (buckets, datasets, DQ tables, GAR repo+policy, SAs, budget, killswitch fn, template, image count).
2. **Burn-down** — month-to-date spend vs BUDGET_AMOUNT, threshold distance, credit status; per-SKU top 5.
3. **Free-tier tracker** — BQ query TB (of 1 TB/mo) + BQ storage GB (of 10) + GCS GB (of 5, US regions) + Cloud Build minutes.
4. **Quota** — `GPUS_ALL_REGIONS`, `NVIDIA_T4_GPUS` us-central1, in-use vs limit.

## NOT in scope

Anything mutating; run submission (gcp-deploy-runner); metric verdicts (e2e-interpreter).
```

- [ ] **Step 4: Create `.claude/skills/gcp-project-ops.md`**

```markdown
---
name: gcp-project-ops
description: Recipes for bootstrapping, verifying, recovering, and tearing down the personal GCP E2E project (public_cloud/deploy/gcp). Load when setting up the project, after a killswitch firing, or when infra drifts from env.sh.
---

# Skill — personal GCP project ops

> Source of truth is the scripts in `public_cloud/deploy/gcp/` — this skill is
> the operating recipe, not a copy. Everything supports `--dry-run`.

## Bootstrap (once)

```bash
export PROJECT_SUFFIX=<suffix> BILLING_ACCOUNT_ID=<id>   # gcloud billing accounts list
cd public_cloud/deploy/gcp
./01_bootstrap_project.sh && ./02_iam.sh && ./03_storage_bq.sh && ./04_budget_killswitch.sh
# MANUAL: upgrade trial -> paid; request T4 quota (script 01 prints URLs).
./05_stage_models.sh && ./06_build_image.sh && ./07_build_flex_template.sh
```

Preflight audit (read-only, reusable): `uv run --no-sync python3 scripts/deployment_prerequisites.py --project $PROJECT_ID --source-table $PROJECT_ID.synthetic_source.citibike_trips_50k --model-uri $MODEL_URI --staging-bucket $DATAFLOW_BUCKET --templates-bucket $DATAFLOW_BUCKET`

## Kill-switch recovery (billing was detached at 90% budget)

1. Confirm what fired: function logs — `gcloud functions logs read billing-killswitch --gen2 --region us-central1 --project $PROJECT_ID --limit 20`.
2. Understand the spend first (gcp-cost-audit skill) — do NOT relink blindly.
3. Relink: `gcloud billing projects link $PROJECT_ID --billing-account $BILLING_ACCOUNT_ID`.
4. Re-verify: `./01_bootstrap_project.sh` (idempotent) then resume.

## Teardown

`./teardown.sh` (granular, keeps project) or `./teardown.sh --full` (deletes project, 30-day recovery window).
```

- [ ] **Step 5: Create `.claude/skills/gcp-e2e-run.md`**

```markdown
---
name: gcp-e2e-run
description: Recipe for running personal-GCP E2E tiers (submit → poll → collect → report). Load before executing any run-matrix campaign or a single tier on the personal project.
---

# Skill — personal GCP E2E runs

## Single tier

```bash
cd public_cloud/deploy/gcp
./run_e2e.sh <tier> <table>     # tiers: S0 R1p R2p R3p N4 P6 P7; tables: citibike hacker_news
```

- run_e2e.sh polls to terminal state, journals to `journal/runs.jsonl`, enforces
  EXPECT semantics (N4 must FAIL — a green N4 job is a broken guard).
- T4 preset is baked in tiers.yaml: qwen3-4b + `vllm_dtype=float16` +
  `vllm_max_model_len=8192` + n1-standard-8 + `install-nvidia-driver:5xx`.

## Campaign (spec §Run matrix)

S0 → R1p → R2p → R3p → N4 → P6 ×2 → P7 on citibike, then R1p on hacker_news.
Stop the campaign at the first unexpected FAIL; hand the landed artifacts to
`e2e-interpreter` before continuing.

## Collect + report (after every landed job; RUN_PLAYBOOK §5)

```bash
JOB=<job_id>; RUN=<run_id>; T=citibike_trips_50k
uv run --no-sync python3 scripts/e2e_gcp_probe.py --project $PROJECT_ID \
  --source-fqn $PROJECT_ID.synthetic_source.$T --landing-fqn $PROJECT_ID.synthetic_data.$T \
  --quality-dataset synthetic_data_quality --region us-central1 \
  --job-id $JOB --run-id $RUN --pk trip_id --out integration_test/$JOB/e2e_gcp_metrics.json
uv run --no-sync python3 scripts/e2e_validation_analysis.py --csv eng=integration_test/$JOB/<export>.csv \
  --schema output/${T}_landing_schema.json --pk trip_id --batch-size 16 \
  --out integration_test/$JOB/e2e_validation_metrics.json
uv run --no-sync python3 scripts/e2e_bundle_export.py --metrics gcp=integration_test/$JOB/e2e_gcp_metrics.json \
  --metrics validation=integration_test/$JOB/e2e_validation_metrics.json --out-root integration_test/$JOB --job-id $JOB
```

(hacker_news: `--pk id`, table `hacker_news_50k`.)

## Token-efficiency rules

- Poll: `gcloud dataflow jobs describe <id> --region us-central1 --format 'value(currentState)'` — nothing broader.
- Never read raw worker logs; the probe script mines them into JSON.
```

- [ ] **Step 6: Create `.claude/skills/gcp-cost-audit.md`**

```markdown
---
name: gcp-cost-audit
description: Read-only cost/quota/free-tier audit recipes for the personal GCP project — burn-down vs budget, per-SKU spend, BQ/GCS/Cloud Build free-tier consumption, T4 quota. Load for any "what is this costing?" question.
---

# Skill — personal GCP cost audit (read-only)

Console quick links (fastest human view — substitute $PROJECT_ID):
- Billing report: https://console.cloud.google.com/billing/reports?project=$PROJECT_ID
- Budgets: https://console.cloud.google.com/billing/budgets
- Quotas: https://console.cloud.google.com/iam-admin/quotas?project=$PROJECT_ID

## Burn-down + budget state

```bash
gcloud billing budgets list --billing-account $BILLING_ACCOUNT_ID \
  --format 'table(displayName, amount.specifiedAmount.units, thresholdRules[].thresholdPercent)'
gcloud functions logs read billing-killswitch --gen2 --region us-central1 \
  --project $PROJECT_ID --limit 5   # 'below threshold: X/Y' lines = live spend ratio
```

## Free-tier trackers (monthly)

```bash
# BQ analysis bytes this month (free: 1 TiB)
bq query --use_legacy_sql=false --project_id=$PROJECT_ID 'SELECT
  ROUND(SUM(total_bytes_billed)/POW(1024,4), 4) AS tib_billed
FROM `region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
WHERE EXTRACT(MONTH FROM creation_time)=EXTRACT(MONTH FROM CURRENT_TIMESTAMP())'
# BQ storage (free: 10 GB): bq show --format=prettyjson <dataset> | numBytes per table
# GCS (free: 5 GB US): gcloud storage du -s gs://$PROJECT_ID-models gs://$PROJECT_ID-dataflow
# GAR stored images (policy keeps 1): gcloud artifacts docker images list \
#   us-central1-docker.pkg.dev/$PROJECT_ID/sdfb --format 'value(package,version)'
# Cloud Build minutes: gcloud builds list --project $PROJECT_ID --region us-central1 \
#   --format 'table(id,createTime,duration,status)' --limit 20
```

## Quota

```bash
gcloud compute regions describe us-central1 --project $PROJECT_ID \
  --format 'table(quotas.filter("metric:NVIDIA_T4_GPUS"))'
gcloud compute project-info describe --project $PROJECT_ID \
  --format 'value(quotas.filter("metric:GPUS_ALL_REGIONS"))'
```

## Cost model cheat-sheet (us-central1, batch)

~$1/worker-hour all-in for the T4 tier (n1-standard-8 + T4 + Dataflow service
+ 200GB PD); a 1000-row run ≈ 30–45 min at maxWorkers=1. Steady-state storage
≈ $2–3/month (image + 16 GB models + BQ snapshots).
```

- [ ] **Step 7: Verify format + laptop suite still green**

Run: `head -8 .claude/agents/gcp-*.md .claude/agents/e2e-interpreter.md && uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q`
Expected: frontmatter blocks present; full suite green.

- [ ] **Step 8: Commit**

```bash
git add .claude/agents .claude/skills
git commit -m "feat: personal-GCP agents (deploy/interpret/audit) + ops/run/cost skills

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 14: ADR 0016, README, index updates, final verification

**Files:**
- Create: `docs/adr/0016-personal-gcp-cloud-build.md`
- Create: `public_cloud/deploy/gcp/README.md`
- Modify: `docs/adr/README.md` (add 0016 row, matching the existing index format)
- Modify: `CLAUDE.md` (agents/skills lists + "When in doubt" entry)
- Modify: `docs/ROADMAP.md` (note the personal E2E layer)

- [ ] **Step 1: Create `docs/adr/0016-personal-gcp-cloud-build.md`**

```markdown
# 0016 — Personal-project images build on Cloud Build (ADR 0008 carve-out)

Date: 2026-07-14
Status: Accepted

## Context

ADR 0008 makes GitHub CI the only sanctioned image builder — its rationale is
corporate (provenance/ITSK, JFrog cache, ARC runners). A personal GCP project
(`public_cloud/deploy/gcp/`, spec `docs/superpowers/specs/2026-07-14-personal-gcp-e2e-design.md`)
now runs the T4 E2E matrix outside the corporate LZ. The dev laptop has no
Docker (Intel Mac, macOS 12), the image is ~15–20 GB, and corporate CI cannot
push to a personal Artifact Registry.

## Decision

For the **personal path only**, `gcloud builds submit` (Cloud Build) is the
sanctioned builder (`public_cloud/deploy/gcp/06_build_image.sh` +
`cloudbuild/build_image.yaml`). The mainline `docker/Dockerfile` is reused
byte-identical; the single JFrog dependency (the pip-install-uv `--index-url`
bootstrap) is retargeted to pypi.org by a `sed` in the **ephemeral Cloud Build
workspace** with a fail-loud grep — never committed. `uv.lock` already pins
public `files.pythonhosted.org` URLs, so dependency resolution is unchanged.
Local `docker build`/`push` remains forbidden on both paths.

## Consequences

- Corporate rule unchanged; zero mainline edits (drift-guard test
  `test_tiers_matrix.py` protects the template contract instead).
- If the Dockerfile bootstrap line changes upstream, the personal build fails
  loudly at the grep, pointing at the sed to update.
- Personal images live only in the personal GAR repo (keep-newest-1 policy).
```

- [ ] **Step 2: Create `public_cloud/deploy/gcp/README.md`**

Full walkthrough with these sections (write complete prose, ~140 lines):

1. **What this is** — third deployment wrapper (personal GCP, T4, cost-capped); zero mainline edits; spec + ADR 0016 links.
2. **Credits reality** — $300/90-day trial; GPU quota requires paid upgrade (credit survives); no other meaningful voucher path for individuals; expected costs (~$1/T4 run, $2–3/mo storage, campaign ≈ $6–10).
3. **One-time setup** — the exact command block from skill gcp-project-ops (export suffix + billing id; scripts 01→07; the two manual steps with URLs; Kaggle secrets creation: `printf '%s' '<user>' | gcloud secrets create kaggle-username --data-file=- --project $PROJECT_ID` and same for `kaggle-key`).
4. **Cost control** — the 4 layers (kill-switch mechanics + what "billing detached" looks like; alert emails; structural caps: maxWorkers=1 / GAR keep-1 / temp lifecycle / snapshot-once; PO-auditor reports) + console links (billing report, budgets, quotas).
5. **Running the matrix** — `./run_e2e.sh <tier> <table>`; tier table (S0/R1p/R2p/R3p/N4/P6/P7 semantics, one line each); campaign order; report recipe pointer (skill gcp-e2e-run); journal location.
6. **Kill-switch recovery** — confirm → audit → relink → re-verify (from skill gcp-project-ops).
7. **Teardown** — granular vs `--full`.
8. **Lessons learned** — seed with: T4 needs `vllm_dtype=float16` + `vllm_max_model_len=8192`; citibike has no natural PK (GENERATE_UUID snapshot); US colocation constraint (BQ cannot cross-region JOIN); bge upstream listings may miss `model.safetensors` (manifest gate exists for this). Append as discovered.

- [ ] **Step 3: Update the three indexes**

- `docs/adr/README.md`: add a 0016 row/line **matching the existing format** with title "Personal-project images build on Cloud Build (ADR 0008 carve-out)".
- `CLAUDE.md`:
  - `.claude/skills/` list — append: `` - `gcp-project-ops.md` / `gcp-e2e-run.md` / `gcp-cost-audit.md` — personal-GCP E2E layer (bootstrap/run/cost; see `public_cloud/deploy/gcp/`) ``
  - `.claude/agents/` list — append: `` - `gcp-deploy-runner` / `e2e-interpreter` / `gcp-po-auditor` — personal-GCP E2E campaign (deploy → interpret → audit) ``
  - "When in doubt" — append: `` - **Personal-GCP E2E runs (T4, cost-capped)** → [`public_cloud/deploy/gcp/README.md`](public_cloud/deploy/gcp/README.md). ``
- `docs/ROADMAP.md`: under the E2E/§11 area add one line: `Personal-GCP T4 E2E layer (public_cloud/deploy/gcp) — spec 2026-07-14, ADR 0016; unblocks the matrix without corporate LZ/M4.`

- [ ] **Step 4: Final verification**

```bash
uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q   # full suite green
uv run ruff check .                                               # includes new test files
bash -n public_cloud/deploy/gcp/*.sh public_cloud/deploy/gcp/lib/common.sh
```
Expected: all green; no ruff findings; no syntax errors.

- [ ] **Step 5: Commit**

```bash
git add docs/adr public_cloud/deploy/gcp/README.md CLAUDE.md docs/ROADMAP.md
git commit -m "docs: ADR 0016 + personal-GCP README + index updates

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Execution order & dependencies

Tasks 1→12 are sequential (each script builds on env.sh/common.sh; Task 10 needs Task 5's table names; Task 11 needs Task 10's renderer). Tasks 13–14 only need Task 12 done. The first REAL cloud execution (bootstrap → S0 run) is deliberately NOT part of this plan — it happens after merge, driven by the gcp-deploy-runner agent per README, because it needs the user's billing account and quota approvals.

## Self-review notes (already applied)

- Spec coverage: all spec sections map to tasks (billing/kill → 6-7; region/us-central1 → env.sh; Cloud Build → 8-9; datasets/snapshots → 4-5; matrix → 10-11; agents/skills → 13; ADR/README/indexes → 14; drift test → 10; teardown → 12; journal → 1+11).
- Type consistency: `render(path, tier, table, vars) -> (params, job)` and `to_shell(params, job) -> str` used identically in Tasks 10 tests and 11 script; env var names identical across all scripts and tests.
- No placeholders: every step carries full file content or exact commands.
```
