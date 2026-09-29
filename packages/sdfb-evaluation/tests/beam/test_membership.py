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
"""Tests for `sdfb_evaluation.beam.membership` (Task 23): map-side row
membership against the R/E/H panel and the full source, the
memorization / exposure / near-match lifts, internal and key duplicates,
and the bounded exact/near-copy row flags (keys only, never values).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import base64
import bz2
import dataclasses
import functools
import json
import math
import pickle
import time as clock
import zlib
from collections import Counter
from collections.abc import Mapping, Sequence
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

from sdfb_evaluation.beam import census, dense, membership
from sdfb_evaluation.beam.encode import EncodeSide, key_hash
from sdfb_evaluation.beam.io import InMemorySources
from sdfb_evaluation.beam.label_key import LabelKey
from sdfb_evaluation.beam.membership import (
    EXACT_COPY,
    NEAR_COPY,
    OWNED_METRIC_IDS,
    UNVERIFIED_REASON,
    KeyCountsCombineFn,
    Membership,
    MembershipSpec,
    PanelIndex,
    PanelRefs,
    RowFlag,
    batch_membership,
    flag_row,
    key_counts,
    membership_outputs,
)
from sdfb_evaluation.canonical import hashed_label
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.schemas import load_schema
from sdfb_evaluation.scoring import status_for, to_metric_row
from sdfb_evaluation.stats import noise
from sdfb_evaluation.types import MetricValue, Status

from .membership_data import (
    CATALOG_PATTERNS,
    LABEL_KEY,
    PEOPLE_FIELDS,
    PEOPLE_IDENTITY,
    PEOPLE_NONKEY,
    SALT,
    SOURCE_ID_BASE,
    SYNTHETIC_ID_BASE,
    catalog_table,
    copy_content,
    encode,
    encode_all,
    panel_of,
    people_rows,
    people_table,
    planned,
    zipf_weights,
)

_CATALOGUE = load_catalogue()
PEOPLE_FIELDS_NAMES = tuple(f["name"] for f in PEOPLE_FIELDS)
_EVALUATED_AT = "2026-09-29T00:00:00+00:00"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _pure(table: Any, rows_by: Mapping[str, Sequence[Mapping[str, Any]]],
          **kwargs: Any) -> membership.MembershipResult:
  """The in-process path over the same encoded batches Beam sees."""
  kwargs.setdefault("label_key", LABEL_KEY)
  return membership_outputs(
      table, encode_all(table, rows_by), salt=SALT, **kwargs)


def _collect(pcoll: beam.PCollection, path: Path, label: str) -> None:

  def dump(actual: Sequence[Any]) -> None:
    path.write_bytes(pickle.dumps(list(actual)))

  assert_that(pcoll, dump, label=label)


def _run(table: Any, rows_by: Mapping[str, list], tmp_path: Path,
         **kwargs: Any) -> tuple[list[MetricValue], list[RowFlag]]:
  """The Beam path on the DirectRunner: read, encode, membership."""
  sources = InMemorySources({
      (table.name, side): rows for side, rows in rows_by.items()
  })
  with BeamTestPipeline() as p:
    batches = [
        sources.read(p, table, side) | EncodeSide(table, side, salt=SALT)
        for side in rows_by
    ] | "Flatten" >> beam.Flatten()
    key = p | "Key" >> beam.Create([LABEL_KEY])
    out = batches | "Membership" >> Membership(
        [table], salt=SALT, label_key=key, **kwargs)
    _collect(out["metrics"], tmp_path / "metrics.pkl", "Metrics")
    _collect(out["flags"], tmp_path / "flags.pkl", "Flags")
  metrics = pickle.loads((tmp_path / "metrics.pkl").read_bytes())
  flags = pickle.loads((tmp_path / "flags.pkl").read_bytes())
  return metrics, flags


def _by_id(metrics: Sequence[MetricValue]) -> dict[str, MetricValue]:
  out: dict[str, MetricValue] = {}
  for mv in metrics:
    assert mv.metric_id not in out, f"duplicate row {mv.metric_id}"
    out[mv.metric_id] = mv
  return out


def _status(mv: MetricValue) -> Status:
  return status_for(_CATALOGUE.get(mv.metric_id), mv)


def _comparable(mv: MetricValue) -> tuple:
  """A metric row minus the path note (side input vs keyed count)."""
  detail = {
      k: v for k, v in mv.detail.items() if k not in ("path", "path_note")
  }
  return (dataclasses.replace(mv,
                              detail={}), json.dumps(detail, sort_keys=True))


def _people_with(copies: int = 0,
                 full_copies: int = 0,
                 near_copies: int = 0,
                 *,
                 e_n: int | None = None,
                 verified: bool = True,
                 n_source: int = 3000,
                 n_synthetic: int = 2000,
                 n_reference: int = 600) -> tuple[Any, dict, dict]:
  """The people table with planted copies: `copies` synthetic rows carry
  an R row's content under their own key, `full_copies` the whole R row
  (its key included), `near_copies` an R row's content with the city
  changed. Returns (table, rows by side, the donor of each copied row by
  synthetic person_id)."""
  source = people_rows(n_source, 31, SOURCE_ID_BASE)
  synthetic = people_rows(n_synthetic, 32, SYNTHETIC_ID_BASE)
  donors: dict[int, dict] = {}
  at = 0
  for k in range(copies):
    donor = source[(7 * k) % n_reference]
    synthetic[at] = copy_content(donor, synthetic[at])
    donors[synthetic[at]["person_id"]] = donor
    at += 1
  for k in range(full_copies):
    donor = source[(11 * k + 301) % n_reference]
    synthetic[at] = dict(donor)  # its key and identity too
    donors[synthetic[at]["person_id"]] = donor
    at += 1
  for k in range(near_copies):
    donor = source[(13 * k + 5) % n_reference]
    changed = copy_content(donor, synthetic[at])
    changed["city"] = f"Elsewhere{k:03d}"
    synthetic[at] = changed
    donors[synthetic[at]["person_id"]] = donor
    at += 1
  table = planned(
      "people",
      PEOPLE_FIELDS,
      source,
      synthetic,
      pk=("person_id",),
      identity=PEOPLE_IDENTITY,
      panel=panel_of(source, n_reference, e_n=e_n, verified=verified))
  return table, {"source": source, "synthetic": synthetic}, donors


def _flag_values_absent(flags: Sequence[RowFlag], rows_by: Mapping[str,
                                                                   Sequence],
                        columns: Sequence[str]) -> None:
  """No attribute value of any row appears in any flag row's JSON."""
  text = json.dumps([
      flag_row(f, evaluation_id="e1", evaluated_at=_EVALUATED_AT) for f in flags
  ],
                    default=str)
  for rows in rows_by.values():
    for row in rows:
      for column in columns:
        value = row[column]
        if value is None:
          continue
        literal = str(value)
        if len(literal) >= 4:  # short numbers collide with ranks/digests
          assert literal not in text, (column, literal)


