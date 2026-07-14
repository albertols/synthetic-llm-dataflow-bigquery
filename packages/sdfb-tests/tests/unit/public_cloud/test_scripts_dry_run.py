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
