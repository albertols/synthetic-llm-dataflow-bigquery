"""Dry-run harness for public_cloud/deploy/gcp scripts.

Laptop-safe: --dry-run never invokes gcloud/bq/gsutil, so no creds or SDK
installs are needed. Each task appends assertions for its script here.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

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
        capture_output=True, text=True, env=FAKE_ENV, cwd=REPO_ROOT, check=False,
    )


def test_env_sh_derives_names():
    out = subprocess.run(
        ["bash", "-c", f'source "{GCP_DIR}/env.sh" && echo "$PROJECT_ID|$IMAGE_URI|$TEMPLATE_PATH|$MODEL_URI"'],
        capture_output=True, text=True, env=FAKE_ENV, check=False,
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
    out = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, env=FAKE_ENV, check=False)
    assert out.returncode == 0, out.stderr
    assert "+ gcloud projects create x" in out.stdout
    assert "ABSENT" in out.stdout  # probe pretends resources are absent in dry-run


def test_all_scripts_pass_bash_syntax_check():
    scripts = sorted(GCP_DIR.glob("*.sh")) + sorted((GCP_DIR / "lib").glob("*.sh"))
    assert scripts, "no scripts found"
    for s in scripts:
        r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, f"{s.name}: {r.stderr}"


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
    assert "output/synthetic_source/ddl_metadata_synthetic_source_citibike_trips_50k.json" in out
    assert "synthetic_data.citibike_trips_50k" in out


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
