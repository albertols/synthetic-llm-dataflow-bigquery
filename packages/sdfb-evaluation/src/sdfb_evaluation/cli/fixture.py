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
"""Offline fixtures: `sdfb-eval plan|run --fixture_dir DIR` (a hidden
flag) evaluates rows held in JSON files, with no call to GCP.

    DIR/fixture.json
      {"project": "demo-project",
       "params": {"engine": "b1_rag"},                  (optional)
       "tables": [{"name": "orders",
                   "schema": [{"name", "type", "mode"}, ...],
                   "pk": ["order_id"], "identity": [],
                   "edges": [{"cols": ["user_id"], "ref": "users",
                              "ref_cols": ["id"]}],
                   "panel_rows": 40}]}                  (parents first)
    DIR/orders.source.json      [ {row}, ... ]   the source rows
    DIR/orders.synthetic.json   [ {row}, ... ]   the landed rows

A cell is JSON-typed and read back as the BigQuery client types it (the
encoder hashes canonical values, so the type matters): TIMESTAMP an
aware UTC `datetime` from ISO-8601 text, DATETIME / DATE / TIME from
ISO text, NUMERIC and BIGNUMERIC a `Decimal` from text, BYTES from
base64, RECORD and JSON as they are, a REPEATED column a list.

There is no BigQuery to plan with, so the plan is made from the rows by
the planner's own pure functions (`kinds_from_schema`, `apply_planning`,
`select_pairs`, `encoding_plan_digest`) over the statistics the planning
SELECT returns (`context.plan`'s module docstring), computed here
exactly instead of approximately:

    planning SELECT                         here
    ──────────────────────────────────────  ──────────────────────────────
    COUNTIF(x IS NULL), COUNTIF(TRIM = '')  counted
    APPROX_COUNT_DISTINCT(x)                exact distinct canonical values
    APPROX_QUANTILES(v, 1000), AVG,         numpy on the planning scale
      STDDEV_POP, MIN, MAX                  (`canonical.numeric_value`),
                                            quantiles by inverted CDF
    APPROX_TOP_COUNT(x, k)                  exact counts, NULL a value
    COUNTIF(TIME(x) = 00:00:00)             counted

    scope            the landing rows as they are (`table`, ok)
    source           the source rows, unpinned
    panel (D3)       R = the first `panel_rows` source rows, H the next
                     `panel_rows`; E and H_E their first 1,024; verified
                     (the digest is computed from R itself)
    launch           manual: no generation job, the fixture's `params`

Nothing is billed, prepared or sampled; the pipeline reads the same
rows through `InMemorySources`.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import numpy as np

from sdfb_evaluation.beam.io import InMemorySources
from sdfb_evaluation.canonical import canonical_value, numeric_value
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.context.bq import normalize_fqn
from sdfb_evaluation.context.budget import predict_shuffle_gb
from sdfb_evaluation.context.launch import LaunchContext
from sdfb_evaluation.context.plan import (
    GRID_POINTS,
    EvaluationPlan,
    Knobs,
    TablePlan,
    apply_planning,
    encoding_plan_digest,
    kinds_from_schema,
    select_pairs,
)
from sdfb_evaluation.context.reference import Panel, reference_digest
from sdfb_evaluation.context.relationships import Edge
from sdfb_evaluation.context.scope import ScopePlan
from sdfb_evaluation.types import Side
from sdfb_evaluation.version import EVALUATOR_VERSION

__all__ = ["Fixture", "FixtureTable", "load_fixture", "planning_stats"]

MANIFEST = "fixture.json"
EXPOSURE_ROWS = 1024  # E: the prompt-exposed prefix of R (D3)
_ATOM_TOP_K = 11  # APPROX_TOP_COUNT(v, 11): 10 atoms, NULL may take a slot
_DICTIONARY_TOP_K = 255  # APPROX_TOP_COUNT(x, 255): 254 values, NULL a slot
_NUMERIC = frozenset({
    "INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC", "DECIMAL",
    "BIGDECIMAL"
})
_TEMPORAL = frozenset({"TIMESTAMP", "DATETIME", "DATE", "TIME"})
_NESTED = frozenset({"RECORD", "STRUCT", "JSON"})
_TABLE_KEYS = frozenset({
    "name", "schema", "pk", "identity", "edges", "panel_rows", "landing_table",
    "source_table"
})
_EDGE_KEYS = frozenset({"cols", "ref", "ref_cols", "enforced"})
_GRID = np.linspace(0.0, 1.0, GRID_POINTS)


def _timestamp(text: str) -> datetime:
  return datetime.fromisoformat(text.replace("Z", "+00:00"))


_DECODERS: dict[str, Callable[[Any], Any]] = {
    "INT64": int,
    "INTEGER": int,
    "FLOAT64": float,
    "FLOAT": float,
    "NUMERIC": Decimal,
    "BIGNUMERIC": Decimal,
    "DECIMAL": Decimal,
    "BIGDECIMAL": Decimal,
    "BOOL": bool,
    "BOOLEAN": bool,
    "STRING": str,
    "GEOGRAPHY": str,
    "BYTES": base64.b64decode,
    "TIMESTAMP": _timestamp,
    "DATETIME": datetime.fromisoformat,
    "DATE": date.fromisoformat,
    "TIME": time.fromisoformat,
}


def _cell(field: Mapping[str, Any], raw: Any) -> Any:
  """One JSON cell with the BigQuery client's Python type."""
  if raw is None:
    return None
  bq_type = str(field.get("type") or "").upper()
  if str(field.get("mode") or "").upper() == "REPEATED":
    scalar = {**field, "mode": "NULLABLE"}
    return [_cell(scalar, item) for item in raw]
  decode = _DECODERS.get(bq_type)
  if decode is None:  # RECORD, JSON, a type the planner calls nested
    return raw
  try:
    return decode(raw)
  except (TypeError, ValueError, ArithmeticError) as exc:
    # the column and the type, never the value
    name = field.get("name")
    raise ValueError(f"column {name!r}: a cell is not a JSON-encoded "
                     f"{bq_type} ({type(exc).__name__})") from exc


