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
from dataclasses import dataclass, field
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
from sdfb_core.contracts.prompt_constraint import parse_llm_prompt_constraint
from sdfb_core.contracts.relationships import (
    RelationshipRegistry,
)
from sdfb_core.observability import (
    log_build_info,
    log_milestone,
    log_milestone_pretty,
    log_milestone_text,
)
from sdfb_core.rag.embedding import embedder_identity
from sdfb_core.stats import PROFILER_VERSION, profile_source_table, stats_rows
from sdfb_core.validation import Thresholds

from sdfb_beam.cli.preflight import preflight
from sdfb_beam.ddl import extract_table_schema
from sdfb_beam.dofns.uniqueness import UNIQUENESS_MODES
from sdfb_beam.io.bq_sources import load_reference_rows
from sdfb_beam.io.digest import compute_reference_digest
from sdfb_beam.io.fk_pools import (
    load_fk_key_pools,
    parent_landing_fqn,
    per_column_view,
)
from sdfb_beam.io.relationships import load_relationship_registry
from sdfb_beam.io.source_values import (
    BigQuerySourceValueStore,
    pool_source_overlap,
)
from sdfb_beam.io.stats_store import BigQuerySourceStatsStore
from sdfb_beam.pipeline import (
    FkEdgeSpec,
    PipelineConfig,
    TableSpec,
    build_pipeline,
    build_relational_pipeline,
)
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
                   help="gs:// or local path to _ddl.json — the OFFLINE "
                        "FALLBACK only (ADR 0027 D2). Live extraction is "
                        "authoritative at every launch: structure from "
                        "--reference_table, description surfaces "
                        "(llm_prompt_constraint + relational contract) "
                        "overlaid from --landing_table — the SOURCE "
                        "table's descriptions are stripped, never used. "
                        "The pin (extract it from the LANDING table) is "
                        "consumed only when live extraction fails "
                        "(air-gap, BQ outage); staleness is reported "
                        "(ddl_pin_drift/ddl_pin_fresh).")
    p.add_argument("--reference_table", required=True,
                   help="FQN of source table for live SELECT reference rows")
    p.add_argument("--reference_rows_limit", type=int, default=10_000)
    p.add_argument("--landing_table", required=True,
                   help="BQ table for synthetic rows (project.dataset.table). "
                        "Accepts a comma-separated list for multi-table "
                        "launches (ADR 0029 scenarios): each table runs "
                        "sequentially with a suffixed run_id.")
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
    p.add_argument("--prompt_debug", default="off",
                   choices=["off", "redacted", "full"],
                   help="Log each built pool prompt as a "
                        "freetext_pool_prompt milestone (ADR 0024). "
                        "'redacted' elides seed exemplars; 'full' logs "
                        "verbatim prompts at WARNING — reference values "
                        "reach Dataflow logs, debug runs only.")
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
    p.add_argument("--generate_fk_relationships", default="true",
                   help="true (default): declared relationships are "
                        "honored — a launch expands to the table's whole "
                        "FK component (parents first) and children sample "
                        "landed parent keys; tables with no declared "
                        "relationships behave exactly as false (zero "
                        "friction). false: isolated generation — declared "
                        "edges ignored LOUDLY, FK columns use marginals. "
                        "ADR 0029.")
    p.add_argument("--fk_parent_landing", default="",
                   help="EXPERT OVERRIDE only (ADR 0029): parents are "
                        "assumed landed in the --landing_table dataset and "
                        "this derives automatically. Set it only when "
                        "parents land in a DIFFERENT project.dataset.")
    p.add_argument("--multi_table_mode", default="single_job",
                   choices=["single_job", "sequential_jobs"],
                   help="How a multi-table plan executes (ADR 0030). "
                        "single_job (default): every planned table in ONE "
                        "Dataflow job — one worker fleet, one vLLM "
                        "ignition, in-DAG FK key handoff. sequential_jobs: "
                        "one job per table, parents first (fallback / "
                        "debugging).")
    p.add_argument("--relationships_uri", default="config/relationships",
                   help="Where the relational models live (ADR 0032): a "
                        "folder or a single YAML file, local or gs://. "
                        "Default: the config/relationships folder packaged "
                        "in the image. Point it at gs://... to change PK/FK "
                        "without rebuilding — this is the ONLY source of "
                        "relational truth; table descriptions are never "
                        "read for it.")
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