# --------------------------------------------------------------------------
# the brief's tests
# --------------------------------------------------------------------------
def test_verbatim_reference_copies_lift_and_flags():
  table, rows, donors = _people_with(copies=40, full_copies=5, e_n=200)
  result = _pure(table, rows)
  by_id = _by_id(result.metrics)
  n_syn = len(rows["synthetic"])
  # 45 synthetic rows reproduce an R record's content
  lift = by_id["row.memorization_lift"]
  assert lift.detail["events_r"] == 45 and lift.detail["events_h"] == 0
  assert lift.ci_low is not None and lift.ci_low >= 5
  assert _status(lift) is Status.FAIL
  nonkey = by_id["row.exact_match_rate_nonkey"]
  assert nonkey.value == pytest.approx(45 / n_syn)
  assert nonkey.ci_low <= nonkey.value <= nonkey.ci_high
  assert _status(nonkey) is Status.FAIL
  # only the 5 full copies match a source row key included
  full = by_id["row.exact_match_rate"]
  assert full.value == pytest.approx(5 / n_syn)
  assert _status(full) is Status.FAIL
  # flags: every copy, E before R, the donor's key as a keyed hash
  flags = [f for f in result.flags if f.check == EXACT_COPY]
  assert len(flags) == 45
  assert [f.rank for f in flags] == list(range(1, 46))
  sets = [f.source_set for f in flags]
  assert set(sets) == {"E", "R"}
  assert sets == sorted(sets, key=["E", "R", "H", "source"].index)
  for flag in flags:
    assert flag.synthetic_key is not None
    donor = donors[flag.synthetic_key["person_id"]]
    assert flag.source_key_hash == hashed_label(
        key_hash([donor["person_id"]]), key=LABEL_KEY)
    assert flag.distance == 0.0
    assert flag.detail["full_row"] is (
        donor["person_id"] == flag.synthetic_key["person_id"])
  assert not [f for f in result.flags if f.check == NEAR_COPY]


