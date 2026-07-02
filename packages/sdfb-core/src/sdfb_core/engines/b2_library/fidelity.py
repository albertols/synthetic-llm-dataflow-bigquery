"""Column profiling + post-hoc fidelity enforcement, LOCAL to B.2.

This is the FASTGEN-spine fidelity layer (ADR 0013 / design §2, §4): we
classify each column once over ``reference_rows`` and then clamp sampled
values back into the observed support. ``sdgx``'s constraint/metadata API
is thinner than SDV's, so range/enum/constant enforcement lives here as
defense-in-depth on top of the Mode-A Pandera contract.

Kept local to ``engines/b2_library/`` during parallel development to avoid
a shared-file merge conflict with B.1; consolidate to a shared
``engines/_fidelity.py`` post-merge if duplication warrants (design §2).

Pure Python + NumPy only — no Beam, no GCP, no torch, no sdgx. Importing
this module must succeed with only ``sdfb-core``'s base deps present.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from sdfb_core.contracts.schema import FieldSchema, TableSchema

# Free-text heuristics. A STRING column is routed to the LLM free-text hook
# when the LLM can plausibly do better than empirical resampling: either the
# values look like prose/JSON, or the column is so high-cardinality that
# resampling the reference pool would leak/duplicate near-unique values.
_HIGH_CARDINALITY_RATIO = 0.9  # distinct / non-null count above this ⇒ free-text
_FREE_TEXT_MIN_LEN = 40  # mean string length above this ⇒ likely prose


class ColumnKind(StrEnum):
    """How a column is synthesized in B.2.

    - ``CONSTANT``: a single observed value across the reference → copied.
    - ``NUMERIC``: int/float/decimal → sampled then clipped to observed range.
    - ``CATEGORICAL``: low-cardinality discrete → empirical-frequency sample.
    - ``FREE_TEXT``: prose / JSON / very-high-cardinality string → LLM hook.
    """

    CONSTANT = "constant"
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    FREE_TEXT = "free_text"


_NUMERIC_BQ_TYPES = frozenset(
    {"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
)
_STRINGY_BQ_TYPES = frozenset({"STRING", "JSON", "GEOGRAPHY", "BYTES"})


@dataclass(frozen=True)
class ColumnProfile:
    """The fitted-once distribution metadata for one column.

    Carries everything ``generate_batch`` needs to enforce fidelity
    without re-reading the reference rows. Frozen + plain-data so it
    pickles cleanly across the Beam worker boundary alongside the engine.
    """

    name: str
    bq_type: str
    kind: ColumnKind
    nullable: bool
    null_fraction: float = 0.0
    # CONSTANT
    constant_value: object | None = None
    # NUMERIC observed bounds (inclusive); None when no non-null samples.
    minimum: float | None = None
    maximum: float | None = None
    is_integer: bool = False
    # NUMERIC / BIGNUMERIC decimal scale (places after the point) — sampled
    # values are rounded to this so they satisfy the record model's
    # `decimal_places` constraint. None ⇒ no rounding (FLOAT/INT).
    decimal_scale: int | None = None
    # CATEGORICAL empirical distribution: parallel value/weight lists.
    categories: tuple[object, ...] = ()
    weights: tuple[float, ...] = ()
    # FREE_TEXT: the observed pool (deduped, order-stable) the LLM hook
    # conditions on / falls back to.
    text_pool: tuple[str, ...] = ()


def _non_null(values: list[object]) -> list[object]:
    return [v for v in values if v is not None]


def _column_values(reference_rows: list[dict], name: str) -> list[object]:
    return [row.get(name) for row in reference_rows]


def _classify(  # noqa: PLR0911 — type classifier; sequential returns read clearer than nesting
    field: FieldSchema,
    non_null_values: list[object],
) -> ColumnKind:
    """Assign a :class:`ColumnKind` from the schema type + observed values."""
    distinct = len({_hashable(v) for v in non_null_values})

    if distinct <= 1:
        return ColumnKind.CONSTANT

    if field.bq_type in _NUMERIC_BQ_TYPES:
        return ColumnKind.NUMERIC

    if field.bq_type in {"BOOLEAN", "BOOL"}:
        return ColumnKind.CATEGORICAL

    if field.bq_type in _STRINGY_BQ_TYPES:
        # JSON columns always go to the free-text hook — empirical resampling
        # of structured blobs is meaningless.
        if field.bq_type == "JSON":
            return ColumnKind.FREE_TEXT
        strs = [str(v) for v in non_null_values]
        cardinality_ratio = distinct / max(len(strs), 1)
        mean_len = sum(len(s) for s in strs) / max(len(strs), 1)
        if cardinality_ratio >= _HIGH_CARDINALITY_RATIO or mean_len >= _FREE_TEXT_MIN_LEN:
            return ColumnKind.FREE_TEXT
        return ColumnKind.CATEGORICAL

    # DATE/TIME/TIMESTAMP and anything else: treat as categorical over the
    # observed pool (empirical resampling keeps values in-support; numeric
    # interpolation of timestamps is out of M1 scope).
    return ColumnKind.CATEGORICAL


def _hashable(value: object) -> object:
    """Coerce to a hashable key for distinct-counting (dicts/lists → repr)."""
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def profile_column(field: FieldSchema, reference_rows: list[dict]) -> ColumnProfile:
    """Profile a single column over the reference rows (the O(1) fit step)."""
    raw = _column_values(reference_rows, field.name)
    non_null = _non_null(raw)
    total = len(raw)
    null_fraction = (total - len(non_null)) / total if total else 0.0
    nullable = field.is_nullable

    # No non-null observations: emit a CONSTANT-None / empty profile. The
    # record model supplies the schema default (None for NULLABLE).
    if not non_null:
        return ColumnProfile(
            name=field.name,
            bq_type=field.bq_type,
            kind=ColumnKind.CONSTANT,
            nullable=nullable,
            null_fraction=null_fraction,
            constant_value=None,
        )

    kind = _classify(field, non_null)

    if kind is ColumnKind.CONSTANT:
        return ColumnProfile(
            name=field.name,
            bq_type=field.bq_type,
            kind=kind,
            nullable=nullable,
            null_fraction=null_fraction,
            constant_value=non_null[0],
        )

    if kind is ColumnKind.NUMERIC:
        nums = [float(v) for v in non_null]
        is_int = field.bq_type in {"INTEGER", "INT64"}
        # NUMERIC/BIGNUMERIC: respect the declared scale (default 2 places
        # for fixed-point money-like columns) so sampled values pass the
        # record model's `decimal_places` constraint. FLOAT has no scale.
        decimal_scale: int | None = None
        if field.bq_type in {"NUMERIC", "BIGNUMERIC"}:
            decimal_scale = field.scale if field.scale is not None else 2
        return ColumnProfile(
            name=field.name,
            bq_type=field.bq_type,
            kind=kind,
            nullable=nullable,
            null_fraction=null_fraction,
            minimum=min(nums),
            maximum=max(nums),
            is_integer=is_int,
            decimal_scale=decimal_scale,
        )

    if kind is ColumnKind.FREE_TEXT:
        pool = _dedupe_stable([str(v) for v in non_null])
        return ColumnProfile(
            name=field.name,
            bq_type=field.bq_type,
            kind=kind,
            nullable=nullable,
            null_fraction=null_fraction,
            text_pool=tuple(pool),
        )

    # CATEGORICAL — empirical frequency table, order-stable for determinism.
    counts = Counter(_hashable(v) for v in non_null)
    values = _dedupe_stable([_hashable(v) for v in non_null])
    total_n = sum(counts.values())
    weights = tuple(counts[v] / total_n for v in values)
    return ColumnProfile(
        name=field.name,
        bq_type=field.bq_type,
        kind=kind,
        nullable=nullable,
        null_fraction=null_fraction,
        categories=tuple(values),
        weights=weights,
    )


def _dedupe_stable(items: list) -> list:
    """Order-preserving de-duplication (determinism for sampling pools)."""
    seen: set = set()
    out: list = []
    for item in items:
        key = _hashable(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def profile_table(ctx_schema: TableSchema, reference_rows: list[dict]) -> dict[str, ColumnProfile]:
    """Profile every top-level column. STRUCT/REPEATED columns are profiled
    as a single free-text/categorical unit over their serialized form;
    nested decomposition is out of M1 scope (single-table, §4)."""
    return {
        col.name: profile_column(col, reference_rows)
        for col in ctx_schema.columns
    }


# ---------------------------------------------------------------------------
# Post-hoc enforcement — clamp a sampled value back into observed support.
# ---------------------------------------------------------------------------


def enforce_value(profile: ColumnProfile, value: object) -> object:
    """Clamp one sampled value to the column's observed support.

    Defense-in-depth on top of the Pandera Mode-A contract: constants are
    copied verbatim, numerics are clipped to ``[min, max]``, categoricals
    are snapped to a known category if a backend produced something unseen.
    Free-text values are passed through (the LLM hook owns their support).
    """
    if profile.kind is ColumnKind.CONSTANT:
        return profile.constant_value

    if value is None:
        # Permit None only where the schema allows it; otherwise fall back to
        # a representative in-support value so the row stays schema-valid.
        return None if profile.nullable else _representative(profile)

    if profile.kind is ColumnKind.NUMERIC:
        return _enforce_numeric(profile, value)

    if profile.kind is ColumnKind.CATEGORICAL:
        known = {_hashable(c) for c in profile.categories}
        return value if _hashable(value) in known else _representative(profile)

    # FREE_TEXT — pass through, but coerce a JSON-string back to a dict so it
    # validates against the JSON column's `dict` record-model type (the LLM
    # hook and the text_pool both carry JSON as a string).
    if profile.bq_type == "JSON":
        return _coerce_json(value, profile)
    return value


def _enforce_numeric(profile: ColumnProfile, value: object) -> object:
    """Clip a sampled numeric to ``[min, max]`` and pin its type/scale."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return _representative(profile)
    if profile.minimum is not None:
        num = max(num, profile.minimum)
    if profile.maximum is not None:
        num = min(num, profile.maximum)
    if profile.is_integer:
        return round(num)
    if profile.decimal_scale is not None:
        # Produce a Decimal quantized to the column's scale so it passes the
        # record model's NUMERIC `decimal_places` constraint exactly.
        return _quantize(num, profile.decimal_scale)
    return num


