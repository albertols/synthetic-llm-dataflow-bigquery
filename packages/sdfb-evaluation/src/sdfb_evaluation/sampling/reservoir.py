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
"""A deterministic, mergeable bottom-k sampler: `priority` assigns every
key a pseudo-random `uint64` rank from a salt, and `BottomK` keeps the `k`
smallest `(priority, key)` pairs seen so far.

This is coordinated/bottom-k sampling (Cohen, 1997; Cohen & Kaplan, 2007):
because `priority` is a pure function of `(salt, key)`, the same key always
gets the same rank everywhere it is computed — on any worker, in any run,
with the same salt — so two samples drawn this way agree on every key they
share (Task 24 relies on exactly this: sampling synthetic rows by a row-hash
key so a row that also exists in the source draws the same priority as its
source twin). `BottomK` is also mergeable: the true bottom-k of a union of
streams is always contained in the union of each stream's own bottom-k (an
item outside a stream's local bottom-k already has `k` smaller items from
that stream alone, so it cannot be in the global bottom-k either), which is
what makes `merge` exact, pure and independent of how work was partitioned.

Design: docs/designs/2026-07-07-evaluation-framework-design.md

References:
  Cohen, E. (1997), "Size-Estimation Framework with Applications to
    Transitive Closure and Reachability" (bottom-k / min-hash sampling).
  Cohen, E., Kaplan, H. (2007), "Summarizing Data using Bottom-k Sketches"
    (mergeability of bottom-k sketches).
"""

from __future__ import annotations

import heapq
from hashlib import blake2b
from typing import Any


def priority(salt: str, key: int) -> int:
  """`key`'s pseudo-random `uint64` rank under `salt`.

  `int.from_bytes(blake2b(f"{salt}:{key}".encode(), digest_size=8).digest(),
  "big")`: a pure function of `(salt, key)` alone, so it is reproducible
  across workers and runs, and different for a different `salt` or `key`
  with overwhelming probability (a 64-bit hash). `BottomK` uses this rank,
  never `key` itself or insertion order, to decide who stays.
  """
  digest = blake2b(f"{salt}:{key}".encode(), digest_size=8).digest()
  return int.from_bytes(digest, "big")


def _payload_rank(payload: Any) -> bytes:
  """A canonical, order-independent tie-break for two payloads under the
  identical key (see `BottomK._resolve_duplicate`).

  `blake2b` of `repr(payload)`: a pure function of the payload's own value,
  never of which `add`/`merge` call happened to see it first, so choosing
  the smaller rank is symmetric — `resolve(a, b) == resolve(b, a)` for any
  two payload values, which is what keeps duplicate-key resolution (and
  therefore `merge`) order-independent.
  """
  return blake2b(repr(payload).encode(), digest_size=8).digest()


