"""Per-record Pydantic validation — line 1 of defense in Mode A.

Cheap, runs on every record. Invalid records are routed to the
`invalid` tagged output with full structured error context for the DLQ.

REFs:
  - https://docs.pydantic.dev/latest/errors/validation_errors/
  - .claude/skills/validation-mode-a.md
"""

from __future__ import annotations

import apache_beam as beam
from apache_beam.metrics import Metrics
from pydantic import ValidationError

from sdfb_core.codegen import derive_record_model
from sdfb_core.contracts import TableSchema


class ValidateRecordDoFn(beam.DoFn):
    """Validate each record dict against the derived Pydantic model."""

    def __init__(self, table_schema: TableSchema) -> None:
        super().__init__()
        self.table_schema = table_schema
        self._record_model = None  # built in setup()

        self._valid = Metrics.counter("validation", "pydantic_valid")
        self._invalid = Metrics.counter("validation", "pydantic_invalid")

    def setup(self):
        self._record_model = derive_record_model(self.table_schema)

    def process(self, record):
        try:
            self._record_model.model_validate(record)  # type: ignore[union-attr]
            self._valid.inc()
            yield record
        except ValidationError as e:
            self._invalid.inc()
            yield beam.pvalue.TaggedOutput(
                "invalid",
                {
                    "raw_record": record,
                    "error_type": "pydantic",
                    "error_detail": e.errors(),
                    "rule_id": "schema.types",
                    "stage": "pre_write",
                },
            )
