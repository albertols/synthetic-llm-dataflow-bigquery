"""Parent-driven fan-out (design 2026-09-10, ADR 0036).

A child table generated from its parent's landed keys: per key, how many
children (the SOURCE fan-out histogram, zero bucket included), which
PK-completing cells (drawn WITHOUT replacement from the source's joint
cell distribution), and which columns are inherited (copied from the
key tuple). This is the part both engines share; everything that is not
a key, a cell or an inherited column is the engine's own sampling.

Why without replacement: a PK that contains an FK is a per-parent key.
Drawing its remaining members at random inside a parent collides as
balls into bins (`1 - C/k (1 - e^(-k/C))`, the 2026-09-09 runs); the
source PK guarantees k <= C, so drawing k distinct cells reproduces it.

Pure Python; no Beam, no GCP.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import accumulate

from sdfb_core.seeding import derive_key_seed

__all__ = [
    "CellTable",
    "ConditionalEdge",
    "FanoutHistogram",
    "FanoutPlan",
    "conditional_values",
    "expand_keys",
]

# Above this fraction of the table, sampling without replacement switches
# from rejection (expected O(k) draws) to a weighted permutation
# (Efraimidis & Spirakis 2006, O(C log k)).
_REJECTION_MAX_FILL = 0.5


@dataclass(frozen=True)
class FanoutHistogram:
    """``k -> number of parent tuples with k children`` in the source."""

    parents_by_k: dict[int, int]
    _ks: tuple[int, ...] = field(init=False, repr=False)
    _cum: tuple[float, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.parents_by_k or sum(self.parents_by_k.values()) <= 0:
            raise ValueError("fan-out histogram is empty")
        ks = tuple(sorted(int(k) for k in self.parents_by_k))
        total = float(sum(self.parents_by_k.values()))
        cum = tuple(accumulate(self.parents_by_k[k] / total for k in ks))
        object.__setattr__(self, "_ks", ks)
        object.__setattr__(self, "_cum", cum)

    @property
    def mean(self) -> float:
        total = sum(self.parents_by_k.values())
        return sum(k * n for k, n in self.parents_by_k.items()) / total

    @property
    def max_k(self) -> int:
        return max(self.parents_by_k)

    @property
    def zero_share(self) -> float:
        return self.parents_by_k.get(0, 0) / sum(self.parents_by_k.values())

    def sample(self, rng: random.Random) -> int:
        i = min(bisect_right(self._cum, rng.random()), len(self._ks) - 1)
        return self._ks[i]

    def to_payload(self) -> dict[str, int]:
        return {str(k): int(n) for k, n in sorted(self.parents_by_k.items())}

    @classmethod
    def from_payload(cls, payload: dict) -> FanoutHistogram:
        return cls({int(k): int(n) for k, n in payload.items()})


@dataclass(frozen=True)
class CellTable:
    """The joint values of the PK-completing categorical members, with
    their source row counts as weights."""

    cols: tuple[str, ...]
    rows: list[tuple]
    counts: list[float]
    _cum: tuple[float, ...] = field(init=False, repr=False)
    _total: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.rows) != len(self.counts) or not self.rows:
            raise ValueError("cell table needs one positive count per row")
        if not all(c > 0 for c in self.counts):
            raise ValueError("cell table needs one positive count per row")
        # Cumulative counts ONCE, exactly as `FanoutHistogram` does.
        # `random.choices(..., weights=...)` rebuilds them on every call, so
        # a k-draw cost O(k*C) and not the O(k) ADR 0036 D3 claims — 746 us
        # per key at C = 10,000, ~20 minutes of pure draw on a 100M-key
        # parent.
        cum = tuple(accumulate(float(c) for c in self.counts))
        object.__setattr__(self, "_cum", cum)
        object.__setattr__(self, "_total", cum[-1])

    @property
    def size(self) -> int:
        return len(self.rows)

    def _weighted_index(self, rng: random.Random) -> int:
        """One weighted index in O(log C) — a bisect over `_cum`."""
        return min(bisect_right(self._cum, rng.random() * self._total), self.size - 1)

    def draw(self, k: int, rng: random.Random, *, exact: bool) -> list[tuple]:
        """``k`` cells. ``exact`` = without replacement (raises when
        ``k`` exceeds the table); otherwise weighted with replacement."""
        if k <= 0:
            return []
        if not exact:
            return [self.rows[self._weighted_index(rng)] for _ in range(k)]
        if k > self.size:
            raise ValueError(
                f"{k} children requested from {self.size} cells over "
                f"{list(self.cols)} — the declared PK is not a key"
            )
        if k <= self.size * _REJECTION_MAX_FILL:
            chosen: dict[int, None] = {}
            while len(chosen) < k:
                chosen.setdefault(self._weighted_index(rng), None)
            return [self.rows[i] for i in chosen]
        # Weighted permutation via full sort: key = u^(1/w), take the k largest.
        # O(C log C) full sort (not k-based selection).
        keyed = sorted(
            range(self.size),
            key=lambda i: -(rng.random() ** (1.0 / self.counts[i])),
        )
        return [self.rows[i] for i in keyed[:k]]

    def to_payload(self) -> dict:
        return {
            "cols": list(self.cols),
            "rows": [list(r) for r in self.rows],
            "counts": [float(c) for c in self.counts],
        }

    @classmethod
    def from_payload(cls, payload: dict) -> CellTable:
        return cls(
            cols=tuple(payload["cols"]),
            rows=[tuple(r) for r in payload["rows"]],
            counts=[float(c) for c in payload["counts"]],
        )


@dataclass(frozen=True)
class ConditionalEdge:
    """A non-driving FK edge whose candidates come from a co-parent
    joined on the driving key's overlap columns (design 2026-09-11 §4,
    ADR 0037) — e.g. a child PK's remaining member is populated from a
    second parent that shares part of the driving key."""

    id: str
    cols: tuple[str, ...]
    nullable: bool

    def to_payload(self) -> dict:
        return {"id": self.id, "cols": list(self.cols), "nullable": bool(self.nullable)}

    @classmethod
    def from_payload(cls, payload: dict) -> ConditionalEdge:
        return cls(
            id=str(payload["id"]),
            cols=tuple(payload["cols"]),
            nullable=bool(payload["nullable"]),
        )


@dataclass(frozen=True)
class FanoutPlan:
    """One driven child's relational recipe."""

    driving_cols: tuple[str, ...]
    histogram: FanoutHistogram
    cells: CellTable | None
    # True when EVERY PK member outside the driving edge is a cell column
    # — then the cells alone must key the child and are drawn without
    # replacement. False when an unbounded member (pattern, numeric,
    # temporal) completes the PK and cells only need to follow weights.
    exact_cells: bool
    # Non-driving FK edges resolved per key via `conditional_values`
    # (design 2026-09-11 §4, ADR 0037). Added at the end so positional
    # construction in existing tests/payloads keeps working.
    conditional: tuple[ConditionalEdge, ...] = ()

    @property
    def columns(self) -> frozenset[str]:
        cols = set(self.driving_cols)
        if self.cells is not None:
            cols.update(self.cells.cols)
        for edge in self.conditional:
            cols.update(edge.cols)
        return frozenset(cols)

    def to_payload(self) -> dict:
        return {
            "driving_cols": list(self.driving_cols),
            "histogram": self.histogram.to_payload(),
            "cells": self.cells.to_payload() if self.cells is not None else None,
            "exact_cells": bool(self.exact_cells),
            "conditional": [edge.to_payload() for edge in self.conditional],
        }

    @classmethod
    def from_payload(cls, payload: dict) -> FanoutPlan:
        cells = payload.get("cells")
        conditional = payload.get("conditional") or ()
        return cls(
            driving_cols=tuple(payload["driving_cols"]),
            histogram=FanoutHistogram.from_payload(payload["histogram"]),
            cells=CellTable.from_payload(cells) if cells else None,
            exact_cells=bool(payload.get("exact_cells", False)),
            conditional=tuple(ConditionalEdge.from_payload(e) for e in conditional),
        )