def _typed_rows(fields: Sequence[Mapping[str, Any]], raw: Any,
                where: str) -> list[dict[str, Any]]:
  if not isinstance(raw, list) or not all(isinstance(r, Mapping) for r in raw):
    raise ValueError(f"{where}: expected a JSON array of row objects")
  return [{
      str(f["name"]): _cell(f, row.get(f["name"])) for f in fields
  } for row in raw]


# --------------------------------------------------------------------------
# the planning statistics, exactly
# --------------------------------------------------------------------------
def _key(value: Any) -> str:
  return repr(canonical_value(value))


def _top(values: Sequence[Any], k: int) -> list[tuple[Any, int]]:
  """APPROX_TOP_COUNT, exact: most frequent first, NULL counted."""
  counts = Counter(_key(v) for v in values)
  first: dict[str, Any] = {}
  for value in values:
    first.setdefault(_key(value), value)
  ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
  return [(first[key], count) for key, count in ranked]


def _planning_scale(value: Any) -> float | None:
  reading = numeric_value(value)
  if reading is None or not math.isfinite(reading):
    return None
  return reading


def _numeric_stats(values: Sequence[Any]) -> dict[str, Any]:
  scaled = [_planning_scale(v) for v in values]
  finite = np.array([v for v in scaled if v is not None], dtype=float)
  stats: dict[str, Any] = {"top": _top(scaled, _ATOM_TOP_K)}
  if finite.size:
    stats.update(
        quantiles=np.quantile(finite, _GRID, method="inverted_cdf").tolist(),
        mean=float(finite.mean()),
        std=float(finite.std()),
        min=float(finite.min()),
        max=float(finite.max()))
  return stats


