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
"""Catalogue-driven scoring, noise-aware status (D5) and score roll-ups.

Every rule here comes from the header of `catalogue/metrics.yaml`, which is
the semantic authority; this module only executes it.

- `score_value` applies one of the five score functions, mapping a value to
  `[0, 1]` (higher is better), or `None`.
- `status_for` assigns PASS / WARN / FAIL / INFO / NOT_EVALUATED. Its order:
  1. no value (or NaN) -> NOT_EVALUATED;
  2. `table.pmse_ratio` whose `detail["ceiling"]` (`N / (k - 1)`) is below
     the fail threshold -> NOT_EVALUATED: the design cannot reach a FAIL
     (Ruling R33);
  3. a relationship-level integrity metric on a documented edge
     (`enforced=False`) -> INFO, never FAIL;
  4. null warn and fail -> INFO;
  5. the gated value g is the value, or the CI bound for `uses_ci_bound`
     metrics (`ci_low` for lower_better, `ci_high` for higher_better; a
     missing bound -> NOT_EVALUATED, never the point estimate; Rulings R1,
     R12); a non-finite g -> NOT_EVALUATED. A `target` metric gates on
     d = |g - target|, its target being the catalogue's or, when that is
     null (`column.novelty_mass`), the row's `source_value` (Ruling R9);
     x below is d on a `target` metric and g otherwise;
  6. warn == fail == 0 (integrity by construction): FAIL iff x > 0, else
     PASS, and the noise floor is never applied;
  7. otherwise crossing is inclusive (Ruling R9): lower_better and target
     reach a threshold at x >= threshold, higher_better at x <= threshold.
     Equality is checked with a 1e-9 relative tolerance, so `|0.9 - 1.0|`
     (0.09999999999999998 in floating point) still reaches a 0.1 warn;
  8. a WARN or FAIL whose |g - reference| <= the row's `noise_floor` is
     sampling noise, not a real difference (D5), and becomes PASS (the
     brief's "past fail but below the noise floor -> PASS", applied to WARN
     alike). The reference is the target (catalogue or `source_value`),
     else 0 for lower_better and 1 for higher_better (Ruling R12).
- A row's score is the score function on the same g its status read, and
  `None` for INFO and NOT_EVALUATED rows. `score: none` is the value only
  for aggregate ids (table.*_score, model.*); every other `score: none`
  metric scores `None` (Ruling R26). So a noise-downgraded PASS can still
  score low: the score measures the value, the status its significance.
- `to_metric_row` writes one `evaluation_metrics` row, JSON-safe.
- `aggregate_scores` rolls scores up over units (Ruling R11), and
  `headline_counts` counts statuses so that they reconcile with the total.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import functools
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, NamedTuple

import numpy as np

from sdfb_evaluation.canonical import canonical_value
from sdfb_evaluation.canonical import json_safe
from sdfb_evaluation.catalogue import Catalogue
from sdfb_evaluation.catalogue import Metric
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.types import Method
from sdfb_evaluation.types import MetricValue
from sdfb_evaluation.types import Status

# The key of the model-wide entry in `aggregate_scores`' result.
MODEL_KEY = "__model__"
# The four measured families; `overall` is the roll-up family.
FAMILIES: tuple[str, ...] = ("fidelity", "privacy", "integrity", "diversity")
OVERALL = "overall"
# Per table (and model): how many non-aggregate integrity rows FAILed, so a
# caller can show a FAIL badge whatever the averages say.
INTEGRITY_FAIL = "integrity_fail"

_PMSE_ID = "table.pmse_ratio"
_PMSE_REASON = "ceiling below fail threshold"
_REL_TOL = 1e-9  # tolerance for "at the threshold" (inclusive crossing)
_UNSCORED = (Status.INFO, Status.NOT_EVALUATED)


@functools.cache
def _catalogue() -> Catalogue:
  return load_catalogue()


def is_aggregate(metric_id: str) -> bool:
  """True for the roll-up ids: `table.*_score` and every `model.*` id."""
  return metric_id.startswith("model.") or (metric_id.startswith("table.") and
                                            metric_id.endswith("_score"))


def _clip01(x: float) -> float:
  return min(1.0, max(0.0, x))


def _band(x: float, warn: float, fail: float) -> float:
  """1 at or under `warn`, 0 at or over `fail`, linear between (lower is better).

  With `warn == fail` it is a step that scores 1 at the threshold itself,
  which is the integrity rule for the catalogue's only such metrics (0 / 0:
  PASS at exactly 0).
  """
  if x <= warn:
    return 1.0
  if x >= fail:
    return 0.0
  return (fail - x) / (fail - warn)


def score_value(  # noqa: PLR0911 — score-function dispatch, clearer flat than nested
    metric: Metric,
    value: float | None,
    *,
    target: float | None = None) -> float | None:
  """`value` mapped to `[0, 1]` (higher is better) by `metric.score_fn`.

  The five functions (catalogue header, Ruling R10): `complement` =
  clip(1 - |v| / range_hi); `linear` = 1 at or past warn on the good side,
  0 at or past fail, linear between (mirrored for higher_better);
  `ratio_to_one` = the same band on d = |v - target|; `auc` = 1 - 2 max(0,
  v - 0.5); `none` = v when 0 <= v <= 1 on an aggregate id, else `None`
  (Ruling R26: a raw distance or indicator is not a score).

  `target` is used only when the catalogue's target is null (the row's
  `source_value` for `column.novelty_mass`); without either a target
  metric has no score. For a `uses_ci_bound` metric pass the CI bound its
  status gates on, not the point estimate. `None`, NaN or an infinite
  value scores `None`.

  Raises:
    ValueError: a `complement` metric without a positive range top.
  """
  if value is None or not math.isfinite(value):
    return None
  fn = metric.score_fn
  if fn == "none":
    in_unit = 0.0 <= value <= 1.0
    return float(value) if in_unit and is_aggregate(metric.id) else None
  if fn == "complement":
    top = metric.range[1]
    if top is None or top <= 0:
      raise ValueError(f"{metric.id}: complement needs a positive range top")
    return _clip01(1.0 - abs(value) / top)
  if fn == "auc":
    return _clip01(1.0 - 2.0 * max(0.0, value - 0.5))
  if metric.warn is None or metric.fail is None:
    return None
  if fn == "ratio_to_one" or metric.direction == "target":
    goal = metric.target if metric.target is not None else target
    if goal is None or not math.isfinite(goal):
      return None
    return _clip01(_band(abs(value - goal), metric.warn, metric.fail))
  if metric.direction == "higher_better":
    return _clip01(_band(-value, -metric.warn, -metric.fail))
  return _clip01(_band(value, metric.warn, metric.fail))


class _Assessment(NamedTuple):
  """A status, the reason behind it and the numbers the score reads."""
  status: Status
  reason: str | None = None
  gated: float | None = None  # the value (or CI bound) status read
  target: float | None = None  # the effective target of a `target` metric


def _reached(x: float, threshold: float | None, higher_better: bool) -> bool:
  """Whether `x` is at or past `threshold` (inclusive, Ruling R9)."""
  if threshold is None:
    return False
  if math.isclose(x, threshold, rel_tol=_REL_TOL):
    return True
  return x <= threshold if higher_better else x >= threshold


def _noise_reference(metric: Metric, target: float | None) -> float:
  """What a value is compared with for the D5 noise floor (Ruling R12)."""
  if target is not None:
    return target
  if metric.target is not None:
    return metric.target
  return 1.0 if metric.direction == "higher_better" else 0.0


def _not_evaluated(reason: str) -> _Assessment:
  return _Assessment(Status.NOT_EVALUATED, reason)


def _gate(metric: Metric, mv: MetricValue) -> _Assessment | float:
  """The value status reads (step 5), or a NOT_EVALUATED assessment."""
  if not metric.uses_ci_bound:
    gated = mv.value
    name = "value"
  elif metric.direction == "target":
    raise ValueError(f"{metric.id}: uses_ci_bound needs a one-sided direction")
  elif metric.direction == "higher_better":
    gated, name = mv.ci_high, "ci_high"
  else:
    gated, name = mv.ci_low, "ci_low"
  if gated is None:
    return _not_evaluated(
        f"{name} missing: {metric.id} gates on its confidence bound")
  if not math.isfinite(gated):
    return _not_evaluated(f"non-finite {name} ({gated!r})")
  return float(gated)


def _grade(metric: Metric, x: float, gated: float, target: float | None,
           floor: float | None) -> Status:
  """Steps 6-8: the integrity rule, inclusive crossings, the D5 noise floor.

  `x` is what the thresholds read: d = |gated - target| on a `target`
  metric, else `gated` itself.
  """
  if metric.warn == 0 and metric.fail == 0:
    return Status.FAIL if x > 0 else Status.PASS
  higher_better = metric.direction == "higher_better"
  if _reached(x, metric.fail, higher_better):
    status = Status.FAIL
  elif _reached(x, metric.warn, higher_better):
    status = Status.WARN
  else:
    return Status.PASS
  if (floor is not None and math.isfinite(floor) and
      abs(gated - _noise_reference(metric, target)) <= floor):
    return Status.PASS
  return status


def _assess(  # noqa: PLR0911 — the status order, clearer flat than nested
    metric: Metric, mv: MetricValue, enforced: bool) -> _Assessment:
  """The full status decision, in the order the module docstring lists."""
  if mv.metric_id != metric.id:
    raise ValueError(
        f"metric value {mv.metric_id!r} scored against catalogue {metric.id!r}")
  if mv.value is None or math.isnan(mv.value):
    fallback = "no value computed" if mv.value is None else "value is NaN"
    return _not_evaluated(str(mv.detail.get("reason") or fallback))
  ceiling = mv.detail.get("ceiling")
  if (metric.id == _PMSE_ID and ceiling is not None and
      metric.fail is not None and float(ceiling) < metric.fail):
    return _not_evaluated(_PMSE_REASON)
  if (not enforced and metric.level == "relationship" and
      metric.family == "integrity"):
    return _Assessment(
        Status.INFO, "documented edge (enforced: false): reported, not gated")
  if metric.warn is None and metric.fail is None:
    return _Assessment(Status.INFO)
  gated = _gate(metric, mv)
  if isinstance(gated, _Assessment):
    return gated
  target = None
  x = gated
  if metric.direction == "target":
    target = metric.target if metric.target is not None else mv.source_value
    if target is None or not math.isfinite(target):
      return _not_evaluated("no target: source_value missing or non-finite")
    x = abs(gated - target)
  status = _grade(metric, x, gated, target, mv.noise_floor)
  return _Assessment(status, None, gated, target)


def status_for(metric: Metric,
               mv: MetricValue,
               *,
               enforced: bool = True) -> Status:
  """`mv`'s status under `metric`'s catalogue entry (D5; see module docstring).

  `enforced=False` marks a documented foreign-key edge: its integrity
  metrics (orphan rates) are reported as INFO, never gated.

  Raises:
    ValueError: `mv.metric_id` is not `metric.id`.
  """
  return _assess(metric, mv, enforced).status


def _as_float(x: Any) -> float | None:
  return None if x is None else float(x)


def _as_int(x: Any) -> int | None:
  return None if x is None else int(x)


def _plain(obj: Any) -> Any:
  """`obj` with numpy scalars/arrays as Python values, for a JSON column."""
  if isinstance(obj, np.generic):
    return obj.item()
  if isinstance(obj, np.ndarray):
    return obj.tolist()
  if isinstance(obj, Mapping):
    return {str(key): _plain(value) for key, value in obj.items()}
  if isinstance(obj, (list, tuple)):
    return [_plain(item) for item in obj]
  return obj


def to_metric_row(mv: MetricValue,
                  *,
                  evaluation_id: str,
                  evaluated_at: datetime | str,
                  landing_table: str | None,
                  source_table: str | None,
                  enforced: bool = True) -> dict[str, Any]:
  """One `evaluation_metrics` row: exactly its fields, in schema order.

  `level`, `family`, `metric_version`, `value_kind`, the thresholds and
  `noise_floor_method` come from the catalogue; `table`/`column`/
  `column_2` map to `table_name`/`column_name`/`column_name_2` (Ruling
  R15). A relationship row is the child table's (`mv.table`), labelled by
  `mv.edge`. Every NOT_EVALUATED row carries `detail["reason"]` (the
  producer's own when it gave one), and a documented edge's INFO row says
  so there too. `evaluated_at` may be a `datetime` (written as UTC ISO-8601
  with microseconds). Non-finite floats become `None` (`json_safe`), so the
  row can go straight to a BigQuery load job.

  Raises:
    KeyError: `mv.metric_id` is not in the catalogue.
  """
  metric = _catalogue().get(mv.metric_id)
  assessment = _assess(metric, mv, enforced)
  detail = dict(mv.detail)
  if assessment.reason is not None:
    detail["reason"] = assessment.reason
  score = None
  if assessment.status not in _UNSCORED:
    score = score_value(metric, assessment.gated, target=assessment.target)
  when = (
      canonical_value(evaluated_at)
      if isinstance(evaluated_at, datetime) else evaluated_at)
  row = {
      "evaluation_id": evaluation_id,
      "evaluated_at": when,
      "table_name": mv.table,
      "landing_table": landing_table,
      "source_table": source_table,
      "level": metric.level,
      "family": metric.family,
      "metric_id": metric.id,
      "metric_version": metric.version,
      "value_kind": metric.value_kind,
      "column_name": mv.column,
      "column_name_2": mv.column_2,
      "column_kind": mv.column_kind,
      "edge": mv.edge,
      "value": _as_float(mv.value),
      "source_value": _as_float(mv.source_value),
      "synthetic_value": _as_float(mv.synthetic_value),
      "baseline_value": _as_float(mv.baseline_value),
      "score": score,
      "status": assessment.status.value,
      "threshold_warn": metric.warn,
      "threshold_fail": metric.fail,
      "noise_floor": _as_float(mv.noise_floor),
      "noise_floor_method": metric.noise_floor,
      "ci_low": _as_float(mv.ci_low),
      "ci_high": _as_float(mv.ci_high),
      "n_source": _as_int(mv.n_source),
      "n_synthetic": _as_int(mv.n_synthetic),
      "method": Method(mv.method).value,
      "sample_rate": _as_float(mv.sample_rate),
      "encoding_plan_digest": mv.encoding_plan_digest,
      "feature_set_digest": mv.feature_set_digest,
      "detail": _plain(detail) or None,
  }
  safe: dict[str, Any] = json_safe(row)
  return safe


def _mean(values: Iterable[float | None]) -> float | None:
  """The mean of the non-`None` values (`None` if there are none).

  `math.fsum` rounds the sum exactly once, so the mean does not depend on
  the order rows arrive in (a Beam combine gives no order).
  """
  present = [value for value in values if value is not None]
  return math.fsum(present) / len(present) if present else None


def _unit(row: Mapping[str, Any]) -> tuple[str, str | None]:
  """The roll-up unit a non-aggregate row belongs to (Ruling R11)."""
  level = row["level"]
  if level in ("field", "column"):
    return ("column", row.get("column_name"))
  if level == "pair":
    return ("pair", None)
  if level in ("row", "table"):
    return (level, row["metric_id"])
  if level == "relationship":
    return ("edge", row.get("edge"))
  raise ValueError(f"no roll-up unit for level {level!r}")


def aggregate_scores(
    rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
  """Family and overall scores per table and for the model (Ruling R11).

  `rows` are `to_metric_row` rows. A table's family score is the mean over
  UNITS with at least one scored row in that family: each column (the mean
  of its field and column scores), the pair group (one unit), each
  row-level metric, each non-aggregate table-level metric and each edge the
  table is the child of (one unit, keyed by `edge`). Aggregate ids never
  feed themselves, `None` scores are skipped and a unit with none left
  does not count. `overall` is the mean of the available family scores.

  Returns `{table: {fidelity, privacy, integrity, diversity, overall,
  integrity_fail}, MODEL_KEY: {...}}`, tables sorted by name. A family
  with no unit is `None`. The model's family score is the mean of the
  tables' family scores and its overall the mean of its family scores.
  `integrity_fail` counts the non-aggregate integrity rows with status
  FAIL (summed for the model), so a caller can show a FAIL badge whatever
  the averages say.
  """
  units: dict[str, dict[str, dict[tuple[str, str | None], list[float]]]] = (
      defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
  fails: dict[str, int] = defaultdict(int)
  tables: set[str] = set()  # every table seen, scored rows or not
  for row in rows:
    table = row["table_name"]
    tables.add(table)
    if is_aggregate(row["metric_id"]):
      continue
    if row["family"] == "integrity" and row["status"] == Status.FAIL:
      fails[table] += 1
    score = row.get("score")
    if score is not None and math.isfinite(score):
      units[table][row["family"]][_unit(row)].append(float(score))
  result: dict[str, dict[str, Any]] = {}
  for table in sorted(tables):
    scores: dict[str, Any] = {
        family: _mean(_mean(unit) for unit in units[table][family].values())
        for family in FAMILIES
    }
    scores[OVERALL] = _mean(scores[family] for family in FAMILIES)
    scores[INTEGRITY_FAIL] = fails[table]
    result[table] = scores
  model: dict[str, Any] = {
      family: _mean(scores[family] for scores in result.values())
      for family in FAMILIES
  }
  model[OVERALL] = _mean(model[family] for family in FAMILIES)
  model[INTEGRITY_FAIL] = sum(fails.values())
  result[MODEL_KEY] = model
  return result


def headline_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
  """How many rows have each status, plus `total`.

  Every `Status` value is a key, zero included, so the counts always sum to
  `total` (info and not_evaluated rows included).

  Raises:
    ValueError: a row's status is not a `Status` value.
  """
  counts = {status.value: 0 for status in Status}
  total = 0
  for row in rows:
    status = row["status"]
    if status not in counts:
      raise ValueError(f"unknown status {status!r}")
    counts[status] += 1
    total += 1
  counts["total"] = total
  return counts
