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
"""Catalogue-driven scoring, noise-aware status (D5) and roll-ups (Task 14).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdfb_evaluation import schemas
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.scoring import MODEL_KEY
from sdfb_evaluation.scoring import aggregate_scores
from sdfb_evaluation.scoring import headline_counts
from sdfb_evaluation.scoring import is_aggregate
from sdfb_evaluation.scoring import score_value
from sdfb_evaluation.scoring import status_for
from sdfb_evaluation.scoring import to_metric_row
from sdfb_evaluation.types import Method
from sdfb_evaluation.types import MetricValue
from sdfb_evaluation.types import Status

CAT = load_catalogue()
_FAMILIES = ("fidelity", "privacy", "integrity", "diversity")


def _metric(metric_id: str):
  return CAT.get(metric_id)


def _mv(metric_id: str, value: float | None, **kw: Any) -> MetricValue:
  return MetricValue(metric_id=metric_id, table="orders", value=value, **kw)


def _status(metric_id: str, value: float | None, **kw: Any) -> Status:
  enforced = kw.pop("enforced", True)
  return status_for(
      _metric(metric_id), _mv(metric_id, value, **kw), enforced=enforced)


def _row_of(mv: MetricValue, *, enforced: bool = True) -> dict[str, Any]:
  return to_metric_row(
      mv,
      evaluation_id="eval-0001",
      evaluated_at=datetime(2026, 9, 28, 10, 0, tzinfo=UTC),
      landing_table="demo-project.synthetic_data.orders",
      source_table="demo-project.synthetic_source.orders",
      enforced=enforced)


# ---------------------------------------------------------------------------
# Catalogue invariants the scorer relies on
# ---------------------------------------------------------------------------


def test_catalogue_threshold_order_matches_direction():
  for metric in CAT.metrics:
    if metric.warn is None or metric.fail is None:
      continue
    if metric.direction == "higher_better":
      assert metric.warn >= metric.fail, metric.id
    else:
      assert metric.warn <= metric.fail, metric.id


def test_catalogue_complement_metrics_have_a_positive_range_top():
  for metric in CAT.metrics:
    if metric.score_fn == "complement":
      assert metric.range[1] is not None and metric.range[1] > 0, metric.id


def test_catalogue_ci_bound_metrics_are_one_sided():
  for metric in CAT.metrics:
    if metric.uses_ci_bound:
      assert metric.direction != "target", metric.id


def test_is_aggregate_ids():
  aggregates = {m.id for m in CAT.metrics if is_aggregate(m.id)}
  assert aggregates == {
      "table.column_shape_score", "table.pair_trend_score",
      "table.fidelity_score", "table.privacy_score", "table.integrity_score",
      "table.diversity_score", "table.overall_score", "model.overall_score",
      "model.fidelity_score", "model.privacy_score", "model.integrity_score",
      "model.diversity_score"
  }


# ---------------------------------------------------------------------------
# score_value: the five score functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [(0.0, 1.0), (0.25, 0.75),
                                                 (1.0, 0.0), (1.5, 0.0)])
def test_score_complement(value, expected):
  assert score_value(_metric("column.ks"), value) == pytest.approx(expected)


@pytest.mark.parametrize(("value", "expected"), [(0.02, 1.0), (0.05, 1.0),
                                                 (0.075, 0.5), (0.1, 0.0),
                                                 (0.3, 0.0)])
def test_score_linear_lower_better(value, expected):
  assert score_value(_metric("column.jsd"), value) == pytest.approx(expected)


@pytest.mark.parametrize(("value", "expected"), [(1.0, 1.0), (0.99, 1.0),
                                                 (0.97, 0.5), (0.95, 0.0),
                                                 (0.5, 0.0)])
def test_score_linear_higher_better(value, expected):
  assert score_value(_metric("field.category_adherence"),
                     value) == pytest.approx(expected)


@pytest.mark.parametrize(("value", "expected"), [(1.0, 1.0), (1.05, 1.0),
                                                 (0.825, 0.5), (1.175, 0.5),
                                                 (1.25, 0.0), (3.0, 0.0)])
def test_score_ratio_to_one(value, expected):
  assert score_value(_metric("column.std_ratio"),
                     value) == pytest.approx(expected)


def test_score_ratio_to_one_null_target_reads_the_given_target():
  novelty = _metric("column.novelty_mass")
  assert novelty.target is None
  # d = |0.3 - 0.1| = 0.2, between warn 0.1 and fail 0.25.
  assert score_value(
      novelty, 0.3, target=0.1) == pytest.approx((0.25 - 0.2) / 0.15)
  assert score_value(novelty, 0.1, target=0.1) == 1.0
  assert score_value(novelty, 0.3) is None  # no target, no score


def test_score_ratio_to_one_catalogue_target_wins_over_the_given_one():
  assert score_value(_metric("column.std_ratio"), 1.0, target=5.0) == 1.0


@pytest.mark.parametrize(("value", "expected"), [(0.3, 1.0), (0.5, 1.0),
                                                 (0.75, 0.5), (1.0, 0.0)])
def test_score_auc(value, expected):
  assert score_value(_metric("table.detection_auc"),
                     value) == pytest.approx(expected)


def test_score_none_is_the_value_for_aggregates_only():
  assert score_value(_metric("table.fidelity_score"), 0.8) == 0.8
  assert score_value(_metric("table.column_shape_score"), 0.4) == 0.4
  assert score_value(_metric("model.overall_score"), 0.6) == 0.6
  assert score_value(_metric("table.overall_score"), 1.2) is None
  for metric_id in ("column.distinct_ceiling_hit", "column.wasserstein",
                    "column.source_stats_drift", "relationship.fanout_w1",
                    "relationship.orphan_rate_source"):
    assert score_value(_metric(metric_id), 0.5) is None, metric_id
    assert score_value(_metric(metric_id), 0.0) is None, metric_id


def test_score_integrity_zero_threshold_is_a_step():
  pk = _metric("table.pk_duplicate_rate")
  assert score_value(pk, 0.0) == 1.0
  assert score_value(pk, 1e-9) == 0.0


@pytest.mark.parametrize("value", [None, math.nan, math.inf, -math.inf])
def test_score_missing_or_non_finite_is_none(value):
  for metric_id in ("column.ks", "column.jsd", "column.std_ratio",
                    "table.detection_auc", "table.fidelity_score"):
    assert score_value(_metric(metric_id), value) is None


# ---------------------------------------------------------------------------
# status_for: thresholds, inclusive crossings, D5 noise floor
# ---------------------------------------------------------------------------


def test_status_value_none_is_not_evaluated():
  assert _status("column.ks", None) == Status.NOT_EVALUATED


@pytest.mark.parametrize(("value", "expected"), [
    (0.0499, Status.PASS),
    (0.05, Status.WARN),
    (0.0999, Status.WARN),
    (0.1, Status.FAIL),
    (0.5, Status.FAIL),
])
def test_status_inclusive_lower_better(value, expected):
  assert _status("column.jsd", value) == expected


@pytest.mark.parametrize(("value", "expected"), [
    (0.991, Status.PASS),
    (0.99, Status.WARN),
    (0.951, Status.WARN),
    (0.95, Status.FAIL),
    (0.2, Status.FAIL),
])
def test_status_inclusive_higher_better(value, expected):
  assert _status("field.category_adherence", value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.05, Status.PASS),
        (1.1, Status.WARN),
        # |0.9 - 1| is 0.09999999999999998 in floating point: still at warn.
        (0.9, Status.WARN),
        (1.25, Status.FAIL),
        (0.75, Status.FAIL),
    ])
def test_status_target_direction(value, expected):
  assert _status("column.std_ratio", value) == expected


@pytest.mark.parametrize(("value", "expected"), [
    (0.15, Status.PASS),
    (0.3, Status.WARN),
    (0.35, Status.FAIL),
    (0.0, Status.WARN),
])
def test_status_target_null_reads_source_value(value, expected):
  # novelty_mass: target = the source's own unseen-mass estimate (R9).
  assert _status("column.novelty_mass", value, source_value=0.1) == expected


def test_status_target_null_without_source_value_is_not_evaluated():
  mv = _mv("column.novelty_mass", 0.3)
  assert status_for(_metric(mv.metric_id), mv) == Status.NOT_EVALUATED
  assert "source_value" in _row_of(mv)["detail"]["reason"]


def test_status_null_thresholds_is_info():
  assert _status("column.wasserstein", 3.2) == Status.INFO
  assert _status("relationship.orphan_rate_source", 0.4) == Status.INFO


def test_status_noise_floor_downgrades_a_fail_to_pass():
  # D5: past fail (0.2) but inside the noise floor -> PASS; past both -> FAIL.
  assert _status("column.ks", 0.25, noise_floor=0.3) == Status.PASS
  assert _status("column.ks", 0.25, noise_floor=0.25) == Status.PASS
  assert _status("column.ks", 0.25, noise_floor=0.1) == Status.FAIL
  assert _status("column.ks", 0.25) == Status.FAIL  # no floor, no downgrade


def test_status_noise_floor_downgrades_a_warn_to_pass():
  assert _status("column.ks", 0.15, noise_floor=0.2) == Status.PASS
  assert _status("column.ks", 0.15, noise_floor=0.05) == Status.WARN


def test_status_noise_floor_non_finite_is_ignored():
  assert _status("column.ks", 0.25, noise_floor=math.nan) == Status.FAIL


def test_status_noise_reference_is_the_catalogue_target():
  # detection_auc target 0.5: |0.9 - 0.5| = 0.4 is what the floor sees.
  assert _status("table.detection_auc", 0.9, noise_floor=0.45) == Status.PASS
  assert _status("table.detection_auc", 0.9, noise_floor=0.3) == Status.FAIL


def test_status_noise_reference_null_target_higher_better_is_one():
  # row.coverage: fail 0.7, reference 1 -> |0.6 - 1| = 0.4.
  assert _status("row.coverage", 0.6, noise_floor=0.45) == Status.PASS
  assert _status("row.coverage", 0.6, noise_floor=0.3) == Status.FAIL


def test_status_lift_gates_on_ci_low():
  lift = "row.memorization_lift"
  assert _status(lift, 8.0, ci_low=1.5, ci_high=20.0) == Status.PASS
  assert _status(lift, 8.0, ci_low=2.0, ci_high=20.0) == Status.WARN
  assert _status(lift, 8.0, ci_low=5.0, ci_high=20.0) == Status.FAIL
  # an infinite point estimate (no holdout events) still gates on ci_low
  assert _status(lift, math.inf, ci_low=1.5, ci_high=math.inf) == Status.PASS


def test_status_lift_scores_the_bound_it_gates_on():
  row = _row_of(_mv("row.memorization_lift", 8.0, ci_low=3.5, ci_high=20.0))
  assert row["status"] == "warn"
  assert row["score"] == pytest.approx(0.5)  # linear on ci_low, not on 8
  assert row["value"] == 8.0


def test_status_holdout_share_gates_on_ci_low():
  share = "row.dcr_train_holdout_share"
  assert _status(share, 0.7, ci_low=0.52, ci_high=0.8) == Status.PASS
  assert _status(share, 0.7, ci_low=0.62, ci_high=0.8) == Status.FAIL


def test_status_ci_bound_missing_is_not_evaluated_with_reason():
  mv = _mv("row.memorization_lift", 8.0)
  assert status_for(_metric(mv.metric_id), mv) == Status.NOT_EVALUATED
  row = _row_of(mv)
  assert row["status"] == "not_evaluated"
  assert "ci_low" in row["detail"]["reason"]
  assert row["score"] is None


def test_status_integrity_zero_threshold():
  assert _status("table.pk_duplicate_rate", 1e-9) == Status.FAIL
  assert _status("table.pk_duplicate_rate", 0.0) == Status.PASS
  # integrity by construction: the noise floor is never applied
  assert _status(
      "table.pk_duplicate_rate", 1e-9, noise_floor=1.0) == Status.FAIL
  assert _status("relationship.orphan_rate", 0.02) == Status.FAIL


def test_status_documented_edge_is_info():
  kw = {"edge": "order_items.user_id->users.id"}
  assert _status(
      "relationship.orphan_rate", 0.3, enforced=False, **kw) == Status.INFO
  assert _status(
      "relationship.orphan_rate", 0.3, enforced=True, **kw) == Status.FAIL
  # only integrity metrics of the edge turn INFO; fan-out is still graded
  assert _status(
      "relationship.fanout_tvd", 0.5, enforced=False, **kw) == Status.FAIL


def test_documented_edge_row_is_info_with_null_score():
  row = _row_of(
      _mv("relationship.orphan_rate", 0.3,
          edge="order_items.user_id->users.id"),
      enforced=False)
  assert row["status"] == "info"
  assert row["score"] is None
  assert row["edge"] == "order_items.user_id->users.id"


@pytest.mark.parametrize(("ceiling", "value", "expected"), [
    (5.0, 2.0, Status.NOT_EVALUATED),
    (9.99, 12.0, Status.NOT_EVALUATED),
    (10.0, 2.0, Status.PASS),
    (50.0, 12.0, Status.FAIL),
])
def test_status_pmse_ceiling_below_fail_is_not_evaluated(
    ceiling, value, expected):
  assert _status(
      "table.pmse_ratio", value, detail={"ceiling": ceiling}) == expected


def test_pmse_ceiling_row_carries_reason_and_no_score():
  row = _row_of(_mv("table.pmse_ratio", 2.0, detail={"ceiling": 5.0, "k": 4}))
  assert row["status"] == "not_evaluated"
  assert row["detail"]["reason"] == "ceiling below fail threshold"
  assert row["detail"]["k"] == 4
  assert row["score"] is None


def test_status_non_finite_value_is_not_evaluated():
  assert _status("column.ks", math.nan) == Status.NOT_EVALUATED
  assert _status("column.ks", math.inf) == Status.NOT_EVALUATED
  row = _row_of(_mv("column.ks", math.inf))
  assert row["detail"]["reason"]
  assert row["value"] is None


def test_status_for_rejects_a_mismatched_metric():
  with pytest.raises(ValueError, match=r"column\.ks"):
    status_for(_metric("column.jsd"), _mv("column.ks", 0.1))


# ---------------------------------------------------------------------------
# to_metric_row
# ---------------------------------------------------------------------------


def test_row_keys_equal_the_schema_fields_exactly():
  row = _row_of(_mv("column.ks", 0.1, column="amount"))
  assert list(row) == list(schemas.field_names("evaluation_metrics"))


def test_row_maps_short_names_and_copies_the_catalogue():
  mv = _mv(
      "pair.pearson_delta",
      0.15,
      column="amount",
      column_2="quantity",
      column_kind="numeric",
      source_value=0.4,
      synthetic_value=0.55,
      noise_floor=0.05,
      n_source=1000,
      n_synthetic=900,
      method=Method.SAMPLE,
      sample_rate=0.5,
      encoding_plan_digest="abc",
      feature_set_digest="def",
  )
  row = _row_of(mv)
  metric = _metric("pair.pearson_delta")
  assert row["table_name"] == "orders"
  assert row["column_name"] == "amount"
  assert row["column_name_2"] == "quantity"
  assert row["edge"] is None
  assert row["level"] == "pair"
  assert row["family"] == "fidelity"
  assert row["metric_version"] == metric.version
  assert row["value_kind"] == metric.value_kind
  assert row["threshold_warn"] == metric.warn
  assert row["threshold_fail"] == metric.fail
  assert row["noise_floor_method"] == "fisher_z"
  assert row["noise_floor"] == 0.05
  assert row["status"] == "warn"
  assert row["score"] == pytest.approx(0.5)
  assert row["method"] == "sample"
  assert row["n_source"] == 1000
  assert row["evaluation_id"] == "eval-0001"
  assert row["evaluated_at"] == "2026-09-28T10:00:00.000000+00:00"
  assert row["landing_table"] == "demo-project.synthetic_data.orders"
  assert row["source_table"] == "demo-project.synthetic_source.orders"


def test_row_noise_floor_method_is_null_when_the_catalogue_has_none():
  row = _row_of(_mv("column.pit_w1", 0.01, column="amount"))
  assert row["noise_floor_method"] is None


def test_row_is_json_safe():
  mv = _mv(
      "column.ks",
      0.1,
      column="amount",
      source_value=math.nan,
      baseline_value=math.inf,
      ci_high=-math.inf,
      detail={
          "edges": [1.0, math.nan],
          "inner": {
              "x": math.inf
          }
      })
  row = _row_of(mv)
  text = json.dumps(row, allow_nan=False)  # raises on any NaN / Infinity
  assert row["source_value"] is None
  assert row["baseline_value"] is None
  assert row["ci_high"] is None
  assert row["detail"] == {"edges": [1.0, None], "inner": {"x": None}}
  assert "NaN" not in text


def test_row_not_evaluated_keeps_the_producers_reason():
  mv = MetricValue.not_evaluated(
      "column.ks", "orders", "column is constant", column="amount")
  row = _row_of(mv)
  assert row["status"] == "not_evaluated"
  assert row["detail"]["reason"] == "column is constant"
  assert row["score"] is None


def test_row_not_evaluated_always_has_a_reason():
  row = _row_of(_mv("column.ks", None, column="amount"))
  assert row["status"] == "not_evaluated"
  assert row["detail"]["reason"]


def test_row_score_none_for_non_aggregate_score_none_metric():
  row = _row_of(_mv("column.distinct_ceiling_hit", 1.0, column="city"))
  assert row["status"] == "fail"
  assert row["score"] is None
  assert _row_of(_mv("column.wasserstein", 0.4,
                     column="amount"))["score"] is None


def test_row_score_for_aggregate_is_its_value():
  row = _row_of(_mv("table.fidelity_score", 0.8))
  assert row["status"] == "warn"
  assert row["score"] == 0.8


# ---------------------------------------------------------------------------
# aggregate_scores (Ruling R11)
# ---------------------------------------------------------------------------


def _row(table: str,
         metric_id: str,
         score: float | None,
         *,
         column: str | None = None,
         edge: str | None = None,
         status: str = "pass") -> dict[str, Any]:
  metric = _metric(metric_id)
  return {
      "table_name": table,
      "level": metric.level,
      "family": metric.family,
      "metric_id": metric_id,
      "column_name": column,
      "column_name_2": None,
      "edge": edge,
      "score": score,
      "status": status,
  }


def test_aggregate_columns_weigh_equally():
  rows = [_row("t", "column.ks", 1.0, column="a") for _ in range(10)]
  rows.append(_row("t", "column.ks", 0.0, column="b"))
  rows.append(_row("t", "column.jsd", 0.5, column="c"))
  result = aggregate_scores(rows)
  # per column first: (1 + 0 + 0.5) / 3, not the row mean 10.5 / 12
  assert result["t"]["fidelity"] == pytest.approx(0.5)


def test_aggregate_field_and_column_metrics_share_the_column_unit():
  rows = [
      _row("t", "field.category_adherence", 1.0, column="a"),
      _row("t", "column.tvd", 0.0, column="a"),
      _row("t", "column.tvd", 1.0, column="b"),
  ]
  # column a = 0.5, column b = 1.0
  assert aggregate_scores(rows)["t"]["fidelity"] == pytest.approx(0.75)


def test_aggregate_units():
  rows = [
      _row("t", "column.ks", 1.0, column="x"),
      _row("t", "column.jsd", 1.0, column="x"),
      _row("t", "pair.pearson_delta", 0.0, column="x"),
      _row("t", "pair.spearman_delta", 1.0, column="x"),
      _row("t", "pair.cramers_v_delta", 1.0, column="y"),
      _row("t", "pair.nmi_delta", 1.0, column="z"),
      _row("t", "row.density", 0.5),
      _row("t", "table.detection_auc", 0.25),
      _row("t", "relationship.fanout_tvd", 1.0, edge="t.u_id->u.id"),
      _row("t", "relationship.fanout_mean_ratio", 0.0, edge="t.u_id->u.id"),
      # aggregate ids never feed themselves
      _row("t", "table.fidelity_score", 0.0),
      _row("t", "table.column_shape_score", 0.0),
      _row("t", "table.pair_trend_score", 0.0),
      _row("t", "model.fidelity_score", 0.0),
  ]
  # units: column x 1.0, pair group 0.75, row.density 0.5,
  # table.detection_auc 0.25, edge 0.5
  assert aggregate_scores(rows)["t"]["fidelity"] == pytest.approx(0.6)


def test_aggregate_never_includes_none():
  rows = [
      _row("t", "column.ks", 0.8, column="a"),
      _row("t", "column.jsd", None, column="a", status="not_evaluated"),
      _row("t", "column.ks", None, column="b", status="not_evaluated"),
      _row("t", "column.wasserstein", None, column="c", status="info"),
      _row("t", "row.exact_match_rate", None, status="not_evaluated"),
  ]
  result = aggregate_scores(rows)
  assert result["t"]["fidelity"] == pytest.approx(0.8)
  assert result["t"]["privacy"] is None
  assert result["t"]["integrity"] is None
  assert result["t"]["diversity"] is None
  assert result["t"]["overall"] == pytest.approx(0.8)
  assert result[MODEL_KEY]["privacy"] is None
  assert result[MODEL_KEY]["overall"] == pytest.approx(0.8)


def test_aggregate_overall_and_model():
  rows = [
      _row("orders", "column.ks", 1.0, column="a"),
      _row("orders", "row.exact_match_rate", 0.5),
      _row("users", "column.ks", 0.5, column="a"),
      _row("users", "column.coverage_mass", 0.0, column="a"),
  ]
  result = aggregate_scores(rows)
  assert result["orders"]["overall"] == pytest.approx(0.75)
  assert result["users"]["overall"] == pytest.approx(0.25)
  model = result[MODEL_KEY]
  assert model["fidelity"] == pytest.approx(0.75)
  assert model["privacy"] == pytest.approx(0.5)
  assert model["diversity"] == pytest.approx(0.0)
  assert model["integrity"] is None
  # overall = mean of the available model family scores
  assert model["overall"] == pytest.approx((0.75 + 0.5 + 0.0) / 3)


def test_aggregate_surfaces_integrity_fails_whatever_the_average():
  rows = [
      _row("orders", "table.pk_duplicate_rate", 0.0, status="fail"),
      _row("orders", "table.row_count_ratio", 1.0),
      _row("orders", "field.type_validity", 1.0, column="a"),
      _row("orders", "field.type_validity", 1.0, column="b"),
      _row(
          "orders",
          "relationship.orphan_rate",
          0.0,
          edge="orders.u->users.id",
          status="fail"),
      _row("orders", "table.integrity_score", 0.5, status="fail"),
      _row("users", "table.row_count_ratio", 1.0),
      _row("users", "column.ks", 0.0, column="a", status="fail"),
  ]
  result = aggregate_scores(rows)
  assert result["orders"]["integrity_fail"] == 2
  assert result["users"]["integrity_fail"] == 0
  assert result[MODEL_KEY]["integrity_fail"] == 2
  assert result["orders"]["integrity"] == pytest.approx(0.6)


def test_aggregate_empty_has_a_model_entry_of_nones():
  assert aggregate_scores([]) == {
      MODEL_KEY: {
          "fidelity": None,
          "privacy": None,
          "integrity": None,
          "diversity": None,
          "overall": None,
          "integrity_fail": 0,
      }
  }


def test_aggregate_is_order_independent():
  rows = [
      _row("t", "column.ks", 0.1, column="a"),
      _row("t", "column.ks", 0.2, column="b"),
      _row("t", "column.ks", 0.7, column="c"),
      _row("u", "column.ks", 0.3, column="a"),
  ]
  assert aggregate_scores(rows) == aggregate_scores(list(reversed(rows)))


def test_aggregate_from_real_rows_skips_a_documented_edge():
  rows = [
      _row_of(_mv("table.pk_duplicate_rate", 0.0)),
      _row_of(
          _mv("relationship.orphan_rate", 0.3, edge="orders.user_id->users.id"),
          enforced=False),
  ]
  result = aggregate_scores(rows)
  assert result["orders"]["integrity"] == 1.0
  assert result["orders"]["integrity_fail"] == 0


# ---------------------------------------------------------------------------
# headline_counts
# ---------------------------------------------------------------------------


def test_headline_counts_every_status_and_reconciles():
  statuses = ["pass", "pass", "warn", "fail", "info", "info", "not_evaluated"]
  rows = [{"status": status} for status in statuses]
  counts = headline_counts(rows)
  assert set(counts) == {status.value for status in Status} | {"total"}
  assert counts["total"] == len(rows)
  assert sum(counts[status.value] for status in Status) == counts["total"]
  assert counts["info"] == 2
  assert counts["not_evaluated"] == 1


def test_headline_counts_empty_has_every_key_at_zero():
  counts = headline_counts([])
  assert counts == {status.value: 0 for status in Status} | {"total": 0}


def test_headline_counts_rejects_an_unknown_status():
  with pytest.raises(ValueError, match="bogus"):
    headline_counts([{"status": "bogus"}])


# ---------------------------------------------------------------------------
# Property: every catalogue metric scores in [0, 1] or None
# ---------------------------------------------------------------------------

_ANY_FLOAT = st.one_of(st.none(),
                       st.floats(allow_nan=True, allow_infinity=True),
                       st.floats(min_value=-2.0, max_value=12.0))


@settings(max_examples=300, deadline=None)
@given(
    metric_id=st.sampled_from(CAT.ids()),
    value=_ANY_FLOAT,
    ci_low=_ANY_FLOAT,
    ci_high=_ANY_FLOAT,
    source_value=_ANY_FLOAT,
    noise_floor=_ANY_FLOAT,
    ceiling=_ANY_FLOAT,
    enforced=st.booleans(),
)
def test_property_score_in_unit_interval_or_none(metric_id, value, ci_low,
                                                 ci_high, source_value,
                                                 noise_floor, ceiling,
                                                 enforced):
  detail = {} if ceiling is None else {"ceiling": ceiling}
  mv = _mv(
      metric_id,
      value,
      ci_low=ci_low,
      ci_high=ci_high,
      source_value=source_value,
      noise_floor=noise_floor,
      detail=detail)
  row = _row_of(mv, enforced=enforced)
  json.dumps(row, allow_nan=False)
  assert row["status"] in {status.value for status in Status}
  score = row["score"]
  assert score is None or 0.0 <= score <= 1.0
  if row["status"] in ("not_evaluated", "info"):
    assert score is None
  if row["status"] == "not_evaluated":
    assert row["detail"]["reason"]
  aggregate = aggregate_scores([row])
  for family in _FAMILIES:
    rolled = aggregate[MODEL_KEY][family]
    assert rolled is None or 0.0 <= rolled <= 1.0
