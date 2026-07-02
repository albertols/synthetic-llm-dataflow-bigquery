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

from dataclasses import dataclass
from typing import Any

import apache_beam as beam
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext, ModelClient
from sdfb_core.validation import (
    STATUS_FAILED_BLOCKER,
    BlockerThresholdExceeded,
    Thresholds,
    build_run_summary,
    normalize_dlq_record,
)

from sdfb_beam.dofns import (
    GenerateRecordsDoFn,
    PanderaValidateBatchDoFn,
    ValidateRecordDoFn,
)
from sdfb_beam.io.digest import compute_reference_digest


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


def build_pipeline(
    p: beam.Pipeline,
    *,
    reference_rows: list[dict],
    config: PipelineConfig,
    landing_sink: beam.PTransform,
    dlq_sink: beam.PTransform,
    validation_runs_sink: beam.PTransform | None = None,
) -> dict[str, Any]:
    """Wire the synthesis DAG onto an existing Beam Pipeline.

    Returns a metadata dict with the reference digest, run id, and
    handles to the resulting PCollections (`valid`, `dlq`) for callers
    that want to attach further transforms (metrics, additional sinks).
    """
    digest = compute_reference_digest(reference_rows)
    ctx = GenerationContext(
        table_schema=config.table_schema,
        reference_rows=reference_rows,
        reference_digest=digest,
        pipeline_run_id=config.run_id,
        model_uri=config.model_uri,
        embedder_uri=config.embedder_uri,
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

    # Landing sink — valid records only.
    _ = batch_validated.main | "WriteLanding" >> landing_sink

    # DLQ — flatten the three failure tags, then normalize the heterogeneous
    # envelopes into the uniform dead_letter schema before writing.
    dlq_raw = (
        (generated.failed, record_validated.invalid, batch_validated.invalid)
        | "FlattenDLQ" >> beam.Flatten()
    )
    dlq = dlq_raw | "NormalizeDLQ" >> beam.Map(
        normalize_dlq_record, run_id=config.run_id
    )
    _ = dlq | "WriteDLQ" >> dlq_sink

    result: dict[str, Any] = {
        "reference_digest": digest,
        "run_id": config.run_id,
        "valid": batch_validated.main,
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
            batch_validated.main | "CountValid" >> beam.combiners.Count.Globally()
        )
        dlq_by_rule = (
            dlq_raw
            | "DlqRulePairs" >> beam.Map(lambda d: (d.get("rule_id", "unknown"), 1))
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

    return result


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