def test_chance_matches_do_not_trip_lift():
  """A low-entropy table: synthetic rows drawn from the source's own
  (skewed) pattern distribution hit R and H alike by chance. Many
  synthetic rows per pattern (n_syn >> n) make row counts overdispersed;
  the lift counts distinct records, so its interval covers 1."""
  for seed in range(4):
    rng = np.random.default_rng(100 + seed)
    weights = zipf_weights(CATALOG_PATTERNS, 1.1)
    source = rng.choice(CATALOG_PATTERNS, size=2400, p=weights)
    synthetic = rng.choice(CATALOG_PATTERNS, size=8000, p=weights)
    table, rows = catalog_table(source, synthetic, n_reference=800)
    by_id = _by_id(_pure(table, rows).metrics)
    lift = by_id["row.memorization_lift"]
    assert lift.detail["events_r"] > 20 and lift.detail["events_h"] > 20
    assert lift.detail["rows_r"] > lift.detail["events_r"]  # clustered rows
    assert lift.ci_low <= 1.0 <= lift.ci_high, (seed, lift)
    assert _status(lift) is Status.PASS
    near = by_id["row.near_match_lift"]
    assert near.ci_low <= 1.0 <= near.ci_high, (seed, near)
    # the chance hits are real exact matches of the source
    assert by_id["row.exact_match_rate_nonkey"].value > 0.3


def test_exposure_lift_attributes_prompt_rows():
  """Copies drawn only from E (the prompt-exposed ranks) raise the
  exposure lift; R\\E vs H\\H_E (`detail.unexposed`) stays at chance."""
  rng = np.random.default_rng(7)
  source = rng.choice(CATALOG_PATTERNS, size=3000)  # uniform patterns
  r_patterns = set(source[:1000].tolist())
  h_patterns = set(source[1000:2000].tolist())
  e_exclusive = [
      p for p in dict.fromkeys(source[:250].tolist()) if p not in h_patterns
  ]
  copies = e_exclusive[:100]
  synthetic = np.concatenate([rng.choice(CATALOG_PATTERNS, size=500), copies])
  table, rows = catalog_table(source, synthetic, n_reference=1000, e_n=250)
  by_id = _by_id(_pure(table, rows).metrics)
  exposure = by_id["row.exposure_lift"]
  assert exposure.value > 5 and exposure.ci_low >= 2
  assert _status(exposure) in (Status.WARN, Status.FAIL)
  unexposed = exposure.detail["unexposed"]
  assert unexposed["ci_low"] <= 1.0 <= unexposed["ci_high"]
  assert unexposed["events_r"] > 10 and unexposed["events_h"] > 10
  # the whole-R lift carries the E copies too, diluted by R\E
  lift = by_id["row.memorization_lift"]
  assert 1.0 < lift.value < exposure.value
  assert r_patterns  # (the fixture really has an R\E part)


def test_near_match_one_field_changed():
  table, rows, donors = _people_with(copies=10, near_copies=30, e_n=300)
  result = _pure(table, rows)
  by_id = _by_id(result.metrics)
  n_syn = len(rows["synthetic"])
  rate = by_id["row.near_match_rate"]
  # the 10 exact copies are exact, not near
  assert rate.value == pytest.approx(30 / n_syn)
  assert rate.ci_low <= rate.value <= rate.ci_high
  assert _status(rate) is Status.FAIL
  lift = by_id["row.near_match_lift"]
  assert lift.detail["events_r"] == 30 and lift.detail["events_h"] == 0
  assert lift.ci_low >= 5
  near = [f for f in result.flags if f.check == NEAR_COPY]
  assert len(near) == 30
  for flag in near:
    assert flag.detail == {"differs_in": "city"}
    donor = donors[flag.synthetic_key["person_id"]]
    assert flag.source_key_hash == hashed_label(
        key_hash([donor["person_id"]]), key=LABEL_KEY)
    assert flag.distance == pytest.approx(1 / len(PEOPLE_NONKEY))
    assert flag.source_set in ("E", "R")
  assert {f.source_set for f in near} == {"E", "R"}
  exact = [f for f in result.flags if f.check == EXACT_COPY]
  assert len(exact) == 10


