"""Relationship models — the single source of truth for PK/FK/identity.

`config/relationships/<model>.yaml`, one file per relational model,
versioned with the code that reads it (ADR 0032). Before this, the
table-level contract lived inside each BigQuery table DESCRIPTION, so
the truth was scattered across N tables and every change meant a
`bq update` / `terraform apply` against production metadata. A launch
now reads one file and knows the whole model.

A model file, whole::

    model: retail                     # id, shown in the log card
    description: orders chain         # optional prose
    tables:
      A_TABLE:
        pk: [A_COL_001, A_COL_002]
        identity: [A_COL_009]
      B_TABLE:
        pk: [B_COL_001]
        enabled: true                 # false = detach from the model
        fk:
          - cols:     [B_COL_006, B_COL_007]
            ref:      A_TABLE         # bare name = in this model
            ref_cols: [A_COL_001, A_COL_002]
            enforced: true            # false = documented, never generated from

Two flags, two different jobs:

* ``enabled: false`` (table) — the table leaves the GRAPH. Anything that
  reached the rest of the model only through it detaches with it, so one
  flag prunes a whole branch without deleting a line. The table itself
  still generates when it is the explicit target: the flag governs
  relationship participation, never permission.
* ``enforced: false`` (edge) — the relationship is real and drawn, but
  no keys are drawn from it. For join keys that exist in the business
  model and not in the DDL.

Column-level ``llm_prompt_constraint`` stays in COLUMN descriptions
(:mod:`sdfb_core.contracts.prompt_constraint`) — that is per-column
generation steering, not relational structure, and it belongs next to
the column it steers.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from sdfb_core.observability import sha12

__all__ = [
    "FkEdge",
    "RelationshipError",
    "RelationshipModel",
    "RelationshipRegistry",
    "TableRelations",
    "parse_relationship_model",
]


class RelationshipError(ValueError):
    """A model file is unusable: bad shape, unknown ref, cycle, or a
    table claimed by two models. Never degraded silently — a half-read
    relational model generates the wrong data."""


def _name(table: str) -> str:
    """Bare table name: model files key on it, targets arrive as FQNs."""
    return table.rsplit(".", 1)[-1]


class FkEdge(BaseModel):
    """One FK edge: this table's ``cols`` reference ``ref``'s ``ref_cols``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cols: tuple[str, ...]
    ref: str
    ref_cols: tuple[str, ...]
    # False = documented-only: drawn in the card, never a source of keys.
    enforced: bool = True
    note: str = ""

    @field_validator("cols", "ref_cols")
    @classmethod
    def _non_empty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v or any(not c for c in v):
            raise ValueError("FK column lists must be non-empty strings")
        return v

    @property
    def external(self) -> bool:
        """True when ``ref`` names a table outside this model (it must be
        dataset-qualified, and must already be landed)."""
        return "." in self.ref


class TableRelations(BaseModel):
    """One table's relational facts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pk: tuple[str, ...] = ()
    identity: tuple[str, ...] = ()
    fk: tuple[FkEdge, ...] = ()
    # False = detached from the model (see module docstring).
    enabled: bool = True
    note: str = ""


class RelationshipModel(BaseModel):
    """One model file: a named set of tables and the edges between them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    description: str = ""
    tables: dict[str, TableRelations] = {}
    # Where this came from — printed in the log card so an operator can
    # go straight to the file that decided the run.
    source: str = ""