def resolve_table_schema(
    ddl_uri: str, reference_table: str, landing_table: str = ""
) -> TableSchema:
    """Live INFORMATION_SCHEMA extraction is AUTHORITATIVE at every launch,
    and generation-steering metadata comes from the TARGET table only
    (ADR 0027 D2, 2026-08-21).

    Two rules from the 2026-08-21 four-run cycle:

    1. **Live-first.** The schema is fetched from the bqClient every
       launch — the cycle consumed a stale ``--ddl_uri`` pin and silently
       dropped every constraint edit (zero `prompt_constraints_found`
       across four jobs). ``--ddl_uri`` demotes to the OFFLINE FALLBACK
       (air-gapped launcher, BQ outage); a corrupt pin in offline mode
       still raises — the operator's declared fallback is broken.
    2. **Target-only steering metadata.** Structure (columns/types/modes)
       mirrors the SOURCE table, but the description surfaces — the
       `llm_prompt_constraint` clauses and the `{"sdfb":1,…}` contract —
       are overlaid from the LANDING (synthetic/target) table, which the
       synthetic-data team owns and Terraforms. The source (lake) table's
       descriptions are another team's prose and must NEVER steer
       generation: with the target unreachable they are STRIPPED, not
       inherited. The offline pin is the one exception — it is the
       operator's declared fallback, extracted from the landing table per
       the propagation runbook, so its descriptions stand when the target
       is also unreachable.
    """
    live_error: Exception | None = None
    if reference_table:
        try:
            schema = extract_table_schema(reference_table)
        except Exception as exc:  # any live failure → offline fallback
            live_error = exc
        else:
            log_milestone(
                "ddl_live_extracted",
                table=reference_table,
                columns=len(schema.columns),
            )
            schema = _overlay_target_metadata(
                schema, landing_table, strip_on_missing=True
            )
            if ddl_uri:
                _check_ddl_pin_staleness(schema, ddl_uri)
            return schema

    if ddl_uri:
        if live_error is not None:
            logger.warning(
                "Live DDL extraction from %s failed (%s); using --ddl_uri "
                "offline fallback %s",
                reference_table,
                type(live_error).__name__,
                ddl_uri,
            )
            log_milestone(
                "ddl_live_extract_failed",
                level=logging.WARNING,
                table=reference_table,
                error=type(live_error).__name__,
                note="using --ddl_uri offline fallback — constraints/contract "
                "are as-of the pin's extraction, not the live deployment",
            )
        schema = load_ddl(ddl_uri)
        log_milestone(
            "ddl_loaded_from_uri",
            uri=ddl_uri,
            fallback=live_error is not None,
        )
        return _overlay_target_metadata(
            schema, landing_table, strip_on_missing=False
        )

    if live_error is not None:
        raise live_error
    raise ValueError(
        "resolve_table_schema needs --reference_table (live extraction) "
        "or --ddl_uri (offline fallback)"
    )


def _overlay_target_metadata(
    base: TableSchema, landing_table: str, *, strip_on_missing: bool
) -> TableSchema:
    """Replace `base`'s description surfaces with the TARGET table's.

    `strip_on_missing=True` (live-source base): the source's descriptions
    must never survive, so an unreachable target strips them to empty.
    `strip_on_missing=False` (offline pin base): the pin is the
    operator's declared fallback and its descriptions stand.
    """
    target: TableSchema | None = None
    if landing_table:
        try:
            target = extract_table_schema(landing_table)
        except Exception as exc:
            log_milestone(
                "target_metadata_unavailable",
                level=logging.WARNING,
                table=landing_table,
                error=type(exc).__name__,
                note="no constraints/contract from the target this run; "
                "source descriptions are never used as a substitute",
            )
    if target is None:
        if not strip_on_missing:
            return base
        return _with_descriptions(base, "", {})
    col_desc = {c.name: (c.description or "") for c in target.columns}
    schema = _with_descriptions(
        base, target.table_info.description or "", col_desc
    )
    log_milestone(
        "target_metadata_overlaid",
        table=landing_table,
        constraint_columns=len(_constraint_clauses(schema)),
    )
    return schema