def test_near_match_is_verified_against_the_row_not_the_hash():
  """A leave-one-out hash hit whose record differs in a second column (a
  hash collision, simulated by editing the R row's cell codes but not its
  row hash) is rejected by the exact per-column check."""
  table, rows, _ = _people_with(near_copies=1)
  spec = MembershipSpec.from_table(table, salt=SALT)
  refs = PanelRefs.from_table(table, spec)
  batches = encode(table, "synthetic", rows["synthetic"], chunk=4096)
  index = PanelIndex.build(spec, refs)
  hits = sum(
      batch_membership(spec, index, None, b, top_k=10)[0].near_rows_r
      for b in batches)
  assert hits == 1
  donor_rank = 5  # `_people_with`'s first near-copy donor
  tampered = refs.r_cells.copy()
  surname = spec.nonkey_columns.index("surname")
  tampered[donor_rank, surname] ^= np.uint64(1)  # same row hash, new cell
  collided = PanelIndex.build(spec, dataclasses.replace(refs, r_cells=tampered))
  hits = sum(
      batch_membership(spec, collided, None, b, top_k=10)[0].near_rows_r
      for b in batches)
  assert hits == 0


def test_reference_unverified_marks_lifts_not_evaluated():
  table, rows, _ = _people_with(copies=40, near_copies=10, verified=False)
  result = _pure(table, rows)
  by_id = _by_id(result.metrics)
  for metric_id in ("row.memorization_lift", "row.exposure_lift",
                    "row.near_match_rate", "row.near_match_lift"):
    mv = by_id[metric_id]
    assert mv.value is None and mv.ci_low is None, metric_id
    assert mv.detail["reason"] == UNVERIFIED_REASON, metric_id
    assert _status(mv) is Status.NOT_EVALUATED
  # the full-source metrics are still computed
  assert by_id["row.exact_match_rate_nonkey"].value == pytest.approx(
      40 / len(rows["synthetic"]))
  assert by_id["row.internal_duplicate_excess"].value is not None
  # no flag claims R, E or H membership
  assert result.flags
  assert {f.source_set for f in result.flags} == {"source"}


def test_pk_duplicates_fail_integrity():
  source = people_rows(1500, 31, SOURCE_ID_BASE)
  synthetic = people_rows(1200, 32, SYNTHETIC_ID_BASE)
  for k in range(5):  # five duplicated keys: ten rows
    synthetic[100 + k]["person_id"] = synthetic[k]["person_id"]
  for k in range(3):  # NULL keys: a separate integrity signal
    synthetic[200 + k]["person_id"] = None
  for k in range(4):  # an identity value reused by a second row: 8 rows
    synthetic[300 + k]["email"] = synthetic[400 + k]["email"]
  table = planned(
      "people",
      PEOPLE_FIELDS,
      source,
      synthetic,
      pk=("person_id",),
      identity=("email",),
      panel=panel_of(source, 300))
  by_id = _by_id(
      _pure(table, {
          "source": source,
          "synthetic": synthetic
      }).metrics)
  pk = by_id["table.pk_duplicate_rate"]
  assert pk.value == pytest.approx(10 / 1200)
  assert pk.detail["duplicate_rows"] == 10
  assert pk.detail["null_key_rows"] == 3
  assert _status(pk) is Status.FAIL
  ident = by_id["table.identity_duplicate_rate"]
  assert ident.value == pytest.approx(8 / 1200)
  assert _status(ident) is Status.FAIL
  # a table without a declared key is NOT_EVALUATED, never clean
  keyless = planned("people", PEOPLE_FIELDS, source, synthetic)
  by_id = _by_id(
      _pure(keyless, {
          "source": source,
          "synthetic": synthetic
      }).metrics)
  for metric_id in ("table.pk_duplicate_rate", "table.identity_duplicate_rate"):
    assert by_id[metric_id].value is None
    assert "declared" in by_id[metric_id].detail["reason"]


