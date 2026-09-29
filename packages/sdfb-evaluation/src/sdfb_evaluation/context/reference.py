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
"""A standalone mirror of the generator's reference-sample provenance:
the row digest and the exact query that produced the sample it digests.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["reference_digest", "reference_sql"]


def reference_digest(rows: Iterable[Mapping[str, Any]]) -> str:
  """SHA-256 hex digest of canonical-encoded reference rows.

  A VERBATIM port of `sdfb_beam.io.digest.compute_reference_digest`:
  sorting the rows by their own canonical JSON before hashing makes the
  digest independent of the order a BigQuery client happened to return
  them in.
  """
  sorted_rows = sorted(
      rows,
      key=lambda r: json.dumps(r, sort_keys=True, default=str),
  )
  h = hashlib.sha256()
  for row in sorted_rows:
    h.update(json.dumps(row, sort_keys=True, default=str).encode("utf-8"))
  return h.hexdigest()


def reference_sql(table: str, limit: int) -> str:
  """The exact `SELECT` text `load_reference_rows` sends for `(table,
  limit)` with no `extra_filters` — a verbatim mirror of
  `sdfb_beam.io.bq_sources.load_reference_rows`'s query string.

  Note the double space between `` AS ref`` and ``ORDER BY``: the
  original's f-string always emits a space before AND after the `WHERE`
  clause slot, and an empty clause (no `extra_filters`, this mirror's
  only case) leaves both spaces in place.
  """
  where = ""
  return (f"SELECT * FROM `{table}` AS ref {where} "
          f"ORDER BY FARM_FINGERPRINT(TO_JSON_STRING(ref)) LIMIT {int(limit)}")
