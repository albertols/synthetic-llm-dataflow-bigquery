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
from sdfb_core.observability import sha12

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


__all__ = [
    "build_constraints_detail",
    "build_plan",
    "build_plan_detail",
    "clear_generation_plan_log",
    "should_log_plan",
]
