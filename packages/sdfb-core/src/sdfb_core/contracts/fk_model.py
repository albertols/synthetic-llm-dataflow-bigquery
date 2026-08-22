"""FK-model graph over a set of relational contracts (ADR 0029).

One definition of the relationship graph — nodes, enforced vs
informational edges, parents-first topological levels, a content hash
for diagram recycling, and the house-style mermaid renderer — shared by
`scripts/run_tableset.py` (wave orchestration), the launcher's
`fk_model_pretty` log entry, and the E2E report tooling. The 2026-07-28
R1 lesson applies: inline copies of graph logic drift.

Informational edges (``ForeignKey.informational``) shape the DIAGRAM
but never the ORDER: they carry no enforcement, so they must not force
a parent to generate first (the 6-table example's PARTY_KEY edge would
otherwise serialize two independent roots).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sdfb_core.observability import sha12

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sdfb_core.contracts.relational import (
        ForeignKey,
        RelationalContract,
    )


class FkModelError(ValueError):
    """Bad table set: duplicates, or an enforced-FK cycle."""


@dataclass(frozen=True)
class FkModel:
    """The resolved relationship graph for one generation set."""

    tables: tuple[str, ...]
    # Referenced parents OUTSIDE the set — prerequisites (must already be
    # landed), never scheduled, drawn dimmed.
    external: tuple[str, ...]
    edges: tuple[tuple[str, ForeignKey], ...]  # (child fqn, edge)
    # Parents-first waves over ENFORCED edges only; each level's tables
    # are independent of one another and may generate in parallel.
    levels: tuple[tuple[str, ...], ...]


def _suffix(fqn: str, parts: int = 2) -> str:
    return ".".join(fqn.split(".")[-parts:])


def _resolve_ref(ref: str, tables: list[str] | tuple[str, ...]) -> str | None:
    """The set member ``ref`` points at, or None (external parent).

    Prefer the ``dataset.table``-qualified match; fall back to a UNIQUE
    table-name match — the worked contracts name refs by their LANDING
    dataset (``synthetic_data.A_TABLE``) while the set lists SOURCE FQNs,
    and `io/fk_pools.parent_landing_fqn` already resolves parents by
    table name alone (the datasets legitimately differ)."""
    qualified = {_suffix(t): t for t in tables}
    hit = qualified.get(_suffix(ref))
    if hit is not None:
        return hit
    name = ref.rsplit(".", 1)[-1]
    by_name = [t for t in tables if t.rsplit(".", 1)[-1] == name]
    return by_name[0] if len(by_name) == 1 else None


def build_fk_model(
    tables: list[str],
    contracts: dict[str, RelationalContract | None],
) -> FkModel:
    """Resolve the FK graph for ``tables`` from their parsed contracts.

    ``fk.ref`` is ``dataset.table``; it matches a set member whose FQN
    ends with it. A parent outside the set lands in ``external``.
    Enforced-edge cycles and duplicate tables raise :class:`FkModelError`.
    """
    if len(set(tables)) != len(tables):
        raise FkModelError(f"duplicate tables in set: {tables}")
    edges: list[tuple[str, ForeignKey]] = []
    parents: dict[str, set[str]] = {t: set() for t in tables}
    external: list[str] = []
    for table in tables:
        contract = contracts.get(table)
        if contract is None:
            continue
        for fk in contract.fk:
            edges.append((table, fk))
            parent = _resolve_ref(fk.ref, tables)
            if parent is None:
                if fk.ref not in external:
                    external.append(fk.ref)
            elif parent != table and not fk.informational:
                parents[table].add(parent)

    # Kahn-style leveling, stable in input order within a level.
    remaining = {t: set(p) for t, p in parents.items()}
    levels: list[tuple[str, ...]] = []
    placed: set[str] = set()
    while remaining:
        ready = tuple(
            t for t in tables
            if t in remaining and remaining[t] <= placed
        )
        if not ready:
            raise FkModelError(
                f"FK cycle among {sorted(remaining)} (enforced edges)"
            )
        for t in ready:
            del remaining[t]
        placed.update(ready)
        levels.append(ready)

    return FkModel(
        tables=tuple(tables),
        external=tuple(external),
        edges=tuple(edges),
        levels=tuple(levels),
    )


def model_sha12(model: FkModel) -> str:
    """Content hash for diagram recycling: same tables + edges ⇒ same
    sha ⇒ the report reuses ``integration_tests/fk_models/<sha>.mmd``
    instead of re-deriving the drawing."""
    canon = ";".join(
        [
            ",".join(model.tables),
            *sorted(
                f"{child}:{'|'.join(fk.cols)}->{fk.ref}:"
                f"{'|'.join(fk.ref_cols)}:{int(fk.informational)}"
                for child, fk in model.edges
            ),
        ]
    )
    return sha12(canon)


def fk_model_mermaid(
    model: FkModel, aliases: dict[str, str] | None = None
) -> str:
    """House-style mermaid source for the model (visual-first docs rule).

    Tables render as store cylinders; enforced edges are solid with
    ``child cols → ref cols`` labels, informational edges dashed;
    external parents use the muted ``data`` class. Paste-renderable in
    the E2E report and readable as text in Cloud Logging.
    """
    aliases = aliases or {}

    def _name(fqn: str) -> str:
        return aliases.get(fqn, fqn)

    def _node_id(fqn: str) -> str:
        return _name(fqn).replace(".", "_").replace("-", "_")

    lines = ["flowchart BT"]
    for t in model.tables:
        lines.append(f'  {_node_id(t)}[("🗄️ {_name(t)}")]')
    for x in model.external:
        lines.append(f'  {_node_id(x)}[("⚪ {_name(x)} (external)")]')
    for child, fk in model.edges:
        parent = _resolve_ref(fk.ref, model.tables) or fk.ref
        label = f"{','.join(fk.cols)} → {','.join(fk.ref_cols)}"
        arrow = "-.->" if fk.informational else "-->"
        lines.append(
            f'  {_node_id(child)} {arrow}|"{label}"| {_node_id(parent)}'
        )
    lines.append(
        "  classDef store fill:#2a78d6,color:#fff,stroke:#1d5599"
    )
    lines.append(
        "  classDef data fill:#6b7280,color:#fff,stroke:#4b5563"
    )
    if model.tables:
        lines.append(
            "  class " + ",".join(_node_id(t) for t in model.tables)
            + " store"
        )
    if model.external:
        lines.append(
            "  class " + ",".join(_node_id(x) for x in model.external)
            + " data"
        )
    return "\n".join(lines)


__all__ = [
    "FkModel",
    "FkModelError",
    "build_fk_model",
    "fk_model_mermaid",
    "model_sha12",
]
