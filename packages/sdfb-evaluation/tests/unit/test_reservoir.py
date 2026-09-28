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
"""Tests for `sdfb_evaluation.sampling.reservoir` (Task 15).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import itertools
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sdfb_evaluation.sampling.reservoir import BottomK
from sdfb_evaluation.sampling.reservoir import priority

# ---------------------------------------------------------------------------
# priority()
# ---------------------------------------------------------------------------


def test_priority_is_deterministic_uint64():
  a = priority("salt-a", 42)
  b = priority("salt-a", 42)
  assert a == b
  assert 0 <= a < 2**64


def test_priority_different_salt_gives_different_value():
  a = priority("salt-a", 42)
  b = priority("salt-b", 42)
  assert a != b


def test_priority_different_key_gives_different_value():
  a = priority("salt", 1)
  b = priority("salt", 2)
  assert a != b


# ---------------------------------------------------------------------------
# BottomK — basic behaviour
# ---------------------------------------------------------------------------


def test_bottomk_keeps_the_k_smallest_priorities():
  bk = BottomK(3)
  for key in range(10):
    bk.add(priority("s", key), key, payload=f"row-{key}")
  extracted = bk.extract()
  assert len(extracted) == 3
  # The 3 keys kept must be exactly the 3 with the smallest priority.
  ranked = sorted(range(10), key=lambda key: (priority("s", key), key))
  expected = [f"row-{key}" for key in ranked[:3]]
  assert extracted == expected


def test_bottomk_never_exceeds_k_after_any_add():
  bk = BottomK(5)
  for key in range(200):
    bk.add(priority("s", key), key, payload=key)
    assert len(bk.extract()) <= 5


def test_bottomk_extract_sorted_by_priority_then_key():
  bk = BottomK(4)
  for key in range(50):
    bk.add(priority("sort-check", key), key, payload=key)
  extracted_keys = bk.extract()
  priorities = [priority("sort-check", key) for key in extracted_keys]
  assert priorities == sorted(priorities)


def test_bottomk_k_zero_always_empty():
  bk = BottomK(0)
  for key in range(20):
    bk.add(priority("s", key), key, payload=key)
  assert bk.extract() == []


def test_bottomk_rejects_negative_k():
  with pytest.raises(ValueError):
    BottomK(-1)


# ---------------------------------------------------------------------------
# merge — purity, associativity, commutativity
# ---------------------------------------------------------------------------


def test_merge_is_pure():
  a, b = BottomK(3), BottomK(3)
  for key in range(5):
    a.add(priority("s", key), key, key)
  for key in range(5, 10):
    b.add(priority("s", key), key, key)
  a_before, b_before = a.extract(), b.extract()
  merged = a.merge(b)
  assert a.extract() == a_before
  assert b.extract() == b_before
  assert merged.extract() != []


def test_merge_matches_single_pass_bottomk():
  keys = list(range(30))
  bk_a, bk_b, bk_all = BottomK(6), BottomK(6), BottomK(6)
  for key in keys[:15]:
    p = priority("merge-check", key)
    bk_a.add(p, key, key)
    bk_all.add(p, key, key)
  for key in keys[15:]:
    p = priority("merge-check", key)
    bk_b.add(p, key, key)
    bk_all.add(p, key, key)
  merged = bk_a.merge(bk_b)
  assert merged.extract() == bk_all.extract()


def test_merge_is_commutative():
  a, b = BottomK(4), BottomK(4)
  for key in range(20):
    p = priority("commute", key)
    (a if key % 2 == 0 else b).add(p, key, key)
  assert a.merge(b).extract() == b.merge(a).extract()


# ---------------------------------------------------------------------------
# Hypothesis: order/tree independence, and the k-bound
# ---------------------------------------------------------------------------


@settings(max_examples=40)
@given(
    st.lists(
        st.integers(min_value=0, max_value=999),
        min_size=0,
        max_size=200,
        unique=True),
    st.integers(min_value=0, max_value=25),
)
def test_bottomk_result_independent_of_insertion_order(keys, k):
  salt = "order-independence"
  priorities = [priority(salt, key) for key in keys]

  first = BottomK(k)
  for key, prio in zip(keys, priorities, strict=True):
    first.add(prio, key, key)

  shuffled = list(zip(keys, priorities, strict=True))
  random.Random(1234).shuffle(shuffled)
  second = BottomK(k)
  for key, prio in shuffled:
    second.add(prio, key, key)

  assert first.extract() == second.extract()
  assert len(first.extract()) <= k


@settings(max_examples=30)
@given(
    st.lists(
        st.integers(min_value=0, max_value=999),
        min_size=0,
        max_size=90,
        unique=True),
    st.integers(min_value=0, max_value=15),
)
def test_bottomk_result_independent_of_merge_tree_shape(keys, k):
  salt = "merge-tree"
  entries = [(priority(salt, key), key, key) for key in keys]

  def build(items) -> BottomK:
    bk = BottomK(k)
    for prio, key, payload in items:
      bk.add(prio, key, payload)
    return bk

  # Tree A: one flat pass.
  flat = build(entries)

  # Tree B: split into thirds, build each, merge pairwise in a different
  # associativity than a naive left-fold.
  third = max(1, len(entries) // 3 or 1)
  chunks = [entries[i:i + third] for i in range(0, len(entries), third)] or [[]]
  parts = [build(chunk) for chunk in chunks]
  # Fold right-to-left instead of left-to-right, and pair up first.
  while len(parts) > 1:
    paired = []
    it = iter(parts)
    for left, right in itertools.zip_longest(it, it):
      paired.append(left.merge(right) if right is not None else left)
    parts = paired
  tree = parts[0]

  assert flat.extract() == tree.extract()
  assert len(flat.extract()) <= k


# ---------------------------------------------------------------------------
# Uniformity
# ---------------------------------------------------------------------------


def test_bottomk_uniform_across_key_id_deciles():
  n_keys, k, deciles = 20_000, 1000, 10
  bk = BottomK(k)
  for key in range(n_keys):
    bk.add(priority("uniformity", key), key, key)
  kept_keys = bk.extract()
  assert len(kept_keys) == k

  bucket_size = n_keys // deciles
  counts = [0] * deciles
  for key in kept_keys:
    counts[min(key // bucket_size, deciles - 1)] += 1

  expected = k // deciles  # 100
  for count in counts:
    assert abs(count - expected) <= 35, counts


# ---------------------------------------------------------------------------
# Duplicate keys
# ---------------------------------------------------------------------------


def test_duplicate_key_keeps_one_payload_deterministically():
  salt = "dup"
  key = 7
  p = priority(salt, key)

  order_a = BottomK(3)
  order_a.add(p, key, "payload-A")
  order_a.add(p, key, "payload-B")

  order_b = BottomK(3)
  order_b.add(p, key, "payload-B")
  order_b.add(p, key, "payload-A")

  assert order_a.extract() == order_b.extract()
  assert len(order_a.extract()) == 1


def test_duplicate_key_does_not_consume_extra_capacity():
  salt = "dup-capacity"
  bk = BottomK(2)
  p = priority(salt, 1)
  bk.add(p, 1, "first")
  bk.add(p, 1, "second")
  bk.add(priority(salt, 2), 2, "other")
  assert len(bk.extract()) == 2
