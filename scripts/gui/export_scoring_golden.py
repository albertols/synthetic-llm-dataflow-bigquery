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
"""The scoring golden: hand-picked metric values through the evaluator's scorer.

`scripts/gui/export_golden_fixtures.py` imports `scoring_golden()` from here
and writes `gui/packages/contracts/generated/golden/scoring.json`; the GUI's
`packages/stats/src/scoring.golden.test.ts` replays every case through the
TypeScript mirror (`packages/stats/src/scoring.ts`) and asserts the same
status, score, detail notes and stored (JSON-safe) numbers.

Environment: the root workspace env with `UV_PROJECT_ENVIRONMENT=.venv-gui`
(Ruling G3), the one `export_golden_fixtures.py` already runs in. The
evaluator is a standalone project excluded from the root workspace (ADR
0041), so it is not installed there; its scoring module needs only numpy and
PyYAML (both in the root env) and no Beam, so `_import_scoring()` puts
`packages/sdfb-evaluation/src` on `sys.path` before importing it. The same
module also runs in the evaluator's own env (`env -u UV_PROJECT_ENVIRONMENT
uv run --project packages/sdfb-evaluation python
scripts/gui/export_golden_fixtures.py`), which additionally needs the root
packages the other goldens import, so the root env is the simpler one.

Cases cover every scoring ruling in both directions: inclusive thresholds
and the 1e-9 tolerance (R9), target metrics with a catalogue or a
`source_value` target, CI-bound lifts that gate on the bound when the point
value is None or infinite (R38), the zero-tolerance integrity rule and the
`integrity_fail` badge (R39), noise-downgraded rows scored at the reference
(R40), the noise method dispatch — scalar floors, interval coverage, no CI
(R41), documented edges (R42), infinities, NaN, the pMSE ceiling and
aggregate ids left out of the headline counts (R43), and edge-reference
interval metrics that realistic Wilson intervals never downgrade (R45).
Non-finite inputs are written as the strings "+inf", "-inf" and "nan".

Design: docs/DESIGN.md §12 Platform GUI
(ADR 0042).
"""

from __future__ import annotations

import importlib
import math
import platform
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parents[2]
_EVAL_SRC = REPO / "packages" / "sdfb-evaluation" / "src"
_EVALUATED_AT = "2026-09-28T12:00:00+00:00"
_INF = math.inf
_NAN = math.nan


def _import_scoring() -> tuple[ModuleType, ModuleType]:
  """`sdfb_evaluation.scoring` and `.types`, from the package source."""
  if str(_EVAL_SRC) not in sys.path:
    sys.path.insert(0, str(_EVAL_SRC))
  return (importlib.import_module("sdfb_evaluation.scoring"),
          importlib.import_module("sdfb_evaluation.types"))


def _round(value: Any) -> Any:
  """Finite floats (anywhere in `value`) to 12 decimals, like the other goldens.

  Keeps float noise such as 0.5555555555556104 out of the file: its 16-digit
  run would reach the sensitive-content gate's card rule. The TypeScript test
  compares scores with a 1e-12 relative tolerance.
  """
  if isinstance(value, float) and math.isfinite(value):
    return round(value, 12)
  if isinstance(value, dict):
    return {key: _round(item) for key, item in value.items()}
  if isinstance(value, list):
    return [_round(item) for item in value]
  return value


def _enc(value: Any) -> Any:
  """A float as JSON: finite as a number, the rest as a tagged string."""
  if isinstance(value, float) and not math.isfinite(value):
    if math.isnan(value):
      return "nan"
    return "+inf" if value > 0 else "-inf"
  return value


def _case(rule: str,
          note: str,
          metric_id: str,
          value: float | None,
          *,
          ci: tuple[float | None, float | None] = (None, None),
          floor: float | None = None,
          source: float | None = None,
          detail: dict[str, Any] | None = None,
          enforced: bool = True,
          table: str = "users") -> dict[str, Any]:
  return {
      "rule": rule,
      "note": note,
      "metric_id": metric_id,
      "table": table,
      "value": value,
      "ci_low": ci[0],
      "ci_high": ci[1],
      "noise_floor": floor,
      "source_value": source,
      "detail": detail or {},
      "enforced": enforced,
  }