def test_flags_never_carry_values(tmp_path):
  table, rows, _ = _people_with(copies=12, full_copies=3, near_copies=8)
  metrics, flags = _run(table, rows, tmp_path, row_flags_top_k=100)
  assert {f.check for f in flags} == {EXACT_COPY, NEAR_COPY}
  schema = [f["name"] for f in load_schema("evaluation_row_flags")]
  for flag in flags:
    row = flag_row(flag, evaluation_id="e1", evaluated_at=_EVALUATED_AT)
    assert list(row) == schema
    json.dumps(row, allow_nan=False)
    assert row["source_key"] is None
    assert set(row["synthetic_key"]) == {"person_id"}  # the PK only
    assert row["source_key_hash"].startswith("h:")
    assert row["source_set"] in ("R", "E", "H", "source")
  # no attribute value, no source key, no identity value
  _flag_values_absent(flags, rows, (*PEOPLE_NONKEY, "email"))
  text = json.dumps([
      flag_row(f, evaluation_id="e1", evaluated_at=_EVALUATED_AT) for f in flags
  ])
  for row in rows["source"]:
    assert str(row["person_id"]) not in text or any(
        f.synthetic_key["person_id"] == row["person_id"] for f in flags)
  assert metrics


# --------------------------------------------------------------------------
# more behaviour
# --------------------------------------------------------------------------
@pytest.fixture(scope="module", name="copies_run")
def fixture_copies_run(tmp_path_factory: pytest.TempPathFactory) -> tuple:
  """One DirectRunner pass (side-input mode) over a people table with
  every planted defect, shared by the tests that only read its output."""
  table, rows, donors = _people_with(
      copies=15,
      full_copies=4,
      near_copies=9,
      e_n=300,
      n_source=1500,
      n_synthetic=1200,
      n_reference=400)
  metrics, flags = _run(table, rows, tmp_path_factory.mktemp("copies"))
  return table, rows, donors, metrics, flags


def test_beam_equals_the_in_process_path(copies_run):
  table, rows, _, metrics, flags = copies_run
  pure = _pure(table, rows)
  assert sorted(
      metrics, key=lambda m: m.metric_id) == sorted(
          pure.metrics, key=lambda m: m.metric_id)
  assert sorted(
      flags, key=lambda f: (f.check, f.rank)) == sorted(
          pure.flags, key=lambda f: (f.check, f.rank))


def test_keyed_count_path_matches_the_side_input(copies_run, tmp_path):
  """Above the side-input budget (forced here with 0 bytes) the full-source
  matches come from the keyed count: the same metric values; only the
  full-source ('source') flags need the map-side set."""
  table, rows, _, metrics, flags = copies_run
  keyed_metrics, keyed_flags = _run(
      table, rows, tmp_path, source_set_max_bytes=0)
  assert sorted(
      map(_comparable, keyed_metrics), key=lambda c: c[0].metric_id) == sorted(
          map(_comparable, metrics), key=lambda c: c[0].metric_id)
  paths = {
      mv.detail.get("path")
      for mv in keyed_metrics
      if mv.metric_id.startswith("row.exact_match")
  }
  assert paths == {"keyed_count"}
  # every copy here comes from the panel, so no flag needs the source set
  assert not [f for f in flags if f.source_set == "source"]
  assert sorted(
      keyed_flags, key=lambda f: (f.check, f.rank)) == sorted(
          flags, key=lambda f: (f.check, f.rank))


def test_catalogue_coverage_every_owned_id_emitted_or_explained(copies_run):
  table, _, _, metrics, _ = copies_run
  ids = set(_CATALOGUE.ids())
  assert set(OWNED_METRIC_IDS) <= ids
  assert Counter(mv.metric_id for mv in metrics) == Counter(OWNED_METRIC_IDS)
  for mv in metrics:
    metric = _CATALOGUE.get(mv.metric_id)
    assert metric.level in ("row", "table")
    assert mv.column is None and mv.table == table.name
    if mv.value is None and not metric.uses_ci_bound:
      assert mv.detail.get("reason"), mv
    if metric.noise_floor in ("wilson", "rate_ratio") and mv.value is not None:
      assert mv.ci_low is not None, mv  # R41: intervals carry their CI
    assert mv.encoding_plan_digest == table.encoding_plan_digest
    json.dumps(
        to_metric_row(
            mv,
            evaluation_id="e1",
            evaluated_at=_EVALUATED_AT,
            landing_table=table.landing_table,
            source_table=table.source_table),
        allow_nan=False)
  # with the dense pass, every row-level id has an owner except Task 24's
  # nearest-neighbour metrics
  row_ids = {m.id for m in _CATALOGUE.by_level("row")}
  owned = set(OWNED_METRIC_IDS) | set(dense.OWNED_METRIC_IDS) | set(
      census.OWNED_METRIC_IDS)
  assert row_ids - owned == {
      "row.dcr_train_holdout_share", "row.dcr_p5_ratio", "row.nndr_p5_ratio",
      "row.density", "row.coverage"
  }


