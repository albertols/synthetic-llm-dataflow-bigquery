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
"""What the generation DAG measured about its own run: `validation_runs`.

The generator writes one `validation_runs` row per table it generates
(`sdfb_beam.pipeline`'s run-level summary). A relational launch with base
run id `B` names its tables' runs `B-NN-<table>`, NN the zero-padded
position in the generation order (parents first); a single-table launch
uses `B` itself (`sdfb_beam.cli.run_pipeline.plan_launch`):

    B = thelook-0913-a1b2c3
    ├── B-00-users         reference_digest, valid_count
    ├── B-01-orders        reference_digest, valid_count
    └── B-02-order_items   reference_digest, valid_count

`runs_for` returns those rows for a set of landing tables inside a time
window, with the base and the position recovered from each run id; it is
the one launch-context source that survives log retention.

Each row carries the generation record when the generator wrote it
(ADR 0044): the Dataflow job, the generator version and a JSON snapshot of
the launch's config. The read is `SELECT *`, so a table created before
those columns works too — its rows simply have none of them.
`runs_of_job` finds one Dataflow job's rows without knowing its tables.

Design: docs/designs/2026-07-07-evaluation-framework-design.md

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sdfb_evaluation.context.jobs import iso_timestamp, parse_timestamp

__all__ = [
    "RECORD_READ_COLUMNS", "of_job", "runs_for", "runs_of_job", "split_run_id"
]

_DATASET_RE = re.compile(
    r"([a-z][a-z0-9-]{4,28}[a-z0-9])[.:]([A-Za-z0-9_]{1,1024})")
_TABLE_NAME_RE = re.compile(r"[A-Za-z0-9_$-]{1,1024}")
_INDEX_RE = re.compile(r"(?P<base>.+)-(?P<index>\d{2,})")
# created_at is stamped inside the job; the pad absorbs clock skew only.
_RUNS_WINDOW_PAD = timedelta(minutes=10)
_COLUMNS = ("run_id", "landing_table", "reference_table", "reference_digest",
            "valid_count", "num_rows_requested", "status", "created_at")
# Read when present: three columns every validation_runs has always had, and
# the generation record's fourteen (ADR 0044).
RECORD_READ_COLUMNS = ("engine", "model_uri", "env", "job_id", "job_name",
                       "launch_run_id", "generator_version", "launched_at",
                       "embedder_uri", "retrieval_method", "similarity", "seed",
                       "source_stats", "relationships_uri",
                       "relationships_digest", "config_digest", "config")


def of_job(row_job_id: str | None, row_job_name: str | None, job_id: str | None,
           job_name: str | None) -> bool:
  """A row written by this Dataflow job: its recorded id, or — when the
  worker could not read the id — its recorded name."""
  if row_job_id:
    return bool(job_id) and row_job_id == job_id
  return bool(job_name) and row_job_name == job_name


def _source(quality_dataset: str, table_name: str) -> str:
  match = _DATASET_RE.fullmatch(quality_dataset or "")
  if match is None:
    raise ValueError(f"expected the quality dataset as project.dataset, got "
                     f"{quality_dataset!r}")
  if _TABLE_NAME_RE.fullmatch(table_name or "") is None:
    raise ValueError(f"malformed table name {table_name!r}")
  return f"`{match.group(1)}.{match.group(2)}.{table_name}`"


def _shaped(rows: Any) -> list[dict]:
  out = []
  for row in rows:
    record = {
        column: row.get(column) for column in (*_COLUMNS, *RECORD_READ_COLUMNS)
    }
    record["created_at"] = iso_timestamp(record["created_at"])
    record["launched_at"] = iso_timestamp(record["launched_at"])
    base, index = split_run_id(
        str(record["run_id"]), str(record["landing_table"]))
    record["base_run_id"] = base
    record["table_index"] = index
    out.append(record)
  return sorted(
      out,
      key=lambda r: (-1 if r["table_index"] is None else r["table_index"], r[
          "created_at"] or "", r["run_id"]))


def split_run_id(run_id: str, landing_table: str) -> tuple[str, int | None]:
  """(base run id, position) of a per-table run id.

  `B-NN-<table>` (where `<table>` is `landing_table`'s short name) gives
  `(B, NN)`; anything else is a single-table run: `(run_id, None)`.
  """
  suffix = "-" + landing_table.rsplit(".", 1)[-1]
  if run_id.endswith(suffix):
    match = _INDEX_RE.fullmatch(run_id[:-len(suffix)])
    if match is not None:
      return match.group("base"), int(match.group("index"))
  return run_id, None


def runs_for(bq: Any,
             *,
             quality_dataset: str,
             landing_tables: Sequence[str],
             window: tuple[str | None, str | None],
             table_name: str = "validation_runs",
             base_run_id: str | None = None) -> list[dict]:
  """`validation_runs` rows created inside `window` for `landing_tables`
  OR, when `base_run_id` is known, for any run of that launch
  (`run_id = @base` or `STARTS_WITH(run_id, CONCAT(@base, '-'))`) — so a
  relational launch is found whole even when only its target table is
  known.

  Args:
    bq: a `Bq` (or fake).
    quality_dataset: `project.dataset` holding the generator's table.
    landing_tables: landing FQNs, bound as an array parameter.
    window: (start, end), RFC 3339; either side may be `None` (unbounded).
      Each bound is padded by ten minutes.
    table_name: the generator's `--validation_runs_table` short name.
    base_run_id: the launch's `--run_id`, when known.

  Returns:
    Rows with `run_id`, `landing_table`, `reference_table`,
    `reference_digest`, `valid_count`, `num_rows_requested`, `status`,
    `created_at` (RFC 3339), every `RECORD_READ_COLUMNS` value (None when
    the table or the row lacks it; `launched_at` as RFC 3339), plus
    `base_run_id` and `table_index` from `split_run_id`; ordered by
    position, then creation time. Rows of OTHER launches that wrote the
    same tables in the window are returned too — the caller decides which
    base is its own.
  """
  source = _source(quality_dataset, table_name)
  if not landing_tables and not base_run_id:
    return []
  params: dict[str, Any] = {}
  matches = []
  if landing_tables:
    params["landing_tables"] = sorted(
        {t.replace(":", ".", 1) for t in landing_tables})
    matches.append("landing_table IN UNNEST(@landing_tables)")
  if base_run_id:
    params["base"] = base_run_id
    matches.append("run_id = @base OR STARTS_WITH(run_id, CONCAT(@base, '-'))")
  clauses = ["(" + " OR ".join(matches) + ")"]
  if window[0]:
    params["start"] = parse_timestamp(window[0]) - _RUNS_WINDOW_PAD
    clauses.append("created_at >= @start")
  if window[1]:
    params["end"] = parse_timestamp(window[1]) + _RUNS_WINDOW_PAD
    clauses.append("created_at <= @end")
  where = " AND ".join(clauses)
  sql = (f"SELECT *\n"
         f"FROM {source}\n"
         f"WHERE {where}\n"
         "ORDER BY created_at, run_id")
  return _shaped(bq.query(sql, params))


def runs_of_job(bq: Any,
                *,
                quality_dataset: str,
                job_id: str | None,
                job_name: str | None,
                window: tuple[str | None, str | None],
                table_name: str = "validation_runs") -> list[dict]:
  """One Dataflow job's own rows (ADR 0044), for a caller that does not
  know the job's tables: every row created inside the job's window (both
  bounds required, each padded by ten minutes), kept when `of_job` says the
  job wrote it. A table without the record columns returns no row — never
  an error. Rows shaped and ordered as `runs_for` shapes them."""
  source = _source(quality_dataset, table_name)
  if not (window[0] and window[1]) or not (job_id or job_name):
    return []
  params = {
      "start": parse_timestamp(window[0]) - _RUNS_WINDOW_PAD,
      "end": parse_timestamp(window[1]) + _RUNS_WINDOW_PAD,
  }
  sql = (f"SELECT *\n"
         f"FROM {source}\n"
         "WHERE created_at >= @start AND created_at <= @end\n"
         "ORDER BY created_at, run_id")
  own = [
      row for row in bq.query(sql, params)
      if of_job(row.get("job_id"), row.get("job_name"), job_id, job_name)
  ]
  return _shaped(own)