def _with_descriptions(
    schema: TableSchema, table_description: str, col_desc: dict[str, str]
) -> TableSchema:
    """A copy of `schema` whose description surfaces are exactly the given
    ones — absent columns get empty, never the base's leftovers."""
    return schema.model_copy(
        update={
            "table_info": schema.table_info.model_copy(
                update={"description": table_description}
            ),
            "columns": [
                c.model_copy(
                    update={"description": col_desc.get(c.name, "")}
                )
                for c in schema.columns
            ],
        }
    )


def _check_ddl_pin_staleness(live: TableSchema, ddl_uri: str) -> None:
    """Say aloud when the ``--ddl_uri`` pin disagrees with the LIVE schema
    on generation-steering metadata.

    Live already won this launch — the check protects the NEXT offline
    day: a stale pin would silently drop the constraint edits again the
    moment INFORMATION_SCHEMA becomes unreachable (exactly the
    2026-08-21 four-run failure class, then with the pin authoritative).
    Best-effort: an unloadable pin is a WARNING here, never fatal.
    """
    try:
        pinned = load_ddl(ddl_uri)
    except Exception as exc:
        log_milestone(
            "ddl_pin_check_error",
            level=logging.WARNING,
            uri=ddl_uri,
            error=type(exc).__name__,
            note="pin unusable as an offline fallback",
        )
        return
    pinned_clauses = _constraint_clauses(pinned)
    live_clauses = _constraint_clauses(live)
    drifted = sorted(
        col
        for col in set(pinned_clauses) | set(live_clauses)
        if pinned_clauses.get(col, "") != live_clauses.get(col, "")
    )
    table_drift = (
        (pinned.table_info.description or "")
        != (live.table_info.description or "")
    )
    if drifted or table_drift:
        log_milestone(
            "ddl_pin_drift",
            level=logging.WARNING,
            uri=ddl_uri,
            columns=",".join(drifted) or "-",
            table_description_drift=table_drift,
            fix="re-run scripts/extract_ddl.py so the offline fallback "
            "matches the live deployment",
        )
    else:
        log_milestone(
            "ddl_pin_fresh",
            uri=ddl_uri,
            constraint_columns=len(live_clauses),
        )


def _constraint_clauses(schema: TableSchema) -> dict[str, str]:
    """column → parsed `llm_prompt_constraint` clause (unparseable → the
    raw description, so a broken edit still reads as drift)."""
    out: dict[str, str] = {}
    for col in schema.columns:
        try:
            clause = parse_llm_prompt_constraint(
                col.description, column=col.name
            )
        except Exception:
            clause = col.description or ""
        if clause:
            out[col.name] = clause
    return out


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


def resolve_fk_mode(
    generate_fk_relationships: bool, fk_parent_landing: str
) -> tuple[str, str]:
    """``(effective fk_parent_landing, mode)`` for one table's run
    (ADR 0029 rev B — minimal-input scenarios).

    Isolated (`--generate_fk_relationships=false`): FK pools are simply
    off — no sentinel value, no preflight refusal; the launcher already
    warned loudly. Relational: the value is whatever the plan derived
    (the landing table's own dataset unless overridden)."""
    if not generate_fk_relationships:
        return "", "isolated"
    return fk_parent_landing, "relational"


def derive_fk_parent_landing(landing_table: str) -> str:
    """``project.dataset`` of the landing table — parents land in the
    SAME dataset, so the old --fk_parent_landing input is derivable and
    no longer a user concern (ADR 0029 rev B)."""
    return landing_table.rsplit(".", 1)[0]


def derive_source_fqn(landing_table: str, reference_table: str) -> str:
    """A sibling's source FQN: the reference table's dataset + the
    sibling's table name (the repo's same-name convention)."""
    src_dataset = reference_table.rsplit(".", 1)[0]
    return f"{src_dataset}.{landing_table.rsplit('.', 1)[-1]}"