def test_degenerate_tables_are_not_evaluated_with_reasons():
  table, rows, _ = _people_with()
  empty = _by_id(
      _pure(table, {
          "source": rows["source"],
          "synthetic": []
      }).metrics)
  assert set(empty) == set(OWNED_METRIC_IDS)
  for metric_id, mv in empty.items():
    assert mv.value is None and mv.detail.get("reason"), metric_id
  no_panel = dataclasses.replace(table, panel=None)
  by_id = _by_id(_pure(no_panel, rows).metrics)
  for metric_id in ("row.memorization_lift", "row.exposure_lift",
                    "row.near_match_rate", "row.near_match_lift"):
    assert "panel" in by_id[metric_id].detail["reason"], metric_id
  assert by_id["row.exact_match_rate"].value is not None
  # every column a key: nothing to match on but the key tuple itself
  keys_only = planned(
      "links",
      PEOPLE_FIELDS[:2], [{
          "person_id": r["person_id"],
          "email": r["email"]
      } for r in rows["source"]], [{
          "person_id": r["person_id"],
          "email": r["email"]
      } for r in rows["synthetic"]],
      pk=("person_id",),
      identity=("email",),
      panel=None)
  by_id = _by_id(
      _pure(
          keys_only, {
              "source": [{
                  "person_id": r["person_id"],
                  "email": r["email"]
              } for r in rows["source"]],
              "synthetic": [{
                  "person_id": r["person_id"],
                  "email": r["email"]
              } for r in rows["synthetic"]]
          }).metrics)
  for metric_id in ("row.exact_match_rate_nonkey",
                    "row.internal_duplicate_excess", "row.near_match_rate"):
    assert "non-key" in by_id[metric_id].detail["reason"], metric_id
  assert by_id["row.exact_match_rate"].value == 0.0


def test_clean_run_lift_is_null_with_a_finite_bound():
  """R38: no match on either side leaves the lift undefined (value NULL)
  with ci_low 0 — PASS on the bound, never NOT_EVALUATED."""
  table, rows = people_table()
  by_id = _by_id(_pure(table, rows).metrics)
  for metric_id in ("row.memorization_lift", "row.exposure_lift",
                    "row.near_match_lift"):
    mv = by_id[metric_id]
    assert mv.value is None and mv.ci_low == 0.0, metric_id
    assert _status(mv) is Status.PASS, metric_id
  assert by_id["row.exact_match_rate_nonkey"].value == 0.0
  assert _status(by_id["row.exact_match_rate_nonkey"]) is Status.PASS


def test_all_null_nonkey_rows_never_match_or_duplicate():
  """R60: a row whose every non-key column is NULL carries no content —
  never an exact or near match, never an internal duplicate."""
  source = people_rows(900, 31, SOURCE_ID_BASE)
  synthetic = people_rows(700, 32, SYNTHETIC_ID_BASE)
  blank = dict.fromkeys(PEOPLE_NONKEY)
  for k in range(60):
    source[k] = {**source[k], **blank}  # inside R
    source[300 + k] = {**source[300 + k], **blank}  # inside H
    synthetic[k] = {**synthetic[k], **blank}
  table = planned(
      "people",
      PEOPLE_FIELDS,
      source,
      synthetic,
      pk=("person_id",),
      identity=PEOPLE_IDENTITY,
      panel=panel_of(source, 300))
  result = _pure(table, {"source": source, "synthetic": synthetic})
  by_id = _by_id(result.metrics)
  assert by_id["row.exact_match_rate_nonkey"].value == 0.0
  assert by_id["row.memorization_lift"].value is None
  assert by_id["row.near_match_rate"].value == 0.0
  dup = by_id["row.internal_duplicate_excess"]
  assert dup.value == 0.0
  assert dup.detail["null_content_rows_synthetic"] == 60
  assert not result.flags