def conditional_values(
    run_id: str,
    key: tuple,
    edge_id: str,
    candidates: Sequence[Sequence],
    k: int,
) -> list[tuple]:
    """The ``rest(X)`` values for ``k`` children of ``key`` on conditional
    edge ``edge_id`` (design 2026-09-11 §4): a seeded shuffle of
    ``candidates``, without replacement until the fan-out exceeds the
    candidate count, then wrapping in the same shuffled order. No
    candidates (an unmatched key) yields ``k`` empty tuples — the NULL
    policy is the caller's (``GenerateRecordsDoFn`` / the engine)."""
    if k <= 0:
        return []
    if not candidates:
        return [()] * k
    order = [tuple(c) for c in candidates]
    random.Random(derive_key_seed(run_id, tuple(key), salt=edge_id)).shuffle(order)
    return [order[i % len(order)] for i in range(k)]


def expand_keys(
    plan: FanoutPlan,
    keys: Sequence[tuple],
    run_id: str,
    chunk_rows: int,
) -> Iterator[list[tuple[tuple, tuple]]]:
    """``(key, cell)`` pairs for every child of every key, in chunks of at
    most ``chunk_rows`` — a hot parent never makes an oversized bundle,
    and a key's cells stay unique across the split because they are
    drawn once per key."""
    chunk_rows = max(1, int(chunk_rows))
    chunk: list[tuple[tuple, tuple]] = []
    for key in keys:
        rng = random.Random(derive_key_seed(run_id, tuple(key)))
        k = plan.histogram.sample(rng)
        if k <= 0:
            continue
        cells: list[tuple] = (
            plan.cells.draw(k, rng, exact=plan.exact_cells)
            if plan.cells is not None
            else [()] * k
        )
        for cell in cells:
            chunk.append((tuple(key), tuple(cell)))
            if len(chunk) >= chunk_rows:
                yield chunk
                chunk = []
    if chunk:
        yield chunk
