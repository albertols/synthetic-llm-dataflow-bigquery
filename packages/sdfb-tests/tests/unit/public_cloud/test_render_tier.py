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
