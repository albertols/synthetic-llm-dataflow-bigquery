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
"""Whether the LANDING table's type can hold a value the source typed as
STRING — `schema.landing_type`, a BLOCKER.

Records are validated against the source contract. When the landing
table types a source STRING column otherwise (TABLE_C.COL_DATE_1: STRING
in the source, DATE in the landing table), a value the contract accepts
can still be one BigQuery's JSON load cannot read (`'2847-46-81'`), and
one such row fails the whole FILE_LOADS job. These functions read the
value the way the load does, so the row goes to the DLQ instead.

The check rejects only what the load cannot read. Where BigQuery's
acceptance is lenient or not documented (one-digit month or day, a date
alone for DATETIME or TIMESTAMP, a time zone name, an epoch-seconds
TIMESTAMP, NUMERIC scale past 9 digits, the CSV-style boolean words), the
value passes and the load stays the last line of defense.

REF: https://cloud.google.com/bigquery/docs/loading-data-cloud-storage-json
REF: https://cloud.google.com/bigquery/docs/reference/standard-sql/data-types
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from sdfb_core.contracts.schema import TableSchema

_CANONICAL = {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL"}

# ASCII digits only: `\d` would accept any Unicode digit.
_DATE = r"(?P<y>[0-9]{4})-(?P<mo>[0-9]{1,2})-(?P<d>[0-9]{1,2})"
_TIME = (r"(?P<h>[0-9]{1,2}):(?P<mi>[0-9]{1,2})"
         r"(?::(?P<s>[0-9]{1,2})(?:\.(?P<f>[0-9]{1,6}))?)?")
_ZONE = (r"\s*(?:[Zz]|[+-][0-9]{1,2}(?::?[0-9]{2})?"
         r"|[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*)")
_DATE_RE = re.compile(_DATE)
_TIME_RE = re.compile(_TIME)
_DATETIME_RE = re.compile(f"{_DATE}(?:[Tt ]{_TIME})?")
_TIMESTAMP_RE = re.compile(f"{_DATE}(?:[Tt ]{_TIME})?(?:{_ZONE})?")
_EPOCH_RE = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
_INT_RE = re.compile(r"[+-]?(?:[0-9]+|0[xX][0-9a-fA-F]+)")
_DECIMAL_RE = re.compile(
    r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_FLOAT_WORDS = re.compile(r"[+-]?(?:nan|inf|infinity)", re.IGNORECASE)
_BOOL_WORDS = frozenset(
    {"true", "false", "t", "f", "1", "0", "yes", "no", "y", "n"})

_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1
# NUMERIC holds 29 integer digits, BIGNUMERIC up to 5.7896...e38.
_NUMERIC_LIMIT = Decimal(10)**29
_BIGNUMERIC_LIMIT = Decimal(2**255) / Decimal(10)**38


def _check_date(m: re.Match[str]) -> str | None:
  try:
    dt.date(int(m["y"]), int(m["mo"]), int(m["d"]))
  except ValueError as exc:
    return f"not a calendar date ({exc})"
  return None


def _check_time(m: re.Match[str]) -> str | None:
  if m.groupdict().get("h") is None:
    return None
  try:
    dt.time(int(m["h"]), int(m["mi"]), int(m["s"] or 0))
  except ValueError as exc:
    return f"not a time of day ({exc})"
  return None


def _temporal(pattern: re.Pattern[str], form: str, value: str) -> str | None:
  m = pattern.fullmatch(value)
  if m is None:
    return f"not in the {form} form"
  return _check_date(m) or _check_time(m)


def _date(value: str) -> str | None:
  return _temporal(_DATE_RE, "YYYY-MM-DD", value)


def _datetime(value: str) -> str | None:
  return _temporal(_DATETIME_RE, "YYYY-MM-DD[( |T)HH:MM[:SS[.FFFFFF]]]", value)


def _timestamp(value: str) -> str | None:
  if _EPOCH_RE.fullmatch(value):
    return None
  return _temporal(_TIMESTAMP_RE, "YYYY-MM-DD[( |T)HH:MM[:SS[.FFFFFF]]][ zone]",
                   value)


def _time(value: str) -> str | None:
  m = _TIME_RE.fullmatch(value)
  if m is None:
    return "not in the HH:MM[:SS[.FFFFFF]] form"
  return _check_time(m)


def _int64(value: str) -> str | None:
  if not _INT_RE.fullmatch(value):
    return "not an integer"
  number = int(value, 0) if "x" in value.lower() else int(value)
  if not _INT64_MIN <= number <= _INT64_MAX:
    return "outside the INT64 range"
  return None


def _decimal(value: str, limit: Decimal, name: str) -> str | None:
  if not _DECIMAL_RE.fullmatch(value):
    return "not a finite decimal number"
  try:
    number = Decimal(value)
  except InvalidOperation:
    return "not a finite decimal number"
  if abs(number) >= limit:
    return f"outside the {name} range"
  return None


def _numeric(value: str) -> str | None:
  return _decimal(value, _NUMERIC_LIMIT, "NUMERIC")


def _bignumeric(value: str) -> str | None:
  return _decimal(value, _BIGNUMERIC_LIMIT, "BIGNUMERIC")


def _float64(value: str) -> str | None:
  if _FLOAT_WORDS.fullmatch(value):
    return None
  if not _DECIMAL_RE.fullmatch(value):
    return "not a number"
  if abs(float(value)) == float("inf"):
    return "outside the FLOAT64 range"
  return None


def _bool(value: str) -> str | None:
  return None if value.lower() in _BOOL_WORDS else "not a boolean"


_PARSERS = {
    "DATE": _date,
    "DATETIME": _datetime,
    "TIMESTAMP": _timestamp,
    "TIME": _time,
    "INT64": _int64,
    "NUMERIC": _numeric,
    "BIGNUMERIC": _bignumeric,
    "FLOAT64": _float64,
    "BOOL": _bool,
}


def landing_type_columns(source: TableSchema,
                         landing: TableSchema | None) -> dict[str, str]:
  """Column → landing type, for every STRING source column the landing
    table types as one of DATE, DATETIME, TIMESTAMP, TIME, INT64, NUMERIC,
    BIGNUMERIC, FLOAT64 or BOOL. Empty without a landing schema (absent or
    unreachable landing table); a column on one side only is skipped."""
  if landing is None:
    return {}
  landing_types = {
      c.name: _CANONICAL.get(c.bq_type, c.bq_type) for c in landing.columns
  }
  return {
      c.name: landing_types[c.name]
      for c in source.columns
      if c.bq_type == "STRING" and landing_types.get(c.name) in _PARSERS
  }


def landing_type_error(bq_type: str, value: str) -> str | None:
  """Why BigQuery's JSON load cannot read the text ``value`` as
    ``bq_type`` (a canonical name from `landing_type_columns`), or None
    when it can. Surrounding whitespace is ignored."""
  return _PARSERS[bq_type](value.strip())
