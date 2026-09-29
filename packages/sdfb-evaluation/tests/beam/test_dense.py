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
"""Tests for `sdfb_evaluation.beam.dense` (Task 21): the dense, mergeable
profile accumulators and the metrics and profiles they yield.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import base64
import bz2
import dataclasses
import json
import math
import pickle
import random
import re
import zlib
import time as clock
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import apache_beam as beam
import numpy as np
import pytest
from apache_beam.internal import pickler
from apache_beam.portability import common_urns
from apache_beam.portability.api import beam_runner_api_pb2
from apache_beam.testing.test_pipeline import TestPipeline as BeamTestPipeline
from apache_beam.testing.util import assert_that
from hypothesis import given, settings
from hypothesis import strategies as st

from sdfb_evaluation.beam import census, dense
from sdfb_evaluation.beam.census import CensusMetrics
from sdfb_evaluation.beam.dense import (
    CHAR_CLASSES,
    NULL_PATTERN_CAP,
    OWNED_METRIC_IDS,
    PAIR_BINS,
    RARE_COUNT,
    DenseMetrics,
    DenseProfile,
    DenseProfileCombineFn,
    DenseSpec,
    calendar_counts,
    char_class_masks,
    dense_outputs,
    pit_from_grid,
)
from sdfb_evaluation.beam.encode import BatchEncoder, EncodeSide
from sdfb_evaluation.beam.io import InMemorySources
from sdfb_evaluation.beam.label_key import LabelKey
from sdfb_evaluation.canonical import hash64, hashed_label, numeric_value
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.context import plan as plan_module
from sdfb_evaluation.scoring import status_for, to_metric_row
from sdfb_evaluation.stats import binned, dependence, distances, noise, shapes
from sdfb_evaluation.types import ColumnKind, MetricValue, ProfileValue, Status

from .dense_data import orders_table, planned_table
from .tables import make_panel

SALT = "d3n5" * 8
LABEL_KEY = b"dense-test-label-key-32-bytes-00"
_CATALOGUE = load_catalogue()
_SIDES = ("source", "synthetic", "reference", "holdout")
_TOL = 1e-9


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _encode(table: Any, side: str, rows: Sequence[Mapping[str, Any]]) -> Any:
  return BatchEncoder.from_table(
      table, side, salt=SALT, subsample_rate=1.0).encode(rows)


def _profile(table: Any,
             side: str,
             rows: Sequence[Mapping[str, Any]],
             chunk: int = 700) -> DenseProfile:
  """The pure path: encode in chunks, profile each, merge in order."""
  spec = DenseSpec.from_table(table)
  merged: DenseProfile | None = None
  for start in range(0, len(rows), chunk):
    part = DenseProfile.from_batch(
        spec, _encode(table, side, rows[start:start + chunk]))
    merged = part if merged is None else merged.merge(part)
  return merged if merged is not None else DenseProfile.empty(spec, side)


def _pure(
    table: Any, rows_by: Mapping[str, Sequence[Mapping[str, Any]]]
) -> tuple[list[MetricValue], list[ProfileValue]]:
  spec = DenseSpec.from_table(table)
  profiles = {
      side: _profile(table, side, rows) for side, rows in rows_by.items()
  }
  return dense_outputs(spec, profiles, label_key=LABEL_KEY)


def _collect(pcoll: beam.PCollection, path: Path, label: str) -> None:
  """Pickle a PCollection's elements to `path` (DirectRunner, in process)."""

  def dump(actual: Sequence[Any]) -> None:
    path.write_bytes(pickle.dumps(list(actual)))

  assert_that(pcoll, dump, label=label)


def _run(tables: Sequence[Any], rows_by: Mapping[str, Mapping[str, list]],
         tmp_path: Path) -> tuple[list[MetricValue], list[ProfileValue], dict]:
  """The Beam path on the DirectRunner: read, encode, dense."""
  sources = InMemorySources({
      (t.name, side): rows_by[t.name][side] for t in tables
      for side in ("source", "synthetic")
      if side in rows_by[t.name]
  })
  with BeamTestPipeline() as p:
    encoded = []
    for table in tables:
      for side in rows_by[table.name]:
        rows = sources.read(p, table, side)
        encoded.append(rows | EncodeSide(table, side, salt=SALT))
    key = p | "Key" >> beam.Create([LABEL_KEY])
    outputs = (
        encoded | "Flatten" >> beam.Flatten()
        | DenseMetrics(tables, label_key=key))
    _collect(outputs["metrics"], tmp_path / "metrics.pkl", "Metrics")
    _collect(outputs["profiles"], tmp_path / "profiles.pkl", "Profiles")
    _collect(outputs["accumulators"], tmp_path / "acc.pkl", "Accumulators")
  metrics = pickle.loads((tmp_path / "metrics.pkl").read_bytes())
  profiles = pickle.loads((tmp_path / "profiles.pkl").read_bytes())
  accumulators = dict(pickle.loads((tmp_path / "acc.pkl").read_bytes()))
  return metrics, profiles, accumulators


def _by_key(metrics: Sequence[MetricValue]) -> dict[tuple, MetricValue]:
  out: dict[tuple, MetricValue] = {}
  for mv in metrics:
    key = (mv.metric_id, mv.column, mv.column_2)
    assert key not in out, f"duplicate metric row {key}"
    out[key] = mv
  return out


def _planning(value: Any) -> float | None:
  if value is None:
    return None
  reading = numeric_value(value)
  return reading if reading is not None and math.isfinite(reading) else None


def _finite(rows: Sequence[Mapping[str, Any]], name: str) -> np.ndarray:
  values = [_planning(r[name]) for r in rows]
  return np.array([v for v in values if v is not None], dtype=float)


def _is_null(value: Any) -> bool:
  return value is None or value == []


# --------------------------------------------------------------------------
# the brief's six tests
# --------------------------------------------------------------------------
def _smd(a: np.ndarray, b: np.ndarray) -> float:
  return float(abs(b.mean() - a.mean()) / a.std())


def _std_ratio(a: np.ndarray, b: np.ndarray) -> float:
  return float(b.std() / a.std())


def _coverage(a: np.ndarray, b: np.ndarray) -> float:
  span = min(b.max(), a.max()) - max(b.min(), a.min())
  return float(max(0.0, span) / (a.max() - a.min()))


def _zero_delta(a: np.ndarray, b: np.ndarray) -> float:
  return float(abs(np.mean(b == 0) - np.mean(a == 0)))


def _legacy_deciles(edges: np.ndarray, counts: np.ndarray,
                    x: np.ndarray) -> list[float]:
  inner = binned.quantiles_from_bins(edges, counts,
                                     [k / 10 for k in range(1, 10)])
  return [x.min(), *np.clip(inner, x.min(), x.max()), x.max()]


def _expected_grid(table: Any, rows_by: Mapping[str, list],
                   out: dict[tuple, tuple]) -> None:
  """Direct Task 6/7 calls per numeric/temporal column: (value, baseline)."""
  for column in table.columns:
    if column.kind not in (ColumnKind.NUMERIC, ColumnKind.TEMPORAL):
      continue
    name = column.name
    xs, xy, xr = (
        _finite(rows_by[s], name) for s in ("source", "synthetic", "reference"))
    edges = binned.union_edges(column.quantiles_src, column.quantiles_syn,
                               column.atoms)
    cs, cy, cr = (binned.bin_counts(x, edges) for x in (xs, xy, xr))
    deciles = binned.decile_edges(column.quantiles_src)
    ds, dy, dr = (binned.bin_counts(x, deciles) for x in (xs, xy, xr))

    def pair(fn: Any, a: Any, b: Any, c: Any) -> tuple:
      return fn(a, b), fn(a, c)

    out[("column.ks", name, None)] = (binned.ks_bracket(cs, cy)[0],
                                      binned.ks_bracket(cs, cr)[0])
    out[("column.pit_w1", name, None)] = pair(binned.pit_w1, cs, cy, cr)
    out[("column.wasserstein", name,
         None)] = (binned.w1_from_bins(edges, cs,
                                       cy), binned.w1_from_bins(edges, cs, cr))
    out[("column.jsd", name, None)] = pair(distances.jsd_bits, ds, dy, dr)
    out[("column.psi", name, None)] = pair(distances.psi, ds, dy, dr)
    out[("column.smd", name, None)] = pair(_smd, xs, xy, xr)
    out[("column.std_ratio", name, None)] = pair(_std_ratio, xs, xy, xr)
    out[("column.range_coverage", name, None)] = pair(_coverage, xs, xy, xr)
    lo, hi = column.quantiles_src[0], column.quantiles_src[-1]
    out[("field.range_adherence", name,
         None)] = (float(np.mean((xy >= lo) & (xy <= hi))), None)
    if column.kind is ColumnKind.NUMERIC:
      # the legacy rule's deciles: each side's exact min and max at 0 and
      # 1, the inner nine read off its union bins
      sides = ((cs, xs), (cy, xy), (cr, xr))
      dec = [_legacy_deciles(edges, c, x) for c, x in sides]
      out[("column.decile_ks_legacy", name,
           None)] = (binned.decile_ks_legacy(dec[0], dec[1]),
                     binned.decile_ks_legacy(dec[0], dec[2]))
      out[("column.zero_rate_delta", name,
           None)] = pair(_zero_delta, xs, xy, xr)


def _calendar(rows: Sequence[Mapping[str, Any]], name: str,
              part: str) -> np.ndarray:
  """dow/month/hour counts straight from the Python values (UTC)."""
  size = {"dow": 7, "month": 12, "hour": 24}[part]
  counts = np.zeros(size, dtype=np.int64)
  for row in rows:
    v = row[name]
    if v is None:
      continue
    if part == "dow":
      counts[v.weekday()] += 1
    elif part == "month":
      counts[v.month - 1] += 1
    else:
      counts[v.hour] += 1
  return counts


def _expected_temporal(table: Any, rows_by: Mapping[str, list],
                       out: dict[tuple, tuple]) -> None:
  for column in table.columns:
    if column.kind is not ColumnKind.TEMPORAL:
      continue
    parts = ["hour"] if column.bq_type == "TIME" else ["dow", "month"]
    if not column.day_granularity and column.bq_type != "TIME":
      parts.append("hour")
    for part in parts:
      cs, cy, cr = (
          _calendar(rows_by[s], column.name, part)
          for s in ("source", "synthetic", "reference"))
      out[(f"column.{part}_tvd", column.name, None)] = (distances.tvd(cs, cy),
                                                        distances.tvd(cs, cr))


def _text_values(rows: Sequence[Mapping[str, Any]], name: str) -> list[str]:
  return [str(r[name]) for r in rows if r[name] is not None]


