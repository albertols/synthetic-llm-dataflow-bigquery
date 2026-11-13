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
"""Driving-key candidate keys — a tuple 1:1 with the PK by construction
(ADR 0046).

The corp TABLE_A carried a triple (COL_P constant, COL_Q, COL_R numeric)
that is 1:1 with its PK in the source, sampled per column: 27% of b2's
10M tuples collided and b1 collapsed further, so the grandchild driven by
the triple fanned out from a fraction of the parent's keys. These tests
pin the mechanism that replaces the per-column draw: an injection from
the row's dense ordinal into the discriminator's integer support, keyed
by the run, shaped by the reference sample, and never landing a source
value.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0046).
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=missing-class-docstring,protected-access

from __future__ import annotations

from decimal import Decimal

import pytest
from sdfb_core.engines.candidate_keys import (
    CandidateKeyEncoder,
    CandidateKeyUnfitError,
    KeyCounts,
    count_keys,
    keyed_permutation,
)


def _rows(n: int = 400) -> list[dict]:
  """K is the key; (P, Q, R) is 1:1 with it. P constant, Q low-card,
    R a wide account-like integer with a dense band and a sparse band."""
  rows = []
  for i in range(n):
    r = 1_000_000 + 1_000 * i if i % 4 else 50_000_000 + 100_000 * i
    rows.append({"K": f"k{i}", "P": "X", "Q": 100 + i % 7, "R": r})
  return rows


_TYPES = {"K": "STRING", "P": "STRING", "Q": "INT64", "R": "INT64"}


class TestCountKeys:

  def test_one_to_one_tuple(self):
    counts = count_keys(_rows(), ("K",), ("P", "Q", "R"))
    assert counts == KeyCounts(rows=400, pk_values=400, tuples=400, pairs=400)
    assert counts.one_to_one()

  def test_a_shared_tuple_is_not_one_to_one(self):
    counts = count_keys(_rows(), ("K",), ("P", "Q"))
    assert counts.tuples == 7
    assert not counts.one_to_one()

  def test_tolerance_absorbs_a_handful_of_dirty_rows(self):
    counts = KeyCounts(
        rows=100_000, pk_values=100_000, tuples=99_950, pairs=100_000)
    assert counts.one_to_one(tolerance=0.001)
    assert not counts.one_to_one(tolerance=0.0001)

  def test_null_is_a_value(self):
    rows = [{"K": 1, "A": None}, {"K": 2, "A": None}]
    assert count_keys(rows, ("K",), ("A",)).tuples == 1


class TestPermutation:

  @pytest.mark.parametrize("size", [1, 2, 7, 64, 1000, 4097])
  def test_is_a_bijection_on_the_domain(self, size):
    perm = keyed_permutation(size, key="run:T:R")
    assert sorted(perm(i) for i in range(size)) == list(range(size))

  def test_key_changes_the_order(self):
    a = keyed_permutation(1000, key="run-a")
    b = keyed_permutation(1000, key="run-b")
    assert [a(i) for i in range(50)] != [b(i) for i in range(50)]


def _encoder(num_rows: int = 20_000, rows=None, **kw) -> CandidateKeyEncoder:
  return CandidateKeyEncoder.build(
      cols=("P", "Q", "R"),
      rows=_rows() if rows is None else rows,
      column_types=_TYPES,
      num_rows=num_rows,
      key="run-1:p.land.TABLE_A",
      **kw)


class TestEncoder:

  def test_discriminator_is_the_widest_integer_support(self):
    assert _encoder().discriminator == "R"

  def test_injective_across_batches(self):
    enc = _encoder()
    values = [enc.value(o) for o in range(20_000)]
    assert len(set(values)) == 20_000

  def test_inside_the_observed_support_and_never_a_source_value(self):
    enc = _encoder()
    observed = {r["R"] for r in _rows()}
    lo, hi = min(observed), max(observed)
    values = [enc.value(o) for o in range(20_000)]
    assert all(lo < v < hi for v in values)
    assert not observed & set(values)
    assert not enc.widened

  def test_marginal_follows_the_sample_bands(self):
    # Three quarters of the sample sits in the dense 1.0M band, one
    # quarter in the sparse 5M band: the generated mass follows.
    enc = _encoder()
    values = [enc.value(o) for o in range(20_000)]
    dense = sum(1 for v in values if v < 2_000_000) / len(values)
    assert dense == pytest.approx(0.75, abs=0.03)

  def test_forbidden_source_values_are_skipped(self):
    observed = sorted({r["R"] for r in _rows()})
    extra = {observed[0] + 1, observed[0] + 2, str(observed[0] + 4)}
    enc = _encoder(num_rows=500, forbidden=extra)
    values = {enc.value(o) for o in range(500)}
    assert len(values) == 500
    assert not values & {observed[0] + 1, observed[0] + 2, observed[0] + 4}

  def test_apply_overwrites_only_the_discriminator(self):
    enc = _encoder()
    columns = {
        "P": ["X"] * 5,
        "Q": [101] * 5,
        "R": [1_000_003] * 5,
        "K": list("abcde")
    }
    enc.apply(columns, row_offset=10, n=5)
    assert columns["P"] == ["X"] * 5
    assert columns["Q"] == [101] * 5
    assert columns["R"] == [enc.value(o) for o in range(10, 15)]

  def test_same_key_reproduces_and_a_new_run_moves(self):
    a = _encoder()
    b = _encoder()
    c = CandidateKeyEncoder.build(
        cols=("P", "Q", "R"),
        rows=_rows(),
        column_types=_TYPES,
        num_rows=20_000,
        key="run-2:p.land.TABLE_A")
    assert [a.value(o) for o in range(100)] == [b.value(o) for o in range(100)]
    assert [a.value(o) for o in range(100)] != [c.value(o) for o in range(100)]

  def test_an_ordinal_past_the_capacity_is_loud(self):
    enc = _encoder(num_rows=100)
    with pytest.raises(ValueError, match="ordinal"):
      enc.value(100)

  def test_widens_within_the_digit_format_when_the_support_is_short(self):
    # R spans 1000..1099 (four digits): 100 integers minus 50 observed
    # cannot hold 2,000 rows, so the window widens to 1000..9999.
    rows = [{"P": "X", "Q": 1, "R": 1000 + 2 * i} for i in range(50)]
    enc = CandidateKeyEncoder.build(
        cols=("P", "R"),
        rows=rows,
        column_types={
            "P": "STRING",
            "R": "INT64"
        },
        num_rows=2_000,
        key="k")
    values = [enc.value(o) for o in range(2_000)]
    assert enc.widened
    assert len(set(values)) == 2_000
    assert all(1000 <= v <= 9999 for v in values)
    assert not {r["R"] for r in rows} & set(values)

  def test_capacity_short_even_widened_is_unfit(self):
    rows = [{"R": i} for i in range(0, 10, 2)]  # one digit: 0..9
    with pytest.raises(CandidateKeyUnfitError) as err:
      CandidateKeyEncoder.build(
          cols=("R",),
          rows=rows,
          column_types={"R": "INT64"},
          num_rows=100,
          key="k")
    assert err.value.reason == "capacity"
    assert err.value.fields["needed"] == 100

  def test_no_integral_column_is_unfit(self):
    rows = [{"P": "X", "S": f"s{i}"} for i in range(10)]
    with pytest.raises(CandidateKeyUnfitError) as err:
      CandidateKeyEncoder.build(
          cols=("P", "S"),
          rows=rows,
          column_types={
              "P": "STRING",
              "S": "STRING"
          },
          num_rows=10,
          key="k")
    assert err.value.reason == "no_integral_discriminator"

  def test_a_null_bearing_column_is_not_a_discriminator(self):
    rows = [{"R": None if i == 3 else 10 * i, "Q": i} for i in range(20)]
    enc = CandidateKeyEncoder.build(
        cols=("Q", "R"),
        rows=rows,
        column_types={
            "Q": "INT64",
            "R": "INT64"
        },
        num_rows=20,
        key="k",
    )
    assert enc.discriminator == "Q"

  def test_numeric_type_keeps_decimal_values(self):
    rows = [{"R": Decimal(1000 + 7 * i)} for i in range(30)]
    enc = CandidateKeyEncoder.build(
        cols=("R",),
        rows=rows,
        column_types={"R": "NUMERIC"},
        num_rows=100,
        key="k")
    value = enc.value(0)
    assert isinstance(value, Decimal)
    assert value == value.to_integral_value()
