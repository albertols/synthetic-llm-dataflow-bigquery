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
"""The compare golden: pairs of metric rows through the evaluator's `compare`.

`scripts/gui/export_golden_fixtures.py` imports `compare_golden()` from here
and writes `gui/packages/contracts/generated/golden/compare.json`; the GUI's
`apps/web/src/features/evaluation/lib/compare.golden.test.ts` replays every
pair through the compare view's rule (`lib/compare.ts`, `diffMetric`) and
asserts the verdict `sdfb-eval compare` gives it, so the same two runs never
read "≈" in one tool and "worse" in the other.

Each case is one metric row of run A and the row with the same key of run B.
The verdict, the delta and what the delta was judged against come from
`sdfb_evaluation.report.render.compare` itself (its `_delta`, `_noise` and
`_direction`), called once over two in-memory evaluations that hold the
cases' rows:

    both rows carry a finite noise_floor   |delta| <= hypot(floor_A, floor_B),
                                           and nothing else is consulted
    else four finite CI bounds             the two intervals overlap
    else                                   no noise information: the delta is
                                           judged by the catalogue's direction

The cases sit on each branch and its boundaries: equal values, a delta
between the larger floor and the hypotenuse, a delta exactly on the
hypotenuse (integers, so the hypotenuse is exact in both languages), one
floor missing, a NaN floor, floors that overrule overlapping intervals,
intervals that touch, an open or non-finite bound, a missing or non-finite
value, and the direction rules (lower, higher, a target, a tie in distance to
the target, a target metric without a catalogue target, an unknown metric).

Environment: as `export_scoring_golden.py`. The evaluator's `report` package
needs only numpy and PyYAML, so it imports from the package source in the root
workspace env. Non-finite inputs are written as "+inf", "-inf" and "nan".

Design: docs/DESIGN.md §12 Platform GUI
(ADR 0042).
"""

from __future__ import annotations

import importlib
import math
import sys
from types import ModuleType
from typing import Any

import export_scoring_golden

_INF = math.inf
_NAN = math.nan
_DECIMALS = 12


def _import_report() -> tuple[ModuleType, ModuleType]:
  """`sdfb_evaluation.report.render` and `.store`, from the package source."""
  src = str(export_scoring_golden.REPO / "packages" / "sdfb-evaluation" / "src")
  if src not in sys.path:
    sys.path.insert(0, src)
  return (importlib.import_module("sdfb_evaluation.report.render"),
          importlib.import_module("sdfb_evaluation.report.store"))


def _row(value: float | None,
         *,
         floor: float | None = None,
         ci: tuple[float | None, float | None] = (None, None),
         status: str = "pass") -> dict[str, Any]:
  return {
      "value": value,
      "noise_floor": floor,
      "ci_low": ci[0],
      "ci_high": ci[1],
      "status": status,
  }


