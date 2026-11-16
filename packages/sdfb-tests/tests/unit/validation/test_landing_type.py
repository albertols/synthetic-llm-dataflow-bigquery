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
"""A STRING source column the landing table types otherwise (TABLE_C.
COL_DATE_1: STRING in the source, DATE in the landing table) is checked
the way BigQuery's JSON load reads it, so a value it cannot read
(`'2847-46-81'`) is caught before the load job fails the whole run."""

from __future__ import annotations

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.validation.landing_type import (
    landing_type_columns,
    landing_type_error,
)


def _schema(*columns: tuple[str, str]) -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "p.d.TABLE_C"
      },
      "schema": [{
          "name": n,
          "type": t
      } for n, t in columns],
  })


# -- which columns are checked ------------------------------------------------
def test_only_string_source_columns_with_a_parsed_landing_type_are_checked():
  source = _schema(("COL_DATE_1", "STRING"), ("COL_D", "DATE"),
                   ("COL_S", "STRING"), ("COL_N", "STRING"),
                   ("COL_I", "INTEGER"), ("COL_ONLY_SOURCE", "STRING"))
  landing = _schema(("COL_DATE_1", "DATE"), ("COL_D", "DATE"),
                    ("COL_S", "STRING"), ("COL_N", "NUMERIC"),
                    ("COL_I", "INT64"), ("COL_ONLY_LANDING", "DATE"))
  assert landing_type_columns(source, landing) == {
      "COL_DATE_1": "DATE",
      "COL_N": "NUMERIC",
  }


def test_no_landing_schema_checks_nothing():
  assert landing_type_columns(_schema(("COL_DATE_1", "STRING")), None) == {}


def test_type_aliases_are_normalized():
  source = _schema(("A", "STRING"), ("B", "STRING"), ("C", "STRING"))
  landing = _schema(("A", "INTEGER"), ("B", "FLOAT"), ("C", "BOOLEAN"))
  assert landing_type_columns(source, landing) == {
      "A": "INT64",
      "B": "FLOAT64",
      "C": "BOOL",
  }


# -- what BigQuery's JSON load reads ------------------------------------------
_VALID = {
    "DATE": [
        "2024-05-01", "0001-01-01", "9999-12-31", "2024-02-29", "2024-5-1"
    ],
    "DATETIME": [
        "2024-05-01 10:00:00", "2024-05-01T10:00:00", "2024-05-01T10:00",
        "9999-12-31T23:59:59.999999", "2024-05-01"
    ],
    "TIMESTAMP": [
        "2024-05-01 10:00:00 UTC", "2024-05-01T10:00:00Z",
        "2024-05-01T10:00:00.123456+02:00", "9999-12-30 23:00:00 UTC",
        "2024-05-01 10:00:00-05", "2024-05-01 10:00:00 Europe/Madrid",
        "2024-05-01", "1700000000"
    ],
    "TIME": ["00:00:00", "23:59:59.999999", "7:05:09", "10:30"],
    "INT64": ["0", "-42", "+7", "9223372036854775807", "-9223372036854775808"],
    "NUMERIC": ["0", "-1.5", "12345678901234567890123456789.123456789", "1e3"],
    "BIGNUMERIC": ["1.5", "-123456789012345678901234567890123456789"],
    "FLOAT64": ["1.5", "-2", "1e-300", ".5", "NaN", "Infinity", "-Infinity"],
    "BOOL": ["true", "FALSE", "t", "f", "1", "0", "yes", "No"],
}

_INVALID = {
    "DATE": [
        "2847-46-81", "2024-02-30", "2023-02-29", "0000-01-01", "24-05-01",
        "2024/05/01", "", "not a date", "2024-05-01T10:00:00"
    ],
    "DATETIME": [
        "2847-46-81 10:00:00", "2024-05-01 24:00:00", "2024-05-01 10:60:00",
        "2024-05-01 10:00:00.1234567", "2024-05-01 10:00:00Z", ""
    ],
    "TIMESTAMP": [
        "2847-46-81 10:00:00 UTC", "2024-05-01 25:00:00 UTC", "", "yesterday"
    ],
    "TIME": ["24:00:00", "10:61:00", "10:00:61", "noon", ""],
    "INT64": ["1.5", "1.0", "", "abc", "1_000", "9223372036854775808", "١٢"],
    "NUMERIC": ["abc", "", "NaN", "Infinity", "123456789012345678901234567890"],
    "BIGNUMERIC": ["abc", "NaN", "1e40"],
    "FLOAT64": ["abc", "", "1_000", "1e400", "1.5.2"],
    "BOOL": ["maybe", "", "2", "truthy"],
}


@pytest.mark.parametrize("bq_type, value",
                         [(t, v) for t, vs in _VALID.items() for v in vs])
def test_a_value_bigquery_loads_passes(bq_type, value):
  assert landing_type_error(bq_type, value) is None


@pytest.mark.parametrize("bq_type, value",
                         [(t, v) for t, vs in _INVALID.items() for v in vs])
def test_a_value_bigquery_cannot_load_is_named(bq_type, value):
  reason = landing_type_error(bq_type, value)
  assert isinstance(reason, str) and reason


def test_the_reason_says_why():
  assert "month" in landing_type_error("DATE", "2847-46-81")
