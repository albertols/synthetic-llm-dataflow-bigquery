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
"""Tests for `sdfb_evaluation.beam.pipeline` (Task 26) beyond the
acceptance: the runner defaults and side-input cache sizing, the prepare
step's degradations, per-table failure isolation on the DirectRunner, and
the label key made once and kept out of the job graph.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import base64
import bz2
import dataclasses
import math
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import apache_beam as beam
import pytest
from apache_beam.internal import pickler
from apache_beam.options.pipeline_options import (
    DebugOptions,
    PipelineOptions,
    SetupOptions,
    WorkerOptions,
)
from apache_beam.portability import common_urns
from apache_beam.portability.api import beam_runner_api_pb2

from sdfb_evaluation.beam.io import InMemorySources, LocalJsonSinks
from sdfb_evaluation.beam.pipeline import (
    MIN_CACHE_MB,
    build_evaluation_pipeline,
    pipeline_options_defaults,
    prepare_evaluation,
    side_input_bytes,
)
from sdfb_evaluation.context.bq import BqApiError
from sdfb_evaluation.context.plan import SAMPLE_MODULUS, PrepareStatement
from sdfb_evaluation.context.scope import pin_source, sampled_read
from sdfb_evaluation.scoring import is_aggregate

from .acceptance_data import (
    ITEMS_FIELDS,
    ORDER_EDGE,
    ORDERS_FIELDS,
    USER_EDGE,
    USERS_FIELDS,
    evaluation_plan,
    launch_rows,
    table_plan,
)
from .relational_data import external_table

LABEL_KEY = b"pipeline-test-label-key-0123456789"
_NOW = "2026-09-30T00:00:00Z"


@pytest.fixture(scope="module", name="small")
def fixture_small() -> dict[str, dict[str, list[dict]]]:
  source = launch_rows(seed=5, id_base=100_000)
  good = launch_rows(seed=6, id_base=500_000)
  return {
      side: {
          name: rows[:120] for name, rows in by_table.items()
      } for side, by_table in (("source", source), ("synthetic", good))
  }


def _tables(small: Mapping[str, Mapping[str, list]], **panels: int) -> list:
  source, synthetic = small["source"], small["synthetic"]
  return [
      table_plan(
          "users",
          USERS_FIELDS,
          source["users"],
          synthetic["users"],
          pk=("id",),
          identity=("email",),
          role="root",
          panel_rows=panels.get("users", 0)),
      table_plan(
          "orders",
          ORDERS_FIELDS,
          source["orders"],
          synthetic["orders"],
          pk=("order_id",),
          edges=(USER_EDGE,),
          role="driven",
          panel_rows=panels.get("orders", 0)),
      table_plan(
          "order_items",
          ITEMS_FIELDS,
          source["order_items"],
          synthetic["order_items"],
          pk=("id",),
          edges=(ORDER_EDGE,),
          role="driven",
          panel_rows=panels.get("order_items", 0)),
  ]


# --------------------------------------------------------------------------
# runner defaults
# --------------------------------------------------------------------------
def test_runner_defaults(small):
  direct = pipeline_options_defaults("DirectRunner")
  assert direct == {
      "runner": "DirectRunner",
      "save_main_session": False,
      "max_cache_memory_usage_mb": MIN_CACHE_MB,
  }
  dataflow = pipeline_options_defaults("DataflowRunner")
  assert dataflow["experiments"] == ["upload_graph"]
  options = PipelineOptions(**dataflow)
  assert options.view_as(SetupOptions).save_main_session is False
  assert options.view_as(WorkerOptions).max_cache_memory_usage_mb >= 512
  assert "enable_data_sampling" not in (
      options.view_as(DebugOptions).experiments or [])
  plan = evaluation_plan(
      _tables(small), evaluation_id="ev_opts", label_key_uri=None)
  assert pipeline_options_defaults(
      "DataflowRunner", plan
  )["max_cache_memory_usage_mb"] == MIN_CACHE_MB  # a small plan: the floor
  with pytest.raises(ValueError, match="runner"):
    pipeline_options_defaults("")


def test_cache_is_the_sum_of_the_broadcast_sets(small):
  plan = evaluation_plan(
      _tables(small), evaluation_id="ev_cache", label_key_uri=None)
  base = side_input_bytes(plan)
  users = plan.tables[0]
  big = dataclasses.replace(
      users, rows_source=30_000_000, rows_synthetic=9_000_000)
  bigger = dataclasses.replace(plan, tables=(big, *plan.tables[1:]))
  # users is the orders edge's parent: its synthetic side (9 M <= 10 M
  # rows) is a side-input key set, its source side (30 M) a CoGroupByKey,
  # and 30 M source rows no longer fit membership's full-source sets —
  # so only the 9 M-key set is added (less the small plan's own sets)
  grown = side_input_bytes(bigger) - base
  assert 9_000_000 * 8 - 10_000 <= grown <= 9_000_000 * 8
  large = dataclasses.replace(
      plan,
      tables=tuple(
          dataclasses.replace(
              t, rows_source=9_000_000, rows_synthetic=9_000_000)
          for t in plan.tables))
  # two 9 M-key parent sets (72 MB each) and three full-source membership
  # sets (9 M rows x 2 arrays x 8 B = 144 MB each): past the 512 MB floor
  wanted = math.ceil(side_input_bytes(large) * 1.25 / 2**20)
  assert wanted > MIN_CACHE_MB
  assert pipeline_options_defaults("DataflowRunner",
                                   large)["max_cache_memory_usage_mb"] == wanted


# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------
class _FakeBq:
  """`Bq.execute`/`dry_run_bytes` over rules: a statement or dry run
  matching `refuse` raises BigQuery's error."""

  def __init__(self, *, refuse: Sequence[str] = ()):
    self.refuse = tuple(refuse)
    self.executed: list[str] = []
    self.dry_runs: list[str] = []

  def execute(self,
              sql: str,
              params: Mapping[str, Any],
              *,
              max_bytes: int | None = None) -> None:
    del params, max_bytes
    if any(word in sql for word in self.refuse):
      raise BqApiError(f"400 cannot run: {sql[:40]}")
    self.executed.append(sql)

  def dry_run_bytes(self, sql: str, params: Any = None) -> int:
    del params
    self.dry_runs.append(sql)
    if any(word in sql for word in self.refuse):
      raise PermissionError("403 tables.getData denied")
    return 0