# (rule, note, metric, value, options). Realistic intervals come from the
# producers' own formulas (Wilson for shares, a folded Newcombe for absolute
# differences, DeLong for the AUC); the two marked "synthetic CI" exercise
# the coverage test itself with intervals no Wilson producer would emit.
CASES: tuple[dict[str, Any], ...] = (
    # --- R9: inclusive thresholds, the relative tolerance, both directions.
    _case(
        "R9",
        "lower_better under warn: PASS, complement score",
        "column.ks",
        0.05,
        floor=0.02),
    _case(
        "R9",
        "exactly at warn is WARN (inclusive)",
        "column.ks",
        0.1,
        floor=0.01),
    _case(
        "R9",
        "past fail, clear of the floor: FAIL",
        "column.ks",
        0.25,
        floor=0.02),
    _case("R9", "|0.9 - 1| = 0.0999… reaches warn 0.1 by the 1e-9 tolerance",
          "column.std_ratio", 0.9),
    _case("R9", "target metric past fail on the high side", "column.std_ratio",
          1.3),
    _case("R9", "target metric inside warn", "column.std_ratio", 1.05),
    _case("R9", "higher_better above warn: PASS", "field.type_validity",
          0.99995),
    _case("R9", "higher_better between warn and fail: WARN",
          "field.type_validity", 0.9995),
    _case("R9", "higher_better exactly at fail: FAIL (inclusive)",
          "field.type_validity", 0.999),
    _case("R9", "higher_better linear score between thresholds",
          "column.range_coverage", 0.8),
    # --- Missing values, INFO, score: none on a measured metric (R26).
    _case(
        "R43",
        "no value, the producer's reason is kept",
        "column.ks",
        None,
        detail={"reason": "constant column on both sides"}),
    _case("R43", "no value, no reason: the scorer names it", "column.ks", None),
    _case("R43", "NaN value: not evaluated", "column.ks", _NAN),
    _case("R9", "no thresholds: INFO, no score", "column.wasserstein", 3.2),
    _case("R9", "the source's own orphan rate is INFO",
          "relationship.orphan_rate_source", 0.002),
    _case("R26", "score: none on a measured metric: graded, never scored",
          "column.distinct_ceiling_hit", 0.2),
    _case("R26", "score: none at its fail threshold: FAIL, no score",
          "column.distinct_ceiling_hit", 1.0),
    # --- R43: infinities past the bad side FAIL; the good side is ungradable.
    _case("R43", "+inf on lower_better: FAIL, nonfinite +inf, score 0",
          "column.psi", _INF),
    _case("R43", "-inf on lower_better is the good side: not evaluated",
          "column.psi", -_INF),
    _case("R43", "-inf on higher_better: FAIL, nonfinite -inf",
          "row.dcr_p5_ratio", -_INF),
    _case("R43", "+inf on higher_better is the good side: not evaluated",
          "row.dcr_p5_ratio", _INF),
    _case("R43", "+inf on a target metric: FAIL", "column.std_ratio", _INF),
    _case("R43", "-inf on a target metric: FAIL (both sides are bad)",
          "column.std_ratio", -_INF),
    _case("R43", "+inf on a complement metric scores 0", "column.tvd", _INF),
    # --- R33/R43: the pMSE ratio needs its ceiling.
    _case(
        "R43",
        "pMSE with a ceiling above fail: graded",
        "table.pmse_ratio",
        2.0,
        detail={"ceiling": 50.0}),
    _case("R43", "pMSE without a ceiling: not evaluated", "table.pmse_ratio",
          12.0),
    _case(
        "R43",
        "pMSE whose ceiling is below fail: not evaluated",
        "table.pmse_ratio",
        5.0,
        detail={"ceiling": 8.0}),
    _case(
        "R43",
        "pMSE past fail with a high ceiling: FAIL",
        "table.pmse_ratio",
        12.0,
        detail={"ceiling": 400.0}),
    # --- Zero tolerance (warn == fail == 0) and R42 documented edges.
    _case("R39", "zero duplicate keys: PASS", "table.pk_duplicate_rate", 0.0),
    _case("R39", "any duplicate key fails", "table.pk_duplicate_rate", 1e-9),
    _case(
        "R39",
        "zero tolerance ignores a noise floor",
        "table.pk_duplicate_rate",
        0.3,
        floor=0.5),
    _case(
        "R42",
        "orphans on an enforced edge: FAIL",
        "relationship.orphan_rate",
        0.01,
        table="orders"),
    _case(
        "R42",
        "orphans on a documented edge: INFO, never FAIL",
        "relationship.orphan_rate",
        0.01,
        enforced=False,
        table="orders"),
    _case(
        "R42",
        "a documented edge with no orphans is still INFO",
        "relationship.orphan_rate",
        0.0,
        enforced=False,
        table="orders"),
    _case(
        "R42",
        "fan-out stays graded on a documented edge",
        "relationship.fanout_tvd",
        0.25,
        floor=0.01,
        enforced=False,
        table="orders"),
    # --- R38: lifts gate on the CI bound, even without a point value.
    _case(
        "R38",
        "no copies either side: value None, ci_low 0 -> PASS 1.0",
        "row.memorization_lift",
        None,
        ci=(0.0, _INF)),
    _case(
        "R38",
        "copies only in R: value +inf, ci_low 1.2 -> PASS",
        "row.memorization_lift",
        _INF,
        ci=(1.2, _INF)),
    _case(
        "R38",
        "ci_low between warn and fail: WARN, score on the bound",
        "row.memorization_lift",
        8.0,
        ci=(3.5, 20.0)),
    _case(
        "R38",
        "ci_low past fail: FAIL",
        "row.memorization_lift",
        8.0,
        ci=(5.5, 12.0)),
    _case("R38", "a point value without its bound: not evaluated",
          "row.memorization_lift", 3.0),
    _case("R38", "neither value nor bound: not evaluated",
          "row.memorization_lift", None),
    _case(
        "R38",
        "no finite value or bound: not evaluated",
        "row.memorization_lift",
        None,
        ci=(_INF, _INF)),
    _case(
        "R43",
        "finite point, infinite ci_low: FAIL on the bound",
        "row.memorization_lift",
        4.0,
        ci=(_INF, _INF)),
    _case(
        "R38",
        "field-level lift, clean run: PASS 1.0",
        "field.value_memorization_lift",
        None,
        ci=(0.0, _INF)),
    _case(
        "R41",
        "rate_ratio has no noise check beyond the bound",
        "row.exposure_lift",
        3.0,
        ci=(2.2, 4.1),
        floor=5.0),
    _case(
        "R45",
        "DCR share: ci_low under warn -> PASS",
        "row.dcr_train_holdout_share",
        0.52,
        ci=(0.5, 0.54)),
    _case(
        "R45",
        "DCR share: CI excludes 0.5 -> WARN stays",
        "row.dcr_train_holdout_share",
        0.62,
        ci=(0.58, 0.66)),
    _case(
        "R41",
        "an interval metric without ci_high: noise unavailable",
        "row.dcr_train_holdout_share",
        0.64,
        ci=(0.61, None)),
    # --- R40/R41: scalar noise floors downgrade; the row scores at 0.
    _case(
        "R40",
        "FAIL within the KS floor: PASS, was fail, score 1",
        "column.ks",
        0.25,
        floor=0.3),
    _case(
        "R40",
        "WARN within the KS floor: PASS, was warn",
        "column.ks",
        0.12,
        floor=0.15),
    _case("R41", "no floor: the WARN stays, noise unavailable", "column.ks",
          0.12),
    _case("R41", "a NaN floor is no floor", "column.ks", 0.12, floor=_NAN),
    _case(
        "R40",
        "fisher_z floor explains a Pearson WARN",
        "pair.pearson_delta",
        0.15,
        floor=0.2),
    _case(
        "R41",
        "mi_bias floor too small: FAIL stays",
        "pair.nmi_delta",
        0.2,
        floor=0.1),
    _case(
        "R40",
        "at exactly the jsd floor: downgraded (<=)",
        "column.jsd",
        0.06,
        floor=0.06),
    _case(
        "R40",
        "tvd_null on a fan-out histogram",
        "relationship.fanout_tvd",
        0.15,
        floor=0.2,
        table="orders"),
    _case(
        "R9",
        "novelty target from source_value: FAIL",
        "column.novelty_mass",
        0.5,
        source=0.1),
    _case(
        "R9",
        "novelty target from source_value: PASS",
        "column.novelty_mass",
        0.15,
        source=0.1),
    _case("R9", "novelty without a source_value: not evaluated",
          "column.novelty_mass", 0.3),
    _case(
        "R9",
        "novelty with an infinite source_value: not evaluated",
        "column.novelty_mass",
        0.3,
        source=_INF),
    # --- R41: interval methods downgrade iff the CI covers the reference.
    _case(
        "R41",
        "Newcombe CI covers 0: WARN -> PASS",
        "column.null_rate_delta",
        0.03,
        ci=(0.0, 0.06)),
    _case(
        "R41",
        "Newcombe CI excludes 0: FAIL stays",
        "column.null_rate_delta",
        0.06,
        ci=(0.04, 0.08)),
    _case(
        "R41",
        "a scalar floor does not count for an interval method",
        "column.null_rate_delta",
        0.06,
        floor=0.1),
    _case(
        "R41",
        "DeLong CI covers 0.5: WARN -> PASS at auc(0.5)",
        "table.detection_auc",
        0.75,
        ci=(0.45, 0.9)),
    _case(
        "R41",
        "DeLong CI excludes 0.5: FAIL",
        "table.detection_auc",
        0.9,
        ci=(0.86, 0.94)),
    _case(
        "R41",
        "AUC under warn: PASS, auc score",
        "table.detection_auc",
        0.6,
        ci=(0.55, 0.65)),
    _case(
        "R45",
        "adherence 970/1000, Wilson CI below 1: WARN stays",
        "relationship.cardinality_adherence",
        0.97,
        ci=(0.957497, 0.978906),
        table="orders"),
    _case(
        "R45",
        "full adherence: PASS",
        "field.category_adherence",
        1.0,
        ci=(0.996173, 1.0)),
    _case(
        "R41",
        "synthetic CI reaching 1 downgrades a higher_better FAIL",
        "field.category_adherence",
        0.9,
        ci=(0.85, 1.0)),
    _case(
        "R45",
        "20 copies in 10k, Wilson CI above 0: FAIL stays",
        "field.substantive_copy_rate",
        0.002,
        ci=(0.001295, 0.003087)),
    _case(
        "R45",
        "no exact matches in 100k: PASS",
        "row.exact_match_rate",
        0.0,
        ci=(0.0, 3.84e-5)),
    _case("R41", "a Wilson metric without a CI: noise unavailable",
          "row.near_match_rate", 0.002),
    _case(
        "R41",
        "synthetic CI reaching 0 downgrades a copy-rate WARN",
        "row.exact_match_rate_nonkey",
        0.002,
        ci=(0.0, 0.004)),
    # --- Aggregate ids: score: none keeps the value in [0, 1].
    _case("R11", "table family score over warn: PASS, score = value",
          "table.fidelity_score", 0.9),
    _case("R11", "model overall between thresholds: WARN",
          "model.overall_score", 0.75),
    _case("R11", "model overall at fail: FAIL (inclusive)",
          "model.overall_score", 0.7),
    _case("R26", "an aggregate outside [0, 1] scores None",
          "table.fidelity_score", 1.2),
    _case("R43", "an aggregate with no value: not evaluated",
          "table.column_shape_score", None),
)

