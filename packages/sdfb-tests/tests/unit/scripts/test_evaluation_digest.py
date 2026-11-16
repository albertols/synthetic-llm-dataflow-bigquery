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
"""Unit tests for `scripts/e2e/evaluation_digest.py`.

The digest turns stored evaluation rows into the numbers an assessment
reads, and writes an `oss/` twin in which every table, column, edge and
relationship model is an alias. Every row here is invented (`demo-project`,
thelook-shaped names); the reason strings are the evaluator's own.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).parents[5] / "scripts" / "e2e" / "evaluation_digest.py")
_spec = importlib.util.spec_from_file_location("evaluation_digest", _SCRIPT)
dg = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dg
_spec.loader.exec_module(dg)

SRC = "demo-project.shop"
LANDING = "demo-project.synthetic_data"
CATALOGUE = dg.load_catalogue(dg.DEFAULT_CATALOGUE)


def evaluation(evaluation_id="eval-1",
               engine="b1_rag",
               tables=("orders",),
               status="SUCCEEDED",
               evaluated_at="2026-11-15T10:00:00+00:00",
               **extra):
  return {
      "evaluation_id": evaluation_id,
      "evaluated_at": evaluated_at,
      "status": status,
      "engine": engine,
      "mode": "exact",
      "trigger": "chained",
      "catalogue_version": "1.0.0",
      "evaluator_version": "0.1.0",
      "relationship_model": "shop_model",
      "overall_score": 0.85,
      "tables": [{
          "name": name,
          "source_table": f"{SRC}.{name}",
          "landing_table": f"{LANDING}.{name}",
          "run_id": f"run-{evaluation_id}-{name}",
          "role": "root",
          "scope_status": "ok",
          "reference_n": 10000,
      } for name in tables],
      **extra,
  }


def row(metric_id, status, table="orders", evaluation_id="eval-1", **extra):
  level = metric_id.split(".", 1)[0]
  families = {
      "row.memorization_lift": "privacy",
      "row.near_match_rate": "privacy",
      "row.near_match_lift": "privacy",
      "row.dcr_train_holdout_share": "privacy",
      "table.pk_duplicate_rate": "integrity",
      "table.identity_duplicate_rate": "integrity",
      "relationship.orphan_rate": "integrity",
      "column.distinct_ratio": "diversity",
  }
  return {
      "evaluation_id": evaluation_id,
      "table_name": table,
      "source_table": f"{SRC}.{table}",
      "landing_table": f"{LANDING}.{table}",
      "level": level,
      "family": families.get(metric_id, "fidelity"),
      "metric_id": metric_id,
      "status": status,
      "detail": {},
      **extra,
  }


def digest_of(evaluations, rows, **criteria):
  loaded = dg.Loaded(list(evaluations), list(rows))
  base = {"latest_per": "table", **criteria}
  return dg.build_digest(
      loaded, base, backends={"b2_library": "empirical"}, catalogue=CATALOGUE)


# --------------------------------------------------------------------------
# readings of one row
# --------------------------------------------------------------------------
def test_rollup_ids_are_the_scores_and_every_model_id():
  assert dg.is_rollup("table.fidelity_score")
  assert dg.is_rollup("model.overall_score")
  assert not dg.is_rollup("table.detection_auc")
  assert not dg.is_rollup("column.ks")


def test_gate_reads_the_ci_bound_of_a_copying_test():
  lift = row(
      "row.memorization_lift", "fail", value=40.0, ci_low=6.0, ci_high=90.0)
  assert dg.gate(lift, CATALOGUE["row.memorization_lift"]) == 6.0


def test_gate_of_a_target_metric_is_the_distance_to_its_target():
  ratio = row("column.std_ratio", "fail", value=0.6, threshold_fail=0.25)
  assert dg.gate(ratio, CATALOGUE["column.std_ratio"]) == pytest.approx(0.4)
  assert dg.excess(ratio, CATALOGUE["column.std_ratio"]) == pytest.approx(0.6)


def test_excess_of_a_higher_is_better_metric_grows_as_the_value_drops():
  low = row("column.coverage_mass", "fail", value=0.4, threshold_fail=0.8)
  assert dg.excess(low, CATALOGUE["column.coverage_mass"]) == pytest.approx(0.5)


def test_baseline_limited_when_the_reference_sample_already_warns():
  meta = CATALOGUE["column.tvd"]
  limited = row(
      "column.tvd", "fail", value=0.30, baseline_value=0.28, threshold_warn=0.1)
  engine = row(
      "column.tvd", "fail", value=0.30, baseline_value=0.02, threshold_warn=0.1)
  assert dg.baseline_limited(limited, meta) is True
  assert dg.baseline_limited(engine, meta) is False
  assert dg.baseline_limited(row("column.tvd", "fail", value=0.3), meta) is None


@pytest.mark.parametrize(("reason", "expected"), [
    ("no primary key declared in the relationship model", "no_key_declared"),
    ("sampled mode cannot measure exact duplicates; run exact mode",
     "sampled_mode"),
    ("reference sample not verified: no reference digest recorded for this "
     "run (validation_runs.reference_digest is empty)",
     "reference_not_verified"),
    ("no reference panel (R/H) was read for this table", "no_reference_panel"),
    ("no free-text pool side input: the CLI reads the launch's "
     "freetext_pools_table", "no_pool"),
    ("the launch records no --source_stats tier", "source_stats_missing"),
    ("R and H hold 600 rows each, fewer than the 2,000 the nearest-neighbour "
     "metrics need", "panel_too_small"),
    ("no value census for this column (census: none — the planner censuses "
     "every non-key, non-nested column)", "no_census"),
    ("no rare (source count < 10), substantive value is held only by the "
     "reference sample R, or none only by the holdout H",
     "no_exclusive_records"),
    ("fewer than k records on a side (k = 10, R80)", "too_few_to_measure"),
    ("no rows on the synthetic side", "empty_side"),
    ("a pair column is constant on the source side: the association is "
     "undefined", "constant_or_undefined"),
    ("the key columns differ in type: INT64 against NUMERIC",
     "edge_type_mismatch"),
    ("privacy could not be computed for this table (MemoryError)", "failure"),
    ("the column is day-granular (no time part)", "day_granular_or_time"),
    ("something nobody has seen before", "other"),
])
def test_not_evaluated_reasons_are_classified(reason, expected):
  assert dg.classify_reason(reason)[0] == expected


def test_a_reason_class_says_what_kind_of_gap_it_is():
  assert dg.classify_reason(
      "sampled mode cannot measure x; run exact mode")[1] == "evaluation_setup"
  assert dg.classify_reason("no primary key declared in the relationship model"
                           )[1] == ("not_applicable")


# --------------------------------------------------------------------------
# selection and slices
# --------------------------------------------------------------------------
def test_running_and_failed_evaluations_are_left_out_with_the_reason():
  keep, excluded = dg.select([
      evaluation("eval-ok"),
      evaluation("eval-running", status="RUNNING"),
      evaluation("eval-failed", status="FAILED"),
      evaluation("eval-b2", engine="b2_library"),
  ], {"engines": ["b1_rag"]})
  assert [e["evaluation_id"] for e in keep] == ["eval-ok"]
  assert {
      e["evaluation_id"]: e["status"] for e in excluded
  } == {
      "eval-running": "RUNNING",
      "eval-failed": "FAILED"
  }  # the other engine is outside the filter: dropped without a reason


def test_b2_library_is_labelled_with_the_backend_the_operator_states():
  b2 = evaluation(engine="b2_library")
  assert dg.engine_effective(
      b2, {"b2_library": "empirical"}) == ("b2_library[empirical]")
  assert dg.engine_effective(b2, {}) == "b2_library[backend unverified]"
  assert dg.engine_effective(evaluation(), {}) == "b1_rag"


@pytest.mark.parametrize(("old_id", "new_id"), [("eval-a", "eval-b"),
                                                ("eval-b", "eval-a")])
def test_status_is_read_from_the_latest_evaluation_of_a_table_and_engine(
    old_id, new_id):
  # Both id orders: "latest" is the evaluation time, never the id's rank.
  old = evaluation(old_id, evaluated_at="2026-11-10T10:00:00+00:00")
  new = evaluation(new_id, evaluated_at="2026-11-15T10:00:00+00:00")
  rows = [
      row("column.ks", "fail", evaluation_id=old_id, column_name="amount"),
      row("column.ks", "pass", evaluation_id=new_id, column_name="amount"),
  ]
  digest, slices = digest_of([old, new], rows)
  assert len(slices) == 2
  assert digest["fleet"]["table_slices_current"] == 1
  assert [c["evaluation_id"] for c in digest["tables"]] == [new_id]
  flips = digest["cross_run"]["status_flips"][0]
  assert (flips["from_evaluation_id"], flips["to_evaluation_id"]) == (old_id,
                                                                      new_id)
  assert flips["improved_by_metric"] == {"column.ks": 1}
  assert flips["regressed_rows"] == 0
  assert len(digest["cross_run"]["history"][0]["evaluations"]) == 2

  every, _ = digest_of([old, new], rows, latest_per="none")
  assert every["fleet"]["table_slices_current"] == 2


def test_two_engines_on_one_table_are_put_side_by_side():
  digest, _ = digest_of(
      [evaluation("eval-b1"),
       evaluation("eval-b2", engine="b2_library")], [
           row("column.ks", "pass", evaluation_id="eval-b1"),
           row("column.ks", "fail", evaluation_id="eval-b2"),
       ])
  pair = digest["cross_run"]["engines_side_by_side"][0]
  assert [e["engine"] for e in pair["engines"]
         ] == ["b1_rag", "b2_library[empirical]"]


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------
def test_headline_counts_follow_the_gui():
  rows = [
      row("pair.spearman_delta", "fail", column_name="a", column_name_2="b"),
      row("pair.nmi_delta", "fail", column_name="a", column_name_2="b"),
      row("column.ks", "fail", column_name="a"),
      row("column.ks", "warn", column_name="b"),
      row("column.tvd",
          "pass",
          column_name="c",
          detail={"noise_downgraded_from": "fail"}),
      row("column.wasserstein", "info", column_name="a"),
      row("table.pk_duplicate_rate",
          "not_evaluated",
          detail={
              "reason": "no primary key declared in the relationship model"
          }),
      row("row.memorization_lift",
          "not_evaluated",
          detail={"reason": "no reference panel (R/H) was read"}),
      row("table.fidelity_score", "warn", value=0.8),  # a roll-up: not counted
  ]
  digest, _ = digest_of([evaluation()], rows)
  head = digest["selection"]["evaluations"][0]["headline"]
  assert head["measured"] == 8
  assert (head["fail"], head["warn"], head["pass"], head["info"],
          head["not_evaluated"]) == (3, 1, 1, 1, 2)
  assert head["not_applicable_keys"] == 1
  assert head["noise_downgraded"] == 1
  assert head["privacy_not_evaluated"] == 1
  assert (head["top_fail_family"],
          head["top_fail_family_fails"]) == ("fidelity", 3)
  assert head["top_fail_level"] == "pair"


@pytest.mark.parametrize(("fail", "warn", "unproven", "capped", "expected"), [
    (0.0, 0.05, 0.0, None, "4"),
    (0.0, 0.30, 0.0, None, "3"),
    (0.04, 0.0, 0.0, None, "3"),
    (0.15, 0.0, 0.0, None, "2"),
    (0.40, 0.0, 0.0, None, "1"),
    (0.80, 0.0, 0.0, None, "0"),
    (0.0, 0.0, 0.6, None, "U"),
    (None, None, 1.0, None, "U"),
    (0.0, 0.0, 0.0, 0.02, "2"),
    (0.0, 0.0, 0.0, 0.50, "1"),
    (0.40, 0.0, 0.0, 0.02, "1"),
])
def test_rubric_grade(fail, warn, unproven, capped, expected):
  assert dg.grade(fail, warn, unproven, capped)[0] == expected


def test_a_cell_counts_the_tables_behind_its_grade():
  evaluations = [evaluation(tables=("orders", "users"))]
  rows = [
      row("table.pk_duplicate_rate", "fail", value=0.01),
      row("table.pk_duplicate_rate", "pass", table="users", value=0.0),
      row("table.identity_duplicate_rate",
          "not_evaluated",
          detail={
              "reason": "no identity columns declared in the relationship "
                        "model"
          }),
  ]
  digest, _ = digest_of(evaluations, rows)
  cell = next(c for c in digest["cells"]
              if (c["level"], c["family"]) == ("table", "integrity"))
  assert cell["tables_measured"] == 2
  assert cell["tables_with_capping_fail"] == 1
  assert cell["not_applicable_keys"] == 1
  assert cell["unproven_share"] == 0.0  # a missing key is not a gap
  assert cell["grade"] == "1"  # 1 of 2 tables has an enforced-key FAIL
  assert cell["evidence"] == "full"


def test_a_signature_says_how_widely_it_fails_and_why():
  tables = ("t1", "t2", "t3", "t4")
  rows = []
  for name, baseline in zip(tables, (0.30, 0.02, 0.02, 0.02), strict=True):
    rows.append(
        row("column.tvd",
            "fail" if name != "t4" else "pass",
            table=name,
            column_name="status",
            column_kind="categorical",
            value=0.3 if name != "t4" else 0.01,
            baseline_value=baseline,
            threshold_warn=0.1,
            threshold_fail=0.2))
  digest, _ = digest_of([evaluation(tables=tables)], rows)
  signature = digest["signatures"][0]
  assert (signature["metric_id"], signature["column_kind"]) == ("column.tvd",
                                                                "categorical")
  assert signature["tables_with_fail"] == 3
  assert signature["prevalence"] == 0.75
  assert signature["scope"] == "systemic"
  assert signature["fails_with_baseline"] == 3
  assert signature["baseline_limited_share"] == pytest.approx(1 / 3, abs=1e-6)


def test_a_signature_with_no_crossing_is_only_named():
  digest, _ = digest_of(
      [evaluation()],
      [row("column.ks", "pass", column_name="a", column_kind="numeric")])
  assert digest["signatures"] == []
  assert digest["signatures_clean"] == [{
      "engine": "b1_rag",
      "clean": ["column.ks:numeric"]
  }]


def test_pairs_tell_a_lost_dependence_from_an_invented_one():

  def pair(metric, a, b, source, synthetic, status):
    return row(
        metric,
        status,
        column_name=a,
        column_name_2=b,
        source_value=source,
        synthetic_value=synthetic)

  rows = [
      row("column.ks", "pass", column_name="qty", column_kind="numeric"),
      row("column.ks", "pass", column_name="amount", column_kind="numeric"),
      row("column.tvd", "pass", column_name="city", column_kind="categorical"),
      row("column.tvd",
          "pass",
          column_name="country",
          column_kind="categorical"),
      pair("pair.spearman_delta", "qty", "amount", 0.72, 0.01, "fail"),
      pair("pair.cramers_v_delta", "city", "country", 0.95, 0.03, "fail"),
      pair("pair.cramers_v_delta", "city", "qty", 0.02, 0.40, "fail"),
      pair("pair.cramers_v_delta", "country", "qty", 0.03, 0.02, "pass"),
  ]
  digest, _ = digest_of([evaluation()], rows)
  by_kind = {
      (p["metric_id"], p["kind_pair"]): p for p in digest["pairs"]["by_kind"]
  }
  numeric = by_kind[("pair.spearman_delta", "numeric x numeric")]
  assert (numeric["lost"], numeric["source_dependent"],
          numeric["dependent_fail_share"]) == (1, 1, 1.0)
  mixed = by_kind[("pair.cramers_v_delta", "categorical x numeric")]
  assert (mixed["invented"], mixed["kept"]) == (1, 1)
  assert mixed["independent_fail_share"] == 0.5
  strong = digest["pairs"]["strong_source_pairs"]
  assert [(p["column"], p["column_2"], p["reading"]) for p in strong
         ] == [("city", "country", "lost"), ("qty", "amount", "lost")]


def test_an_absolute_rate_is_read_next_to_its_lift():
  rows = [
      row("row.near_match_rate", "fail", value=0.2),
      row("row.near_match_lift", "pass", value=1.0, ci_low=0.9),
      row("row.dcr_train_holdout_share",
          "not_evaluated",
          detail={"reason": "no reference panel"}),
  ]
  digest, _ = digest_of([evaluation()], rows)
  reading = digest["privacy_readings"][0]
  assert "chance-level" in reading["readings"][0]
  assert reading["row_copying_tests_not_run"] == ["row.dcr_train_holdout_share"]


def test_not_evaluated_rows_are_grouped_by_what_closes_them():
  reason = "sampled mode cannot measure exact duplicates; run exact mode"
  rows = [
      row("table.pk_duplicate_rate", "not_evaluated",
          detail={"reason": reason}),
      row("row.near_match_rate", "not_evaluated", detail={"reason": reason}),
  ]
  digest, _ = digest_of([evaluation()], rows)
  groups = {
      (g["reason_class"], g["family"]): g for g in digest["not_evaluated"]
  }
  assert set(groups) == {("sampled_mode", "integrity"),
                         ("sampled_mode", "privacy")}
  assert groups[("sampled_mode",
                 "privacy")]["closes_with"].endswith("--mode exact")
  assert groups[("sampled_mode", "privacy")]["reason_examples"] == [reason]


def test_an_edge_card_carries_each_relationship_metric():
  label = "orders(user_id) -> users(id)"
  rows = [
      row("relationship.orphan_rate",
          "fail",
          edge=label,
          value=0.02,
          detail={"enforced": True}),
      row("relationship.fanout_mean_ratio",
          "pass",
          edge=label,
          value=1.01,
          detail={"enforced": True}),
  ]
  digest, _ = digest_of([evaluation()], rows)
  card = digest["edges"][0]
  assert card["enforced"] is True
  assert card["metrics"]["orphan_rate"] == {"value": 0.02, "status": "fail"}
  assert digest["offenders"]["relationship"][0]["edge"] == label


def test_offenders_rank_enforced_keys_first_and_cap_a_table():
  rows = [
      row("column.ks",
          "fail",
          column_name=f"c{i}",
          value=0.9,
          threshold_fail=0.2) for i in range(8)
  ] + [
      row("column.ks",
          "fail",
          table="users",
          column_name="x",
          value=0.3,
          threshold_fail=0.2)
  ]
  digest, _ = digest_of([evaluation(tables=("orders", "users"))], rows)
  listed = digest["offenders"]["column"]
  assert sum(r["table"] == "orders" for r in listed) == 5  # per-table cap
  assert any(r["table"] == "users" for r in listed)
  assert listed[0]["excess"] == pytest.approx(3.5)


def test_a_table_card_carries_the_generators_own_verdict():
  loaded = dg.Loaded([evaluation()], [
      row("table.pk_duplicate_rate", "fail", value=0.1),
      row("table.fidelity_score", "fail", value=0.6),
  ],
                     validation_runs=[{
                         "run_id": "run-eval-1-orders",
                         "status": "PASSED",
                         "valid_count": 1000,
                         "dlq_count": 3,
                         "dlq_by_rule": '{"pk.duplicate": 3, "Not A Rule!": 9}',
                         "excluded_blocker_rules": ["pk.duplicate"],
                         "status_reason": "free text that must stay out",
                     }])
  digest, _ = dg.build_digest(
      loaded, {"latest_per": "table"}, backends={}, catalogue=CATALOGUE)
  card = digest["tables"][0]
  assert card["scores"]["fidelity"] == 0.6
  assert card["zero_tolerance_fails"] == ["table.pk_duplicate_rate"]
  assert card["validation"] == {
      "status": "PASSED",
      "valid_count": 1000,
      "dlq_count": 3,
      "dlq_by_rule": {
          "pk.duplicate": 3
      },
      "excluded_blocker_rules": ["pk.duplicate"],
  }  # the gate passed a table whose key the evaluator fails


def test_knobs_are_scalars_and_never_a_uri():
  entry = dg.evaluation_entry(
      evaluation(
          similarity=0.5,
          reference_rows_limit=10000,
          generator_version="0.6.1-feat/secret-branch-abc1234-dirty",
          llm_model_uri="gs://corp-bucket/synthetic/models/gemma/4b/v1/",
          generation_params={
              "values": {
                  "sampling": {
                      "reference_rows_limit": 10000,
                      "source_stats": "exact"
                  },
                  "io": {
                      "source_table": "demo-project.shop.orders",
                      "model_uri": "gs://corp-bucket/x"
                  },
                  "free_text": {
                      "free_text_pool_max": 512
                  },
              }
          }), {}, [])
  assert entry["knobs"] == {
      "free_text.free_text_pool_max": 512,
      "reference_rows_limit": 10000,
      "sampling.reference_rows_limit": 10000,
      "sampling.source_stats": "exact",
      "similarity": 0.5,
  }
  assert entry["generator_version"] == "0.6.1-abc1234-dirty"
  assert entry["llm_model"] == "gemma/4b/v1"


# --------------------------------------------------------------------------
# aliases and the oss/ twin
# --------------------------------------------------------------------------
def _aliased(digest, slices):
  aliaser = dg.Aliaser(dg.history_mappings.HistoryMappings())
  names = dg.register_tables(
      slices, {f"{SRC}.orders": ["order_id", "user_id", "status"]}, aliaser)
  return dg.to_oss(digest, aliaser), aliaser, names


def test_every_table_of_the_digest_gets_its_own_aliases():
  rows = [
      row("column.ks", "fail", column_name="status", value=0.9),
      row("column.ks", "fail", table="users", column_name="status", value=0.9),
      row("relationship.orphan_rate",
          "fail",
          edge="orders(user_id) -> users(id)",
          value=0.1),
      row("relationship.orphan_rate",
          "fail",
          table="users",
          edge="users(region_id) -> ref.regions(id)",
          value=0.1),
  ]
  digest, slices = digest_of([evaluation(tables=("orders", "users"))], rows)
  oss, aliaser, names = _aliased(digest, slices)
  column = {(r["table"], r["column"]) for r in oss["offenders"]["column"]}
  # DDL order numbers `orders`; the same column name differs per table.
  assert column == {("A_TABLE", "A_COL_003"), ("B_TABLE", "B_COL_001")}
  edges = {r["edge"] for r in oss["offenders"]["relationship"]}
  assert "A_TABLE(A_COL_002) -> B_TABLE(B_COL_002)" in edges
  assert "B_TABLE(B_COL_003) -> EXTERNAL_TABLE_01(KEY_1)" in edges
  assert oss["selection"]["evaluations"][0]["relationship_model"] == (
      "MODEL_01")
  text = "\n".join(dg.strings(oss))
  for real in ("orders", "users", "demo-project", "shop_model", "regions",
               "user_id"):
    assert real not in text
  assert "table_ref" not in json.dumps(oss)
  alias_map = aliaser.alias_map(names, names)
  assert alias_map["tables"][0]["columns"]["status"] == "A_COL_003"


def test_a_string_that_is_not_allow_listed_stops_the_oss_twin():
  aliaser = dg.Aliaser(dg.history_mappings.HistoryMappings())
  with pytest.raises(dg.LeakError):
    dg.to_oss({"comment": "the customer's name, verbatim!"}, aliaser)
  with pytest.raises(dg.LeakError):
    dg.to_oss({"table": "orders"}, aliaser)  # no table to alias against


def test_scan_reads_names_as_words_and_identifiers_as_substrings():
  tokens = {
      "identifiers": {"demo-project", "demo-project.shop.orders"},
      "tables": {"orders"},
      "columns": {"status"}
  }
  hits = dg.scan_text("reorders in `demo-project.shop`; status ok", tokens)
  assert hits == {
      "identifiers": ["demo-project"],
      "tables": [],
      "columns": ["status"]
  }
  assert dg.scan_text("orders", tokens, allow=["orders"])["tables"] == []


def test_decode_puts_the_real_names_back():
  alias_map = {
      "tables": [{
          "table": "orders",
          "alias": "A_TABLE",
          "columns": {
              "status": "A_COL_003"
          }
      }],
      "models": {
          "shop_model": "MODEL_01"
      },
  }
  assert dg.decode_text("A_TABLE.A_COL_003 of MODEL_01, not A_COL_999",
                        alias_map) == ("orders.status of shop_model, "
                                       "not A_COL_999")


def test_prune_and_render_keep_sections_and_inline_records():
  digest = {"cells": [], "tables": [{"table": "t", "column": None, "x": {}}]}
  pruned = dg.prune(digest)
  assert pruned == {"cells": [], "tables": [{"table": "t"}]}
  text = dg.render(pruned)
  assert json.loads(text) == pruned
  assert '{"table": "t"}' in text  # one record a line


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------
def test_sql_is_parameterized_and_refuses_a_hostile_identifier():
  sql, params = dg.metrics_sql("demo-project", "quality", ["eval-1"], "lo",
                               "hi")
  assert "`demo-project.quality.evaluation_metrics`" in sql
  assert "evaluation_id IN UNNEST(@ids)" in sql
  assert "evaluated_at >= @lo AND evaluated_at <= @hi" in sql
  assert params == {"ids": ["eval-1"], "lo": "lo", "hi": "hi"}
  sql, params = dg.registry_sql(
      "demo-project", "quality", ids=[], window=("a", "b"))
  assert sql.endswith(
      "WHERE evaluated_at >= @from_ts AND evaluated_at < @to_ts")
  with pytest.raises(SystemExit):
    dg.quoted("demo-project", "quality`; DROP TABLE x; --")
  with pytest.raises(SystemExit):
    dg.quoted_fqn("only.two")


def test_load_bq_reads_only_the_selected_evaluations():
  args = dg.build_parser().parse_args([
      "digest", "--project", "demo-project", "--since-days", "7", "--engine",
      "b1_rag", "--validation-runs-table",
      "demo-project.quality.validation_runs"
  ])
  asked = []

  def query(client, sql, params):
    assert client is not None
    asked.append((sql, params))
    if "evaluation_latest" in sql:
      return [evaluation("eval-b1"), evaluation("eval-b2", engine="b2_library")]
    if "evaluation_metrics" in sql:
      return [row("column.ks", "pass", evaluation_id="eval-b1")]
    if "validation_runs" in sql:
      return [{"run_id": "run-eval-b1-orders", "status": "PASSED"}]
    return [{"table_name": "orders", "column_name": "order_id"}]

  loaded = dg.load_bq(object(), args, query)
  metrics = next(p for s, p in asked if "evaluation_metrics" in s)
  assert metrics["ids"] == ["eval-b1"]  # the other engine is never read
  assert loaded.validation_runs[0]["status"] == "PASSED"
  assert loaded.ddl_columns == {f"{SRC}.orders": ["order_id"]}


# --------------------------------------------------------------------------
# the commands, end to end
# --------------------------------------------------------------------------
def _export(tmp_path, name, evaluation_row, rows):
  path = tmp_path / f"{name}.json"
  path.write_text(json.dumps({"evaluation": evaluation_row, "metrics": rows}))
  return path


def test_digest_scan_show_and_decode_end_to_end(tmp_path, capsys):
  export = _export(tmp_path, "run", evaluation(tables=("orders", "users")), [
      row("column.ks", "fail", column_name="status", value=0.9),
      row("column.ks", "pass", table="users", column_name="age"),
  ])
  root = tmp_path / "reports"
  registry = tmp_path / "history.json"
  assert dg.main([
      "digest", "--report-json",
      str(export), "--out-root",
      str(root), "--history-mappings",
      str(registry), "--report-id", "rep"
  ]) == 0
  real = json.loads((root / "rep/real/evaluation_digest.json").read_text())
  oss_text = (root / "rep/oss/evaluation_digest.json").read_text()
  assert real["tables"][0]["table"] in ("orders", "users")
  assert "orders" not in oss_text and "demo-project" not in oss_text
  assert json.loads(oss_text)["space"] == "oss"
  assert json.loads(registry.read_text())["tables"][0]["alias"] == "A_TABLE"

  scan = [
      "scan", "--dir",
      str(root / "rep/oss"), "--alias-map",
      str(root / "rep/real/alias_map.json")
  ]
  assert dg.main(scan) == 0
  (root / "rep/oss/report.md").write_text("A_TABLE is fine; orders is not.\n")
  assert dg.main(scan) == 1
  assert dg.main([*scan, "--allow", "orders"]) == 0

  capsys.readouterr()
  assert dg.main([
      "show", "--digest",
      str(root / "rep/oss/evaluation_digest.json"), "--section",
      "offenders.column", "--where", "status=fail"
  ]) == 0
  shown = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
  assert [r["table"] for r in shown] == ["A_TABLE"]

  assert dg.main([
      "decode", "--alias-map",
      str(root / "rep/real/alias_map.json"), "--in",
      str(root / "rep/oss/report.md"), "--out",
      str(root / "rep/real/report.md")
  ]) == 0
  assert (root / "rep/real/report.md").read_text().startswith("orders is fine")


def test_no_readable_evaluation_is_an_error_not_an_empty_digest(tmp_path):
  export = _export(tmp_path, "running", evaluation(status="RUNNING"), [])
  with pytest.raises(SystemExit):
    dg.main([
        "digest", "--report-json",
        str(export), "--out-root",
        str(tmp_path / "reports"), "--history-mappings",
        str(tmp_path / "history.json")
    ])
  assert not (tmp_path / "reports").exists()