def assert_fk_pools_nonempty(
    fks, fk_pools: dict, parent_landing: str
) -> None:
    """Loud stop when an enforced FK edge loaded an EMPTY parent pool
    (ADR 0029 rev B): an empty parent means "not landed yet", and
    generating the child anyway would silently repeat the 2026-08-21
    false '0 orphans'. Scenario 2 orders parents first automatically;
    a direct child launch must land parents first."""
    missing = sorted(
        fk.ref for fk in fks if not fk_pools.get(fk.cols[0])
    )
    if missing:
        raise SystemExit(
            f"FK parents not landed (or empty) in {parent_landing}: "
            f"{missing}. Scenario 2 (--generate_fk_relationships=true on "
            f"the launch) generates parents first automatically; for a "
            f"manual child-only run, land the parents first."
        )


def parse_landing_tables(value: str) -> list[str]:
    """``--landing_table`` accepts one FQN or a comma-separated list."""
    return [t.strip() for t in value.split(",") if t.strip()]


@dataclass(frozen=True)
class TableRun:
    """One planned per-table generation inside a launch."""

    landing_table: str
    source_table: str
    run_id: str
    fk_parent_landing: str


@dataclass(frozen=True)
class LaunchPlan:
    scenario: str
    runs: tuple[TableRun, ...]
    warnings: tuple[str, ...] = field(default=())


def plan_launch(
    landing_tables: list[str],
    reference_table: str,
    generate_fk_relationships: bool,
    registry: RelationshipRegistry,
    run_id: str,
) -> LaunchPlan:
    """The launch scenarios, resolved to an ordered per-table plan.

    Two inputs decide everything (ADR 0029 rev B, now sourced from
    `config/relationships/` — ADR 0032): the landing table(s) and the
    flag. The registry answers the rest.

    1. one table + false  → itself only; any enforced edge is loudly
       ignored (referential integrity UNVERIFIED for that run).
    2. one table + true   → not in a model, or detached: identical to 1,
       zero friction. In a model: its whole ENABLED component, ordered
       parents-first. Disabling one table in the model file detaches it
       and everything that reached the rest only through it.
    3. many tables + false → each independently, in the given order —
       concurrent isolated generation, no relationships anywhere.
       many tables + true  → the union of their components. Rare and
       expensive, so it says so.
    """
    warnings: list[str] = []
    if generate_fk_relationships:
        by_name: dict[str, str] = {}
        for target in landing_tables:
            dataset = derive_fk_parent_landing(target)
            for name in registry.component(target):
                by_name.setdefault(name, f"{dataset}.{name}")
        ordered = [
            by_name[name]
            for name in registry.generation_order(tuple(by_name))
        ]
        expanded = [t for t in ordered if t not in landing_tables]
        if expanded:
            warnings.append(
                f"the relational model expanded this launch to {expanded} "
                f"(declared in config/relationships; parents generate first)"
            )
        if len(landing_tables) > 1:
            warnings.append(
                f"{len(landing_tables)} targets WITH relationships: the "
                f"launch covers {len(ordered)} tables in one job. Pass "
                f"--generate_fk_relationships=false for independent "
                f"concurrent generation instead."
            )
        relational = bool(expanded) or any(
            registry.enforced_edges(t) for t in ordered
        )
        scenario = (
            "relational_closure"
            if relational
            else ("isolated" if len(ordered) == 1 else "multi_independent")
        )
    else:
        ordered = list(landing_tables)
        ignored = [t for t in ordered if registry.enforced_edges(t)]
        if ignored:
            warnings.append(
                f"generate_fk_relationships=false ignores the enforced FK "
                f"edges declared on {ignored} — referential integrity "
                f"UNVERIFIED for this run"
            )
        scenario = "isolated" if len(ordered) == 1 else "multi_isolated"

    multi = len(ordered) > 1
    runs = tuple(
        TableRun(
            landing_table=t,
            source_table=derive_source_fqn(t, reference_table),
            run_id=(
                f"{run_id}-{i:02d}-{t.rsplit('.', 1)[-1]}" if multi else run_id
            ),
            fk_parent_landing=(
                derive_fk_parent_landing(t)
                if generate_fk_relationships and registry.enforced_edges(t)
                else ""
            ),
        )
        for i, t in enumerate(ordered)
    )
    return LaunchPlan(
        scenario=scenario, runs=runs, warnings=tuple(warnings)
    )