def _pinned(small, *, sampled: bool):
  users = _tables(small)[0]
  pin = pin_source(
      source_table=str(users.source_table),
      job_create_time="2026-09-29T23:00:00Z",
      now=_NOW,
      time_travel_hours=168,
      temp_dataset="demo-project.synthetic_data_quality",
      evaluation_id="ev_prep")
  statements = [PrepareStatement(sql, {}) for sql in pin.prepare_sql]
  table = dataclasses.replace(
      users,
      source_pin=pin,
      source_pinned=True,
      source_read_table=pin.read_table)
  if sampled:
    keep = SAMPLE_MODULUS // 4
    sql, temp, params = sampled_read(
        pin.read_table,
        keep=keep,
        modulo=SAMPLE_MODULUS,
        salt="s" * 32,
        temp_dataset="demo-project.synthetic_data_quality",
        evaluation_id="ev_prep",
        side="src")
    statements.append(PrepareStatement(sql, params))
    table = dataclasses.replace(
        table, source_read_table=temp, sample_rate_source=keep / SAMPLE_MODULUS)
  plan = evaluation_plan([table], evaluation_id="ev_prep", label_key_uri=None)
  return dataclasses.replace(
      plan, prepare_sql=tuple(statements), salt="s" * 32), pin


def test_prepare_runs_every_statement(small):
  plan, pin = _pinned(small, sampled=True)
  bq = _FakeBq()
  prepared = prepare_evaluation(plan, bq)
  assert bq.executed == [s.sql for s in plan.prepare_sql]
  assert prepared.tables[0].source_pinned
  assert prepared.tables[0].source_pin == pin
  assert prepared.warnings == plan.warnings


