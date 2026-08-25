"""Driver-side relational preflight — before any Beam graph is built.

The checks are metadata-only (milliseconds, zero DAG cost) and fail with
the exact fix, following the pool-table precedent in ``run_pipeline.py``
(the 2026-07-25 TEST_1 lesson: a cryptic driver NotFound costs a run).

Check ladder (2026-08-05 spec, WS-A A3; relational input per ADR 0032):
  P2  model columns exist in the schema    → SystemExit naming them
  P3  FK parents resolved (when a resolver is provided) → SystemExit
  P4  the PK's generator can cover num_rows → SystemExit (ADR 0028)
  P5  PK tuple unique in the reference sample → WARNING milestone only
      (source data may legitimately violate an undeclared PK)

The relational input is this table's entry in `config/relationships/`
(ADR 0032) — the model file is the source of truth, so its PK/identity
win and a conflicting ``--pk_cols`` is loudly IGNORED. Tables no model
declares fall back to the CLI flags, which is how a one-off table with
no relationships generates with zero config.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sdfb_core.contracts.description_json import DescriptionJsonError
from sdfb_core.contracts.prompt_constraint import (
    parse_llm_prompt_constraint,
    parse_prompt_constraint,
    render_prompt_clause,
)
from sdfb_core.engines.b1_rag.profile import profile_columns
from sdfb_core.engines.constraint_sampler import compile_pattern_sampler
from sdfb_core.engines.generation_plan import FREE_TEXT_POOL_MAX
from sdfb_core.engines.text_shapes import is_binary_class
from sdfb_core.observability import (
    log_milestone,
    log_milestone_pretty,
    sha12,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sdfb_core.contracts import TableSchema
    from sdfb_core.contracts.relationships import TableRelations


@dataclass(frozen=True)
class PreflightResult:
    pk_cols: tuple[str, ...]
    identity_cols: tuple[str, ...]
    # This table's entry in `config/relationships/` (ADR 0032), or None
    # when no model declares it — then PK/identity come from the CLI.
    relations: TableRelations | None
    warnings: list[str] = field(default_factory=list)


def _missing(cols: tuple[str, ...], valid: set[str]) -> list[str]:
    return [c for c in cols if c not in valid]


def _report_prompt_constraints(
    table_schema: TableSchema, enabled: bool
) -> None:
    """Say aloud whether any column carries an `llm_prompt_constraint`.

    C5 is prompt refinement, never a requirement: `--prompt_constraints=on`
    against a DDL with no constraints anywhere is a perfectly healthy
    no-op — but a silent one reads as 'did it even look?'. One informative
    milestone answers that. A column whose description carries a MARKED but
    unparseable constraint object stops loudly (the P1 posture, per column).
    """
    found: dict[str, dict] = {}
    for col in table_schema.columns:
        try:
            clause = parse_llm_prompt_constraint(
                col.description, column=col.name
            )
        except DescriptionJsonError as exc:
            raise SystemExit(
                f"[preflight P1] {table_schema.fqn}.{col.name}: column "
                f"description carries an 'llm_prompt_constraint'-marked JSON "
                f"object that does not parse.\n{exc}"
            ) from exc
        if clause:
            # The fetched clause travels with the milestone (2026-08-20
            # follow-up): a Terraform edit is verifiable from logs by
            # clause text or by diffing clause_sha12 across launches.
            found[col.name] = {
                "clause": clause,
                "clause_sha12": sha12(clause),
                "chars": len(clause),
            }
    if found:
        log_milestone(
            "prompt_constraints_found",
            columns=",".join(found),
            count=len(found),
            enabled=enabled,
            detail=json.dumps(found, separators=(",", ":")),
        )
    elif enabled:
        log_milestone(
            "prompt_constraints_none",
            note="no column description carries llm_prompt_constraint — "
            "--prompt_constraints=on is a no-op, generation unchanged",
        )


# Column types that route through the (capped) free-text machinery —
# everything else keeps its typed route and never touches a pool
# (ADR 0024; mirrors b1_rag/profile._STRINGY_BQ_TYPES).
_POOL_ROUTED_BQ_TYPES = frozenset({"STRING", "JSON", "GEOGRAPHY", "BYTES"})


def _pk_capacity_factor(field, pc) -> int | None:
    """One PK member's unique-value capacity; None = unbounded.

    Unbounded: no constraint, or a non-STRING type (numeric / temporal /
    etc. keep their typed generators — a cosmetic ``examples`` clause on
    a NUMERIC branch code must not read as a 512-value pool, the
    2026-08-22 A_TABLE false stop). Bounded: an enum ``values`` clause
    (its domain), a samplable ``pattern`` (its language), else the
    ``FREE_TEXT_POOL_MAX`` pool cap."""
    if pc is None:
        return None
    if pc.values:
        return len(pc.values)
    if (
        field.bq_type not in _POOL_ROUTED_BQ_TYPES
        or field.is_struct
        or field.is_repeated
    ):
        return None
    if pc.pattern:
        sampler = compile_pattern_sampler(pc.pattern, families=pc.families)
        if sampler is not None:
            return sampler.capacity
    return FREE_TEXT_POOL_MAX


def _check_pk_capacity(
    table_schema: TableSchema, effective_pk: tuple[str, ...], num_rows: int
) -> None:
    """P4 (ADR 0028, product-aware since ADR 0030 follow-up) — the PK
    TUPLE's generator capacity must cover ``num_rows`` unique values.

    Tuple capacity is the PRODUCT of per-member factors
    (`_pk_capacity_factor`); any unbounded member passes the whole
    tuple. The original per-member rule falsely stopped the 2026-08-22
    single-job launch on a 5-column composite PK whose only constrained
    member was a numeric code — while the 2026-08-21 disaster this
    check exists for (a single capped-pool PK at 1M rows) still stops
    exactly as before."""
    by_name = {c.name: c for c in table_schema.columns}
    factors: dict[str, int] = {}
    product = 1
    for col in effective_pk:
        field = by_name.get(col)
        if field is None:
            continue
        pc = parse_prompt_constraint(field.description, column=col)
        factor = _pk_capacity_factor(field, pc)
        if factor is None:
            return  # one unbounded member covers the tuple
        factors[col] = factor
        product *= factor
    if factors and product < num_rows:
        detail = ", ".join(f"{c}={f}" for c, f in factors.items())
        raise SystemExit(
            f"[preflight P4] {table_schema.fqn}: the declared PK tuple "
            f"{list(effective_pk)} has a bounded generator capacity of "
            f"{product} ({detail}) < num_rows={num_rows} — every excess "
            f"row would be a pk.duplicate BLOCKER. Give a PK member a "
            f"samplable 'pattern' with enough capacity, or remove the "
            f"constraint from one member so its typed route stays "
            f"unbounded."
        )


def _constraint_vehicle(prof, field) -> str:
    """What this clause ACTUALLY drives (2026-08-22 operator ask): a
    clause is a generation vehicle only on the free-text path — forced
    by route:'llm' or reached by natural free-text classification.
    Everywhere else it is prompt steering at most, and the log says so
    instead of leaving the operator to infer it."""
    stringy = (
        field.bq_type in _POOL_ROUTED_BQ_TYPES
        and not field.is_struct
        and not field.is_repeated
    )
    if not stringy:
        return (
            f"{prof.kind.value} typed route — a clause on a non-STRING "
            f"column is NEVER a generation vehicle "
            f"(prompt_constraint_route_unsupported); it will not build a "
            f"freetext pool, RAG chunks or retrieval"
        )
    if prof.kind.value == "free_text":
        if prof.identifier_shape is not None:
            return "shaped_identifier template (no LLM)"
        if is_binary_class(prof.observed_values):
            return (
                "byte_template (Tier B, ADR 0028 — no LLM, never "
                "source values)"
            )
        if prof.constraint_pattern and compile_pattern_sampler(
            prof.constraint_pattern,
            families=getattr(prof, "constraint_families", ()),
        ):
            return (
                "pattern_sampler (Tier P, ADR 0028 — no LLM, "
                "unlimited uniques)"
            )
        return (
            "freetext_llm_pool (LLM pool + RAG retrieval; the clause "
            "pins the pool prompt, ADR 0024/0026)"
        )
    return (
        f"{prof.kind.value} typed route — the clause steers prompts "
        f"ONLY on the free-text path; add route:'llm' to force this "
        f"STRING column onto freetext_llm_pool/RAG"
    )


def _report_constraint_vehicles(
    table_schema: TableSchema, reference_rows: list[dict]
) -> None:
    """One pretty block per table: every constrained column, keyed
    TABLE.COL, with its clause, its declared ``route`` and the RESOLVED
    generation vehicle (from the real profiler over the reference
    sample) — visible at preflight, before any worker exists."""
    if not reference_rows:
        return
    profiles = profile_columns(table_schema, reference_rows)
    name = table_schema.fqn.rsplit(".", 1)[-1]
    payload: dict[str, dict] = {}
    for col in table_schema.columns:
        pc = parse_prompt_constraint(col.description, column=col.name)
        if pc is None:
            continue
        prof = profiles.get(col.name)
        if prof is None:
            continue
        clause = render_prompt_clause(pc)
        payload[f"{name}.{col.name}"] = {
            "route": pc.route,
            "vehicle": _constraint_vehicle(prof, col),
            "clause": clause,
            "clause_sha12": sha12(clause),
            "pattern": bool(pc.pattern),
            "values": len(pc.values),
            "examples": len(pc.examples),
            "length": list(pc.length) if pc.length else None,
        }
    if payload:
        log_milestone_pretty(
            "prompt_constraints_pretty",
            payload,
            table=name,
            count=len(payload),
        )


# Above this share of duplicate PK tuples in the reference sample, the
# declared PK is not a key of the data at all — it is a typo or a missing
# column, not a data-quality dent. Generation samples those same
# marginals, so almost every row will collide and divert as
# `pk.duplicate` (2026-08-25: 99.4% duplicates in the sample ->
# 999 926 of 1 000 000 rows DLQ'd, BLOCKER gate tripped).
_PK_NOT_A_KEY_RATIO = 0.5


def _check_pk_is_a_key(
    fqn: str,
    pk: tuple[str, ...],
    distinct: int,
    sample_rows: int,
    num_rows: int,
) -> None:
    """Stop when the sample proves the run cannot fill ``num_rows``.

    A key that repeats on most of its own source rows cannot key a
    larger synthetic table: the run lands about as many rows as the
    tuple has distinct values and diverts the rest. Cheap to see here,
    expensive to discover at the gate.
    """
    duplicate_ratio = 1 - distinct / sample_rows
    if (
        num_rows <= 0
        or duplicate_ratio < _PK_NOT_A_KEY_RATIO
        or num_rows <= distinct
    ):
        return
    raise SystemExit(
        f"[preflight P5] {fqn}: the declared PK {list(pk)} is not a key of "
        f"this data — only {distinct:,} distinct tuples in {sample_rows:,} "
        f"sample rows ({duplicate_ratio:.1%} duplicates). Generation draws "
        f"the same marginals, so a {num_rows:,}-row run would land on the "
        f"order of {distinct:,} rows and divert the rest as pk.duplicate, "
        f"tripping the BLOCKER gate. Fix one of: the `pk:` in the "
        f"relationship model (add the column that discriminates rows), the "
        f"row count (--num_rows <= the real key space), or move the column "
        f"to `identity:` if it was never meant to be a key."
    )


def preflight(
    table_schema: TableSchema,
    pk_cols: tuple[str, ...],
    identity_cols: tuple[str, ...],
    reference_rows: list[dict],
    relations: TableRelations | None = None,
    fk_parents_resolved: dict[str, bool] | None = None,
    prompt_constraints_enabled: bool = True,
    num_rows: int = 0,
) -> PreflightResult:
    """Run P1-P5 + P4; returns the effective pk/identity columns.

    ``num_rows`` > 0 arms the P4 PK-capacity check. FK activation is no
    longer a preflight concern (ADR 0029 rev B): fk_parent_landing
    derives from the landing table, and an unlanded/empty parent stops
    loudly at pool-load time instead."""
    warnings: list[str] = []
    fqn = table_schema.fqn
    _report_prompt_constraints(table_schema, prompt_constraints_enabled)
    _report_constraint_vehicles(table_schema, reference_rows)

    if relations is None:
        log_milestone("relationships_absent_for_table", table=fqn)
        if num_rows > 0 and pk_cols:
            _check_pk_capacity(table_schema, pk_cols, num_rows)
        return PreflightResult(pk_cols, identity_cols, None, warnings)

    # P2 — every column the model names must exist in the schema. This is
    # where a typo in `config/relationships/*.yaml` stops the launch, at
    # the cost of one comparison, instead of generating the wrong shape.
    valid = {c.name for c in table_schema.columns}
    # Documented edges (`enforced: false`) describe a relationship whose
    # join key need not be in the DDL — they never draw keys, so their
    # columns are exempt by definition.
    enforced_fk = tuple(fk for fk in relations.fk if fk.enforced)
    fk_cols = tuple(c for fk in enforced_fk for c in fk.cols)
    for label, cols in (
        ("pk", relations.pk),
        ("identity", relations.identity),
        ("fk.cols", fk_cols),
    ):
        missing = _missing(cols, valid)
        if missing:
            raise SystemExit(
                f"[preflight P2] {fqn}: the relationship model's {label} "
                f"names unknown columns {missing}. Fix the model file (or "
                f"the table). Schema columns: {sorted(valid)}"
            )

    # P3 — FK closure, when the caller resolved parents (multi-table runs).
    if fk_parents_resolved is not None and enforced_fk:
        unresolved = sorted(
            {fk.ref for fk in enforced_fk if not fk_parents_resolved.get(fk.ref)}
        )
        if unresolved:
            raise SystemExit(
                f"[preflight P3] {fqn}: FK parents not resolved: {unresolved}. "
                f"Generate parents first (run_tableset orders this) or land "
                f"their synthetic tables before this run."
            )

    # The model is the source of truth; CLI flags fill the gaps it leaves.
    effective_pk = relations.pk or pk_cols
    effective_identity = relations.identity or identity_cols
    if pk_cols and relations.pk and tuple(pk_cols) != relations.pk:
        warnings.append(
            f"--pk_cols {list(pk_cols)} IGNORED — the relationship model "
            f"declares pk {list(relations.pk)} and is the source of truth"
        )
        log_milestone(
            "relationships_pk_override_ignored",
            level=logging.WARNING,
            table=fqn,
            cli_pk=",".join(pk_cols),
            model_pk=",".join(relations.pk),
        )

    # P5 — is the declared PK actually a key of this data?
    if effective_pk and reference_rows:
        tuples = {
            tuple(r.get(c) for c in effective_pk) for r in reference_rows
        }
        if len(tuples) < len(reference_rows):
            dupes = len(reference_rows) - len(tuples)
            _check_pk_is_a_key(
                fqn, effective_pk, len(tuples), len(reference_rows), num_rows
            )
            warnings.append(
                f"PK {list(effective_pk)} not unique in the reference sample "
                f"({dupes} duplicate tuples of {len(reference_rows)} rows)"
            )
            log_milestone(
                "preflight_pk_not_unique_in_sample",
                level=logging.WARNING,
                table=fqn,
                pk=",".join(effective_pk),
                duplicates=dupes,
                sample_rows=len(reference_rows),
            )

    # P4 — PK generation capacity (ADR 0028).
    if num_rows > 0 and effective_pk:
        _check_pk_capacity(table_schema, tuple(effective_pk), num_rows)

    log_milestone(
        "relations_loaded",
        table=fqn,
        pk=",".join(relations.pk),
        fk_count=len(relations.fk),
        enforced_fk=len(enforced_fk),
        identity=",".join(relations.identity),
        enabled=relations.enabled,
    )
    return PreflightResult(
        tuple(effective_pk), tuple(effective_identity), relations, warnings
    )


__all__ = ["PreflightResult", "preflight"]