def log_relationship_model(
    table_fqn: str,
    registry: RelationshipRegistry,
    mode: str,
) -> None:
    """The run's relational truth, in one glanceable log entry.

    `relationship_model` carries the card (tables, PK, identity, every
    edge with enforced/documented/disabled state, generation waves, and
    the file it came from) plus the fenced mermaid source below it for
    report tooling. `fk_generation_mode` stays as the one-line greppable
    state. Nothing here is inferred: it is the model file, rendered.
    """
    log_milestone("fk_generation_mode", table=table_fqn, mode=mode)
    relations = registry.relations(table_fqn)
    enforced = len(registry.enforced_edges(table_fqn))
    documented = sum(
        1 for e in (relations.fk if relations else ()) if not e.enforced
    )
    log_milestone_text(
        "relationship_model",
        registry.log_body(table_fqn),
        level=(
            logging.WARNING
            if mode == "relational" and relations is not None and not enforced
            and documented
            else logging.INFO
        ),
        table=table_fqn,
        model=getattr(registry.model_for(table_fqn), "model", "none"),
        enforced=enforced,
        documented=documented,
        enabled=registry.enabled(table_fqn),
        sha=registry.sha12(),
        mode=mode,
    )


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


def _load_reference_and_preflight(
    args,
    table_schema,
    registry: RelationshipRegistry,
    in_set_landing: frozenset[str] = frozenset(),
):
    """Eager reference read + relational preflight (ADR 0032): the
    table's relations come from `config/relationships/`, pk/identity
    default from them (CLI fills the gaps for tables no model declares),
    and unknown columns fail fast — all driver-side, before any graph
    exists."""
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
        relations=registry.relations(args.landing_table),
        prompt_constraints_enabled=args.prompt_constraints == "on",
        # ADR 0028 P4: refuse a PK whose routed generator cannot cover
        # num_rows — before any graph exists.
        num_rows=args.num_rows,
    )
    for warning in pf.warnings:
        logger.warning("preflight: %s", warning)
    # ADR 0029 rev B — FK activation derives, never asks: with the flag
    # on and enforced edges declared, parents live in the SAME landing
    # dataset (--fk_parent_landing stays as an expert override only).
    # An empty parent pool is a loud, actionable stop.
    fk_pools: dict = {}
    fk_key_pools: list[dict] = []
    in_set_names = {t.rsplit(".", 1)[-1] for t in in_set_landing}
    enforced_fk = registry.enforced_edges(args.landing_table)
    external_fk = tuple(
        fk
        for fk in enforced_fk
        if fk.ref.rsplit(".", 1)[-1] not in in_set_names
    )
    if parse_bool_flag(args.generate_fk_relationships) and external_fk:
        parent_landing = args.fk_parent_landing or derive_fk_parent_landing(
            args.landing_table
        )
        fk_key_pools = load_fk_key_pools(external_fk, parent_landing)
        fk_pools = per_column_view(fk_key_pools)
        assert_fk_pools_nonempty(external_fk, fk_pools, parent_landing)
        args.fk_parent_landing = parent_landing  # ctx fk_edges read it
    elif parse_bool_flag(args.generate_fk_relationships) and enforced_fk:
        # All enforced edges resolve in-set (ADR 0030 single job): keys
        # arrive as side inputs; still derive for the fk_edges metadata.
        args.fk_parent_landing = args.fk_parent_landing or (
            derive_fk_parent_landing(args.landing_table)
        )
    source_distinct = _emit_source_stats(args, table_schema, reference_rows, pf)
    return reference_rows, pf, fk_pools, fk_key_pools, source_distinct


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
        table_schema, reference_rows, relations=pf.relations
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
    # First line of every launch: which build is this? (2026-08-21 cycle —
    # two same-day runs were indistinguishable by build from the logs.)
    log_build_info("launcher")
    args, beam_argv = parse_args(argv or sys.argv[1:])

    # ADR 0029 rev B — resolve the launch scenario BEFORE any per-table
    # work: minimal inputs (landing table(s) + one flag), everything
    # else derived. One milestone states the whole plan.
    targets = parse_landing_tables(args.landing_table)
    generate_fk = parse_bool_flag(args.generate_fk_relationships)
    # ADR 0032 — the relational model comes from config, not from
    # production metadata: no dataset scan, no table-description parse,
    # one file the operator can read and version.
    registry = load_relationship_registry(args.relationships_uri)
    plan = plan_launch(
        targets, args.reference_table, generate_fk, registry, args.run_id
    )
    for warning in plan.warnings:
        logger.warning("launch plan: %s", warning)
    log_milestone(
        "launch_scenario",
        scenario=plan.scenario,
        tables=len(plan.runs),
        order=",".join(r.landing_table.rsplit(".", 1)[-1] for r in plan.runs),
        generate_fk_relationships=generate_fk,
    )
    # ONE human-readable block stating every enabled/disabled config of
    # this execution (ADR 0030): pasted _full_report.md +
    # worker_logs.jsonl answer "what was on?" without arg archaeology.
    log_milestone_pretty(
        "launch_config",
        {
            **{
                k: v
                for k, v in sorted(vars(args).items())
                if not k.startswith("_") and k != "model_client"
            },
            "resolved": {
                "scenario": plan.scenario,
                "generate_fk_relationships": generate_fk,
                "multi_table_mode": args.multi_table_mode,
                "tables_in_order": [
                    r.landing_table for r in plan.runs
                ],
                "run_ids": [r.run_id for r in plan.runs],
                "fk_parent_landing_derived": [
                    r.fk_parent_landing or "(none)" for r in plan.runs
                ],
                "warnings": list(plan.warnings),
            },
        },
        scenario=plan.scenario,
    )
    for target in targets:
        log_relationship_model(
            target,
            registry,
            mode="relational" if generate_fk else "isolated",
        )
    if len(plan.runs) > 1 and args.multi_table_mode == "single_job":
        return _run_relational_job(plan, args, beam_argv, registry=registry)
    for i, run in enumerate(plan.runs):
        table_args = argparse.Namespace(**vars(args))
        table_args._multi_table_plan = len(plan.runs) > 1
        table_args.landing_table = run.landing_table
        table_args.reference_table = run.source_table
        table_args.run_id = run.run_id
        table_args.fk_parent_landing = (
            args.fk_parent_landing or run.fk_parent_landing
        )
        # The --ddl_uri pin describes the FIRST target only; siblings
        # extract live (authoritative per ADR 0027 D2).
        if run.landing_table != targets[0]:
            table_args.ddl_uri = ""
        rc = _run_one_table(table_args, beam_argv, registry=registry)
        if rc != 0:
            logger.error(
                "table %s failed (rc=%d) — aborting the remaining %d "
                "planned tables (children never run without parents)",
                run.landing_table, rc, len(plan.runs) - i - 1,
            )
            return rc
    return 0


