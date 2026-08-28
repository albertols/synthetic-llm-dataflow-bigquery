"""Shared plumbing for the once-per-run `generation_plan` milestone.

Both engines answer the same question — which fields are LLM free-text
pools, which are shaped identifiers routed off the LLM, which are plain
samplers — from their own `ColumnProfile` types. The two profile classes
are distinct dataclasses but agree on the duck-typed surface this module
needs: ``.kind`` (a StrEnum sharing the five values) and
``.identifier_shape``. One definition of the label mapping and the
once-guard, every engine (the 2026-07-28 R1 lesson: inline copies drift).
"""

from __future__ import annotations

import threading
from typing import Any

from sdfb_core.engines.text_shapes import shape_mix_is_identifier_like
from sdfb_core.observability import (
    log_milestone_pretty,
    log_milestone_text,
    sha12,
)

# The bounded-pool ceiling shared by the engines' free-text ladders and
# the launcher's PK-capacity preflight (ADR 0028 P4): a constrained
# column with no samplable pattern can never exceed this many distinct
# values, so a PK routed there caps at it — the 2026-08-21 run DLQ'd
# 999 488 of 1M rows exactly this way.
FREE_TEXT_POOL_MAX = 512

# (engine, reference_digest, table_fqn) triples already logged by this
# worker process. Keyed per engine so a b1 + b2 comparison run on the same
# reference sample logs BOTH plans.
_LOGGED: set[tuple[str, str, str]] = set()
_LOGGED_LOCK = threading.Lock()


def clear_generation_plan_log() -> None:
    """Forget which plans were logged (tests / maintenance only)."""
    with _LOGGED_LOCK:
        _LOGGED.clear()


def should_log_plan(engine: str, reference_digest: str, table_fqn: str) -> bool:
    """True exactly once per (engine, digest, table) per worker process."""
    key = (engine, reference_digest or "", table_fqn)
    with _LOGGED_LOCK:
        if key in _LOGGED:
            return False
        _LOGGED.add(key)
        return True


def build_plan_detail(profiles: dict[str, Any]) -> dict[str, dict]:
    """column → compact fidelity detail for the generation_plan milestone.

    The 2026-08-05 spec (WS-B): the plan line should answer "what will this
    column's sparsity and shape handling be" without a postmortem re-derive.
    Duck-typed like :func:`build_plan`; fields absent on an engine's profile
    default safely (b1/b2 parity guaranteed by the shared tests).
    """
    detail: dict[str, dict] = {}
    labels = {
        col: label
        for label, cols in build_plan(profiles).items()
        for col in cols
    }
    for name, prof in profiles.items():
        detail[name] = {
            "kind": labels[name],
            "null_fraction": round(float(prof.null_fraction), 4),
            "empty_fraction": round(
                float(getattr(prof, "empty_fraction", 0.0)), 4
            ),
            "shapes": len(getattr(prof, "shape_mix", None) or ()),
            "constraint": bool(getattr(prof, "llm_prompt_constraint", "")),
            # Whether the default (`identifiers`) expansion draws this
            # column from its shape mix instead of the bounded pool — the
            # 2026-08-11 R1 postmortems reverse-engineered this per column.
            "expandable": bool(
                getattr(prof, "identifier_shape", None) is not None
                or shape_mix_is_identifier_like(
                    getattr(prof, "shape_mix", None)
                )
            ),
        }
    return dict(sorted(detail.items()))


def build_constraints_detail(profiles: dict[str, Any]) -> dict[str, dict]:
    """column → the `llm_prompt_constraint` actually fetched from the DDL.

    The 2026-08-20 follow-up: the launcher preflight named constrained
    columns and the plan detail said `constraint: true`, but nothing in the
    worker logs showed WHAT was fetched — a Terraform description edit was
    unverifiable without a `--prompt_debug` run. The rendered clause is
    config (Terraform/git-owned; real values are banned from constraints by
    ADR 0024's privacy rule), so it logs in full; `clause_sha12` is the
    drift-comparison key shared with `freetext_pool_prompt`. Duck-typed
    like :func:`build_plan_detail` (b1/b2 parity).
    """
    detail: dict[str, dict] = {}
    for name, prof in profiles.items():
        clause = getattr(prof, "llm_prompt_constraint", "") or ""
        pattern = getattr(prof, "constraint_pattern", "") or ""
        examples = getattr(prof, "constraint_examples", ()) or ()
        sets_length = bool(getattr(prof, "constraint_sets_length", False))
        if not clause and not pattern and not examples:
            continue
        detail[name] = {
            "clause": clause,
            "clause_sha12": sha12(clause),
            "chars": len(clause),
            "pattern": bool(pattern),
            "sets_length": sets_length,
            "examples": len(examples),
        }
    return dict(sorted(detail.items()))


