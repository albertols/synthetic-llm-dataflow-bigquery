---
name: beam-dofn
description: Recipe for writing and testing Beam DoFns in this project — lifecycle hooks, tagged outputs, side inputs, DLQ pattern, metrics, and the testing pattern with `TestPipeline`. Load when authoring or modifying anything in `sdfb_beam/dofns/`.
---

# Skill — writing a Beam DoFn

## Lifecycle hooks — when to use which

| Hook | Called | Use for |
|---|---|---|
| `__init__` | Pipeline build time (driver) | Args only. Must be picklable. **No model loading.** |
| `setup` | Once per worker | Loading models, embedding indexes, BQ clients. Heavy init. |
| `start_bundle` | Once per bundle | Light per-bundle state. Usually a no-op. |
| `process` | Per element | Pure per-element work. **NO heavy init here.** |
| `finish_bundle` | Once per bundle | Flushing buffers. |
| `teardown` | Once per worker | Releasing GPU memory, closing connections. |

**Anti-pattern:** building the LLM client in `process()`. Always `setup()`.

REF: https://beam.apache.org/documentation/programming-guide/#parallel-processing

## Tagged outputs for DLQ

```python
class ValidateRecordDoFn(beam.DoFn):
    def process(self, record):
        try:
            yield GeneratedRecord.model_validate(record)
        except ValidationError as e:
            yield beam.pvalue.TaggedOutput("invalid", {
                "raw_record": record,
                "error_type": "pydantic",
                "error_detail": e.errors(),
                "rule_id": "schema.types",
                "stage": "pre_write",
            })
```

Then in the pipeline:
```python
result = pcoll | beam.ParDo(ValidateRecordDoFn()).with_outputs("invalid", main="main")
valid, invalid = result.main, result.invalid
```

Three tagged streams in this project: `invalid` (validation failures), `failed` (engine generation failures), `dropped` (deduplication on PK). All flatten into the DLQ table.

REF: https://beam.apache.org/documentation/programming-guide/#additional-outputs

## Side inputs

The DAG uses two side inputs across all generation DoFns:
- `ddl_side`: `beam.pvalue.AsSingleton(ddl_pcoll)` — single dict with the parsed `_ddl.json`.
- `ref_side`: `beam.pvalue.AsList(reference_rows_pcoll)` — full reference sample materialized per worker.

Treat side inputs as broadcast read-only. They're loaded into worker memory once.

## Metrics

```python
from apache_beam.metrics import Metrics

class GenerateBatchDoFn(beam.DoFn):
    def __init__(self, ...):
        self.valid_counter = Metrics.counter("generation", "valid")
        self.invalid_counter = Metrics.counter("generation", "invalid")
        self.latency_dist = Metrics.distribution("generation", "batch_latency_ms")
```

These surface in Cloud Monitoring with no extra wiring.

REF: https://beam.apache.org/documentation/programming-guide/#metrics

## Testing pattern

For every DoFn:
1. **Direct call test** — invoke `process()` directly with a fake element:
   ```python
   def test_validate_record_rejects_bad_type():
       out = list(ValidateRecordDoFn().process({"id": "not-an-int"}))
       assert all(isinstance(x, beam.pvalue.TaggedOutput) for x in out)
   ```
2. **TestPipeline test** — full DAG with `assert_that`:
   ```python
   from apache_beam.testing.test_pipeline import TestPipeline
   from apache_beam.testing.util import assert_that, equal_to

   with TestPipeline() as p:
       out = (p | beam.Create([...]) | beam.ParDo(MyDoFn()))
       assert_that(out, equal_to([...]))
   ```

Mark integration tests `@pytest.mark.integration`. They run with `DirectRunner` on the laptop.

## Current implementation

| DoFn | File | Tests |
|---|---|---|
| `GenerateRecordsDoFn` | `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` | covered via integration test |
| `ValidateRecordDoFn` | `packages/sdfb-beam/src/sdfb_beam/dofns/validate_record.py` | `tests/unit/dofns/test_validate_record.py` |
| `PanderaValidateBatchDoFn` | `packages/sdfb-beam/src/sdfb_beam/dofns/pandera_batch.py` | `tests/unit/dofns/test_pandera_batch.py` |
| `WhylogsProfileDoFn` + `MergeProfilesFn` | TBD | 🔒 deferred to M1 §11 |

Pipeline composition: `packages/sdfb-beam/src/sdfb_beam/pipeline.py`. End-to-end DirectRunner test: `packages/sdfb-tests/tests/integration/test_pipeline_end_to_end.py`.

## References

- Beam programming guide: https://beam.apache.org/documentation/programming-guide/
- Tagged outputs: https://beam.apache.org/documentation/programming-guide/#additional-outputs
- DLQ pattern: https://beam.apache.org/documentation/patterns/bigqueryio/
- Metrics: https://beam.apache.org/documentation/programming-guide/#metrics
- Testing: https://beam.apache.org/documentation/pipelines/test-your-pipeline/