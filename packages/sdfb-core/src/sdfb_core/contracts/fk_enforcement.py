"""What this launch will actually enforce, said out loud (ADR 0031).

`informational: true` is a legitimate declaration — it exists for
relationships whose join key is absent from the DDL (ADR 0029). But it
is indistinguishable, at a glance, from an edge someone marked
display-only by accident: the 2026-08-23 run grouped two tables into one
49-minute job on the strength of an informational edge, generated both,
enforced nothing, and reported PASSED.

The difference is derivable, not a judgment call: an informational edge
whose columns all EXIST on both sides could have been enforced. This
module turns that derivation into one glanceable launcher block that
names the edge, the verdict, and the exact contract edit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sdfb_core.contracts.relational import ForeignKey, RelationalContract

__all__ = ["FkEnforcementSummary", "enforcement_summary"]


@dataclass(frozen=True)
class FkEnforcementSummary:
    """Per-launch verdict on referential integrity."""

    table: str
    enforced: int
    informational: int
    # Informational edges that COULD be enforced today — the actionable set.
    enforceable: tuple[str, ...]
    text: str

    @property
    def warn(self) -> bool:
        """Warn when the launch enforces nothing it could have enforced."""
        return self.enforced == 0 and bool(self.enforceable)


def _columns_for(
    ref: str, columns_by_table: Mapping[str, set[str] | frozenset[str]]
) -> set[str] | None:
    """Known columns of ``ref``, matched by FQN then by table name."""
    if ref in columns_by_table:
        return set(columns_by_table[ref])
    name = ref.rsplit(".", 1)[-1]
    hits = [
        set(cols)
        for table, cols in columns_by_table.items()
        if table.rsplit(".", 1)[-1] == name
    ]
    return hits[0] if len(hits) == 1 else None


def _edge_line(table: str, fk: ForeignKey) -> str:
    arrow = "..>" if fk.informational else "-->"
    return (
        f"  {table.rsplit('.', 1)[-1]} ({','.join(fk.cols)}) {arrow} "
        f"{fk.ref} ({','.join(fk.ref_cols)})"
        + ("   [informational]" if fk.informational else "   [enforced]")
    )


def enforcement_summary(
    table: str,
    contract: RelationalContract | None,
    columns_by_table: Mapping[str, set[str] | frozenset[str]],
) -> FkEnforcementSummary | None:
    """The launch's FK verdict, or None when no edge is declared.

    ``columns_by_table`` is whatever schemas this launch already
    resolved (the target, plus in-set tables of a relational closure).
    A parent whose schema is unknown is reported as such — the summary
    never guesses that an edge is enforceable.
    """
    if contract is None or not contract.fk:
        return None
    child_columns = _columns_for(table, columns_by_table) or set()
    enforced = [fk for fk in contract.fk if not fk.informational]
    informational = [fk for fk in contract.fk if fk.informational]

    lines: list[str] = []
    enforceable: list[str] = []
    for fk in contract.fk:
        lines.append(_edge_line(table, fk))
        if not fk.informational:
            continue
        parent_columns = _columns_for(fk.ref, columns_by_table)
        missing_child = [c for c in fk.cols if c not in child_columns]
        if parent_columns is None:
            lines.append(
                "      parent schema unknown this launch — enforceability "
                "not assessed"
            )
        elif missing_child or [
            c for c in fk.ref_cols if c not in parent_columns
        ]:
            lines.append(
                "      join key is not in the DDL on both sides — "
                "display-only is the correct declaration"
            )
        else:
            enforceable.append(",".join(fk.cols))
            lines.append(
                "      ENFORCEABLE: every column exists on both sides. "
                'Delete `"informational": true` from this FK entry in '
                f"{table.rsplit('.', 1)[-1]}'s table description to "
                "generate real referential integrity (the fk.orphan rule "
                "then gates the run)."
            )

    header = (
        f"FK ENFORCEMENT | {len(enforced)} enforced · "
        f"{len(informational)} informational"
    )
    if not enforced:
        lines.append(
            "  → referential integrity NOT verified this run: with 0 "
            "enforced edges there is no parent key set to draw from and "
            "the fk.orphan rule has nothing to check."
        )
    return FkEnforcementSummary(
        table=table,
        enforced=len(enforced),
        informational=len(informational),
        enforceable=tuple(enforceable),
        text="\n".join([header, *lines]),
    )
