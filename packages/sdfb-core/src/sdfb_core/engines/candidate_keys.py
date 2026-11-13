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
"""Driving-key candidate keys — a tuple 1:1 with the PK, by construction
(ADR 0046).

A grandchild driven by a NON-PK tuple of its parent fans out from that
tuple's DISTINCT values (ADR 0036 `FanoutDistinct`). When the tuple is
1:1 with the owning table's PK in the source, but each of its columns is
sampled on its own, the generated tuples collide and the grandchild is
driven by a fraction of the parent's keys (corp TABLE_A: 27% of b2's 10M
triples collided; b1's numeric filters collapsed them further).

The relationship model already says which tuples drive a grandchild; the
launcher measures whether each is 1:1 with the owner's PK on the source
and, when it is, hands the owner's engine one ``{"cols", "pk"}`` entry.
This module turns that entry into a generator that cannot collide:

* ONE column of the tuple — the **discriminator**, the integral column
  with the widest observed support — is set from the row's dense
  ordinal (``row_offset + position``, the same coordinate that keeps
  identity PKs unique across batches), so no two rows of the run share
  it and therefore no two share the tuple. Every other column keeps the
  engine's own draw, so a constant stays constant and a code column
  keeps its codes.
* The ordinal is first scrambled by a keyed permutation with
  cycle-walking (a bijection on ``[0, num_rows)``), then placed into the
  discriminator's integer support by a band allocation: the gaps between
  consecutive observed values get rows in proportion to the sample mass
  around them (the linear-interpolated empirical CDF), capped at the
  integers they hold. Observed values and the source domain are never
  emitted, so no source tuple is copied.
* A support too narrow for ``num_rows`` is widened to the same digit
  count; still too narrow, the entry is unfit and the table generates per
  column as before, loudly.

Pure Python; no Beam, no GCP, no NumPy.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0036, 0046).
"""

from __future__ import annotations

import hashlib
import logging
import math
from bisect import bisect_left, bisect_right
from itertools import pairwise
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sdfb_core.observability import log_milestone

__all__ = [
    "CANDIDATE_KEY_TOLERANCE",
    "CandidateKeyEncoder",
    "CandidateKeyUnfitError",
    "KeyCounts",
    "apply_candidate_keys",
    "bind_candidate_keys",
    "count_keys",
    "keyed_permutation",
    "pick_discriminator",
    "row_offset_of",
]

# Relative slack on the source's 1:1 measurement: a handful of dirty
# rows in a large source must not cost the table its candidate key (the
# generator makes the tuple exactly 1:1 either way), while a tuple that
# a real share of keys share stays per column. 0.1% of the key count.
CANDIDATE_KEY_TOLERANCE = 0.001

_INTEGRAL_TYPES = frozenset(
    {"INT64", "INTEGER", "NUMERIC", "BIGNUMERIC", "DECIMAL", "BIGDECIMAL"})
_DECIMAL_TYPES = frozenset({"NUMERIC", "BIGNUMERIC", "DECIMAL", "BIGDECIMAL"})
_MIX_ROUNDS = 3
# A discriminator with one value is a constant: it cannot tell rows apart.
_MIN_DISTINCT = 2


@dataclass(frozen=True)
class KeyCounts:
  """Distinct counts that decide whether ``cols`` is 1:1 with ``pk``:
    the tuple is a candidate key exactly when the PK values, the tuples
    and the (PK, tuple) pairs all count the same."""

  rows: int
  pk_values: int
  tuples: int
  pairs: int

  def one_to_one(self, tolerance: float = CANDIDATE_KEY_TOLERANCE) -> bool:
    if self.pk_values <= 0:
      return False
    slack = tolerance * self.pk_values
    return (abs(self.tuples - self.pk_values) <= slack and
            abs(self.pairs - self.pk_values) <= slack)


def count_keys(rows: Iterable[Mapping], pk: Sequence[str],
               cols: Sequence[str]) -> KeyCounts:
  """:class:`KeyCounts` over in-memory rows (the reference sample). A
    NULL member is a value, as BigQuery's GROUP BY treats it."""
  pks, tuples, pairs = set(), set(), set()
  n = 0
  for row in rows:
    n += 1
    k = tuple(row.get(c) for c in pk)
    t = tuple(row.get(c) for c in cols)
    pks.add(k)
    tuples.add(t)
    pairs.add((k, t))
  return KeyCounts(
      rows=n, pk_values=len(pks), tuples=len(tuples), pairs=len(pairs))


class CandidateKeyUnfitError(ValueError):
  """The tuple cannot be generated 1:1 with this sample and row count;
    ``reason`` is a stable token for the milestone, ``fields`` its
    numbers."""

  def __init__(self, reason: str, **fields: Any) -> None:
    detail = " ".join(f"{k}={v}" for k, v in fields.items())
    super().__init__(f"candidate key unfit: {reason} {detail}".strip())
    self.reason = reason
    self.fields = fields