# (metric, value, target): the five score functions straight, including the
# limits an infinite value takes and a target metric's source_value target.
SCORE_VALUES: tuple[tuple[str, float | None, float | None], ...] = (
    ("column.ks", 0.0, None),
    ("column.ks", 0.37, None),
    ("column.ks", 1.5, None),
    ("column.char_class_l1", 0.15, None),
    ("column.char_class_l1", 0.1, None),
    ("column.char_class_l1", 0.2, None),
    ("field.type_validity", 0.9995, None),
    ("field.type_validity", 1.0, None),
    ("field.type_validity", 0.5, None),
    ("column.std_ratio", 0.8, None),
    ("column.std_ratio", 1.2, None),
    ("column.novelty_mass", 0.3, 0.1),
    ("column.novelty_mass", 0.3, None),
    ("table.detection_auc", 0.4, None),
    ("table.detection_auc", 0.8, None),
    ("table.pk_duplicate_rate", 0.0, None),
    ("table.pk_duplicate_rate", 0.01, None),
    ("table.fidelity_score", 0.42, None),
    ("column.distinct_ceiling_hit", 0.42, None),
    ("column.psi", _INF, None),
    ("row.dcr_p5_ratio", -_INF, None),
    ("row.dcr_p5_ratio", _INF, None),
    ("column.ks", _NAN, None),
    ("column.ks", None, None),
    ("column.wasserstein", 3.0, None),
)


