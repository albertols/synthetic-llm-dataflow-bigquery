#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""Per-batch Pandera validation — line 2 of defense in Mode A.

Converts a micro-batch of record dicts into a pandas DataFrame and runs
the Pandera schema with `lazy=True` (collect-all-failures). Failing
rows are split out via the `invalid` tagged output; passing rows
continue downstream.

Two rules. A failure Pandera ties to a row (its failure case has an
index) sends that row to the DLQ as `schema.batch`. A failure with no
index is the column's, not a row's (a dtype the column cannot be coerced
to, a missing or unexpected column): no row of the batch passed, so the
whole batch goes to the DLQ as `schema.column`, a BLOCKER in
config/thresholds.yml, and every row's detail names the column's
failures. The validated frame is thrown away: passing rows continue as
the dicts they came in as.

REFs:
  - https://pandera.readthedocs.io/en/stable/lazy_validation.html
  - .claude/skills/validation-mode-a.md
"""

from __future__ import annotations

import apache_beam as beam
import pandas as pd
import pandera.errors as pa_err
from apache_beam.metrics import Metrics
from sdfb_core.contracts import TableSchema

from sdfb_beam.codegen import derive_pandera_schema

# The DLQ rule ids (config/thresholds.yml): a failure tied to a row, and
# one of the whole column.
RULE_ROW = "schema.batch"
RULE_COLUMN = "schema.column"
# How many failure cases a row's DLQ detail carries, of each kind.
_DETAIL_CASES = 5


def _cases(failure_cases: pd.DataFrame) -> list[dict]:
  """The first failure cases as JSON-safe records: each value as text, a
  missing one as None (pandas 3 keeps NaN through `astype(str)`, and a
  NaN is no JSON)."""
  return [{
      key: None if pd.isna(value) else str(value)
      for key, value in record.items()
  }
          for record in failure_cases.head(_DETAIL_CASES).to_dict("records")]


class PanderaValidateBatchDoFn(beam.DoFn):
  """Validate micro-batches with Pandera; split DLQ via tagged output."""

  def __init__(self, table_schema: TableSchema) -> None:
    super().__init__()
    self.table_schema = table_schema
    self._schema = None  # built in setup()

    self._valid = Metrics.counter("validation", "pandera_valid")
    self._invalid = Metrics.counter("validation", "pandera_invalid")

  def setup(self):
    self._schema = derive_pandera_schema(self.table_schema)

  # pylint: disable-next=arguments-renamed  # Beam passes the element positionally
  def process(self, batch):
    if not batch:
      return

    df = pd.DataFrame(batch)
    try:
      self._schema.validate(df, lazy=True)  # type: ignore[union-attr]
      for record in batch:
        self._valid.inc()
        yield record
      return
    except pa_err.SchemaErrors as e:
      column_level = self._has_column_failures(e)
      failed_indices = (
          set(range(len(batch)))
          if column_level else self._extract_failed_indices(e, len(batch)))
      rule_id = RULE_COLUMN if column_level else RULE_ROW
      for i, record in enumerate(batch):
        if i in failed_indices:
          self._invalid.inc()
          yield beam.pvalue.TaggedOutput(
              "invalid",
              {
                  "raw_record": record,
                  "error_type": "pandera",
                  "error_detail": self._summarize_for_row(e, i),
                  "rule_id": rule_id,
                  "stage": "pre_write",
              },
          )
        else:
          self._valid.inc()
          yield record

  @staticmethod
  def _has_column_failures(errors: pa_err.SchemaErrors) -> bool:
    """Whether a failure case has no row index: the column's own."""
    try:
      fc = errors.failure_cases
      return (not fc.empty) and ("index" not in fc.columns or
                                 bool(fc["index"].isna().any()))
    except Exception:  # pylint: disable=broad-exception-caught
      return True  # unreadable failure cases: no row can be trusted

  @staticmethod
  def _extract_failed_indices(errors: pa_err.SchemaErrors,
                              batch_size: int) -> set[int]:
    """Best-effort: map Pandera failures back to batch row indices.

        Falls back to "whole batch invalid" if indices can't be teased out
        of the failure-cases DataFrame (which happens for schema-level
        failures like missing columns or wrong dtype on the whole series).
        """
    try:
      fc = errors.failure_cases
      if fc.empty:
        return set()
      if "index" not in fc.columns:
        return set(range(batch_size))
      indices = fc["index"].dropna()
      if indices.empty:
        return set(range(batch_size))
      out: set[int] = set()
      for v in indices.tolist():
        try:
          out.add(int(v))
        except (TypeError, ValueError):
          continue
      return out if out else set(range(batch_size))
    except Exception:  # pylint: disable=broad-exception-caught
      return set(range(batch_size))

  @staticmethod
  def _summarize_for_row(errors: pa_err.SchemaErrors, idx: int) -> dict:
    """Compact, JSON-friendly error summary for one row: its own failure
    cases and the column-level ones (no index), which every row of the
    batch shares, each with its schema_context, column and check."""
    try:
      fc = errors.failure_cases
      if "index" in fc.columns:
        own = fc[fc["index"] == idx]
        shared = fc[fc["index"].isna()]
      else:
        own, shared = fc.iloc[0:0], fc
      cases = _cases(own) + _cases(shared)
      if not cases:
        return {"raw": str(errors)[:500]}
      return {
          "failure_count": len(own) + len(shared),
          "column_failure_count": len(shared),
          "first_failures": cases,
      }
    except Exception:  # pylint: disable=broad-exception-caught
      return {"raw": str(errors)[:500]}
