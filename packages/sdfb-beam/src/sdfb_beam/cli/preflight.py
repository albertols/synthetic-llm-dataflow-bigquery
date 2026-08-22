"""Driver-side relational preflight — before any Beam graph is built.

The checks are metadata-only (milliseconds, zero DAG cost) and fail with
the exact fix, following the pool-table precedent in ``run_pipeline.py``
(the 2026-07-25 TEST_1 lesson: a cryptic driver NotFound costs a run).

Check ladder (2026-08-05 spec, WS-A A3):
  P1  contract parses + validates          → SystemExit (loud, with snippet)
  P2  contract columns exist in the schema → SystemExit naming them
  P3  FK parents resolved (when a resolver is provided) → SystemExit
  P5  PK tuple unique in the reference sample → WARNING milestone only
      (source data may legitimately violate an undeclared PK)

CLI-provided ``--pk_cols`` / ``--identity_cols`` always win over the
contract; the override is logged, never silent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sdfb_core.contracts.description_json import DescriptionJsonError
from sdfb_core.contracts.prompt_constraint import parse_prompt_constraint
from sdfb_core.contracts.relational import parse_llm_prompt_constraint
from sdfb_core.engines.constraint_sampler import compile_pattern_sampler
from sdfb_core.engines.generation_plan import FREE_TEXT_POOL_MAX
from sdfb_core.observability import log_milestone, sha12

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sdfb_core.contracts import TableSchema
    from sdfb_core.contracts.relational import RelationalContract


@dataclass(frozen=True)
class PreflightResult:
    pk_cols: tuple[str, ...]
    identity_cols: tuple[str, ...]
    contract: RelationalContract | None
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


def _check_pk_capacity(
    table_schema: TableSchema, effective_pk: tuple[str, ...], num_rows: int
) -> None:
    """P4 (ADR 0028) — every constrained PK column's routed generator
    must cover ``num_rows`` unique values.

    A constrained column with no samplable ``pattern`` draws from a pool
    capped at ``FREE_TEXT_POOL_MAX``; the 2026-08-21 run declared such a
    column as PK at 1M rows and DLQ'd 999 488 of them — 37 minutes and
    1 586 GPU-s after a check that costs microseconds here.
    Unconstrained PK columns keep their existing (unbounded) routes and
    are not judged.
    """
    by_name = {c.name: c for c in table_schema.columns}
    for col in effective_pk:
        field = by_name.get(col)
        if field is None:
            continue
        pc = parse_prompt_constraint(field.description, column=col)
        if pc is None:
            continue
        sampler = (
            compile_pattern_sampler(pc.pattern, families=pc.families)
            if pc.pattern
            else None
        )
        capacity = sampler.capacity if sampler is not None else FREE_TEXT_POOL_MAX
        if capacity < num_rows:
            vehicle = (
                f"pattern {pc.pattern!r} (capacity {capacity:.2e})"
                if sampler is not None
                else f"a bounded LLM pool (cap {FREE_TEXT_POOL_MAX})"
            )
            raise SystemExit(
                f"[preflight P4] {table_schema.fqn}.{col}: declared PK "
                f"carries an llm_prompt_constraint whose generator is "
                f"{vehicle} — it cannot produce {num_rows} unique values "
                f"and every excess row would be a pk.duplicate BLOCKER. "
                f"Add a samplable 'pattern' with capacity >= num_rows, or "
                f"remove the constraint from the PK column."
            )


def _check_fk_activation(
    table_schema: TableSchema,
    contract: RelationalContract,
    fk_parent_landing: str,
) -> None:
    """P6 (ADR 0028) — a declared FK is resolved or loudly refused.

    The 2026-08-21 run declared an FK and launched without
    ``--fk_parent_landing``: pools silently never loaded, zero
    ``fk.orphan`` evaluations, and '0 orphans' read as a pass."""
    if not contract.fk:
        return
    if fk_parent_landing == "skip":
        log_milestone(
            "fk_declared_skipped",
            level=logging.WARNING,
            table=table_schema.fqn,
            fk_refs=",".join(fk.ref for fk in contract.fk),
            note="contract declares FK edges but --fk_parent_landing=skip "
            "was passed — FK columns generate from marginals, referential "
            "integrity UNVERIFIED this run",
        )
        return
    if not fk_parent_landing:
        refs = sorted(fk.ref for fk in contract.fk)
        raise SystemExit(
            f"[preflight P6] {table_schema.fqn}: the contract declares FK "
            f"edges to {refs} but --fk_parent_landing was not passed — the "
            f"run would silently generate FK columns from marginals with "
            f"no fk.orphan check. Pass "
            f"--fk_parent_landing=<project.landing_dataset> (parents must "
            f"be landed first) or explicitly --fk_parent_landing=skip."
        )


def preflight(
    table_schema: TableSchema,
    pk_cols: tuple[str, ...],
    identity_cols: tuple[str, ...],
    reference_rows: list[dict],
    fk_parents_resolved: dict[str, bool] | None = None,
    prompt_constraints_enabled: bool = True,
    num_rows: int = 0,
    fk_parent_landing: str | None = None,
) -> PreflightResult:
    """Run P1-P6; returns the effective pk/identity columns.

    ``num_rows`` > 0 arms the P4 PK-capacity check; ``fk_parent_landing``
    not-None arms the P6 FK-activation check (pass the CLI value
    verbatim, "" included)."""
    warnings: list[str] = []
    fqn = table_schema.fqn
    _report_prompt_constraints(table_schema, prompt_constraints_enabled)

    # P1 — parse. A marked-but-invalid contract is a stop, not a warning.
    try:
        contract = table_schema.relational_contract()
    except DescriptionJsonError as exc:
        raise SystemExit(
            f"[preflight P1] {fqn}: the table description carries an "
            f"'sdfb'-marked JSON object that does not validate.\n{exc}\n"
            f"Fix the Terraform description (use jsonencode) or remove the "
            f"marker."
        ) from exc

    if contract is None:
        log_milestone("relational_contract_absent", table=fqn)
        if num_rows > 0 and pk_cols:
            _check_pk_capacity(table_schema, pk_cols, num_rows)
        return PreflightResult(pk_cols, identity_cols, None, warnings)

    # P2 — every contract column must exist in the schema.
    valid = {c.name for c in table_schema.columns}
    fk_cols = tuple(c for fk in contract.fk for c in fk.cols)
    for label, cols in (
        ("pk", contract.pk),
        ("identity", contract.identity),
        ("fk.cols", fk_cols),
    ):
        missing = _missing(cols, valid)
        if missing:
            raise SystemExit(
                f"[preflight P2] {fqn}: contract {label} references unknown "
                f"columns {missing}. Schema columns: {sorted(valid)}"
            )

    # P3 — FK closure, when the caller resolved parents (multi-table runs).
    if fk_parents_resolved is not None and contract.fk:
        unresolved = sorted(
            {fk.ref for fk in contract.fk if not fk_parents_resolved.get(fk.ref)}
        )
        if unresolved:
            raise SystemExit(
                f"[preflight P3] {fqn}: FK parents not resolved: {unresolved}. "
                f"Generate parents first (run_tableset orders this) or land "
                f"their synthetic tables before this run."
            )

    # CLI wins; the contract fills the gaps.
    effective_pk = pk_cols or contract.pk
    effective_identity = identity_cols or contract.identity
    if pk_cols and contract.pk and tuple(pk_cols) != contract.pk:
        warnings.append(
            f"--pk_cols {list(pk_cols)} overrides contract pk {list(contract.pk)}"
        )
        log_milestone(
            "relational_contract_overridden",
            level=logging.WARNING,
            table=fqn,
            cli_pk=",".join(pk_cols),
            contract_pk=",".join(contract.pk),
        )

    # P5 — PK sanity against the reference sample (warning only).
    if effective_pk and reference_rows:
        tuples = {
            tuple(r.get(c) for c in effective_pk) for r in reference_rows
        }
        if len(tuples) < len(reference_rows):
            dupes = len(reference_rows) - len(tuples)
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

    # P4 — PK generation capacity; P6 — FK activation (ADR 0028).
    if num_rows > 0 and effective_pk:
        _check_pk_capacity(table_schema, tuple(effective_pk), num_rows)
    if fk_parent_landing is not None:
        _check_fk_activation(table_schema, contract, fk_parent_landing)

    log_milestone(
        "relational_contract_loaded",
        table=fqn,
        pk=",".join(contract.pk),
        fk_count=len(contract.fk),
        identity=",".join(contract.identity),
    )
    return PreflightResult(
        tuple(effective_pk), tuple(effective_identity), contract, warnings
    )


__all__ = ["PreflightResult", "preflight"]
