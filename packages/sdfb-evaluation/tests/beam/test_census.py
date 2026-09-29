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
"""Tests for `sdfb_evaluation.beam.census` (Task 22): the keyed value
census, value-hash sampling, the value/pool memorization lifts and the
top-k / shape_mix profiles under the D6 literal policy (R56, R64).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import time as clock
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import apache_beam as beam
import numpy as np
import pytest
from apache_beam.testing.test_pipeline import TestPipeline as BeamTestPipeline
from apache_beam.testing.util import assert_that
from apache_beam.utils.windowed_value import WindowedValue

from sdfb_evaluation.beam import census, dense
from sdfb_evaluation.beam.census import (
    MAX_PREAGG_KEYS,
    OWNED_METRIC_IDS,
    POOL_CAP,
    RARE_COUNT,
    TOPK_ITEMS,
    CensusMetrics,
    CensusPreAggregateFn,
    CensusSpec,
    CountsCombineFn,
    FreeTextPool,
    batch_counts,
    census_outputs,
    census_refs,
    pools_from_rows,
    pools_sql,
    read_pools,
    shape_masks,
)
from sdfb_evaluation.beam.dense import DenseMetrics
from sdfb_evaluation.beam.encode import BatchEncoder, EncodeSide
from sdfb_evaluation.beam.io import InMemorySources
from sdfb_evaluation.canonical import hash64
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.context import budget as budget_module
from sdfb_evaluation.context import plan as plan_module
from sdfb_evaluation.scoring import status_for, to_metric_row, to_profile_row
from sdfb_evaluation.stats import distances, shapes
from sdfb_evaluation.stats.diversity import CensusAccumulator, summarize
from sdfb_evaluation.types import MetricValue, ProfileValue, Status

from .census_data import (
    LABEL_KEY,
    SALT,
    encode,
    identity_table,
    totals_for,
    users_table,
    with_census,
)
from .dense_data import planned_table

_CATALOGUE = load_catalogue()
_TOL = 1e-9
_SENTINEL_PREFIXES = ("0001-", "9999-")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _pure(table: Any,
          rows_by: Mapping[str, Sequence[Mapping[str, Any]]],
          *,
          pools: Mapping[str, FreeTextPool] | None = None,
          label_key: bytes = LABEL_KEY) -> census.CensusResult:
  """The in-process path: encode, dense totals, census."""
  batches = {side: encode(table, side, rows) for side, rows in rows_by.items()}
  totals = totals_for(table, batches)
  spec = CensusSpec.from_table(table)
  refs = census_refs(table, pools=pools)
  return census_outputs(
      spec, [b for side in batches.values() for b in side],
      totals,
      refs,
      label_key=label_key)


def _collect(pcoll: beam.PCollection, path: Path, label: str) -> None:

  def dump(actual: Sequence[Any]) -> None:
    path.write_bytes(pickle.dumps(list(actual)))

  assert_that(pcoll, dump, label=label)


def _run(
    table: Any,
    rows_by: Mapping[str, list],
    tmp_path: Path,
    *,
    pools: Mapping[str, Mapping[str, FreeTextPool]] | None = None
) -> tuple[list[MetricValue], list[ProfileValue], dict]:
  """The Beam path on the DirectRunner: read, encode, dense, census."""
  sources = InMemorySources({
      (table.name, side): rows for side, rows in rows_by.items()
  })
  with BeamTestPipeline() as p:
    encoded = [
        sources.read(p, table, side) | EncodeSide(table, side, salt=SALT)
        for side in rows_by
    ]
    batches = encoded | "Flatten" >> beam.Flatten()
    dense_out = batches | "Dense" >> DenseMetrics([table])
    out = ({
        "batches": batches,
        "accumulators": dense_out["accumulators"]
    }
           |
           "Census" >> CensusMetrics([table], label_key=LABEL_KEY, pools=pools))
    _collect(out["metrics"], tmp_path / "metrics.pkl", "Metrics")
    _collect(out["profiles"], tmp_path / "profiles.pkl", "Profiles")
    _collect(out["summaries"], tmp_path / "summaries.pkl", "Summaries")
  metrics = pickle.loads((tmp_path / "metrics.pkl").read_bytes())
  profiles = pickle.loads((tmp_path / "profiles.pkl").read_bytes())
  summaries = dict(pickle.loads((tmp_path / "summaries.pkl").read_bytes()))
  return metrics, profiles, summaries


def _by_key(metrics: Sequence[MetricValue]) -> dict[tuple, MetricValue]:
  out: dict[tuple, MetricValue] = {}
  for mv in metrics:
    key = (mv.metric_id, mv.column)
    assert key not in out, f"duplicate row {key}"
    out[key] = mv
  return out


def _substantive(value: Any) -> bool:
  if isinstance(value, str):
    return value.strip() != "" and not value.startswith(_SENTINEL_PREFIXES)
  if isinstance(value, date):
    return value.year not in (1, 9999)
  return True


def _pure_accumulator(
    table: Any, j: int,
    rows_by: Mapping[str, Sequence[Mapping[str, Any]]]) -> CensusAccumulator:
  """The Task 8 accumulator of plan column j, counted with a Counter over
  `hash64` codes (the matched-n flags are the encoder's own)."""
  name = table.columns[j].name
  full: dict[str, Counter] = {}
  matched: dict[str, Counter] = {}
  substantive: dict[int, bool] = {}
  for side, rows in rows_by.items():
    flags = BatchEncoder.from_table(table, side, salt=SALT).encode(rows)
    full[side], matched[side] = Counter(), Counter()
    for row, flag in zip(rows, flags.subsample_m.tolist(), strict=True):
      value = row[name]
      if value is None:
        continue
      code = hash64(name, value)
      substantive[code] = _substantive(value)
      full[side][code] += 1
      if flag:
        matched[side][code] += 1
  acc = CensusAccumulator()
  for code in sorted(set(full["source"]) | set(full["synthetic"])):
    acc.add(
        code,
        full["source"][code],
        full["synthetic"][code],
        matched["source"][code],
        matched["synthetic"][code],
        substantive=substantive[code])
  return acc


def _column_index(table: Any, name: str) -> int:
  return [c.name for c in table.columns].index(name)


def _counts(rows: Sequence[Mapping[str, Any]], name: str) -> Counter:
  return Counter(r[name] for r in rows if r[name] is not None)


@pytest.fixture(scope="module", name="users_run")
def fixture_users_run(tmp_path_factory: pytest.TempPathFactory) -> tuple:
  """One DirectRunner pass over the users table, shared by the tests that
  only read its output."""
  table, rows = users_table()
  metrics, profiles, summaries = _run(table, rows,
                                      tmp_path_factory.mktemp("users"))
  return table, rows, metrics, profiles, summaries


# --------------------------------------------------------------------------
# the brief's tests
# --------------------------------------------------------------------------
def test_census_matches_pure_summary(users_run):
  table, rows, metrics, profiles, summaries = users_run
  censused = [j for j, c in enumerate(table.columns) if c.census != "none"]
  assert censused, "the users plan censuses its non-key columns"
  assert set(summaries) == {(table.name, j) for j in censused}
  for j in censused:
    expected = summarize(_pure_accumulator(table, j, rows))
    got = summarize(summaries[(table.name, j)])
    assert got.keys() == expected.keys()
    for key, value in expected.items():
      if isinstance(value, float):  # 12 digits (R21); merge-order ulps
        assert got[key] == pytest.approx(value, rel=1e-10), (j, key)
      else:
        assert got[key] == value, (j, key)
  # the Beam path equals the in-process path, metric by metric
  pure = _pure(table, rows)
  beam_rows, pure_rows = _by_key(metrics), _by_key(pure.metrics)
  assert beam_rows.keys() == pure_rows.keys()
  for key, mv in beam_rows.items():
    other = pure_rows[key]
    for name in ("value", "baseline_value", "noise_floor", "ci_low", "ci_high",
                 "source_value", "synthetic_value"):
      a, b = getattr(mv, name), getattr(other, name)
      if a is None or b is None:
        assert a is b, (key, name, a, b)
      elif math.isinf(a) or math.isinf(b):
        assert a == b, (key, name)
      else:
        # 12 significant digits (R21): merge-order ulps may straddle one
        assert a == pytest.approx(b, rel=1e-10, abs=1e-12), (key, name)

  def payloads(values: Sequence[ProfileValue]) -> list[tuple]:
    return sorted((pv.profile_kind, pv.side, str(pv.column), pv.n, pv.truncated,
                   json.dumps(pv.payload, sort_keys=True)) for pv in values)

  assert payloads(profiles) == payloads(pure.profiles)
  # value distances equal the pure stats on the aligned count vectors
  for name in ("status", "city", "is_member"):
    src = _counts(rows["source"], name)
    syn = _counts(rows["synthetic"], name)
    _, p, q = distances.align(src, syn)
    assert beam_rows[("column.tvd", name)].value == pytest.approx(
        distances.tvd(p, q), abs=_TOL)
    assert beam_rows[("column.jsd", name)].value == pytest.approx(
        distances.jsd_bits(p, q), abs=_TOL)
    w = distances.cohens_w(p, q)
    assert w is not None
    cohens = beam_rows[("column.cohens_w", name)]
    assert cohens.value == pytest.approx(w.w, abs=_TOL)
    assert cohens.detail["q_mass_on_p0"] == pytest.approx(
        w.q_mass_on_p0, abs=_TOL)
    ref = _counts(rows["source"][:600], name)
    _, p_src, r = distances.align(src, ref)
    assert beam_rows[("column.tvd", name)].baseline_value == pytest.approx(
        distances.tvd(p_src, r), abs=_TOL)


def test_census_high_cardinality_profile_truncates_metrics_exact(users_run):
  table, rows, metrics, profiles, summaries = users_run
  by_key = _by_key(metrics)
  name = "full_name"
  j = _column_index(table, name)
  src, syn = _counts(rows["source"], name), _counts(rows["synthetic"], name)
  assert len(src) > 1000  # past the accumulator's top-1000 list
  acc = summaries[(table.name, j)]
  assert len(acc.top_src) == 1000
  assert acc.k_src == len(src) and acc.k_syn == len(syn)
  # metrics read every value, not the truncated lists
  novelty = by_key[("column.novelty_mass", name)]
  novel = sum(c for v, c in syn.items() if v not in src)
  assert novelty.value == pytest.approx(novel / sum(syn.values()), abs=_TOL)
  singletons = sum(1 for c in src.values() if c == 1)
  assert novelty.source_value == pytest.approx(
      singletons / sum(src.values()), abs=_TOL)
  distinct = by_key[("column.distinct_ratio", name)]
  assert distinct.detail["distinct_src"] == len(src)
  assert distinct.detail["distinct_syn"] == len(syn)
  # the profile truncates: at most TOPK_ITEMS labelled items plus "other"
  topk = [
      pv for pv in profiles if pv.profile_kind == "topk" and pv.column == name
  ]
  assert {pv.side for pv in topk} == {"source", "synthetic"}
  for pv in topk:
    payload = pv.payload
    assert len(payload["items"]) == TOPK_ITEMS
    assert pv.truncated is True
    total = sum(item["count"] for item in payload["items"])
    assert total + payload["other_count"] == payload["total"]
    counts = src if pv.side == "source" else syn
    assert payload["distinct"] == len(counts)
    assert payload["total"] == sum(counts.values())
    assert all(not item["literal"] for item in payload["items"])


def test_value_sampling_above_budget_is_unbiased():
  rng = np.random.default_rng(5)
  n_src, n_syn = 60_000, 40_000
  fields = ({
      "name": "row_id",
      "type": "INT64",
      "mode": "REQUIRED"
  }, {
      "name": "handle",
      "type": "STRING",
      "mode": "NULLABLE"
  })
  # source: 45k distinct handles, most of them rare (a long tail)
  handles = [f"h{int(k):06d}" for k in rng.integers(0, 45_000, n_src)]
  source = [{"row_id": i, "handle": h} for i, h in enumerate(handles)]
  # synthetic: 30 % copy a source handle, the rest are invented
  synthetic = []
  for i in range(n_syn):
    if rng.random() < 0.3:
      handle = handles[int(rng.integers(0, n_src))]
    else:
      handle = f"n{int(rng.integers(0, 10**7)):07d}"
    synthetic.append({"row_id": 10**6 + i, "handle": handle})
  rows = {"source": source, "synthetic": synthetic}
  planned = planned_table(
      "handles", fields, source, synthetic, pk=("row_id",), pair_max_columns=0)
  exact = _by_key(
      _pure(with_census(planned, handle=("exact", None)), rows).metrics)
  sampled_table = with_census(planned, handle=("value_sampled", 0.02))
  sampled = _by_key(_pure(sampled_table, rows).metrics)

  distinct = sampled[("column.distinct_ratio", "handle")]
  assert distinct.method.value == "value_sampled"
  assert distinct.sample_rate == 0.02
  truth = exact[("column.distinct_ratio", "handle")]
  for key in ("distinct_src", "distinct_syn"):
    assert distinct.detail[key] == pytest.approx(
        truth.detail[key], rel=0.10), key
  for field in ("source_value", "synthetic_value"):  # matched-n distinct
    assert getattr(distinct, field) == pytest.approx(
        getattr(truth, field), rel=0.10), field

  copy = sampled[("field.substantive_copy_rate", "handle")]
  exact_copy = exact[("field.substantive_copy_rate", "handle")].value
  assert copy.ci_low is not None and copy.ci_high is not None
  assert copy.ci_low <= exact_copy <= copy.ci_high
  assert copy.method.value == "value_sampled"
  # the Horvitz-Thompson entropy lands near the exact plug-in entropy
  entropy = sampled[("column.entropy_ratio", "handle")]
  truth_entropy = exact[("column.entropy_ratio", "handle")]
  assert entropy.source_value == pytest.approx(
      truth_entropy.source_value, rel=0.05)
  # only sampled hashes enter the census
  spec = CensusSpec.from_table(sampled_table)
  counts = batch_counts(spec, encode(sampled_table, "source", source)[0])
  keep = round(0.02 * budget_module.VALUE_SAMPLE_MODULUS)
  assert counts.values
  assert all(code % budget_module.VALUE_SAMPLE_MODULUS < keep
             for (_, _, code) in counts.values)
  # a value-sampled top-1 may lie outside the sampled hash range
  top1 = sampled[("column.top1_share_delta", "handle")]
  assert top1.value is None and "top-1" in top1.detail["reason"]


def test_value_lift_detects_planted_rare_copies(tmp_path):
  table, rows = users_table(copies_from_reference=300)
  metrics, _, _ = _run(table, rows, tmp_path)
  lift = _by_key(metrics)[("field.value_memorization_lift", "full_name")]
  assert lift.ci_low is not None and lift.ci_low >= 5
  assert lift.detail["copies_r"] >= 250 and lift.detail["copies_h"] == 0
  family = lift.detail["bonferroni_family"]
  assert family >= 2 and lift.detail["alpha"] == pytest.approx(0.05 / family)
  assert status_for(_CATALOGUE.get(lift.metric_id), lift) is Status.FAIL
  # the untouched run: no copies on either side, the bound passes
  clean = _by_key(
      _pure(*users_table()).metrics)[("field.value_memorization_lift",
                                      "full_name")]
  assert clean.value is None and clean.ci_low == 0.0
  assert status_for(_CATALOGUE.get(clean.metric_id), clean) is Status.PASS


def test_literal_policy_hashes_rare_values(users_run):
  table, rows, _, profiles, _ = users_run
  payloads = [pv for pv in profiles if pv.profile_kind in ("topk", "shape_mix")]
  assert payloads
  labels: set[str] = set()
  for pv in payloads:
    for item in pv.payload["items"]:
      labels.add(item.get("label", item.get("mask")))
  text = json.dumps([pv.payload for pv in profiles], ensure_ascii=False)
  source_values: Counter = Counter()
  synthetic_values: set[str] = set()
  for column in table.columns:
    if column.bq_type != "STRING":
      continue
    source_values.update((column.name, r[column.name])
                         for r in rows["source"]
                         if r[column.name] is not None)
    synthetic_values.update(
        r[column.name] for r in rows["synthetic"] if r[column.name])
  rare = {v for (_, v), c in source_values.items() if c < RARE_COUNT and v}
  assert rare, "the users table holds rare source values"
  assert not rare & labels
  assert not [v for v in rare if len(v) >= 5 and v in text]
  synthetic_only = synthetic_values - {v for _, v in source_values}
  assert synthetic_only and not synthetic_only & labels
  # a frequent value of a literal-ok column is shown as itself
  status = [pv for pv in payloads if pv.column == "status"]
  shown = {item["label"] for pv in status for item in pv.payload["items"]}
  assert {"active", "dormant"} <= shown
  # labels are keyed (R64): enumerating the source domain with an unkeyed
  # formula recovers none of them
  codes = {hash64(name, value) for (name, value) in source_values}
  unkeyed = {f"h:{(code >> 32) & 0xFFFFFFFF:08x}" for code in codes} | {
      "h:" +
      hashlib.blake2b(code.to_bytes(8, "big"), digest_size=4).hexdigest()
      for code in codes
  }
  hashed = {label for label in labels if label.startswith("h:")}
  assert hashed and not hashed & unkeyed


def test_labels_are_keyed_and_stable_within_a_key():
  table, rows = users_table(n_source=1500, n_synthetic=1000, n_reference=300)

  def city_labels(key: bytes) -> dict[tuple[str, int], str]:
    result = _pure(table, rows, label_key=key)
    return {
        (pv.side, rank): item["label"] for pv in result.profiles
        if pv.profile_kind == "topk" and pv.column == "city"
        for rank, item in enumerate(pv.payload["items"])
    }

  first, again, other = (city_labels(LABEL_KEY), city_labels(LABEL_KEY),
                         city_labels(b"another-32-byte-label-key-000000"))
  assert first == again
  hashed = [k for k, label in first.items() if label.startswith("h:")]
  assert hashed and all(first[k] != other[k] for k in hashed)
  # one value carries one label on both sides within a run
  by_count = {}
  for pv in _pure(table, rows).profiles:
    if pv.profile_kind == "topk" and pv.column == "city":
      for item in pv.payload["items"]:
        by_count.setdefault(item["label"], set()).add(pv.side)
  assert any(sides == {"source", "synthetic"} for sides in by_count.values())


def test_numeric_and_day_temporal_copy_rate_is_info(users_run):
  _, _, metrics, _, _ = users_run
  by_key = _by_key(metrics)
  metric = _CATALOGUE.get("field.substantive_copy_rate")
  for name in ("age", "signup_date"):
    mv = by_key[(metric.id, name)]
    assert mv.value is not None
    assert status_for(metric, mv) is Status.INFO, name
    row = to_metric_row(
        mv,
        evaluation_id="e1",
        evaluated_at="2026-09-29T00:00:00+00:00",
        landing_table=None,
        source_table=None)
    assert row["status"] == "info" and row["score"] is None
    assert "domain" in row["detail"]["reason"]
  assert by_key[(metric.id, "signup_date")].detail["day_granularity"] is True
  for name in ("last_login", "full_name", "city"):
    mv = by_key[(metric.id, name)]
    assert status_for(metric, mv) is not Status.INFO, name


# --------------------------------------------------------------------------
# coverage, lifts, pools
# --------------------------------------------------------------------------
def test_catalogue_coverage_every_owned_id_emitted_or_explained(users_run):
  table, _, metrics, profiles, _ = users_run
  ids = set(_CATALOGUE.ids())
  assert set(OWNED_METRIC_IDS) <= ids
  emitted = {mv.metric_id for mv in metrics}
  assert emitted == set(OWNED_METRIC_IDS), sorted(
      set(OWNED_METRIC_IDS) ^ emitted)
  rows_per_id: Counter = Counter()
  for mv in metrics:
    metric = _CATALOGUE.get(mv.metric_id)
    assert mv.column is not None and mv.column_kind in metric.kinds, mv
    assert mv.column_kind in {str(k) for k in census.APPLIES_TO[mv.metric_id]}
    if mv.value is None and not metric.uses_ci_bound:
      assert mv.detail.get("reason"), mv
    assert mv.encoding_plan_digest == table.encoding_plan_digest
    json.dumps(
        to_metric_row(
            mv,
            evaluation_id="e1",
            evaluated_at="2026-09-29T00:00:00+00:00",
            landing_table=table.landing_table,
            source_table=table.source_table),
        allow_nan=False)
    rows_per_id[mv.metric_id] += 1
  # one row per applicable (id, column): the reach, within the catalogue's
  # kinds (column.jsd's numeric/temporal side is the dense pass's)
  assert set(census.APPLIES_TO) == set(OWNED_METRIC_IDS)
  for metric_id in OWNED_METRIC_IDS:
    kinds = set(_CATALOGUE.get(metric_id).kinds)
    reach = {str(k) for k in census.APPLIES_TO[metric_id]}
    assert reach <= kinds, metric_id
    if metric_id != "column.jsd":
      assert reach == kinds, metric_id
    applicable = [c for c in table.columns if str(c.kind) in reach]
    assert rows_per_id[metric_id] == len(applicable), metric_id
  # a key column (census none) is explained, never silently skipped
  keyed = [
      mv for mv in metrics
      if mv.column == "user_id" and mv.metric_id == "column.distinct_ratio"
  ]
  assert all("census" in mv.detail["reason"] for mv in keyed)
  # with the dense pass, every field/column id has an owner (R62: Task 26
  # owns column.source_stats_drift)
  field_column = {
      m.id for m in _CATALOGUE.metrics if m.level in ("field", "column")
  }
  owned = set(OWNED_METRIC_IDS) | set(dense.OWNED_METRIC_IDS)
  assert field_column - owned == {"column.source_stats_drift"}
  for pv in profiles:
    json.dumps(
        to_profile_row(
            pv, evaluation_id="e1", evaluated_at="2026-09-29T00:00:00+00:00"),
        allow_nan=False)


def test_reference_unverified_marks_lifts_not_evaluated():
  table, rows = users_table(copies_from_reference=300, verified=False)
  by_key = _by_key(_pure(table, rows).metrics)
  for name in ("full_name", "city", "age"):
    mv = by_key[("field.value_memorization_lift", name)]
    assert mv.value is None and mv.ci_low is None
    assert "reference" in mv.detail["reason"]
  # fidelity is still computed
  assert by_key[("column.tvd", "city")].value is not None


def test_pool_lift_needs_the_pool_side_input_and_detects_reference_values():
  table, rows = users_table()
  bios = [r["bio"] for r in rows["source"]]
  pure = _by_key(_pure(table, rows).metrics)
  missing = pure[("field.pool_memorization_lift", "bio")]
  assert missing.value is None and "side input" in missing.detail["reason"]
  pool = FreeTextPool(
      column="bio",
      values=tuple(bios[:120] + bios[600:605]),
      target=POOL_CAP,
      reference_digest="d" * 64,
      model_uri="gs://bucket/synthetic/models/m/v1/")
  lifted = _by_key(_pure(table, rows, pools={"bio": pool}).metrics)
  lift = lifted[("field.pool_memorization_lift", "bio")]
  assert lift.detail["copies_r"] == 120 and lift.detail["copies_h"] == 5
  assert lift.ci_low is not None and lift.ci_low > 5
  assert status_for(_CATALOGUE.get(lift.metric_id), lift) is Status.FAIL
  other = _by_key(_pure(table, rows, pools={
      "other": pool
  }).metrics)[("field.pool_memorization_lift", "bio")]
  assert "no free-text pool" in other.detail["reason"]


def test_distinct_ceiling_hit_flags_the_pool_cap():
  n = 2000
  fields = ({
      "name": "row_id",
      "type": "INT64",
      "mode": "REQUIRED"
  }, {
      "name": "note",
      "type": "STRING",
      "mode": "NULLABLE"
  })
  words = " was delivered on time and in perfect condition"
  source = [{"row_id": i, "note": f"order {i}{words}"} for i in range(n)]
  synthetic = [{
      "row_id": 10**6 + i,
      "note": f"pool {i % POOL_CAP}{words}"
  } for i in range(n)]
  table = planned_table(
      "reviews", fields, source, synthetic, pk=("row_id",), pair_max_columns=0)
  assert table.columns[1].kind.value == "text"
  hit = _by_key(
      _pure(table, {
          "source": source,
          "synthetic": synthetic
      }).metrics)[("column.distinct_ceiling_hit", "note")]
  assert hit.value == 1.0 and hit.detail["distinct_syn"] == POOL_CAP
  miss = _by_key(_pure(table, {
      "source": source,
      "synthetic": source
  }).metrics)[("column.distinct_ceiling_hit", "note")]
  assert miss.value == 0.0


def test_read_pools_seam_binds_the_pool_identity():

  class FakeBq:

    def __init__(self):
      self.calls = []

    def query(self, sql, params=None, *, max_bytes=None):
      self.calls.append((sql, dict(params or {}), max_bytes))
      return [{
          "reference_digest": "d" * 64,
          "model_uri": "gs://bucket/m",
          "column": "bio",
          "target": 40,
          "values": ["a note", "another note"],
      }]

  bq = FakeBq()
  pools = read_pools(
      bq,
      pools_table="demo-project.synthetic_rag.freetext_pools",
      reference_digest="d" * 64,
      model_uri="gs://bucket/m",
      max_bytes=1 << 30)
  assert pools["bio"].values == ("a note", "another note")
  assert pools["bio"].target == 40
  sql, params, max_bytes = bq.calls[0]
  assert sql == pools_sql("demo-project.synthetic_rag.freetext_pools")
  assert params == {"reference_digest": "d" * 64, "model_uri": "gs://bucket/m"}
  assert max_bytes == 1 << 30
  assert "`values`" in sql
  assert pools_from_rows([]) == {}
  with pytest.raises(ValueError):
    pools_sql("not a table")


# --------------------------------------------------------------------------
# building blocks
# --------------------------------------------------------------------------
def test_shape_masks_match_the_verbatim_port():
  values = [
      "AB-1234", "sk042", "", "東京 3丁目", "ǅemo", "½ cup", "٣ items", "a\tb",
      "😀x", "\ud800", "\x1c", "UPPER lower 99", "x" * 300
  ]
  assert shape_masks(values) == [shapes.shape_of(v) for v in values]


def test_shape_head_tv_follows_the_pure_head_pooling(users_run):
  table, rows, metrics, profiles, _ = users_run
  name = "sku"
  assert str(table.columns[_column_index(table, name)].kind) == "identifier"

  def mass(side_rows):
    masks = Counter(
        shapes.shape_of(r[name])
        for r in side_rows
        if r[name] not in (None, ""))
    total = sum(masks.values())
    return {m: c / total for m, c in masks.items()}

  src, syn = mass(rows["source"]), mass(rows["synthetic"])
  raw, head, heads = shapes.shape_head_tv(src, syn)
  mv = _by_key(metrics)[("column.shape_head_tv", name)]
  assert mv.value == pytest.approx(head, abs=_TOL)
  assert mv.detail["raw_tv"] == pytest.approx(raw, abs=_TOL)
  assert mv.noise_floor is not None and mv.noise_floor > 0
  mixes = [
      pv for pv in profiles
      if pv.profile_kind == "shape_mix" and pv.column == name
  ]
  assert {pv.side for pv in mixes} == {"source", "synthetic"}
  for pv in mixes:
    assert {item["mask"] for item in pv.payload["items"]} == heads
    assert pv.payload["head_floor"] == 0.02
    shares = sum(item["share"] for item in pv.payload["items"])
    assert shares + pv.payload["tail_share"] == pytest.approx(1.0)
  adherence = _by_key(metrics)[("field.shape_adherence", name)]
  inside = sum(m for s, m in syn.items() if s in src)
  assert adherence.value == pytest.approx(inside, abs=_TOL)
  assert adherence.ci_low <= adherence.value <= adherence.ci_high
  # long prose has no head mask: not evaluated, with the reason
  bio = _by_key(metrics)[("column.shape_head_tv", "bio")]
  assert bio.value is None and "2 %" in bio.detail["reason"]


def test_preaggregation_flushes_early_and_never_drops():
  table, rows = users_table(n_source=800, n_synthetic=600, n_reference=200)
  spec = CensusSpec.from_table(table)
  batches = encode(table, "source", rows["source"], chunk=200)
  batches += encode(table, "synthetic", rows["synthetic"], chunk=200)
  fn = CensusPreAggregateFn({table.name: spec}, max_keys=50)
  fn.setup()
  fn.start_bundle()
  early = []
  for batch in batches:
    early.extend(fn.process(batch))
  late = list(fn.finish_bundle())
  assert early, "a full dict flushes before the bundle ends"
  totals: dict[tuple, list[int]] = {}
  masks: dict[tuple, list[int]] = {}
  combine = CountsCombineFn()
  for output in early + late:
    tag, element = None, output
    if isinstance(element, beam.pvalue.TaggedOutput):
      tag, element = element.tag, element.value
    if isinstance(element, WindowedValue):
      element = element.value
    key, value = element
    if tag == "masks":
      slot = masks.setdefault(key, [0, 0])
      slot[0] += value[0]
      slot[1] += value[1]
    elif tag is None:
      totals[key] = combine.add_input(
          totals.get(key) or combine.create_accumulator(), value)
  expected = batch_counts(spec, batches[0])
  for batch in batches[1:]:
    census.accumulate(spec, batch, expected)
  # a Beam key carries the code as the int64 with the same 64 bits
  assert {
      k: tuple(v) for k, v in totals.items()
  } == {
      (t, j, code - 2**64 if code >= 2**63 else code): tuple(v)
      for (t, j, code), v in expected.values.items()
  }
  assert {
      k: tuple(v) for k, v in masks.items()
  } == {
      k: tuple(v) for k, v in expected.masks.items()
  }
  assert MAX_PREAGG_KEYS == 100_000


def test_constants_match_the_planner():
  assert RARE_COUNT == plan_module.LITERAL_MIN_COUNT
  assert census.VALUE_SAMPLE_MODULUS == budget_module.VALUE_SAMPLE_MODULUS
  assert census.HOT_KEY_FANOUT == 16


def test_throughput_census_batch(record_property):
  table, rows = users_table(n_source=8192, n_synthetic=100, n_reference=100)
  spec = CensusSpec.from_table(table)
  batch = encode(table, "source", rows["source"], chunk=8192)[0]
  batch_counts(spec, batch)  # warm-up
  start = clock.perf_counter()
  counts = batch_counts(spec, batch)
  elapsed = clock.perf_counter() - start
  record_property("census_rows_per_s", round(batch.n / elapsed))
  assert counts.values
  assert elapsed < 30


def test_identity_columns_are_censused_by_their_text():
  table, rows = identity_table(copies=150)
  spec = CensusSpec.from_table(table)
  by_name = {c.name: c for c in spec.columns}
  assert by_name["email"].code_block == "text"
  assert by_name["member_no"].code_block == "text"
  assert by_name["tier"].code_block == "nonkey"
  assert not by_name["id"].censused
  result = _pure(table, rows)
  for name in ("email", "member_no"):
    j = _column_index(table, name)
    expected: Counter = Counter()
    for side, side_rows in rows.items():
      for r in side_rows:
        expected[(side, hash64(name, str(r[name])))] += 1
    acc = result.summaries[j]
    assert acc.k_src == len(rows["source"]) and acc.k_both == 150
    assert acc.n_syn == sum(
        c for (s, _), c in expected.items() if s == "synthetic")
    top = {code for _, code in acc.top_src}
    assert top <= {code for (s, code) in expected if s == "source"}
  by_key = _by_key(result.metrics)
  for name in ("email", "member_no"):
    lift = by_key[("field.value_memorization_lift", name)]
    assert lift.detail["copies_r"] == 150 and lift.detail["copies_h"] == 0
    assert lift.ci_low is not None and lift.ci_low > 5


def test_empty_synthetic_side_is_not_evaluated_with_reasons():
  table, rows = users_table(n_source=900, n_synthetic=600, n_reference=200)
  result = _pure(table, {"source": rows["source"]})
  assert result.metrics
  for mv in result.metrics:
    metric = _CATALOGUE.get(mv.metric_id)
    if metric.uses_ci_bound and mv.ci_low is not None:
      continue  # a lift with no events gates on its bound (R38)
    if mv.value is None:
      assert mv.detail["reason"], mv
    else:
      assert mv.metric_id in ("field.value_memorization_lift",), mv
  assert all(pv.side == "source" or pv.payload["total"] == 0
             for pv in result.profiles
             if pv.profile_kind == "topk")


def test_interval_metrics_carry_ci_and_scalar_metrics_a_noise_floor(users_run):
  _, _, metrics, _, _ = users_run
  for mv in metrics:
    if mv.value is None and mv.ci_low is None:
      continue
    method = _CATALOGUE.get(mv.metric_id).noise_floor
    if method in ("wilson", "newcombe", "rate_ratio"):
      assert mv.ci_low is not None and mv.ci_high is not None, mv
      assert mv.ci_low <= mv.ci_high, mv
      assert mv.noise_floor is None, mv
    elif method in ("tvd_null", "jsd_null"):
      assert mv.noise_floor is not None and mv.noise_floor >= 0, mv
      assert mv.ci_low is None and mv.ci_high is None, mv
    else:
      assert method in (None, "none"), mv
      assert mv.noise_floor is None and mv.ci_low is None, mv