# (note, metric id, the row of run A, the row of run B).
CASES: tuple[tuple[str, str, dict[str, Any], dict[str, Any]], ...] = (
    # --- "=" comes first: no noise reading is needed for an unchanged value.
    ("equal values read = whatever the floors", "column.ks",
     _row(0.1, floor=0.02), _row(0.1, floor=0.02)),
    # --- Both floors: the hypotenuse decides, alone.
    ("a delta under both floors: within noise", "column.ks",
     _row(0.1, floor=0.02), _row(0.11, floor=0.02)),
    ("a delta between the larger floor and the hypotenuse: within noise "
     "(a max-of-floors rule would judge it)", "column.ks",
     _row(0.1, floor=0.03), _row(0.145, floor=0.04)),
    ("a delta exactly on the hypotenuse (3, 4, 5): within noise, inclusive",
     "column.wasserstein", _row(10.0, floor=3.0), _row(15.0, floor=4.0)),
    ("a delta just past the hypotenuse, lower is better: worse",
     "column.wasserstein", _row(10.0, floor=3.0), _row(15.5, floor=4.0)),
    ("the same delta the other way: better", "column.wasserstein",
     _row(15.5, floor=3.0), _row(10.0, floor=4.0)),
    ("both floors overrule overlapping intervals: judged, not within noise",
     "column.ks", _row(0.1, floor=0.02,
                       ci=(0.05, 0.25)), _row(0.2, floor=0.02, ci=(0.08, 0.3))),
    # --- One floor is no floor.
    ("one floor missing, a delta under the other, no intervals: no noise "
     "information, judged (a one-floor rule would read within noise)",
     "column.ks", _row(0.1, floor=0.02), _row(0.11)),
    ("one floor missing, four finite bounds that overlap: within noise by "
     "the intervals", "column.null_rate_delta",
     _row(0.03, floor=0.02, ci=(0.0, 0.06)), _row(0.04, ci=(0.01, 0.07))),
    ("a NaN floor is no floor", "column.ks", _row(0.1, floor=_NAN),
     _row(0.11, floor=0.02)),
    # --- No floors: four finite bounds, or nothing.
    ("no floors, intervals that overlap: within noise",
     "row.dcr_train_holdout_share", _row(0.52, ci=(0.5, 0.54)),
     _row(0.55, ci=(0.53, 0.57))),
    ("no floors, intervals that touch at one point: within noise, inclusive",
     "row.dcr_train_holdout_share", _row(0.52, ci=(0.5, 0.54)),
     _row(0.56, ci=(0.54, 0.58))),
    ("no floors, intervals apart: judged", "row.dcr_train_holdout_share",
     _row(0.52, ci=(0.5, 0.54)), _row(0.62, ci=(0.58, 0.66))),
    ("an open interval (no upper bound) is no interval: judged (reading the "
     "missing bound as infinity would overlap)", "row.memorization_lift",
     _row(3.0, ci=(0.5, 12.0)), _row(1.1, ci=(0.4, None))),
    ("a non-finite bound is no bound: judged", "row.memorization_lift",
     _row(3.0, ci=(0.5, 12.0)), _row(1.1, ci=(0.4, _INF))),
    # --- No delta without two finite values.
    ("a value missing on one side: nothing to judge", "row.memorization_lift",
     _row(None, ci=(0.0, None)), _row(1.1, ci=(0.4, 2.0))),
    ("a non-finite value: nothing to judge", "column.psi", _row(_INF),
     _row(0.2)),
    # --- The direction, when noise does not explain the delta.
    ("higher is better: a rise is better", "field.type_validity", _row(0.99),
     _row(0.995)),
    ("higher is better: a drop is worse", "column.range_coverage", _row(0.95),
     _row(0.8)),
    ("a target metric: closer to the target is better", "column.std_ratio",
     _row(1.3), _row(0.95)),
    ("a target metric: farther from the target is worse", "column.std_ratio",
     _row(1.05), _row(1.3)),
    ("a target metric: the same distance on the other side reads better",
     "column.std_ratio", _row(0.5), _row(1.5)),
    ("a target metric without a catalogue target: changed, no direction",
     "column.novelty_mass", _row(0.2), _row(0.3)),
    ("a metric this catalogue does not know: changed, no direction",
     "column.not_in_this_catalogue", _row(0.2), _row(0.3)),
)


def _enc(value: Any) -> Any:
  """A float as JSON: finite as a number, the rest as a tagged string."""
  if isinstance(value, float) and not math.isfinite(value):
    if math.isnan(value):
      return "nan"
    return "+inf" if value > 0 else "-inf"
  return value


def _metric_row(case_id: str, metric_id: str, row: dict[str,
                                                        Any]) -> dict[str, Any]:
  """A stored `evaluation_metrics` row, as far as `compare` reads one; the
  case id is its column, so every case has a key of its own."""
  return {
      "table_name": "users",
      "metric_id": metric_id,
      "column_name": case_id,
      "column_name_2": None,
      "edge": None,
      **row
  }


def compare_golden() -> dict[str, Any]:
  """The golden document written as `golden/compare.json`."""
  render, store = _import_report()
  ids = [f"c{index:02d}" for index in range(1, len(CASES) + 1)]
  event = {"event": "FINAL", "status": "SUCCEEDED"}
  sides = [
      store.Evaluation(f"golden-{label}", [dict(event)], [
          _metric_row(case_id, metric_id, rows[side])
          for case_id, (_, metric_id, *rows) in zip(ids, CASES, strict=True)
      ])
      for side, label in enumerate("ab")
  ]
  by_case = {
      entry["scope"]: entry for entry in render.compare(*sides)["metrics"]
  }
  cases = []
  for case_id, (note, metric_id, a, b) in zip(ids, CASES, strict=True):
    entry = by_case[case_id]
    delta = entry["delta"]
    cases.append({
        "id": case_id,
        "note": note,
        "metric_id": metric_id,
        "a": {
            key: _enc(value) for key, value in a.items()
        },
        "b": {
            key: _enc(value) for key, value in b.items()
        },
        "python": {
            "delta": None if delta is None else round(delta, _DECIMALS),
            "noise": entry["noise"],
            "verdict": entry["verdict"],
        },
    })
  return {
      "generated_by": "scripts/gui/export_golden_fixtures.py",
      "cases_from": "scripts/gui/export_compare_golden.py",
      "python": export_scoring_golden.PYTHON,
      "source": "packages/sdfb-evaluation/src/sdfb_evaluation/report/"
                "render.py (compare: _delta, _noise, _direction)",
      "encoding": "non-finite inputs are the strings '+inf', '-inf', 'nan'; "
                  "`noise` is what the delta was judged against ('floor <the "
                  "hypotenuse of the two floors, 4 significant digits>', "
                  "'CI overlap', 'no noise floor'), null without a delta",
      "verdicts": {
          "=": "the two values are equal",
          "≈": "within noise",
          "worse": "by the catalogue's direction",
          "better": "by the catalogue's direction",
          "changed": "no direction to judge by",
          "n/a": "a side has no finite value",
      },
      "cases": cases,
  }
