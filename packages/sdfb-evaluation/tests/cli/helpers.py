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
"""Doubles shared by the CLI tests: an in-memory BigQuery that also
records the driver's own writes, an `Env` over it, and a validator of a
row against its packaged table schema. Nothing here is real data
(`demo-project`, invented thelook rows).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import apache_beam as beam
from unit.context.plan_fakes import PlanBq, thelook_rows

from sdfb_evaluation.beam.assemble import final_row, finish_time
from sdfb_evaluation.beam.io import InMemorySources
from sdfb_evaluation.cli.driver import Env
from sdfb_evaluation.schemas import load_schema
from sdfb_evaluation.scoring import to_metric_row
from sdfb_evaluation.types import MetricValue

NOW = datetime(2026, 9, 14, 8, 0, 0, tzinfo=UTC)  # plan_fakes.NOW
REGISTRY = "evaluation_data_history"
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)")


class RecordingBq(PlanBq):
  """`PlanBq` (the planner's BigQuery over invented thelook rows) that
  also takes the driver's writes: `loads` records every `load_json`,
  `canned` answers a query by SQL substring before the planner's rules,
  and `load_failures` raises instead of loading a table."""

  def __init__(self, **kwargs: Any):
    super().__init__(source=thelook_rows(1), landing=thelook_rows(2), **kwargs)
    self.loads: list[tuple[str, list[dict]]] = []
    self.canned: list[tuple[str, Sequence[Mapping[str, Any]]]] = []
    self.load_failures: dict[str, BaseException] = {}

  def load_json(self, fqn: str, rows: Sequence[Mapping[str, Any]],
                schema: list[dict]) -> str:
    table = fqn.rsplit(".", 1)[1]
    assert schema == load_schema(table), table
    if table in self.load_failures:
      raise self.load_failures[table]
    self.events.append(("load", fqn))
    self.loads.append((fqn, [dict(row) for row in rows]))
    return f"load_{len(self.loads)}"

  def query(self,
            sql: str,
            params: Mapping[str, Any] | None = None,
            *,
            max_bytes: int | None = None) -> list[dict]:
    for needle, rows in self.canned:
      if needle in sql:
        self.queries.append((sql, dict(params or {})))
        return [dict(row) for row in rows]
    return super().query(sql, params, max_bytes=max_bytes)

  def registry_rows(self) -> list[dict]:
    """Every registry row the driver loaded, in order."""
    return [
        row for fqn, rows in self.loads if fqn.endswith(f".{REGISTRY}")
        for row in rows
    ]


def thelook_sources() -> InMemorySources:
  """The rows `RecordingBq` plans, as the pipeline's sources."""
  rows_by = {}
  for side, seed in (("source", 1), ("synthetic", 2)):
    for name, rows in thelook_rows(seed).items():
      rows_by[(name, side)] = rows
  return InMemorySources(rows_by)


def make_env(bq: Any,
             *,
             execute: Callable[[Any, bool], str | None] | None = None,
             now: datetime = NOW,
             **overrides: Any) -> Env:
  """An `Env` over `bq`: a fixed clock, counting id tokens, in-memory
  sources and a pipeline that is built but not run (unless `execute`)."""
  counter = itertools.count(1)
  env = Env(
      make_bq=lambda project: bq,
      session_factory=None,
      now=lambda: now,
      token=lambda: f"{next(counter):08x}",
      make_sources=thelook_sources,
      execute=execute or (lambda pipeline, wait: None))
  return dataclasses.replace(env, **overrides)


def _check_scalar(field: Mapping[str, Any], value: Any, name: str,
                  ordered: bool) -> None:
  kind = field["type"]
  if kind == "RECORD":
    assert isinstance(value, dict), name
    names = [f["name"] for f in field["fields"]]
    assert (list(value) if ordered else sorted(value)) == (names if ordered else
                                                           sorted(names)), name
    for sub in field["fields"]:
      _check_value(sub, value[sub["name"]], f"{name}.", ordered)
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
    json.dumps(value)
  else:
    raise AssertionError(f"{name}: unchecked type {kind}")


def _check_value(field: Mapping[str, Any], value: Any, where: str,
                 ordered: bool) -> None:
  name = where + field["name"]
  if field.get("mode") == "REPEATED":
    assert isinstance(value, list), name
    for item in value:
      _check_scalar(field, item, name, ordered)
    return
  if value is None:
    assert field.get("mode") != "REQUIRED", f"{name} is REQUIRED"
    return
  _check_scalar(field, value, name, ordered)


def check_row(table: str,
              row: Mapping[str, Any],
              *,
              ordered: bool = True) -> None:
  """`row` is exactly `table`'s schema: its keys (in schema order unless
  `ordered` is False: a local NDJSON line sorts them), every value of its
  field's type, no REQUIRED field NULL, no non-finite float."""
  fields = load_schema(table)
  names = [f["name"] for f in fields]
  if ordered:
    assert list(row) == names, table
  else:
    assert sorted(row) == sorted(names), table
  for field in fields:
    _check_value(field, row[field["name"]], f"{table}.", ordered)


def ks_row(plan: Any, value: float) -> dict[str, Any]:
  """One `evaluation_metrics` row of `plan`: `column.ks` of
  orders.num_of_item at `value`, graded by the catalogue (no noise
  floor, so the value alone decides)."""
  return to_metric_row(
      MetricValue(
          metric_id="column.ks",
          table="orders",
          value=value,
          column="num_of_item",
          column_kind="numeric",
          n_source=240,
          n_synthetic=240),
      evaluation_id=plan.evaluation_id,
      evaluated_at=plan.evaluated_at,
      landing_table=None,
      source_table=None)


def tiny_pipeline(
    *,
    counts: Mapping[str, int] | None = None,
    status: str = "SUCCEEDED",
    write: bool = True,
    ks: Sequence[float] = ()) -> Callable[..., dict]:
  """A stand-in for `build_evaluation_pipeline` that writes only a FINAL
  registry row with `counts` through the run's own sinks (and one
  `ks_row` per value of `ks`): the driver's whole path, in a pipeline
  that takes a second. `built` on the returned function records each
  call's (plan, keyword arguments); `write=False` builds an empty
  pipeline (for a run whose pipeline is never executed)."""
  built: list[tuple[Any, dict[str, Any]]] = []

  def build(p: beam.Pipeline, plan: Any, **kwargs: Any) -> dict:
    built.append((plan, kwargs))
    if not write:
      return {}
    row = final_row(
        plan, {
            "overall": 0.75,
            "fidelity": 0.75
        },
        dict(counts or {
            "total": 1,
            "pass": 1
        }), {},
        finished_at=finish_time(plan.evaluated_at),
        status=status,
        status_reason=None)
    final = p | "Final" >> beam.Create([row])
    kwargs["sinks"].write(final, REGISTRY)
    if ks:
      metrics = p | "Metrics" >> beam.Create([ks_row(plan, v) for v in ks])
      kwargs["sinks"].write(metrics, "evaluation_metrics")
    return {"registry": final}

  build.built = built  # type: ignore[attr-defined]
  return build
