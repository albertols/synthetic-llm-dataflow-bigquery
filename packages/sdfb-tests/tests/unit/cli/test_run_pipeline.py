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
    parse_bool_flag,
    resolve_engine_strictness,
    resolve_landing_dispositions,
)
from sdfb_core.codegen import derive_bq_load_schema
from sdfb_core.contracts import TableSchema


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
    argv = [
        *_common_args(),
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
    argv = [*_common_args(), "--runner", "DirectRunner", "--project", "demo"]
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
    argv = [*_common_args(), "--client_type", "made-up"]
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


# --- vllm_max_model_len: cap the KV-cache context allocation ------------------
# Qwen3-4B-Instruct-2507 ships max_position_embeddings=262144; vLLM defaults
# max_model_len to that and needs a 36GiB KV cache — a T4 has ~5GiB free after
# weights, so the EngineCore dies at startup (E2E 2026-07-14, R-run on T4).
# The registry default in config/models.yml (max_model_len: 8192) is
# documentation-only; the cap must be plumbed CLI → Flex Template → DAG like
# vllm_dtype was.


def test_parse_args_vllm_max_model_len_defaults_8192():
    args, _ = parse_args(_common_args())
    assert args.vllm_max_model_len == "8192"


def test_parse_args_vllm_max_model_len_accepts_override():
    args, beam_argv = parse_args(
        [*_common_args(), "--vllm_max_model_len", "16384"]
    )
    assert args.vllm_max_model_len == "16384"
    assert beam_argv == []


def test_build_model_client_vllm_passes_max_model_len():
    client = build_model_client(
        "vllm", "gs://b/m/v1/", vllm_max_model_len="8192"
    )
    assert client.vllm_server_kwargs.get("max-model-len") == "8192"


def test_build_model_client_vllm_empty_max_model_len_sends_no_flag():
    """Empty = escape hatch: let vLLM use the checkpoint's native context."""
    client = build_model_client(
        "vllm", "gs://b/m/v1/", vllm_max_model_len=""
    )
    assert "max-model-len" not in client.vllm_server_kwargs


def test_build_model_client_vllm_combines_dtype_and_max_model_len():
    """The T4+Qwen profile needs BOTH flags on the server command."""
    client = build_model_client(
        "vllm", "gs://b/m/v1/",
        vllm_dtype="float16", vllm_max_model_len="8192",
    )
    assert client.vllm_server_kwargs.get("dtype") == "float16"
    assert client.vllm_server_kwargs.get("max-model-len") == "8192"


def test_build_model_client_vllm_rejects_non_integer_max_model_len():
    """Fail at launch, not 10 minutes later inside DoFn.setup() on a GPU
    worker — a garbled value would otherwise ride the Flex Template all the
    way to the vLLM server spawn."""
    with pytest.raises(ValueError, match="vllm_max_model_len"):
        build_model_client(
            "vllm", "gs://b/m/v1/", vllm_max_model_len="lots"
        )


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


def test_parse_args_rag_layer_flags_default_off():
    args, _ = parse_args(_common_args())
    assert args.build_rag_layer is False
    assert args.rag_chunks_table == ""


def test_parse_args_rag_layer_flags():
    argv = [
        *_common_args(),
        "--build_rag_layer",
        "--rag_chunks_table", "proj.synthetic_rag.rag_chunks",
    ]
    args, _ = parse_args(argv)
    assert args.build_rag_layer is True
    assert args.rag_chunks_table == "proj.synthetic_rag.rag_chunks"


def test_parse_args_build_rag_layer_requires_table():
    with pytest.raises(SystemExit):
        parse_args([*_common_args(), "--build_rag_layer"])


def test_parse_args_write_disposition_default_and_choices():
    args, _ = parse_args(_common_args())
    assert args.write_disposition == "append"
    assert args.create_if_not_exists == "false"
    args, _ = parse_args([*_common_args(), "--write_disposition", "overwrite"])
    assert args.write_disposition == "overwrite"
    with pytest.raises(SystemExit):
        parse_args([*_common_args(), "--write_disposition", "truncate"])


def test_landing_sink_schema_uses_load_safe_projection(narrow_ddl_dict):
    """WS4 final-review CRITICAL-1: the landing sink for CREATE_IF_NEEDED
    must be built from `derive_bq_load_schema`, not `derive_bq_schema` —
    the FILE_LOADS runtime path (vendored apitools `TableFieldSchema`)
    rejects `maxLength`/`precision`/`scale`/`defaultValueExpression` at
    load-job time even though Beam accepts them at graph construction.

    `run_pipeline.main()` calls `derive_bq_load_schema(table_schema)`
    directly to build `landing_kwargs["schema"]`; exercise that same call
    here against a parameterized fixture (STRING max_length + NUMERIC
    precision/scale) rather than driving the whole pipeline.
    """
    import sdfb_beam.cli.run_pipeline as run_pipeline_module

    # Regression guard: the module must not have re-imported the unsafe
    # `derive_bq_schema` under the name used to build the landing schema.
    assert run_pipeline_module.derive_bq_load_schema is derive_bq_load_schema
    assert not hasattr(run_pipeline_module, "derive_bq_schema")

    ts = TableSchema.model_validate(narrow_ddl_dict)
    schema = run_pipeline_module.derive_bq_load_schema(ts)

    forbidden = {"maxLength", "precision", "scale", "defaultValueExpression"}
    for field in schema["fields"]:
        assert not forbidden & set(field.keys()), field
    email = next(f for f in schema["fields"] if f["name"] == "email")
    assert email["type"] == "STRING"
    ltv = next(f for f in schema["fields"] if f["name"] == "lifetime_value")
    assert ltv["type"] == "NUMERIC"


def test_parse_bool_flag_truthy_set():
    assert parse_bool_flag("true")
    assert parse_bool_flag("1")
    assert parse_bool_flag(" YES ")
    assert not parse_bool_flag("false")
    assert not parse_bool_flag("0")
    assert not parse_bool_flag("")
    assert not parse_bool_flag("no")


def test_resolve_landing_dispositions_matrix():
    from apache_beam.io.gcp.bigquery import BigQueryDisposition

    assert resolve_landing_dispositions("append", False) == (
        BigQueryDisposition.WRITE_APPEND,
        BigQueryDisposition.CREATE_NEVER,
    )
    assert resolve_landing_dispositions("overwrite", False) == (
        BigQueryDisposition.WRITE_TRUNCATE,
        BigQueryDisposition.CREATE_NEVER,
    )
    assert resolve_landing_dispositions("append", True) == (
        BigQueryDisposition.WRITE_APPEND,
        BigQueryDisposition.CREATE_IF_NEEDED,
    )
    assert resolve_landing_dispositions("overwrite", True) == (
        BigQueryDisposition.WRITE_TRUNCATE,
        BigQueryDisposition.CREATE_IF_NEEDED,
    )


# --- ddl_uri optional with live-extraction precedence (WS4 §6b) ---------


def _args_without_ddl_uri() -> list[str]:
    args = _common_args()
    i = args.index("--ddl_uri")
    return args[:i] + args[i + 2:]


def test_parse_args_ddl_uri_optional_defaults_empty():
    args, beam_argv = parse_args(_args_without_ddl_uri())
    assert args.ddl_uri == ""
    assert beam_argv == []


def test_resolve_table_schema_prefers_explicit_uri(monkeypatch):
    from sdfb_beam.cli import run_pipeline as rp

    sentinel = object()
    monkeypatch.setattr(rp, "load_ddl", lambda uri: sentinel)

    def fake_extract(fqn: str):
        live_calls.append(fqn)

    live_calls: list[str] = []
    monkeypatch.setattr(
        "sdfb_beam.ddl.extract_table_schema",
        fake_extract,
    )
    assert rp.resolve_table_schema("gs://b/d.json", "p.d.t") is sentinel
    assert live_calls == []  # precedence: pin wins, live never touched


def test_resolve_table_schema_live_extracts_when_uri_empty(monkeypatch):
    from types import SimpleNamespace

    from sdfb_beam.cli import run_pipeline as rp

    sentinel = SimpleNamespace(columns=[1, 2], fqn="p.d.t")
    monkeypatch.setattr(
        "sdfb_beam.ddl.extract_table_schema", lambda fqn: sentinel
    )
    assert rp.resolve_table_schema("", "p.d.t") is sentinel
