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
"""B.2: sparse date strings route as dates, never as identifier templates.

2026-11-12 b2_library run 3 on the corp relationship model:
TABLE_C.COL_DATE_1 is a source STRING (landing DATE) that is 99.95 % NULL;
its few non-null values are valid ``%Y-%m-%d`` dates, near-unique in the
sample. ``fidelity._classify`` applied the free-text cardinality rule
(distinct / non-null >= 0.9) BEFORE the date shape, so the column became
FREE_TEXT -> identifier template ``9999-99-99`` with every digit drawn
independently: ``'2847-46-81'`` passed the STRING contract and failed the
BigQuery load into the DATE landing column, killing the job.

B.1 already checks the date shape before its identifier route
(``b1_rag/profile.py``); B.2 now does the same, with the thresholds it
already had: <= 20 distinct is CATEGORICAL, above it TEMPORAL.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sdfb_beam.handlers.fake_client import FakeModelClient
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationConfig, GenerationContext
from sdfb_core.engines.b2_library import B2LibraryEngine
from sdfb_core.engines.b2_library.fidelity import ColumnKind, profile_table

_NULLS = 3000


def _schema() -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "demo.table_c"
      },
      "schema": [{
          "name": "COL_DATE_1",
          "type": "STRING",
          "mode": "NULLABLE"
      }],
      "primary_keys": None,
  })


def _dates(n: int) -> list[str]:
  # Fictitious, distinct, spread over decades like the source range.
  start = date(1990, 1, 7)
  return [(start + timedelta(days=97 * i)).isoformat() for i in range(n)]


def _sparse_rows(n_dates: int) -> list[dict]:
  return ([{
      "COL_DATE_1": None
  }] * _NULLS + [{
      "COL_DATE_1": d
  } for d in _dates(n_dates)])


def _is_date(value: str) -> bool:
  try:
    datetime.strptime(value, "%Y-%m-%d")
  except ValueError:
    return False
  return True


def test_sparse_column_with_few_dates_is_categorical() -> None:
  p = profile_table(_schema(), _sparse_rows(12))["COL_DATE_1"]
  assert p.kind is ColumnKind.CATEGORICAL
  assert p.identifier_shape is None


def test_sparse_column_with_many_dates_is_temporal() -> None:
  p = profile_table(_schema(), _sparse_rows(40))["COL_DATE_1"]
  assert p.kind is ColumnKind.TEMPORAL
  assert p.temporal_format == "%Y-%m-%d"


def test_dense_unique_dates_are_temporal() -> None:
  rows = [{"COL_DATE_1": d} for d in _dates(200)]
  p = profile_table(_schema(), rows)["COL_DATE_1"]
  assert p.kind is ColumnKind.TEMPORAL


def test_unique_non_date_codes_stay_free_text() -> None:
  # The reorder must not pull identifier-like strings off the FREE_TEXT
  # route: only all-parseable date columns change route.
  rows = [{"COL_DATE_1": f"REF-{i:06d}"} for i in range(200)]
  p = profile_table(_schema(), rows)["COL_DATE_1"]
  assert p.kind is ColumnKind.FREE_TEXT


def test_engine_generates_valid_dates_and_keeps_the_null_rate() -> None:
  rows = _sparse_rows(40)
  ctx = GenerationContext(
      table_schema=_schema(),
      reference_rows=rows,
      reference_digest="sparse-date-digest",
      pipeline_run_id="b2-sparse-date-run",
  )
  engine = B2LibraryEngine(use_sdgx=False)
  engine.setup(FakeModelClient(responses=rows), ctx)
  records = list(
      engine.generate_batch(20_000, GenerationConfig(seed=7,
                                                     batch_size=20_000)))
  values = [r.model_dump(mode="python")["COL_DATE_1"] for r in records]
  non_null = [v for v in values if v is not None]
  assert non_null, "the column must still carry some dates"
  bad = [v for v in non_null if not _is_date(v)]
  assert not bad, bad[:5]
  null_rate = 1 - len(non_null) / len(values)
  expected = _NULLS / (_NULLS + 40)
  assert abs(null_rate - expected) < 0.01