def test_a_refused_pin_reads_the_source_unpinned(small):
  plan, _ = _pinned(small, sampled=False)
  prepared = prepare_evaluation(plan, _FakeBq(refuse=("CREATE SNAPSHOT",)))
  table = prepared.tables[0]
  assert table.source_read_table == table.source_table
  assert not table.source_pinned and table.source_pin is not None
  assert not table.source_pin.pinned
  assert table.registry_entry()["source_snapshot_ts"] is None
  assert any("read unpinned" in w for w in prepared.warnings)
  assert prepared.prepare_sql == ()


def test_a_refused_pin_resamples_the_live_source(small):
  plan, pin = _pinned(small, sampled=True)
  bq = _FakeBq(refuse=("CREATE SNAPSHOT",))
  prepared = prepare_evaluation(plan, bq)
  table = prepared.tables[0]
  assert len(bq.executed) == 1
  sample = bq.executed[0]
  assert f"FROM `{table.source_table}`" in sample
  assert pin.read_table not in sample
  assert sample.startswith(f"CREATE TABLE `{table.source_read_table}`")
  assert table.source_read_table != plan.tables[0].source_read_table
  assert table.sample_rate_source == plan.tables[0].sample_rate_source
  assert f"`{table.source_read_table}`" in prepared.prepare_sql[0].sql


def test_other_prepare_failures_raise(small):
  plan, _ = _pinned(small, sampled=True)
  with pytest.raises(BqApiError):
    prepare_evaluation(plan, _FakeBq(refuse=("FARM_FINGERPRINT",)))


def test_an_unreadable_read_only_parent_loses_that_side(small):
  products = external_table("products", ({
      "name": "id",
      "type": "INT64",
      "mode": "REQUIRED"
  },))
  plan = evaluation_plan([*_tables(small), products],
                         evaluation_id="ev_ext",
                         label_key_uri=None)
  bq = _FakeBq(refuse=(str(products.source_read_table),))
  prepared = prepare_evaluation(plan, bq)
  parent = prepared.tables[-1]
  assert parent.source_read_table == ""
  assert parent.scope.read_table == products.scope.read_table
  assert any("source twin" in w for w in prepared.warnings)
  bq = _FakeBq(refuse=(products.landing_table,))
  parent = prepare_evaluation(plan, bq).tables[-1]
  assert parent.scope.read_table == "" and parent.synthetic_read_table == ""
  assert "cannot be read" in str(parent.scope.reason)


# --------------------------------------------------------------------------
# per-table failure isolation (DirectRunner)
# --------------------------------------------------------------------------
def _run(plan, rows_by: Mapping[tuple[str, str], list], out: Path,
         **kwargs: Any) -> LocalJsonSinks:
  sinks = LocalJsonSinks(str(out))
  options = PipelineOptions(**pipeline_options_defaults("DirectRunner", plan))
  with beam.Pipeline(options=options) as p:
    build_evaluation_pipeline(
        p, plan, sources=InMemorySources(rows_by), sinks=sinks, **kwargs)
  return sinks


def test_a_failing_table_is_not_evaluated_and_the_run_continues(
    small, tmp_path):
  """orders' synthetic rows lack a plan column (the encoder raises on a
  worker); order_items' source cannot be read (the driver raises): both
  are not_evaluated with the reason, users is evaluated, the run is
  PARTIAL."""
  plan = evaluation_plan(
      _tables(small), evaluation_id="ev_isolated", label_key_uri=None)
  broken = [{
      k: v for k, v in row.items() if k != "status"
  } for row in small["synthetic"]["orders"]]
  rows_by = {
      ("users", "source"): small["source"]["users"],
      ("users", "synthetic"): small["synthetic"]["users"],
      ("orders", "source"): small["source"]["orders"],
      ("orders", "synthetic"): broken,
      ("order_items", "synthetic"): small["synthetic"]["order_items"],
  }
  sinks = _run(plan, rows_by, tmp_path / "out")
  metrics = [
      r for r in sinks.read_rows("evaluation_metrics")
      if not is_aggregate(r["metric_id"])
  ]
  by_table: dict[str, list[dict]] = {}
  for row in metrics:
    by_table.setdefault(row["table_name"], []).append(row)
  assert any(r["status"] == "pass" for r in by_table["users"])
  for row in by_table["orders"]:
    assert row["status"] == "not_evaluated", row
    assert "could not be encoded" in row["detail"]["reason"]
    assert "status" in row["detail"]["reason"]  # the column, never a value
  for row in by_table["order_items"]:
    assert row["status"] == "not_evaluated", row
    assert "could not be set up" in row["detail"]["reason"]
  items_ids = {r["metric_id"] for r in by_table["order_items"]}
  assert {
      "column.ks", "row.memorization_lift", "table.detection_auc",
      "relationship.orphan_rate"
  } <= items_ids
  for table in ("evaluation_profiles", "evaluation_row_flags"):
    names = {r["table_name"] for r in sinks.read_rows(table)}
    assert not names & {"orders", "order_items"}, table
  registry = sinks.read_rows("evaluation_data_history")
  assert len(registry) == 1
  final = registry[0]
  assert final["status"] == "PARTIAL"
  assert "orders: not evaluated" in final["status_reason"]
  assert "order_items: not evaluated" in final["status_reason"]