def _column_stats(field: Mapping[str, Any], values: Sequence[Any],
                  is_key: bool) -> dict[str, Any]:
  bq_type = str(field.get("type") or "").upper()
  if str(field.get("mode") or "").upper() == "REPEATED" or bq_type in _NESTED:
    return {"null": sum(1 for v in values if v is None or v == [])}
  present = [v for v in values if v is not None]
  stats: dict[str, Any] = {
      "null": len(values) - len(present),
      "distinct": len({_key(v) for v in present}),
  }
  if bq_type == "STRING":
    stats["empty"] = sum(1 for v in present if v.strip() == "")
  elif bq_type == "BYTES":
    stats["empty"] = sum(1 for v in present if len(v) == 0)
  if is_key:
    return stats
  if bq_type in _NUMERIC | _TEMPORAL:
    stats.update(_numeric_stats(values))
    if bq_type in ("TIMESTAMP", "DATETIME"):
      stats["midnight"] = sum(
          1 for v in present if isinstance(v, datetime) and v.time() == time(0))
  elif bq_type in ("BOOL", "BOOLEAN"):
    stats["top"] = _top(values, _DICTIONARY_TOP_K)
  elif bq_type in ("STRING", "BYTES"):
    stats["avg_len"] = (
        sum(len(v) for v in present) / len(present) if present else None)
    stats["top"] = _top(values, _DICTIONARY_TOP_K)
  return stats


def planning_stats(
    fields: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    keys: frozenset[str] = frozenset()
) -> dict[str, Any]:
  """`context.plan.parse_planning`'s shape for `rows` (`{"rows": n,
  "columns": {name: {stat: value}}}`), computed exactly in Python
  (module docstring). A `keys` column stops after its distinct count,
  as the planning SELECT does."""
  return {
      "rows": len(rows),
      "columns": {
          str(f["name"]):
              _column_stats(f, [r.get(f["name"]) for r in rows],
                            str(f["name"]) in keys) for f in fields
      },
  }


# --------------------------------------------------------------------------
# the fixture
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FixtureTable:
  """One table of a fixture: its schema, keys and both sides' rows."""
  name: str
  fields: tuple[Mapping[str, Any], ...]
  pk: tuple[str, ...]
  identity: tuple[str, ...]
  edges: tuple[Edge, ...]
  panel_rows: int
  landing_table: str
  source_table: str
  source_rows: list[dict[str, Any]]
  synthetic_rows: list[dict[str, Any]]