def _prepare_table_spec(
    args,
    model_client,
    in_set_landing: frozenset[str] = frozenset(),
    registry: RelationshipRegistry | None = None,
) -> TableSpec:
    """Everything one table needs, driver-side: schema, preflight, FK
    pools (EXTERNAL parents only — in-set parents arrive as in-DAG side
    inputs, ADR 0030), stores, sinks, config. Shared by the
    single-table runner and the single-job relational runner."""
    table_schema = resolve_table_schema(
        args.ddl_uri, args.reference_table, args.landing_table
    )
    logger.info("Loaded schema for %s (%d columns)",
                table_schema.fqn, len(table_schema.columns))

    # ADR 0029 rev B: mode is descriptive here — activation derives
    # inside _load_reference_and_preflight from the table's relations.
    args.fk_parent_landing, fk_mode = resolve_fk_mode(
        parse_bool_flag(args.generate_fk_relationships),
        args.fk_parent_landing,
    )
    if fk_mode == "isolated":
        log_milestone(
            "fk_generation_disabled",
            level=logging.WARNING,
            table=args.reference_table,
            note="--generate_fk_relationships=false — any declared FK "
            "edges generate from marginals; referential integrity "
            "UNVERIFIED this run",
        )
    registry = registry or RelationshipRegistry()
    (
        reference_rows,
        pf,
        fk_pools,
        fk_key_pools,
        source_distinct,
    ) = _load_reference_and_preflight(
        args, table_schema, registry, in_set_landing=in_set_landing
    )
    log_relationship_model(args.landing_table, registry, mode=fk_mode)

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
        prompt_debug=args.prompt_debug,
        fk_pools=fk_pools,
        fk_key_pools=fk_key_pools,
        log_table_prefix=(
            args.landing_table.rsplit(".", 1)[-1]
            if getattr(args, "_multi_table_plan", False) or in_set_landing
            else ""
        ),
        fk_edges=tuple(
            {
                "cols": list(fk.cols),
                "ref": fk.ref,
                "ref_cols": list(fk.ref_cols),
                "enforced": fk.enforced,
                "parent_landing": (
                    parent_landing_fqn(fk.ref, args.fk_parent_landing)
                    if args.fk_parent_landing and fk.enforced
                    else ""
                ),
            }
            for fk in (pf.relations.fk if pf.relations else ())
        ),
        # The card the launcher printed, carried verbatim to the workers
        # (ADR 0032): the model is resolved ONCE, driver-side, and both
        # logs show the same thing.
        relationship_card=registry.log_body(args.landing_table),
        source_distinct=source_distinct,
        # ADR 0023 generate-path seam: B.2 builds pools lazily in workers.
        source_values_table=args.reference_table,
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

    # In-set enforced edges: parents live in THIS job's spec list — no BQ
    # pool load; the composer wires the parent's landed keys as a side
    # input (ADR 0030). parent_pk is patched in by the relational runner.
    in_set_names = {t.rsplit(".", 1)[-1] for t in in_set_landing}
    parent_edges = tuple(
        FkEdgeSpec(
            child_cols=tuple(fk.cols),
            ref_cols=tuple(fk.ref_cols),
            parent_landing=parent_landing_fqn(
                fk.ref, derive_fk_parent_landing(args.landing_table)
            ),
        )
        for fk in registry.enforced_edges(args.landing_table)
        if fk.ref.rsplit(".", 1)[-1] in in_set_names
    )

    return TableSpec(
        config=config,
        reference_rows=reference_rows,
        landing_sink=landing_sink,
        dlq_sink=dlq_sink,
        validation_runs_sink=validation_runs_sink,
        rag_chunks_sink=rag_chunks_sink,
        freetext_pools_store=freetext_pools_store,
        source_value_store=(
            source_value_store if freetext_pools_store is not None else None
        ),
        parent_edges=parent_edges,
    )


