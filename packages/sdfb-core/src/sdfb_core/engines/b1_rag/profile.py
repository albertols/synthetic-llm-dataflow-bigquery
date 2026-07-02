"""Column profiling for the distribution-estimator spine.

Profiles each schema column from the reference rows into one of four kinds
(see `ColumnKind`). Profiling is the cheap O(N_ref) pass that decides which
fidelity primitive samples the column at `generate_batch` time:

  - CONSTANT     → literal copy (``nunique() == 1``); never sent to the LLM.
  - NUMERIC      → clip-to-observed-range empirical sampling.
  - CATEGORICAL  → empirical-frequency sampling from the observed value set.
  - FREE_TEXT    → bounded LLM-generated pool, sampled with replacement.

Pure-Python (stdlib only) so it lives in `sdfb-core`. Numeric stats are
computed without NumPy here; the vectorized *sampling* (in the engine) is
where NumPy is used and deferred-imported.

REF: spec §2 fidelity primitives; ADR 0013 distribution-estimator spine.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sdfb_core.contracts import FieldSchema, TableSchema

# BQ types that are inherently numeric (sampled by range, not by category).
_NUMERIC_BQ_TYPES = frozenset(
    {"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
)
# BQ types whose Python value is a string we might treat as free text.
_STRINGY_BQ_TYPES = frozenset({"STRING", "JSON", "GEOGRAPHY", "BYTES"})

# A string column with more unique non-null values than this *fraction* of
# its non-null count, AND a mean rendered length above the threshold, is
# treated as FREE_TEXT rather than CATEGORICAL. Tuned so low-cardinality
# enums (country, tier) stay categorical while names / descriptions /
# free-form notes go to the LLM pool.
_FREE_TEXT_UNIQUE_RATIO = 0.9
_FREE_TEXT_MIN_MEAN_LEN = 20
# Above this absolute distinct count a string column is high-cardinality and
# treated as free text even if short (e.g. emails, ids-as-strings).
_FREE_TEXT_MAX_CATEGORIES = 50


class ColumnKind(StrEnum):
    CONSTANT = "constant"
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    FREE_TEXT = "free_text"


@dataclass(frozen=True)
class ColumnProfile:
    """Per-column statistics derived from the reference sample.

    Engine-local (kept under `b1_rag/` per the spec's "fidelity helpers
    local during parallel dev" guidance).
    """

    name: str
    bq_type: str
    kind: ColumnKind
    nullable: bool
    null_fraction: float
    # CONSTANT
    constant_value: object | None = None
    # NUMERIC — observed bounds + whether values are integral.
    numeric_min: float | None = None
    numeric_max: float | None = None
    is_integral: bool = False
    is_decimal: bool = False
    # NUMERIC/BIGNUMERIC decimal scale from the DDL (places to round to).
    decimal_scale: int | None = None
    # CATEGORICAL — value → count (insertion order = first-seen, for determinism).
    categories: dict[object, int] = field(default_factory=dict)
    # FREE_TEXT — a deduped sample of observed values (the retrieval seed pool).
    text_examples: tuple[str, ...] = ()
    # All non-null observed values, original order — used for sampling fallbacks.
    observed_values: tuple[object, ...] = ()


def profile_columns(
    table_schema: TableSchema, reference_rows: list[dict]
) -> dict[str, ColumnProfile]:
    """Profile every top-level column. Returns name → `ColumnProfile`.

    STRUCT/RECORD and REPEATED columns are profiled as CATEGORICAL over
    their JSON-rendered values (M1 is single-table, nested handling stays
    coarse; deep nested synthesis is M2+).
    """
    profiles: dict[str, ColumnProfile] = {}
    for col in table_schema.columns:
        values = [r.get(col.name, None) for r in reference_rows]
        profiles[col.name] = _profile_one(col, values)
    return profiles


def _profile_one(col: FieldSchema, values: list[object]) -> ColumnProfile:
    n = len(values)
    non_null = [v for v in values if v is not None]
    null_fraction = (n - len(non_null)) / n if n else 0.0
    nullable = col.is_nullable

    distinct = _ordered_distinct(non_null)

    # CONSTANT: exactly one distinct non-null value and no nulls observed.
    if len(distinct) == 1 and not (n - len(non_null)):
        return ColumnProfile(
            name=col.name,
            bq_type=col.bq_type,
            kind=ColumnKind.CONSTANT,
            nullable=nullable,
            null_fraction=0.0,
            constant_value=distinct[0],
            observed_values=tuple(non_null),
        )

    if col.bq_type in _NUMERIC_BQ_TYPES:
        return _profile_numeric(col, non_null, nullable, null_fraction)

    if col.is_struct or col.is_repeated:
        return _profile_categorical(
            col, non_null, nullable, null_fraction, render=_json_render
        )

    if col.bq_type in _STRINGY_BQ_TYPES:
        return _profile_string(col, non_null, nullable, null_fraction)

    # BOOL, DATE, DATETIME, TIME, TIMESTAMP → categorical over observed values.
    return _profile_categorical(col, non_null, nullable, null_fraction)


def _profile_numeric(
    col: FieldSchema,
    non_null: list[object],
    nullable: bool,
    null_fraction: float,
) -> ColumnProfile:
    numbers: list[float] = []
    integral = True
    is_decimal = col.bq_type in {"NUMERIC", "BIGNUMERIC"}
    for v in non_null:
        f = _to_float(v)
        if f is None:
            continue
        numbers.append(f)
        if f != int(f):
            integral = False
    if not numbers:
        # No parseable numbers (all null / unparseable) — treat as categorical.
        return _profile_categorical(col, non_null, nullable, null_fraction)
    # Use the DDL scale when present; default to 2 places for NUMERIC currency.
    decimal_scale = col.scale if (is_decimal and col.scale is not None) else (2 if is_decimal else None)
    return ColumnProfile(
        name=col.name,
        bq_type=col.bq_type,
        kind=ColumnKind.NUMERIC,
        nullable=nullable,
        null_fraction=null_fraction,
        numeric_min=min(numbers),
        numeric_max=max(numbers),
        is_integral=integral and col.bq_type in {"INTEGER", "INT64"},
        is_decimal=is_decimal,
        decimal_scale=decimal_scale,
        observed_values=tuple(non_null),
    )


def _profile_string(
    col: FieldSchema,
    non_null: list[object],
    nullable: bool,
    null_fraction: float,
) -> ColumnProfile:
    strings = [str(v) for v in non_null]
    distinct = _ordered_distinct(strings)
    n = len(strings)
    unique_ratio = (len(distinct) / n) if n else 0.0
    mean_len = (sum(len(s) for s in strings) / n) if n else 0.0

    is_free_text = (
        len(distinct) > _FREE_TEXT_MAX_CATEGORIES
        or (unique_ratio >= _FREE_TEXT_UNIQUE_RATIO and mean_len >= _FREE_TEXT_MIN_MEAN_LEN)
    )
    if is_free_text:
        # Cap the seed pool — exemplars condition the LLM, they aren't the bulk.
        examples = tuple(distinct[:64])
        return ColumnProfile(
            name=col.name,
            bq_type=col.bq_type,
            kind=ColumnKind.FREE_TEXT,
            nullable=nullable,
            null_fraction=null_fraction,
            text_examples=examples,
            observed_values=tuple(strings),
        )
    return _profile_categorical(col, non_null, nullable, null_fraction)


def _profile_categorical(
    col: FieldSchema,
    non_null: list[object],
    nullable: bool,
    null_fraction: float,
    render=None,
) -> ColumnProfile:
    counter: Counter = Counter()
    # Preserve first-seen order for determinism (Counter keeps insertion order).
    keyed: list[object] = []
    for v in non_null:
        key = render(v) if render is not None else v
        keyed.append(key)
        counter[_hashable(key)] += 1
    # Rebuild an ordered dict keyed by the original (hashable) values.
    categories: dict[object, int] = {}
    for key in keyed:
        h = _hashable(key)
        if h not in categories:
            categories[h] = counter[h]
    return ColumnProfile(
        name=col.name,
        bq_type=col.bq_type,
        kind=ColumnKind.CATEGORICAL,
        nullable=nullable,
        null_fraction=null_fraction,
        categories=categories,
        observed_values=tuple(_hashable(k) for k in keyed),
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _ordered_distinct(values: list) -> list:
    seen: dict = {}
    for v in values:
        h = _hashable(v)
        if h not in seen:
            seen[h] = None
    return list(seen.keys())


def _hashable(v: object) -> object:
    """Coerce unhashable JSON values (lists/dicts) to a stable string key."""
    if isinstance(v, (list, dict)):
        return _json_render(v)
    return v


def _json_render(v: object) -> str:
    import json

    return json.dumps(v, sort_keys=True, default=str)


def _to_float(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        try:
            return float(Decimal(v))
        except (InvalidOperation, ValueError):
            return None
    return None


__all__ = ["ColumnKind", "ColumnProfile", "profile_columns"]
