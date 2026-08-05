"""Per-column source-table stats from the reference sample.

One computation, four consumers (2026-08-05 spec, WS-B): a compact
``source_table_stats`` milestone, a GCS JSON artifact, BQ rows via
``stats_rows``, and human reports. Engines do NOT read these — they profile
per worker from the same rows; this module is the driver/human view, so it
must never disagree with the profilers on definitions:

  - ``null_fraction``  — fraction of rows whose value is None;
  - ``empty_fraction`` — fraction of rows whose value is a string that is
    empty after ``.strip()`` (the crosscheck's trimmed-empty definition);
  - ``distinct``       — distinct NON-EMPTY, non-null values.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sdfb_core.engines.text_shapes import build_shape_mix

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sdfb_core.contracts.relational import RelationalContract
    from sdfb_core.contracts.schema import TableSchema

_NUMERIC_BQ_TYPES = frozenset(
    {"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
)
_TEMPORAL_BQ_TYPES = frozenset({"DATE", "DATETIME", "TIMESTAMP"})
_SHAPE_MIX_TOP_K = 8
_LEN_PCTS = (0.05, 0.50, 0.95)

# Part of the stats-table append-skip key: the reference digest hashes ROWS,
# not this module, so a profiler upgrade must bump this or already-profiled
# tables keep stale stats forever (ADR 0022).
PROFILER_VERSION = "2"


def _is_empty_str(v: object) -> bool:
    return isinstance(v, str) and not v.strip()


def _mask(v: str) -> str:
    return "".join(
        "9" if ch.isdigit() else "A" if ch.isupper() else "a" if ch.islower() else ch
        for ch in v
    )


def _to_float(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _day_granularity(strings: list[str]) -> bool:
    """True when every parseable temporal value has zero time-of-day."""
    saw_any = False
    for s in strings:
        text = s.strip().replace("T", " ")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            continue
        saw_any = True
        if (dt.hour, dt.minute, dt.second, dt.microsecond) != (0, 0, 0, 0):
            return False
    return saw_any


def profile_source_table(
    table_schema: TableSchema,
    reference_rows: list[dict],
    contract: RelationalContract | None = None,
    generation_plan: dict[str, str] | None = None,
) -> dict[str, dict]:
    """name → stats dict for every top-level column of the schema."""
    pk = set(contract.pk) if contract else set()
    identity = set(contract.identity) if contract else set()
    fk_cols: set[str] = set()
    if contract:
        for fk in contract.fk:
            fk_cols.update(fk.cols)
    plan = generation_plan or {}
    n = len(reference_rows)

    stats: dict[str, dict] = {}
    for col in table_schema.columns:
        values = [r.get(col.name) for r in reference_rows]
        non_null = [v for v in values if v is not None]
        empties = sum(1 for v in non_null if _is_empty_str(v))
        substantive = [v for v in non_null if not _is_empty_str(v)]
        strings = [str(v) for v in substantive]
        distinct = len(set(strings))

        entry: dict = {
            "type": str(col.bq_type),
            "in_source_schema": True,
            "sample_rows": n,
            "null_fraction": round((n - len(non_null)) / n, 6) if n else 0.0,
            "empty_fraction": round(empties / n, 6) if n else 0.0,
            "zero_fraction": 0.0,
            "distinct": distinct,
            "distinct_ratio": round(distinct / n, 6) if n else 0.0,
            "is_constant": distinct <= 1 and not empties and len(non_null) == n,
            "is_pk": col.name in pk,
            "is_fk": col.name in fk_cols,
            "identity_col": col.name in identity,
            "min": None,
            "max": None,
            "len_p05": None,
            "len_p50": None,
            "len_p95": None,
            "mean_len": None,
            "shape_mix": [],
            "temporal_day_granularity": False,
            "generation_plan": plan.get(col.name, ""),
            "stats_tier": "sample",
            "profiler_version": PROFILER_VERSION,
        }

        if col.bq_type in _NUMERIC_BQ_TYPES:
            numbers = [f for v in substantive if (f := _to_float(v)) is not None]
            if numbers:
                entry["min"] = min(numbers)
                entry["max"] = max(numbers)
                entry["zero_fraction"] = round(
                    sum(1 for f in numbers if f == 0.0) / len(numbers), 6
                )
        elif strings:
            lengths = sorted(len(s) for s in strings)
            last = len(lengths) - 1
            for p, key in zip(_LEN_PCTS, ("len_p05", "len_p50", "len_p95"), strict=True):
                entry[key] = lengths[int(p * last)]
            entry["mean_len"] = round(sum(lengths) / len(lengths), 2)
            if col.bq_type in _TEMPORAL_BQ_TYPES or _day_granularity(strings[:64]):
                entry["temporal_day_granularity"] = _day_granularity(strings)
            shapes = build_shape_mix(strings, top_k=_SHAPE_MIX_TOP_K)
            if shapes:
                total = sum(w for w, _ in shapes)
                entry["shape_mix"] = [
                    [_mask_of_template(shape), round(w / total, 4)]
                    for w, shape in shapes
                ]

        stats[col.name] = entry
    return stats


def _mask_of_template(shape: tuple[str, ...]) -> str:
    """Render a per-position template back into a 9/A/a mask string.

    A class position maps to the mask of its dominant character family;
    a literal position masks like a plain character.
    """
    out = []
    for entry in shape:
        if len(entry) == 1:
            out.append(_mask(entry))
        elif all(c.isdigit() for c in entry):
            out.append("9")
        elif entry.islower():
            out.append("a")
        else:
            out.append("A")
    return "".join(out)


def stats_rows(
    table_fqn: str, reference_digest: str, run_id: str, stats: dict[str, dict]
) -> list[dict]:
    """Flatten per-column stats into BQ-loadable rows.

    Headline numerics are real columns; the full entry rides in ``stats``
    as JSON so the table schema never chases this module's field list.
    """
    computed_at = datetime.now(tz=UTC).isoformat()
    rows = []
    for column, entry in sorted(stats.items()):
        rows.append(
            {
                "table_fqn": table_fqn,
                "reference_digest": reference_digest,
                "run_id": run_id,
                "column": column,
                "generation_plan": entry.get("generation_plan", ""),
                "null_fraction": entry["null_fraction"],
                "empty_fraction": entry["empty_fraction"],
                "distinct": entry["distinct"],
                "distinct_ratio": entry["distinct_ratio"],
                "is_pk": entry["is_pk"],
                "is_fk": entry["is_fk"],
                "stats": json.dumps(entry, default=str, sort_keys=True),
                "sample_rows": entry.get("sample_rows"),
                "stats_tier": entry.get("stats_tier", "sample"),
                "profiler_version": entry.get(
                    "profiler_version", PROFILER_VERSION
                ),
                "computed_at": computed_at,
            }
        )
    return rows


__all__ = ["PROFILER_VERSION", "profile_source_table", "stats_rows"]
