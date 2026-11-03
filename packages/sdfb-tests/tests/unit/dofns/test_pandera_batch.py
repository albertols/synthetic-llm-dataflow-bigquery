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
"""Unit tests for `PanderaValidateBatchDoFn`. Covers M1 §8 acceptance #2
at the per-batch line of defense — specifically duplicate-PK detection
which is structurally impossible to catch at the per-record stage."""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=use-implicit-booleaness-not-comparison

from __future__ import annotations

import datetime as dt
import json

import apache_beam as beam
import pytest
from sdfb_beam.dofns.pandera_batch import PanderaValidateBatchDoFn
from sdfb_core.contracts.schema import FieldSchema, TableInfo, TableSchema


def test_passes_valid_batch(customers_schema, customers_reference):
  do_fn = PanderaValidateBatchDoFn(table_schema=customers_schema)
  do_fn.setup()
  out = list(do_fn.process(customers_reference[:5]))
  assert len(out) == 5
  assert not any(isinstance(x, beam.pvalue.TaggedOutput) for x in out)


def test_empty_batch_is_noop(customers_schema):
  do_fn = PanderaValidateBatchDoFn(table_schema=customers_schema)
  do_fn.setup()
  out = list(do_fn.process([]))
  assert out == []


def test_duplicate_pk_routes_to_dlq(customers_schema, customers_reference):
  """Two rows with the same `customer_id` — PK uniqueness violation."""
  do_fn = PanderaValidateBatchDoFn(table_schema=customers_schema)
  do_fn.setup()
  batch = [customers_reference[0], dict(customers_reference[0])]  # same PK
  out = list(do_fn.process(batch))

  tagged = [x for x in out if isinstance(x, beam.pvalue.TaggedOutput)]
  assert tagged, "duplicate PK should route something to DLQ"
  for t in tagged:
    assert t.tag == "invalid"
    assert t.value["error_type"] == "pandera"
    assert t.value["rule_id"] == "schema.batch"
    assert t.value["stage"] == "pre_write"
    assert "failure_count" in t.value["error_detail"]


# -- temporal sentinels and column-level failures -----------------------------
def _temporal_schema():
  return TableSchema(
      table_info=TableInfo(table_id="X_TABLE"),
      columns=[
          FieldSchema(name="COL_001", bq_type="INT64", mode="REQUIRED"),
          FieldSchema(name="VALID_TO", bq_type="TIMESTAMP"),
          FieldSchema(name="COL_DT", bq_type="DATETIME"),
          FieldSchema(name="COL_D", bq_type="DATE"),
      ],
      primary_keys=["COL_001"])


def _run(batch):
  do_fn = PanderaValidateBatchDoFn(table_schema=_temporal_schema())
  do_fn.setup()
  out = list(do_fn.process(batch))
  return ([x for x in out if not isinstance(x, beam.pvalue.TaggedOutput)],
          [x.value for x in out if isinstance(x, beam.pvalue.TaggedOutput)])


_UTC = dt.UTC


@pytest.mark.parametrize(
    "valid_to, col_dt, col_d",
    [
        # An SCD table's open-ended sentinel, as str and as datetime.
        ("9999-12-30 23:00:00 UTC", "9999-12-31T23:59:59.999999", "9999-12-31"),
        ("9999-12-31 23:59:59.999999+00:00", dt.datetime(
            9999, 12, 31), dt.date(9999, 12, 31)),
        (dt.datetime(9999, 12, 30, 23, tzinfo=_UTC),
         dt.datetime(9999, 12, 31, 23, 59, 59, 999999), "0001-01-01"),
        # BigQuery's lower bound.
        ("0001-01-01 00:00:00+00:00", "0001-01-01T00:00:00", dt.date(1, 1, 1)),
        (dt.datetime(1, 1, 1, tzinfo=_UTC), dt.datetime(1, 1,
                                                        1), dt.date(1, 1, 1)),
        # An ordinary value, and a null.
        ("2024-05-01T10:00:00Z", "2024-05-01T10:00:00", "2024-05-01"),
        (None, None, None),
    ])
def test_bigquerys_whole_temporal_range_passes(valid_to, col_dt, col_d):
  """BigQuery's range ends at 9999-12-31; pandas' nanoseconds end at
  2262-04-11, which failed the column of a table whose every row carried
  the sentinel."""
  batch = [{
      "COL_001": i,
      "VALID_TO": valid_to,
      "COL_DT": col_dt,
      "COL_D": col_d
  } for i in range(3)]
  passed, dlq = _run(batch)
  assert dlq == []
  assert passed == batch  # the records as they came, for the sink


def test_a_row_level_failure_keeps_its_row_index():
  batch = [{
      "COL_001": key,
      "VALID_TO": "9999-12-30 23:00:00 UTC",
      "COL_DT": None,
      "COL_D": "9999-12-31"
  } for key in (1, 2, 2, 3)]
  passed, dlq = _run(batch)
  assert [r["COL_001"] for r in passed] == [1, 3]
  assert [d["raw_record"]["COL_001"] for d in dlq] == [2, 2]
  for envelope in dlq:
    assert envelope["rule_id"] == "schema.batch"
    detail = envelope["error_detail"]
    assert detail["column_failure_count"] == 0
    assert detail["first_failures"]
    assert all(c["index"] == str(dlq.index(envelope) + 1)
               for c in detail["first_failures"])
    json.dumps(detail, allow_nan=False)


def test_a_column_level_failure_sends_the_batch_as_schema_column():
  """A failure case with no index is the column's: no row of the batch
  passed, and every row's detail names it (it was empty before)."""
  batch = [
      {
          "COL_001": key,
          "VALID_TO": "2024-01-01T00:00:00Z",
          "COL_DT": None,
          "COL_D": None,
          "STRAY": "x"  # strict=True: a column the contract does not have
      } for key in (1, 2, 2)
  ]
  passed, dlq = _run(batch)
  assert passed == []
  assert len(dlq) == 3
  assert {d["rule_id"] for d in dlq} == {"schema.column"}
  for i, envelope in enumerate(dlq):
    detail = envelope["error_detail"]
    assert detail["column_failure_count"] >= 1
    cases = detail["first_failures"]
    assert cases
    assert {"schema_context", "column", "check"} <= set(cases[0])
    assert any(c["index"] is None for c in cases), i  # the column's own
    json.dumps(detail, allow_nan=False)  # strict JSON for the DLQ's readers
  # The duplicate rows carry their own case beside the column's.
  assert dlq[1]["error_detail"]["failure_count"] > dlq[0]["error_detail"][
      "failure_count"]
