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

import json
from dataclasses import dataclass
from typing import Any

import apache_beam as beam
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext, ModelClient
from sdfb_core.evaluation.gate import raise_if_blocker
from sdfb_core.evaluation.profile import choose_stratification_column
from sdfb_core.evaluation.sampling import per_stratum_cap
from sdfb_core.validation import (
    STATUS_FAILED_BLOCKER,
    BlockerThresholdExceeded,
    Thresholds,
    build_run_summary,
    normalize_dlq_record,
)

from sdfb_beam.dofns import (
    EnforceUniqueness,
    EvaluationDoFn,
    GenerateRecordsDoFn,
    PanderaValidateBatchDoFn,
    StratifiedReservoirFn,
    ValidateRecordDoFn,
)
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
    # WS3 — post-WriteLanding evaluation branch. enable_evaluation and the
    # sink are independent (both required for the branch); execution_id is
    # the append-only natural key (a run_id may be re-evaluated).
    enable_evaluation: bool = False
    execution_id: str = ""
    validation_runs_table: str = ""
    validation_data_history_table: str = ""


def build_pipeline(  # noqa: PLR0915 — one linear DAG-composition pass across
    # the four sibling optional branches (rag_chunks, validation_runs,
    # validation_data_history); splitting would scatter the "same
    # Create([None]) + AsSingleton collapse" shape they share across
    # helpers that would each need the same digest/uniq/config state.
    p: beam.Pipeline,
    *,
    reference_rows: list[dict],
    config: PipelineConfig,
    landing_sink: beam.PTransform,
    dlq_sink: beam.PTransform,
    validation_runs_sink: beam.PTransform | None = None,
    rag_chunks_sink: beam.PTransform | None = None,
    validation_data_history_sink: beam.PTransform | None = None,
) -> dict[str, Any]:
    """Wire the synthesis DAG onto an existing Beam Pipeline.

    Returns a metadata dict with the reference digest, run id, and
    handles to the resulting PCollections (`valid`, `dlq`) for callers
    that want to attach further transforms (metrics, additional sinks).
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
        strict_freetext=config.strict_freetext,
        num_rows=config.num_rows,
        embedder_id=config.embedder_id,
        embedder_version=config.embedder_version,
        rag_chunks_table=config.rag_chunks_table,
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

    requests = p | "CreateRequests" >> beam.Create(request_specs)

    generated = (
        requests
        | "Generate" >> beam.ParDo(
            GenerateRecordsDoFn(
                engine_name=config.engine_name,
                model_client=config.model_client,
                ctx=ctx,
                similarity=config.similarity,
                seed=config.seed,
            )
        ).with_outputs("failed", main="main")
    )

    record_validated = (
        generated.main
        | "ValidateRecord" >> beam.ParDo(
            ValidateRecordDoFn(table_schema=config.table_schema)
        ).with_outputs("invalid", main="main")
    )

    batched = (
        record_validated.main
        | "Batch" >> beam.BatchElements(min_batch_size=10, max_batch_size=100)
    )
    batch_validated = (
        batched
        | "PanderaValidate" >> beam.ParDo(
            PanderaValidateBatchDoFn(table_schema=config.table_schema)
        ).with_outputs("invalid", main="main")
    )

    # Line 3 of defense — full-row and identity-column duplicates divert to
    # the DLQ instead of landing (first occurrence per key wins).
    uniq = batch_validated.main | "EnforceUniqueness" >> EnforceUniqueness(
        identity_columns=list(config.identity_columns),
        pk_columns=list(config.pk_columns),
    )

    # Landing sink — valid, unique records only.
    _ = uniq["unique"] | "WriteLanding" >> landing_sink

    # DLQ — flatten the four failure tags, then normalize the heterogeneous
    # envelopes into the uniform dead_letter schema before writing.
    dlq_raw = (
        (
            generated.failed,
            record_validated.invalid,
            batch_validated.invalid,
            uniq["duplicates"],
        )
        | "FlattenDLQ" >> beam.Flatten()
    )
    dlq = dlq_raw | "NormalizeDLQ" >> beam.Map(
        normalize_dlq_record, run_id=config.run_id
    )
    _ = dlq | "WriteDLQ" >> dlq_sink

    # WS2 §4b.1 — optional rag_chunks population branch. The driver decides
    # (existence check) whether to pass a sink; None ⇒ branch absent, DAG
    # unchanged (the validation_runs_sink precedent).
    if rag_chunks_sink is not None:
        free_text_columns = _rag_free_text_columns(
            config.table_schema, reference_rows
        )
        chunks = (
            p
            | "RagReferenceRows" >> beam.Create(reference_rows)
            | "RagChunkRows"
            >> beam.ParDo(
                ChunkReferenceRowsDoFn(
                    source_fqn=config.table_schema.fqn,
                    reference_digest=digest,
                    column_order=[c.name for c in config.table_schema.columns],
                    free_text_columns=free_text_columns,
                    pk_columns=list(config.pk_columns),
                    embedder_id=config.embedder_id,
                    embedder_version=config.embedder_version,
                )
            )
            | "RagBatchChunks"
            >> beam.BatchElements(min_batch_size=32, max_batch_size=256)
            | "RagEmbedChunks" >> beam.ParDo(EmbedChunksDoFn(config.embedder_uri))
        )
        _ = chunks | "WriteRagChunks" >> rag_chunks_sink

    result: dict[str, Any] = {
        "reference_digest": digest,
        "run_id": config.run_id,
        "valid": uniq["unique"],
        "dlq": dlq,
    }

    # §12 — run-level summary row + BLOCKER gate. Only wired when a
    # validation_runs sink is supplied; happy-path DirectRunner tests that
    # don't assert run metadata omit it.
    if validation_runs_sink is not None:
        thresholds = config.thresholds or Thresholds(
            env="dev", blocker_failure_ratio=1.0
        )
        valid_count = (
            uniq["unique"] | "CountValid" >> beam.combiners.Count.Globally()
        )
        dlq_by_rule = (
            dlq_raw
            | "DlqRulePairs" >> beam.Map(_dlq_rule_weight)
            | "DlqRuleCounts" >> beam.CombinePerKey(sum)
            | "DlqRuleDict" >> beam.combiners.ToDict()
        )
        summary_rows = (
            p
            | "SummarySeed" >> beam.Create([None])
            | "BuildValidationRun" >> beam.Map(
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
        _ = summary_rows | "WriteValidationRun" >> validation_runs_sink
        if config.fail_on_blocker:
            _ = summary_rows | "BlockerGate" >> beam.ParDo(_BlockerGateDoFn())
        result["validation_run"] = summary_rows

    # WS3 — post-WriteLanding evaluation branch. Sibling of the
    # validation_runs block: same Create([None]) + AsSingleton collapse.
    if config.enable_evaluation and validation_data_history_sink is not None:
        eval_thresholds = config.thresholds or Thresholds(
            env="dev", blocker_failure_ratio=1.0
        )
        plan = choose_stratification_column(config.table_schema, reference_rows)
        cap = per_stratum_cap(max(len(plan.values), 1))
        real_sample = (
            p
            | "EvalReferenceRows" >> beam.Create(reference_rows)
            | "EvalSampleReference"
            >> beam.CombineGlobally(StratifiedReservoirFn(plan, config.run_id, cap))
        )
        synth_sample = uniq["unique"] | "EvalSampleSynthetic" >> beam.CombineGlobally(
            StratifiedReservoirFn(plan, config.run_id, cap)
        )
        eval_rows = (
            p
            | "EvalSeed" >> beam.Create([None])
            | "Evaluate"
            >> beam.ParDo(
                EvaluationDoFn(
                    table_schema=config.table_schema,
                    run_id=config.run_id,
                    execution_id=config.execution_id or config.run_id,
                    engine=config.engine_name,
                    engine_version=_engine_version(config.engine_name),
                    feature_flag_tags=_build_feature_flag_tags(config),
                    thresholds=eval_thresholds,
                    free_text_columns=_rag_free_text_columns(
                        config.table_schema, reference_rows
                    ),
                    num_rows=config.num_rows,
                    landing_table=config.landing_table,
                    history_table=config.validation_data_history_table,
                    validation_runs_table=config.validation_runs_table,
                ),
                real_sample=beam.pvalue.AsSingleton(real_sample),
                synth_sample=beam.pvalue.AsSingleton(synth_sample),
            )
        )
        _ = eval_rows | "WriteValidationDataHistory" >> validation_data_history_sink
        if config.fail_on_blocker:
            _ = eval_rows | "MemorizationGate" >> beam.ParDo(_MemorizationGateDoFn())
        result["validation_data_history"] = eval_rows

    return result


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


def _build_feature_flag_tags(config: PipelineConfig) -> list[str]:
    """Sorted, human-diffable run-configuration tags for
    validation_data_history.feature_flag_tags — two identical configs
    produce byte-identical arrays (queryable via IN UNNEST)."""
    tags = [
        f"engine:{config.engine_name}",
        f"similarity:{config.similarity:.2f}",
        f"strict_freetext:{str(config.strict_freetext).lower()}",
    ]
    if config.identity_columns:
        tags.append("identity_columns:" + ",".join(config.identity_columns))
    if config.embedder_id:
        tags.append(f"embedder:{config.embedder_id}-{config.embedder_version}")
    if config.rag_chunks_table:
        tags.append("rag_read_path:on")
    return sorted(tags)


def _engine_version(engine_name: str) -> str:
    from sdfb_core.engines import ENGINE_REGISTRY

    return getattr(ENGINE_REGISTRY.get(engine_name), "version", "unknown")


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
    """Fails the Dataflow job when the run summary tripped the BLOCKER gate."""

    def process(self, row: dict):
        if row.get("status") == STATUS_FAILED_BLOCKER:
            raise BlockerThresholdExceeded(
                f"run_id={row.get('run_id')} blocker_count={row.get('blocker_count')} "
                f"observed={row.get('observed_blocker_ratio')} > "
                f"gate={row.get('blocker_failure_ratio')} (env={row.get('env')})"
            )
        yield row


class _MemorizationGateDoFn(beam.DoFn):
    """Fails the job when the eval row's memorization gate tripped at BLOCKER
    severity (§5a). Wired as a sibling of the sink write — on Dataflow, stage
    fusion means a tripped gate can fail the bundle before the FILE_LOADS
    write commits, so the eval row is NOT guaranteed to land on a gate-trip
    (same latent caveat as _BlockerGateDoFn). Follow-up: sequence both gates
    on the write result and verify landing on the first real M4 gate-trip."""

    def process(self, row: dict):
        raw = json.loads(row.get("raw_metrics_json") or "{}")
        raise_if_blocker(
            raw.get("memorization_gate") or {}, run_id=str(row.get("run_id"))
        )
        yield row