def keyed_permutation(size: int, *, key: str) -> Callable[[int], int]:
  """A keyed bijection on ``[0, size)`` (ADR 0046): three rounds of
    multiply-add-xorshift on the smallest power-of-two domain covering
    ``size`` — each step is invertible mod ``2^bits``, the SplitMix64
    mixer's building blocks (Steele, Lea & Flood 2014) — cycle-walked
    back into the domain (Black & Rogaway 2002), so at most two steps
    per value on average. A statistical scrambler that hides the
    ordinal's order, not a cipher. Deterministic per key."""
  if size <= 0:
    raise ValueError("permutation domain must be non-empty")
  bits = max(1, (size - 1).bit_length())
  mask = (1 << bits) - 1
  shift = max(1, bits // 2)
  seed = hashlib.blake2b(key.encode(), digest_size=16 * _MIX_ROUNDS).digest()
  rounds = [(int.from_bytes(seed[16 * i:16 * i + 8], "big") | 1,
             int.from_bytes(seed[16 * i + 8:16 * i + 16], "big"))
            for i in range(_MIX_ROUNDS)]

  def _permute(x: int) -> int:
    while True:
      for mult, add in rounds:
        x = (x * mult + add) & mask
        x ^= x >> shift
      if x < size:
        return x

  return _permute


def _as_int(value: Any) -> int | None:
  """An integral reference value as ``int``; ``None`` for anything that
    is not one (NULL, a fraction, a non-number)."""
  if value is None or isinstance(value, bool):
    return None
  if isinstance(value, int):
    return value
  if isinstance(value, Decimal):
    return int(value) if value == value.to_integral_value() else None
  if isinstance(value, float):
    return int(value) if math.isfinite(value) and value.is_integer() else None
  if isinstance(value, str):
    text = value.strip()
    body = text[1:] if text[:1] in "+-" else text
    return int(text) if body.isdigit() else None
  return None


def _integral_support(rows: Sequence[Mapping], col: str) -> list[int] | None:
  values = []
  for row in rows:
    v = _as_int(row.get(col))
    if v is None:
      return None
    values.append(v)
  return values if len(set(values)) >= _MIN_DISTINCT else None


def _digit_window(lo: int, hi: int) -> tuple[int, int]:
  """The widest integer window with the digit counts of the observed
    extremes — the "within format" widening."""

  def digits(v: int) -> int:
    return len(str(abs(v)))

  def smallest(v: int) -> int:  # the smallest magnitude with v's digits
    return 10**(digits(v) - 1) if digits(v) > 1 else 0

  low = -(10**digits(lo) - 1) if lo < 0 else smallest(lo)
  high = -max(1, smallest(hi)) if hi < 0 else 10**digits(hi) - 1
  return low, high


def _apportion(total: int, weights: Sequence[float],
               caps: Sequence[int]) -> list[int] | None:
  """``total`` slots over bands, proportional to ``weights`` and capped
    at ``caps`` (water-filling; largest remainder for the last split).
    Mass the weighted bands cannot hold goes to zero-weight capacity
    (the widened tails) by residual capacity. ``None`` when every band
    together holds fewer than ``total``."""
  alloc = [0] * len(caps)
  remaining = total
  active = [i for i, w in enumerate(weights) if w > 0 and caps[i] > 0]
  while remaining > 0 and active:
    mass = sum(weights[i] for i in active)
    full = [i for i in active if caps[i] <= remaining * weights[i] / mass]
    if not full:
      _split(remaining, active, [weights[i] for i in active], caps, alloc)
      remaining = 0
      break
    for i in full:
      alloc[i] = caps[i]
      remaining -= caps[i]
    active = [i for i in active if i not in set(full)]
  if remaining > 0:
    spare = [i for i, c in enumerate(caps) if c - alloc[i] > 0]
    room = sum(caps[i] - alloc[i] for i in spare)
    if room < remaining:
      return None
    _split(remaining, spare, [float(caps[i] - alloc[i]) for i in spare], caps,
           alloc)
  return alloc


def _split(amount: int, idx: list[int], weights: list[float],
           caps: Sequence[int], alloc: list[int]) -> None:
  mass = sum(weights)
  shares = [amount * w / mass for w in weights]
  base = [
      min(int(s), caps[i] - alloc[i]) for s, i in zip(shares, idx, strict=True)
  ]
  left = amount - sum(base)
  order = sorted(range(len(idx)), key=lambda j: (base[j] - shares[j], j))
  for j in order:
    if left <= 0:
      break
    if base[j] < caps[idx[j]] - alloc[idx[j]]:
      base[j] += 1
      left -= 1
  for j, i in enumerate(idx):
    alloc[i] += base[j]


def pick_discriminator(cols: Sequence[str], rows: Sequence[Mapping],
                       column_types: Mapping[str, str]) -> str | None:
  """The column of ``cols`` the encoder sets: integral by type and in
    every reference row, never NULL, at least two values — the widest
    observed support wins (ties by name). ``None`` when none qualifies."""
  best: tuple[int, str] | None = None
  for col in cols:
    if str(column_types.get(col, "")).upper() not in _INTEGRAL_TYPES:
      continue
    values = _integral_support(rows, col)
    if values is None:
      continue
    candidate = (max(values) - min(values), col)
    if best is None or candidate > best:
      best = candidate
  return None if best is None else best[1]


@dataclass(frozen=True)
class CandidateKeyEncoder:
  """The discriminator of one candidate key as a function of the row
    ordinal: injective on ``[0, capacity)``, inside the (possibly
    widened) support, never a forbidden value."""

  cols: tuple[str, ...]
  discriminator: str
  capacity: int
  widened: bool
  decimal: bool
  _low: int
  _gaps: tuple[int, ...]  # F[i] - low - i, for select()
  _starts: tuple[int, ...]  # prefix sums of the band allocation
  _alloc: tuple[int, ...]
  _rank0: tuple[int, ...]
  _cap: tuple[int, ...]
  _permute: Callable[[int], int] = field(repr=False, compare=False)

  @classmethod
  def build(
      cls,
      *,
      cols: Sequence[str],
      rows: Sequence[Mapping],
      column_types: Mapping[str, str],
      num_rows: int,
      key: str,
      forbidden: Iterable[Any] = (),
  ) -> CandidateKeyEncoder:
    """Pick the discriminator and lay ``num_rows`` slots over its
        support. Raises :class:`CandidateKeyUnfitError` when the sample has
        no integral, NULL-free column in ``cols`` or the support cannot
        hold ``num_rows`` even widened to its digit format."""
    if num_rows <= 0:
      raise CandidateKeyUnfitError("num_rows_unknown", num_rows=num_rows)
    rows = list(rows)
    col = pick_discriminator(cols, rows, column_types)
    if col is None:
      raise CandidateKeyUnfitError(
          "no_integral_discriminator", cols=",".join(cols))
    values = _integral_support(rows, col) or []
    counts: dict[int, int] = {}
    for v in values:
      counts[v] = counts.get(v, 0) + 1
    points = sorted(counts)
    extra = {i for i in (_as_int(v) for v in forbidden) if i is not None}
    for widened in (False, True):
      low, high = ((points[0], points[-1]) if not widened else _digit_window(
          points[0], points[-1]))
      banned = sorted({*points, *(v for v in extra if low <= v <= high)})
      plan = cls._plan(points, counts, banned, low, high, num_rows)
      if plan is not None:
        break
    else:
      free = (high - low + 1) - len(banned)
      raise CandidateKeyUnfitError(
          "capacity",
          column=col,
          needed=num_rows,
          available=free,
          window=f"{low}..{high}")
    alloc, rank0, cap = plan
    starts, total = [], 0
    for a in alloc:
      starts.append(total)
      total += a
    return cls(
        cols=tuple(cols),
        discriminator=col,
        capacity=num_rows,
        widened=widened,
        decimal=str(column_types.get(col, "")).upper() in _DECIMAL_TYPES,
        _low=low,
        _gaps=tuple(f - low - i for i, f in enumerate(banned)),
        _starts=tuple(starts),
        _alloc=tuple(alloc),
        _rank0=tuple(rank0),
        _cap=tuple(cap),
        _permute=keyed_permutation(num_rows, key=f"{key}:{col}"),
    )

  @staticmethod
  def _plan(points: list[int], counts: Mapping[int, int], banned: list[int],
            low: int, high: int, num_rows: int):
    """Bands (the gaps between observed points, plus the two tails of
        the window), their free-integer ranks and the slot allocation —
        or ``None`` when they cannot hold ``num_rows``."""
    total = sum(counts.values())

    def free_before(z: int) -> int:
      return (z - low) - bisect_left(banned, z)

    bands: list[tuple[int, int, float]] = [(low, points[0], 0.0)]
    for a, b in pairwise(points):
      bands.append((a + 1, b, (counts[a] + counts[b]) / (2 * total)))
    bands.append((points[-1] + 1, high + 1, 0.0))
    rank0 = [free_before(a) for a, _, _ in bands]
    cap = [max(0, free_before(b) - free_before(a)) for a, b, _ in bands]
    alloc = _apportion(num_rows, [w for _, _, w in bands], cap)
    if alloc is None:
      return None
    return alloc, rank0, cap

  def _select(self, rank: int) -> int:
    """The ``rank``-th (0-based) integer of the window not banned."""
    return self._low + rank + bisect_right(self._gaps, rank)

  def value(self, ordinal: int) -> int | Decimal:
    """The discriminator for the row at ``ordinal`` (``0 <= ordinal <
        capacity``) — distinct ordinals, distinct values."""
    if not 0 <= ordinal < self.capacity:
      raise ValueError(f"candidate key ordinal {ordinal} outside "
                       f"[0, {self.capacity}) on {self.discriminator} — the "
                       f"table generated more rows than it planned (ADR 0046)")
    slot = self._permute(ordinal)
    # The LAST band starting at or before the slot: an empty band
    # shares its start with the next one, so it is never the answer.
    band = bisect_right(self._starts, slot) - 1
    offset = slot - self._starts[band]
    rank = self._rank0[band] + offset * self._cap[band] // self._alloc[band]
    v = self._select(rank)
    return Decimal(v) if self.decimal else v

  def apply(self, columns: dict[str, list], row_offset: int, n: int) -> None:
    """Overwrite the discriminator for rows ``[row_offset, row_offset +
        n)`` in an engine's column dict; every other column is left as
        the engine drew it."""
    columns[self.discriminator] = [self.value(row_offset + i) for i in range(n)]


def row_offset_of(cfg: Any) -> int | None:
  """The batch's first row ordinal, carried by a root's request
    (``engine_specific["row_offset"]``); ``None`` when absent."""
  offset = (getattr(cfg, "engine_specific", None) or {}).get("row_offset")
  return None if offset is None else int(offset)


def apply_candidate_keys(encoders: Sequence[CandidateKeyEncoder],
                         columns: dict[str, list], cfg: Any,
                         n: int) -> frozenset[str]:
  """Both engines' batch hook: overwrite each encoder's discriminator for
    this batch's ``n`` rows and return the columns it set. A request with
    no ``row_offset`` (one that did not come from a root's batch plan)
    keeps the per-column draw."""
  offset = row_offset_of(cfg) if encoders else None
  if offset is None:
    return frozenset()
  for encoder in encoders:
    encoder.apply(columns, offset, n)
  return frozenset(encoder.discriminator for encoder in encoders)


def _source_domain(ctx: Any, column: str) -> frozenset[str]:
  """The discriminator's full source domain through the ADR 0023 store,
    when one is attached — the same rejection set both engines see."""
  store = getattr(ctx, "source_value_store", None)
  if store is None:
    return frozenset()
  try:
    values = store.fetch_distinct(column)
  except Exception as exc:  # pylint: disable=broad-exception-caught
    log_milestone(
        "candidate_key_source_filter_error",
        level=logging.WARNING,
        column=column,
        error=type(exc).__name__)
    return frozenset()
  if values is None:
    log_milestone(
        "candidate_key_source_filter_absent",
        level=logging.WARNING,
        column=column)
    return frozenset()
  return frozenset(values)


def bind_candidate_keys(ctx: Any) -> list[CandidateKeyEncoder]:
  """Every candidate key the launcher adopted for this table, as an
    encoder. Both engines call it in ``setup()`` so the mechanism has ONE
    implementation; an entry that does not fit on the worker is logged
    and dropped (the table then generates that tuple per column)."""
  entries = list(getattr(ctx, "candidate_keys", None) or ())
  if not entries:
    return []
  table = getattr(ctx, "landing_table", "") or ctx.table_schema.fqn
  if getattr(ctx, "fanout", None):
    log_milestone(
        "candidate_key_unbound",
        level=logging.WARNING,
        table=table,
        reason="driven_owner",
        note="a driven table has no dense row ordinal (ADR 0046)")
    return []
  types = {c.name: c.bq_type for c in ctx.table_schema.columns}
  encoders = []
  for entry in entries:
    cols = tuple(entry.get("cols") or ())
    column = pick_discriminator(cols, ctx.reference_rows, types)
    domain = _source_domain(ctx, column) if column else frozenset()
    try:
      encoder = CandidateKeyEncoder.build(
          cols=cols,
          rows=ctx.reference_rows,
          column_types=types,
          num_rows=int(getattr(ctx, "num_rows", 0) or 0),
          key=f"{ctx.pipeline_run_id}:{table}",
          forbidden=domain,
      )
    except CandidateKeyUnfitError as exc:
      log_milestone(
          "candidate_key_unbound",
          level=logging.WARNING,
          table=table,
          cols=",".join(cols),
          reason=exc.reason,
          **exc.fields)
      continue
    log_milestone(
        "candidate_key_bound",
        table=table,
        cols=",".join(cols),
        pk=",".join(entry.get("pk") or ()),
        discriminator=encoder.discriminator,
        capacity=encoder.capacity,
        widened=encoder.widened)
    encoders.append(encoder)
  return encoders
