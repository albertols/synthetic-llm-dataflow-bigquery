"""Flex Template entrypoint for the synthesis pipeline.

Set via `FLEX_TEMPLATE_PYTHON_PY_FILE` in `docker/Dockerfile`. The Python
launcher invokes this with argparse args populated from the Flex Template
parameters declared in `docker/flex_template_metadata.json`.

Runtime modes:
  - Production (Dataflow + L4 + Gemma 4 via vLLM):
      --runner=DataflowRunner --client_type=vllm --model_uri=gs://…
  - Local smoke on M4 (MLX backend, see docs/M4_LOCAL_SMOKE.md):
      --runner=DirectRunner --client_type=mlx --model_uri=./models/…
  - Deterministic CI integration test (no real LLM):
      --runner=DirectRunner --client_type=fake

REFs:
  - .claude/skills/beam-dofn.md
  - docs/CICD.md
  - docs/MODEL_LAYOUT.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import TYPE_CHECKING

import apache_beam as beam
import sdfb_core.engines  # noqa: F401  populates ENGINE_REGISTRY at import time
import yaml
from apache_beam.io.filesystems import FileSystems
from apache_beam.io.gcp.bigquery import BigQueryDisposition, WriteToBigQuery
from apache_beam.options.pipeline_options import (
    GoogleCloudOptions,
    PipelineOptions,
    SetupOptions,
    StandardOptions,
    WorkerOptions,
)
from sdfb_core.contracts import TableSchema
from sdfb_core.validation import Thresholds

from sdfb_beam.io.bq_sources import load_reference_rows
from sdfb_beam.pipeline import PipelineConfig, build_pipeline

if TYPE_CHECKING:
    from sdfb_core.engines import ModelClient

logger = logging.getLogger(__name__)

# Worker boot disk. The GPU image (torch + vLLM + CUDA runtime libs) is multi-GB;
# the Dataflow default of 25GB overflows while the kubelet unpacks it. Pinned in
# code — like ``sdk_container_image`` below — because the Flex Template
# ``environment.diskSizeGb`` does NOT propagate to the worker harness (observed:
# workers booted at the 25GB default despite the DAG requesting 200).
_DEFAULT_WORKER_DISK_GB = 200


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(description="Synthetic Dataflow BigQuery — pipeline launcher")
    p.add_argument("--ddl_uri", required=True,
                   help="gs:// or local path to _ddl.json")
    p.add_argument("--reference_table", required=True,
                   help="FQN of source table for live SELECT reference rows")
    p.add_argument("--reference_rows_limit", type=int, default=10_000)
    p.add_argument("--landing_table", required=True,
                   help="BQ table for synthetic rows (project.dataset.table)")
    p.add_argument("--dlq_table", required=True,
                   help="BQ DLQ table (project.dataset.table)")
    p.add_argument("--num_rows", type=int, required=True)
    p.add_argument("--batch_size", type=int, default=16)
    p.add_argument("--similarity", type=float, default=0.5)
    p.add_argument("--run_id", required=True)
    p.add_argument("--identity_cols", default="",
                   help="Comma-separated per-row-unique columns synthesized "
                        "fresh each row (PK/UUID); never sampled from "
                        "reference data")
    p.add_argument("--pk_cols", default="",
                   help="Comma-separated declared primary-key columns; "
                        "duplicate PK tuples divert to the DLQ "
                        "(rule_id=pk.duplicate, BLOCKER). Empty disables.")
    p.add_argument("--engine", default="b1_rag",
                   help="Engine name registered in ENGINE_REGISTRY")
    p.add_argument("--model_uri", required=True,
                   help="gs://<bucket>/synthetic/models/<family>/<model>/<version>/")
    p.add_argument("--embedder_uri", default="",
                   help="gs://<bucket>/synthetic/models/embedders/<model>/<version>/ "
                        "for the B.1 RAG embedder (optional; empty → HashingEmbedder)")
    p.add_argument("--validation_runs_table", default="",
                   help="BQ table for the run-level summary row "
                        "(project.dataset.table); empty skips the write")
    p.add_argument("--env", default="dev",
                   help="Environment tier selecting thresholds (dev|uat|prd)")
    p.add_argument("--thresholds_uri", default="config/thresholds.yml",
                   help="gs:// or local path to thresholds.yml")
    p.add_argument("--client_type", default="vllm",
                   choices=["vllm", "mlx", "fake"])
    p.add_argument("--vllm_dtype", default="auto",
                   choices=["auto", "float16", "bfloat16"],
                   help="vLLM --dtype override. auto = checkpoint dtype "
                        "(bf16 for Gemma/Qwen). float16 is REQUIRED on T4 "
                        "for fp16-safe bf16 checkpoints (Qwen); refused for "
                        "gemma-family models (fp16 Gemma emits empty output)")
    return p.parse_known_args(argv)


def resolve_thresholds(thresholds_uri: str, env: str) -> Thresholds:
    """Load thresholds.yml; fall back to a permissive gate if unavailable."""
    try:
        with FileSystems.open(thresholds_uri) as f:
            data = yaml.safe_load(f.read())
        return Thresholds.from_mapping(data, env)
    except Exception as e:
        logger.warning(
            "Could not load thresholds from %s (%s); using permissive gate",
            thresholds_uri, e,
        )
        return Thresholds(env=env, blocker_failure_ratio=1.0)


def build_model_client(
    client_type: str, model_uri: str, vllm_dtype: str = "auto"
) -> ModelClient:
    """Lazy factory — avoids importing vLLM / MLX on machines that don't have them."""
    if client_type == "fake":
        from sdfb_beam.handlers.fake_client import FakeModelClient
        # Empty pool — caller is expected to override for any real smoke test.
        return FakeModelClient(reference_pool=[{}])
    if client_type == "vllm":
        from sdfb_beam.handlers.vllm_client import VLLMModelClient
        # "auto" = vLLM picks the checkpoint dtype. float16 = explicit
        # downcast so fp16-safe bf16 checkpoints (Qwen) run on T4/SM 7.5;
        # the client's init guard still refuses fp16 for gemma-family.
        kwargs = {} if vllm_dtype == "auto" else {"dtype": vllm_dtype}
        return VLLMModelClient(model_uri=model_uri, vllm_server_kwargs=kwargs)
    if client_type == "mlx":
        from sdfb_beam.handlers.mlx_client import MLXModelClient
        return MLXModelClient(model_uri=model_uri)
    raise ValueError(f"Unknown client_type: {client_type}")


