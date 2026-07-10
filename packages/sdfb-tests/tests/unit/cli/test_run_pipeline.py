"""Unit tests for `sdfb_beam.cli.run_pipeline` — arg parsing and factory."""

from __future__ import annotations

import re

import pytest
from apache_beam.options.pipeline_options import (
    GoogleCloudOptions,
    PipelineOptions,
    SetupOptions,
    WorkerOptions,
)
from sdfb_beam.cli.run_pipeline import (
    _DEFAULT_WORKER_DISK_GB,
    build_model_client,
    configure_pipeline_options,
    parse_args,
    resolve_engine_strictness,
)


def _common_args() -> list[str]:
    return [
        "--ddl_uri", "gs://bucket/ddl.json",
        "--reference_table", "p.d.t",
        "--landing_table", "p.d.landing",
        "--dlq_table", "p.d.dlq",
        "--num_rows", "100",
        "--run_id", "abc-123",
        "--model_uri", "gs://bucket/models/gemma4/e4b-it/v1/",
    ]


def test_parse_args_minimal():
    args, beam_argv = parse_args(_common_args())
    assert args.ddl_uri == "gs://bucket/ddl.json"
    assert args.num_rows == 100
    assert args.engine == "b1_rag"        # default
    assert args.batch_size == 16          # default
    assert args.similarity == 0.5         # default
    assert args.client_type == "vllm"     # default
    assert beam_argv == []


def test_parse_args_overrides():
    argv = _common_args() + [
        "--engine", "b2_library",
        "--batch_size", "32",
        "--similarity", "0.9",
        "--client_type", "fake",
        "--reference_rows_limit", "500",
    ]
    args, _ = parse_args(argv)
    assert args.engine == "b2_library"
    assert args.batch_size == 32
    assert args.similarity == 0.9
    assert args.client_type == "fake"
    assert args.reference_rows_limit == 500


def test_parse_args_passes_unknown_to_beam():
    argv = _common_args() + ["--runner", "DirectRunner", "--project", "demo"]
    _, beam_argv = parse_args(argv)
    assert "--runner" in beam_argv
    assert "DirectRunner" in beam_argv


def test_parse_args_identity_cols_uses_underscore_flag():
    """Flex Template launchers pass underscore-named params
    (``--identity_cols=...``, never ``--identity-cols``). Every sibling flag
    on this parser uses underscores; if this one didn't match, argparse's
    `parse_known_args` would silently divert it into `beam_argv` instead of
    populating `args.identity_cols`, and the value would never reach
    `PipelineConfig.identity_columns`."""
    argv = [*_common_args(), "--identity_cols", "customer_id,email"]
    args, beam_argv = parse_args(argv)
    assert args.identity_cols == "customer_id,email"
    assert beam_argv == []


def test_parse_args_identity_cols_defaults_empty():
    args, _ = parse_args(_common_args())
    assert args.identity_cols == ""


def test_parse_args_pk_cols_uses_underscore_flag():
    """Flex Template launchers pass underscore-named params
    (``--pk_cols=...``, never ``--pk-cols``). Every sibling flag
    on this parser uses underscores; if this one didn't match, argparse's
    `parse_known_args` would silently divert it into `beam_argv` instead of
    populating `args.pk_cols`, and the value would never reach
    `PipelineConfig.pk_columns`."""
    argv = [*_common_args(), "--pk_cols", "id,sku"]
    args, beam_argv = parse_args(argv)
    assert args.pk_cols == "id,sku"
    assert beam_argv == []


def test_parse_args_pk_cols_defaults_empty():
    args, _ = parse_args(_common_args())
    assert args.pk_cols == ""


def test_parse_args_rejects_unknown_engine_value():
    """argparse-level rejection of bad client_type; engine is free-form."""
    argv = _common_args() + ["--client_type", "made-up"]
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_build_model_client_fake():
    """Fake client builds without touching vLLM / MLX imports."""
    c = build_model_client("fake", "ignored")
    assert c is not None