def _metric_value(types: ModuleType, case: dict[str, Any]) -> Any:
  return types.MetricValue(
      metric_id=case["metric_id"],
      table=case["table"],
      value=case["value"],
      column="email" if case["metric_id"].startswith(
          ("field.", "column.")) else None,
      column_2="country" if case["metric_id"].startswith("pair.") else None,
      edge="orders.user_id->users.id"
      if case["metric_id"].startswith("relationship.") else None,
      source_value=case["source_value"],
      noise_floor=case["noise_floor"],
      ci_low=case["ci_low"],
      ci_high=case["ci_high"],
      detail=case["detail"])


def _row(scoring: ModuleType, types: ModuleType, case: dict[str, Any]):
  mv = _metric_value(types, case)
  row = scoring.to_metric_row(
      mv,
      evaluation_id="golden",
      evaluated_at=_EVALUATED_AT,
      landing_table=None,
      source_table=None,
      enforced=case["enforced"])
  metric = scoring._catalogue().get(case["metric_id"])  # pylint: disable=protected-access  # the scorer's own catalogue instance
  status = scoring.status_for(metric, mv, enforced=case["enforced"])
  if status.value != row["status"]:
    raise AssertionError(f"{case['note']}: status_for {status} != row")
  return row


def _rollup_rows(scoring: ModuleType, types: ModuleType) -> list[dict]:
  """Two tables' rows (plus aggregates) for aggregate_scores/headline_counts."""
  plan = (
      _case("R11", "", "column.ks", 0.05, floor=0.02),
      _case("R11", "", "column.null_rate_delta", 0.01, ci=(0.0, 0.03)),
      _case("R11", "", "field.type_validity", 1.0),
      _case("R11", "", "column.tvd", 0.12, floor=0.2),
      _case("R11", "", "pair.pearson_delta", 0.15, floor=0.05),
      _case("R11", "", "row.memorization_lift", None, ci=(0.0, _INF)),
      _case("R11", "", "table.pk_duplicate_rate", 0.02),
      _case("R11", "", "table.row_count_ratio", 1.02),
      _case("R11", "", "column.wasserstein", 2.0),
      _case("R11", "", "column.distinct_ceiling_hit", 0.1),
      _case("R11", "", "table.fidelity_score", 0.8),
      _case("R11", "", "table.overall_score", 0.8),
      _case("R11", "", "relationship.orphan_rate", 0.03, table="orders"),
      _case(
          "R11",
          "",
          "relationship.orphan_rate",
          0.05,
          enforced=False,
          table="orders"),
      _case(
          "R11", "", "relationship.fanout_tvd", 0.3, floor=0.02,
          table="orders"),
      _case("R11", "", "table.identity_duplicate_rate", 0.0, table="orders"),
      _case("R11", "", "field.type_validity", 0.9, table="orders"),
      _case("R11", "", "column.ks", None, table="orders"),
      _case(
          "R11",
          "",
          "table.detection_auc",
          0.72,
          ci=(0.66, 0.78),
          table="orders"),
      _case("R11", "", "table.privacy_score", 0.9, table="orders"),
      _case("R11", "", "model.overall_score", 0.8),
  )
  rows = []
  for case in plan:
    row = _row(scoring, types, case)
    rows.append({
        key: _round(row[key])
        for key in ("table_name", "level", "family", "metric_id", "column_name",
                    "edge", "status", "score")
    })
  return rows