def _run_one_table(
    args, beam_argv: list[str],
    registry: RelationshipRegistry | None = None,
) -> int:
    options = PipelineOptions(beam_argv)
    runner = options.view_as(StandardOptions).runner or "DataflowRunner"
    configure_pipeline_options(options, runner, args.run_id)

    logger.info("Building model client (client_type=%s, vllm_dtype=%s, "
                "vllm_max_model_len=%s)",
                args.client_type, args.vllm_dtype, args.vllm_max_model_len)
    model_client = build_model_client(
        args.client_type,
        args.model_uri,
        vllm_dtype=args.vllm_dtype,
        vllm_max_model_len=args.vllm_max_model_len,
    )
    spec = _prepare_table_spec(args, model_client, registry=registry)

    with beam.Pipeline(options=options) as p:
        result = build_pipeline(
            p,
            reference_rows=spec.reference_rows,
            config=spec.config,
            landing_sink=spec.landing_sink,
            dlq_sink=spec.dlq_sink,
            validation_runs_sink=spec.validation_runs_sink,
            rag_chunks_sink=spec.rag_chunks_sink,
            freetext_pools_store=spec.freetext_pools_store,
            source_value_store=spec.source_value_store,
        )
        logger.info(
            "Pipeline launched: run_id=%s reference_digest=%s",
            result["run_id"],
            result["reference_digest"],
        )
    return 0


