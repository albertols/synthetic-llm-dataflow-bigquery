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
from sdfb_core.codegen import derive_bq_load_schema
from sdfb_core.contracts import TableSchema
from sdfb_core.observability import log_milestone
from sdfb_core.rag.embedding import embedder_identity
from sdfb_core.stats import PROFILER_VERSION, profile_source_table, stats_rows
from sdfb_core.validation import Thresholds

from sdfb_beam.cli.preflight import preflight
from sdfb_beam.ddl import extract_table_schema
from sdfb_beam.dofns.uniqueness import UNIQUENESS_MODES
from sdfb_beam.io.bq_sources import load_reference_rows
from sdfb_beam.io.digest import compute_reference_digest
from sdfb_beam.io.fk_pools import load_fk_pools
from sdfb_beam.io.source_values import (
    BigQuerySourceValueStore,
    pool_source_overlap,
)
from sdfb_beam.io.stats_store import BigQuerySourceStatsStore
from sdfb_beam.pipeline import PipelineConfig, build_pipeline
from sdfb_beam.pools.store import BigQueryFreeTextPoolStore
from sdfb_beam.rag.store import BigQueryChunkStore

if TYPE_CHECKING:
    from sdfb_core.engines import ModelClient

logger = logging.getLogger(__name__)

# Worker boot disk. The GPU image (torch + vLLM + CUDA runtime libs) is multi-GB;
# the Dataflow default of 25GB overflows while the kubelet unpacks it. Pinned in
# code — like ``sdk_container_image`` below — because the Flex Template
# ``environment.diskSizeGb`` does NOT propagate to the worker harness (observed:
# workers booted at the 25GB default despite the DAG requesting 200).
_DEFAULT_WORKER_DISK_GB = 200

# batch_size is rows-per-element. At the historic fixed default of 16, a 1M-row
# run produced 62,500 elements and paid per-element Python overhead 62,500
# times over instead of amortising it across vectorized draws (2026-07-26
# E2E). Scale toward ~1,000 elements, but never below the historic default so
# small runs — and their goldens — are untouched.
DEFAULT_BATCH_SIZE = 16
_TARGET_ELEMENTS = 1_000


# WS5 §3 — the seeding experiment's only variable. Three arms off ONE build
# so the E2E runs differ in exactly one thing.
POOL_SEED_STRATEGIES = ("centroid", "kcenter", "kcenter_rotate")


def validate_seed_strategy(value: str) -> str:
    """Reject a typo at launch: silently degrading to the control arm would
    corrupt the comparison the flag exists for."""
    if value not in POOL_SEED_STRATEGIES:
        raise ValueError(
            f"--pool_seed_strategy must be one of {POOL_SEED_STRATEGIES}, "
            f"got {value!r}"
        )
    return value


