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
"""Unit tests for `scripts/e2e/evaluation_probes.py`.

The probes measure row coherence the metric catalogue has no metric for
(the order of two timestamps in a row, a parent's against its child's,
combinations of categories the source never holds). BigQuery is a fake
here: the SQL is checked as text and the readings from canned aggregates.
Every name is invented.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).parents[5] / "scripts" / "e2e"
_spec = importlib.util.spec_from_file_location("evaluation_probes",
                                               _DIR / "evaluation_probes.py")
pr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pr
_spec.loader.exec_module(pr)

SRC = "demo-project.shop"
LANDING = "demo-project.synthetic_data"


def card(table, pair_fails=0, overall=0.9):
  return {
      "evaluation_id": "eval-1",
      "engine": "b1_rag",
      "table": table,
      "table_ref": f"{SRC}.{table}",
      "source_table": f"{SRC}.{table}",
      "landing_table": f"{LANDING}.{table}",
      "scores": {
          "overall": overall
      },
      "fail_by_level": {
          "pair": pair_fails
      },
  }


DIGEST = {
    "tables": [card("users"),
               card("orders", pair_fails=4, overall=0.8)],
    "edges": [{
        "evaluation_id": "eval-1",
        "table": "orders",
        "table_ref": f"{SRC}.orders",
        "edge": "orders(user_id) -> users(id)",
    }],
    "pairs": {
        "strong_source_pairs": [{
            "evaluation_id": "eval-1",
            "table": "orders",
            "table_ref": f"{SRC}.orders",
            "column": "city",
            "column_2": "country",
            "kind_pair": "categorical x categorical",
            "source_value": 0.97,
        }, {
            "evaluation_id": "eval-1",
            "table": "orders",
            "table_ref": f"{SRC}.orders",
            "column": "num_of_item",
            "column_2": "amount",
            "kind_pair": "numeric x numeric",
            "source_value": 0.72,
        }]
    },
}
ALIAS_MAP = {
    "identifiers": {
        "projects": ["demo-project"],
        "datasets": ["shop"]
    },
    "models": {},
    "external_tables": {},
    "tables": [{
        "table_ref": f"{SRC}.orders",
        "table": "orders",
        "alias": "A_TABLE",
        "columns": {}
    }, {
        "table_ref": f"{SRC}.users",
        "table": "users",
        "alias": "B_TABLE",
        "columns": {}
    }],
}
COLUMNS = {
    "orders": [("order_id", "INT64"), ("user_id", "INT64"),
               ("created_at", "TIMESTAMP"), ("shipped_at", "TIMESTAMP"),
               ("city", "STRING"), ("country", "STRING")],
    "users": [("id", "INT64"), ("created_at", "TIMESTAMP")],
}


def fake_query(answers):
  """A BigQuery stand-in: schema lookups from COLUMNS, aggregates from
  `answers` (a function of the SQL)."""
  asked = []

  def query(client, sql, params):
    assert client is not None
    asked.append(sql)
    if "INFORMATION_SCHEMA" in sql:
      return [{
          "column_name": name,
          "data_type": kind
      } for name, kind in COLUMNS[params["table"]]]
    return [answers(sql)]

  query.asked = asked
  return query


# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------
def test_temporal_order_sql_counts_every_pair_in_one_scan():
  sql = pr.temporal_order_sql(f"{SRC}.orders", ["a", "b", "c"])
  assert sql.startswith("SELECT COUNT(*) AS n, ")
  assert sql.endswith("FROM `demo-project.shop.orders`")
  assert ("COUNTIF(CAST(`a` AS TIMESTAMP) < CAST(`b` AS TIMESTAMP)) AS lt_0_1"
          in sql)
  assert sql.count(" AS both_") == 3
  assert "TABLESAMPLE" not in sql
  assert "TABLESAMPLE SYSTEM (5 PERCENT)" in pr.temporal_order_sql(
      f"{SRC}.orders", ["a", "b"], 5)


def test_parent_child_sql_joins_on_the_edge_keys():
  sql = pr.parent_child_order_sql(f"{SRC}.orders", f"{SRC}.users",
                                  [("user_id", "id")], ["created_at"],
                                  ["created_at"])
  assert ("FROM `demo-project.shop.orders` AS c JOIN "
          "`demo-project.shop.users` AS p ON c.`user_id` = p.`id`") in sql
  assert ("COUNTIF(CAST(p.`created_at` AS TIMESTAMP) <= "
          "CAST(c.`created_at` AS TIMESTAMP)) AS le_0_0") in sql


def test_combination_sql_counts_pairs_the_source_never_holds():
  sql = pr.combination_sql(f"{SRC}.orders", f"{LANDING}.orders", "city",
                           "country")
  assert "SELECT DISTINCT CAST(`city` AS STRING) AS a" in sql
  assert "FROM `demo-project.shop.orders` WHERE" in sql
  assert "COUNTIF(s.a IS NULL) AS unseen" in sql
  assert "LEFT JOIN src AS s ON y.a = s.a AND y.b = s.b" in sql


@pytest.mark.parametrize("name", ["a`; DROP TABLE x; --", "with space", ""])
def test_a_column_that_is_not_a_plain_identifier_is_refused(name):
  with pytest.raises(ValueError, match="plain identifier"):
    pr.temporal_order_sql(f"{SRC}.orders", ["ok", name])


def test_sample_sql_reads_a_block_sample_not_the_table():
  assert pr.sample_sql(
      f"{LANDING}.orders", 40,
      1.0) == ("SELECT * FROM `demo-project.synthetic_data.orders` "
               "TABLESAMPLE SYSTEM (1 PERCENT) LIMIT 40")
  with pytest.raises(ValueError, match="sample-percent"):
    pr.sample_sql(f"{LANDING}.orders", 40, 0)


# --------------------------------------------------------------------------
# readings
# --------------------------------------------------------------------------
def test_an_order_the_source_keeps_and_the_synthetic_side_breaks():
  source = {"n": 1000, "both_0_1": 1000, "lt_0_1": 990, "eq_0_1": 10}
  synthetic = {"n": 1000, "both_0_1": 1000, "lt_0_1": 480, "eq_0_1": 20}
  reading = pr.read_temporal_order(["created_at", "shipped_at"], source,
                                   synthetic)[0]
  assert reading["source_order"] == "not_after"
  assert reading["kept_source"] == 1.0
  assert reading["kept_synthetic"] == 0.5
  assert reading["reading"] == "invariant_broken"


def test_the_direction_of_the_order_comes_from_the_source():
  source = {"n": 100, "both_0_1": 100, "lt_0_1": 0, "eq_0_1": 0}
  synthetic = {"n": 100, "both_0_1": 100, "lt_0_1": 0, "eq_0_1": 1}
  reading = pr.read_temporal_order(["closed_at", "opened_at"], source,
                                   synthetic)[0]
  assert reading["source_order"] == "not_before"
  assert reading["reading"] == "invariant_kept"


def test_no_invariant_when_the_source_itself_has_no_order():
  source = {"n": 100, "both_0_1": 100, "lt_0_1": 55, "eq_0_1": 0}
  reading = pr.read_temporal_order(["a", "b"], source, source)[0]
  assert reading["reading"] == "no_invariant"
  empty = pr.read_temporal_order(["a", "b"], {"n": 0}, {"n": 0})[0]
  assert empty["reading"] == "no_invariant"
  assert empty["kept_source"] is None


def test_parent_child_reading_is_parent_not_after_child():
  source = {"n": 500, "both_0_0": 500, "le_0_0": 500}
  synthetic = {"n": 480, "both_0_0": 480, "le_0_0": 250}
  reading = pr.read_parent_child(["created_at"], ["created_at"], source,
                                 synthetic)[0]
  assert reading["kept_source"] == 1.0
  assert reading["kept_synthetic"] == pytest.approx(0.520833)
  assert reading["reading"] == "invariant_broken"


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------
def test_the_tables_with_the_most_pair_failures_are_probed_first():
  assert [c["table"] for c in pr.pick_tables(DIGEST, [], 1)] == ["orders"]
  assert [c["table"] for c in pr.pick_tables(DIGEST, ["users"], 5)] == ["users"]
  no_paths = {"tables": [{"table": "t", "table_ref": "t"}]}
  assert pr.pick_tables(no_paths, [], 5) == []


# --------------------------------------------------------------------------
# the command, end to end (fake BigQuery)
# --------------------------------------------------------------------------
def _report_dir(tmp_path):
  real = tmp_path / "rep" / "real"
  real.mkdir(parents=True)
  (real / "evaluation_digest.json").write_text(json.dumps(DIGEST))
  (real / "alias_map.json").write_text(json.dumps(ALIAS_MAP))
  return tmp_path / "rep"


def _answers(sql):
  if "unseen" in sql:
    return {"n": 1000, "unseen": 400}
  broken = "synthetic_data" in sql
  if " JOIN " in sql:  # users.created_at against the two order instants
    kept = 450 if broken else 900
    return {
        "n": 900,
        "both_0_0": 900,
        "le_0_0": kept,
        "both_0_1": 900,
        "le_0_1": 900
    }
  return {
      "n": 1000,
      "both_0_1": 1000,
      "lt_0_1": 500 if broken else 1000,
      "eq_0_1": 0
  }


def test_run_writes_real_and_aliased_probe_readings(tmp_path, capsys):
  report = _report_dir(tmp_path)
  args = pr.build_parser().parse_args([
      "run", "--report-dir",
      str(report), "--project", "demo-project", "--table", "orders",
      "--history-mappings",
      str(tmp_path / "history.json")
  ])
  query = fake_query(_answers)
  assert pr._cmd_run(args, client=object(), query=query) == 0  # pylint: disable=protected-access
  real = json.loads((report / "real/evaluation_probes.json").read_text())
  by_probe = {
      r["probe"]: r for r in real["probes"] if r["column"] != "shipped_at"
  }
  assert set(by_probe) == set(pr.PROBES)
  assert len(real["probes"]) == 4  # the edge has two child instants
  assert by_probe["temporal_order"]["reading"] == "invariant_broken"
  assert by_probe["parent_child_order"]["parent"]["column"] == "created_at"
  assert by_probe["parent_child_order"]["kept_synthetic"] == 0.5
  assert by_probe["combination"]["unseen_combination_share"] == 0.4
  assert by_probe["combination"]["reading"] == "invented_combinations"
  # the numeric pair is the pair metrics' business, not a combination probe
  assert sum(r["probe"] == "combination" for r in real["probes"]) == 1

  oss_text = (report / "oss/evaluation_probes.json").read_text()
  oss = json.loads(oss_text)
  assert oss["space"] == "oss"
  for real_name in ("orders", "users", "demo-project", "created_at", "city"):
    assert real_name not in oss_text
  parent_child = next(
      r for r in oss["probes"] if r["probe"] == "parent_child_order")
  assert parent_child["table"] == "A_TABLE"
  assert parent_child["parent"]["table"] == "B_TABLE"
  assert parent_child["edge"].startswith("A_TABLE(A_COL_")
  assert "4 reading(s), 3 broken" in capsys.readouterr().out


def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path, capsys):
  report = _report_dir(tmp_path)
  args = pr.build_parser().parse_args([
      "run", "--report-dir",
      str(report), "--project", "demo-project", "--table", "orders",
      "--dry-run", "--probe", "temporal_order", "--history-mappings",
      str(tmp_path / "history.json")
  ])
  query = fake_query(_answers)
  assert pr._cmd_run(args, client=object(), query=query) == 0  # pylint: disable=protected-access
  out = capsys.readouterr().out
  assert "2 query(ies) planned; nothing was run" in out
  assert "CAST(`created_at` AS TIMESTAMP)" in out
  assert not (report / "real/evaluation_probes.json").exists()
  assert all("INFORMATION_SCHEMA" in sql for sql in query.asked)


def test_a_missing_digest_is_reported_before_any_query(tmp_path):
  args = pr.build_parser().parse_args(
      ["run", "--report-dir",
       str(tmp_path), "--project", "demo-project"])
  with pytest.raises(SystemExit):
    pr._cmd_run(args, client=object(), query=fake_query(_answers))  # pylint: disable=protected-access