def _run_relational_job(
    plan, args, beam_argv: list[str],
    registry: RelationshipRegistry | None = None,
) -> int:
    """ADR 0030 — scenario 2/3 in ONE Dataflow job: every planned table's
    subgraph in one pipeline, parents-first, children fed by in-DAG
    parent-key side inputs. One worker fleet and one vLLM ignition serve
    all tables; the 911 s launch+boot (measured, ADR 0028 figures) is
    paid once instead of per table."""
    from dataclasses import replace as _dc_replace

    options = PipelineOptions(beam_argv)
    runner = options.view_as(StandardOptions).runner or "DataflowRunner"
    configure_pipeline_options(options, runner, args.run_id)

    model_client = build_model_client(
        args.client_type,
        args.model_uri,
        vllm_dtype=args.vllm_dtype,
        vllm_max_model_len=args.vllm_max_model_len,
    )
    in_set = frozenset(r.landing_table for r in plan.runs)
    # The --ddl_uri pin describes the USER'S target table(s) — closure
    # siblings extract live only (2026-08-22 launch: the first PLANNED
    # table, a parent, wrongly inherited the target's pin and logged
    # spurious ddl_pin_drift).
    pin_owners = set(parse_landing_tables(args.landing_table))
    specs = []
    prep_failures: list[tuple[str, str]] = []
    for run in plan.runs:
        table_args = argparse.Namespace(**vars(args))
        table_args.landing_table = run.landing_table
        table_args.reference_table = run.source_table
        table_args.run_id = run.run_id
        table_args.fk_parent_landing = (
            args.fk_parent_landing or run.fk_parent_landing
        )
        if run.landing_table not in pin_owners:
            table_args.ddl_uri = ""  # pin describes the target only
        # Collect-then-fail (2026-08-22 launch lesson): one table's
        # preflight stop must not HIDE the remaining tables' constraint
        # reports and blockers — prep everything, abort once with all.
        try:
            specs.append(
                _prepare_table_spec(
                    table_args,
                    model_client,
                    in_set_landing=in_set,
                    registry=registry,
                )
            )
        except SystemExit as exc:
            logger.error(
                "prep failed for %s: %s", run.landing_table, exc
            )
            prep_failures.append((run.landing_table, str(exc)))
    if prep_failures:
        summary = "\n".join(f"- {t}: {e}" for t, e in prep_failures)
        raise SystemExit(
            f"{len(prep_failures)} of {len(plan.runs)} planned tables "
            f"failed driver-side preflight — nothing was launched:\n"
            f"{summary}"
        )
    # Patch each edge's parent_pk from the sibling spec so the composer
    # can skip the Distinct shuffle when ref tuple == parent PK.
    pk_by_landing = {
        s.config.landing_table: tuple(s.config.pk_columns) for s in specs
    }
    specs = [
        _dc_replace(
            s,
            parent_edges=tuple(
                _dc_replace(
                    e, parent_pk=pk_by_landing.get(e.parent_landing, ())
                )
                for e in s.parent_edges
            ),
        )
        for s in specs
    ]
    total_edges = sum(len(s.parent_edges) for s in specs)
    log_milestone(
        "relational_single_job",
        tables=len(specs),
        order=",".join(
            s.config.landing_table.rsplit(".", 1)[-1] for s in specs
        ),
        edges=total_edges,
        edges_detail=",".join(
            f"{s.config.landing_table.rsplit('.', 1)[-1]}:"
            f"{len(s.parent_edges)}"
            for s in specs
        ),
    )
    if len(specs) > 1 and total_edges == 0:
        log_milestone(
            "relational_closure_no_enforced_edges",
            level=logging.WARNING,
            tables=len(specs),
            note="the model grouped these tables but ZERO enforced "
            "in-set FK edges resolved — every FK column generates from "
            "marginals this run. Read the relationship_model card: `..>` "
            "edges are documented-only (`enforced: false`) and a table "
            "marked [DISABLED] hands out no keys. Flip the flag in "
            "config/relationships to change it.",
        )
    with beam.Pipeline(options=options) as p:
        results = build_relational_pipeline(p, specs)
        logger.info(
            "Relational pipeline launched: %d tables, run_id=%s",
            len(results), args.run_id,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
