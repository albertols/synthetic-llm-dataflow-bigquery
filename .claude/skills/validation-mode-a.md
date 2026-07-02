---
name: validation-mode-a
description: Recipe for the in-pipeline (Mode A) validation stack — three lines of defense (Pydantic per-record, Pandera per-batch, BQ FailedRows), whylogs profile merge, DLQ schema, fail-fast thresholds. Load when working on `sdfb_beam/dofns/validate_*`, `pandera_*`, `whylogs_*`, or the DLQ write.
---

# Skill — Mode A in-pipeline validation

Mode A runs INSIDE the Beam DAG, BEFORE `WriteToBigQuery`. There are three lines of defense plus one safety net.

## Line 1 — per-record Pydantic

`ValidateRecordDoFn` (see `beam-dofn.md`). Tags `invalid` for any Pydantic `ValidationError`. Cheap, runs on every record.

REF: https://docs.pydantic.dev/latest/

## Line 2 — per-batch Pandera

`PanderaValidateBatchDoFn`. Batches built by `beam.BatchElements(min_batch_size=1000, max_batch_size=5000)`. The Pandera schema is **derived from the same Pydantic model** via `sdfb_core/codegen/derive_pandera.py` — one source of truth, no drift.

```python
self.schema.validate(df, lazy=True)  # collect-all-failures
```

Failing rows split to DLQ with `error_type="pandera"`; passing rows continue.

REFs: https://pandera.readthedocs.io/en/stable/ · https://pandera.readthedocs.io/en/stable/lazy_validation.html

## Line 3 — BigQueryIO `FailedRows`

`WriteToBigQuery(...).failed_rows` catches anything that survives lines 1–2 but the BQ load job rejects (schema mismatch, partition errors, malformed values).

Flatten line-3 failures into the same DLQ table — `error_type="bq_load"`, `rule_id="schema.bq_reject"`.

REF: https://beam.apache.org/documentation/patterns/bigqueryio/

## Profile (parallel, non-blocking)

`WhylogsProfileDoFn` builds a mergeable profile per worker. `CombineGlobally(MergeProfilesFn())` merges them at the end of the job.

The CombineFn **MUST** be commutative & associative (§12 of the user spec). Use `whylogs.ResultSet.merge()` which guarantees both:

```python
class MergeProfilesFn(beam.CombineFn):
    def create_accumulator(self): return why.log({}).profile()
    def add_input(self, acc, x):  return acc.merge(why.log(x).profile())
    def merge_accumulators(self, accs):
        out = self.create_accumulator()
        for a in accs: out = out.merge(a)
        return out
    def extract_output(self, acc): return acc
```

REF: https://whylogs.readthedocs.io/en/latest/examples/integrations/Apache_Beam.html

## DLQ table schema (`{project}.synthetic_data_quality.dead_letter`)

Partitioned by DAY on `dlq_inserted_at`, clustered on `(error_type, rule_id)`.

| Column | Type | Notes |
|---|---|---|
| `dlq_inserted_at` | TIMESTAMP | partition key |
| `run_id` | STRING | links to `validation_runs` |
| `raw_record` | JSON | the rejected payload |
| `error_type` | STRING | `pydantic` / `pandera` / `bq_load` / `engine` |
| `error_detail` | JSON | structured error |
| `rule_id` | STRING | from the check catalog (§6 user spec) |
| `pipeline_step` | STRING | which DoFn produced the rejection |
| `stage` | STRING | always `pre_write` in Mode A |

## Failing the job (BLOCKER gate)

If `invalid_count / total > threshold_blocker` (from `config/thresholds.yml`), raise `BlockerThresholdExceeded` — Dataflow marks the job FAILED.

Severity < BLOCKER does NOT fail Mode A. Those propagate to Mode B (M2) for CI gating.

## Mode B is M2 — out of scope here

Anything GX / Soda / SDMetrics / Evidently is M2. Do not add it to Mode A files. If a request reaches for those, push back and confirm scope first.

## Current implementation

| Line of defense | File | Tests | Status |
|---|---|---|---|
| Per-record Pydantic | `packages/sdfb-beam/src/sdfb_beam/dofns/validate_record.py` | `tests/unit/dofns/test_validate_record.py` | ✅ |
| Per-batch Pandera | `packages/sdfb-beam/src/sdfb_beam/dofns/pandera_batch.py` | `tests/unit/dofns/test_pandera_batch.py` | ✅ |
| `WhylogsProfileDoFn` + `MergeProfilesFn` | — | — | 🔒 M1 §11 |
| BigQueryIO `FailedRows` safety net | `packages/sdfb-beam/src/sdfb_beam/pipeline.py` | (DirectRunner uses JSONL sinks; real BQ wiring in M1 §11) | 🟡 partial |

Thresholds — env-scoped severity gates per rule_id: `config/thresholds.yml`. Loaded at job start by `pipeline.build_pipeline()` (wiring in M1 §12).

## References

- Pydantic v2: https://docs.pydantic.dev/latest/
- Pandera: https://pandera.readthedocs.io/en/stable/
- Pandera lazy validation: https://pandera.readthedocs.io/en/stable/lazy_validation.html
- whylogs Beam integration: https://whylogs.readthedocs.io/en/latest/examples/integrations/Apache_Beam.html
- whylogs core (mergeable profiles): https://github.com/whylabs/whylogs
- Beam DLQ pattern: https://beam.apache.org/documentation/patterns/bigqueryio/
- Beam metrics: https://beam.apache.org/documentation/programming-guide/#metrics