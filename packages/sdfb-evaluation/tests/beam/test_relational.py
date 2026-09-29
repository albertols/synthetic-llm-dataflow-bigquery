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
"""Tests for `sdfb_evaluation.beam.relational` (Task 25): foreign-key
orphan rates (SQL MATCH SIMPLE) and the children-per-parent fan-out
metrics of every edge, on the DirectRunner, against oracles computed
straight from the rows with `stats.relational`.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import dataclasses
import json
import pickle
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import apache_beam as beam
import numpy as np
import pytest
from apache_beam.testing.test_pipeline import TestPipeline as BeamTestPipeline
from apache_beam.testing.util import assert_that

from sdfb_evaluation.beam import relational
from sdfb_evaluation.beam.encode import EncodeSide
from sdfb_evaluation.beam.io import InMemorySources
from sdfb_evaluation.beam.relational import (
    COGROUP,
    FANOUT_CAP,
    OWNED_METRIC_IDS,
    SIDE_INPUT,
    SIDE_INPUT_MAX_KEYS,
    Relational,
)
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.context.plan import TablePlan
from sdfb_evaluation.context.relationships import Edge
from sdfb_evaluation.scoring import status_for, to_metric_row
from sdfb_evaluation.stats import noise
from sdfb_evaluation.stats.relational import fanout_histogram, fanout_metrics
from sdfb_evaluation.types import Method, MetricValue, Side, Status

from .relational_data import (
    CHILD_BASE,
    BUNDLE_EDGE,
    ITEMS_FIELDS,
    ORDER_EDGE,
    PRODUCT_EDGE,
    PRODUCTS_FIELDS,
    SALT,
    SIDES,
    SYNTHETIC_BASE,
    USER_EDGE,
    counted,
    external_table,
    items_for,
    launch_table,
    users_orders,
)

_CATALOGUE = load_catalogue()
_EVALUATED_AT = "2026-09-30T00:00:00+00:00"
_PATH_KEYS = ("path", "path_note", "paths")
_FANOUT_IDS = tuple(i for i in OWNED_METRIC_IDS if "orphan" not in i)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
class _RecordingSources(InMemorySources):
  """In-memory rows that record every read: (table, side, columns)."""

  def __init__(self, rows_by: Mapping[tuple[str, Any], Sequence[Any]]):
    super().__init__(rows_by)
    self.reads: list[tuple[str, str, tuple[str, ...]]] = []

  def _read(self, p: beam.Pipeline, table: TablePlan, side: Side,
            label: str) -> beam.PCollection:
    self.reads.append(
        (table.name, str(side), tuple(c.name for c in table.columns)))
    return super()._read(p, table, side, label)


def _collect(pcoll: beam.PCollection, path: Path) -> None:

  def dump(actual: Sequence[Any]) -> None:
    path.write_bytes(pickle.dumps(list(actual)))

  assert_that(pcoll, dump, label="Collect")


def _run(tables: Sequence[TablePlan],
         rows_by: Mapping[tuple[str, str], Sequence[Any]],
         tmp_path: Path,
         *,
         sources: InMemorySources | None = None,
         **kwargs: Any) -> list[MetricValue]:
  """Read and encode every evaluated table's two sides, then the
  relational pass over every table (read-only parents included)."""
  sources = sources or InMemorySources(rows_by)
  out_path = tmp_path / "metrics.pkl"
  with BeamTestPipeline() as p:
    batches = [
        sources.read(p, table, side) | EncodeSide(table, side, salt=SALT)
        for table in tables if table.evaluated for side in SIDES
    ] | "Flatten" >> beam.Flatten()
    out = batches | "Relational" >> Relational(
        tables, sources=sources, **kwargs)
    _collect(out["metrics"], out_path)
  metrics: list[MetricValue] = pickle.loads(out_path.read_bytes())
  return metrics


def _by_edge(
    metrics: Sequence[MetricValue]) -> dict[str, dict[str, MetricValue]]:
  out: dict[str, dict[str, MetricValue]] = {}
  for mv in metrics:
    assert mv.edge is not None, mv
    rows = out.setdefault(mv.edge, {})
    assert mv.metric_id not in rows, f"duplicate {mv.metric_id} on {mv.edge}"
    rows[mv.metric_id] = mv
  return out


def _status(mv: MetricValue) -> Status:
  return status_for(
      _CATALOGUE.get(mv.metric_id), mv, enforced=mv.detail["enforced"])


def _comparable(mv: MetricValue) -> tuple[Any, str]:
  """A metric row minus the join-path notes (side input vs CoGroupByKey)."""
  detail = {k: v for k, v in mv.detail.items() if k not in _PATH_KEYS}
  return (dataclasses.replace(mv,
                              detail={}), json.dumps(detail, sort_keys=True))


def _numbers(value: Any) -> list[float]:
  """Every number anywhere in a detail payload."""
  if isinstance(value, bool):
    return []
  if isinstance(value, (int, float)):
    return [value]
  if isinstance(value, Mapping):
    return [n for item in value.values() for n in _numbers(item)]
  if isinstance(value, (list, tuple)):
    return [n for item in value for n in _numbers(item)]
  return []


def _thelook(
    *,
    source_twin: bool = True,
    syn_orphan_products: Sequence[int] = ()
) -> tuple[list[TablePlan], dict[tuple[str, str], list[dict[str, Any]]]]:
  """users → orders → order_items, and order_items → the read-only
  synthetic_data.products: 30 products, 40 users with 0-4 orders each."""
  fanouts = [k % 5 for k in range(40)]
  tables, rows_by = users_orders(fanouts, fanouts)
  products = list(range(1, 31))
  for side in SIDES:
    orders = rows_by[("orders", side)]
    ids = [o["order_id"] for o in orders for _ in range(1 + o["order_id"] % 3)]
    items = items_for(ids, products, start=CHILD_BASE * 2)
    if side == "synthetic":
      for k, product_id in enumerate(syn_orphan_products):
        items[k]["product_id"] = product_id
    rows_by[("order_items", side)] = items
    rows_by[("products", side)] = [{"id": i} for i in products]
  items_plan = launch_table(
      "order_items",
      ITEMS_FIELDS,
      pk=("id",),
      edges=(ORDER_EDGE, PRODUCT_EDGE),
      role="driven")
  plans = [
      external_table("products", PRODUCTS_FIELDS, source_twin=source_twin),
      *tables,
      counted(items_plan, rows_by),
  ]
  return plans, rows_by


# --------------------------------------------------------------------------
# the brief's tests
# --------------------------------------------------------------------------
def test_orphans_detected(tmp_path):
  """Seven synthetic orders reference two users the synthetic users table
  does not hold: orphans, a FAIL on an enforced edge; the source, intact,
  has none. Orphans never enter the fan-out."""
  fanouts = [1, 2, 3] * 10
  tables, rows_by = users_orders(fanouts, fanouts)
  orders = rows_by[("orders", "synthetic")]
  for k in range(7):
    ghost = SYNTHETIC_BASE + 900 + (k % 2)
    orders.append({
        "order_id": CHILD_BASE + 500 + k,
        "user_id": ghost,
        "user_email": f"user{ghost}@example.com",
        "status": "Complete",
    })
  tables[1] = counted(tables[1], rows_by)
  by_edge = _by_edge(_run(tables, rows_by, tmp_path))
  rows = by_edge[USER_EDGE.label("orders")]
  orphan = rows["relationship.orphan_rate"]
  assert orphan.table == "orders"
  assert orphan.value == pytest.approx(7 / 67)
  assert orphan.n_synthetic == 67
  assert orphan.detail["orphans"] == 7 and orphan.detail["orphan_keys"] == 2
  assert orphan.detail["null_keys"] == 0 and orphan.detail["enforced"] is True
  assert orphan.source_value == 0.0
  assert _status(orphan) is Status.FAIL
  baseline = rows["relationship.orphan_rate_source"]
  assert baseline.value == 0.0 and baseline.n_source == 60
  assert _status(baseline) is Status.INFO
  # the 60 matched children keep the source's fan-out exactly
  assert rows["relationship.fanout_mean_ratio"].value == pytest.approx(1.0)
  assert rows["relationship.fanout_tvd"].value == pytest.approx(0.0)


def test_fanout_distribution_metrics(tmp_path):
  """Every fan-out metric equals `stats.relational.fanout_metrics` over the
  exact per-parent counts (childless parents at 0, two source parents and
  one synthetic parent past the 50 cap), with its noise in the right
  field."""
  src = [0] * 6 + [1] * 10 + [2] * 8 + [3] * 5 + [4] * 3 + [7] * 2 + [52, 55]
  syn = [0] * 3 + [1] * 12 + [2] * 9 + [3] * 6 + [5] * 4 + [51]
  tables, rows_by = users_orders(src, syn)
  rows = _by_edge(_run(tables, rows_by, tmp_path))[USER_EDGE.label("orders")]
  h_src, h_syn = fanout_histogram(src), fanout_histogram(syn)
  want = fanout_metrics(
      h_src,
      h_syn,
      sum(src) / len(src),
      sum(syn) / len(syn),
      min(src),
      max(src),
      cap=FANOUT_CAP,
      mean_overflow_src=(52 + 55) / 2,
      mean_overflow_syn=51.0)
  assert want is not None
  tvd = rows["relationship.fanout_tvd"]
  assert tvd.value == pytest.approx(want["tvd"])
  assert tvd.noise_floor == pytest.approx(
      noise.tvd_null_expectation((h_src / len(src)).tolist(), len(src),
                                 len(syn)))
  assert (tvd.n_source, tvd.n_synthetic) == (len(src), len(syn))
  assert rows["relationship.fanout_w1"].value == pytest.approx(want["w1"])
  ratio = rows["relationship.fanout_mean_ratio"]
  assert ratio.value == pytest.approx((119 / 35) / (174 / 36))
  assert ratio.value == pytest.approx(want["mean_ratio"])
  assert ratio.source_value == pytest.approx(174 / 36)
  assert ratio.synthetic_value == pytest.approx(119 / 35)
  zero = rows["relationship.zero_child_share_delta"]
  assert zero.value == pytest.approx(abs(3 / 35 - 6 / 36))
  assert (zero.ci_low, zero.ci_high) == pytest.approx(
      (want["zero_child_share_delta_ci_low"],
       want["zero_child_share_delta_ci_high"]))
  assert zero.ci_low <= zero.value <= zero.ci_high
  assert (zero.source_value, zero.synthetic_value) == pytest.approx(
      (6 / 36, 3 / 35))
  adherence = rows["relationship.cardinality_adherence"]
  assert adherence.value == pytest.approx(want["cardinality_adherence"])
  assert adherence.detail["adherent"] == want["cardinality_adherence_count"]
  assert (adherence.ci_low, adherence.ci_high) == pytest.approx(
      (want["cardinality_adherence_ci_low"],
       want["cardinality_adherence_ci_high"]))
  assert adherence.detail["range_exact"] is False  # max_src >= the cap
  coverage = rows["relationship.parent_coverage"]
  assert coverage.value == pytest.approx((1 - 3 / 35) / (1 - 6 / 36))
  assert coverage.value == pytest.approx(want["parent_coverage"])
  for metric_id in _FANOUT_IDS:
    mv = rows[metric_id]
    assert mv.method is Method.EXACT, metric_id
    if metric_id != "relationship.fanout_tvd":
      assert mv.noise_floor is None, metric_id
    # an extreme is one parent's count: never published (R65/R71)
    assert not {52, 55} & set(_numbers(mv.detail)), metric_id
  # an 8-point swing in the childless share is past fail (0.05), but at
  # 35 parents a side Newcombe's interval covers 0: noise, not a verdict
  row = to_metric_row(
      zero,
      evaluation_id="e1",
      evaluated_at=_EVALUATED_AT,
      landing_table=tables[1].landing_table,
      source_table=tables[1].source_table)
  assert zero.ci_low == 0.0
  assert row["status"] == "pass"
  assert row["detail"]["noise_downgraded_from"] == "fail"


def test_documented_edge_is_info(tmp_path):
  """A documented edge (`enforced: false`): its orphan rate is INFO, never
  FAIL — only the orphan rate; the fan-out metrics stay graded."""
  edge = dataclasses.replace(USER_EDGE, enforced=False)
  tables, rows_by = users_orders([1] * 30, [3] * 10, edges=(edge,))
  rows_by[("orders", "synthetic")].append({
      "order_id": CHILD_BASE + 999,
      "user_id": SYNTHETIC_BASE + 999,
      "user_email": None,
      "status": "Complete",
  })
  tables[1] = counted(tables[1], rows_by)
  rows = _by_edge(_run(tables, rows_by, tmp_path))[edge.label("orders")]
  orphan = rows["relationship.orphan_rate"]
  assert orphan.value == pytest.approx(1 / 31)
  assert orphan.detail["enforced"] is False
  assert _status(orphan) is Status.INFO
  row = to_metric_row(
      orphan,
      evaluation_id="e1",
      evaluated_at=_EVALUATED_AT,
      landing_table=tables[1].landing_table,
      source_table=tables[1].source_table,
      enforced=orphan.detail["enforced"])
  assert row["status"] == "info" and row["edge"] == edge.label("orders")
  assert row["table_name"] == "orders"
  for metric_id in _FANOUT_IDS:
    assert rows[metric_id].detail["enforced"] is False
    if _CATALOGUE.get(metric_id).warn is not None:  # fanout_w1: informational
      assert _status(rows[metric_id]) is not Status.INFO, metric_id
  assert _status(rows["relationship.fanout_tvd"]) is Status.FAIL
  assert _status(rows["relationship.fanout_mean_ratio"]) is Status.FAIL


def test_null_fk_counted_not_orphaned(tmp_path):
  """MATCH SIMPLE: a key tuple with any NULL part is counted, never joined
  — not an orphan and outside the rate's denominator — on a single-column
  and on a composite edge."""
  composite = Edge(
      cols=("user_id", "user_email"), ref="users", ref_cols=("id", "email"))
  fanouts = [2] * 20
  tables, rows_by = users_orders(fanouts, fanouts, edges=(USER_EDGE, composite))
  orders = rows_by[("orders", "synthetic")]
  for k in range(9):
    orders.append({
        "order_id": CHILD_BASE + 700 + k,
        "user_id": None,
        "user_email": None,
        "status": "Shipped",
    })
  for k in range(4):  # a valid user id, a NULL e-mail
    orders.append({
        "order_id": CHILD_BASE + 800 + k,
        "user_id": SYNTHETIC_BASE + k,
        "user_email": None,
        "status": "Shipped",
    })
  tables[1] = counted(tables[1], rows_by)
  by_edge = _by_edge(_run(tables, rows_by, tmp_path))
  single = by_edge[USER_EDGE.label("orders")]["relationship.orphan_rate"]
  assert single.value == 0.0 and single.detail["orphans"] == 0
  assert single.detail["null_keys"] == 9
  assert single.n_synthetic == 40 + 4
  assert _status(single) is Status.PASS
  pair = by_edge[composite.label("orders")]["relationship.orphan_rate"]
  assert pair.value == 0.0 and pair.detail["orphans"] == 0
  assert pair.detail["null_keys"] == 13 and pair.n_synthetic == 40
  # the 4 matched single-column children add to their parents' fan-out
  single_ratio = by_edge[USER_EDGE.label(
      "orders")]["relationship.fanout_mean_ratio"]
  assert single_ratio.synthetic_value == pytest.approx(44 / 20)
  pair_ratio = by_edge[composite.label(
      "orders")]["relationship.fanout_mean_ratio"]
  assert pair_ratio.value == pytest.approx(1.0)


def test_external_parent_checked_against_external_table(tmp_path):
  """order_items.product_id → synthetic_data.products(id), a parent this
  launch did not write: checked against the external table as it is (two
  synthetic items reference products it does not hold), its source side
  against the source-dataset twin, and read once per side even with two
  edges referencing it."""
  plans, rows_by = _thelook(syn_orphan_products=(31, 32, 31))
  edges = (ORDER_EDGE, PRODUCT_EDGE, BUNDLE_EDGE)
  plans[-1] = counted(
      launch_table(
          "order_items", ITEMS_FIELDS, pk=("id",), edges=edges, role="driven"),
      rows_by)
  sources = _RecordingSources(rows_by)
  by_edge = _by_edge(_run(plans, rows_by, tmp_path, sources=sources))
  rows = by_edge[PRODUCT_EDGE.label("order_items")]
  orphan = rows["relationship.orphan_rate"]
  n_items = len(rows_by[("order_items", "synthetic")])
  assert orphan.value == pytest.approx(3 / n_items)
  assert orphan.detail["orphan_keys"] == 2
  assert orphan.detail["parent_role"] == "external"
  assert orphan.detail["path"] == COGROUP  # a read-only parent is not counted
  assert _status(orphan) is Status.FAIL
  assert rows["relationship.orphan_rate_source"].value == 0.0
  assert rows["relationship.fanout_tvd"].value is not None
  assert by_edge[BUNDLE_EDGE.label(
      "order_items")]["relationship.orphan_rate"].value == 0.0
  assert by_edge[ORDER_EDGE.label(
      "order_items")]["relationship.orphan_rate"].value == 0.0
  product_reads = Counter(
      (t, s) for t, s, _ in sources.reads if t == "products")
  assert product_reads == {
      ("products", "source"): 1,
      ("products", "synthetic"): 1
  }
  # without a source-side twin only the source-side metrics are explained
  plans, rows_by = _thelook(source_twin=False, syn_orphan_products=(31,))
  del rows_by[("products", "source")]
  rows = _by_edge(_run(plans, rows_by,
                       tmp_path))[PRODUCT_EDGE.label("order_items")]
  assert rows["relationship.orphan_rate"].value == pytest.approx(1 / n_items)
  for metric_id in ("relationship.orphan_rate_source", *_FANOUT_IDS):
    mv = rows[metric_id]
    assert mv.value is None and "source" in mv.detail["reason"], metric_id


# --------------------------------------------------------------------------
# also required
# --------------------------------------------------------------------------
def test_side_input_and_cogroup_paths_agree(tmp_path):
  """At or under SIDE_INPUT_MAX_KEYS parent rows the parent set is a side
  input (np.searchsorted); forced to 0 every edge joins with
  CoGroupByKey. Every value, interval and count agrees."""
  plans, rows_by = _thelook(syn_orphan_products=(33, 34))
  orders = rows_by[("orders", "synthetic")]
  orders[0] = {**orders[0], "user_id": None}
  orders[1] = {**orders[1], "user_id": SYNTHETIC_BASE + 777}
  side_input = _run(plans, rows_by, tmp_path)
  cogroup = _run(plans, rows_by, tmp_path, side_input_max_keys=0)
  assert sorted(
      map(_comparable, side_input), key=lambda c:
      (c[0].edge, c[0].metric_id)) == sorted(
          map(_comparable, cogroup), key=lambda c: (c[0].edge, c[0].metric_id))
  paths = {
      mv.edge: mv.detail["path"]
      for mv in side_input
      if mv.metric_id == "relationship.orphan_rate"
  }
  assert paths == {
      USER_EDGE.label("orders"): SIDE_INPUT,
      ORDER_EDGE.label("order_items"): SIDE_INPUT,
      PRODUCT_EDGE.label("order_items"): COGROUP,
  }
  assert {
      mv.detail["path"]
      for mv in cogroup
      if mv.metric_id == "relationship.orphan_rate"
  } == {COGROUP}
  assert SIDE_INPUT_MAX_KEYS == 10_000_000


def test_catalogue_coverage_every_owned_id_emitted_or_explained(tmp_path):
  """Every `relationship.*` id is owned here and emitted once per edge — a
  value, or not_evaluated with a reason — scoped to the child table and
  the edge label, with its noise in the field the scorer reads (R41)."""
  plans, rows_by = _thelook(syn_orphan_products=(31,))
  metrics = _run(plans, rows_by, tmp_path)
  relationship_ids = {m.id for m in _CATALOGUE.by_level("relationship")}
  assert set(OWNED_METRIC_IDS) == relationship_ids
  by_plan = {t.name: t for t in plans}
  labels = {
      e.label(t.name): t for t in plans if t.role != "external" for e in t.edges
  }
  by_edge = _by_edge(metrics)
  assert set(by_edge) == set(labels)
  for label, rows in by_edge.items():
    assert sorted(rows) == sorted(OWNED_METRIC_IDS), label
    for metric_id, mv in rows.items():
      metric = _CATALOGUE.get(metric_id)
      child = labels[label]
      assert mv.table == child.name and mv.column is None, mv
      assert mv.encoding_plan_digest == child.encoding_plan_digest
      assert isinstance(mv.detail["enforced"], bool)
      if mv.value is None:
        assert mv.detail.get("reason"), mv
        continue
      if metric.noise_floor in ("wilson", "newcombe"):
        assert mv.ci_low is not None and mv.ci_high is not None, mv
      elif metric.noise_floor == "tvd_null":
        assert mv.noise_floor is not None, mv
      else:
        assert mv.noise_floor is None and mv.ci_low is None, mv
      json.dumps(
          to_metric_row(
              mv,
              evaluation_id="e1",
              evaluated_at=_EVALUATED_AT,
              landing_table=by_plan[mv.table].landing_table,
              source_table=by_plan[mv.table].source_table,
              enforced=mv.detail["enforced"]),
          allow_nan=False)
  evaluated = [mv for mv in metrics if mv.value is not None]
  assert {mv.metric_id for mv in evaluated} == set(OWNED_METRIC_IDS)


def test_ref_cols_other_than_the_pk_or_reordered_give_no_false_orphans(
    tmp_path):
  """Parent keys are hashed per edge over its own `ref_cols`, never taken
  from the parent's PK hash: an edge to the identity column (`email`) and
  a composite edge listing the PK's columns in the other order both match
  every child — and see the same fan-out as the PK edge."""
  by_email = Edge(cols=("user_email",), ref="users", ref_cols=("email",))
  reordered = Edge(
      cols=("user_email", "user_id"), ref="users", ref_cols=("email", "id"))
  edges = (USER_EDGE, by_email, reordered)
  fanouts = [0, 1, 2, 3, 4] * 6
  tables, rows_by = users_orders(
      fanouts, list(reversed(fanouts)), edges=edges, parent_pk=("id", "email"))
  by_edge = _by_edge(_run(tables, rows_by, tmp_path))
  reference = by_edge[USER_EDGE.label("orders")]
  for edge in edges:
    rows = by_edge[edge.label("orders")]
    for metric_id in ("relationship.orphan_rate",
                      "relationship.orphan_rate_source"):
      assert rows[metric_id].value == 0.0, (edge, metric_id)
      assert rows[metric_id].detail["orphans"] == 0
    for metric_id in _FANOUT_IDS:
      assert rows[metric_id].value == pytest.approx(
          reference[metric_id].value), (edge, metric_id)


def test_sampled_mode_withholds_what_a_sample_biases(tmp_path):
  """A row-sampled side cannot support a metric that needs every row of
  it: a sampled child side hides orphans and thins every parent's fan-out;
  a sampled parent side makes children look orphaned and pulls the
  source's extremes in. Those are not_evaluated with the observed lower
  bound; what a parent sample leaves unbiased stays, marked sampled."""
  fanouts = [1, 2, 3] * 10
  tables, rows_by = users_orders(fanouts, fanouts)
  users, orders = tables
  label = USER_EDGE.label("orders")
  child_sampled = _by_edge(
      _run(
          [users, dataclasses.replace(orders, sample_rate_synthetic=0.5)],
          rows_by, tmp_path))[label]
  orphan = child_sampled["relationship.orphan_rate"]
  assert orphan.value is None
  assert orphan.detail["reason"].startswith("sampled mode cannot measure")
  assert orphan.detail["reason"].endswith("run exact mode")
  assert orphan.detail["orphans_lower_bound"] == 0
  assert orphan.detail["matched_lower_bound"] == 60
  for metric_id in _FANOUT_IDS:
    mv = child_sampled[metric_id]
    assert mv.value is None and "run exact mode" in mv.detail["reason"]
    assert mv.detail["parents_with_children_lower_bound_synthetic"] == 30
  assert child_sampled["relationship.orphan_rate_source"].value == 0.0

  parent_sampled = _by_edge(
      _run([dataclasses.replace(users, sample_rate_source=0.4), orders],
           rows_by, tmp_path))[label]
  source = parent_sampled["relationship.orphan_rate_source"]
  assert source.value is None and "run exact mode" in source.detail["reason"]
  assert "orphans_lower_bound" not in source.detail  # not a bound here
  assert source.detail["matched_lower_bound"] == 60
  assert parent_sampled["relationship.orphan_rate"].value == 0.0
  adherence = parent_sampled["relationship.cardinality_adherence"]
  assert adherence.value is None
  assert "run exact mode" in adherence.detail["reason"]
  for metric_id in ("relationship.fanout_tvd", "relationship.fanout_w1",
                    "relationship.fanout_mean_ratio",
                    "relationship.zero_child_share_delta",
                    "relationship.parent_coverage"):
    mv = parent_sampled[metric_id]
    assert mv.value is not None, metric_id
    assert mv.method is Method.SAMPLE and mv.sample_rate == 0.4, metric_id


def test_a_failing_edge_does_not_fail_the_run(tmp_path, monkeypatch):
  """One edge's parent keys fail on a worker, another edge's parent cannot
  even be read: both edges are not_evaluated with the reason on every
  owned id; the third edge is computed as if alone."""
  by_email = Edge(cols=("user_email",), ref="users", ref_cols=("email",))
  missing = Edge(cols=("user_id",), ref=PRODUCT_EDGE.ref, ref_cols=("id",))
  edges = (USER_EDGE, by_email, missing)
  tables, rows_by = users_orders([1, 2] * 10, [2, 1] * 10, edges=edges)
  # a read-only parent with no rows to read: its read fails on the driver
  products = external_table("products", PRODUCTS_FIELDS)
  real_key_hashes = relational.key_hashes

  def failing(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> Any:
    if tuple(columns) == ("email",):
      raise ValueError("a malformed parent batch (test)")
    return real_key_hashes(rows, columns)

  monkeypatch.setattr(relational, "key_hashes", failing)
  metrics = _run([*tables, products], rows_by, tmp_path)
  by_edge = _by_edge(metrics)
  for edge, cause in ((by_email, "malformed"), (missing, "products")):
    rows = by_edge[edge.label("orders")]
    assert sorted(rows) == sorted(OWNED_METRIC_IDS)
    for mv in rows.values():
      assert mv.value is None and cause in mv.detail["reason"], (edge, mv)
  good = by_edge[USER_EDGE.label("orders")]
  assert good["relationship.orphan_rate"].value == 0.0
  assert good["relationship.fanout_mean_ratio"].value == pytest.approx(1.0)


def test_unreadable_parents_and_skipped_children_are_explained(tmp_path):
  """No silent drop: an edge whose parent is not in the plan, and every
  edge of a child the plan skipped, write a not_evaluated row per owned
  id with the reason."""
  ghost = Edge(cols=("user_id",), ref="products", ref_cols=("id",))
  tables, rows_by = users_orders([1] * 10, [1] * 10, edges=(USER_EDGE, ghost))
  items = launch_table(
      "order_items",
      ITEMS_FIELDS,
      pk=("id",),
      edges=(ORDER_EDGE,),
      role="driven")
  skipped = dataclasses.replace(
      items, skip_reason="scope unknown: the landing table could not be read")
  by_edge = _by_edge(_run([*tables, skipped], rows_by, tmp_path))
  for label, cause in ((ghost.label("orders"), "products is not in the"),
                       (ORDER_EDGE.label("order_items"), "scope unknown")):
    rows = by_edge[label]
    assert sorted(rows) == sorted(OWNED_METRIC_IDS), label
    for mv in rows.values():
      assert mv.value is None and cause in mv.detail["reason"], (label, mv)
  assert by_edge[USER_EDGE.label(
      "orders")]["relationship.orphan_rate"].value == 0.0


def test_parents_are_read_once_projected_to_their_ref_columns(tmp_path):
  """Each parent side is read once, whatever the number of edges into it,
  and only its referenced columns are selected (a DIRECT_READ bills the
  selected columns)."""
  by_email = Edge(cols=("user_email",), ref="users", ref_cols=("email",))
  edges = (USER_EDGE, by_email)
  tables, rows_by = users_orders([1, 3] * 5, [3, 1] * 5, edges=edges)
  sources = _RecordingSources(rows_by)
  _run(tables, rows_by, tmp_path, sources=sources)
  parent_reads = [r for r in sources.reads if r[0] == "users"]
  # the encoding reads (every plan column) and one projected read a side
  projected = sorted(
      r for r in parent_reads if r[2] != ("id", "email", "country"))
  assert projected == [("users", "source", ("id", "email")),
                       ("users", "synthetic", ("id", "email"))]


def test_fanout_counts_are_exact_past_one_batch(tmp_path):
  """Children of one parent spread over many batches (and bundles) still
  add up to one exact fan-out: 9000 orders over 3 users."""
  src = [3000, 4000, 2000, 0]
  syn = [2500, 4500, 2000, 0]
  tables, rows_by = users_orders(src, syn)
  rows = _by_edge(_run(tables, rows_by, tmp_path))[USER_EDGE.label("orders")]
  ratio = rows["relationship.fanout_mean_ratio"]
  assert ratio.source_value == pytest.approx(9000 / 4)
  assert ratio.synthetic_value == pytest.approx(9000 / 4)
  assert rows["relationship.zero_child_share_delta"].value == 0.0
  assert rows["relationship.fanout_tvd"].value == 0.0  # all past the cap
  assert rows["relationship.fanout_w1"].value == pytest.approx(0.0)
  assert rows["relationship.cardinality_adherence"].value == 1.0
  assert rows["relationship.cardinality_adherence"].detail["adherent"] == 4
  assert np.isclose(rows["relationship.parent_coverage"].value, 1.0)
