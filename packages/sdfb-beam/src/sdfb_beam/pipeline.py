"""Main Beam DAG composer for synthetic-dataflow-bigquery.

Build the synthesis pipeline by wiring sources / sinks into an existing
`beam.Pipeline`. The caller decides DirectRunner-vs-Dataflow and local
files-vs-BigQuery I/O — `build_pipeline()` is runner-agnostic.

Layouts:
  M1 §8 (laptop):   DirectRunner + in-memory `reference_rows` +
                    `WriteToJsonLines` sinks                  (this file)
  M1 §11 (M4):      DataflowRunner + `ReadFromBigQuery` +
                    `WriteToBigQuery` sinks                   (cli.py TBD)

REFs:
  - .claude/skills/beam-dofn.md
  - .claude/skills/validation-mode-a.md
  - .claude/skills/reference-data.md
  - https://beam.apache.org/documentation/programming-guide/#additional-outputs
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any

import apache_beam as beam
from apache_beam.transforms import combiners
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext, ModelClient
from sdfb_core.rag.chunking import (
    MAX_ROW_DOC_ROWS,
    chunk_free_text_value,
    distinct_free_text_values,
)
from sdfb_core.validation import (
    STATUS_FAILED_BLOCKER,
    BlockerThresholdExceeded,
    Thresholds,
    build_run_summary,
    normalize_dlq_record,
)

from sdfb_beam.dofns import (
    EnforceUniqueness,
    GenerateRecordsDoFn,
    PanderaValidateBatchDoFn,
    ValidateRecordDoFn,
)
from sdfb_beam.dofns.pools import BuildFreeTextPoolsDoFn
from sdfb_beam.io.digest import compute_reference_digest
from sdfb_beam.rag.population import ChunkReferenceRowsDoFn, EmbedChunksDoFn


@dataclass
class PipelineConfig:
    """All non-I/O knobs for one pipeline run.

    Source / sink wiring is passed separately to `build_pipeline()` so
    that the same config can drive DirectRunner-local and Dataflow-prod
    invocations.
    """

    table_schema: TableSchema
    engine_name: str
    model_client: ModelClient
    num_rows: int
    batch_size: int = 16
    similarity: float = 0.5
    seed: int | None = None
    # Per-row-unique columns (PK/UUID) synthesized fresh each row; never
    # sampled from reference data. See engines/identity.py.
    identity_columns: tuple[str, ...] = ()
    # Declared primary-key columns; duplicate PK tuples divert to the DLQ as
    # rule_id=pk.duplicate (BLOCKER). Empty = PK not declared (rule idle).
    pk_columns: tuple[str, ...] = ()
    # Real-LLM runs re-raise on free-text LLM failure instead of silently
    # copying exemplars. Set from client_type at the CLI boundary.
    strict_freetext: bool = False
    run_id: str = "local-run"
    # Worker-local model paths surfaced to engines via GenerationContext.
    # model_uri = the LLM (also given to the ModelClient); embedder_uri =
    # B.1's embedder. Empty ⇒ the engine uses its dependency-free default.
    model_uri: str = ""
    embedder_uri: str = ""
    # §12 — Mode-A run-level gate + provenance for synthetic_data_quality.
    reference_table: str = ""
    landing_table: str = ""
    thresholds: Thresholds | None = None
    fail_on_blocker: bool = True
    # RAG layer (WS2 §4b). rag_chunks_table threads the READ path into the
    # worker ctx (self-gating on data); embedder identity pins the vector
    # space and must come from the ORIGINAL embedder URI (driver-side).
    rag_chunks_table: str = ""
    embedder_id: str = ""
    embedder_version: str = ""
    # Opt-in decode-time format constraint for identifier-ish free-text
    # pools (2026-07-25 hallucination fix, layer 2). See
    # GenerationContext.pool_pattern_guidance.
    pool_pattern_guidance: bool = False
    # Persisted free-text pools (WS5 §2). Threads the READ path into the
    # worker ctx exactly as rag_chunks_table does; the build branch is
    # gated separately by the driver passing `freetext_pools_store`.
    freetext_pools_table: str = ""
    # WS5 §3 seeding experiment: centroid | kcenter | kcenter_rotate.
    pool_seed_strategy: str = "centroid"
    # Shape-preserving expander (2026-08-05 spec C3): off | identifiers
    # (default) | all. See GenerationContext.freetext_expansion.
    freetext_expansion: str = "identifiers"
    # Attach per-column llm_prompt_constraint (from column-description
    # JSON) to pool prompts (spec C5). See GenerationContext.prompt_constraints.
    prompt_constraints: bool = True
    # off | redacted | full — log each built pool prompt as a
    # freetext_pool_prompt milestone (ADR 0024 §3c). See
    # GenerationContext.prompt_debug.
    prompt_debug: str = "off"
    # Tier-2 exact per-column distinct counts (--source_stats=exact,
    # ADR 0022) — feeds free-text pool sizing. Empty = Tier 1 only.
    source_distinct: dict = field(default_factory=dict)
    # Reference-table FQN for the worker-side ADR 0023 source-value store
    # attach (B.2 builds pools lazily in Generate workers). Empty = off.
    source_values_table: str = ""
    # FK columns → parent synthetic key values (ADR 0021), loaded
    # driver-side by io/fk_pools when the contract declares FKs.
    fk_pools: dict = field(default_factory=dict)
    # Declared FK edges as display metadata for the worker's
    # `relational_e2e` pretty log (ADR 0028 follow-up): each
    # {"cols": [...], "ref": "ds.parent", "parent_landing": fqn}.
    fk_edges: tuple = ()
    # Multi-table launches (ADR 0030): landing table NAME qualifying
    # column references in logs; empty = single-table bare names.
    log_table_prefix: str = ""
    # WS6 W3: "exact" (default, today) diverts every duplicate to the DLQ
    # behind up to three shuffle barriers; "streaming" lands rows as they
    # are generated and measures the duplicate rate instead.
    uniqueness_mode: str = "exact"


def build_pipeline(
    p: beam.Pipeline,
    *,
    reference_rows: list[dict],
    config: PipelineConfig,
    landing_sink: beam.PTransform,
    dlq_sink: beam.PTransform,
    validation_runs_sink: beam.PTransform | None = None,
    rag_chunks_sink: beam.PTransform | None = None,
    freetext_pools_store: Any = None,
    source_value_store: Any = None,
    label_prefix: str = "",
    fk_side: Any = None,
) -> dict[str, Any]:
    """Wire the synthesis DAG onto an existing Beam Pipeline.

    Returns a metadata dict with the reference digest, run id, and
    handles to the resulting PCollections (`valid`, `dlq`) for callers
    that want to attach further transforms (metrics, additional sinks).

    ``label_prefix`` namespaces every transform label so N tables can
    share ONE pipeline (ADR 0030 single-job relational mode); ``fk_side``
    is that mode's parent-keys side input (`AsSingleton` of a
    {child_col: (values…)} dict) — it defers the child's engine build to
    the first bundle (`GenerateRecordsDoFn.expect_fk_side`).
    """
    for label, cols in (
        ("identity_columns", config.identity_columns),
        ("pk_columns", config.pk_columns),
    ):
        if cols:
            valid_columns = {c.name for c in config.table_schema.columns}
            unknown = [c for c in cols if c not in valid_columns]
            if unknown:
                raise ValueError(
                    f"{label} not found on {config.table_schema.fqn}: "
                    f"{unknown}. Valid columns: {sorted(valid_columns)}"
                )

    digest = compute_reference_digest(reference_rows)
    ctx = GenerationContext(
        table_schema=config.table_schema,
        reference_rows=reference_rows,
        reference_digest=digest,
        pipeline_run_id=config.run_id,
        model_uri=config.model_uri,
        embedder_uri=config.embedder_uri,
        identity_columns=list(config.identity_columns),
        pk_columns=list(config.pk_columns),
        strict_freetext=config.strict_freetext,
        num_rows=config.num_rows,
        embedder_id=config.embedder_id,
        embedder_version=config.embedder_version,
        rag_chunks_table=config.rag_chunks_table,
        pool_pattern_guidance=config.pool_pattern_guidance,
        freetext_pools_table=config.freetext_pools_table,
        pool_seed_strategy=config.pool_seed_strategy,
        freetext_expansion=config.freetext_expansion,
        prompt_constraints=config.prompt_constraints,
        prompt_debug=config.prompt_debug,
        fk_pools=config.fk_pools,
        fk_edges=[dict(e) for e in config.fk_edges],
        landing_table=config.landing_table,
        log_table_prefix=config.log_table_prefix,
        source_distinct=config.source_distinct,
        source_values_table=config.source_values_table,
    )

    # Build batch request specs eagerly — driver-side, before the graph.
    request_specs: list[dict] = []
    remaining = config.num_rows
    batch_id = 0
    while remaining > 0:
        n = min(config.batch_size, remaining)
        request_specs.append({"batch_id": batch_id, "n": n})
        remaining -= n
        batch_id += 1

    requests = p | f"{label_prefix}CreateRequests" >> beam.Create(request_specs)

    # WS5 §2 / 2026-07-29 four-run postmortem — optional free-text pool
    # build branch. The driver decides (digest existence check) whether to
    # pass a store; None ⇒ branch absent, DAG unchanged. The branch builds
    # the pool ladder ONCE, writes `freetext_pools` itself (blocking load
    # job inside the DoFn), and its OUTPUT gates Generate below: on the
    # R1/R3 cold runs an ungated Generate raced the branch and every pool
    # was built twice concurrently on the same GPU (R3: 2x 13 ladders,
    # 2,005 s + 2,026 s of duplicated LLM time). The AsList side input is a
    # runner-level barrier — Generate bundles are not scheduled until the
    # branch (build + store write) completes, so every Generate setup's
    # store fetch hits.
    if freetext_pools_store is not None:
        pool_rows = (
            p
            | f"{label_prefix}PoolTrigger" >> beam.Create([None])
            | f"{label_prefix}BuildFreeTextPools"
            >> beam.ParDo(
                BuildFreeTextPoolsDoFn(
                    config.engine_name,
                    config.model_client,
                    ctx,
                    store=freetext_pools_store,
                    source_value_store=source_value_store,
                )
            )
        )
        requests = requests | f"{label_prefix}AwaitFreeTextPools" >> beam.Map(
            lambda spec, _pools: spec, _pools=beam.pvalue.AsList(pool_rows)
        )

    generated = (
        requests
        | f"{label_prefix}Generate" >> _generate_pardo(
            config, ctx, fk_side
        ).with_outputs("failed", main="main")
    )

    record_validated = (
        generated.main
        | f"{label_prefix}ValidateRecord" >> beam.ParDo(
            ValidateRecordDoFn(table_schema=config.table_schema)
        ).with_outputs("invalid", main="main")
    )

    batched = (
        record_validated.main
        # WS6 F3 (2026-07-27_10_42_52 E2E): 10-100-row batches meant Pandera
        # validated 1M rows as 10k-100k MICRO-DataFrames — the per-frame
        # construction + schema-compile overhead made PanderaValidate the
        # funnel inside the fused Generate->KeyByRowDigest stage (~0.88k
        # rows/s). Pandera's cost is amortized over rows in the frame, so
        # validate thousands at a time, not tens.
        | f"{label_prefix}Batch" >> beam.BatchElements(min_batch_size=1_000, max_batch_size=10_000)
    )
    batch_validated = (
        batched
        | f"{label_prefix}PanderaValidate" >> beam.ParDo(
            PanderaValidateBatchDoFn(table_schema=config.table_schema)
        ).with_outputs("invalid", main="main")
    )

    # Line 3 of defense — full-row and identity-column duplicates divert to
    # the DLQ instead of landing (first occurrence per key wins).
    uniq = batch_validated.main | f"{label_prefix}EnforceUniqueness" >> EnforceUniqueness(
        identity_columns=list(config.identity_columns),
        pk_columns=list(config.pk_columns),
        mode=config.uniqueness_mode,
    )

    # Landing sink — valid, unique records only.
    _ = uniq["unique"] | f"{label_prefix}WriteLanding" >> landing_sink

    # DLQ — flatten the four failure tags, then normalize the heterogeneous
    # envelopes into the uniform dead_letter schema before writing.
    dlq_raw = (
        (
            generated.failed,
            record_validated.invalid,
            batch_validated.invalid,
            uniq["duplicates"],
        )
        | f"{label_prefix}FlattenDLQ" >> beam.Flatten()
    )
    dlq = dlq_raw | f"{label_prefix}NormalizeDLQ" >> beam.Map(
        normalize_dlq_record, run_id=config.run_id
    )
    _ = dlq | f"{label_prefix}WriteDLQ" >> dlq_sink

    # WS2 §4b.1 — optional rag_chunks population branch. The driver decides
    # (existence check) whether to pass a sink; None ⇒ branch absent, DAG
    # unchanged (the validation_runs_sink precedent). Feeds on
    # `reference_rows` — the driver-loaded ≤10k sample whose digest is this
    # run's provenance key — NOT a full-table read; scope rationale in
    # sdfb_beam/rag/population.py.
    if rag_chunks_sink is not None:
        free_text_columns = _rag_free_text_columns(
            config.table_schema, reference_rows
        )
        # Population is scoped to what its consumers can read (ADR 0019):
        # row_doc chunks cover EXACTLY the engine's read prefix
        # (`_vectors_from_store` is all-or-nothing over rows[:1024]) — the
        # 2026-07-25 06:18 run embedded all 10k rows and 90 % could never
        # be read back. free_text_col chunks dedupe to distinct
        # (column, value), computed driver-side (reference_rows is already
        # in memory here); Beam still fans the embed itself out across
        # workers via the Reshuffle below.
        distinct_values = distinct_free_text_values(
            reference_rows, free_text_columns
        )
        row_doc_chunks = (
            p
            | f"{label_prefix}RagReferenceRows"
            >> beam.Create(reference_rows[:MAX_ROW_DOC_ROWS])
            | f"{label_prefix}RagChunkRows"
            >> beam.ParDo(
                ChunkReferenceRowsDoFn(
                    source_fqn=config.table_schema.fqn,
                    reference_digest=digest,
                    column_order=[c.name for c in config.table_schema.columns],
                    free_text_columns=[],  # value chunks come deduped below
                    pk_columns=list(config.pk_columns),
                    embedder_id=config.embedder_id,
                    embedder_version=config.embedder_version,
                )
            )
        )
        value_chunks = (
            p
            | f"{label_prefix}RagDistinctValues"
            >> beam.Create(
                [(c, v) for c, vals in distinct_values.items() for v in vals]
            )
            | f"{label_prefix}RagValueChunks"
            >> beam.MapTuple(
                functools.partial(
                    chunk_free_text_value,
                    source_fqn=config.table_schema.fqn,
                    reference_digest=digest,
                    embedder_id=config.embedder_id,
                    embedder_version=config.embedder_version,
                )
            )
        )
        chunks = (
            (row_doc_chunks, value_chunks)
            | f"{label_prefix}RagAllChunks" >> beam.Flatten()
            # Spread the (now small) chunk set across workers so the embed
            # stage keeps Beam's embarrassing parallelism.
            | f"{label_prefix}RagFanout" >> beam.Reshuffle()
            | f"{label_prefix}RagBatchChunks"
            >> beam.BatchElements(min_batch_size=32, max_batch_size=256)
            | f"{label_prefix}RagEmbedChunks" >> beam.ParDo(EmbedChunksDoFn(config.embedder_uri))
        )
        _ = chunks | f"{label_prefix}WriteRagChunks" >> rag_chunks_sink

    result: dict[str, Any] = {
        "reference_digest": digest,
        "run_id": config.run_id,
        "valid": uniq["unique"],
        "dlq": dlq,
        "generation_context": ctx,
    }

    # §12 — run-level summary row + BLOCKER gate. Only wired when a
    # validation_runs sink is supplied; happy-path DirectRunner tests that
    # don't assert run metadata omit it.
    if validation_runs_sink is not None:
        thresholds = config.thresholds or Thresholds(
            env="dev", blocker_failure_ratio=1.0
        )
        valid_count, dlq_by_rule = _gate_inputs(
            uniq, dlq_raw, config.uniqueness_mode, label_prefix
        )
        summary_rows = (
            p
            | f"{label_prefix}SummarySeed" >> beam.Create([None])
            | f"{label_prefix}BuildValidationRun" >> beam.Map(
                _build_validation_run_row,
                valid_count=beam.pvalue.AsSingleton(valid_count),
                dlq_by_rule=beam.pvalue.AsSingleton(dlq_by_rule),
                thresholds=thresholds,
                run_id=config.run_id,
                reference_digest=digest,
                num_rows=config.num_rows,
                reference_table=config.reference_table,
                landing_table=config.landing_table,
                engine=config.engine_name,
                model_uri=config.model_uri,
            )
        )
        write_result = summary_rows | f"{label_prefix}WriteValidationRun" >> validation_runs_sink
        if config.fail_on_blocker:
            gate_kwargs = {}
            load_jobs = getattr(write_result, "destination_load_jobid_pairs", None)
            if load_jobs is not None:
                # Order the gate AFTER the FILE_LOADS load jobs commit — a
                # tripped gate must fail the JOB, not suppress the FAILED
                # run's own summary row (2026-07-20 b2 run: zero
                # validation_runs trace). Non-BQ sinks (DirectRunner tests)
                # expose no WriteResult and keep the sibling wiring.
                gate_kwargs["wait_on_write"] = beam.pvalue.AsIter(load_jobs)
            _ = summary_rows | f"{label_prefix}BlockerGate" >> beam.ParDo(
                _BlockerGateDoFn(), **gate_kwargs
            )
        result["validation_run"] = summary_rows

    return result


def _generate_pardo(config: PipelineConfig, ctx, fk_side):
    """The Generate ParDo; a child table's parent-key side input rides
    as a process() kwarg and defers the engine build (ADR 0030)."""
    dofn = GenerateRecordsDoFn(
        engine_name=config.engine_name,
        model_client=config.model_client,
        ctx=ctx,
        similarity=config.similarity,
        seed=config.seed,
        expect_fk_side=fk_side is not None,
    )
    if fk_side is not None:
        return beam.ParDo(dofn, fk_side=fk_side)
    return beam.ParDo(dofn)


# In-DAG FK key-pool cap (ADR 0030) — mirrors io/fk_pools._DEFAULT_LIMIT:
# a child samples from at most this many parent key tuples; the side input
# stays a few MB even under 100M-row parents.
_FK_SIDE_SAMPLE_CAP = 100_000


@dataclass(frozen=True)
class FkEdgeSpec:
    """One enforced FK edge resolved INSIDE the job (ADR 0030): the
    child's ``child_cols`` sample from the parent's landed ``ref_cols``,
    delivered as a side input — no BQ round-trip, integrity by
    construction. ``parent_pk`` lets the composer skip the Distinct
    shuffle when the ref tuple IS the parent PK (already unique after
    EnforceUniqueness)."""

    child_cols: tuple[str, ...]
    ref_cols: tuple[str, ...]
    parent_landing: str
    parent_pk: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableSpec:
    """One table of a single-job relational launch (ADR 0030)."""

    config: PipelineConfig
    reference_rows: list[dict]
    landing_sink: beam.PTransform
    dlq_sink: beam.PTransform
    validation_runs_sink: beam.PTransform | None = None
    rag_chunks_sink: beam.PTransform | None = None
    freetext_pools_store: Any = None
    source_value_store: Any = None
    parent_edges: tuple[FkEdgeSpec, ...] = ()


def _edge_key_pools(parent_valid, edge: FkEdgeSpec, prefix: str):
    """The parent's landed key tuples for one edge → a one-element
    PCollection holding {child_col: (values…)}, aligned per column
    (composite edges keep per-column independence, the recorded v1
    semantics — joint tuple draws are the M2 follow-up)."""
    tuples = parent_valid | f"{prefix}FkTuples" >> beam.Map(
        lambda r, rc=edge.ref_cols: tuple(r[c] for c in rc)
    )
    if tuple(sorted(edge.ref_cols)) != tuple(sorted(edge.parent_pk)):
        tuples = tuples | f"{prefix}FkDistinct" >> beam.Distinct()
    sampled = (
        tuples
        | f"{prefix}FkSample"
        >> combiners.Sample.FixedSizeGlobally(_FK_SIDE_SAMPLE_CAP)
    )
    return sampled | f"{prefix}FkPools" >> beam.Map(
        lambda ts, cc=edge.child_cols: {
            c: tuple(t[i] for t in ts) for i, c in enumerate(cc)
        }
    )


def build_relational_pipeline(
    p: beam.Pipeline, specs: list[TableSpec]
) -> dict[str, dict[str, Any]]:
    """N tables, ONE pipeline (ADR 0030): each table's full subgraph
    (pools, RAG, generation, validation, DLQ, gate) label-namespaced by
    its landing table name; a child's FK columns take the parent's
    landed keys as an in-DAG side input, which is also the runner-level
    ordering barrier — children never generate before parents. One
    worker fleet, one vLLM ignition, serves every table.

    ``specs`` must arrive parents-first (`plan_launch` order). Returns
    {landing_table: build_pipeline result}."""
    valid_by_landing: dict[str, Any] = {}
    results: dict[str, dict[str, Any]] = {}
    for spec in specs:
        name = spec.config.landing_table.rsplit(".", 1)[-1]
        prefix = f"{name}/"
        side = None
        if spec.parent_edges:
            edge_pools = []
            for j, edge in enumerate(spec.parent_edges):
                parent = valid_by_landing.get(edge.parent_landing)
                if parent is None:
                    raise ValueError(
                        f"{spec.config.landing_table}: parent "
                        f"{edge.parent_landing!r} not built earlier in the "
                        f"spec list — specs must be parents-first"
                    )
                edge_pools.append(
                    _edge_key_pools(parent, edge, f"{prefix}edge{j}/")
                )
            if len(edge_pools) == 1:
                merged = edge_pools[0]
            else:
                merged = (
                    tuple(edge_pools)
                    | f"{prefix}FkEdgeFlatten" >> beam.Flatten()
                    | f"{prefix}FkMerge"
                    >> beam.CombineGlobally(
                        lambda dicts: {
                            k: v for d in dicts for k, v in d.items()
                        }
                    )
                )
            side = beam.pvalue.AsSingleton(merged)
        results[spec.config.landing_table] = build_pipeline(
            p,
            reference_rows=spec.reference_rows,
            config=spec.config,
            landing_sink=spec.landing_sink,
            dlq_sink=spec.dlq_sink,
            validation_runs_sink=spec.validation_runs_sink,
            rag_chunks_sink=spec.rag_chunks_sink,
            freetext_pools_store=spec.freetext_pools_store,
            source_value_store=spec.source_value_store,
            label_prefix=prefix,
            fk_side=side,
        )
        valid_by_landing[spec.config.landing_table] = results[
            spec.config.landing_table
        ]["valid"]
    return results


def _rag_free_text_columns(
    table_schema: TableSchema, reference_rows: list[dict]
) -> list[str]:
    """Columns that get `free_text_col` chunks — B.1's own FREE_TEXT
    classification, minus identifier-shaped ones (retrieval-worthy prose,
    not per-row IDs)."""
    from sdfb_core.engines.b1_rag.profile import ColumnKind, profile_columns

    profiles = profile_columns(table_schema, reference_rows)
    return [
        p.name
        for p in profiles.values()
        if p.kind is ColumnKind.FREE_TEXT and p.identifier_shape is None
    ]


def _gate_inputs(
    uniq: dict, dlq_raw, uniqueness_mode: str, label_prefix: str = ""
):
    """`(valid_count, dlq_by_rule)` singletons for the BLOCKER gate.

    `build_run_summary` computes ``total = valid_count + dlq_count``. In
    STREAMING mode duplicates land instead of diverting, so counting landed
    rows would push `total` above the rows actually generated and quietly
    dilute the blocker ratio — a silently weaker gate. `EnforceUniqueness`
    publishes `distinct_count` for exactly this reason: distinct + excess is
    the number of rows generated, so the arithmetic is identical in both
    modes.
    """
    if uniqueness_mode == "streaming":
        valid_count = uniq["distinct_count"]
    else:
        valid_count = (
            uniq["unique"]
            | f"{label_prefix}CountValid" >> beam.combiners.Count.Globally()
        )
    dlq_by_rule = (
        (
            dlq_raw
            | f"{label_prefix}DlqRulePairs" >> beam.Map(_dlq_rule_weight),
            # Streaming reports duplicates as measured counts rather than
            # diverted envelopes; the gate folds them identically.
            uniq["rule_counts"],
        )
        | f"{label_prefix}AllRulePairs" >> beam.Flatten()
        | f"{label_prefix}DlqRuleCounts" >> beam.CombinePerKey(sum)
        | f"{label_prefix}DlqRuleDict" >> beam.combiners.ToDict()
    )
    return valid_count, dlq_by_rule


def _dlq_rule_weight(envelope: dict) -> tuple[str, int]:
    """Map a DLQ envelope to a ``(rule_id, weight)`` pair for the BLOCKER
    gate's per-rule counts.

    Every rule counts 1 envelope = 1 lost row, EXCEPT ``engine_failure``:
    one such envelope represents a whole crashed batch (`raw_request` is
    the batch request dict ``{"batch_id": ..., "n": ...}`` — see
    `GenerateRecordsDoFn`'s ``failed`` tagged output), so it must weight by
    the batch's row count or a half-failed run scores a misleadingly low
    observed_blocker_ratio and wrongly PASSES. Module-level (not a
    lambda) to stay picklable for the Dataflow worker harness.
    """
    rule_id = envelope.get("rule_id", "unknown")
    if rule_id == "engine_failure":
        try:
            return (rule_id, max(1, int(envelope.get("raw_request", {}).get("n", 1))))
        except (TypeError, ValueError):
            return (rule_id, 1)
    return (rule_id, 1)


def _build_validation_run_row(
    _seed,
    *,
    valid_count: int,
    dlq_by_rule: dict[str, int],
    thresholds: Thresholds,
    run_id: str,
    reference_digest: str,
    num_rows: int,
    reference_table: str,
    landing_table: str,
    engine: str,
    model_uri: str,
) -> dict:
    """Driver of the single validation_runs row (side inputs are singletons)."""
    summary = build_run_summary(
        run_id=run_id,
        reference_digest=reference_digest,
        valid_count=valid_count,
        dlq_by_rule=dlq_by_rule,
        thresholds=thresholds,
        num_rows_requested=num_rows,
        reference_table=reference_table,
        landing_table=landing_table,
        engine=engine,
        model_uri=model_uri,
    )
    return summary.to_bq_row()


class _BlockerGateDoFn(beam.DoFn):
    """Fails the Dataflow job when the run summary tripped the BLOCKER gate.

    ``wait_on_write`` is an optional ``AsIter`` side input over the
    upstream sink's FILE_LOADS ``destination_load_jobid_pairs`` (see
    `build_pipeline`). It is never read in the body — its only purpose is
    the Dataflow-graph ordering edge it creates, forcing this DoFn's stage
    to run after the load jobs commit. Without it, a tripped gate tears
    the job down concurrently with (and can race ahead of) the
    `validation_runs` FILE_LOADS write, so a FAILED run's own summary row
    never lands — exactly what happened on the 2026-07-20 b2 E2E run,
    which left zero trace in `synthetic_data_quality.validation_runs`.
    """

    def process(self, row: dict, wait_on_write=None):
        if row.get("status") == STATUS_FAILED_BLOCKER:
            raise BlockerThresholdExceeded(
                f"run_id={row.get('run_id')} blocker_count={row.get('blocker_count')} "
                f"observed={row.get('observed_blocker_ratio')} > "
                f"gate={row.get('blocker_failure_ratio')} (env={row.get('env')})"
            )
        yield row
