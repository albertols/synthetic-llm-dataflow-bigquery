"""Stratification-column selection for the evaluation sample (WS3 §2).

Deliberately independent of the engine-local ColumnKind classifiers
(b1_rag/profile.py, b2_library/fidelity.py) — evaluation must not depend
on whichever engine happened to run.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdfb_core.contracts import TableSchema

_NUMERIC_BQ_TYPES = {"INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC"}


@dataclass(frozen=True)
class StratificationPlan:
    """None column ⇒ unstratified single "__all__" bucket."""

    column: str | None
    values: tuple[object, ...]


def choose_stratification_column(
    table_schema: TableSchema,
    reference_rows: list[dict],
    *,
    min_categories: int = 2,
    max_categories: int = 50,
) -> StratificationPlan:
    """First column (declared schema order) whose reference-sample distinct
    count falls in [min_categories, max_categories] and whose BQ type is not
    numeric. No qualifying column ⇒ the unstratified fallback plan — this
    always terminates with a concrete plan."""
    for field in table_schema.columns:
        if field.bq_type in _NUMERIC_BQ_TYPES or field.is_struct or field.is_repeated:
            continue
        distinct = {
            row.get(field.name)
            for row in reference_rows
            if row.get(field.name) is not None
        }
        if min_categories <= len(distinct) <= max_categories:
            return StratificationPlan(
                column=field.name, values=tuple(sorted(distinct, key=str))
            )
    return StratificationPlan(column=None, values=())


def stratum_key(plan: StratificationPlan, row: dict) -> str:
    return str(row.get(plan.column)) if plan.column else "__all__"