def test_build_model_client_vllm_stub_does_not_fail_on_import():
    """The vllm_client stub must be importable on M4 laptop (no vllm dep).
    Constructor succeeds; generate_json raises NotImplementedError but is
    not exercised here."""
    c = build_model_client("vllm", "gs://bucket/models/foo/")
    assert c.model_uri == "gs://bucket/models/foo/"


def test_build_model_client_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown client_type"):
        build_model_client("openai", "ignored")


def test_configure_options_directrunner_sets_save_main_session():
    """Regression: save_main_session lives on SetupOptions, not GoogleCloudOptions."""
    opts = PipelineOptions(["--runner=DirectRunner"])
    configure_pipeline_options(opts, "DirectRunner", "r1")
    assert opts.view_as(SetupOptions).save_main_session is True


def test_configure_options_dataflow_sets_job_name_not_save_main_session():
    opts = PipelineOptions(["--runner=DataflowRunner"])
    configure_pipeline_options(opts, "DataflowRunner", "abc-123")
    assert opts.view_as(SetupOptions).save_main_session is False
    assert opts.view_as(GoogleCloudOptions).job_name == "sdfb-abc-123"


def test_configure_options_sanitizes_airflow_run_id():
    """Regression: Airflow run_ids carry :/+/__ that Dataflow job names reject."""
    opts = PipelineOptions(["--runner=DataflowRunner"])
    configure_pipeline_options(opts, "DataflowRunner", "scheduled__2026-05-20T00:00:00+00:00")
    name = opts.view_as(GoogleCloudOptions).job_name
    assert name == "sdfb-scheduled-2026-05-20t00-00-00-00-00"
    assert re.fullmatch(r"[a-z][-a-z0-9]*[a-z0-9]", name)  # Dataflow constraint


def test_configure_options_preserves_launcher_job_name():
    """The flex launcher's valid --job_name must not be overridden."""
    opts = PipelineOptions(
        ["--runner=DataflowRunner", "--job_name=synthetic-sdfb-vlatest-e48544ec"]
    )
    configure_pipeline_options(opts, "DataflowRunner", "scheduled__bad:name")
    assert opts.view_as(GoogleCloudOptions).job_name == "synthetic-sdfb-vlatest-e48544ec"


# --- sdk_container_image: pin Runner v2 workers to THIS image -----------------
# Without it, Dataflow boots workers on the stock Beam SDK container (no
# sdfb_core/sdfb_beam) and DoFn unpickling dies with ModuleNotFoundError. The
# image bakes its own pushed coordinate into SDFB_SDK_CONTAINER_IMAGE so the
# sha-free Flex Template / DAG never has to carry it.

_IMG = "artifactory.example/dkr-public-local/ns/sdfb-python:main-abc1234"


def test_configure_options_dataflow_sets_sdk_container_image_from_env(monkeypatch):
    monkeypatch.setenv("SDFB_SDK_CONTAINER_IMAGE", _IMG)
    opts = PipelineOptions(["--runner=DataflowRunner"])
    configure_pipeline_options(opts, "DataflowRunner", "abc-123")
    assert opts.view_as(WorkerOptions).sdk_container_image == _IMG


def test_configure_options_dataflow_respects_explicit_sdk_container_image(monkeypatch):
    """An explicit --sdk_container_image (e.g. the probe) must win over the bake."""
    monkeypatch.setenv("SDFB_SDK_CONTAINER_IMAGE", _IMG)
    opts = PipelineOptions(
        ["--runner=DataflowRunner", "--sdk_container_image=other/image:explicit"]
    )
    configure_pipeline_options(opts, "DataflowRunner", "abc-123")
    assert opts.view_as(WorkerOptions).sdk_container_image == "other/image:explicit"