def _length_hist(values: Sequence[str]) -> np.ndarray:
  return np.bincount(np.minimum([len(v) for v in values], 256), minlength=257)


def _expected_strings(table: Any, rows_by: Mapping[str, list],
                      out: dict[tuple, tuple]) -> None:
  for column in table.columns:
    if column.kind not in (ColumnKind.CATEGORICAL, ColumnKind.TEXT,
                           ColumnKind.IDENTIFIER):
      continue
    name = column.name
    vs, vy, vr = (
        _text_values(rows_by[s], name)
        for s in ("source", "synthetic", "reference"))
    hs, hy, hr = (_length_hist(v) for v in (vs, vy, vr))
    out[("column.length_ks", name, None)] = (binned.ks_bracket(hs, hy)[0],
                                             binned.ks_bracket(hs, hr)[0])
    fs, fy, fr = (shapes.char_class_fractions(v) for v in (vs, vy, vr))
    out[("column.char_class_l1", name, None)] = (shapes.char_class_l1(fs, fy),
                                                 shapes.char_class_l1(fs, fr))

    def empty(side: str, name: str = name) -> float:
      rows = rows_by[side]
      return sum(
          1 for r in rows
          if r[name] is not None and str(r[name]).strip() == "") / len(rows)

    es, ey, er = (empty(s) for s in ("source", "synthetic", "reference"))
    out[("column.empty_rate_delta", name, None)] = (abs(ey - es), abs(er - es))


def _expected_nulls(table: Any, rows_by: Mapping[str, list],
                    out: dict[tuple, tuple]) -> None:
  for column in table.columns:
    rates = {
        side:
            sum(1 for r in rows_by[side] if _is_null(r[column.name])) /
            len(rows_by[side]) for side in ("source", "synthetic", "reference")
    }
    out[("column.null_rate_delta", column.name,
         None)] = (abs(rates["synthetic"] - rates["source"]),
                   abs(rates["reference"] - rates["source"]))