def _quantize(num: float, scale: int) -> Decimal:
    """Round a float to ``scale`` decimal places as an exact ``Decimal``.

    Going through ``str(num)`` avoids binary float-representation artifacts
    (``Decimal(15.37)`` would be ``15.3699...``); the quantize then pins it
    to exactly ``scale`` places so Pydantic's ``decimal_places`` check holds.
    """
    exponent = Decimal(1).scaleb(-scale) if scale > 0 else Decimal(1)
    return Decimal(str(num)).quantize(exponent, rounding=ROUND_HALF_UP)


def _coerce_json(value: object, profile: ColumnProfile) -> object:
    """Parse a JSON-string into a dict for a JSON column; tolerate failure."""
    if isinstance(value, dict | list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    # Unparseable → fall back to a representative observed value (also parsed).
    rep = _representative(profile)
    if isinstance(rep, str):
        try:
            return json.loads(rep)
        except (ValueError, TypeError):
            return None
    return rep


def _representative(profile: ColumnProfile) -> object:
    """A guaranteed in-support fallback value for a column."""
    if profile.kind is ColumnKind.CONSTANT:
        return profile.constant_value
    if profile.kind is ColumnKind.NUMERIC:
        lo = profile.minimum if profile.minimum is not None else 0.0
        return round(lo) if profile.is_integer else lo
    if profile.kind is ColumnKind.CATEGORICAL and profile.categories:
        return profile.categories[0]
    if profile.kind is ColumnKind.FREE_TEXT and profile.text_pool:
        return profile.text_pool[0]
    return None