class BottomK:
  """The `k` smallest `(priority, key)` pairs seen by `add`/`merge`, with
  their payloads, via a max-heap on `(priority, key)` bounded to size `k`
  at all times.

  Ties on `priority` are broken by `key` (both are plain integers, so the
  order is total and needs no randomness); a `key` added more than once —
  which, since `priority` is a pure function of `(salt, key)`, normally
  means the same priority reaching `add` twice with two different payloads
  — resolves via `_resolve_duplicate` rather than either occupying its own
  slot or letting arrival order decide.

  `k` must be non-negative; `k = 0` is a valid, permanently empty sampler
  (every `add` is then a no-op).
  """

  def __init__(self, k: int) -> None:
    if k < 0:
      raise ValueError(f"k must be non-negative, got {k}")
    self._k = k
    # A max-heap on (priority, key), simulated with heapq's min-heap by
    # storing (-priority, -key, payload): heap[0] is always the WORST kept
    # entry (the one evicted first when a smaller candidate arrives). Keys
    # are always distinct within this list (duplicates are resolved before
    # ever reaching heappush/heapreplace, see `add`), so no two entries
    # ever tie on their first two fields, and `payload` is never compared.
    self._heap: list[tuple[int, int, Any]] = []
    # key -> its current priority, for O(1) duplicate detection and O(1)
    # lookup of the priority half of "is this candidate better than what
    # this key already holds" (the payload half needs `_find_index` below).
    self._priorities: dict[int, int] = {}

  def __len__(self) -> int:
    return len(self._heap)

  def _find_index(self, key: int) -> int:
    """The position of `key`'s current entry in `self._heap`.

    O(k) — acceptable because it only runs on the rare duplicate-key path
    (`_resolve_duplicate`), never on the common "new distinct key" path,
    which stays O(log k) via `heapq.heappush`/`heapreplace`.
    """
    neg_key = -key
    for index, (_, entry_neg_key, _) in enumerate(self._heap):
      if entry_neg_key == neg_key:
        return index
    raise KeyError(key)  # unreachable: only called when key in self._priorities

  def _resolve_duplicate(self, prio: int, key: int, payload: Any) -> None:
    """Deterministically resolve a second `add` for an already-held `key`.

    Compares the full `(priority, key, payload_rank)` triple against the
    held entry's, and keeps the smaller one — the same "keep the smaller"
    rule `add` uses for distinct keys, just applied with `payload_rank`
    (`_payload_rank`) as the tie-break once `priority` and `key` are both
    equal (the expected case: the same key hashes to the same priority
    every time). This is symmetric in the two payloads, so which `add`
    call is "first" never changes the outcome.
    """
    existing_prio = self._priorities[key]
    index = self._find_index(key)
    if prio == existing_prio:
      _, _, existing_payload = self._heap[index]
      if _payload_rank(payload) < _payload_rank(existing_payload):
        self._heap[index] = (-prio, -key, payload)
      return
    if prio < existing_prio:
      self._heap[index] = (-prio, -key, payload)
      self._priorities[key] = prio
      # The ordering key changed, so the heap invariant may no longer
      # hold; a full re-heapify is O(k), fine for this rare path.
      heapq.heapify(self._heap)
    # else: the held entry already has the smaller priority; discard.

  def add(self, prio: int, key: int, payload: Any) -> None:
    """Offer `(prio, key, payload)`; it is kept iff it belongs among the
    `k` smallest `(priority, key)` pairs seen so far.
    """
    if self._k == 0:
      return
    if key in self._priorities:
      self._resolve_duplicate(prio, key, payload)
      return
    if len(self._heap) < self._k:
      heapq.heappush(self._heap, (-prio, -key, payload))
      self._priorities[key] = prio
      return
    worst_neg_prio, worst_neg_key, _ = self._heap[0]
    if (prio, key) < (-worst_neg_prio, -worst_neg_key):
      heapq.heapreplace(self._heap, (-prio, -key, payload))
      del self._priorities[-worst_neg_key]
      self._priorities[key] = prio
    # else: the candidate is no better than the current worst kept entry
    # and the sampler is already full; discard it.

  def merge(self, other: BottomK) -> BottomK:
    """A new `BottomK(k)` holding the `k` smallest `(priority, key)` pairs
    across `self` and `other` combined. Pure: neither operand is modified.

    Exact, associative and commutative: `self`'s and `other`'s own kept
    entries already dominate everything each of them discarded (see the
    module docstring), so replaying only those (at most `2k`) entries
    through a fresh `BottomK.add` — in either order, since `add` selects
    purely by value, never arrival order — reconstructs the true bottom-k
    of the full union.
    """
    merged = BottomK(self._k)
    for neg_prio, neg_key, payload in self._heap:
      merged.add(-neg_prio, -neg_key, payload)
    # `other` is a BottomK like `self`; reading its heap to replay entries
    # is this class's own merge logic, not an outside caller reaching into
    # private state.
    for neg_prio, neg_key, payload in other._heap:  # pylint: disable=protected-access
      merged.add(-neg_prio, -neg_key, payload)
    return merged

  def extract(self) -> list[Any]:
    """The kept payloads, ordered by `(priority, key)` ascending."""
    ordered = sorted(self._heap, key=lambda entry: (-entry[0], -entry[1]))
    return [payload for _, _, payload in ordered]
