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
"""What the composed pipeline writes, as pure functions: the registry's
lifecycle rows, the run status, the metric rows the transforms do not own
and the roll-up rows.

The registry is append-only events (D7):

    driver ──► running_row(plan)            event RUNNING, status RUNNING
                  │  (DirectRunner/ClientLoadSinks: loaded before the
                  │   pipeline; Dataflow: written before it is launched)
    pipeline ─► metric / profile / flag sinks ──► load AND copy jobs done
                  │                      (their job-id PCollections, read
                  ▼                       as AsIter side inputs)
               final_event(...)          event FINAL, recorded after them
    driver ──► failed_row(plan, exc)     event FINAL, status FAILED, on any
                                         exception (then it re-raises)

    status                   when
    ───────────────────────  ─────────────────────────────────────────────
    SKIPPED                  no launch table can be evaluated (the plan's
                             `skip_reason`; `registry_seed`'s FINAL row)
    PARTIAL                  a launch table was not evaluated (skipped at
                             planning, unreadable or failing in the
                             pipeline), a scope count mismatch, or a
                             reference panel absent or unverified (its
                             privacy block not evaluated)
    SUCCEEDED_WITH_WARNINGS  otherwise, with warnings (contaminated,
                             expired or unknown scopes, fallbacks, …)
    SUCCEEDED                otherwise
    FAILED                   the driver caught an exception

A status describes the RUN, never the data: a FAIL metric leaves it
SUCCEEDED (the counts and scores carry the verdict). Every row records
`evaluation_params.label_key_mode` (operator | ephemeral, R64; never the
key or its URI) and `evaluation_params.planning_ddl` (the as_of_diff
start snapshots planning created, R57/R58; the plan's warnings name them
too). A FINAL row's `recorded_at` is its finish time, strictly after the
RUNNING row's (`evaluation_latest` orders by it).

Metric rows the transforms do not own:

    table.row_count_ratio       n_syn / n_expected per evaluated table
                                (the scope's Σ valid_count, else the
                                job's own committed output rows)
    column.source_stats_drift   the generator's source_table_stats row vs
                                the evaluator's dense SOURCE profile
                                (Ruling R62), below
    a failed table's rows       every catalogue id the transforms own and
                                the drift, per applicable column / pair,
                                not_evaluated with the failure (driver-side
                                failures; `guarded` rewrites a worker
                                failure's rows)
    roll-ups                    table.{family}_score, table.overall_score,
                                table.column_shape_score,
                                table.pair_trend_score and model.* from the
                                scored rows (`scoring.aggregate_scores`,
                                Ruling R11); `headline_counts` leaves them
                                out (R43)

Determinism. The same plan and rows give byte-identical metric and
profile rows. Integer counts merge exactly, but Moments and co-moments
merge in floating point in the order the runner chooses (bundles,
`BatchElements`' timing-sized batches, hot-key fan-out), so a rerun
differs in the last ulps: up to about 1e-11 relative where a small delta
cancels two near-equal correlations (measured on the acceptance run).
Every float of a metric row and a profile payload is therefore written
at `STABLE_DIGITS` = 9 significant digits and never finer than
`STABLE_FLOOR` = 1e-10 absolute (every catalogue threshold is 1e-5 or
more) — the census's rule (R21) applied to every row. A value whose
merge noise straddles a rounding boundary can still differ, with a
probability of about the noise over the step (1e-6 relative noise … 1e-4
for the micro-epoch means), not the ulp noise of every value.

`checked_ci` flags a producer's reversed interval (ci_low > ci_high) in
`detail.ci_invalid` instead of letting the D5 coverage test silently read
it as "not noise" (Task 14 review). A relationship row passes its edge's
`detail["enforced"]` to `to_metric_row` (Ruling R42).

source_stats_drift (Ruling R62). The driver reads the generator's rows
for (source table, reference digest, tier) once, behind an injectable
query (`read_source_stats`), keeps only the NULL fraction, the distinct
count and the nine inner deciles per column (never a top value or an
extreme), and each column's row compares them with the evaluator's own
dense profile of the source:

    component      compared with                        when
    ─────────────  ───────────────────────────────────  ──────────────────
    null           |p_null(stats) - p_null(source)|     always
    deciles        max_i dist(i/10, [F⁻(d_i), F(d_i)])  numeric columns the
                   — where the evaluator's exact source  generator gave
                   CDF puts the generator's i-th decile  deciles and the
                                                         plan gave a grid
    distinct       |d_stats - d_src| / max(d_stats,     tier `exact` only
                   d_src), d_src the plan's source      (a `sample` tier's
                   distinct less the empty string       distinct is bounded
                                                        by its sample)

The value is the largest available component (the catalogue's formula).
With no stats (tier off, no stats table, no reference digest, no row, a
read error) every column is not_evaluated with the reason.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np

from sdfb_evaluation.beam import census, dense, membership, privacy
from sdfb_evaluation.beam.dense import DenseProfile, DenseSpec
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.context.bq import BqApiError, normalize_fqn, quote_fqn
from sdfb_evaluation.schemas import load_schema
from sdfb_evaluation.scoring import (
    FAMILIES,
    INTEGRITY_FAIL,
    MODEL_KEY,
    OVERALL,
    aggregate_scores,
    headline_counts,
    is_aggregate,
    to_metric_row,
)
from sdfb_evaluation.types import ColumnKind, Method, MetricValue

if TYPE_CHECKING:
  from sdfb_evaluation.context.plan import EvaluationPlan, TablePlan

__all__ = [
    "REGISTRY_STATUSES",
    "ColumnStats",
    "DriftSpec",
    "RegistryContext",
    "RowContext",
    "RowSummary",
    "StatsQuery",
    "TableStats",
    "aggregate_metrics",
    "checked_ci",
    "drift_metrics",
    "evaluation_status",
    "failed_row",
    "failed_table_metrics",
    "final_event",
    "final_row",
    "finish_time",
    "guarded",
    "metric_row",
    "plan_problems",
    "read_source_stats",
    "row_count_metric",
    "running_row",
    "skipped_row",
    "source_stats_sql",
    "stable_floats",
    "summarize_rows",
]

REGISTRY_STATUSES = ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PARTIAL",
                     "SKIPPED", "FAILED")
_REGISTRY = "evaluation_data_history"
_REASON_CHARS = 1000
_DRIFT_ID = "column.source_stats_drift"
_ROW_COUNT_ID = "table.row_count_ratio"
_NO_ROWS = "no source rows read"
_DECILES = tuple(i / 10 for i in range(1, 10))
_STATS_ERRORS = (BqApiError, PermissionError, LookupError, ValueError,
                 TypeError, KeyError)
# The catalogue ids the transforms own (a failed table's rows cover them).
_TRANSFORM_IDS = frozenset(
    (*dense.OWNED_METRIC_IDS, *census.OWNED_METRIC_IDS,
     *membership.OWNED_METRIC_IDS, *privacy.OWNED_METRIC_IDS))
_ROW_LEVELS = ("row", "table")
STABLE_DIGITS = 9
STABLE_FLOOR = 1e-10
_FLOOR_PLACES = 10  # decimal places of STABLE_FLOOR
_CELL_LEVELS = ("field", "column")
_TABLE_ROLLUPS = tuple(f"table.{family}_score" for family in FAMILIES)
_MODEL_ROLLUPS = tuple(f"model.{family}_score" for family in FAMILIES)

StatsQuery = Callable[..., Sequence[Mapping[str, Any]]]


# --------------------------------------------------------------------------
# times
# --------------------------------------------------------------------------
def _rfc3339(moment: datetime) -> str:
  return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(text: str) -> datetime:
  moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
  return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def finish_time(evaluated_at: str, now: datetime | None = None) -> str:
  """A FINAL event's time: `now`, but strictly after `evaluated_at` (the
  RUNNING row's `recorded_at`) whatever the clocks say, so the latest
  event is always the FINAL one."""
  moment = now or datetime.now(UTC)
  floor = _parse(evaluated_at) + timedelta(microseconds=1)
  return _rfc3339(max(moment, floor))


# --------------------------------------------------------------------------
# registry rows
# --------------------------------------------------------------------------
def _ordered(row: Mapping[str, Any]) -> dict[str, Any]:
  names = [str(f["name"]) for f in load_schema(_REGISTRY)]
  if set(row) != set(names):
    raise AssertionError(f"registry row keys differ from the schema: missing "
                         f"{sorted(set(names) - set(row))}, extra "
                         f"{sorted(set(row) - set(names))}")
  return {name: row[name] for name in names}


def _seed(plan: EvaluationPlan) -> dict[str, Any]:
  """`plan.registry_seed()` with the evaluation params only the composed
  run knows: the label-key mode and the planning DDL."""
  row = plan.registry_seed()
  row["evaluation_params"] = {
      **row["evaluation_params"],
      "label_key_mode":
          "operator" if plan.label_key_uri else "ephemeral",
      "planning_ddl": [statement.sql for statement in plan.planning_ddl],
  }
  return row


def running_row(plan: EvaluationPlan) -> dict[str, Any]:
  """The RUNNING event, in schema order (the driver writes it first)."""
  row = _seed(plan)
  row.update(
      event="RUNNING",
      status="RUNNING",
      status_reason=None,
      finished_at=None,
      recorded_at=plan.evaluated_at,
      metrics_total=None,
      metrics_pass=None,
      metrics_warn=None,
      metrics_fail=None,
      metrics_not_evaluated=None,
      metrics_info=None)
  return _ordered(row)


def skipped_row(plan: EvaluationPlan, *, finished_at: str) -> dict[str, Any]:
  """The FINAL SKIPPED event of a plan with nothing to evaluate
  (`registry_seed`'s own, stamped at `finished_at`)."""
  if plan.skip_reason is None:
    raise ValueError("the plan has tables to evaluate: it is not skipped")
  row = _seed(plan)
  row.update(recorded_at=finished_at, finished_at=finished_at)
  return _ordered(row)


def final_event(
    seed: Mapping[str, Any],
    *,
    scores: Mapping[str, Any],
    counts: Mapping[str, int],
    per_table: Mapping[str, Mapping[str, Any]],
    finished_at: str,
    status: str,
    status_reason: str | None,
    warnings: Sequence[str] = ()) -> dict[str, Any]:
  """The FINAL event from a RUNNING row (`running_row`): the model's
  family scores, the headline counts (R37), each table's `table_score`
  (`per_table[name]["table_score"]`) and any warnings the run added.

  Raises:
    ValueError: a status that is not a FINAL status.
  """
  if status not in REGISTRY_STATUSES:
    raise ValueError(f"status {status!r}: expected one of "
                     f"{list(REGISTRY_STATUSES)}")
  row = dict(seed)
  row.update(
      recorded_at=finished_at,
      finished_at=finished_at,
      event="FINAL",
      status=status,
      status_reason=status_reason,
      overall_score=scores.get(OVERALL),
      metrics_total=counts.get("total", 0),
      metrics_pass=counts.get("pass", 0),
      metrics_warn=counts.get("warn", 0),
      metrics_fail=counts.get("fail", 0),
      metrics_not_evaluated=counts.get("not_evaluated", 0),
      metrics_info=counts.get("info", 0))
  for family in FAMILIES:
    row[f"{family}_score"] = scores.get(family)
  row["tables"] = [{
      **entry, "table_score":
          per_table.get(str(entry["name"]), {}).get("table_score",
                                                    entry["table_score"])
  } for entry in seed["tables"]]
  row["warnings"] = list(dict.fromkeys([*seed["warnings"], *warnings]))
  return _ordered(row)


def final_row(
    plan: EvaluationPlan,
    scores: Mapping[str, Any],
    counts: Mapping[str, int],
    per_table: Mapping[str, Mapping[str, Any]],
    *,
    finished_at: str,
    status: str,
    status_reason: str | None,
    warnings: Sequence[str] = ()) -> dict[str, Any]:
  """The FINAL event of `plan` (`final_event` over `running_row(plan)`)."""
  return final_event(
      running_row(plan),
      scores=scores,
      counts=counts,
      per_table=per_table,
      finished_at=finished_at,
      status=status,
      status_reason=status_reason,
      warnings=warnings)


def failed_row(plan: EvaluationPlan,
               exc: BaseException,
               *,
               now: datetime | None = None) -> dict[str, Any]:
  """The FINAL FAILED event the driver appends when anything raised: the
  error class and message as the reason (bounded; this package's messages
  name tables, columns and types, never values or keys), no counts and no
  scores. An error carrying `planning_ddl` (R58) names what planning had
  created in the warnings."""
  row = running_row(plan)
  finished = finish_time(plan.evaluated_at, now)
  created = [s.sql for s in getattr(exc, "planning_ddl", ()) or ()]
  row.update(
      recorded_at=finished,
      finished_at=finished,
      event="FINAL",
      status="FAILED",
      status_reason=f"{type(exc).__name__}: {exc}"[:_REASON_CHARS])
  if created:
    row["warnings"] = [
        *row["warnings"],
        *(f"planning created {sql}" for sql in created),
    ]
  return _ordered(row)


# --------------------------------------------------------------------------
# the run status
# --------------------------------------------------------------------------
def plan_problems(plan: EvaluationPlan) -> list[str]:
  """What the plan already knows makes the run PARTIAL (module docstring),
  one line per launch table."""
  problems = []
  for table in plan.tables:
    if table.role == "external":
      continue
    if table.skip_reason is not None:
      problems.append(f"{table.name}: not evaluated — {table.skip_reason}")
    elif table.scope.status == "count_mismatch":
      problems.append(f"{table.name}: scope count_mismatch — "
                      f"{table.scope.reason}")
    elif table.panel is None:
      problems.append(f"{table.name}: no reference panel — the "
                      "reference-based privacy metrics are not evaluated")
    elif not table.panel.verified:
      why = table.panel.reason or "digest mismatch"
      problems.append(f"{table.name}: reference not verified — {why}")
  return problems


def evaluation_status(problems: Sequence[str],
                      warnings: Sequence[str]) -> tuple[str, str | None]:
  """(status, status_reason) of a run that finished (module docstring)."""
  if problems:
    return "PARTIAL", "; ".join(problems)[:_REASON_CHARS]
  if warnings:
    return ("SUCCEEDED_WITH_WARNINGS",
            f"{len(warnings)} warning(s): see warnings")
  return "SUCCEEDED", None


# --------------------------------------------------------------------------
# metric rows
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RowContext:
  """What turns a `MetricValue` into an `evaluation_metrics` row (slim:
  pickled into the pipeline): the run's ids and each table's landing and
  source table."""
  evaluation_id: str
  evaluated_at: str
  tables: Mapping[str, tuple[str | None, str | None]]
  digests: Mapping[str, str]

  @classmethod
  def from_plan(cls, plan: EvaluationPlan) -> RowContext:
    return cls(
        evaluation_id=plan.evaluation_id,
        evaluated_at=plan.evaluated_at,
        tables={t.name: (t.landing_table, t.source_table) for t in plan.tables},
        digests={t.name: t.encoding_plan_digest for t in plan.tables})


def _stable(x: float) -> float:
  """`x` to `STABLE_DIGITS` significant digits, never finer than
  `STABLE_FLOOR` (module docstring, Determinism)."""
  if not math.isfinite(x):
    return x
  if x == 0:
    return 0.0  # -0.0 too: a sign of noise must not reach the row
  places = STABLE_DIGITS - 1 - math.floor(math.log10(abs(x)))
  value = float(round(x, min(places, _FLOOR_PLACES)))
  return value if value != 0 else 0.0


def stable_floats(obj: Any) -> Any:
  """`obj` with every float `_stable`, recursively (dicts and lists)."""
  if isinstance(obj, float):
    return _stable(obj)
  if isinstance(obj, Mapping):
    return {key: stable_floats(value) for key, value in obj.items()}
  if isinstance(obj, (list, tuple)):
    return [stable_floats(item) for item in obj]
  return obj


def metric_row(mv: MetricValue, context: RowContext) -> dict[str, Any]:
  """`scoring.to_metric_row` with the run's ids and the table's landing
  and source, its floats run-stable (`stable_floats`); a relationship row
  passes its edge's `enforced` (R42)."""
  landing, source = context.tables.get(mv.table, (None, None))
  enforced = mv.detail.get("enforced", True)
  row: dict[str, Any] = stable_floats(
      to_metric_row(
          mv,
          evaluation_id=context.evaluation_id,
          evaluated_at=context.evaluated_at,
          landing_table=landing,
          source_table=source,
          enforced=enforced if isinstance(enforced, bool) else True))
  return row


def checked_ci(mv: MetricValue) -> MetricValue:
  """`mv`, its detail flagging a reversed interval (both bounds finite,
  ci_low > ci_high): a producer defect the D5 coverage test would
  otherwise read as "not noise"."""
  low, high = mv.ci_low, mv.ci_high
  if (low is None or high is None or not math.isfinite(low) or
      not math.isfinite(high) or low <= high):
    return mv
  return dataclasses.replace(
      mv,
      detail={
          **mv.detail, "ci_invalid":
              ("reversed interval (ci_low > ci_high): a producer defect; "
               "the noise check does not trust it")
      })


def _not_evaluated(mv: MetricValue, reason: str) -> MetricValue:
  detail: dict[str, Any] = {"reason": reason}
  if "enforced" in mv.detail:
    detail["enforced"] = mv.detail["enforced"]
  return MetricValue(
      metric_id=mv.metric_id,
      table=mv.table,
      value=None,
      column=mv.column,
      column_2=mv.column_2,
      edge=mv.edge,
      column_kind=mv.column_kind,
      encoding_plan_digest=mv.encoding_plan_digest,
      detail=detail)


def guarded(mv: MetricValue, failures: Mapping[str, str]) -> MetricValue:
  """`mv`, or — its table failed in the pipeline — the same metric and
  scope `not_evaluated` with the failure: rows computed from part of a
  table's batches are never published."""
  reason = failures.get(mv.table)
  if reason is None:
    return mv
  return _not_evaluated(mv, reason)


def failed_table_metrics(table: TablePlan, reason: str) -> list[MetricValue]:
  """A table that failed on the driver (unreadable, unplannable): every
  catalogue id the transforms own and `column.source_stats_drift`, per
  applicable column (by kind) or planned pair, or once for a row/table
  id, `not_evaluated` with `reason`. Its edges' rows come from the
  relational pass and its row-count row from the plan, like any
  table's."""
  out: list[MetricValue] = []
  columns = table.columns
  for metric in load_catalogue().metrics:
    if metric.id not in _TRANSFORM_IDS and metric.id != _DRIFT_ID:
      continue
    scope: dict[str, Any] = {"encoding_plan_digest": table.encoding_plan_digest}
    if metric.level in _CELL_LEVELS:
      out.extend(
          MetricValue.not_evaluated(
              metric.id,
              table.name,
              reason,
              column=c.name,
              column_kind=str(c.kind),
              **scope) for c in columns if str(c.kind) in metric.kinds)
    elif metric.level == "pair":
      out.extend(
          MetricValue.not_evaluated(
              metric.id,
              table.name,
              reason,
              column=columns[i].name,
              column_2=columns[j].name,
              **scope)
          for i, j in table.pairs
          if str(columns[i].kind) in metric.kinds and
          str(columns[j].kind) in metric.kinds)
    elif metric.level in _ROW_LEVELS:
      out.append(
          MetricValue.not_evaluated(metric.id, table.name, reason, **scope))
  return out


def row_count_metric(table: TablePlan) -> MetricValue:
  """`table.row_count_ratio`: the rows the scope holds over the rows the
  run promised (Σ valid_count, else the job's own committed output rows)."""
  n = table.rows_synthetic
  expected, basis = table.scope.expected_rows, "valid_count"
  if not expected:
    expected, basis = table.scope.written_rows, "written_rows"
  digest = table.encoding_plan_digest
  if n is None:
    return MetricValue.not_evaluated(
        _ROW_COUNT_ID,
        table.name,
        "the synthetic side was not counted",
        encoding_plan_digest=digest)
  if not expected:
    return MetricValue.not_evaluated(
        _ROW_COUNT_ID,
        table.name,
        "no expected row count: validation_runs gave no valid_count and the "
        "job's committed output rows are unknown",
        n_synthetic=n,
        encoding_plan_digest=digest)
  return MetricValue(
      metric_id=_ROW_COUNT_ID,
      table=table.name,
      value=n / expected,
      n_synthetic=n,
      encoding_plan_digest=digest,
      detail={
          "rows_synthetic": n,
          "rows_expected": expected,
          "expected_from": basis
      })


# --------------------------------------------------------------------------
# roll-ups
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RowSummary:
  """What the scored rows add up to: `scoring.aggregate_scores`, the
  headline counts and the SDMetrics-style column-shape / pair-trend
  means per table."""
  scores: Mapping[str, Mapping[str, Any]]
  counts: Mapping[str, int]
  shapes: Mapping[str, float | None]
  trends: Mapping[str, float | None]


# the fields of a row the roll-ups read (a CombineFn holds only these)
SUMMARY_FIELDS = ("table_name", "level", "family", "metric_id", "column_name",
                  "column_name_2", "edge", "score", "status")


def _fmean(values: Iterable[float]) -> float | None:
  present = list(values)
  return math.fsum(present) / len(present) if present else None


def _unit_mean(units: Mapping[Any, list[float]]) -> float | None:
  return _fmean(m for m in (_fmean(u) for u in units.values()) if m is not None)


def summarize_rows(rows: Iterable[Mapping[str, Any]]) -> RowSummary:
  """The roll-ups of `to_metric_row` rows (aggregate ids ignored). Every
  mean is an exact `fsum`, so the result never depends on row order."""
  measured = [row for row in rows if not is_aggregate(row["metric_id"])]
  shapes: dict[str, dict[str | None,
                         list[float]]] = defaultdict(lambda: defaultdict(list))
  trends: dict[str, dict[tuple[Any, Any],
                         list[float]]] = defaultdict(lambda: defaultdict(list))
  for row in measured:
    score = row.get("score")
    if score is None or not math.isfinite(score):
      continue
    if row["level"] in _CELL_LEVELS and row["family"] == "fidelity":
      shapes[row["table_name"]][row.get("column_name")].append(float(score))
    elif row["level"] == "pair":
      key = (row.get("column_name"), row.get("column_name_2"))
      trends[row["table_name"]][key].append(float(score))
  scores = aggregate_scores(measured)
  tables = [name for name in scores if name != MODEL_KEY]
  return RowSummary(
      scores=scores,
      counts=headline_counts(measured),
      shapes={name: _unit_mean(shapes.get(name, {})) for name in tables},
      trends={name: _unit_mean(trends.get(name, {})) for name in tables})


def _rollup(metric_id: str,
            table: str,
            value: float | None,
            digest: str | None,
            reason: str,
            detail: Mapping[str, Any] | None = None) -> MetricValue:
  if value is None:
    return MetricValue.not_evaluated(
        metric_id, table, reason, encoding_plan_digest=digest)
  return MetricValue(
      metric_id=metric_id,
      table=table,
      value=float(value),
      encoding_plan_digest=digest,
      detail=dict(detail or {}))


def aggregate_metrics(summary: RowSummary,
                      digests: Mapping[str, str]) -> list[MetricValue]:
  """The roll-up rows (`table.*_score`, `model.*`) of a run: a value, or
  `not_evaluated` when no scored row feeds it. A family score's row
  carries the integrity FAIL badge count (R39) on `*.integrity_score`."""
  out: list[MetricValue] = []
  for table, scores in summary.scores.items():
    model = table == MODEL_KEY
    digest = None if model else digests.get(table)
    ids = _MODEL_ROLLUPS if model else _TABLE_ROLLUPS
    for family, metric_id in zip(FAMILIES, ids, strict=True):
      badge = ({
          INTEGRITY_FAIL: scores[INTEGRITY_FAIL]
      } if family == "integrity" else None)
      out.append(
          _rollup(metric_id, table, scores[family], digest,
                  f"no scored {family} metric", badge))
    overall = "model.overall_score" if model else "table.overall_score"
    out.append(
        _rollup(overall, table, scores[OVERALL], digest,
                "no family score to average"))
    if not model:
      out.append(
          _rollup("table.column_shape_score", table, summary.shapes[table],
                  digest, "no scored column-level fidelity metric"))
      out.append(
          _rollup("table.pair_trend_score", table, summary.trends[table],
                  digest, "no scored pair metric (no planned pair)"))
  return out


@dataclass(frozen=True)
class RegistryContext:
  """What the pipeline's FINAL step needs (slim, pickled): the RUNNING
  row it completes, the PARTIAL conditions and warnings the plan knows."""
  seed: Mapping[str, Any]
  problems: tuple[str, ...]
  warnings: tuple[str, ...]

  @classmethod
  def from_plan(cls, plan: EvaluationPlan) -> RegistryContext:
    return cls(
        seed=running_row(plan),
        problems=tuple(plan_problems(plan)),
        warnings=tuple(plan.warnings))

  def final(self, summary: RowSummary, failures: Mapping[str, str], *,
            finished_at: str) -> dict[str, Any]:
    """The FINAL row once every metric write is committed; `failures`
    are the tables the pipeline could not evaluate."""
    failed = [
        f"{table}: not evaluated — {reason}"
        for table, reason in sorted(failures.items())
    ]
    status, reason = evaluation_status([*self.problems, *failed],
                                       [*self.warnings, *failed])
    per_table = {
        table: {
            "table_score": scores.get(OVERALL)
        } for table, scores in summary.scores.items() if table != MODEL_KEY
    }
    return final_event(
        self.seed,
        scores=summary.scores.get(MODEL_KEY, {}),
        counts=summary.counts,
        per_table=per_table,
        finished_at=finished_at,
        status=status,
        status_reason=reason,
        warnings=failed)


# --------------------------------------------------------------------------
# source_stats_drift (Ruling R62)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ColumnStats:
  """One column of the generator's source_table_stats row, slimmed to
  what the drift compares (no top value, no extreme)."""
  null_fraction: float | None
  distinct: int | None
  deciles: tuple[float, ...] = ()  # the nine inner deciles, p10 … p90


@dataclass(frozen=True)
class TableStats:
  """The generator's stats of one table, or why there are none."""
  tier: str | None
  columns: Mapping[str, ColumnStats] = field(default_factory=dict)
  reason: str | None = None


def source_stats_sql(stats_table: str) -> str:
  """The latest source_table_stats row per column for one (source table,
  reference digest, tier); a legacy row without a tier counts as
  `sample`.

  Raises:
    ValueError: `stats_table` is not a strict `project.dataset.table`.
  """
  return ("SELECT `column`, null_fraction, `distinct`, stats, stats_tier, "
          "sample_rows, profiler_version, computed_at "
          f"FROM {quote_fqn(normalize_fqn(stats_table))} "
          "WHERE table_fqn = @table_fqn "
          "AND reference_digest = @reference_digest "
          "AND IFNULL(stats_tier, 'sample') = @tier "
          "QUALIFY ROW_NUMBER() OVER (PARTITION BY `column` "
          "ORDER BY computed_at DESC) = 1")


def _finite(value: Any) -> float | None:
  if value is None or isinstance(value, bool):
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if math.isfinite(number) else None


def _inner_deciles(stats: Any) -> tuple[float, ...]:
  """The nine inner deciles of a stats JSON's 11-point `deciles`, or ()
  (the ends are the sample's exact extremes: never read)."""
  if isinstance(stats, str):
    try:
      stats = json.loads(stats)
    except ValueError:
      return ()
  deciles = stats.get("deciles") if isinstance(stats, Mapping) else None
  if not isinstance(deciles, list) or len(deciles) != len(_DECILES) + 2:
    return ()
  inner = [_finite(d) for d in deciles[1:-1]]
  if any(d is None for d in inner):
    return ()
  return tuple(float(d) for d in inner if d is not None)


def _column_stats(row: Mapping[str, Any]) -> ColumnStats:
  distinct = row.get("distinct")
  return ColumnStats(
      null_fraction=_finite(row.get("null_fraction")),
      distinct=(int(distinct) if isinstance(distinct, int) and
                not isinstance(distinct, bool) else None),
      deciles=_inner_deciles(row.get("stats")))


def _no_stats_reason(table: TablePlan, tier: str | None,
                     stats_table: str) -> str | None:
  if tier is None:
    return "the launch records no --source_stats tier"
  if tier == "off":
    return ("the generation ran with --source_stats=off: it wrote no "
            "source_table_stats")
  if not stats_table:
    return ("the launch named no --source_stats_table: the generator's "
            "stats never reached BigQuery")
  if not table.source_table:
    return "the table has no source table"
  if not table.reference_digest:
    return "no reference digest is recorded for this table (validation_runs)"
  return None


def read_source_stats(plan: EvaluationPlan,
                      query: StatsQuery | None) -> dict[str, TableStats]:
  """The generator's source_table_stats of every evaluated table, read on
  the driver through `query` (`Bq.query`'s shape: `query(sql, params,
  max_bytes=…)`; a fake in tests). A table without stats gets the reason;
  a read error never fails the run."""
  params = plan.launch.params
  tier_raw = params.get("source_stats")
  tier = str(tier_raw).strip().lower() if tier_raw not in (None, "") else None
  stats_table = str(params.get("source_stats_table") or "")
  out: dict[str, TableStats] = {}
  for table in plan.tables:
    if not table.evaluated:
      continue
    reason = _no_stats_reason(table, tier, stats_table)
    if reason is not None or query is None:
      out[table.name] = TableStats(
          tier=tier,
          reason=reason or
          "no BigQuery reader in this run: source_table_stats not read")
      continue
    try:
      rows = query(
          source_stats_sql(stats_table), {
              "table_fqn": normalize_fqn(str(table.source_table)),
              "reference_digest": table.reference_digest,
              "tier": tier,
          },
          max_bytes=plan.budget.max_bytes_billed)
      columns = {str(row["column"]): _column_stats(row) for row in rows}
    except _STATS_ERRORS as exc:
      out[table.name] = TableStats(
          tier=tier,
          reason=(f"source_table_stats could not be read "
                  f"({type(exc).__name__}: {exc})")[:300])
      continue
    if not columns:
      out[table.name] = TableStats(
          tier=tier,
          reason=(f"no source_table_stats row for {table.source_table} at "
                  f"this reference digest and tier {tier}"))
      continue
    out[table.name] = TableStats(tier=tier, columns=columns)
  return out


@dataclass(frozen=True, eq=False)
class _DriftColumn:
  name: str
  kind: str
  j: int
  grid: int | None  # index into the dense spec's grids
  edges: np.ndarray | None  # that grid's union edges
  string: int | None  # index into the dense spec's strings
  source_distinct: int | None


@dataclass(frozen=True, eq=False)
class DriftSpec:
  """What the drift reads of one table's plan: per non-nested column its
  dense slots and union edges (slim, pickled)."""
  table: str
  digest: str
  sample_rate: float | None
  columns: tuple[_DriftColumn, ...]

  @classmethod
  def from_table(cls, table: TablePlan) -> DriftSpec:
    spec = DenseSpec.from_table(table)
    grids = {g.j: (gi, g.union) for gi, g in enumerate(spec.grids)}
    strings = {s.j: si for si, s in enumerate(spec.strings)}
    columns = []
    for j, column in enumerate(table.columns):
      if column.kind is ColumnKind.NESTED:
        continue
      gi, edges = grids.get(j, (None, None))
      columns.append(
          _DriftColumn(
              name=column.name,
              kind=str(column.kind),
              j=j,
              grid=gi,
              edges=edges,
              string=strings.get(j),
              source_distinct=column.source_distinct))
    rate = table.sample_rate_source
    return cls(
        table=table.name,
        digest=table.encoding_plan_digest,
        sample_rate=rate if rate is not None and rate < 1 else None,
        columns=tuple(columns))


def _cdf_interval(edges: np.ndarray, right: np.ndarray, left: np.ndarray,
                  x: float) -> tuple[float, float]:
  """[F⁻(x), F(x)] of the source from its right- and left-closed union
  counts (unnormalised): exact at an edge, linear inside a bin, and the
  whole unknown range beyond the first or last edge."""
  total = float(right.sum())
  le = np.cumsum(right)[:-1] / total  # count(x <= e_k) / N
  lt = np.cumsum(left)[:-1] / total  # count(x < e_k) / N
  k = int(np.searchsorted(edges, x, side="left"))
  if k < edges.size and edges[k] == x:
    return float(lt[k]), float(le[k])
  if k == 0:
    return 0.0, float(lt[0])
  if k == edges.size:
    return float(le[-1]), 1.0
  lo_edge, hi_edge = float(edges[k - 1]), float(edges[k])
  inside = (x - lo_edge) / (hi_edge - lo_edge)
  value = float(le[k - 1] + inside * (lt[k] - le[k - 1]))
  return value, value


def _decile_delta(column: _DriftColumn, profile: DenseProfile,
                  deciles: Sequence[float]) -> float | None:
  if column.grid is None or column.edges is None or not deciles:
    return None
  right, left = profile.union[column.grid], profile.union_left[column.grid]
  if column.edges.size == 0 or right.sum() <= 0:
    return None
  worst = 0.0
  for p, d in zip(_DECILES, deciles, strict=True):
    lo, hi = _cdf_interval(column.edges, right, left, d)
    worst = max(worst, 0.0 if lo <= p <= hi else min(abs(p - lo), abs(p - hi)))
  return worst


def _distinct_delta(column: _DriftColumn, profile: DenseProfile,
                    stats: ColumnStats) -> float | None:
  if stats.distinct is None or column.source_distinct is None:
    return None
  mine = column.source_distinct
  if column.string is not None and profile.str_empty[column.string] > 0:
    mine -= 1  # the generator counts non-empty values only
  top = max(stats.distinct, mine, 1)
  return abs(stats.distinct - mine) / top


def drift_metrics(spec: DriftSpec, profile: DenseProfile | None,
                  stats: TableStats) -> list[MetricValue]:
  """One `column.source_stats_drift` row per non-nested column (module
  docstring): the largest component, the components in detail, never a
  source value."""
  out = []
  rows = 0 if profile is None else profile.rows
  for column in spec.columns:
    scope: dict[str, Any] = {
        "column": column.name,
        "column_kind": column.kind,
        "encoding_plan_digest": spec.digest,
    }
    entry = stats.columns.get(column.name)
    reason = stats.reason
    if reason is None and entry is None:
      reason = f"no source_table_stats row for column {column.name}"
    if reason is None and (profile is None or rows <= 0):
      reason = _NO_ROWS
    if reason is not None or profile is None or entry is None:
      out.append(
          MetricValue.not_evaluated(_DRIFT_ID, spec.table, str(reason),
                                    **scope))
      continue
    parts: dict[str, float] = {}
    if entry.null_fraction is not None:
      mine = float(profile.nulls[column.j]) / rows
      parts["null_fraction_delta"] = abs(entry.null_fraction - mine)
    decile = _decile_delta(column, profile, entry.deciles)
    if decile is not None:
      parts["decile_cdf_delta"] = decile
    notes: dict[str, Any] = {"tier": stats.tier}
    if stats.tier == "exact":
      distinct = _distinct_delta(column, profile, entry)
      if distinct is not None:
        parts["distinct_rel_delta"] = distinct
    else:
      notes["distinct"] = ("not compared: the sample tier's distinct count "
                           "is bounded by its sample")
    if not parts:
      out.append(
          MetricValue.not_evaluated(
              _DRIFT_ID, spec.table,
              "the stats row holds nothing comparable for this column",
              **scope))
      continue
    detail = {
        **{
            k: round(v, 12) for k, v in parts.items()
        }, "compared": sorted(parts),
        **notes
    }
    sampled = spec.sample_rate is not None
    out.append(
        MetricValue(
            metric_id=_DRIFT_ID,
            table=spec.table,
            value=round(max(parts.values()), 12),
            n_source=rows,
            method=Method.SAMPLE if sampled else Method.EXACT,
            sample_rate=spec.sample_rate,
            detail=detail,
            **scope))
  return out