def _pair_codes(column: Any, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
  codes = []
  if column.kind in (ColumnKind.NUMERIC, ColumnKind.TEMPORAL):
    deciles = binned.decile_edges(column.quantiles_src)
    for row in rows:
      v = _planning(row[column.name])
      codes.append(
          PAIR_BINS if v is
          None else int(np.searchsorted(deciles, v, side="left")))
  else:
    index = {code: i for i, code in enumerate(column.dictionary)}
    for row in rows:
      v = row[column.name]
      codes.append(
          PAIR_BINS if v is
          None else index.get(hash64(column.name, v), PAIR_BINS - 1))
  return np.array(codes, dtype=np.int64)


def _joint(a: np.ndarray, b: np.ndarray) -> np.ndarray:
  table = np.zeros((PAIR_BINS + 1, PAIR_BINS + 1), dtype=np.int64)
  np.add.at(table, (a, b), 1)
  return table


def _pearson(rows: Sequence[Mapping[str, Any]], a: str, b: str) -> float:
  pairs = [(_planning(r[a]), _planning(r[b])) for r in rows]
  xy = np.array([p for p in pairs if None not in p], dtype=float)
  return float(np.corrcoef(xy[:, 0], xy[:, 1])[0, 1])


def _spearman(column_a: Any, column_b: Any, rows: Sequence[Mapping[str, Any]],
              side: str) -> float:
  grid = "quantiles_syn" if side == "synthetic" else "quantiles_src"
  xa = np.array([
      np.nan
      if _planning(r[column_a.name]) is None else _planning(r[column_a.name])
      for r in rows
  ])
  xb = np.array([
      np.nan
      if _planning(r[column_b.name]) is None else _planning(r[column_b.name])
      for r in rows
  ])
  pa = pit_from_grid(np.asarray(getattr(column_a, grid)), xa)
  pb = pit_from_grid(np.asarray(getattr(column_b, grid)), xb)
  ok = ~np.isnan(pa) & ~np.isnan(pb)
  return float(np.corrcoef(pa[ok], pb[ok])[0, 1])


def _expected_pairs(table: Any, rows_by: Mapping[str, list],
                    out: dict[tuple, tuple]) -> None:
  grid = (ColumnKind.NUMERIC, ColumnKind.TEMPORAL)
  deltas_syn, deltas_ref = [], []
  for i, j in table.pairs:
    a, b = table.columns[i], table.columns[j]
    key = (a.name, b.name)
    if a.kind in grid and b.kind in grid:
      rho = {
          s: _pearson(rows_by[s], a.name, b.name)
          for s in ("source", "synthetic", "reference")
      }
      out[("pair.pearson_delta",
           *key)] = (abs(rho["source"] - rho["synthetic"]),
                     abs(rho["source"] - rho["reference"]))
      deltas_syn.append(rho["source"] - rho["synthetic"])
      deltas_ref.append(rho["source"] - rho["reference"])
      sp = {
          s: _spearman(a, b, rows_by[s], s)
          for s in ("source", "synthetic", "reference")
      }
      out[("pair.spearman_delta", *key)] = (abs(sp["source"] - sp["synthetic"]),
                                            abs(sp["source"] - sp["reference"]))
    tables = {
        s: _joint(_pair_codes(a, rows_by[s]), _pair_codes(b, rows_by[s]))
        for s in ("source", "synthetic", "reference")
    }
    for metric_id, fn in (("pair.cramers_v_delta",
                           dependence.cramers_v_bias_corrected),
                          ("pair.nmi_delta", dependence.nmi_min)):
      v = {s: fn(t) for s, t in tables.items()}
      out[(metric_id, *key)] = (abs(v["source"] - v["synthetic"]),
                                abs(v["source"] - v["reference"]))
    out[("pair.contingency_tvd",
         *key)] = (dependence.contingency_tvd(tables["source"],
                                              tables["synthetic"]),
                   dependence.contingency_tvd(tables["source"],
                                              tables["reference"]))
  rms_max = dependence.corr_rms_max(np.array(deltas_syn))
  base = dependence.corr_rms_max(np.array(deltas_ref))
  out[("table.corr_rms_delta", None, None)] = (rms_max[0], base[0])
  out[("table.corr_max_delta", None, None)] = (rms_max[1], base[1])


def _patterns(table: Any, rows: Sequence[Mapping[str, Any]]) -> Counter:
  return Counter(
      sum(1 << j
          for j, c in enumerate(table.columns)
          if _is_null(row[c.name]))
      for row in rows)


def _expected_null_patterns(table: Any, rows_by: Mapping[str, list],
                            out: dict[tuple, tuple]) -> None:
  counts = {
      s: _patterns(table, rows_by[s])
      for s in ("source", "synthetic", "reference")
  }
  top = sorted(counts["source"], key=lambda p: (-counts["source"][p], p))[:64]

  def vector(c: Counter) -> list[int]:
    head = [c.get(p, 0) for p in top]
    return [*head, sum(c.values()) - sum(head)]

  vs, vy, vr = (vector(counts[s]) for s in ("source", "synthetic", "reference"))
  out[("row.null_pattern_tvd", None, None)] = (distances.tvd(vs, vy),
                                               distances.tvd(vs, vr))


def _with_invalid_amounts(rows: list[dict]) -> list[dict]:
  """Six synthetic amounts that BigQuery holds but no metric can use."""
  rows = [dict(r) for r in rows]
  for i, bad in enumerate([math.nan] * 5 + [math.inf]):
    rows[i]["amount"] = bad
  return rows


@pytest.fixture(scope="module", name="orders_run")
def fixture_orders_run(tmp_path_factory: pytest.TempPathFactory) -> tuple:
  """One DirectRunner pass over the orders table, shared by the tests that
  only read its output (six synthetic amounts are non-finite)."""
  table, rows_by = orders_table()
  rows_by["synthetic"] = _with_invalid_amounts(rows_by["synthetic"])
  metrics, _, accumulators = _run([table], {"orders": rows_by},
                                  tmp_path_factory.mktemp("orders"))
  return table, rows_by, metrics, accumulators


def test_dense_equals_pure_stats(orders_run):
  table, rows_by, metrics, accumulators = orders_run
  got = _by_key(metrics)

  expected: dict[tuple, tuple] = {}
  _expected_grid(table, rows_by, expected)
  _expected_temporal(table, rows_by, expected)
  _expected_strings(table, rows_by, expected)
  _expected_nulls(table, rows_by, expected)
  _expected_pairs(table, rows_by, expected)
  _expected_null_patterns(table, rows_by, expected)
  assert expected, "the fixture exercises no metric"
  for key, (value, baseline) in expected.items():
    mv = got.get(key)
    assert mv is not None, f"{key} was not emitted"
    assert mv.value == pytest.approx(value, abs=_TOL), key
    if _CATALOGUE.get(key[0]).baseline:
      assert mv.baseline_value == pytest.approx(baseline, abs=_TOL), key

  # type validity: 6 non-finite synthetic amounts out of the non-null ones
  amounts = [r["amount"] for r in rows_by["synthetic"]]
  nonnull = sum(1 for a in amounts if a is not None)
  validity = got[("field.type_validity", "amount", None)]
  assert validity.value == pytest.approx((nonnull - 6) / nonnull, abs=_TOL)
  assert validity.detail["invalid"] == 6

  # exact counts in the accumulators
  source = accumulators[("orders", "source")]
  names = [c.name for c in table.columns]
  for j, name in enumerate(names):
    assert int(source.nulls[j]) == sum(
        1 for r in rows_by["source"] if _is_null(r[name])), name
  spec = DenseSpec.from_table(table)
  amount = next(i for i, g in enumerate(spec.grids) if g.name == "amount")
  edges = spec.grids[amount].union
  np.testing.assert_array_equal(
      source.union[amount],
      binned.bin_counts(_finite(rows_by["source"], "amount"), edges))
  note = next(i for i, s in enumerate(spec.strings) if s.name == "note")
  np.testing.assert_array_equal(
      source.lengths[note],
      _length_hist(_text_values(rows_by["source"], "note")))
  assert source.null_patterns == dict(_patterns(table, rows_by["source"]))
  assert source.null_overflow == 0
  assert source.rows == len(rows_by["source"])


def test_shift_500_fails_ks_pitw1_jsd(tmp_path):
  table, rows_by = orders_table(shift=500.0)
  metrics, _, _ = _run([table], {"orders": rows_by}, tmp_path)
  got = _by_key(metrics)
  for metric_id in ("column.ks", "column.pit_w1", "column.jsd"):
    mv = got[(metric_id, "amount", None)]
    assert status_for(_CATALOGUE.get(metric_id), mv) is Status.FAIL, mv
  # the unshifted column of the same table passes
  quantity = got[("column.ks", "quantity", None)]
  assert status_for(_CATALOGUE.get("column.ks"), quantity) is not Status.FAIL


_NULL_HEAVY_FIELDS = (
    {
        "name": "id",
        "type": "INT64",
        "mode": "REQUIRED"
    },
    {
        "name": "score",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
    {
        "name": "label",
        "type": "STRING",
        "mode": "NULLABLE"
    },
)


def _null_heavy(n: int, every: int, seed: int) -> list[dict]:
  rng = random.Random(seed)
  return [{
      "id": i,
      "score": rng.random() if i % every == 0 else None,
      "label": rng.choice("abc") if i % every == 1 else None,
  } for i in range(n)]


def test_null_heavy_column_counts_exact_without_keys(tmp_path):
  source = _null_heavy(20_000, 100, seed=3)
  synthetic = _null_heavy(18_000, 50, seed=4)
  table = planned_table(
      "events", _NULL_HEAVY_FIELDS, source, synthetic, pk=("id",))
  metrics, _, accumulators = _run(
      [table], {"events": {
          "source": source,
          "synthetic": synthetic
      }}, tmp_path)
  # the dense pass shuffles on (table, side) only: no value, no NULL, keys
  assert set(accumulators) == {("events", "source"), ("events", "synthetic")}
  names = [c.name for c in table.columns]
  for side, rows in (("source", source), ("synthetic", synthetic)):
    acc = accumulators[("events", side)]
    assert acc.rows == len(rows)
    for j, name in enumerate(names):
      assert int(acc.nulls[j]) == sum(1 for r in rows if r[name] is None)
  got = _by_key(metrics)
  p_src = sum(1 for r in source if r["score"] is None) / len(source)
  p_syn = sum(1 for r in synthetic if r["score"] is None) / len(synthetic)
  delta = got[("column.null_rate_delta", "score", None)]
  assert delta.value == pytest.approx(abs(p_syn - p_src), abs=1e-15)
  assert delta.source_value == pytest.approx(p_src, abs=1e-15)
  assert delta.n_source == len(source)


def test_baseline_present_for_fidelity_metrics(orders_run):
  table, rows_by, metrics, _ = orders_run
  checked = 0
  for mv in metrics:
    metric = _CATALOGUE.get(mv.metric_id)
    if mv.value is None:
      continue
    if metric.baseline:
      assert mv.baseline_value is not None, (mv.metric_id, mv.column)
      checked += 1
    else:
      assert mv.baseline_value is None, mv.metric_id
  assert checked > 100
  # without a reference side the baseline says why it is missing
  no_ref = {k: v for k, v in rows_by.items() if k != "reference"}
  metrics, _ = _pure(table, no_ref)
  ks = _by_key(metrics)[("column.ks", "amount", None)]
  assert ks.baseline_value is None
  assert "reference" in ks.detail["baseline_reason"]


_SCALE_FIELDS = (
    {
        "name": "id",
        "type": "INT64",
        "mode": "REQUIRED"
    },
    {
        "name": "flat",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
    {
        "name": "same",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
    {
        "name": "spiky",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
    {
        "name": "wide",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
)


def _scale_rows(n: int, seed: int, *, flat: float,
                synthetic: bool) -> list[dict]:
  rng = random.Random(seed)
  rows = []
  for i in range(n):
    rows.append({
        "id": i,
        # source constant, synthetic spread: scale undefined
        "flat": rng.gauss(flat, 1.0) if synthetic else flat,
        # both sides the same constant: R20 "same"
        "same": 7.0,
        # 90 % zeros (IQR = 0) but a real spread (sigma > 0)
        "spiky": 0.0 if rng.random() < 0.9 else rng.expovariate(0.1),
        # a normal column; the synthetic side collapses to a constant
        "wide": 3.0 if synthetic else rng.gauss(0.0, 1.0) + 0.5 * (i % 2),
    })
  return rows


def test_zero_iqr_column_not_evaluated_where_scale_needed(tmp_path):
  source = _scale_rows(2000, 5, flat=4.0, synthetic=False)
  synthetic = _scale_rows(2000, 6, flat=4.0, synthetic=True)
  table = planned_table(
      "scales",
      _SCALE_FIELDS,
      source,
      synthetic,
      pk=("id",),
      panel=make_panel(source[:300], source[300:600]))
  metrics, _, _ = _run(
      [table], {
          "scales": {
              "source": source,
              "synthetic": synthetic,
              "reference": source[:300],
              "holdout": source[300:600],
          }
      }, tmp_path)
  got = _by_key(metrics)
  # a constant source column: every scale-needing metric says why not
  for metric_id in ("column.smd", "column.std_ratio", "column.range_coverage"):
    mv = got[(metric_id, "flat", None)]
    assert mv.value is None, metric_id
    assert "constant" in mv.detail["reason"], metric_id
  # the scale-free distances still measure it
  for metric_id in ("column.ks", "column.pit_w1", "column.jsd", "column.psi"):
    assert got[(metric_id, "flat", None)].value is not None, metric_id
  # both sides the same constant (Ruling R20): the same dispersion
  assert got[("column.std_ratio", "same", None)].value == 1.0
  assert got[("column.smd", "same", None)].value == 0.0
  assert got[("column.range_coverage", "same", None)].value == 1.0
  assert got[("column.wasserstein", "same", None)].value == 0.0
  # IQR = 0 with a real spread: no catalogue metric divides by the IQR
  for metric_id in ("column.smd", "column.std_ratio", "column.range_coverage",
                    "column.wasserstein"):
    assert got[(metric_id, "spiky", None)].value is not None, metric_id
  # a synthetic column collapsed to a constant: std_ratio 0, SMD finite,
  # and on a pair it carries no association (R20), so the delta is |rho_src|
  assert got[("column.std_ratio", "wide", None)].value == 0.0
  collapsed = got[("pair.pearson_delta", "spiky", "wide")]
  assert collapsed.detail["synthetic_constant"] is True
  assert collapsed.synthetic_value == 0.0
  assert collapsed.value == pytest.approx(abs(collapsed.source_value))
  for metric_id in ("pair.cramers_v_delta", "pair.nmi_delta"):
    assert got[(metric_id, "spiky", "wide")].synthetic_value == 0.0
  for mv in metrics:
    for number in (mv.value, mv.baseline_value, mv.noise_floor, mv.ci_low,
                   mv.ci_high):
      assert number is None or math.isfinite(number), mv


def _batches(table: Any, side: str, rows: list[dict],
             sizes: Sequence[int]) -> list[Any]:
  out, start = [], 0
  for size in sizes:
    if start >= len(rows):
      break
    out.append(_encode(table, side, rows[start:start + size]))
    start += size
  if start < len(rows):
    out.append(_encode(table, side, rows[start:]))
  return out


def _assert_same(a: DenseProfile, b: DenseProfile) -> None:
  """Counts exactly equal; moments and co-moments to float rounding."""
  assert (a.table, a.side, a.rows, a.rows_m) == (b.table, b.side, b.rows,
                                                 b.rows_m)
  for name in ("nulls", "nulls_m", "in_range", "dow", "month", "hour",
               "str_nonnull", "str_empty", "str_nonempty", "lengths",
               "classes"):
    np.testing.assert_array_equal(getattr(a, name), getattr(b, name), name)
  for name in ("union", "union_left", "profile", "profile_left", "deciles"):
    for x, y in zip(getattr(a, name), getattr(b, name), strict=True):
      np.testing.assert_array_equal(x, y, name)
  for x, y in zip(a.moments, b.moments, strict=True):
    assert (x.n, x.zeros, x.nonfinite, x.min,
            x.max) == (y.n, y.zeros, y.nonfinite, y.min, y.max)
    np.testing.assert_allclose([x.mean, x.m2, x.m3, x.m4],
                               [y.mean, y.m2, y.m3, y.m4],
                               rtol=1e-9,
                               atol=1e-6)
  assert a.null_patterns == b.null_patterns
  assert a.null_overflow == b.null_overflow
  assert a.literals == b.literals
  if a.bivariate is None or b.bivariate is None:
    assert a.bivariate is b.bivariate
    return
  np.testing.assert_array_equal(a.bivariate.counts2d, b.bivariate.counts2d)
  np.testing.assert_allclose(
      a.bivariate.com_std, b.bivariate.com_std, rtol=1e-9, atol=1e-6)
  np.testing.assert_allclose(
      a.bivariate.com_pit, b.bivariate.com_pit, rtol=1e-9, atol=1e-9)


def test_accumulator_merge_commutative():
  table, rows_by = orders_table(n_source=1500, n_synthetic=10, n_reference=10)
  spec = DenseSpec.from_table(table)
  rows = rows_by["source"]
  a = DenseProfile.from_batch(spec, _encode(table, "source", rows[:600]))
  b = DenseProfile.from_batch(spec, _encode(table, "source", rows[600:]))
  _assert_same(a.merge(b), b.merge(a))
  whole = DenseProfile.from_batch(spec, _encode(table, "source", rows))
  _assert_same(a.merge(b), whole)
  # merge is pure: neither operand moved
  _assert_same(
      a, DenseProfile.from_batch(spec, _encode(table, "source", rows[:600])))
  empty = DenseProfile.empty(spec, "source")
  _assert_same(empty.merge(whole), whole)
  with pytest.raises(ValueError, match="side"):
    a.merge(DenseProfile.empty(spec, "synthetic"))


_MERGE_TABLE, _MERGE_ROWS = orders_table(
    n_source=900, n_synthetic=10, n_reference=10)
_MERGE_SPEC = DenseSpec.from_table(_MERGE_TABLE)
_MERGE_WHOLE = DenseProfile.from_batch(
    _MERGE_SPEC, _encode(_MERGE_TABLE, "source", _MERGE_ROWS["source"]))


@settings(max_examples=25, deadline=None)
@given(
    sizes=st.lists(st.integers(1, 400), min_size=1, max_size=6),
    order=st.randoms(use_true_random=False))
def test_merging_split_accumulators_in_any_order_equals_one(sizes, order):
  parts = [
      DenseProfile.from_batch(_MERGE_SPEC, batch) for batch in _batches(
          _MERGE_TABLE, "source", _MERGE_ROWS["source"], sizes)
  ]
  order.shuffle(parts)
  while len(parts) > 1:  # a random merge tree
    i = order.randrange(len(parts) - 1)
    parts[i:i + 2] = [parts[i].merge(parts[i + 1])]
  _assert_same(parts[0], _MERGE_WHOLE)


# --------------------------------------------------------------------------
# contract tests: catalogue coverage, R60, R41
# --------------------------------------------------------------------------
def test_catalogue_coverage_every_owned_id_emitted_or_explained(orders_run):
  table, _, metrics, _ = orders_run
  ids = set(_CATALOGUE.ids())
  assert set(OWNED_METRIC_IDS) <= ids
  emitted = {mv.metric_id for mv in metrics}
  assert emitted == set(OWNED_METRIC_IDS), (
      f"never emitted: {sorted(set(OWNED_METRIC_IDS) - emitted)}; "
      f"not owned: {sorted(emitted - set(OWNED_METRIC_IDS))}")
  for mv in metrics:
    metric = _CATALOGUE.get(mv.metric_id)
    if mv.value is None:
      assert mv.detail.get("reason"), mv
    if metric.level in ("field", "column", "pair"):
      reach = dense.APPLIES_TO.get(mv.metric_id, metric.kinds)
      assert mv.column_kind in {str(k) for k in reach}, mv.metric_id
      assert mv.column_kind in metric.kinds, (mv.metric_id, mv.column_kind)
      assert mv.column is not None
    assert mv.encoding_plan_digest == table.encoding_plan_digest
    # every row goes through the metric writer
    row = to_metric_row(
        mv,
        evaluation_id="e1",
        evaluated_at="2026-09-29T00:00:00+00:00",
        landing_table=table.landing_table,
        source_table=table.source_table)
    json.dumps(row, allow_nan=False)
  # census-owned ids stay out of the dense pass
  for census_id in ("column.tvd", "column.entropy_ratio",
                    "column.distinct_ratio", "field.category_adherence"):
    assert census_id not in emitted
  # the owned-kind reach per metric: never a kind the catalogue excludes
  for metric_id, reach in dense.APPLIES_TO.items():
    assert metric_id in OWNED_METRIC_IDS
    assert {str(k) for k in reach} <= set(_CATALOGUE.get(metric_id).kinds)


def test_null_pattern_tvd_not_evaluated_when_null_bits_incomplete():
  fields = [{
      "name": "id",
      "type": "INT64",
      "mode": "REQUIRED"
  }] + [{
      "name": f"c{i:02d}",
      "type": "FLOAT64",
      "mode": "NULLABLE"
  } for i in range(65)]
  rng = random.Random(9)

  def rows(n: int) -> list[dict]:
    return [{
        "id": k,
        **{
            f["name"]: (None if rng.random() < 0.1 else rng.random())
            for f in fields[1:]
        }
    }
            for k in range(n)]

  source, synthetic = rows(300), rows(300)
  table = planned_table("wide", fields, source, synthetic, pk=("id",))
  metrics, profiles = _pure(table, {"source": source, "synthetic": synthetic})
  tvd = _by_key(metrics)[("row.null_pattern_tvd", None, None)]
  assert tvd.value is None
  assert "64" in tvd.detail["reason"]
  assert not [p for p in profiles if p.profile_kind == "null_patterns"]


def test_interval_metrics_carry_ci_and_scalar_metrics_a_noise_floor():
  table, rows_by = orders_table()
  metrics, _ = _pure(table, rows_by)
  seen = Counter()
  for mv in metrics:
    if mv.value is None:
      continue
    method = _CATALOGUE.get(mv.metric_id).noise_floor
    if method in ("wilson", "newcombe", "delong"):
      assert mv.ci_low is not None and mv.ci_high is not None, mv
      assert mv.ci_low <= mv.ci_high, mv
      assert mv.noise_floor is None, mv
      seen["interval"] += 1
    elif method in ("ks_two_sample", "tvd_null", "jsd_null", "fisher_z",
                    "mi_bias"):
      assert mv.noise_floor is not None and mv.noise_floor >= 0, mv
      assert mv.ci_low is None and mv.ci_high is None, mv
      seen["scalar"] += 1
    else:
      assert mv.noise_floor is None and mv.ci_low is None, mv
  assert seen["interval"] > 10 and seen["scalar"] > 10
  # an absolute-difference interval is folded around the value
  null_delta = _by_key(metrics)[("column.null_rate_delta", "amount", None)]
  assert null_delta.ci_low <= null_delta.value <= null_delta.ci_high
  assert null_delta.ci_low >= 0.0


# --------------------------------------------------------------------------
# building blocks
# --------------------------------------------------------------------------
def test_pit_from_grid_is_the_mid_cdf():
  grid = np.array([0.0] * 700 + [1.0] * 301)  # 70 % zeros, 30 % ones
  pit = pit_from_grid(grid, np.array([0.0, 1.0, np.nan, -1.0, 2.0]))
  np.testing.assert_allclose(pit[:2], [0.3495, 0.85], atol=1e-12)
  assert math.isnan(pit[2])
  assert (pit[3], pit[4]) == (0.0, 1.0)
  # a continuous grid: a grid point maps to its probability, and values
  # between two points interpolate linearly
  smooth = np.linspace(10.0, 20.0, 1001)
  np.testing.assert_allclose(
      pit_from_grid(smooth, np.array([10.0, 15.0, 15.005, 20.0])),
      [0.0, 0.5, 0.5005, 1.0],
      atol=1e-12)
  # one point: the whole mass sits there, so its mid-CDF is 1/2
  np.testing.assert_allclose(
      pit_from_grid(np.array([3.0]), np.array([2.0, 3.0, 4.0])),
      [0.0, 0.5, 1.0])
  assert np.isnan(pit_from_grid(np.array([]), np.array([1.0]))).all()


def test_pit_mid_cdf_matches_mid_ranks_on_a_fine_grid():
  rng = np.random.default_rng(11)
  x = rng.integers(0, 20, size=5000).astype(float)  # heavy ties
  grid = np.quantile(x, np.linspace(0, 1, 1001), method="inverted_cdf")
  pit = pit_from_grid(grid, x)
  _, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
  mid = (np.cumsum(counts) - counts / 2.0)[inverse] / x.size
  np.testing.assert_allclose(pit, mid, atol=2e-3)


_TRICKY = [
    "", "   ", "\x1c\x1d", "abc", "ABC", "a1!", "Ünïcode", "東京", "ǅemo", "½",
    "٣", "x\ud800y", "tab\tnew\nline", "mixed Case 42 — dash", "\x00", "🙂"
]


def test_char_class_masks_match_the_verbatim_port():
  masks = char_class_masks(_TRICKY)
  bits = {name: 1 << i for i, name in enumerate(CHAR_CLASSES)}
  for value, mask in zip(_TRICKY, masks, strict=True):
    presence = shapes.char_class_presence(value)
    for name in ("digit", "upper", "lower", "space", "punct"):
      assert bool(mask & bits[name]) == presence[name], (value, name)
    other = any(not (c.isdigit() or c.isspace() or not c.isalnum() or
                     (c.isalpha() and (c.isupper() or c.islower())))
                for c in value)
    assert bool(mask & bits["other"]) == other, value
  # chunking never changes a mask
  many = _TRICKY * 500
  np.testing.assert_array_equal(
      char_class_masks(many, chunk_codepoints=64), np.tile(masks, 500))


def test_calendar_counts_match_python_datetime():
  rng = np.random.default_rng(12)
  micros = rng.integers(
      -2_000_000_000_000_000, 4_000_000_000_000_000, size=3000).astype(float)
  dow, month, hour = calendar_counts(micros, time_only=False)
  epoch = datetime(1970, 1, 1, tzinfo=UTC)
  moments = [epoch + timedelta(microseconds=int(m)) for m in micros]
  np.testing.assert_array_equal(
      dow, np.bincount([m.weekday() for m in moments], minlength=7))
  np.testing.assert_array_equal(
      month, np.bincount([m.month - 1 for m in moments], minlength=12))
  np.testing.assert_array_equal(
      hour, np.bincount([m.hour for m in moments], minlength=24))
  times = np.array([0.0, 3_600e6 * 13 + 5, 86_400e6 - 1])
  dow, month, hour = calendar_counts(times, time_only=True)
  assert dow.sum() == 0 and month.sum() == 0
  assert list(np.flatnonzero(hour)) == [0, 13, 23]


def test_null_pattern_cap_keeps_the_fewest_null_patterns_order_free():
  fields = [{
      "name": "id",
      "type": "INT64",
      "mode": "REQUIRED"
  }] + [{
      "name": f"c{i:02d}",
      "type": "FLOAT64",
      "mode": "NULLABLE"
  } for i in range(16)]
  rng = random.Random(13)
  rows = [{
      "id": k,
      **{
          f["name"]: None if rng.random() < 0.5 else 1.0 for f in fields[1:]
      }
  } for k in range(12_000)]
  table = planned_table("patterns", fields, rows, rows, pk=("id",))
  spec = DenseSpec.from_table(table)
  parts = [
      DenseProfile.from_batch(spec, b)
      for b in _batches(table, "source", rows, [3000] * 4)
  ]
  forward = parts[0].merge(parts[1]).merge(parts[2]).merge(parts[3])
  backward = parts[3].merge(parts[2].merge(parts[1].merge(parts[0])))
  assert len(forward.null_patterns) == NULL_PATTERN_CAP
  assert forward.null_patterns == backward.null_patterns
  assert forward.null_overflow == backward.null_overflow > 0
  assert sum(forward.null_patterns.values()) + forward.null_overflow == 12_000
  exact = _patterns(table, rows)
  for pattern, count in forward.null_patterns.items():
    assert exact[pattern] == count
  kept = max(forward.null_patterns, key=lambda p: (p.bit_count(), p))
  for pattern in exact:
    if (pattern.bit_count(), pattern) < (kept.bit_count(), kept):
      assert pattern in forward.null_patterns
  # the metric says its head deviates from the catalogue's top-64
  twin = dataclasses.replace(forward, side="synthetic")
  metrics, _ = dense_outputs(
      spec, {
          "source": forward,
          "synthetic": twin
      }, label_key=LABEL_KEY)
  tvd = _by_key(metrics)[("row.null_pattern_tvd", None, None)]
  assert tvd.detail["capped_head"] is True
  assert "top-64" in tvd.detail["note"]
  uncapped, _ = _pure(*orders_table(n_source=300, n_synthetic=300))
  detail = _by_key(uncapped)[("row.null_pattern_tvd", None, None)].detail
  assert detail["capped_head"] is False and "note" not in detail


def test_profiles_are_bounded_json_safe_and_follow_the_gui_contract(
    monkeypatch):
  table, rows_by = orders_table()
  _, profiles = _pure(table, rows_by)
  kinds = Counter((p.profile_kind, p.side) for p in profiles)
  for kind in ("histogram", "quantiles", "moments", "temporal_mix",
               "length_hist", "char_classes", "null_patterns", "corr_matrix",
               "contingency"):
    assert kinds[(kind, "source")] > 0, kind
    assert kinds[(kind, "synthetic")] > 0, kind
  contingency = [p for p in profiles if p.profile_kind == "contingency"]
  assert len({(p.column, p.payload["column_y"]) for p in contingency}) == 5
  for p in profiles:
    json.dumps(p.payload, allow_nan=False)
    if p.profile_kind == "histogram":
      assert len(p.payload["counts"]) == len(p.payload["edges"]) + 1 <= 1000
      assert p.edges_digest
    if p.profile_kind == "null_patterns":
      assert len(p.payload["patterns"]) <= NULL_PATTERN_CAP
      assert set(p.payload) >= {"columns", "patterns", "overflow"}
    if p.profile_kind == "temporal_mix":
      assert len(p.payload["dow"]) == 7 and len(p.payload["month"]) == 12
  # temporal payloads are in epoch seconds (the GUI contract), not micros
  created = next(p for p in profiles if p.profile_kind == "moments" and
                 p.column == "created_at" and p.side == "source")
  assert created.payload["unit"] == "epoch_seconds"
  assert 1.7e9 < created.payload["mean"] < 1.8e9
  # D6: a literal_ok dictionary keeps its literals, the rest are hashed
  # (every pair gets a contingency here, so both columns are on an axis)
  monkeypatch.setattr(dense, "CONTINGENCY_TOP_PAIRS", 10_000)
  _, profiles = _pure(table, rows_by)
  contingency = [p for p in profiles if p.profile_kind == "contingency"]
  status_axes = [
      p for p in contingency if "status" in (p.column, p.payload["column_y"])
  ]
  for p in status_axes:
    labels = (
        p.payload["x_labels"]
        if p.column == "status" else p.payload["y_labels"])
    assert "Complete" in labels
  assert status_axes
  note_axes = [
      p for p in contingency if "note" in (p.column, p.payload["column_y"])
  ]
  for p in note_axes:
    labels = (
        p.payload["x_labels"] if p.column == "note" else p.payload["y_labels"])
    assert all(
        label.startswith("h:") or label in ("other", "NULL")
        for label in labels)
  assert note_axes


def test_combine_fn_merges_and_skips_empty_accumulators():
  table, rows_by = orders_table(n_source=800, n_synthetic=10, n_reference=10)
  spec = DenseSpec.from_table(table)
  fn = DenseProfileCombineFn()
  parts = [
      DenseProfile.from_batch(spec, b)
      for b in _batches(table, "source", rows_by["source"], [300, 300])
  ]
  acc = fn.create_accumulator()
  for part in parts:
    acc = fn.add_input(acc, part)
  merged = fn.merge_accumulators([acc, fn.create_accumulator()])
  _assert_same(
      fn.extract_output(merged),
      DenseProfile.from_batch(spec, _encode(table, "source",
                                            rows_by["source"])))


def test_pair_bins_match_the_plan_grid():
  assert PAIR_BINS == plan_module.PAIR_GRID_CELLS
  assert PAIR_BINS - 1 == plan_module.DICTIONARY_SIZE


def test_metrics_for_a_missing_side_are_not_evaluated_with_a_reason():
  table, rows_by = orders_table(n_source=500, n_synthetic=10, n_reference=10)
  metrics, _ = _pure(table, {"source": rows_by["source"]})
  assert metrics
  for mv in metrics:
    assert mv.value is None
    assert "synthetic" in mv.detail["reason"], mv


def test_dense_spec_pickles_without_panel_rows():
  table, _ = orders_table(n_source=500, n_synthetic=10, n_reference=200)
  blob = pickle.dumps(DenseSpec.from_table(table))
  assert b"order was" not in blob  # no row text rides along with the spec
  assert len(blob) < 400_000


def _wide_rows(n: int, seed: int) -> tuple[list[dict], list[dict]]:
  rng = np.random.default_rng(seed)
  fields: list[dict[str, str]] = [{
      "name": "id",
      "type": "INT64",
      "mode": "REQUIRED"
  }]
  kinds = ["FLOAT64"] * 12 + ["TIMESTAMP"] * 4 + ["STRING"] * 12 + ["BOOL"] * 2
  for i, bq_type in enumerate(kinds[:29]):
    fields.append({"name": f"c{i:02d}", "type": bq_type, "mode": "NULLABLE"})
  start = datetime(2024, 1, 1, tzinfo=UTC)
  rows = []
  for k in range(n):
    row: dict[str, Any] = {"id": k}
    for f in fields[1:]:
      if rng.random() < 0.05:
        row[f["name"]] = None
      elif f["type"] == "FLOAT64":
        row[f["name"]] = float(rng.normal())
      elif f["type"] == "TIMESTAMP":
        row[f["name"]] = start + timedelta(seconds=int(rng.integers(0, 10**7)))
      elif f["type"] == "BOOL":
        row[f["name"]] = bool(rng.random() < 0.5)
      else:
        row[f["name"]] = (f"value {int(rng.integers(0, 40))}"
                          if f["name"] < "c20" else
                          f"free text {int(rng.integers(0, 10**9))} ok")
    rows.append(row)
  return fields, rows


def test_throughput_8192_by_30_batch(record_property):
  fields, rows = _wide_rows(8192, seed=21)
  table = planned_table("wide", fields, rows, rows, pk=("id",))
  spec = DenseSpec.from_table(table)
  batch = _encode(table, "synthetic", rows)
  DenseProfile.from_batch(spec, batch)  # warm-up
  runs = 3
  started = clock.perf_counter()
  for _ in range(runs):
    DenseProfile.from_batch(spec, batch)
  elapsed = (clock.perf_counter() - started) / runs
  rows_per_s = len(rows) / elapsed
  record_property("dense_rows_per_s", rows_per_s)
  print(f"dense from_batch: {rows_per_s:,.0f} rows/s on an 8192 x 30 batch "
        f"({len(table.pairs)} pairs)")
  assert rows_per_s > 20_000


# --------------------------------------------------------------------------
# review round 1: R63, R64, R65, legacy deciles, edge labels
# --------------------------------------------------------------------------
_UNIFORM_FIELDS = (
    {
        "name": "id",
        "type": "INT64",
        "mode": "REQUIRED"
    },
    {
        "name": "x",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
)


def test_decile_ks_legacy_reads_exact_extremes_when_synthetic_overflows():
  rng = np.random.default_rng(31)
  source = [{"id": i, "x": float(v)} for i, v in enumerate(rng.random(4000))]
  synthetic = [{
      "id": i,
      "x": float(v)
  } for i, v in enumerate(3.0 * rng.random(4000))]
  table = planned_table(
      "uniform", _UNIFORM_FIELDS, source, synthetic, pk=("id",))
  metrics, _ = _pure(table, {
      "source": source,
      "synthetic": synthetic,
      "reference": source[:1000],
  })
  legacy = _by_key(metrics)[("column.decile_ks_legacy", "x", None)]
  xs = np.array([r["x"] for r in source])
  xy = np.array([r["x"] for r in synthetic])
  probs = np.linspace(0.0, 1.0, 11)
  exact = binned.decile_ks_legacy(
      np.quantile(xs, probs).tolist(),
      np.quantile(xy, probs).tolist())
  # U(0, 1) vs U(0, 3): the legacy value is 2/3 (the grid-extreme
  # reconstruction read 0.60)
  assert legacy.value == pytest.approx(2 / 3, abs=0.02)
  assert legacy.value == pytest.approx(exact, abs=2e-3)
  xr = xs[:1000]
  exact_base = binned.decile_ks_legacy(
      np.quantile(xs, probs).tolist(),
      np.quantile(xr, probs).tolist())
  assert legacy.baseline_value == pytest.approx(exact_base, abs=0.02)


def _json_text(profiles: Sequence[ProfileValue],
               metrics: Sequence[MetricValue]) -> str:
  return json.dumps(
      [dict(p.payload) for p in profiles] + [dict(m.detail) for m in metrics],
      default=str)


def _unkeyed_label(code: int) -> str:
  """The pre-R64 label: the code's top 32 bits, reversible by enumeration."""
  top = code >> 32
  return f"h:{top:08x}"


def test_hashed_labels_are_keyed_and_leak_neither_values_nor_key(monkeypatch):
  monkeypatch.setattr(dense, "CONTINGENCY_TOP_PAIRS", 10_000)
  table, rows_by = orders_table(n_source=1500, n_synthetic=1200)
  spec = DenseSpec.from_table(table)
  profiles = {s: _profile(table, s, rows) for s, rows in rows_by.items()}

  def note_labels(key: bytes) -> tuple[list[str], list, list]:
    metrics, payloads = dense_outputs(spec, profiles, label_key=key)
    for p in payloads:
      if p.profile_kind == "contingency" and p.payload["column_y"] == "note":
        return list(p.payload["y_labels"]), metrics, payloads
    raise AssertionError("no contingency axis on note")

  first, metrics, payloads = note_labels(LABEL_KEY)
  other, _, _ = note_labels(b"another-key")
  hashed = [label for label in first if label.startswith("h:")]
  assert len(hashed) == 9  # note is not literal_ok: its top-9 are labels
  assert all(
      a != b for a, b in zip(first, other, strict=True) if a.startswith("h:"))
  # an enumeration attack with the old unkeyed label recovers nothing
  domain = {
      r["note"]
      for rows in rows_by.values()
      for r in rows
      if r["note"] is not None
  }
  unkeyed = {_unkeyed_label(hash64("note", v)) for v in domain}
  assert not set(hashed) & unkeyed
  # and the key itself is in no payload and no detail
  text = _json_text(payloads, metrics)
  for form in (LABEL_KEY.decode(), LABEL_KEY.hex(), repr(LABEL_KEY)):
    assert form not in text
  for bad in (b"", "text-key"):
    with pytest.raises(ValueError, match="R64"):
      dense_outputs(spec, profiles, label_key=bad)  # type: ignore[arg-type]
  # bytes handed to the transform would be pickled into the job graph
  with pytest.raises(TypeError, match="R68"):
    DenseMetrics([table], label_key=LABEL_KEY)  # type: ignore[arg-type]


def _numbers(obj: Any) -> Iterator[float]:
  if isinstance(obj, bool):
    return
  if isinstance(obj, (int, float)):
    yield float(obj)
  elif isinstance(obj, Mapping):
    for value in obj.values():
      yield from _numbers(value)
  elif isinstance(obj, (list, tuple)):
    for value in obj:
      yield from _numbers(value)


def test_no_source_extreme_in_any_payload_or_detail():
  table, rows_by = orders_table()
  metrics, profiles = _pure(table, rows_by)
  assert not _leaks(metrics, profiles, rows_by, ("amount", "created_at"))
  # no side shows its exact extremes (R71): each says what it shows
  histograms = {
      (p.side, p.column): p.payload
      for p in profiles
      if p.profile_kind == "histogram"
  }
  assert {h["extremes"] for h in histograms.values()} == {"p0.5_p99.5"}
  xy = _finite(rows_by["synthetic"], "amount")
  synthetic = histograms[("synthetic", "amount")]
  assert xy.min() < synthetic["min"] < synthetic["max"] < xy.max()
  coverage = _by_key(metrics)[("column.range_coverage", "amount", None)]
  assert coverage.detail["source_p0_5"] is not None
  assert coverage.detail["synthetic_p0_5"] is not None
  assert not {"source_min", "synthetic_min", "synthetic_max"} & set(
      coverage.detail)


def test_contingency_tvd_noise_floor_is_the_joint_null_expectation():
  table, rows_by = orders_table()
  spec = DenseSpec.from_table(table)
  metrics, _ = _pure(table, rows_by)
  source = _profile(table, "source", rows_by["source"])
  assert source.bivariate is not None
  names = [c.name for c in spec.pair_columns]
  for k, (a, b) in enumerate(spec.pairs):
    mv = _by_key(metrics)[("pair.contingency_tvd", names[a], names[b])]
    joint = source.bivariate.counts2d[k]
    expected = noise.tvd_null_expectation((joint / joint.sum()).ravel(),
                                          mv.n_source, mv.n_synthetic)
    assert mv.noise_floor == pytest.approx(expected, rel=1e-12)
  assert _CATALOGUE.get("pair.contingency_tvd").noise_floor == "tvd_null"


def test_edge_labels_tell_every_edge_apart():
  labels = dense._edge_labels(  # pylint: disable=protected-access  # the formatter itself is under test
      [1.0000001, 1.0000002, 12.5, 1e-05], ColumnKind.NUMERIC)
  assert len(set(labels)) == 4
  assert labels[2] == "12.5"
  stamps = dense._edge_labels(  # pylint: disable=protected-access  # as above
      [1_700_000_000_000_000.0, 1_700_000_000_000_001.0], ColumnKind.TEMPORAL)
  assert len(set(stamps)) == 2 and stamps[0].endswith(".000000")
  assert dense._edge_labels(  # pylint: disable=protected-access  # as above
      [0.0, 86_400e6],
      ColumnKind.TEMPORAL) == ["1970-01-01T00:00:00", "1970-01-02T00:00:00"]


# --------------------------------------------------------------------------
# review round 2: R68 (the key off the job graph), R69 (the count rule)
# --------------------------------------------------------------------------
def _pickled_payloads(p: beam.Pipeline) -> list[bytes]:
  """Every ParDo's pickled DoFn in `p`'s runner API graph, decompressed
  (Beam pickles as base64(bz2|zlib(cloudpickle)))."""
  proto = p.to_runner_api()
  out = []
  for transform in proto.components.transforms.values():
    if transform.spec.urn != common_urns.primitives.PAR_DO.urn:
      continue
    payload = beam_runner_api_pb2.ParDoPayload.FromString(
        transform.spec.payload)
    blob = payload.do_fn.payload
    pickler.loads(blob)  # every payload unpickles
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


def _key_file_reader(uri: str) -> bytes:
  """A fake operator-key reader (Secret Manager/GCS in production): the
  key lives in a local file, so only its path is in the graph."""
  return Path(uri).read_bytes()


@pytest.mark.parametrize("mode", ["ephemeral", "operator"])
def test_label_key_never_enters_the_job_graph(mode, tmp_path):
  table, rows_by = orders_table(n_source=300, n_synthetic=300, n_reference=60)
  operator_key = b"operator-secret-key-material-0042"
  key_file = tmp_path / "label.key"
  key_file.write_bytes(operator_key)
  uri = str(key_file) if mode == "operator" else None
  sources = InMemorySources({
      ("orders", side): rows_by[side] for side in ("source", "synthetic")
  })
  pipeline = BeamTestPipeline()
  with pipeline as p:
    batches = [
        sources.read(p, table, side) | EncodeSide(table, side, salt=SALT)
        for side in rows_by
    ] | "Flatten" >> beam.Flatten()
    key = p | "LabelKey" >> LabelKey(uri, reader=_key_file_reader)
    dense_out = batches | "Dense" >> DenseMetrics([table], label_key=key)
    census_out = ({
        "batches": batches,
        "accumulators": dense_out["accumulators"]
    }
                  | "Census" >> CensusMetrics([table], label_key=key))
    _collect(key, tmp_path / "key.pkl", "Key")
    _collect(dense_out["profiles"], tmp_path / "dense.pkl", "DenseProfiles")
    _collect(census_out["profiles"], tmp_path / "census.pkl", "CensusProfiles")
  (resolved,) = pickle.loads((tmp_path / "key.pkl").read_bytes())
  if mode == "operator":
    assert resolved == operator_key
  else:
    assert len(resolved) == 32 and resolved != operator_key
  for blob in _pickled_payloads(pipeline):
    assert resolved not in blob
    assert resolved.hex().encode() not in blob
  # both passes labelled with the worker's key
  profiles = (
      pickle.loads((tmp_path / "dense.pkl").read_bytes()) + pickle.loads(
          (tmp_path / "census.pkl").read_bytes()))
  text = json.dumps([dict(p.payload) for p in profiles], default=str)
  note = next(c for c in table.columns if c.name == "note")
  expected = hashed_label(note.dictionary[0], key=resolved)
  assert expected in text
  assert resolved.hex() not in text


def test_label_key_constructor_bytes_are_refused_by_census():
  table, _ = orders_table(n_source=100, n_synthetic=10, n_reference=10)
  with pytest.raises(TypeError, match="R68"):
    CensusMetrics([table], label_key=LABEL_KEY)  # type: ignore[arg-type]


def test_rare_count_is_the_d6_floor():
  assert RARE_COUNT == plan_module.LITERAL_MIN_COUNT == census.RARE_COUNT


_TINY_FIELDS = (
    {
        "name": "id",
        "type": "INT64",
        "mode": "REQUIRED"
    },
    {
        "name": "x",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
    {
        "name": "y",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?")
_ISO = re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+")


def _tiny(n: int, seed: int) -> list[dict]:
  rng = np.random.default_rng(seed)
  xs, ys = rng.normal(50, 10, n), rng.normal(20, 5, n)
  return [{
      "id": i,
      "x": float(x),
      "y": float(y)
  } for i, (x, y) in enumerate(zip(xs, ys, strict=True))]


def _extremes_of(rows: Sequence[Mapping[str, Any]],
                 names: Sequence[str]) -> set[float]:
  out: set[float] = set()
  for name in names:
    x = _finite(rows, name)
    if x.size:
      out.update({float(x.min()), float(x.max())})
  return out


def _tail_values(rows: Sequence[Mapping[str, Any]], name: str) -> set[float]:
  """The column's values with fewer than RARE_COUNT records at or below
  or at or above them: what R69/R71 never lets a payload show (the exact
  extremes among them)."""
  v = np.sort(_finite(rows, name))
  below = np.searchsorted(v, v, side="right")
  above = v.size - np.searchsorted(v, v, side="left")
  return set(v[(below < RARE_COUNT) | (above < RARE_COUNT)].tolist())


def _floats(obj: Any) -> Iterator[float]:
  """Every float in a payload or detail (record values are floats; the
  counts beside them are ints and never a record's value)."""
  if isinstance(obj, float):
    yield obj
  elif isinstance(obj, Mapping):
    for value in obj.values():
      yield from _floats(value)
  elif isinstance(obj, (list, tuple)):
    for value in obj:
      yield from _floats(value)


def _label_shows(token: str, secret: float) -> bool:
  """Whether a label's printed number is `secret` at the label's own
  precision (its significant digits)."""
  mantissa = token.lstrip("-").split("e")[0]
  digits = len(mantissa.replace(".", "").lstrip("0")) or 1
  return f"{secret:.{digits}g}" == f"{float(token):.{digits}g}"


def _iso_shows(stamp: str, secret: float) -> bool:
  """Whether an ISO label is the micros `secret` at the label's own unit."""
  clock_part = stamp.split("T")[1]
  unit = {8: "s", 12: "ms"}.get(len(clock_part), "us")
  shown = np.datetime_as_string(np.datetime64(int(secret), "us"), unit=unit)
  return bool(shown == stamp)


def _label_leaks(labels: Sequence[str], secrets: set[float]) -> list:
  found = []
  for label in labels:
    stamps = _ISO.findall(label)
    if stamps:
      found += [(label, s) for t in stamps for s in secrets if _iso_shows(t, s)]
      continue
    found += [(label, s)
              for token in _NUMBER.findall(label)
              for s in secrets
              if _label_shows(token, s)]
  return found


def _leaks(metrics: Sequence[MetricValue],
           profiles: Sequence[ProfileValue],
           rows_by: Mapping[str, list],
           names: Sequence[str],
           *,
           tail: bool = False) -> list:
  """Every shown value that is one of a column's SOURCE secrets — its
  exact extremes, or with `tail` every value with fewer than k records at
  or beyond it — matched against that column's own payloads, details and
  axis labels only: floats to within a few ulps (temporal payloads are
  in epoch seconds, micros * 1e-6, so a secret is matched in both
  units), labels at their printed precision (ISO labels at their unit).
  Also any reference/holdout payload min/max that is that side's own
  exact extreme."""
  found: list = []
  for name in names:
    secrets = (
        _tail_values(rows_by["source"], name) if tail else _extremes_of(
            rows_by["source"], [name]))
    scaled = secrets | {v / 1e6 for v in secrets}
    shown = [
        x for p in profiles if p.column == name for x in _floats(p.payload)
    ]
    shown += [
        x for m in metrics if name in (m.column, m.column_2)
        for x in _floats(m.detail)
    ]
    found += [(name, x) for x in shown if any(
        math.isclose(x, s, rel_tol=4e-16) for s in scaled)]
    for p in profiles:
      if p.profile_kind != "contingency":
        continue
      if p.payload["column_x"] == name:
        found += _label_leaks(p.payload["x_labels"], secrets)
      if p.payload["column_y"] == name:
        found += _label_leaks(p.payload["y_labels"], secrets)
  for p in profiles:
    if p.side in ("reference", "holdout") and p.column in names and (
        p.profile_kind in ("histogram", "moments")):
      own = _extremes_of(rows_by[p.side], [p.column])
      found += [(p.side, p.payload[key])
                for key in ("min", "max")
                if p.payload[key] in own]
  return found


@pytest.mark.parametrize("n", [20, 50, 190])
def test_small_tables_publish_no_source_extreme(n, monkeypatch):
  monkeypatch.setattr(dense, "CONTINGENCY_TOP_PAIRS", 10_000)
  source, synthetic = _tiny(n, seed=n), _tiny(n, seed=n + 1)
  rows_by = {
      "source": source,
      "synthetic": synthetic,
      "reference": source[:max(1, n // 5)],
      "holdout": source[max(1, n // 5):2 * max(1, n // 5)],
  }
  table = planned_table("tiny", _TINY_FIELDS, source, synthetic, pk=("id",))
  metrics, profiles = _pure(table, rows_by)
  assert [p for p in profiles if p.profile_kind == "contingency"]
  assert not _leaks(metrics, profiles, rows_by, ("x", "y"))
  assert not _leaks(metrics, profiles, rows_by, ("x", "y"), tail=True)
  for p in profiles:
    if p.profile_kind in ("histogram", "moments"):
      # n * 0.005 < 10 on every side: bounds withheld, so no "p0.5" below
      # a side's own minimum (the n = 190 artifact) can be shown
      assert p.payload["min"] is None and p.payload["max"] is None
    if p.profile_kind == "quantiles":
      for q in p.payload["probs"]:
        assert q * p.n >= RARE_COUNT and (1 - q) * p.n >= RARE_COUNT
  for metric_id in ("field.range_adherence", "column.range_coverage"):
    detail = _by_key(metrics)[(metric_id, "x", None)].detail
    assert detail["source_p0_5"] is None and detail["source_p99_5"] is None


def test_a_common_end_atom_may_be_shown_a_lone_extreme_never():
  rng = np.random.default_rng(41)
  values = [0.0] * 30 + [100.0] * 30 + list(rng.uniform(1, 99, 140))
  source = [{
      "id": i,
      "x": v,
      "y": float(rng.normal())
  } for i, v in enumerate(values)]
  source.append({"id": 999, "x": 250.0, "y": 0.5})  # a lone maximum
  synthetic = _tiny(200, seed=43)
  table = planned_table("atoms", _TINY_FIELDS, source, synthetic, pk=("id",))
  _, profiles = _pure(table, {"source": source, "synthetic": synthetic})
  histogram = next(p for p in profiles if p.profile_kind == "histogram" and
                   p.side == "source" and p.column == "x")
  edges = histogram.payload["edges"]
  assert 0.0 in edges and 100.0 in edges  # 30 records sit at each
  assert 250.0 not in edges  # one record: never published
  assert sum(histogram.payload["counts"]) == len(source)
  assert len(histogram.payload["counts"]) == len(edges) + 1


def test_bounds_are_shown_once_n_clears_the_count_rule():
  table, rows_by = orders_table()  # ~2,850 finite amounts: 0.005 n >= 10
  metrics, profiles = _pure(table, rows_by)
  histogram = next(p for p in profiles if p.profile_kind == "histogram" and
                   p.side == "source" and p.column == "amount")
  xs = _finite(rows_by["source"], "amount")
  assert xs.min() < histogram.payload["min"] < histogram.payload["max"] < (
      xs.max())
  # the reference (600 rows) is below the rule: withheld
  reference = next(p for p in profiles if p.profile_kind == "histogram" and
                   p.side == "reference" and p.column == "amount")
  assert reference.payload["min"] is None
  assert not _leaks(metrics, profiles, rows_by, ("amount", "created_at"))


# --------------------------------------------------------------------------
# review round 3: no source tail value through any side's quantiles (R71)
# --------------------------------------------------------------------------
def _uniform_rows(values: np.ndarray) -> list[dict]:
  return [{"id": i, "x": float(v)} for i, v in enumerate(values)]


def test_synthetic_quantiles_never_land_on_a_source_extreme():
  # the reviewer's construction: 30 of 3,000 synthetic values below the
  # source min, the 31st between the source min and its second value, so
  # the synthetic p0.01 sat on the source min (an interpolation plateau)
  rng = np.random.default_rng(7)
  src = rng.normal(50, 10, 3000)
  ordered = np.sort(src)
  lo, lo2 = ordered[0], ordered[1]
  syn = np.concatenate([
      rng.uniform(lo - 20, lo - 1, 30),
      [lo + (lo2 - lo) / 2],
      rng.uniform(lo2 + 1e-9, ordered[-1], 2969),
  ])
  source, synthetic = _uniform_rows(src), _uniform_rows(syn)
  table = planned_table("q", _UNIFORM_FIELDS, source, synthetic, pk=("id",))
  rows_by = {"source": source, "synthetic": synthetic}
  metrics, profiles = _pure(table, rows_by)
  assert not _leaks(metrics, profiles, rows_by, ("x",), tail=True)
  quantiles = next(p for p in profiles
                   if p.profile_kind == "quantiles" and p.side == "synthetic")
  assert lo not in quantiles.payload["values"]
  assert quantiles.payload["probs"][0] == 0.01  # the payload still shows p1


def test_a_generator_clamping_to_the_source_range_shows_no_source_extreme():
  rng = np.random.default_rng(8)
  src = rng.normal(50, 10, 3000)
  syn = np.clip(rng.normal(50, 20, 3000), src.min(), src.max())
  source, synthetic = _uniform_rows(src), _uniform_rows(syn)
  table = planned_table("clamp", _UNIFORM_FIELDS, source, synthetic, pk=("id",))
  rows_by = {"source": source, "synthetic": synthetic}
  metrics, profiles = _pure(table, rows_by)
  # hundreds of synthetic records sit on each source extreme, yet no
  # synthetic payload or detail shows them (R71)
  assert np.count_nonzero(syn == src.min()) > RARE_COUNT
  assert not _leaks(metrics, profiles, rows_by, ("x",), tail=True)


_PROPERTY_FIELDS = (
    {
        "name": "id",
        "type": "INT64",
        "mode": "REQUIRED"
    },
    {
        "name": "x",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
    {
        "name": "y",
        "type": "FLOAT64",
        "mode": "NULLABLE"
    },
    {
        "name": "t",
        "type": "TIMESTAMP",
        "mode": "NULLABLE"
    },
)
_T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _property_rows(n: int,
                   seed: int,
                   *,
                   sd: float = 10.0,
                   tsd: float = 86_400e6) -> list[dict]:
  rng = np.random.default_rng(seed)
  xs = rng.normal(50, sd, n)
  ys = rng.normal(20, 5, n) + 0.3 * xs
  ts = np.abs(rng.normal(30 * 86_400e6, tsd, n))
  return [{
      "id": i,
      "x": float(x),
      "y": float(y),
      "t": _T0 + timedelta(microseconds=int(t))
  } for i, (x, y, t) in enumerate(zip(xs, ys, ts, strict=True))]


@pytest.mark.parametrize(("n", "shape"), [(20, "wide"), (50, "narrow"),
                                          (190, "same"), (400, "wide"),
                                          (3000, "narrow")])
def test_no_source_tail_value_on_any_side_property(n, shape, monkeypatch):
  """The reviewer's generic probe as a seeded property: every float any
  side's payload, any detail or any axis label shows is never a source
  value with fewer than k records at or beyond it (x, y and a TIMESTAMP,
  whose payloads are in epoch seconds and labels ISO)."""
  monkeypatch.setattr(dense, "CONTINGENCY_TOP_PAIRS", 10_000)
  source = _property_rows(n, seed=n)
  spread = {
      "same": (n, 10.0, 86_400e6),
      "wide": (3000, 30.0, 5 * 86_400e6),
      "narrow": (3000, 2.0, 86_400e6 / 5)
  }[shape]
  synthetic = _property_rows(spread[0], seed=n + 1, sd=spread[1], tsd=spread[2])
  cut = max(1, n // 5)
  rows_by = {
      "source": source,
      "synthetic": synthetic,
      "reference": source[:cut],
      "holdout": source[cut:2 * cut],
  }
  table = planned_table("p", _PROPERTY_FIELDS, source, synthetic, pk=("id",))
  metrics, profiles = _pure(table, rows_by)
  assert not _leaks(metrics, profiles, rows_by, ("x", "y", "t"), tail=True)
  # R74.6: and what every side does publish is accurate to one kept bin
  for name in ("x", "y", "t"):
    kept = _kept_of(_union_of(table, name), _finite(source, name))
    for side, rows in rows_by.items():
      _assert_within_one_bin(
          profiles,
          side,
          name,
          _finite(rows, name),
          kept,
          scale=1e-6 if name == "t" else 1.0)


# --------------------------------------------------------------------------
# review round 4: accurate, privacy-safe payloads (R74)
# --------------------------------------------------------------------------
_PROBS = [round(p / 100, 2) for p in range(1, 100)]


def _union_of(table: Any, name: str) -> np.ndarray:
  spec = DenseSpec.from_table(table)
  return next(g.union for g in spec.grids if g.name == name)


def _kept_of(edges: np.ndarray, source: np.ndarray) -> np.ndarray:
  """R74's rule re-derived from the raw source values: an edge is dropped
  only when a source record sits on it and fewer than k source records
  lie at or below it or at or above it."""
  ordered = np.sort(source)
  below = np.searchsorted(ordered, edges, side="right")  # count(x <= e)
  under = np.searchsorted(ordered, edges, side="left")  # count(x < e)
  rare = (below < RARE_COUNT) | (source.size - under < RARE_COUNT)
  return edges[~(rare & (below > under))]


def _payload(profiles: Sequence[ProfileValue], kind: str, side: str,
             column: str) -> dict:
  return next(
      p.payload
      for p in profiles
      if p.profile_kind == kind and p.side == side and p.column == column)


def _bin_of(edges: np.ndarray, x: float) -> int:
  return int(np.searchsorted(edges, x, side="left"))  # bin (e_{i-1}, e_i]


def _true_quantile(values: np.ndarray, prob: float) -> float:
  """Q(p) = inf{x : F(x) >= p}: the smallest order statistic x_(r) with
  r / n >= p — compared as the accumulator compares shares (`k / n`
  against p), where `numpy.quantile(method="inverted_cdf")` rounds
  `p * n` instead (0.28 * 50 = 14.000000000000002 → x_(15), not x_(14))."""
  ordered = np.sort(values)
  shares = np.arange(1, ordered.size + 1) / ordered.size
  return float(ordered[min(np.searchsorted(shares, prob), ordered.size - 1)])


def _assert_within_one_bin(profiles: Sequence[ProfileValue],
                           side: str,
                           column: str,
                           values: np.ndarray,
                           kept: np.ndarray,
                           scale: float = 1.0) -> int:
  """Every published quantile and bound of (side, column) lies in the same
  or an adjacent kept union bin as the true quantile of `values` (R74.6):
  the quantile function `_true_quantile`, which is what a CDF inverted at
  the edges targets — numpy's default linear method interpolates between
  two order statistics, which can straddle an edge. Returns how many
  were checked."""
  shown: list[tuple[float, float | None]] = []
  for p in profiles:
    if (p.profile_kind == "quantiles" and p.side == side and
        p.column == column):
      shown += list(zip(p.payload["probs"], p.payload["values"], strict=True))
  histogram = _payload(profiles, "histogram", side, column)
  shown += [(0.005, histogram["min"]), (0.995, histogram["max"])]
  edges = kept * scale
  checked = 0
  for prob, value in shown:
    if value is None:
      continue
    true = _true_quantile(values, prob) * scale
    assert abs(_bin_of(edges, value) -
               _bin_of(edges, true)) <= 1, (side, column, prob, value, true)
    checked += 1
  return checked


_CLAMP_CASES = ((20, 3000, 10.0), (20, 3000, 30.0), (50, 3000,
                                                     30.0), (190, 3000, 30.0),
                (3000, 3000, 30.0), (3000, 3000, 60.0), (3000, 3000, 10.0))


@pytest.mark.parametrize(("n_src", "n_syn", "sd_syn"), _CLAMP_CASES)
def test_quantiles_follow_each_side_within_one_bin_never_clamped(
    n_src, n_syn, sd_syn):
  """The reviewer's p_clamp cases (R74.1, R74.3, R74.6): a synthetic wider
  or narrower than the source keeps its own range. Every published
  quantile and bound on either side lies within one kept union bin of
  the true one, the synthetic p1/p99 are never squeezed into the
  source's k-th extremes, and no source tail value shows."""
  rng = np.random.default_rng(1)
  src, syn = rng.normal(50, 10, n_src), rng.normal(50, sd_syn, n_syn)
  source, synthetic = _uniform_rows(src), _uniform_rows(syn)
  table = planned_table("q", _UNIFORM_FIELDS, source, synthetic, pk=("id",))
  rows_by = {"source": source, "synthetic": synthetic}
  metrics, profiles = _pure(table, rows_by)
  kept = _kept_of(_union_of(table, "x"), src)
  # 99 quantiles + 2 bounds on the synthetic side (n = 3,000 clears every
  # count rule); the source side shows what its n allows
  assert _assert_within_one_bin(profiles, "synthetic", "x", syn, kept) == 101
  assert _assert_within_one_bin(profiles, "source", "x", src, kept) > 0
  assert not _leaks(metrics, profiles, rows_by, ("x",), tail=True)
  quantiles = _payload(profiles, "quantiles", "synthetic", "x")
  assert quantiles["probs"] == _PROBS
  low, high = quantiles["values"][0], quantiles["values"][-1]
  if sd_syn > 10:  # a wider synthetic shows its own range, not the source's
    ordered = np.sort(src)
    assert low < ordered[RARE_COUNT - 1] and high > ordered[-RARE_COUNT]


def test_a_common_end_atom_is_its_own_quantiles():
  """orders.quantity is 1..5 with ~600 records at each value (R74.2, the
  reviewer's p_atoms case): both end atoms pass the symmetric count rule,
  a jump is exact, so every quantile is the inverted CDF's (5 past p80
  instead of a clamp at 4), the bounds are the end atoms, and so are the
  range_coverage details."""
  table, rows_by = orders_table()
  metrics, profiles = _pure(table, rows_by)
  for side in ("source", "synthetic"):
    values = _finite(rows_by[side], "quantity")
    quantiles = _payload(profiles, "quantiles", side, "quantity")
    assert quantiles["probs"] == _PROBS
    for prob, value in zip(
        quantiles["probs"], quantiles["values"], strict=True):
      assert value == _true_quantile(values, prob), (side, prob, value)
    assert quantiles["values"][-1] == 5.0 and quantiles["values"][0] == 1.0
    histogram = _payload(profiles, "histogram", side, "quantity")
    assert (histogram["min"], histogram["max"]) == (1.0, 5.0)
    assert {1.0, 5.0} <= set(histogram["edges"])
  detail = _by_key(metrics)[("column.range_coverage", "quantity", None)].detail
  assert (detail["source_p0_5"], detail["source_p99_5"]) == (1.0, 5.0)
  assert (detail["synthetic_p0_5"], detail["synthetic_p99_5"]) == (1.0, 5.0)
  assert not _leaks(
      metrics,
      profiles,
      rows_by, ("quantity", "discount", "ship_date"),
      tail=True)


def test_a_clamping_generator_has_its_tails_withheld_not_clamped():
  """R74.3: a synthetic clamped to the source's exact range piles ~4 % of
  its records on each source extreme — rare source records, so those
  edges are dropped. The probabilities inside the piles are withheld,
  not mapped onto the next kept edge; everything else is accurate to one
  bin; the bounds sit inside the piles, so they are withheld too."""
  rng = np.random.default_rng(8)
  src = rng.normal(50, 10, 3000)
  syn = np.clip(rng.normal(50, 20, 3000), src.min(), src.max())
  source, synthetic = _uniform_rows(src), _uniform_rows(syn)
  table = planned_table("clamp", _UNIFORM_FIELDS, source, synthetic, pk=("id",))
  rows_by = {"source": source, "synthetic": synthetic}
  metrics, profiles = _pure(table, rows_by)
  kept = _kept_of(_union_of(table, "x"), src)
  assert src.min() not in kept and src.max() not in kept
  low_pile = np.count_nonzero(syn < kept[0]) / syn.size  # F⁻(first kept)
  high_pile = np.count_nonzero(syn <= kept[-1]) / syn.size  # F(last kept)
  assert low_pile > 0.01 and high_pile < 0.99  # the piles cover p1 and p99
  quantiles = _payload(profiles, "quantiles", "synthetic", "x")
  assert quantiles["probs"][0] > low_pile
  assert quantiles["probs"][-1] <= high_pile
  assert all(kept[0] <= v <= kept[-1] for v in quantiles["values"])
  assert _assert_within_one_bin(profiles, "synthetic", "x", syn, kept) > 50
  histogram = _payload(profiles, "histogram", "synthetic", "x")
  assert histogram["min"] is None and histogram["max"] is None
  assert not _leaks(metrics, profiles, rows_by, ("x",), tail=True)


def test_no_source_side_shows_the_synthetic_payloads_normally():
  """R74.4 (the reviewer's p_misc case): with no source side there is
  nothing to leak, so the synthetic keeps every histogram edge, its 99
  quantiles and its bounds."""
  rng = np.random.default_rng(4)
  source = _uniform_rows(rng.normal(50, 10, 5))
  syn = rng.normal(50, 10, 3000)
  synthetic = _uniform_rows(syn)
  table = planned_table("m", _UNIFORM_FIELDS, source, synthetic, pk=("id",))
  _, profiles = _pure(table, {"synthetic": synthetic})
  histogram = _payload(profiles, "histogram", "synthetic", "x")
  assert histogram["edges"] == sorted(r["x"] for r in source)
  assert histogram["min"] is not None and histogram["max"] is not None
  assert _payload(profiles, "quantiles", "synthetic", "x")["probs"] == _PROBS
  union = _union_of(table, "x")  # nothing dropped: every edge stays
  assert _assert_within_one_bin(profiles, "synthetic", "x", syn, union) == 101


def test_moments_are_withheld_below_the_count_floor():
  """R74.5 (the reviewer's p_misc case): fewer than k values determine
  the moments (n = 1: the mean IS the record), so mean, std, skewness and
  kurtosis are withheld on such a side; n and the counts stay, and a side
  with exactly k values shows them."""
  rng = np.random.default_rng(4)
  source = _uniform_rows(rng.normal(50, 10, 5))
  synthetic = _uniform_rows(rng.normal(50, 10, 3000))
  table = planned_table("m", _UNIFORM_FIELDS, source, synthetic, pk=("id",))
  _, profiles = _pure(
      table, {
          "source": source,
          "synthetic": synthetic,
          "reference": source[:1],
          "holdout": source[1:3],
      })
  withheld = ("mean", "std", "skewness", "kurtosis_excess", "min", "max")
  for side, n in (("source", 5), ("reference", 1), ("holdout", 2)):
    moments = _payload(profiles, "moments", side, "x")
    assert moments["n"] == n
    assert all(moments[key] is None for key in withheld)
  moments = _payload(profiles, "moments", "synthetic", "x")
  assert moments["n"] == 3000
  assert all(moments[key] is not None for key in withheld)
  source_k = _uniform_rows(rng.normal(50, 10, RARE_COUNT))
  table_k = planned_table("k", _UNIFORM_FIELDS, source_k, synthetic, pk=("id",))
  _, profiles_k = _pure(table_k, {"source": source_k, "synthetic": synthetic})
  moments_k = _payload(profiles_k, "moments", "source", "x")
  assert moments_k["n"] == RARE_COUNT and moments_k["mean"] is not None


def test_union_left_counts_are_exact_in_any_merge_order():
  """`union_left` (the left-closed union bins) gives count(x < e) exactly
  next to `union`'s count(x <= e), so count(x == e) and count(x >= e)
  are exact for R74's symmetric rule — and it adds, in any merge order."""
  rng = np.random.default_rng(3)
  values = np.array(
      [*rng.integers(0, 20, 400).astype(float), *rng.normal(10, 3, 300), 99.0])
  source = _uniform_rows(values)
  synthetic = _uniform_rows(rng.normal(10, 4, 500))
  table = planned_table("l", _UNIFORM_FIELDS, source, synthetic, pk=("id",))
  spec = DenseSpec.from_table(table)
  parts = [
      DenseProfile.from_batch(spec, b)
      for b in _batches(table, "source", source, [37, 101, 5, 250, 90])
  ]
  merged = parts[-1]
  for part in reversed(parts[:-1]):
    merged = part.merge(merged)
  edges = spec.grids[0].union
  np.testing.assert_array_equal(
      np.cumsum(merged.union_left[0])[:-1], [(values < e).sum() for e in edges])
  np.testing.assert_array_equal(
      np.cumsum(merged.union[0])[:-1], [(values <= e).sum() for e in edges])
  assert merged.union_left[0].sum() == merged.union[0].sum() == values.size