def test_internal_duplicates_count_the_full_data():
  """R59: duplicate rates at FULL n on both sides (never the matched-n
  subsample), synthetic minus source, with a Newcombe interval and the
  reference sample's own rate as the baseline (D4)."""
  source = people_rows(2000, 31, SOURCE_ID_BASE)
  synthetic = people_rows(5000, 32, SYNTHETIC_ID_BASE)
  for k in range(30):  # 30 source records repeated once: 60 rows
    source[1000 + k] = copy_content(source[1500 + k], source[1000 + k])
  for k in range(100):  # 100 synthetic records repeated twice: 300 rows
    for extra in (1, 2):
      at = 1000 + 3 * k + extra
      synthetic[at] = copy_content(synthetic[1000 + 3 * k], synthetic[at])
  for k in range(5):  # two duplicated pairs inside R
    source[10 + k] = copy_content(source[20 + k], source[10 + k])
  table = planned(
      "people",
      PEOPLE_FIELDS,
      source,
      synthetic,
      pk=("person_id",),
      identity=PEOPLE_IDENTITY,
      panel=panel_of(source, 500))
  mv = _by_id(_pure(table, {
      "source": source,
      "synthetic": synthetic
  }).metrics)["row.internal_duplicate_excess"]
  d_src, d_syn = 70 / 2000, 300 / 5000
  assert mv.source_value == pytest.approx(d_src)
  assert mv.synthetic_value == pytest.approx(d_syn)
  assert mv.value == pytest.approx(d_syn - d_src)
  lo, hi = noise.newcombe_diff_interval(300, 5000, 70, 2000)
  assert (mv.ci_low, mv.ci_high) == pytest.approx((lo, hi))
  assert mv.baseline_value == pytest.approx(10 / 500 - d_src)
  assert mv.n_source == 2000 and mv.n_synthetic == 5000


def test_panel_size_guard_skips_near_matching_with_a_reason():
  table, rows, _ = _people_with(near_copies=5)
  by_id = _by_id(_pure(table, rows, panel_max_bytes=8192).metrics)
  for metric_id in ("row.near_match_rate", "row.near_match_lift"):
    assert "MB" in by_id[metric_id].detail["reason"], metric_id
  assert by_id["row.memorization_lift"].ci_low is not None
  tiny = _by_id(_pure(table, rows, panel_max_bytes=64).metrics)
  assert "MB" in tiny["row.memorization_lift"].detail["reason"]
  assert tiny["row.exact_match_rate"].value is not None


def test_flags_are_bounded_ranked_and_deterministic():
  table, rows, _ = _people_with(copies=40, near_copies=20, e_n=100)
  first = _pure(table, rows, row_flags_top_k=7).flags
  again = _pure(table, rows, row_flags_top_k=7).flags
  assert first == again
  for check in (EXACT_COPY, NEAR_COPY):
    ranked = [f for f in first if f.check == check]
    assert [f.rank for f in ranked] == list(range(1, 8))
    assert ranked[0].source_set == "E"  # the prompt-exposed copies first
  # batch boundaries do not change the chosen flags
  rechunked = membership_outputs(
      table,
      encode_all(table, rows, chunk=311),
      salt=SALT,
      label_key=LABEL_KEY,
      row_flags_top_k=7).flags
  assert rechunked == first


def test_keyed_counts_do_not_depend_on_buckets_or_compaction(monkeypatch):
  table, rows, _ = _people_with(copies=20, near_copies=5)
  baseline = _pure(table, rows)
  monkeypatch.setattr(membership, "_bucket_bits", lambda _table: 5)
  monkeypatch.setattr(membership, "_COMPACT_CODES", 64)
  assert _pure(table, rows) == baseline
  # the keyed-count combine: split and merged in any order, exact sums
  spec = MembershipSpec.from_table(table, salt=SALT)
  assert spec.bucket_bits == 5
  parts: dict[tuple, list] = {}
  for batch in encode_all(table, rows):
    for key, part in key_counts(spec, batch)[0]:
      parts.setdefault(key, []).append(part)
  assert len({key[2] for key in parts}) > 1
  fn = KeyCountsCombineFn()
  for (_, _, bucket), items in parts.items():
    whole = fn.extract_output(
        functools.reduce(fn.add_input, items, fn.create_accumulator()))
    halves = [
        functools.reduce(fn.add_input, items[k::2], fn.create_accumulator())
        for k in (1, 0)
    ]
    merged = fn.extract_output(fn.merge_accumulators(halves))
    for name in ("codes", "src", "syn"):
      assert np.array_equal(getattr(whole, name), getattr(merged, name))
    assert set((whole.codes >> np.uint64(59)).tolist()) == {bucket}
    assert int(whole.src.sum() + whole.syn.sum()) == sum(
        int(i.src.sum() + i.syn.sum()) for i in items)


