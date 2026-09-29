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
"""Which landing rows one generation job wrote, and the source as the job
saw it — planned as SQL, never run here.

Landing rows carry no run id, so one job's rows are recovered from the
launch's write disposition and the job's own commit window (the labelled
`JobWrite`s of `context.jobs`):

    disposition  a foreign write after the job?  mode     what is read
    ───────────  ─────────────────────────────  ───────  ─────────────────────────
    overwrite    no                             table    the landing table itself
    overwrite    yes                            as_of    a snapshot clone AS OF the
                                                         window end
    append       (irrelevant)                   appends  CTAS over APPENDS(TABLE t,
                                                         window start, window end)
    --scope manual                              manual   the table as it is now

The window pads the job's own commits by a second on each side, and every
other writer is placed against it:

      now - time_travel_hours          ws = first start - 1 s   we = last end + 1 s
    ──────────┬───────────────────────────┬─────── job commits ───────┬──────────►
              │ earlier writers: ignored  │ overlapping writers:      │ later writers:
              │ (truncated / outside the  │ contaminated — their rows │ as_of (overwrite),
              │ APPENDS window)           │ look like the job's       │ ignored (appends)
              └── ws (appends) or we (as_of) older than this floor → expired

A writer overlaps when its own [start, end] meets [ws, we]. Its rows
appear at its commit, which lies somewhere in [start, end] (a job's
`end_time` only bounds the commit from above), so overlap is exactly the
set of writers whose commit MAY fall in the window. That is conservative
for an overwrite: a writer that committed before the job's truncating
first commit is flagged although the truncate removed its rows.
`expired` (time travel can no longer recover the window), `empty` (the job
wrote nothing), a rejected `contaminated` and an `unknown` that cannot be
placed leave nothing to read (`read_table == ""`): the current table is
never read in their place. `--scope manual` is the explicit way to
evaluate the table as it is now.

SQL values. The APPENDS window is bound as `@start`/`@end`: the CTAS's
`SELECT` is an ordinary query and binds like any other (`ScopePlan.params`
carries the values for `prepare_sql` AND `read_expr`). The snapshot
clone's `FOR SYSTEM_TIME AS OF` is a DDL clause BigQuery documents only
with constant expressions, so it carries an RFC 3339 `TIMESTAMP '…'`
literal re-rendered from a parsed `datetime` — only digits and fixed
separators can reach the text — and `read_expr` reuses the same literal,
so a planning read and the clone agree to the microsecond.

Temp tables are `{temp_dataset}.sdfb_eval_{evaluation_id}_{side}_{short}`
(`short` = the table id plus an 8-hex digest of the table and the
table's use, so two landing tables named alike, or a scope and a sample
of the same table, never collide) and expire 24 h after creation. The
temp dataset must live in the landing/source table's location; a snapshot
with an expiration needs `bigquery.tables.createSnapshot` and
`bigquery.tables.deleteSnapshot`. BigQuery's change history records
loads and DML appends; whether a WRITE_APPEND copy job (Beam's
multi-partition FILE_LOADS path) shows up in `APPENDS` is not documented,
and `ScopePlan.verify` — the row count against `validation_runs` — is the
guard that catches it either way. A foreign write with no BigQuery job
(a streaming insert) or run from another project is invisible to
`foreign_writes`; the same count check is the only guard there too.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from sdfb_evaluation.context.bq import normalize_fqn, quote_fqn
from sdfb_evaluation.context.jobs import JobWrite, parse_timestamp

__all__ = [
    "MODES",
    "STATUSES",
    "ScopePlan",
    "SourcePin",
    "as_of_expr",
    "from_item",
    "pin_source",
    "resolve_scope",
    "sampled_read",
]

MODES = ("table", "as_of", "appends", "manual")
STATUSES = ("ok", "count_mismatch", "contaminated", "expired", "empty",
            "unknown")
_REQUESTS = ("auto", *MODES)
_AUTO_MODE = {"overwrite": "table", "append": "appends"}

_PAD = timedelta(seconds=1)
_EXPIRY = ("OPTIONS(expiration_timestamp="
           "TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR))")
_APPENDS_SELECT = ("SELECT * EXCEPT(_CHANGE_TYPE, _CHANGE_TIMESTAMP) "
                   "FROM APPENDS(TABLE {table}, @start, @end)")
# Row counts agree within 0.5 % — exactly below 1000 expected rows.
_COUNT_TOLERANCE = 0.005
_EXACT_BELOW = 1000

_EVALUATION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SIDE_RE = re.compile(r"[a-z][a-z0-9_]{0,15}")
_SHORT_STEM = 48
_AS_OF_EXPR_RE = re.compile(r"\(SELECT \* FROM `([^`]+)` "
                            r"FOR SYSTEM_TIME AS OF TIMESTAMP '([^']+)'\)")


def _rfc3339(moment: datetime) -> str:
  return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _moment(value: str | datetime, what: str) -> datetime:
  try:
    return parse_timestamp(value)
  except (TypeError, ValueError) as exc:
    raise ValueError(f"{what}: {value!r} is not an RFC 3339 timestamp") from exc


def _timestamp_literal(moment: datetime) -> str:
  return f"TIMESTAMP '{_rfc3339(moment)}'"


def _count(value: Any, what: str) -> int:
  if isinstance(value, bool) or not isinstance(value, int) or value < 0:
    raise ValueError(f"{what}: expected a non-negative int, got {value!r}")
  return value


def _hours(value: Any) -> int:
  if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
    raise ValueError(
        f"time_travel_hours: expected a positive int, got {value!r}")
  return value


def as_of_expr(table: str, moment: str | datetime) -> str:
  """`(SELECT * FROM `t` FOR SYSTEM_TIME AS OF TIMESTAMP '…')`: the table
  as it was at `moment`, parenthesized so `FROM {expr} AS alias` stays
  valid (BigQuery's grammar puts a table's alias BEFORE its `FOR
  SYSTEM_TIME AS OF`, so a bare clause could not take one after it)."""
  literal = _timestamp_literal(_moment(moment, "as-of time"))
  return f"(SELECT * FROM {quote_fqn(table)} FOR SYSTEM_TIME AS OF {literal})"


def from_item(source: str) -> str:
  """A validated FROM item for `source`: a strict `project.dataset.table`
  (backtick-quoted) or an `as_of_expr` (re-rendered from its parsed table
  and timestamp, so no other text passes through).

  Raises:
    ValueError: anything else.
  """
  match = _AS_OF_EXPR_RE.fullmatch(source or "")
  if match is not None:
    return as_of_expr(match.group(1), match.group(2))
  return quote_fqn(source)


def _temp_table(temp_dataset: str, evaluation_id: str, side: str, table: str,
                use: str) -> str:
  if _EVALUATION_ID_RE.fullmatch(evaluation_id or "") is None:
    raise ValueError(f"evaluation_id {evaluation_id!r}: expected 1-128 of "
                     "[A-Za-z0-9_-]")
  if _SIDE_RE.fullmatch(side or "") is None:
    raise ValueError(f"side {side!r}: expected a short lowercase word")
  try:
    dataset = normalize_fqn(f"{temp_dataset}.probe").rsplit(".", 1)[0]
  except ValueError as exc:
    raise ValueError(f"temp_dataset {temp_dataset!r}: expected "
                     "project.dataset") from exc
  fqn = normalize_fqn(table)
  stem = re.sub(r"[^A-Za-z0-9_]", "_", fqn.rsplit(".", 1)[1])[:_SHORT_STEM]
  digest = hashlib.blake2b(f"{use}|{fqn}".encode(), digest_size=4).hexdigest()
  return normalize_fqn(
      f"{dataset}.sdfb_eval_{evaluation_id}_{side}_{stem}_{digest}")


@dataclass(frozen=True)
class ScopePlan:
  """The rows of one landing table that one generation job wrote.

  `read_table` is what the pipeline DIRECT_READs once `prepare_sql` (DDL,
  run driver-side right before the pipeline) has built it; `read_expr` is
  the same rows as a FROM item for planning queries and dry runs, which
  must not create tables. Both reference exactly the parameters in
  `params`. `read_table == ""` means nothing can be read (see `readable`).
  """
  landing_table: str
  mode: str  # table | as_of | appends | manual
  status: str  # ok | count_mismatch | contaminated | expired | empty | unknown
  reason: str | None
  read_table: str
  prepare_sql: tuple[str, ...]
  window: tuple[str | None, str | None]  # RFC 3339 UTC, padded by 1 s
  expected_rows: int | None
  read_expr: str = ""
  params: dict[str, Any] = field(default_factory=dict, hash=False)
  observed_rows: int | None = None

  @property
  def ok(self) -> bool:
    """The scope check passed (the registry's `scope_ok`)."""
    return self.status == "ok"

  @property
  def readable(self) -> bool:
    """There are rows to read — possibly with a non-ok status (an allowed
    contamination, an `unknown` whole-table read, a count mismatch)."""
    return bool(self.read_table)

  def verify(self, observed_rows: int) -> ScopePlan:
    """This plan with the row count actually read checked against
    `expected_rows` (Σ `validation_runs.valid_count`): equal below 1000
    expected rows, within 0.5 % from 1000 up.

    A mismatch turns an `ok` scope into `count_mismatch`; a scope that is
    already worse keeps its status. Either way the reason carries both
    numbers and `observed_rows` is set. With no expected count the reason
    says the count went unverified.

    Raises:
      ValueError: `observed_rows` is not a non-negative int, or the plan
        has nothing to read.
    """
    observed = _count(observed_rows, "observed_rows")
    if not self.readable:
      raise ValueError(f"nothing was read for {self.landing_table} (scope "
                       f"{self.status}): there is no row count to verify")
    expected = self.expected_rows
    if expected is None:
      return replace(
          self,
          observed_rows=observed,
          reason=_joined(
              self.reason, f"row count not verified: {observed} rows read "
              "and no expected count (validation_runs valid_count) is known"))
    if expected < _EXACT_BELOW:
      agree, rule = observed == expected, "exact below 1000 rows"
    else:
      agree = abs(observed - expected) <= _COUNT_TOLERANCE * expected
      rule = "tolerance 0.5 %"
    if agree:
      return replace(self, observed_rows=observed)
    return replace(
        self,
        status="count_mismatch" if self.status == "ok" else self.status,
        observed_rows=observed,
        reason=_joined(
            self.reason, f"row count mismatch: {observed} rows read, "
            f"{expected} expected ({rule})"))


def _joined(reason: str | None, note: str) -> str:
  return f"{reason}; {note}" if reason else note


class SourcePin(NamedTuple):
  """Where the source is read from: `(read_table, prepare_sql, pinned)`
  first, then the planning FROM item, the pin time and why it was not
  pinned (None when it was)."""
  read_table: str
  prepare_sql: tuple[str, ...]
  pinned: bool
  read_expr: str
  as_of: str | None
  reason: str | None


def _start(write: JobWrite) -> datetime:
  return _moment(write.start or write.end, f"start of job {write.job_id}")


def _end(write: JobWrite) -> datetime:
  return _moment(write.end or write.start, f"end of job {write.job_id}")


def _for_table(writes: Sequence[JobWrite], table: str) -> list[JobWrite]:
  return [w for w in writes if normalize_fqn(w.table) == table]


def _is_empty(own: Sequence[JobWrite], expected: int | None) -> bool:
  """Nothing to evaluate: `validation_runs` counted zero valid rows and no
  write committed any, or (count unknown) every write committed zero."""
  if expected == 0:
    return not any((w.output_rows or 0) > 0 for w in own)
  if expected is None:
    return bool(own) and all(w.output_rows == 0 for w in own)
  return False


@dataclass(frozen=True)
class _Scope:
  """What every plan of one `resolve_scope` call shares."""
  table: str
  temp: str
  expected: int | None
  window: tuple[str | None, str | None]
  hours: int
  floor: datetime  # now - hours: the oldest point time travel recovers

  def plan(self, mode: str, status: str, reason: str | None,
           **read: Any) -> ScopePlan:
    return ScopePlan(
        landing_table=self.table,
        mode=mode,
        status=status,
        reason=reason,
        read_table=read.get("read_table", ""),
        prepare_sql=read.get("prepare_sql", ()),
        window=self.window,
        expected_rows=self.expected,
        read_expr=read.get("read_expr", ""),
        params=read.get("params", {}))

  def reads(self, mode: str, ws: datetime, we: datetime) -> dict[str, Any]:
    """`read_table`/`prepare_sql`/`read_expr`/`params` of one mode."""
    table = quote_fqn(self.table)
    if mode == "table":
      return {"read_table": self.table, "read_expr": table}
    if mode == "as_of":
      return {
          "read_table": self.temp,
          "prepare_sql":
              (f"CREATE SNAPSHOT TABLE `{self.temp}` CLONE {table} "
               f"FOR SYSTEM_TIME AS OF {_timestamp_literal(we)} {_EXPIRY}",),
          "read_expr": as_of_expr(self.table, we),
      }
    select = _APPENDS_SELECT.format(table=table)
    return {
        "read_table": self.temp,
        "prepare_sql": (f"CREATE TABLE `{self.temp}` {_EXPIRY} AS {select}",),
        "read_expr": f"({select})",
        "params": {
            "start": ws,
            "end": we
        },
    }


def _manual(scope: _Scope, own: Sequence[JobWrite],
            others: Sequence[JobWrite]) -> ScopePlan:
  writers = (["this job"] if own else []) + sorted({w.job_id for w in others})
  reason = None
  if len(writers) > 1:
    # Hoisted: a quote nested in an f-string trips the py3.14 W1405 gate.
    named = ", ".join(writers)
    reason = (f"manual scope: {scope.table} is read as it is now; "
              f"{len(writers)} writers touched it ({named}), so rows this "
              "job did not write are evaluated too")
  return scope.plan(
      "manual",
      "ok",
      reason,
      read_table=scope.table,
      read_expr=quote_fqn(scope.table))


def _without_window(scope: _Scope, mode: str, requested: str) -> ScopePlan:
  missing = (f"no labelled BigQuery write by this job into {scope.table} "
             "was found (JOBS denied or past retention, or nothing landed)")
  if mode == "table" and requested == "table":
    return scope.plan(
        "table",
        "ok",
        f"{missing}; the whole table is attributed to the job as requested",
        read_table=scope.table,
        read_expr=quote_fqn(scope.table))
  if mode == "table":
    return scope.plan(
        "table",
        "unknown",
        f"{missing}: the whole table is read, but writes after the job "
        "cannot be ruled out — verify() compares its row count with the "
        "expected count",
        read_table=scope.table,
        read_expr=quote_fqn(scope.table))
  return scope.plan(
      mode, "unknown", f"{missing}, so the {mode} window cannot be placed; "
      "pass --scope manual to evaluate the table as it is now, or --scope "
      "table if the job overwrote it")


def resolve_scope(*,
                  landing_table: str,
                  write_disposition: str | None,
                  writes: Sequence[JobWrite],
                  foreign: Sequence[JobWrite],
                  expected_rows: int | None,
                  requested: str = "auto",
                  now: str | datetime,
                  time_travel_hours: int,
                  temp_dataset: str,
                  evaluation_id: str,
                  allow_contaminated: bool = False) -> ScopePlan:
  """The landing rows one generation job wrote, as a read plan.

  Args:
    landing_table: the landing FQN (`project.dataset.table` or
      `project:dataset.table`).
    write_disposition: the launch's `--write_disposition` (`append` |
      `overwrite`), None when unresolved.
    writes: the job's labelled writes (`writes_by_beam_job`); writes into
      other tables are ignored.
    foreign: other writers into the table (`foreign_writes` from the
      window start, open-ended); earlier ones are ignored.
    expected_rows: Σ `validation_runs.valid_count` for the table, or None.
    requested: `auto` (derive from the disposition) or a mode to force.
    now: the evaluation's clock (RFC 3339 text or a datetime).
    time_travel_hours: the table's own time-travel window (its dataset's
      `max_time_travel_hours`; BigQuery's default is 168).
    temp_dataset: `project.dataset` for the materialized scope.
    evaluation_id: names the temp table (`[A-Za-z0-9_-]`).
    allow_contaminated: evaluate a contaminated scope anyway; the status
      stays `contaminated` and the reason says so.

  Returns:
    A `ScopePlan`; nothing is raised for a scope that cannot be read —
    its status and reason say why and `read_table` is empty.

  Raises:
    ValueError: an input that would reach SQL is malformed, `requested`
      or `write_disposition` is unknown, or a write's times do not parse.
  """
  table = normalize_fqn(landing_table)
  if requested not in _REQUESTS:
    choices = ", ".join(_REQUESTS)
    raise ValueError(f"requested scope {requested!r}: expected one of "
                     f"{choices}")
  if write_disposition is not None and write_disposition not in _AUTO_MODE:
    raise ValueError(f"write_disposition {write_disposition!r}: expected "
                     "append or overwrite (or None when unknown)")
  expected = (None if expected_rows is None else _count(expected_rows,
                                                        "expected_rows"))
  hours = _hours(time_travel_hours)
  temp = _temp_table(temp_dataset, evaluation_id, "syn", table, "scope")

  own = _for_table(writes, table)
  own_ids = {w.job_id for w in own}
  others = [w for w in _for_table(foreign, table) if w.job_id not in own_ids]
  bounds = ((min(_start(w) for w in own) - _PAD,
             max(_end(w) for w in own) + _PAD) if own else None)
  scope = _Scope(
      table=table,
      temp=temp,
      expected=expected,
      window=((_rfc3339(bounds[0]), _rfc3339(bounds[1])) if bounds else
              (None, None)),
      hours=hours,
      floor=_moment(now, "now") - timedelta(hours=hours))

  if requested == "manual":
    return _manual(scope, own, others)
  mode = requested if requested != "auto" else _AUTO_MODE.get(
      write_disposition or "")
  if mode is None:
    return scope.plan(
        "manual", "unknown",
        "write disposition unknown (no launch parameters resolved): the "
        "job's rows cannot be isolated; pass --scope table|as_of|appends|"
        "manual")
  if _is_empty(own, expected):
    return scope.plan(mode, "empty", f"the job committed no rows into {table}")
  if bounds is None:
    return _without_window(scope, mode, requested)
  return _windowed(scope, mode, requested, others, bounds, allow_contaminated)


def _windowed(scope: _Scope, mode: str, requested: str,
              others: Sequence[JobWrite], bounds: tuple[datetime, datetime],
              allow_contaminated: bool) -> ScopePlan:
  """The plan once the job's window is known: expired, contaminated
  (rejected or allowed) or ok."""
  table = scope.table
  ws, we = bounds
  overlapping = [w for w in others if _start(w) <= we and _end(w) >= ws]
  later = [w for w in others if _start(w) > we]
  if mode == "table" and later and requested == "auto":
    mode = "as_of"
  point = {"appends": ws, "as_of": we}.get(mode)
  if point is not None and point < scope.floor:
    return scope.plan(
        mode, "expired",
        f"the {mode} point {_rfc3339(point)} is older than {table}'s "
        f"{scope.hours} h time-travel window (earliest recoverable "
        f"{_rfc3339(scope.floor)}): "
        "the job's rows can no longer be separated; pass --scope manual to "
        "evaluate the table as it is now")

  contaminating = overlapping + (later if mode == "table" else [])
  if not contaminating:
    return scope.plan(mode, "ok", None, **scope.reads(mode, ws, we))
  ids = ", ".join(sorted({w.job_id for w in contaminating}))
  where = ("inside or after the job's write window"
           if mode == "table" else "inside the job's write window")
  found = (f"{len(contaminating)} other write(s) into {table} {where} "
           f"[{scope.window[0]}, {scope.window[1]}] ({ids}): their rows "
           "cannot be told apart from the job's")
  if not allow_contaminated:
    return scope.plan(
        mode, "contaminated", f"{found}; rejected — pass "
        "allow_contaminated=True to evaluate the scope anyway")
  return scope.plan(
      mode, "contaminated", f"{found}; evaluated anyway "
      "(allow_contaminated=True): its metrics include rows the job did not "
      "write", **scope.reads(mode, ws, we))


def pin_source(*, source_table: str, job_create_time: str | datetime | None,
               now: str | datetime, time_travel_hours: int, temp_dataset: str,
               evaluation_id: str) -> SourcePin:
  """The source table as the generation job saw it (D4).

  Within the source's time-travel window, a snapshot clone AS OF the job's
  create time (`read_table` = the clone, built by `prepare_sql`;
  `read_expr` = the same state for planning reads). Otherwise, or when the
  create time is unknown, the source as it is now with `pinned=False` and
  the reason — the reference digest check then tells whether it still
  yields the generator's sample.

  Raises:
    ValueError: an input that would reach SQL is malformed, or the create
      time is after `now`.
  """
  source = normalize_fqn(source_table)
  current = quote_fqn(source)
  clock = _moment(now, "now")
  hours = _hours(time_travel_hours)
  floor = clock - timedelta(hours=hours)
  temp = _temp_table(temp_dataset, evaluation_id, "src", source, "pin")
  if job_create_time is None or job_create_time == "":
    return SourcePin(
        source, (), False, current, None,
        "generation job create time unknown: the source is read as it is "
        "now, not as the job saw it")
  created = _moment(job_create_time, "job_create_time")
  if created > clock:
    raise ValueError(f"job create time {_rfc3339(created)} is after now "
                     f"({_rfc3339(clock)})")
  if created < floor:
    return SourcePin(
        source, (), False, current, None,
        f"job create time {_rfc3339(created)} is older than {source}'s "
        f"{hours} h time-travel window (earliest recoverable "
        f"{_rfc3339(floor)}): the source is read as it is now, not as the "
        "job saw it")
  prepare = (f"CREATE SNAPSHOT TABLE `{temp}` CLONE {current} FOR SYSTEM_TIME "
             f"AS OF {_timestamp_literal(created)} {_EXPIRY}",)
  return SourcePin(temp, prepare, True, as_of_expr(source, created),
                   _rfc3339(created), None)


def sampled_read(read_table: str, *, keep: int, modulo: int, salt: str,
                 temp_dataset: str, evaluation_id: str,
                 side: str) -> tuple[str, str]:
  """`(CTAS sql, temp table)`: a salted hash sample of `read_table`
  keeping the rows whose `ABS(MOD(FARM_FINGERPRINT(CONCAT(@salt,
  TO_JSON_STRING(t))), modulo)) < keep` — about keep/modulo of them,
  the same rows for the same salt and contents.

  Execute the SQL with `{"salt": salt}` bound: the salt never enters the
  text. `keep` and `modulo` are validated ints. `ABS(MOD(x, m))` keeps
  exactly the rows `MOD(ABS(x), m)` would (BigQuery's MOD takes the sign
  of `x`) but cannot fail: `ABS` of the one INT64 without a positive
  counterpart is an overflow error. Sample from a scope's `read_table`
  after its `prepare_sql` ran.

  Raises:
    ValueError: a malformed table, count, salt or id.
  """
  table = normalize_fqn(read_table)
  modulo = _count(modulo, "modulo")
  keep = _count(keep, "keep")
  if not 0 < keep <= modulo:
    raise ValueError(f"keep {keep} / modulo {modulo}: expected "
                     "0 < keep <= modulo")
  if not isinstance(salt, str) or not salt:
    raise ValueError("salt: expected a non-empty string")
  temp = _temp_table(temp_dataset, evaluation_id, side, table, "sample")
  sql = (f"CREATE TABLE `{temp}` {_EXPIRY} AS SELECT * FROM `{table}` AS t "
         "WHERE ABS(MOD(FARM_FINGERPRINT(CONCAT(@salt, TO_JSON_STRING(t))), "
         f"{modulo})) < {keep}")
  return sql, temp