def test_configure_options_directrunner_ignores_sdk_container_image_env(monkeypatch):
    monkeypatch.setenv("SDFB_SDK_CONTAINER_IMAGE", _IMG)
    opts = PipelineOptions(["--runner=DirectRunner"])
    configure_pipeline_options(opts, "DirectRunner", "r1")
    assert opts.view_as(WorkerOptions).sdk_container_image is None


def test_configure_options_dataflow_no_env_leaves_sdk_container_image_unset(monkeypatch):
    monkeypatch.delenv("SDFB_SDK_CONTAINER_IMAGE", raising=False)
    opts = PipelineOptions(["--runner=DataflowRunner"])
    configure_pipeline_options(opts, "DataflowRunner", "abc-123")
    assert opts.view_as(WorkerOptions).sdk_container_image is None


# --- disk_size_gb: pin the worker boot disk in code --------------------------
# The GPU image is multi-GB and overflows Dataflow's 25GB default while the
# kubelet unpacks it. The Flex Template environment.diskSizeGb does NOT reach the
# worker harness, so configure_pipeline_options pins it the same way it pins
# sdk_container_image.


def test_configure_options_dataflow_pins_default_disk_size():
    opts = PipelineOptions(["--runner=DataflowRunner"])
    configure_pipeline_options(opts, "DataflowRunner", "abc-123")
    assert opts.view_as(WorkerOptions).disk_size_gb == _DEFAULT_WORKER_DISK_GB


def test_configure_options_dataflow_respects_explicit_disk_size():
    """An explicit --disk_size_gb must win over the baked default."""
    opts = PipelineOptions(["--runner=DataflowRunner", "--disk_size_gb=500"])
    configure_pipeline_options(opts, "DataflowRunner", "abc-123")
    assert opts.view_as(WorkerOptions).disk_size_gb == 500


def test_configure_options_directrunner_leaves_disk_size_unset():
    opts = PipelineOptions(["--runner=DirectRunner"])
    configure_pipeline_options(opts, "DirectRunner", "r1")
    assert opts.view_as(WorkerOptions).disk_size_gb is None


def test_parse_args_vllm_dtype_defaults_auto():
    args, _ = parse_args(_common_args())
    assert args.vllm_dtype == "auto"


def test_parse_args_vllm_dtype_accepts_float16():
    args, beam_argv = parse_args([*_common_args(), "--vllm_dtype", "float16"])
    assert args.vllm_dtype == "float16"
    assert beam_argv == []


def test_build_model_client_vllm_passes_dtype_override():
    client = build_model_client("vllm", "gs://b/m/v1/", vllm_dtype="float16")
    assert client.vllm_server_kwargs.get("dtype") == "float16"


def test_build_model_client_vllm_auto_dtype_sends_no_flag():
    client = build_model_client("vllm", "gs://b/m/v1/")
    assert "dtype" not in client.vllm_server_kwargs


def test_parse_args_seed_uses_underscore_flag():
    """Flex Template launchers pass underscore-named params
    (``--seed=...``). Every sibling flag on this parser uses underscores;
    if this one didn't match, argparse's `parse_known_args` would silently
    divert it into `beam_argv` instead of populating `args.seed`, and the
    value would never reach `PipelineConfig.seed`."""
    argv = [*_common_args(), "--seed", "42"]
    args, beam_argv = parse_args(argv)
    assert args.seed == "42"
    assert beam_argv == []


def test_parse_args_seed_defaults_empty():
    args, _ = parse_args(_common_args())
    assert args.seed == ""


@pytest.mark.parametrize(
    "client_type,expected",
    [
        ("vllm", True),
        ("mlx", True),
        ("fake", False),
    ],
)
def test_resolve_engine_strictness(client_type, expected):
    """vllm and mlx are real-LLM paths (Dataflow/L4 and M4 DirectRunner
    respectively) — a failed generation must be loud, not silently
    degrade into memorized reference data. Only the deterministic fake
    client (CPU smoke) stays lenient."""
    assert resolve_engine_strictness(client_type) is expected
