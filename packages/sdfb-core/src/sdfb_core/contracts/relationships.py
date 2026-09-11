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
            drives:   true            # this edge generates the child (ADR 0036)

Three flags, three different jobs:

* ``enabled: false`` (table) — the table leaves the GRAPH. Anything that
  reached the rest of the model only through it detaches with it, so one
  flag prunes a whole branch without deleting a line. The table itself
  still generates when it is the explicit target: the flag governs
  relationship participation, never permission.
* ``enforced: false`` (edge) — the relationship is real and drawn, but
  no keys are drawn from it. For join keys that exist in the business
  model and not in the DDL.
* ``drives: true`` (edge, ADR 0036) — this is the edge the child is
  GENERATED from: its parent's landed keys become the child's request
  stream, one child row per source fan-out draw. Needed only to
  disambiguate when a table has several enforced in-model edges (a lone
  one drives by itself, and when no marker and no ancestry decides it,
  the first declared edge defaults to driving — ADR 0037 ruling A).
  Every other enforced edge is then `implied` (a subset of the driving
  columns, carried transitively), `conditional` (shares columns with
  the driving edge) or `independent` (shares none) — see
  :meth:`RelationshipRegistry.edge_roles`. Only more than one edge
  marked `drives: true` still stops the launch.

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
    # Design 2026-09-10 (ADR 0036): the edge whose parent keys this table
    # is generated FROM. Needed only when a child has several enforced
    # in-model edges; a lone edge drives by itself.
    drives: bool = False

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
        seen: set[tuple[tuple[str, ...], str, tuple[str, ...]]] = set()
        for edge in relations.fk:
            # Two fk: entries with the same (cols, ref, ref_cols) are a
            # copy-paste mistake, not two edges: under value equality
            # they collapse silently downstream (edge_roles' dict keying,
            # edge_overlap/edge_rest's driving-edge comparison) instead
            # of being caught here, at load time.
            key = (edge.cols, edge.ref, edge.ref_cols)
            if key in seen:
                raise RelationshipError(
                    f"{model.source}: {table}: edge "
                    f"({','.join(edge.cols)})->{edge.ref} declared twice "
                    f"— one fk entry per (cols, ref, ref_cols)"
                )
            seen.add(key)
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

    def _raw_enforced(self, table: str) -> tuple[FkEdge, ...]:
        """Enforced edges exactly as declared: both ends enabled."""
        relations = self.relations(table)
        if relations is None or not relations.enabled:
            return ()
        return tuple(
            edge
            for edge in relations.fk
            if edge.enforced
            and (edge.external or self.enabled(edge.ref))
        )

    def enforced_edges(self, table: str) -> tuple[FkEdge, ...]:
        """Edges this table actually draws keys from: enforced, both ends
        enabled, and WIDENED with the column pairs the model's own
        children pin (`derived_widenings`, ADR 0036 rev): when a child
        references the same columns in this table AND in this table's
        parent, those columns are inherited from the parent, so this
        table's edge to it carries them."""
        return tuple(
            self._widened(table, edge) for edge in self._raw_enforced(table)
        )

    def _widened(self, table: str, edge: FkEdge) -> FkEdge:
        added = [
            pair
            for rec in self._widenings()
            if rec["table"] == _name(table) and rec["ref"] == edge.ref
            for pair in rec["added"]
            if pair[0] not in edge.cols
        ]
        if not added:
            return edge
        return edge.model_copy(
            update={
                "cols": edge.cols + tuple(c for c, _ in added),
                "ref_cols": edge.ref_cols + tuple(r for _, r in added),
            }
        )

    def _widenings(self) -> list[dict]:
        """Column pairs a parent's edge to a grandparent must carry, pinned
        by a child that references the SAME columns in both (the model
        asserts the correspondence). Derived, never written back."""
        records: list[dict] = []
        for model in self.models:
            for child, relations in model.tables.items():
                if not relations.enabled:
                    continue
                edges = [e for e in self._raw_enforced(child) if not e.external]
                for i, e1 in enumerate(edges):
                    for e2 in edges[i + 1 :]:
                        if e1.cols != e2.cols or e1.ref == e2.ref:
                            continue
                        for near, far in ((e1, e2), (e2, e1)):
                            # `near.ref` must itself hold a direct edge to `far.ref`.
                            for up in self._raw_enforced(near.ref):
                                if up.external or up.ref != far.ref:
                                    continue
                                known = set(zip(up.cols, up.ref_cols, strict=True))
                                added = [
                                    (n, f)
                                    for n, f in zip(near.ref_cols, far.ref_cols, strict=True)
                                    if (n, f) not in known and n not in up.cols
                                ]
                                if added:
                                    records.append(
                                        {"table": near.ref, "ref": far.ref,
                                         "via": child, "added": added}
                                    )
        return records

    def derived_widenings(self) -> list[dict]:
        """``[{"table", "ref", "via", "added": [(col, ref_col), …]}, …]`` —
        every edge the registry widened, for the launcher to announce."""
        return self._widenings()

    def _descends(self, table: str, ancestor: str, seen: set[str] | None = None) -> bool:
        """True when ``table`` reaches ``ancestor`` over enforced edges."""
        seen = seen if seen is not None else set()
        if table == ancestor:
            return True
        if table in seen:
            return False
        seen.add(table)
        return any(
            not e.external and self._descends(e.ref, ancestor, seen)
            for e in self._raw_enforced(table)
        )

    def edge_roles(self, table: str) -> dict[FkEdge, str]:
        """Role of every enforced edge of ``table`` (ADR 0036/0037): the
        ``driving`` edge is the parent whose keys the child is generated
        from (:meth:`driving_choice` says how it was picked);
        ``implied`` — a subset of the driving edge's columns, carried by
        the driving parent from that parent through its own enforced
        edges, transitively: nothing to draw; ``conditional`` — shares
        at least one column with the driving edge (co-drawn from a
        joint pool on the shared columns, ADR 0037 §4); ``independent``
        — shares no column with the driving edge (drawn from a
        side-input key pool, ADR 0037 §5); ``external`` — the parent is
        outside this model. The only launch stop left is more than one
        edge marked ``drives: true`` (rule 5)."""
        edges = self.enforced_edges(table)
        roles: dict[FkEdge, str] = {e: "external" for e in edges if e.external}
        internal = [e for e in edges if not e.external]
        if not internal:
            return roles
        driving, _ = self._pick_driving(table, internal)
        roles[driving] = "driving"
        for edge in internal:
            if edge is driving:
                continue
            if self._implied(edge, driving):
                roles[edge] = "implied"
                continue
            overlap = tuple(c for c in edge.cols if c in driving.cols)
            roles[edge] = "conditional" if overlap else "independent"
        return roles

    def driving_edge(self, table: str) -> FkEdge | None:
        return next(
            (e for e, r in self.edge_roles(table).items() if r == "driving"), None
        )

    def _pick_driving(
        self, table: str, internal: list[FkEdge]
    ) -> tuple[FkEdge, str]:
        """The driving edge and how it was chosen (ADR 0037 §3, in
        order): a lone internal edge drives itself (``"single"``);
        exactly one edge marked ``drives: true`` (``"marked"``); the
        parent that descends from every other candidate, widened with
        the child's pins (``"derived"``, ADR 0036 rev 2); else — no
        marker and no ancestry between the parents — the first declared
        edge drives (``"first_declared"``, ruling A, 2026-09-11). More
        than one edge marked ``drives: true`` is the only stop left."""
        if len(internal) == 1:
            return internal[0], "single"
        marked = [e for e in internal if e.drives]
        if len(marked) > 1:
            names = ", ".join(f"({','.join(e.cols)})->{e.ref}" for e in internal)
            raise RelationshipError(
                f"{_name(table)}: {len(internal)} enforced edges [{names}] "
                f"and {len(marked)} marked `drives: true` — mark exactly "
                f"one edge `drives: true` (the parent whose keys this "
                f"table is generated from)"
            )
        if len(marked) == 1:
            return marked[0], "marked"
        # Unmarked (the operator only toggled `enabled`): the DAG
        # decides — the parent that itself descends from every other
        # candidate parent is the most-derived one and drives.
        parents = {e.ref for e in internal}
        lowest = [
            e for e in internal
            if all(self._descends(e.ref, other) for other in parents if other != e.ref)
        ]
        if len({e.ref for e in lowest}) == 1:
            return lowest[0], "derived"
        # No marker, no ancestry between the parents (ADR 0037 ruling A,
        # 2026-09-11): the first declared internal enforced edge drives.
        return internal[0], "first_declared"

    def _driving_edge_and_choice(self, table: str) -> tuple[FkEdge | None, str | None]:
        internal = [e for e in self.enforced_edges(table) if not e.external]
        if not internal:
            return None, None
        return self._pick_driving(table, internal)

    def driving_choice(self, table: str) -> str | None:
        """How the driving edge was picked (ADR 0037 §3): ``"single"``,
        ``"marked"``, ``"derived"`` or ``"first_declared"`` — see
        :meth:`_pick_driving`. ``None`` when ``table`` has no internal
        enforced edge (a root, a disabled table, or a table outside
        every model)."""
        return self._driving_edge_and_choice(table)[1]

    def _overlap_with_driving(
        self, table: str, edge: FkEdge
    ) -> tuple[str, ...] | None:
        """``edge``'s overlap with the driving edge, in ``edge.cols``
        order — or ``None`` when ``edge`` IS the driving edge, or
        ``table`` has no driving edge at all. Compared by VALUE
        (``==``), not identity: `_widened()` returns a fresh `FkEdge`
        via `model_copy()` on every call, so a widened driving edge
        obtained from an earlier `enforced_edges()`/`edge_roles()` call
        is never the same Python object as the one this method derives
        — but it is always equal (frozen pydantic model, structural
        `__eq__`), as :meth:`mermaid`'s `roles.get(widened)` already
        relies on elsewhere in this file."""
        driving, _ = self._driving_edge_and_choice(table)
        if driving is None or edge == driving:
            return None
        return tuple(c for c in edge.cols if c in driving.cols)

    def edge_overlap(self, table: str, edge: FkEdge) -> tuple[str, ...]:
        """Child column names of ``edge`` that also appear in the
        driving edge's columns, in ``edge.cols`` order. Empty for the
        driving edge itself, and when ``table`` has no driving edge."""
        return self._overlap_with_driving(table, edge) or ()

    def edge_rest(self, table: str, edge: FkEdge) -> tuple[str, ...]:
        """``edge.cols`` outside :meth:`edge_overlap`. Empty for the
        driving edge itself, and when ``table`` has no driving edge."""
        overlap = self._overlap_with_driving(table, edge)
        if overlap is None:
            return ()
        return tuple(c for c in edge.cols if c not in overlap)

    def _implied(self, edge: FkEdge, driving: FkEdge) -> bool:
        """``edge`` is satisfied by construction when its columns ride on
        the driving edge AND the driving parent obtains them from
        ``edge.ref`` (transitively over enforced edges)."""
        if not set(edge.cols) <= set(driving.cols):
            return False
        # The parent-side names of edge.cols on the driving parent.
        pos = {c: i for i, c in enumerate(driving.cols)}
        parent_cols = {driving.ref_cols[pos[c]] for c in edge.cols}
        return self._carries(driving.ref, edge.ref, parent_cols, seen=set())

    def _carries(
        self,
        table: str,
        target: str,
        cols: set[str],
        seen: set[tuple[str, frozenset[str]]],
    ) -> bool:
        if table == target:
            return True
        key = (table, frozenset(cols))
        if key in seen:
            return False
        seen.add(key)
        for up in self.enforced_edges(table):
            if up.external or not cols <= set(up.cols):
                continue
            pos = {c: i for i, c in enumerate(up.cols)}
            upstream = {up.ref_cols[pos[c]] for c in cols}
            if self._carries(up.ref, target, upstream, seen):
                return True
        return False

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
                    f"{int(e.enforced)}:{int(e.drives)}"
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
        disabled table is dimmed like an external one. Driving and
        implied edges keep their plain ``cols → ref_cols`` label
        unchanged; independent and conditional edges (ADR 0037) get an
        extra suffix."""
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
            try:
                roles = self.edge_roles(name) if relations.enabled else {}
            except RelationshipError:
                roles = {}
            for edge in relations.fk:
                arrow = "-->" if edge.enforced else "-.->"
                label = f"{','.join(edge.cols)} → {','.join(edge.ref_cols)}"
                widened = self._widened(name, edge) if edge.enforced else edge
                role = roles.get(widened)
                if role == "independent":
                    label += " -- independent"
                elif role == "conditional":
                    overlap = self.edge_overlap(name, widened)
                    label += f" -- conditional on {','.join(overlap)}"
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

    def _role_tag(
        self, name: str, edge: FkEdge, roles: dict[FkEdge, str], choice: str | None
    ) -> str | None:
        """The ``[enforced, ...]`` suffix for one edge's role (card tags,
        ADR 0036/0037), or ``None`` to keep the caller's plain
        ``"enforced"`` tag (an edge :meth:`edge_roles` didn't classify —
        should not happen once ``roles`` is non-empty)."""
        role = roles.get(edge)
        if role == "driving":
            if choice == "first_declared":
                return "enforced, DRIVES (first declared — mark drives: true to choose)"
            return "enforced, DRIVES"
        if role == "implied":
            driving = next((e for e, r in roles.items() if r == "driving"), None)
            return f"enforced, implied via {_name(driving.ref)}" if driving else None
        if role == "independent":
            return "enforced, independent"
        if role == "conditional":
            overlap = self.edge_overlap(name, edge)
            return f"enforced, conditional on ({','.join(overlap)})"
        return None

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
        # Compute roles once per table (ADR 0036/0037).
        try:
            roles = self.edge_roles(name)
            choice = self.driving_choice(name)
        except RelationshipError:
            roles = {}
            choice = None
        widened_by = {
            (rec["ref"], tuple(rec["added"])): rec["via"]
            for rec in self._widenings() if rec["table"] == name
        }
        for declared in relations.fk:
            edge = self._widened(name, declared) if declared.enforced else declared
            arrow = "-->" if edge.enforced else "..>"
            tag = "enforced" if edge.enforced else "documented, never drawn"
            if edge.enforced and not relations.enabled:
                tag = "this table DISABLED — not drawn"
            elif edge.enforced and not (
                edge.external or self.enabled(edge.ref)
            ):
                tag = "parent DISABLED — not drawn"
            # Render edge roles (ADR 0036/0037).
            elif edge.enforced and tag == "enforced" and roles:
                tag = self._role_tag(name, edge, roles, choice) or tag
            if edge is not declared:
                added = tuple(zip(edge.cols[len(declared.cols):], edge.ref_cols[len(declared.ref_cols):], strict=True))
                via = widened_by.get((edge.ref, added), "?")
                tag += f", widened via {via} (+{','.join(c for c, _ in added)})"
            lines.append(
                f"        |   +- ({','.join(edge.cols)}) {arrow} "
                f"{edge.ref} ({','.join(edge.ref_cols)})   [{tag}]"
            )
        return lines