def scoring_golden() -> dict[str, Any]:
  """The golden document written as `golden/scoring.json`."""
  scoring, types = _import_scoring()
  cases = []
  for index, case in enumerate(CASES, start=1):
    row = _row(scoring, types, case)
    cases.append({
        "id": f"s{index:02d}",
        "rule": case["rule"],
        "note": case["note"],
        "input": {
            "metric_id": case["metric_id"],
            "value": _enc(case["value"]),
            "ci_low": _enc(case["ci_low"]),
            "ci_high": _enc(case["ci_high"]),
            "noise_floor": _enc(case["noise_floor"]),
            "source_value": _enc(case["source_value"]),
            "detail": case["detail"],
            "enforced": case["enforced"],
        },
        "row": {
            key: _round(row[key])
            for key in ("status", "score", "value", "ci_low", "ci_high",
                        "noise_floor", "source_value", "detail")
        },
    })
  score_values = []
  for metric_id, value, target in SCORE_VALUES:
    metric = scoring._catalogue().get(metric_id)  # pylint: disable=protected-access  # the scorer's own catalogue instance
    score_values.append({
        "metric_id": metric_id,
        "value": _enc(value),
        "target": target,
        "score": _round(scoring.score_value(metric, value, target=target)),
    })
  rows = _rollup_rows(scoring, types)
  aggregates = scoring.aggregate_scores(rows)
  return {
      "generated_by": "scripts/gui/export_golden_fixtures.py",
      "cases_from": "scripts/gui/export_scoring_golden.py",
      "python": platform.python_version(),
      "source": "packages/sdfb-evaluation/src/sdfb_evaluation/scoring/"
                "__init__.py (to_metric_row, status_for, score_value, "
                "aggregate_scores, headline_counts)",
      "encoding": "non-finite inputs are the strings '+inf', '-inf', 'nan'; "
                  "row numbers are JSON-safe (non-finite -> null)",
      "cases": cases,
      "score_values": score_values,
      "rollup": {
          "rows": rows,
          "model_key": scoring.MODEL_KEY,
          "aggregate_scores": _round(aggregates),
          "headline_counts": scoring.headline_counts(rows),
      },
  }