def _names(spec: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
  value = spec.get(key) or ()
  if isinstance(value, str) or not all(isinstance(v, str) for v in value):
    raise ValueError(f"{where}: {key} must be a list of column names")
  return tuple(value)


def _edge(spec: Any, where: str) -> Edge:
  if not isinstance(spec, Mapping) or not set(spec) <= _EDGE_KEYS:
    raise ValueError(f"{where}: an edge is {{cols, ref, ref_cols[, "
                     "enforced]}")
  enforced = spec.get("enforced", True)
  if not isinstance(enforced, bool) or not isinstance(spec.get("ref"), str):
    raise ValueError(f"{where}: an edge needs a ref table name and a "
                     "true/false enforced")
  return Edge(
      cols=_names(spec, "cols", where),
      ref=spec["ref"],
      ref_cols=_names(spec, "ref_cols", where),
      enforced=enforced)


def _read_json(path: str) -> Any:
  try:
    with open(path, encoding="utf-8") as handle:
      return json.load(handle)
  except FileNotFoundError as exc:
    raise ValueError(f"fixture file {path} does not exist") from exc
  except json.JSONDecodeError as exc:
    raise ValueError(f"fixture file {path} is not JSON ({exc})") from exc


def _table(directory: str, project: str, spec: Any) -> FixtureTable:
  if not isinstance(spec, Mapping) or not isinstance(spec.get("name"), str):
    raise ValueError(f"{MANIFEST}: every table needs a name")
  name = spec["name"]
  where = f"{MANIFEST} table {name!r}"
  unknown = sorted(set(spec) - _TABLE_KEYS)
  if unknown:
    raise ValueError(f"{where}: unknown key(s) {unknown}; expected any of "
                     f"{sorted(_TABLE_KEYS)}")
  fields = spec.get("schema")
  if not isinstance(fields, list) or not fields or not all(
      isinstance(f, Mapping) and isinstance(f.get("name"), str)
      for f in fields):
    raise ValueError(f"{where}: schema must be a non-empty list of "
                     "{name, type, mode} fields")
  panel_rows = spec.get("panel_rows", 0)
  if isinstance(panel_rows,
                bool) or not isinstance(panel_rows, int) or panel_rows < 0:
    raise ValueError(f"{where}: panel_rows must be an int >= 0")
  sides = {
      side:
          _typed_rows(
              fields,
              _read_json(os.path.join(directory, f"{name}.{side}.json")),
              f"{name}.{side}.json")
      for side in (Side.SOURCE.value, Side.SYNTHETIC.value)
  }
  return FixtureTable(
      name=name,
      fields=tuple(fields),
      pk=_names(spec, "pk", where),
      identity=_names(spec, "identity", where),
      edges=tuple(_edge(e, where) for e in spec.get("edges") or ()),
      panel_rows=panel_rows,
      landing_table=normalize_fqn(
          spec.get("landing_table") or f"{project}.fixture_synthetic.{name}"),
      source_table=normalize_fqn(
          spec.get("source_table") or f"{project}.fixture_source.{name}"),
      source_rows=sides[Side.SOURCE.value],
      synthetic_rows=sides[Side.SYNTHETIC.value])


def _roles(tables: Sequence[FixtureTable]) -> dict[str, str]:
  """root / driven / side_input / isolated, as the planner assigns them
  from the enforced edges."""
  names = {t.name for t in tables}
  referenced = {
      e.ref
      for t in tables
      for e in t.edges
      if e.enforced and not e.external and e.ref in names
  }
  roles = {}
  for table in tables:
    enforced = [e for e in table.edges if e.enforced]
    if any(not e.external and e.ref in names for e in enforced):
      roles[table.name] = "driven"
    elif enforced:
      roles[table.name] = "side_input"
    elif table.name in referenced:
      roles[table.name] = "root"
    else:
      roles[table.name] = "isolated"
  return roles


def _panel(rows: Sequence[Mapping[str, Any]], n: int) -> Panel:
  r_rows = [dict(row) for row in rows[:n]]
  h_rows = [dict(row) for row in rows[n:2 * n]]
  digest = reference_digest(r_rows)
  return Panel(
      r_rows=r_rows,
      h_rows=h_rows,
      e_n=min(len(r_rows), EXPOSURE_ROWS),
      he_n=min(len(h_rows), EXPOSURE_ROWS),
      digest=digest,
      verified=True,
      expected_digest=digest)


@dataclass(frozen=True)
class Fixture:
  """A fixture directory, loaded (module docstring)."""
  directory: str
  project: str
  params: Mapping[str, Any]
  tables: tuple[FixtureTable, ...]

  def sources(self) -> InMemorySources:
    """The pipeline's sources: every table's two sides, in memory."""
    rows_by: dict[tuple[str, Side | str], Sequence[Mapping[str, Any]]] = {}
    for table in self.tables:
      rows_by[(table.name, Side.SOURCE)] = table.source_rows
      rows_by[(table.name, Side.SYNTHETIC)] = table.synthetic_rows
    return InMemorySources(rows_by)

  def _table_plan(self, table: FixtureTable, role: str, knobs: Knobs,
                  evaluation_id: str) -> TablePlan:
    keys = frozenset(table.pk) | {c for e in table.edges for c in e.cols}
    columns = apply_planning(
        kinds_from_schema(table.fields, keys=keys, identity=table.identity),
        planning_stats(table.fields, table.source_rows, keys),
        planning_stats(table.fields, table.synthetic_rows, keys),
        budget=knobs.budget)
    pairs = select_pairs(columns, knobs.pair_max_columns)
    panel = (
        _panel(table.source_rows, table.panel_rows)
        if table.panel_rows else None)
    landing = table.landing_table
    return TablePlan(
        name=table.name,
        landing_table=landing,
        source_table=table.source_table,
        run_id=f"{evaluation_id}-{table.name}",
        role=role,
        pk=table.pk,
        identity=table.identity,
        edges=table.edges,
        columns=tuple(columns),
        scope=ScopePlan(
            landing_table=landing,
            mode="table",
            status="ok",
            reason="offline fixture: the landing rows as they are",
            read_table=landing,
            prepare_sql=(),
            window=(None, None),
            expected_rows=len(table.synthetic_rows),
            read_expr=f"`{landing}`"),
        source_read_table=table.source_table,
        source_pinned=False,
        panel=panel,
        pairs=pairs,
        encoding_plan_digest=encoding_plan_digest(columns, pairs, table.edges),
        model="fixture",
        synthetic_read_table=landing,
        rows_source=len(table.source_rows),
        rows_synthetic=len(table.synthetic_rows),
        sample_rate_source=1.0,
        sample_rate_synthetic=1.0,
        reference_digest=panel.digest if panel is not None else None)

  def plan(self, *, knobs: Knobs, mode: str, trigger: str, runner: str,
           now: datetime, evaluation_id: str) -> EvaluationPlan:
    """The fixture's `EvaluationPlan` (module docstring): every table
    planned from its rows, `evaluation_key` from the tables, the mode
    and the value knobs, `salt = blake2b(evaluation_key)`."""
    roles = _roles(self.tables)
    tables = tuple(
        self._table_plan(t, roles[t.name], knobs, evaluation_id)
        for t in self.tables)
    catalogue_version = load_catalogue().version
    payload = json.dumps(
        {
            "fixture": [t.encoding_plan_digest for t in tables],
            "tables": [t.landing_table for t in tables],
            "catalogue_version": catalogue_version,
            "evaluator_version": EVALUATOR_VERSION,
            "mode": mode,
            "knobs": knobs.key_dict(),
        },
        sort_keys=True,
        separators=(",", ":"))
    key = hashlib.blake2b(payload.encode(), digest_size=16).hexdigest()
    launch = LaunchContext(
        generation_job_id=None,
        job_name=None,
        region=None,
        started_at=None,
        finished_at=None,
        base_run_id=None,
        run_ids=tuple(str(t.run_id) for t in tables),
        tables_in_order=tuple(t.landing_table for t in tables),
        reference_table=None,
        write_disposition=None,
        relationships_uri=None,
        params=dict(self.params),
        model_sha=None,
        model_name="fixture",
        adjusted_model_uri=None,
        params_source="manual",
        writes=(),
        warnings=(f"offline fixture {self.directory}: planned from its "
                  "rows, nothing read from BigQuery",))
    return EvaluationPlan(
        evaluation_id=evaluation_id,
        evaluation_key=key,
        evaluated_at=now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        salt=hashlib.blake2b(key.encode(), digest_size=16).hexdigest(),
        mode=mode,
        trigger=trigger,
        runner=runner,
        launch=launch,
        models=(),
        model_sha=None,
        tables=tables,
        knobs=knobs,
        prepare_sql=(),
        bq_bytes_estimate=0,
        predicted_shuffle_gb=predict_shuffle_gb(tables),
        warnings=launch.warnings,
        catalogue_version=catalogue_version,
        temp_dataset=knobs.temp_dataset or
        f"{self.project}.{knobs.output_dataset}")


def load_fixture(directory: str) -> Fixture:
  """Load the fixture under `directory` (module docstring).

  Raises:
    ValueError: a missing or malformed manifest or rows file, an unknown
      key, or a cell that is not its column's type (the message names
      the file, table or column, never a value).
  """
  manifest = _read_json(os.path.join(directory, MANIFEST))
  if not isinstance(manifest, Mapping):
    raise ValueError(f"{MANIFEST}: expected a JSON object")
  project = manifest.get("project") or "demo-project"
  params = manifest.get("params") or {}
  specs = manifest.get("tables")
  if not isinstance(project, str) or not isinstance(params, Mapping):
    raise ValueError(f"{MANIFEST}: project must be text and params an object")
  if not isinstance(specs, list) or not specs:
    raise ValueError(f"{MANIFEST}: tables must be a non-empty list")
  tables = tuple(_table(directory, project, spec) for spec in specs)
  names = [t.name for t in tables]
  if len(set(names)) != len(names):
    raise ValueError(f"{MANIFEST}: table names repeat ({names})")
  return Fixture(
      directory=directory, project=project, params=params, tables=tables)