def parse_relationship_model(text: str, source: str = "") -> RelationshipModel:
    """One YAML document → a validated :class:`RelationshipModel`."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RelationshipError(f"{source}: not valid YAML — {exc}") from exc
    if not isinstance(raw, dict):
        raise RelationshipError(
            f"{source}: expected a mapping at the top level, got "
            f"{type(raw).__name__}"
        )
    try:
        model = RelationshipModel.model_validate({**raw, "source": source})
    except ValidationError as exc:
        raise RelationshipError(f"{source}: {exc}") from exc
    _validate_refs(model)
    return model


def _validate_refs(model: RelationshipModel) -> None:
    known = set(model.tables)
    for table, relations in model.tables.items():
        for edge in relations.fk:
            if edge.ref == table or _name(edge.ref) == table:
                raise RelationshipError(
                    f"{model.source}: {table} references itself "
                    f"({edge.ref}) — an FK edge needs two tables"
                )
            if len(edge.cols) != len(edge.ref_cols):
                raise RelationshipError(
                    f"{model.source}: {table} FK arity mismatch — "
                    f"{list(edge.cols)} vs {list(edge.ref_cols)}"
                )
            if not edge.external and edge.ref not in known:
                raise RelationshipError(
                    f"{model.source}: {table}'s fk.ref {edge.ref!r} does "
                    f"not name a table in model {model.model!r} "
                    f"({sorted(known)}). Qualify it as dataset.table if "
                    f"the parent lives outside this model."
                )


@dataclass(frozen=True)
class RelationshipRegistry:
    """Every model the launch loaded, indexed by table.

    The registry is the ONLY thing the pipeline asks about relational
    structure. It answers four questions: what are this table's keys,
    which tables travel with it, in what order, and what should the log
    show.
    """

    models: tuple[RelationshipModel, ...] = ()

    @classmethod
    def from_sources(
        cls, sources: list[tuple[str, str]]
    ) -> RelationshipRegistry:
        """``[(source, yaml text), …]`` → a validated registry.

        A table may appear in exactly ONE model: two models claiming it
        would make "the single source of truth" a question of file
        ordering.
        """
        models = tuple(
            parse_relationship_model(text, source=source)
            for source, text in sources
        )
        owners: dict[str, list[str]] = {}
        for model in models:
            for table in model.tables:
                owners.setdefault(table, []).append(model.model)
        clashes = {t: m for t, m in owners.items() if len(m) > 1}
        if clashes:
            detail = "; ".join(
                f"{t} in {sorted(set(ms))}" for t, ms in sorted(clashes.items())
            )
            raise RelationshipError(
                f"table declared in 2 models or more — one source of "
                f"truth per table: {detail}"
            )
        registry = cls(models=models)
        for model in models:
            for table in model.tables:
                # Surfaces enforced-edge cycles at LOAD time, with the
                # file name, instead of mid-launch.
                registry.generation_order(registry.component(table))
        return registry

    # -- lookups -----------------------------------------------------------

    def model_for(self, table: str) -> RelationshipModel | None:
        key = _name(table)
        for model in self.models:
            if key in model.tables:
                return model
        return None

    def relations(self, table: str) -> TableRelations | None:
        model = self.model_for(table)
        return model.tables[_name(table)] if model else None

    def enabled(self, table: str) -> bool:
        relations = self.relations(table)
        return True if relations is None else relations.enabled

    def enforced_edges(self, table: str) -> tuple[FkEdge, ...]:
        """Edges this table actually draws keys from: enforced, and with
        BOTH ends enabled (a detached parent hands out nothing)."""
        relations = self.relations(table)
        if relations is None or not relations.enabled:
            return ()
        return tuple(
            edge
            for edge in relations.fk
            if edge.enforced
            and (edge.external or self.enabled(edge.ref))
        )

    # -- graph -------------------------------------------------------------

    def _adjacency(self) -> dict[str, set[str]]:
        """Undirected neighbours over ENABLED tables only. Documented
        edges count here: they still say "these tables belong together",
        which is what a scenario-2 launch is asking about."""
        adjacent: dict[str, set[str]] = {}
        for model in self.models:
            for table, relations in model.tables.items():
                adjacent.setdefault(table, set())
                if not relations.enabled:
                    continue
                for edge in relations.fk:
                    if edge.external or not self.enabled(edge.ref):
                        continue
                    adjacent[table].add(edge.ref)
                    adjacent.setdefault(edge.ref, set()).add(table)
        return adjacent

    def component(self, table: str) -> tuple[str, ...]:
        """``table`` plus every table still reachable from it.

        A disabled table is not traversed, so anything that reached the
        model only through it is no longer part of this launch. A
        disabled TARGET is returned alone — the flag detaches, it does
        not forbid.
        """
        key = _name(table)
        if self.relations(key) is None:
            return (key,)
        adjacent = self._adjacency()
        seen = {key}
        frontier = [key]
        while frontier:
            nxt = []
            for current in frontier:
                for neighbour in adjacent.get(current, ()):
                    if neighbour not in seen:
                        seen.add(neighbour)
                        nxt.append(neighbour)
            frontier = nxt
        ordered = [t for m in self.models for t in m.tables if t in seen]
        return tuple(ordered)

    def generation_waves(
        self, tables: tuple[str, ...]
    ) -> tuple[tuple[str, ...], ...]:
        """Parents-first WAVES over ENFORCED edges, stable in model order.

        Every table inside a wave is independent of the others, so a wave
        may run in parallel while the waves themselves stay ordered — a
        child never starts before its parent has landed.
        """
        members = {_name(t) for t in tables}
        parents = {
            t: {
                edge.ref
                for edge in self.enforced_edges(t)
                if not edge.external and edge.ref in members
            }
            for t in members
        }
        order_hint = [t for m in self.models for t in m.tables if t in members]
        order_hint += [t for t in sorted(members) if t not in order_hint]
        waves: list[tuple[str, ...]] = []
        placed: set[str] = set()
        remaining = dict(parents)
        while remaining:
            ready = tuple(
                t for t in order_hint
                if t in remaining and remaining[t] <= placed
            )
            if not ready:
                raise RelationshipError(
                    f"FK cycle among {sorted(remaining)} — enforced edges "
                    f"must form a DAG (a child cannot be its own ancestor)"
                )
            for table in ready:
                del remaining[table]
            placed.update(ready)
            waves.append(ready)
        return tuple(waves)

    def generation_order(self, tables: tuple[str, ...]) -> tuple[str, ...]:
        """Parents first — :meth:`generation_waves` flattened."""
        return tuple(
            t for wave in self.generation_waves(tables) for t in wave
        )

    def sha12(self) -> str:
        """Content hash of every loaded model — same models, same sha, so
        a report can recycle a diagram instead of redrawing it."""
        canon = ";".join(
            sorted(
                f"{m.model}:{t}:{','.join(r.pk)}:{','.join(r.identity)}:"
                f"{int(r.enabled)}:"
                + "|".join(
                    f"{','.join(e.cols)}->{e.ref}:{','.join(e.ref_cols)}:"
                    f"{int(e.enforced)}"
                    for e in r.fk
                )
                for m in self.models
                for t, r in m.tables.items()
            )
        )
        return sha12(canon)

    # -- the log card ------------------------------------------------------

    def card(self, table: str) -> str:
        """One glanceable block: the model, where it came from, and every
        table's keys, edges and state.

        Written for Cloud Logging at 3am: no renderer, no copy-paste,
        waves state the generation order, ``-->`` is an enforced edge and
        ``..>`` a documented one, and a disabled table says so on its own
        line.
        """
        model = self.model_for(table)
        if model is None:
            return (
                f"RELATIONSHIP MODEL | none — {_name(table)} is not in any "
                f"model file; generating it alone (PK/identity from CLI "
                f"flags if given)"
            )
        component = self.component(table)
        order = self.generation_order(component)
        disabled = [
            t for t in model.tables
            if not model.tables[t].enabled
        ]
        enforced = sum(len(self.enforced_edges(t)) for t in order)
        documented = sum(
            1
            for t in model.tables
            for e in model.tables[t].fk
            if not e.enforced
        )
        head = (
            f"RELATIONSHIP MODEL {model.model} | source {model.source} | "
            f"sha {self.sha12()}"
        )
        counts = (
            f"  {len(model.tables)} tables declared · {len(order)} in this "
            f"launch · {enforced} enforced + {documented} documented edges"
            + (f" · {len(disabled)} DISABLED" if disabled else "")
        )
        lines = [head, counts]
        if model.description:
            lines.append(f"  \"{model.description}\"")
        for wave, name in enumerate(order):
            lines.extend(self._table_lines(model, name, f"wave {wave}"))
        for name in model.tables:
            if name not in order:
                lines.extend(self._table_lines(model, name, "  --  "))
        return "\n".join(lines)

    def mermaid(self, table: str) -> str:
        """House-style diagram source for the table's model — the ONE
        renderer reports embed (visual-first docs rule). Stores are
        cylinders, enforced edges solid, documented edges dashed, and a
        disabled table is dimmed like an external one."""
        model = self.model_for(table)
        if model is None:
            return ""

        def node_id(name: str) -> str:
            return name.replace(".", "_").replace("-", "_")

        lines = ["flowchart BT"]
        external: list[str] = []
        for name, relations in model.tables.items():
            icon = "🗄️" if relations.enabled else "🚫"
            suffix = "" if relations.enabled else " (disabled)"
            lines.append(f'  {node_id(name)}[("{icon} {name}{suffix}")]')
            for edge in relations.fk:
                if edge.external and edge.ref not in external:
                    external.append(edge.ref)
        for ref in external:
            lines.append(f'  {node_id(ref)}[("⚪ {ref} (external)")]')
        for name, relations in model.tables.items():
            for edge in relations.fk:
                arrow = "-->" if edge.enforced else "-.->"
                label = f"{','.join(edge.cols)} → {','.join(edge.ref_cols)}"
                lines.append(
                    f'  {node_id(name)} {arrow}|"{label}"| '
                    f"{node_id(edge.ref)}"
                )
        lines.append("  classDef store fill:#2a78d6,color:#fff,stroke:#1d5599")
        lines.append("  classDef data fill:#6b7280,color:#fff,stroke:#4b5563")
        live = [t for t, r in model.tables.items() if r.enabled]
        dim = [t for t, r in model.tables.items() if not r.enabled] + external
        if live:
            lines.append("  class " + ",".join(node_id(t) for t in live) + " store")
        if dim:
            lines.append("  class " + ",".join(node_id(t) for t in dim) + " data")
        return "\n".join(lines)

    def log_body(self, table: str) -> str:
        """What the launcher and the workers both print: the card for
        humans, the fenced mermaid below it for the report tooling to
        lift by sha. One entry, both audiences."""
        diagram = self.mermaid(table)
        card = self.card(table)
        if not diagram:
            return card
        return card + "\n\n```mermaid\n" + diagram + "\n```"

    def _table_lines(
        self, model: RelationshipModel, name: str, prefix: str
    ) -> list[str]:
        relations = model.tables[name]
        keys = []
        if relations.pk:
            keys.append(f"pk({','.join(relations.pk)})")
        if relations.identity:
            keys.append(f"identity({','.join(relations.identity)})")
        state = "" if relations.enabled else "   [DISABLED — detached]"
        lines = [
            f" {prefix} | {name:<24} {' '.join(keys) or '(no keys declared)'}"
            f"{state}"
        ]
        for edge in relations.fk:
            arrow = "-->" if edge.enforced else "..>"
            tag = "enforced" if edge.enforced else "documented, never drawn"
            if edge.enforced and not relations.enabled:
                tag = "this table DISABLED — not drawn"
            elif edge.enforced and not (
                edge.external or self.enabled(edge.ref)
            ):
                tag = "parent DISABLED — not drawn"
            lines.append(
                f"        |   +- ({','.join(edge.cols)}) {arrow} "
                f"{edge.ref} ({','.join(edge.ref_cols)})   [{tag}]"
            )
        return lines
