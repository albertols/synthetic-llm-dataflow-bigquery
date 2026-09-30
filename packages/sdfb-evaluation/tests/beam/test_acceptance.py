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
"""The planted-defect acceptance of the whole evaluator (Task 26): the
composed pipeline (`beam.pipeline.build_evaluation_pipeline`) on the
DirectRunner with `LocalJsonSinks`, over the invented thelook-shaped
launch of `acceptance_data`.

    good synthetic (same generator) ─► no FAIL on any fidelity, privacy
                                       or integrity row or headline
    bad synthetic (7 planted defects) ─► every defect FAILs the metrics
                                         `acceptance_data.DEFECTS` names

Defect 1's 1 % of copies FAILs the memorization lift and the exact-match
rate; the DCR holdout share it cannot move past its gate (a copy fraction
c moves the share by at most c / 2, the gate reads ci_low >= 0.60), so
that metric's FAIL is proved on a heavy copier (30 % of R copies).

Plus the brief's lifecycle and hygiene tests: a skipped registry row for
an empty scope, every written row valid against its schema, the FINAL
registry row written only after the metric sinks' load and copy jobs,
degenerate columns never NaN, and a rerun byte-identical.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions

from sdfb_evaluation import schemas
from sdfb_evaluation.beam.io import InMemorySources, LocalJsonSinks, Sinks
from sdfb_evaluation.beam.pipeline import (
    build_evaluation_pipeline,
    pipeline_options_defaults,
)
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.context.plan import EvaluationPlan
from sdfb_evaluation.scoring import MODEL_KEY, headline_counts, is_aggregate

from .acceptance_data import (
    DEFECTS,
    FIELDS_BY_TABLE,
    FakeStatsQuery,
    acceptance_plan,
    evaluation_plan,
    heavy_copies,
    launch_rows,
    plant_defects,
    table_plan,
    with_scope,
)

pytestmark = pytest.mark.slow  # three full DirectRunner evaluations

LABEL_KEY = b"acceptance-label-key-0123456789ab"
_GATED_FAMILIES = ("fidelity", "privacy", "integrity")
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)")
_CATALOGUE = load_catalogue()


class Outcome(NamedTuple):
  """What one evaluation wrote, table by table."""
  plan: EvaluationPlan
  metrics: list[dict[str, Any]]
  profiles: list[dict[str, Any]]
  flags: list[dict[str, Any]]
  registry: list[dict[str, Any]]
  lines: list[str]  # the metric shards' raw NDJSON lines


def _key_file(directory: Path) -> str:
  path = directory / "label.key"
  path.write_bytes(LABEL_KEY)
  return str(path)


def evaluate(plan: EvaluationPlan,
             rows: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
             out_dir: Path,
             *,
             stats_query: Any = None,
             sinks: Sinks | None = None) -> Outcome:
  """One evaluation on the DirectRunner with the runner defaults;
  `rows` maps side → table → rows."""
  sources = InMemorySources({
      (name, side): table_rows for side, by_table in rows.items()
      for name, table_rows in by_table.items()
  })
  sinks = sinks or LocalJsonSinks(str(out_dir))
  options = PipelineOptions(**pipeline_options_defaults("DirectRunner", plan))
  with beam.Pipeline(options=options) as p:
    build_evaluation_pipeline(
        p, plan, sources=sources, sinks=sinks, stats_query=stats_query)
  assert isinstance(sinks, LocalJsonSinks)
  lines = sorted(
      line for path in sorted(out_dir.glob("evaluation_metrics/*.jsonl"))
      for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
  return Outcome(plan, sinks.read_rows("evaluation_metrics"),
                 sinks.read_rows("evaluation_profiles"),
                 sinks.read_rows("evaluation_row_flags"),
                 sinks.read_rows("evaluation_data_history"), lines)


@pytest.fixture(scope="module", name="launch")
def fixture_launch() -> dict[str, dict[str, list[dict]]]:
  source = launch_rows(seed=11, id_base=100_000)
  good = launch_rows(seed=23, id_base=500_000)
  return {"source": source, "good": good, "bad": plant_defects(source, good)}


def _run(launch: Mapping[str, Any], which: str, directory: Path) -> Outcome:
  key = _key_file(directory)
  plan = acceptance_plan(
      launch["source"],
      launch[which],
      evaluation_id=f"ev_acc_{which}",
      label_key_uri=key)
  return evaluate(
      plan, {
          "source": launch["source"],
          "synthetic": launch[which]
      },
      directory / "out",
      stats_query=FakeStatsQuery(plan))


@pytest.fixture(scope="module", name="good_run")
def fixture_good_run(launch, tmp_path_factory) -> Outcome:
  return _run(launch, "good", tmp_path_factory.mktemp("good"))


@pytest.fixture(scope="module", name="bad_run")
def fixture_bad_run(launch, tmp_path_factory) -> Outcome:
  return _run(launch, "bad", tmp_path_factory.mktemp("bad"))


def _rows_for(outcome: Outcome, table: str, metric_id: str,
              column: str | tuple[str, str] | None) -> list[dict[str, Any]]:
  found = [
      row for row in outcome.metrics
      if row["table_name"] == table and row["metric_id"] == metric_id
  ]
  if column is None:
    return found
  if isinstance(column, tuple):  # a pair, in either order
    return [
        row for row in found
        if {row["column_name"], row["column_name_2"]} == set(column)
    ]
  return [
      row for row in found
      if column in (row["column_name"], row["column_name_2"])
  ]


def _describe(rows: Sequence[Mapping[str, Any]]) -> str:
  """One line per row, for an assertion message."""
  lines = []
  for row in rows:
    scope = "/".join(
        str(row[k])
        for k in ("table_name", "metric_id", "column_name", "column_name_2"))
    status, value, detail = row["status"], row["value"], row["detail"]
    ci = (row["ci_low"], row["ci_high"])
    lines.append(f"{scope}: {status} value={value} ci={ci} "
                 f"detail={json.dumps(detail)[:200]}")
  return "; ".join(lines)


# --------------------------------------------------------------------------
# the acceptance
# --------------------------------------------------------------------------
def test_good_synthetic_has_no_fail_on_gated_headlines(good_run):
  """A faithful generator FAILs nothing in the fidelity, privacy and
  integrity families: no measured row, no table or model headline."""
  failed = [
      row for row in good_run.metrics
      if row["family"] in _GATED_FAMILIES and row["status"] == "fail"
  ]
  assert not failed, _describe(failed)
  for _, table, column, metric_ids in DEFECTS:
    for metric_id in metric_ids:
      rows = _rows_for(good_run, table, metric_id, column)
      assert rows, (table, metric_id, column)
      assert all(r["status"] in ("pass", "warn") for r in rows), _describe(rows)
  headlines = [
      row for row in good_run.metrics
      if is_aggregate(row["metric_id"]) and row["family"] in _GATED_FAMILIES
  ]
  assert {row["table_name"] for row in headlines
         } == {"users", "orders", "order_items", MODEL_KEY}
  assert all(row["status"] != "fail" for row in headlines), _describe(headlines)
  final = good_run.registry[-1]
  assert final["event"] == "FINAL" and final["status"] == "SUCCEEDED"


@pytest.mark.parametrize(
    "defect", DEFECTS, ids=[d[0].split(" ", 1)[0] for d in DEFECTS])
def test_bad_synthetic_fails_every_planted_defect(bad_run, defect):
  _, table, column, metric_ids = defect
  for metric_id in metric_ids:
    rows = _rows_for(bad_run, table, metric_id, column)
    assert rows, (table, metric_id, column)
    assert all(r["status"] == "fail" for r in rows), _describe(rows)


def _one(outcome: Outcome, table: str, metric_id: str) -> dict[str, Any]:
  [row] = _rows_for(outcome, table, metric_id, None)
  return row


def test_one_percent_copies_raise_the_holdout_share(good_run, bad_run):
  """Defect 1 at the brief's 1 % (plus defect 2's 0.5 %) moves the DCR
  holdout share up, but a copy fraction c moves it by at most c / 2
  (the copies' nearest record is in R; the rest split evenly), so 1.5 %
  of copies cannot bring its CI lower bound to the 0.60 gate: the exact
  copies are the lift's and the exact-match rate's to catch (both FAIL,
  above), and the share's FAIL is proved at its design effect size in
  `test_heavy_memorization_fails_the_holdout_share`."""
  good = _one(good_run, "users", "row.dcr_train_holdout_share")
  bad = _one(bad_run, "users", "row.dcr_train_holdout_share")
  assert bad["value"] > good["value"]
  assert bad["value"] <= 0.5 + 0.015 / 2 + 4 * bad["detail"]["se_perm"]
  assert good["status"] == "pass"


def test_heavy_memorization_fails_the_holdout_share(launch, tmp_path):
  source = launch["source"]
  users = heavy_copies(source["users"], launch["good"]["users"])
  table = table_plan(
      "users",
      _fields("users"),
      source["users"],
      users,
      pk=("id",),
      identity=("email",),
      panel_rows=2000)
  plan = evaluation_plan([table],
                         evaluation_id="ev_acc_heavy",
                         label_key_uri=_key_file(tmp_path))
  outcome = evaluate(plan, {
      "source": {
          "users": source["users"]
      },
      "synthetic": {
          "users": users
      }
  }, tmp_path / "out")
  share = _one(outcome, "users", "row.dcr_train_holdout_share")
  assert share["status"] == "fail", _describe([share])
  assert share["ci_low"] >= 0.6
  for metric_id in ("row.memorization_lift", "row.exact_match_rate_nonkey"):
    row = _one(outcome, "users", metric_id)
    assert row["status"] == "fail", _describe([row])


def test_bad_run_integrity_headline_and_registry(bad_run):
  final = bad_run.registry[-1]
  assert final["status"] == "SUCCEEDED" and final["event"] == "FINAL"
  assert final["metrics_fail"] > 0
  integrity = [
      row for row in bad_run.metrics
      if row["metric_id"] == "table.integrity_score" and
      row["table_name"] == "order_items"
  ]
  assert integrity and integrity[0]["detail"]["integrity_fail"] >= 1


# --------------------------------------------------------------------------
# the brief's other tests
# --------------------------------------------------------------------------
def test_empty_scope_writes_skipped_registry_row(launch, tmp_path):
  source = {name: rows[:40] for name, rows in launch["source"].items()}
  synthetic = {name: rows[:40] for name, rows in launch["good"].items()}
  full = acceptance_plan(
      source, synthetic, evaluation_id="ev_acc_empty", label_key_uri=None)
  tables = [
      with_scope(t, status="empty", reason="no rows in the appends window")
      for t in full.tables
  ]
  plan = dataclasses.replace(full, tables=tuple(tables))
  assert plan.skip_reason is not None
  outcome = evaluate(plan, {
      "source": source,
      "synthetic": synthetic
  }, tmp_path / "out")
  assert outcome.metrics == [] and outcome.profiles == []
  assert outcome.flags == []
  [row] = outcome.registry
  assert (row["event"], row["status"]) == ("FINAL", "SKIPPED")
  assert "empty" in row["status_reason"]
  assert row["metrics_total"] == 0 and row["metrics_fail"] == 0
  assert row["finished_at"] and row["recorded_at"] >= row["evaluated_at"]
  assert row["evaluation_params"]["label_key_mode"] == "ephemeral"
  _check_rows("evaluation_data_history", outcome.registry)


def _check_scalar(field: Mapping[str, Any], value: Any, name: str) -> None:
  kind = field["type"]
  if kind == "RECORD":  # NDJSON lines are key-sorted: compare the names
    assert isinstance(value, dict), name
    assert set(value) == {f["name"] for f in field["fields"]}, name
    for sub in field["fields"]:
      _check_value(sub, value[sub["name"]], f"{name}.")
  elif kind == "STRING":
    assert isinstance(value, str), name
  elif kind == "INT64":
    assert isinstance(value, int) and not isinstance(value, bool), name
  elif kind == "FLOAT64":
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    assert math.isfinite(value), name
  elif kind == "BOOL":
    assert isinstance(value, bool), name
  elif kind == "TIMESTAMP":
    assert isinstance(value, str) and _TIMESTAMP_RE.fullmatch(value), name
  elif kind == "JSON":
    json.dumps(value, allow_nan=False)
  else:
    raise AssertionError(f"{name}: unchecked type {kind}")


def _check_value(field: Mapping[str, Any], value: Any, where: str) -> None:
  name = where + field["name"]
  if field.get("mode") == "REPEATED":
    assert isinstance(value, list), name
    for item in value:
      _check_scalar(field, item, name)
    return
  if value is None:
    assert field.get("mode") != "REQUIRED", f"{name} is REQUIRED"
    return
  _check_scalar(field, value, name)


def _check_rows(table: str, rows: Sequence[Mapping[str, Any]]) -> None:
  fields = schemas.load_schema(table)
  names = {f["name"] for f in fields}
  for row in rows:
    assert set(row) == names, (table, sorted(set(row) ^ names))
    for field in fields:
      _check_value(field, row[field["name"]], f"{table}.")


def test_all_rows_validate_against_schemas(good_run, bad_run):
  for outcome in (good_run, bad_run):
    assert outcome.metrics and outcome.profiles and outcome.flags
    _check_rows("evaluation_metrics", outcome.metrics)
    _check_rows("evaluation_profiles", outcome.profiles)
    _check_rows("evaluation_row_flags", outcome.flags)
    _check_rows("evaluation_data_history", outcome.registry)
    catalogue_ids = set(_CATALOGUE.ids())
    assert {row["metric_id"] for row in outcome.metrics} <= catalogue_ids
    for row in outcome.metrics:
      if row["status"] == "not_evaluated":
        assert row["detail"] and row["detail"].get("reason"), row
    [final] = outcome.registry
    measured = [r for r in outcome.metrics if not is_aggregate(r["metric_id"])]
    counts = headline_counts(measured)
    assert final["metrics_total"] == counts["total"] == len(measured)
    assert final["metrics_info"] == counts["info"]
    assert (final["metrics_pass"] + final["metrics_warn"] +
            final["metrics_fail"] + final["metrics_info"] +
            final["metrics_not_evaluated"]) == final["metrics_total"]


class _OrderLog:
  """An append-only event log shared by the fake sinks' DoFns (the
  DirectRunner runs them in this process)."""

  def __init__(self, path: Path):
    self.path = str(path)

  def __call__(self, element: Any, event: str) -> Any:
    with open(self.path, "a", encoding="utf-8") as log:
      log.write(event + "\n")
    return element


class _FakeWriteResultSinks(Sinks):
  """Sinks whose metric writes return fake `WriteToBigQuery` results: a
  load-job and a copy-job PCollection, each logged when it completes;
  the registry write logs every row it receives."""

  def __init__(self, log: _OrderLog):
    self.log = log
    self.signals: dict[str, tuple[beam.PCollection, ...]] = {}

  def write(self,
            rows: beam.PCollection,
            table: str,
            *,
            label: str | None = None) -> tuple[beam.PCollection, ...]:
    label = label or f"Write[{table}]"
    if table == "evaluation_data_history":
      logged = rows | f"{label}/Log" >> beam.Map(self.log, "registry")
      return (logged,)
    counted = rows | f"{label}/Count" >> beam.combiners.Count.Globally()
    loads = counted | f"{label}/LoadJobs" >> beam.Map(
        lambda n, t=table: (t, f"load-{t}-{n}")) | (
            f"{label}/LoadDone" >> beam.Map(self.log, f"load:{table}"))
    copies = loads | f"{label}/CopyJobs" >> beam.Map(
        lambda pair: (pair[0], f"copy-{pair[0]}")) | (
            f"{label}/CopyDone" >> beam.Map(self.log, f"copy:{table}"))
    self.signals[table] = (loads, copies)
    return (loads, copies)


def test_registry_final_after_metric_writes(launch, tmp_path):
  source = {name: rows[:300] for name, rows in launch["source"].items()}
  synthetic = {name: rows[:300] for name, rows in launch["good"].items()}
  users = table_plan(
      "users",
      _fields("users"),
      source["users"],
      synthetic["users"],
      pk=("id",),
      identity=("email",))
  plan = evaluation_plan([users],
                         evaluation_id="ev_acc_order",
                         label_key_uri=None)
  log = _OrderLog(tmp_path / "events.log")
  sinks = _FakeWriteResultSinks(log)
  options = PipelineOptions(**pipeline_options_defaults("DirectRunner", plan))
  with beam.Pipeline(options=options) as p:
    out = build_evaluation_pipeline(
        p,
        plan,
        sources=InMemorySources({
            ("users", "source"): source["users"],
            ("users", "synthetic"): synthetic["users"]
        }),
        sinks=sinks)
    final_transform = out["registry"].producer
  events = Path(log.path).read_text(encoding="utf-8").split()
  writes = [e for e in events if e != "registry"]
  assert sorted(writes) == sorted(f"{kind}:{table}" for kind in ("load", "copy")
                                  for table in ("evaluation_metrics",
                                                "evaluation_profiles",
                                                "evaluation_row_flags"))
  assert events.count("registry") == 1
  assert events[-1] == "registry", events
  # the FINAL row's step reads every load and copy signal as a side input
  side_inputs = {id(s.pvalue) for s in final_transform.side_inputs}
  for loads, copies in sinks.signals.values():
    assert id(loads) in side_inputs and id(copies) in side_inputs


def _fields(name: str) -> Sequence[Mapping[str, Any]]:
  return FIELDS_BY_TABLE[name]


_DEGENERATE_FIELDS: tuple[dict[str, str], ...] = (
    {
        "name": "id",
        "type": "INT64",
        "mode": "REQUIRED"
    },
    {
        "name": "all_null",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
    {
        "name": "constant",
        "type": "INT64",
        "mode": "NULLABLE"
    },
    {
        "name": "blank",
        "type": "STRING",
        "mode": "NULLABLE"
    },
    {
        "name": "single",
        "type": "STRING",
        "mode": "NULLABLE"
    },
    {
        "name": "flag",
        "type": "BOOL",
        "mode": "NULLABLE"
    },
    {
        "name": "moment",
        "type": "TIMESTAMP",
        "mode": "NULLABLE"
    },
    {
        "name": "amount",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
)


def _degenerate(n: int, base: int, *, spread: bool) -> list[dict[str, Any]]:
  moment = datetime(2025, 5, 1, 12, tzinfo=UTC)
  return [{
      "id": base + i,
      "all_null": None,
      "constant": 7,
      "blank": "" if i % 2 else "   ",
      "single": "same",
      "flag": True,
      "moment": moment,
      "amount": float(i % 3) if spread else 2.5,
  } for i in range(n)]


def _no_constant(token: str) -> Any:
  raise AssertionError(f"a {token} token in a written row")


def test_degenerate_columns_never_nan(tmp_path):
  """All-NULL, constant, all-blank, single-valued and one-sided columns,
  tiny sides and a table with no rows at all: every metric row is a
  value or `not_evaluated` with a reason — never NaN, never a crash."""
  source = _degenerate(40, 1000, spread=True)
  synthetic = _degenerate(30, 5000, spread=False)
  tiny = table_plan(
      "tiny", _DEGENERATE_FIELDS, source, synthetic, pk=("id",), panel_rows=10)
  empty = table_plan("empty", _DEGENERATE_FIELDS, [], [], pk=("id",))
  plan = evaluation_plan([tiny, empty],
                         evaluation_id="ev_acc_degenerate",
                         label_key_uri=None)
  outcome = evaluate(
      plan, {
          "source": {
              "tiny": source,
              "empty": []
          },
          "synthetic": {
              "tiny": synthetic,
              "empty": []
          }
      }, tmp_path / "out")
  assert {row["table_name"] for row in outcome.metrics} >= {"tiny", "empty"}
  empty_rows = [
      r for r in outcome.metrics
      if r["table_name"] == "empty" and not is_aggregate(r["metric_id"])
  ]
  assert empty_rows and all(r["status"] == "not_evaluated" for r in empty_rows)
  assert any(r["metric_id"] == "column.null_rate_delta" for r in empty_rows)
  numeric = ("value", "source_value", "synthetic_value", "baseline_value",
             "score", "noise_floor", "ci_low", "ci_high", "threshold_warn",
             "threshold_fail", "sample_rate")
  for row in outcome.metrics:
    for name in numeric:
      value = row[name]
      assert value is None or math.isfinite(value), (row["metric_id"], name)
    if row["status"] == "not_evaluated":
      assert row["detail"]["reason"], row
  for line in outcome.lines:
    json.loads(line, parse_constant=_no_constant)  # no NaN / ±Infinity token
  _check_rows("evaluation_metrics", outcome.metrics)
  [final] = outcome.registry
  assert final["status"] in ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "PARTIAL")


@pytest.fixture(scope="module", name="bad_rerun")
def fixture_bad_rerun(launch, tmp_path_factory) -> Outcome:
  return _run(launch, "bad", tmp_path_factory.mktemp("bad_again"))


def test_rerun_is_deterministic(bad_run, bad_rerun):
  """The same plan and rows give byte-identical metric rows (sorted: the
  shards' order is the runner's), profiles and flags."""
  assert bad_run.lines == bad_rerun.lines
  assert len(bad_run.lines) == len(bad_run.metrics) > 0

  def canonical(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted(json.dumps(r, sort_keys=True) for r in rows)

  assert canonical(bad_run.profiles) == canonical(bad_rerun.profiles)
  assert canonical(bad_run.flags) == canonical(bad_rerun.flags)