def resolve_batch_size(requested: int, num_rows: int) -> int:
    """Rows per element. An explicit non-default ``--batch_size`` always wins."""
    if requested != DEFAULT_BATCH_SIZE:
        return requested
    if num_rows <= 0:
        return DEFAULT_BATCH_SIZE
    return max(DEFAULT_BATCH_SIZE, num_rows // _TARGET_ELEMENTS)


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(description="Synthetic Dataflow BigQuery — pipeline launcher")
    p.add_argument("--ddl_uri", default="",
                   help="gs:// or local path to _ddl.json. OPTIONAL "
                        "(WS4 §6b): empty = live INFORMATION_SCHEMA "
                        "extraction from --reference_table at "
                        "graph-construction time. An explicit URI is the "
                        "pin/air-gap escape hatch and always wins.")
    p.add_argument("--reference_table", required=True,
                   help="FQN of source table for live SELECT reference rows")
    p.add_argument("--reference_rows_limit", type=int, default=10_000)
    p.add_argument("--landing_table", required=True,
                   help="BQ table for synthetic rows (project.dataset.table)")
    p.add_argument("--dlq_table", required=True,
                   help="BQ DLQ table (project.dataset.table)")
    p.add_argument("--write_disposition", default="append",
                   choices=["append", "overwrite"],
                   help="Landing-table write mode. append = WRITE_APPEND "
                        "(default, today's behavior); overwrite = "
                        "WRITE_TRUNCATE (FILE_LOADS-compatible). DLQ, "
                        "validation_runs and rag_chunks always append.")
    p.add_argument("--create_if_not_exists", default="false",
                   help="true/1/yes: create the landing table on first "
                        "write (CREATE_IF_NEEDED) carrying the landing "
                        "schema derived in-pipeline from the DDL. Anything "
                        "else: CREATE_NEVER (default). Quality/RAG tables "
                        "are never auto-created. NOTE: the auto-created "
                        "table only gets name/type/mode/description per "
                        "column — parameterized constraints (STRING "
                        "max_length, NUMERIC precision/scale, column "
                        "default expressions) are NOT carried over, "
                        "because the FILE_LOADS load-job API rejects them "
                        "at runtime. Pre-provision the table out-of-band "
                        "(e.g. `bq mk`/DDL) if those constraints matter.")
    p.add_argument("--num_rows", type=int, required=True)
    p.add_argument("--batch_size", type=int, default=DEFAULT_BATCH_SIZE,
                   help="Rows per element. Left at the default, this scales "
                        "with --num_rows toward ~1,000 elements (never below "
                        "the default). Pass an explicit value to pin it.")
    p.add_argument("--similarity", type=float, default=0.5)
    p.add_argument("--seed", default="",
                   help="Explicit base RNG seed (int). Empty = derive per "
                        "(run_id, batch_id) — never replays across runs "
                        "because run_id is salted per trigger.")
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
    p.add_argument("--build_rag_layer", nargs="?", const="true", default="",
                   help="true/false (bare flag = true). Populate "
                        "synthetic_rag.rag_chunks from this run's "
                        "reference sample (skipped if the reference_digest "
                        "is already present for this embedder id+version). "
                        "String-valued so the Flex Template/DAG chain can "
                        "pass --build_rag_layer=true.")
    p.add_argument("--rag_chunks_table", default="",
                   help="FQN of synthetic_rag.rag_chunks. Enables the "
                        "read-instead-of-reembed path; with "
                        "--build_rag_layer also enables population.")
    p.add_argument("--build_pool_layer", nargs="?", const="true", default="",
                   help="true/false (bare flag = true). Build free-text "
                        "pools in their own branch and persist them to "
                        "--freetext_pools_table (skipped if this "
                        "reference_digest + model_uri is already present). "
                        "Without it pools are inferred inside every worker "
                        "process's setup().")
    p.add_argument("--freetext_pools_table", default="",
                   help="FQN of synthetic_rag.freetext_pools. Enables the "
                        "read-instead-of-rebuild path; with "
                        "--build_pool_layer also enables the build branch.")
    p.add_argument("--uniqueness_mode", default="exact",
                   choices=list(UNIQUENESS_MODES),
                   help="exact = divert every duplicate to the DLQ "
                        "(default, today). streaming = land rows as they "
                        "are generated and MEASURE the duplicate rate "
                        "instead of removing it, so no GroupByKey barrier "
                        "sits between generation and BigQuery. In streaming mode duplicate rows LAND — the run is still marked "
                        "FAILED_BLOCKER, so re-run with "
                        "--write_disposition=overwrite.")
    p.add_argument("--pool_seed_strategy", default="centroid",
                   choices=list(POOL_SEED_STRATEGIES),
                   help="How the 8 free-text prompt seeds are chosen. "
                        "centroid = control (densest region, today). "
                        "kcenter = seeds span the column's modes. "
                        "kcenter_rotate = re-seeded per ladder attempt "
                        "(forfeits vLLM prefix caching by design).")
    p.add_argument("--pool_pattern_guidance", nargs="?", const="true",
                   default="",
                   help="true/false (bare flag = true). Constrain "
                        "identifier-ish free-text pool completions at "
                        "decode time with a charset/length regex "
                        "(vLLM structured-output items.pattern). Opt-in "
                        "until T4 throughput is confirmed; the post-hoc "
                        "format gate protects pools either way.")
    p.add_argument("--freetext_expansion", default="identifiers",
                   choices=["off", "identifiers", "all"],
                   help="Shape-preserving expander for free-text columns "
                        "(2026-08-05 spec C3). off = pool draws only "
                        "(distinct capped at pool size). identifiers "
                        "(default) = code-like columns expand from their "
                        "observed shape mix. all = also mutate digit runs "
                        "inside texty pool draws. Never adds an LLM call. "
                        "Mode-by-mode panels, guarantees and trade-offs: "
                        "docs/designs/2026-08-05-freetext-expansion-modes.md")
    p.add_argument("--prompt_constraints", default="on",
                   choices=["on", "off"],
                   help="Attach per-column llm_prompt_constraint (parsed "
                        "from column-description JSON) to pool prompts "
                        "(spec C5). Prefix-cache-safe constant suffix.")
    p.add_argument("--source_stats", default="sample",
                   choices=["off", "sample", "exact"],
                   help="Compute per-column source_table_stats from the "
                        "reference sample (driver-side, zero DAG cost). "
                        "exact adds ONE aggregate scan of the live table "
                        "(HLL distinct, deciles, top-k; ADR 0022) and "
                        "feeds exact distinct into free-text pool sizing. "
                        "off disables entirely.")
    p.add_argument("--source_stats_table", default="",
                   help="BQ table for source_table_stats rows "
                        "(project.dataset.table); empty skips the BQ write. "
                        "Existing (table, digest) rows are never rewritten.")
    p.add_argument("--source_stats_json", default="",
                   help="gs:// or local path for the stats JSON artifact; "
                        "empty skips it.")
    p.add_argument("--fk_parent_landing", default="",
                   help="project.dataset holding already-landed synthetic "
                        "parent tables (ADR 0021). With a contract that "
                        "declares FKs, child FK columns sample from the "
                        "parents' landed keys. Empty = FK pools off.")
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
    p.add_argument("--vllm_max_model_len", default="8192",
                   help="vLLM --max-model-len cap. Without it vLLM sizes the "
                        "KV cache for the checkpoint's NATIVE context "
                        "(Qwen3-2507: 262K → 36GiB KV, kills the T4 "
                        "EngineCore at startup). 8192 fits every registry "
                        "model/GPU pairing and dwarfs the synthesis prompts. "
                        "Empty = no cap (native context).")
    args, beam_args = p.parse_known_args(argv)
    if parse_bool_flag(args.build_rag_layer) and not args.rag_chunks_table:
        p.error("--build_rag_layer requires --rag_chunks_table")
    if parse_bool_flag(args.build_pool_layer) and not args.freetext_pools_table:
        p.error("--build_pool_layer requires --freetext_pools_table")
    return args, beam_args


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
    client_type: str,
    model_uri: str,
    vllm_dtype: str = "auto",
    vllm_max_model_len: str = "8192",
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
        # Cap the context so the KV cache fits the GPU; without this vLLM
        # allocates for the checkpoint's native max_position_embeddings
        # (Qwen3-2507: 262K → 36GiB KV vs ~5GiB free on a T4) and the
        # EngineCore exits 1 at startup. Empty = no cap.
        max_len = str(vllm_max_model_len).strip()
        if max_len:
            if not max_len.isdigit() or int(max_len) <= 0:
                raise ValueError(
                    f"vllm_max_model_len must be a positive integer or "
                    f"empty, got {vllm_max_model_len!r}"
                )
            kwargs["max-model-len"] = max_len
        return VLLMModelClient(model_uri=model_uri, vllm_server_kwargs=kwargs)
    if client_type == "mlx":
        from sdfb_beam.handlers.mlx_client import MLXModelClient
        return MLXModelClient(model_uri=model_uri)
    raise ValueError(f"Unknown client_type: {client_type}")


def load_ddl(ddl_uri: str) -> TableSchema:
    """Load `_ddl.json` from gs:// or local; transparent via Beam FileSystems."""
    with FileSystems.open(ddl_uri) as f:
        return TableSchema.model_validate(json.loads(f.read()))


# A pinned --ddl_uri that does not EXIST is an operational miss (new source
# table whose DDL was never exported) and must degrade to live extraction:
# TEST_1 (2026-07-25 16:38) died at template launch on a 404 with a perfectly
# good source table available. A pin that exists but is CORRUPT is a different
# failure — the operator asked for that exact schema — and still raises.
_MISSING_DDL_MARKERS = ("notfound", "no such object", "404", "filenotfound")


def _is_missing_ddl(exc: BaseException) -> bool:
    """True when `exc` means "the object isn't there", not "it's malformed"."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, FileNotFoundError):
            return True
        if isinstance(cur, json.JSONDecodeError):
            return False  # parsed-but-broken: never silently swap the schema
        blob = f"{type(cur).__name__} {cur}".lower()
        if any(m in blob for m in _MISSING_DDL_MARKERS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def resolve_table_schema(ddl_uri: str, reference_table: str) -> TableSchema:
    """WS4 §6b precedence: explicit ``--ddl_uri`` (pin/air-gap) > live
    INFORMATION_SCHEMA extraction from the source table.

    A MISSING pin falls through to live extraction (WS5 T1); a corrupt or
    schema-invalid pin still raises.
    """
    if ddl_uri:
        logger.info("Loading DDL from %s", ddl_uri)
        try:
            schema = load_ddl(ddl_uri)
        except Exception as exc:
            if not _is_missing_ddl(exc):
                raise
            logger.warning(
                "DDL pin %s not found; live-extracting from %s",
                ddl_uri,
                reference_table,
            )
            log_milestone(
                "ddl_uri_miss_fallback",
                uri=ddl_uri,
                table=reference_table,
                error=type(exc).__name__,
            )
        else:
            log_milestone("ddl_loaded_from_uri", uri=ddl_uri)
            return schema
    else:
        logger.info(
            "No --ddl_uri; live-extracting schema from %s", reference_table
        )

    schema = extract_table_schema(reference_table)
    log_milestone(
        "ddl_live_extracted",
        table=reference_table,
        columns=len(schema.columns),
    )
    return schema


def resolve_engine_strictness(client_type: str) -> bool:
    """True for real-LLM client types (``vllm`` on Dataflow/L4, ``mlx`` on
    the M4 DirectRunner) — a failed generation must be loud (strict
    free-text fallback, BLOCKER gate fails the job), not silently degrade
    into memorized reference data. Only the deterministic ``fake`` client
    (CPU smoke run, produces fake data regardless) stays lenient."""
    return client_type != "fake"


# Flex Template parameters are strings; this is the accepted truthy set for
# string-valued boolean flags threaded through the template/DAG chain.
_TRUTHY_FLAG_VALUES = frozenset({"true", "1", "yes"})


def parse_bool_flag(value: str) -> bool:
    """Normalize a string-valued boolean Flex-Template parameter."""
    return str(value).strip().lower() in _TRUTHY_FLAG_VALUES


def resolve_landing_dispositions(
    write_disposition: str, create_if_not_exists: bool
) -> tuple[str, str]:
    """Landing-sink ``(write, create)`` dispositions (WS4 §6a/§6c).

    Landing ONLY — the DLQ, validation_runs and rag_chunks sinks stay
    WRITE_APPEND/CREATE_NEVER unconditionally (blast-radius rule)."""
    write = (
        BigQueryDisposition.WRITE_TRUNCATE
        if write_disposition == "overwrite"
        else BigQueryDisposition.WRITE_APPEND
    )
    create = (
        BigQueryDisposition.CREATE_IF_NEEDED
        if create_if_not_exists
        else BigQueryDisposition.CREATE_NEVER
    )
    return write, create


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


def _load_reference_and_preflight(args, table_schema):
    """Eager reference read + relational preflight (ADR 0021): parse and
    validate the description contract, default pk/identity from it
    (explicit CLI wins), fail fast on unknown columns — all driver-side,
    before any graph exists."""
    logger.info("Loading reference rows from %s (limit=%d)",
                args.reference_table, args.reference_rows_limit)
    reference_rows = load_reference_rows(
        table=args.reference_table,
        limit=args.reference_rows_limit,
    )
    pf = preflight(
        table_schema,
        tuple(c.strip() for c in args.pk_cols.split(",") if c.strip()),
        tuple(c.strip() for c in args.identity_cols.split(",") if c.strip()),
        reference_rows,
        prompt_constraints_enabled=args.prompt_constraints == "on",
    )
    for warning in pf.warnings:
        logger.warning("preflight: %s", warning)
    fk_pools: dict = {}
    if pf.contract and pf.contract.fk and args.fk_parent_landing:
        fk_pools = load_fk_pools(pf.contract.fk, args.fk_parent_landing)
    source_distinct = _emit_source_stats(args, table_schema, reference_rows, pf)
    return reference_rows, pf, fk_pools, source_distinct


def _emit_source_stats(
    args, table_schema, reference_rows, pf
) -> dict[str, int]:
    """WS-B: one profiling pass over the already-loaded reference sample —
    milestone always, JSON artifact and BQ rows when configured. A digest
    that already has rows is skipped (pool-store exists() idiom).

    Returns per-column EXACT distinct counts when ``--source_stats=exact``
    ran (ADR 0022 — they feed free-text pool sizing via
    ``GenerationContext.source_distinct``); empty dict otherwise.
    """
    if args.source_stats == "off" or not reference_rows:
        return {}
    stats = profile_source_table(
        table_schema, reference_rows, contract=pf.contract
    )
    source_distinct: dict[str, int] = {}
    if args.source_stats == "exact":
        from sdfb_beam.io.exact_stats import compute_exact_stats

        # A failed exact pass degrades LOUDLY to sample-tier stats: the run
        # is still valid, just without exact pool sizing.
        try:
            stats = compute_exact_stats(
                args.reference_table, table_schema, stats
            )
        except Exception as exc:
            log_milestone(
                "source_stats_exact_failed",
                level=logging.WARNING,
                table=args.reference_table,
                error=f"{type(exc).__name__}: {exc}",
            )
        else:
            source_distinct = {
                name: int(entry["distinct"])
                for name, entry in stats.items()
                if entry.get("stats_tier") == "exact"
            }
            log_milestone(
                "source_stats_exact",
                table=args.reference_table,
                columns=len(source_distinct),
            )
    sparse = sorted(
        stats.items(), key=lambda kv: -kv[1]["empty_fraction"]
    )[:5]
    log_milestone(
        "source_table_stats",
        table=table_schema.fqn,
        columns=len(stats),
        sparsest=",".join(
            f"{name}:{entry['empty_fraction']:.2f}" for name, entry in sparse
        ),
    )
    digest = compute_reference_digest(reference_rows)
    if args.source_stats_json:
        with FileSystems.create(args.source_stats_json) as fh:
            fh.write(json.dumps(stats, indent=2, default=str).encode())
    if args.source_stats_table:
        store = BigQuerySourceStatsStore(args.source_stats_table)
        # Skip on the tier actually ACHIEVED (a degraded exact run writes
        # sample-tier rows and stays retryable), never the requested one.
        achieved_tier = "exact" if source_distinct else "sample"
        if store.exists(
            table_schema.fqn,
            digest,
            profiler_version=PROFILER_VERSION,
            stats_tier=achieved_tier,
        ):
            log_milestone("source_stats_skipped", reference_digest=digest[:12])
        else:
            store.write_rows(
                stats_rows(table_schema.fqn, digest, args.run_id, stats)
            )
            log_milestone(
                "source_stats_written",
                reference_digest=digest[:12],
                rows=len(stats),
            )
    return source_distinct


def warm_pools_trusted(
    pool_store, source_value_store, reference_digest: str, model_uri: str
) -> bool:
    """False ⇒ the persisted pools overlap the live source and were
    deleted for a clean rebuild; True ⇒ keep the warm path.

    2026-08-07 10M warm run: `exists()` was the only guard, so the
    memorized 2026-08-05 pools (33-99% verbatim source values) were
    replayed wholesale at 10M-row scale. Both failure paths keep the warm
    pools — LOUDLY — because a taint check must never kill a launch
    (pools are an optimisation) and an append-rebuild without a clean
    delete would leave stale rows racing the rebuilt ones in `fetch`.
    """
    try:
        overlap = pool_source_overlap(
            pool_store, source_value_store, reference_digest, model_uri
        )
    except Exception as exc:
        log_milestone(
            "pool_taint_check_error",
            level=logging.WARNING,
            error=type(exc).__name__,
        )
        return True
    if not overlap:
        return True
    log_milestone(
        "pool_taint_rebuild",
        level=logging.WARNING,
        columns=len(overlap),
        # Counts only — reference values must never reach logs.
        overlap_counts={c: n for c, n in sorted(overlap.items())},
    )
    try:
        pool_store.delete(reference_digest, model_uri)
    except Exception as exc:
        log_milestone(
            "pool_taint_delete_error",
            level=logging.ERROR,
            error=type(exc).__name__,
        )
        return True
    return False


def resolve_pool_layer(args, reference_rows: list[dict]) -> tuple:
    """(freetext_pools_store, source_value_store) for `build_pipeline`.

    (None, None) when the pool layer is off; (None, store) when the warm
    pools were verified clean (branch skipped); (pool_store, value_store)
    when the branch must build — cold store, or a warm store the taint
    preflight condemned.
    """
    if not (parse_bool_flag(args.build_pool_layer) and args.freetext_pools_table):
        return None, None
    digest = compute_reference_digest(reference_rows)
    pool_store = BigQueryFreeTextPoolStore(args.freetext_pools_table)
    # Full-domain novelty rejection for the build branch, and the
    # taint preflight for the warm path (2026-08-05/07 E2E findings).
    source_value_store = BigQuerySourceValueStore(args.reference_table)
    # A MISSING pool table must not surface as a cryptic NotFound out of
    # the driver — that is exactly how TEST_1 (2026-07-25 16:38) died on
    # a 404. The table is never auto-created (the CREATE_IF_NEEDED
    # blast-radius rule confines auto-create to the landing sink), so
    # say what to run. The READ path degrades silently and correctly on
    # its own; only an explicit --build_pool_layer reaches here.
    try:
        already_built = pool_store.exists(digest, args.model_uri)
    except Exception as exc:
        proj, ds, tbl = args.freetext_pools_table.split(".", 2)
        raise SystemExit(
            f"--build_pool_layer needs {args.freetext_pools_table}, which "
            f"could not be read ({type(exc).__name__}: {exc}).\n"
            f"Create it once:\n"
            f"  bq mk --table {proj}:{ds}.{tbl} "
            f"config/bq_schema/synthetic_rag/freetext_pools.schema.json\n"
            f"Or drop --build_pool_layer: pools are an optimisation, and "
            f"the run works without them (they are rebuilt per worker)."
        ) from exc
    if already_built:
        # exists() alone let the 2026-08-07 10M warm run replay the
        # memorized 2026-08-05 pools; a warm store must also prove it
        # holds no live source values before it is trusted.
        already_built = warm_pools_trusted(
            pool_store, source_value_store, digest, args.model_uri
        )
    if already_built:
        log_milestone(
            "pool_build_skipped",
            reference_digest=digest[:12],
            model_uri=args.model_uri,
        )
        return None, source_value_store
    # The branch writes the store itself (blocking load job inside
    # the DoFn) so the pipeline's AwaitFreeTextPools gate releases
    # Generate only once the rows are readable — a sibling
    # WriteToBigQuery sink raced Generate on the 2026-07-28/29 cold
    # runs and every pool was built twice.
    return pool_store, source_value_store


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

    table_schema = resolve_table_schema(args.ddl_uri, args.reference_table)
    logger.info("Loaded schema for %s (%d columns)",
                table_schema.fqn, len(table_schema.columns))

    logger.info("Building model client (client_type=%s, vllm_dtype=%s, "
                "vllm_max_model_len=%s)",
                args.client_type, args.vllm_dtype, args.vllm_max_model_len)
    model_client = build_model_client(
        args.client_type,
        args.model_uri,
        vllm_dtype=args.vllm_dtype,
        vllm_max_model_len=args.vllm_max_model_len,
    )

    reference_rows, pf, fk_pools, source_distinct = _load_reference_and_preflight(
        args, table_schema
    )

    thresholds = resolve_thresholds(args.thresholds_uri, args.env)
    logger.info("Thresholds (env=%s): blocker_failure_ratio=%.4f",
                thresholds.env, thresholds.blocker_failure_ratio)

    embedder_id, embedder_version = embedder_identity(args.embedder_uri)

    rag_chunks_sink = None
    if parse_bool_flag(args.build_rag_layer) and args.rag_chunks_table:
        digest = compute_reference_digest(reference_rows)
        store = BigQueryChunkStore(args.rag_chunks_table)
        if store.exists(digest, embedder_id, embedder_version):
            log_milestone(
                "rag_population_skipped",
                reference_digest=digest[:12],
                embedder_id=embedder_id,
            )
        else:
            rag_chunks_sink = WriteToBigQuery(
                table=args.rag_chunks_table,
                method=WriteToBigQuery.Method.FILE_LOADS,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_NEVER,
            )

    freetext_pools_store, source_value_store = resolve_pool_layer(
        args, reference_rows
    )

    config = PipelineConfig(
        table_schema=table_schema,
        engine_name=args.engine,
        model_client=model_client,
        num_rows=args.num_rows,
        batch_size=resolve_batch_size(args.batch_size, args.num_rows),
        similarity=args.similarity,
        seed=int(args.seed) if str(args.seed).strip() else None,
        run_id=args.run_id,
        identity_columns=pf.identity_cols,
        pk_columns=pf.pk_cols,
        strict_freetext=resolve_engine_strictness(args.client_type),
        model_uri=args.model_uri,
        embedder_uri=args.embedder_uri,
        reference_table=args.reference_table,
        landing_table=args.landing_table,
        thresholds=thresholds,
        # A fake-client (CPU smoke) run produces fake data, so failing the job
        # on the BLOCKER gate is meaningless — keep it informational (the
        # validation_runs row still records the status). Real engines gate.
        fail_on_blocker=resolve_engine_strictness(args.client_type),
        rag_chunks_table=args.rag_chunks_table,
        embedder_id=embedder_id,
        embedder_version=embedder_version,
        pool_pattern_guidance=parse_bool_flag(args.pool_pattern_guidance),
        freetext_pools_table=args.freetext_pools_table,
        pool_seed_strategy=validate_seed_strategy(args.pool_seed_strategy),
        uniqueness_mode=args.uniqueness_mode,
        freetext_expansion=args.freetext_expansion,
        prompt_constraints=args.prompt_constraints == "on",
        fk_pools=fk_pools,
        source_distinct=source_distinct,
    )

    create_if_not_exists = parse_bool_flag(args.create_if_not_exists)
    landing_write, landing_create = resolve_landing_dispositions(
        args.write_disposition, create_if_not_exists
    )
    landing_kwargs: dict = {}
    if create_if_not_exists:
        # CREATE_IF_NEEDED must carry the target schema — derived
        # in-pipeline from the resolved TableSchema (WS4 §6c), never
        # hand-provisioned. Must be the load-safe projection: the
        # FILE_LOADS runtime path (vendored apitools `TableFieldSchema`)
        # rejects `maxLength`/`precision`/`scale`/`defaultValueExpression`
        # with an `AttributeError` at load-job time, even though Beam
        # accepts the fuller `derive_bq_schema` dict at graph construction
        # (WS4 final-review CRITICAL-1).
        landing_kwargs["schema"] = derive_bq_load_schema(table_schema)
    log_milestone(
        "landing_sink_config",
        write_disposition=landing_write,
        create_disposition=landing_create,
    )
    landing_sink = WriteToBigQuery(
        table=args.landing_table,
        method=WriteToBigQuery.Method.FILE_LOADS,
        write_disposition=landing_write,
        create_disposition=landing_create,
        **landing_kwargs,
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
            rag_chunks_sink=rag_chunks_sink,
            freetext_pools_store=freetext_pools_store,
            source_value_store=(
                source_value_store if freetext_pools_store is not None else None
            ),
        )
        logger.info(
            "Pipeline launched: run_id=%s reference_digest=%s",
            result["run_id"],
            result["reference_digest"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
