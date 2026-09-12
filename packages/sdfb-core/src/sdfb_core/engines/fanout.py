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
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import accumulate

from sdfb_core.seeding import derive_key_seed

__all__ = [
    "CellTable",
    "ConditionalEdge",
    "FanoutHistogram",
    "FanoutPlan",
    "KeyDraw",
    "conditional_edge_id",
    "expand_keys",
    "joint_key_draw",
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
        return [self.rows[i] for i in self.permutation(rng)[:k]]

    def permutation(self, rng: random.Random) -> list[int]:
        """Every row index once, in weighted random order (Efraimidis &
        Spirakis 2006: sort on ``u^(1/w)``).

        A prefix of it IS a draw without replacement, which is why the
        exact draw's high-fill branch takes the first ``k``. The joint
        per-key walk (`joint_key_draw`) indexes into the WHOLE thing:
        a child's cell is then a pure function of its combination index,
        so two children of one key can never collide on it, and the
        weights still decide which cells come first.
        """
        return sorted(
            range(self.size),
            key=lambda i: -(rng.random() ** (1.0 / self.counts[i])),
        )

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


def conditional_edge_id(cols: Sequence[str], ref: str) -> str:
    """The ONE name a conditional edge answers to — ``(cols)->ref``, the
    label the launcher's milestones already print.

    The composer (`FkEdgeSpec.edge_id`), the plan (`ConditionalEdge.id`),
    the request payload's ``matches`` and preflight's ``conditional_rest``
    must all spell it the same way or an edge's candidates are looked up
    under a key nothing wrote. It carries the PARENT because the child
    columns alone do not identify an edge: two conditional edges from the
    same columns to DIFFERENT parents are declarable (the parse-time
    duplicate check only rejects an identical ``(cols, ref, ref_cols)``),
    and under a columns-only id they collided — the second join
    overwrote the first in ``matches``, so one edge's candidates
    answered for both (ADR 0037 review, ruling 14)."""
    return f"({','.join(cols)})->{ref}"


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


@dataclass(frozen=True)
class KeyDraw:
    """One parent key's children, decided JOINTLY (ADR 0037 final review,
    fix wave A1): the cell each child carries and, per conditional edge,
    the candidate tuple it carries — index-aligned, one entry per child.

    Why jointly. The cell sequence and each edge's candidate sequence
    used to be independent cyclic walks (``i % len`` each), so the
    realised number of distinct ``(cell, cand_1, …, cand_m)``
    combinations per key was ``lcm(n_cells, c_1, …, c_m)``, NOT their
    product — 2 cells and 2 candidates gave 2 combinations for 4
    children, i.e. PK duplicates on a shape preflight had just declared
    safe. And the cells were drawn by ``CellTable.draw(k, exact=True)``,
    which RAISED as soon as the fan-out exceeded the cell table, killing
    a whole key batch instead of using the candidates that make those
    children representable.

    ``capacity`` is the real per-key ceiling (the product); ``requested``
    is the fan-out the histogram drew. ``shortfall`` is what a capped key
    could not emit — the DoFn-visible number behind `fanout_rows_capped`.
    """

    cells: tuple[tuple, ...]
    values: dict[str, list[tuple]]
    requested: int
    capacity: int

    @property
    def n_children(self) -> int:
        return len(self.cells)

    @property
    def shortfall(self) -> int:
        return max(0, self.requested - len(self.cells))


def _mixed_radix(index: int, radices: Sequence[int]) -> list[int]:
    """``index`` decomposed over ``radices``, FIRST radix varying fastest.

    Injective on ``[0, prod(radices))``, which is what makes every
    child's combination distinct. The cells come first deliberately: for
    the ADR 0036 regime (``k <= n_cells``) each child then takes a
    different cell, exactly as `CellTable.draw(exact=True)` did, and the
    candidate digits only start advancing once the cells are exhausted —
    the regime where the old code raised.
    """
    digits: list[int] = []
    rest = index
    for radix in radices:
        digits.append(rest % radix)
        rest //= radix
    return digits


def joint_key_draw(
    plan: FanoutPlan,
    key: tuple,
    run_id: str,
    candidates: Sequence[Sequence[Sequence]],
) -> KeyDraw:
    """One key's children over the CROSS PRODUCT of its cells and its
    conditional edges' candidates (design 2026-09-11 §4, ADR 0037).

    ``candidates[j]`` is edge ``plan.conditional[j]``'s candidate list
    for this key (empty = no candidates; the NULL policy is the caller's).
    Child ``i`` takes its combination from `_mixed_radix` over
    ``(n_cells, c_1, …, c_m)``, indexing a per-key seeded PERMUTATION of
    the cell rows (weighted, `CellTable.permutation`) and of each
    candidate list — the same per-key / per-edge seeds the rest of the
    fan-out layer uses, so a re-run reproduces the children exactly.

    The fan-out is CAPPED at the capacity only when the combination must
    key the child: ``exact_cells`` (every PK member outside the driving
    edge is a cell or an edge-supplied column) AND every edge actually
    has candidates. An inexact PK is completed by an unbounded member, so
    capping there would drop rows the PK can represent; an edge with no
    candidates NULL-fills, and a NULL is not a key member (ADR 0031), so
    it must not shrink a key's fan-out either. Both wrap instead.
    """
    key_t = tuple(key)
    rng = random.Random(derive_key_seed(run_id, key_t))
    k = plan.histogram.sample(rng)
    values: dict[str, list[tuple]] = {edge.id: [] for edge in plan.conditional}
    if k <= 0:
        return KeyDraw((), values, 0, 0)
    orders: list[list[tuple]] = []
    for edge, candidate_list in zip(plan.conditional, candidates, strict=True):
        order = [tuple(c) for c in candidate_list]
        random.Random(derive_key_seed(run_id, key_t, salt=edge.id)).shuffle(order)
        orders.append(order)
    n_cells = plan.cells.size if plan.cells is not None else 1
    radices = [n_cells, *(max(1, len(order)) for order in orders)]
    capacity = 1
    for radix in radices:
        capacity *= radix
    n_children = min(k, capacity) if (plan.exact_cells and all(orders)) else k
    cell_order = plan.cells.permutation(rng) if plan.cells is not None else []
    cells: list[tuple] = []
    for i in range(n_children):
        digits = _mixed_radix(i % capacity, radices)
        cells.append(
            plan.cells.rows[cell_order[digits[0]]]
            if plan.cells is not None
            else ()
        )
        for j, edge in enumerate(plan.conditional):
            order = orders[j]
            values[edge.id].append(order[digits[j + 1]] if order else ())
    return KeyDraw(tuple(cells), values, k, capacity)


def expand_keys(
    plan: FanoutPlan,
    keys: Sequence[tuple],
    run_id: str,
    chunk_rows: int,
    draws: Mapping[tuple, KeyDraw | None] | None = None,
) -> Iterator[list[tuple[tuple, tuple]]]:
    """``(key, cell)`` pairs for every child of every key, in chunks of at
    most ``chunk_rows`` — a hot parent never makes an oversized bundle,
    and a key's cells stay unique across the split because they are
    drawn once per key.

    ``draws`` (ADR 0037) is the per-key `joint_key_draw` result a plan
    WITH conditional edges must be expanded from: the cells then come out
    of the joint walk that also decides each child's candidates, so the
    two stay index-aligned across a chunk split. A key absent from the
    mapping, or mapped to ``None`` (dropped: no candidate on a
    non-nullable edge), emits nothing. Without it — every ADR 0036 plan —
    the cells are drawn here exactly as before.
    """
    chunk_rows = max(1, int(chunk_rows))
    chunk: list[tuple[tuple, tuple]] = []
    for key in keys:
        cells: list[tuple]
        if draws is not None:
            draw = draws.get(tuple(key))
            if draw is None:
                continue
            cells = list(draw.cells)
        else:
            rng = random.Random(derive_key_seed(run_id, tuple(key)))
            k = plan.histogram.sample(rng)
            if k <= 0:
                continue
            cells = (
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