def test_every_table_failing_still_writes_rows_and_a_final_row(small, tmp_path):
  """No table reaches a transform (no side can be read): every launch
  table is not_evaluated with the reason, the roll-ups say why they are
  empty, and the FINAL row is PARTIAL."""
  plan = evaluation_plan(
      _tables(small)[:2], evaluation_id="ev_none", label_key_uri=None)
  sinks = _run(plan, {}, tmp_path / "out")
  metrics = sinks.read_rows("evaluation_metrics")
  measured = [r for r in metrics if not is_aggregate(r["metric_id"])]
  assert {r["table_name"] for r in measured} == {"users", "orders"}
  assert all(r["status"] == "not_evaluated" for r in measured)
  assert all("cannot be read" in r["detail"]["reason"] for r in measured)
  model = [r for r in metrics if r["metric_id"] == "model.overall_score"]
  assert len(model) == 1 and model[0]["status"] == "not_evaluated"
  registry = sinks.read_rows("evaluation_data_history")
  assert len(registry) == 1
  assert registry[0]["status"] == "PARTIAL"
  assert registry[0]["metrics_total"] == len(measured)


# --------------------------------------------------------------------------
# the label key
# --------------------------------------------------------------------------
def _payloads(p: beam.Pipeline) -> list[bytes]:
  proto = p.to_runner_api()
  out = []
  for transform in proto.components.transforms.values():
    if transform.spec.urn != common_urns.primitives.PAR_DO.urn:
      continue
    payload = beam_runner_api_pb2.ParDoPayload.FromString(
        transform.spec.payload)
    blob = payload.do_fn.payload
    if not blob:
      continue
    pickler.loads(blob)
    raw = base64.b64decode(blob)
    for decompress in (bz2.decompress, zlib.decompress):
      try:
        raw = decompress(raw)
        break
      except (OSError, zlib.error):
        continue
    out.append(raw)
  out.append(proto.SerializeToString())
  return out


def test_label_key_made_once_and_never_in_the_graph(small, tmp_path):
  key_file = tmp_path / "label.key"
  key_file.write_bytes(LABEL_KEY)
  plan = evaluation_plan(
      _tables(small, users=40),
      evaluation_id="ev_key",
      label_key_uri=str(key_file))
  rows_by = {
      (name, side): rows for side, by_table in small.items()
      for name, rows in by_table.items()
  }
  p = beam.Pipeline(
      options=PipelineOptions(
          **pipeline_options_defaults("DirectRunner", plan)))
  build_evaluation_pipeline(
      p,
      plan,
      sources=InMemorySources(rows_by),
      sinks=LocalJsonSinks(str(tmp_path / "out")))
  labels = [
      applied.full_label
      for applied in p.transforms_stack[0].parts
      if applied.full_label.startswith("LabelKey")
  ]
  assert labels == ["LabelKey"]
  for blob in _payloads(p):
    assert LABEL_KEY not in blob and LABEL_KEY.hex().encode() not in blob