def test_keyless_table_flags_name_the_record_by_its_content():
  """No key at all: the full row IS the content, and a flag names the
  copied record by a keyed label of its row hash (no synthetic key)."""
  source = people_rows(900, 31, SOURCE_ID_BASE)
  synthetic = people_rows(700, 32, SYNTHETIC_ID_BASE)
  for k in range(12):
    synthetic[k] = dict(source[k])
  table = planned(
      "people", PEOPLE_FIELDS, source, synthetic, panel=panel_of(source, 300))
  result = _pure(table, {"source": source, "synthetic": synthetic})
  by_id = _by_id(result.metrics)
  assert by_id["row.exact_match_rate"].value == pytest.approx(12 / 700)
  assert by_id["row.exact_match_rate_nonkey"].value == pytest.approx(12 / 700)
  assert by_id["table.pk_duplicate_rate"].value is None
  flags = [f for f in result.flags if f.check == EXACT_COPY]
  assert len(flags) == 12
  for flag in flags:
    assert flag.synthetic_key is None and flag.detail["full_row"] is True
    assert flag.source_key_hash is not None
    assert flag.source_key_hash.startswith("h:")
  _flag_values_absent(flags, {"source": source}, PEOPLE_FIELDS_NAMES)


def test_label_key_never_enters_the_job_graph(tmp_path):
  table, rows, _ = _people_with(copies=6)
  operator_key = b"operator-secret-key-material-0042"
  key_file = tmp_path / "label.key"
  key_file.write_bytes(operator_key)
  sources = InMemorySources({
      ("people", side): rows[side] for side in ("source", "synthetic")
  })
  pipeline = BeamTestPipeline()
  with pipeline as p:
    batches = [
        sources.read(p, table, side) | EncodeSide(table, side, salt=SALT)
        for side in rows
    ] | "Flatten" >> beam.Flatten()
    key = p | "LabelKey" >> LabelKey(str(key_file), reader=_key_file_reader)
    out = batches | "Membership" >> Membership(
        [table], salt=SALT, label_key=key)
    _collect(out["flags"], tmp_path / "flags.pkl", "Flags")
  flags = pickle.loads((tmp_path / "flags.pkl").read_bytes())
  assert flags and all(
      f.source_key_hash is None or f.source_key_hash.startswith("h:")
      for f in flags)
  labelled = next(f for f in flags if f.source_set != "source")
  assert labelled.source_key_hash in {
      hashed_label(key_hash([r["person_id"]]), key=operator_key)
      for r in rows["source"]
  }
  for blob in _pickled_payloads(pipeline):
    assert operator_key not in blob
    assert operator_key.hex().encode() not in blob
  with pytest.raises(TypeError, match="PCollection"):
    Membership([table], salt=SALT,
               label_key=LABEL_KEY)  # type: ignore[arg-type]


def _key_file_reader(uri: str) -> bytes:
  """A fake operator-key reader: the key lives in a local file, so only
  its path is in the graph."""
  return Path(uri).read_bytes()


def _pickled_payloads(pipeline: beam.Pipeline) -> list[bytes]:
  """Every ParDo payload of the job graph, unpickled to raw bytes, plus the
  serialised graph itself."""
  proto = pipeline.to_runner_api()
  out = []
  for transform in proto.components.transforms.values():
    if transform.spec.urn != common_urns.primitives.PAR_DO.urn:
      continue
    payload = beam_runner_api_pb2.ParDoPayload.FromString(
        transform.spec.payload)
    blob = payload.do_fn.payload
    if not blob:  # CombineGlobally's KeyWithVoid: a URN, no pickled payload
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


def test_throughput_batch_membership(record_property):
  table, rows, _ = _people_with(
      copies=50,
      near_copies=50,
      n_source=9000,
      n_synthetic=8192,
      n_reference=2000)
  spec = MembershipSpec.from_table(table, salt=SALT)
  index = PanelIndex.build(spec, PanelRefs.from_table(table, spec))
  batch = encode(table, "synthetic", rows["synthetic"], chunk=8192)[0]
  batch_membership(spec, index, None, batch, top_k=100)  # warm-up
  start = clock.perf_counter()
  part, _ = batch_membership(spec, index, None, batch, top_k=100)
  elapsed = clock.perf_counter() - start
  record_property("membership_rows_per_s", round(batch.n / elapsed))
  assert part.near_rows_r == 50 and part.rows_r_only == 50
  assert elapsed < 30
  assert math.isfinite(elapsed)