def load_ddl(ddl_uri: str) -> TableSchema:
    """Load `_ddl.json` from gs:// or local; transparent via Beam FileSystems."""
    with FileSystems.open(ddl_uri) as f:
        return TableSchema.model_validate(json.loads(f.read()))


def sanitize_job_name(prefix: str, run_id: str) -> str:
    """Build a Dataflow-legal job name: ``[-a-z0-9]``, starts with a letter,
    ends alphanumeric, ≤63 chars. Airflow run_ids carry ``:`` / ``+`` / ``__``
    (e.g. ``scheduled__2026-05-20T00:00:00+00:00``) that are all illegal."""
    slug = re.sub(r"[^a-z0-9]+", "-", run_id.lower()).strip("-")
    name = f"{prefix}-{slug}" if slug else prefix
    return name[:63].rstrip("-")


def configure_pipeline_options(
    options: PipelineOptions, runner: str, run_id: str
) -> None:
    """Set runner-dependent options.

    ``save_main_session`` lives on ``SetupOptions`` (NOT ``GoogleCloudOptions``);
    True only for DirectRunner ad-hoc runs — on Dataflow the image bakes in
    deps + source, so it just adds startup cost.

    The flex launcher already passes a valid ``--job_name`` (from the DAG's
    ``jobName``), so we only synthesize one — sanitized from ``run_id`` — when
    it's absent (e.g. a direct DataflowRunner launch).

    ``sdk_container_image`` pins Runner v2 *workers* to THIS image. The Flex
    Template's ``--image`` only sets the *launcher*; the worker harness image
    must be set separately (Flex Templates can't bake a default — see Google's
    "Configure Flex Templates"). The image bakes its own pushed coordinate into
    ``SDFB_SDK_CONTAINER_IMAGE`` (``docker/Dockerfile``) so the sha-free template
    + Composer DAG never have to carry it. Without this, workers boot the stock
    Beam SDK container (no ``sdfb_core``/``sdfb_beam``) and DoFn unpickling dies
    with ``ModuleNotFoundError: No module named 'sdfb_core'``. An explicit
    ``--sdk_container_image`` (e.g. ``scripts/probe_gpu_dataflow.sh``) wins.
    """
    options.view_as(SetupOptions).save_main_session = runner == "DirectRunner"
    if runner == "DataflowRunner":
        gco = options.view_as(GoogleCloudOptions)
        if not gco.job_name:
            gco.job_name = sanitize_job_name("sdfb", run_id)
        worker_options = options.view_as(WorkerOptions)
        if worker_options.disk_size_gb:
            logger.info("Worker disk_size_gb already set explicitly (%d GB); leaving as-is.",
                        worker_options.disk_size_gb)
        else:
            worker_options.disk_size_gb = _DEFAULT_WORKER_DISK_GB
            logger.info("Pinned worker disk_size_gb to %d GB.", _DEFAULT_WORKER_DISK_GB)
        sdk_image = os.environ.get("SDFB_SDK_CONTAINER_IMAGE")
        if worker_options.sdk_container_image:
            logger.info(
                "Worker sdk_container_image already set explicitly (%s); leaving as-is "
                "(SDFB_SDK_CONTAINER_IMAGE=%s ignored).",
                worker_options.sdk_container_image, sdk_image or "<unset>",
            )
        elif sdk_image:
            worker_options.sdk_container_image = sdk_image
            logger.info("Pinned worker sdk_container_image to %s (from SDFB_SDK_CONTAINER_IMAGE).",
                        sdk_image)
        else:
            logger.warning(
                "Neither --sdk_container_image nor SDFB_SDK_CONTAINER_IMAGE is set; Dataflow "
                "Runner v2 workers will fall back to the stock Beam SDK container (no sdfb_core/"
                "sdfb_beam) and DoFn unpickling will fail. Bake it in docker/Dockerfile or pass "
                "--sdk_container_image explicitly.",
            )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        force=True,
    )
    args, beam_argv = parse_args(argv or sys.argv[1:])
    options = PipelineOptions(beam_argv)
    runner = options.view_as(StandardOptions).runner or "DataflowRunner"

    configure_pipeline_options(options, runner, args.run_id)

    logger.info("Loading DDL from %s", args.ddl_uri)
    table_schema = load_ddl(args.ddl_uri)
    logger.info("Loaded schema for %s (%d columns)",
                table_schema.fqn, len(table_schema.columns))

    logger.info("Building model client (client_type=%s, vllm_dtype=%s)",
                args.client_type, args.vllm_dtype)
    model_client = build_model_client(
        args.client_type, args.model_uri, vllm_dtype=args.vllm_dtype
    )

    logger.info("Loading reference rows from %s (limit=%d)",
                args.reference_table, args.reference_rows_limit)
    reference_rows = load_reference_rows(
        table=args.reference_table,
        limit=args.reference_rows_limit,
    )

    thresholds = resolve_thresholds(args.thresholds_uri, args.env)
    logger.info("Thresholds (env=%s): blocker_failure_ratio=%.4f",
                thresholds.env, thresholds.blocker_failure_ratio)

    config = PipelineConfig(
        table_schema=table_schema,
        engine_name=args.engine,
        model_client=model_client,
        num_rows=args.num_rows,
        batch_size=args.batch_size,
        similarity=args.similarity,
        run_id=args.run_id,
        identity_columns=tuple(
            c.strip() for c in args.identity_cols.split(",") if c.strip()
        ),
        pk_columns=tuple(
            c.strip() for c in args.pk_cols.split(",") if c.strip()
        ),
        strict_freetext=args.client_type == "vllm",
        model_uri=args.model_uri,
        embedder_uri=args.embedder_uri,
        reference_table=args.reference_table,
        landing_table=args.landing_table,
        thresholds=thresholds,
        # A fake-client (CPU smoke) run produces fake data, so failing the job
        # on the BLOCKER gate is meaningless — keep it informational (the
        # validation_runs row still records the status). Real engines gate.
        fail_on_blocker=args.client_type != "fake",
    )

    landing_sink = WriteToBigQuery(
        table=args.landing_table,
        method=WriteToBigQuery.Method.FILE_LOADS,
        write_disposition=BigQueryDisposition.WRITE_APPEND,
        create_disposition=BigQueryDisposition.CREATE_NEVER,
    )
    dlq_sink = WriteToBigQuery(
        table=args.dlq_table,
        method=WriteToBigQuery.Method.FILE_LOADS,
        write_disposition=BigQueryDisposition.WRITE_APPEND,
        create_disposition=BigQueryDisposition.CREATE_NEVER,
    )
    validation_runs_sink = None
    if args.validation_runs_table:
        validation_runs_sink = WriteToBigQuery(
            table=args.validation_runs_table,
            method=WriteToBigQuery.Method.FILE_LOADS,
            write_disposition=BigQueryDisposition.WRITE_APPEND,
            create_disposition=BigQueryDisposition.CREATE_NEVER,
        )

    with beam.Pipeline(options=options) as p:
        result = build_pipeline(
            p,
            reference_rows=reference_rows,
            config=config,
            landing_sink=landing_sink,
            dlq_sink=dlq_sink,
            validation_runs_sink=validation_runs_sink,
        )
        logger.info(
            "Pipeline launched: run_id=%s reference_digest=%s",
            result["run_id"],
            result["reference_digest"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