def build_plan(profiles: dict[str, Any]) -> dict[str, list[str]]:
    """column-profile map → {generation_type: sorted [columns]}.

    FREE_TEXT splits on ``identifier_shape``: a shaped identifier generates
    from its per-position template and never reaches the LLM (both
    engines); everything else is an LLM free-text pool.
    """
    plan: dict[str, list[str]] = {}
    for name, prof in profiles.items():
        kind = str(prof.kind.value)
        if kind == "free_text":
            label = (
                "freetext_llm_pool"
                if prof.identifier_shape is None
                else "shaped_identifier"
            )
        else:
            label = kind
        plan.setdefault(label, []).append(name)
    return {k: sorted(v) for k, v in sorted(plan.items())}


def log_plan_pretty(
    engine: str,
    ctx: Any,
    profiles: dict[str, Any],
    pool_sources: dict[str, str] | None = None,
) -> None:
    """The two once-per-plan pretty entries (ADR 0028 follow-up).

    Emitted by both engines right after their compact ``generation_plan``
    milestone, under the same once-guard: an indent-2
    ``generation_plan_pretty`` for quick per-column inspection, and a
    ``relational_e2e`` block naming every relational table the run
    touches — landing table, PK, each FK edge with its parent landing
    table and loaded pool size — plus the fetched constraint clauses.
    One glance answers "did the whole relational contract reach this
    run", which the 2026-08-21 job could not (its FK was silently
    inactive)."""
    table = ctx.table_schema.fqn
    prefix = getattr(ctx, "log_table_prefix", "") or ""

    def _q(cols: dict) -> dict:
        # Multi-table runs (ADR 0030): LANDING_NAME.COL keys, so a pasted
        # worker log or _full_report names every column unambiguously and
        # oss/ replacements stay mechanical.
        if not prefix:
            return cols
        return {f"{prefix}.{k}": v for k, v in cols.items()}

    plan_payload: dict[str, Any] = {
        "engine": engine,
        "table": table,
        "plan": build_plan(profiles),
        "columns": _q(build_plan_detail(profiles)),
    }
    if pool_sources:
        plan_payload["pool_sources"] = dict(sorted(pool_sources.items()))
    log_milestone_pretty(
        "generation_plan_pretty", plan_payload, engine=engine, table=table
    )

    fk_pools: dict[str, tuple] = getattr(ctx, "fk_pools", {}) or {}
    edges: list[dict] = list(getattr(ctx, "fk_edges", []) or [])
    if not edges and fk_pools:
        edges = [{"cols": [c]} for c in sorted(fk_pools)]
    # Joint key pools (ADR 0031) are the sampling truth; the per-column
    # projection is only a fallback for a legacy single-column pool. A
    # composite edge MUST report its key-tuple count — the first
    # column's distinct count can be 1 for a pool of a million tuples.
    key_pools: list[dict] = list(getattr(ctx, "fk_key_pools", []) or [])
    tuples_by_cols = {
        tuple(p.get("cols") or ()): len(p.get("keys") or ())
        for p in key_pools
    }
    fk_view = []
    for edge in edges:
        cols = tuple(edge.get("cols") or ())
        key_tuples = tuples_by_cols.get(cols)
        if key_tuples is None:
            pool_size = len(fk_pools.get(cols[0] if cols else "", ()))
            fk_view.append(
                {**edge, "pool_size": pool_size, "active": pool_size > 0}
            )
            continue
        fk_view.append(
            {
                **edge,
                "key_tuples": key_tuples,
                "joint": True,
                "active": key_tuples > 0,
            }
        )
    relational_payload = {
        "source_table": table,
        "landing_table": getattr(ctx, "landing_table", "") or None,
        "pk": list(getattr(ctx, "pk_columns", []) or []),
        "identity": list(getattr(ctx, "identity_columns", []) or []),
        "fk": fk_view,
        "llm_prompt_constraints": _q(build_constraints_detail(profiles)),
    }
    log_milestone_pretty(
        "relational_e2e", relational_payload, engine=engine, table=table
    )
    _log_relationship_card(engine, table, ctx)


def _log_relationship_card(engine: str, table: str, ctx) -> None:
    """The launcher's relationship card, echoed once per plan in the
    WORKER log (ADR 0032).

    Workers are where a run is debugged, and a card the driver rendered
    is the same card — the model was resolved once, from
    `config/relationships/`, and travels as text.
    """
    card = getattr(ctx, "relationship_card", "") or ""
    if not card.strip():
        return
    log_milestone_text(
        "relationship_model", card, engine=engine, table=table
    )


__all__ = [
    "build_constraints_detail",
    "build_plan",
    "build_plan_detail",
    "clear_generation_plan_log",
    "log_plan_pretty",
    "should_log_plan",
]
