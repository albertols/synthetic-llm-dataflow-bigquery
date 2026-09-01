#!/usr/bin/env python
"""Offline, table-agnostic validation analysis for synthetic CSV samples.

Reads one or more engine-output CSVs plus the target BQ schema (JSON) and
computes cross-sample + per-column metrics that feed the
``end_to_end_validation_report_*.md`` deliverable. Nothing here is specific to
any table or environment: pass the CSVs, the schema, and (optionally) the
primary-key / identity columns, and it works anywhere.

Per engine it reports:
  * row count, full-row duplicate count + ratio, distinct full rows;
  * REPETITION — per-column top-value share + PK consecutive run-length
    histogram (detects the batch-replay defect: runs equal to the batch size);
  * SINGULARITY — constant / near-constant columns (one dominant value);
  * SPARSITY — null/empty fraction, zero fraction (numeric), date-sentinel
    fraction;
  * identity-column uniqueness (any columns named via --identity-cols);
  * integer type conformance per INTEGER column.

Across engines it reports per-column value-set overlap (Jaccard) — a proxy for
shared memorization when the live source table is not queried here (the live
copy-ratio lives in ``e2e_gcp_probe.py``).

Usage:
    python scripts/e2e/e2e_validation_analysis.py \
        --csv b1_rag=runs/b1_rag/<sample>.csv \
        --csv b2_library=runs/b2_library/<sample>.csv \
        --schema config/bq_schema/<dataset>/<TABLE>.schema.json \
        --pk <PK_COL[,PK_COL2]> \
        --identity-cols <ID_COL[,ID_COL2]> \
        --out output/e2e_validation_metrics.json
"""

from __future__ import annotations

import argparse
import json
import math
from itertools import groupby, pairwise
from pathlib import Path
from typing import Any

import pandas as pd

# Low-information sentinel values discounted when judging sparsity / diversity.
_DATE_SENTINELS = frozenset(
    {"0001-01-01", "0001-01-01 00:00:00", "1970-01-01", "", "0"}
)
# BQ types treated as integer for type-conformance and zero-fraction checks.
_INT_BQ_TYPES = frozenset({"INTEGER", "INT64"})
_NUMERIC_BQ_TYPES = frozenset(
    {"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
)
# A column whose single most-common value covers ≥ this share is "near-constant".
_NEAR_CONSTANT_SHARE = 0.98
# Minimum engine samples required for a cross-sample overlap comparison.
_MIN_ENGINES_FOR_OVERLAP = 2
# Minimum non-empty values needed to judge a column "sequential" (need at
# least one gap to have a step to compare).
_MIN_VALUES_FOR_SEQUENTIAL = 2


def _load_schema(path: Path) -> list[dict[str, str]]:
    return json.loads(path.read_text())


def _read_csv(path: Path) -> pd.DataFrame:
    # dtype=str keeps values byte-faithful so duplicate / overlap checks are
    # exact; type conformance is judged separately against the schema.
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])


def _run_lengths(series: pd.Series, batch_size: int = 0) -> dict[str, Any]:
    """Longest consecutive run of an identical value + run-length histogram.

    A histogram dominated by a single large run-length is the signature of
    batch-level replay (every batch draws the same sequence). When the
    pipeline's batch size is known (``--batch-size``), ``equals_batch_size``
    flags the sharper case: the longest run exactly matches the batch size,
    i.e. every batch replayed the identical value."""
    runs = [(key, len(list(grp))) for key, grp in groupby(series.tolist())]
    if not runs:
        return {
            "max_run": 0,
            "top_runs": [],
            "run_length_histogram": {},
            "equals_batch_size": False,
        }
    runs_sorted = sorted(runs, key=lambda kv: kv[1], reverse=True)
    hist: dict[int, int] = {}
    for _, length in runs:
        hist[length] = hist.get(length, 0) + 1
    return {
        "max_run": runs_sorted[0][1],
        "top_runs": [
            {"value": str(v)[:32], "run_length": length}
            for v, length in runs_sorted[:5]
        ],
        "run_length_histogram": {str(k): v for k, v in sorted(hist.items())},
        "equals_batch_size": batch_size > 0 and runs_sorted[0][1] == batch_size,
    }


def _int_conformance(series: pd.Series) -> float:
    ok = total = 0
    for v in series:
        if v == "" or v is None:
            continue
        total += 1
        try:
            int(v)
            ok += 1
        except (TypeError, ValueError):
            pass
    return round(ok / total, 6) if total else 1.0


def _column_report(df: pd.DataFrame, schema: list[dict[str, str]]) -> dict[str, Any]:
    n = len(df)
    cols: dict[str, Any] = {}
    for field in schema:
        name, bq_type = field["name"], field["type"]
        if name not in df.columns:
            cols[name] = {"present": False}
            continue
        series = df[name]
        non_empty = series[series != ""]
        distinct = int(non_empty.nunique())
        vc = non_empty.value_counts()
        top_count = int(vc.iloc[0]) if len(vc) else 0
        is_numeric = bq_type in _NUMERIC_BQ_TYPES
        zero_n = int(non_empty.isin({"0"}).sum()) if is_numeric else 0
        sentinel_n = int(non_empty.isin(_DATE_SENTINELS).sum())
        entry: dict[str, Any] = {
            "present": True,
            "bq_type": bq_type,
            "distinct": distinct,
            "distinct_ratio": round(distinct / n, 6) if n else 0.0,
            # SPARSITY
            "null_fraction": round(1.0 - (len(non_empty) / n), 6) if n else 0.0,
            "sentinel_fraction": round(sentinel_n / n, 6) if n else 0.0,
            "zero_fraction": round(zero_n / n, 6) if (n and is_numeric) else None,
            # REPETITION / SINGULARITY
            "top_value_share": round(top_count / n, 6) if n else 0.0,
            "is_constant": distinct <= 1,
            "is_near_constant": (top_count / n >= _NEAR_CONSTANT_SHARE) if n else False,
            "top_values": [
                {"value": str(idx)[:48], "count": int(cnt)}
                for idx, cnt in list(vc.items())[:3]
            ],
        }
        if bq_type in _INT_BQ_TYPES:
            entry["int_type_conformance"] = _int_conformance(non_empty)
        if len(non_empty) and distinct > 1:
            probs = (vc / len(non_empty)).tolist()
            h = -sum(p * math.log2(p) for p in probs if p > 0)
            entry["normalized_entropy"] = round(h / math.log2(distinct), 6)
        else:
            entry["normalized_entropy"] = 0.0
        cols[name] = entry
    return cols


def _is_sequential(values: list[str]) -> bool:
    """True when non-empty values are all-numeric and strictly increasing
    with a constant step (e.g. 1, 2, 3, ... or 100, 200, 300, ...).

    Kept deliberately simple: no gap-tolerance, no reordering, no float
    epsilon handling — just the textbook autoincrement-id signature."""
    if len(values) < _MIN_VALUES_FOR_SEQUENTIAL:
        return False
    try:
        nums = [float(v) for v in values]
    except (TypeError, ValueError):
        return False
    step = nums[1] - nums[0]
    if step <= 0:
        return False
    return all(b - a == step for a, b in pairwise(nums))


def _analyze_one(
    df: pd.DataFrame,
    schema: list[dict[str, str]],
    pk_cols: list[str],
    identity_cols: list[str],
    batch_size: int = 0,
) -> dict[str, Any]:
    n = len(df)
    full_dupes = int(df.duplicated(keep="first").sum())
    return {
        "row_count": n,
        "full_row_duplicates": full_dupes,
        "full_row_duplicate_ratio": round(full_dupes / n, 6) if n else 0.0,
        "distinct_full_rows": int(df.drop_duplicates().shape[0]),
        "primary_key": _key_report(df, pk_cols, batch_size),
        "identity_columns": {
            c: _identity_report(df[c], batch_size)
            for c in identity_cols
            if c in df.columns
        },
        "columns": _column_report(df, schema),
    }


def _key_report(
    df: pd.DataFrame, pk_cols: list[str], batch_size: int = 0
) -> dict[str, Any]:
    present = [c for c in pk_cols if c in df.columns]
    if not present:
        return {"declared": bool(pk_cols), "present": False}
    n = len(df)
    key = (
        df[present].astype(str).agg("\x1f".join, axis=1)
        if len(present) > 1
        else df[present[0]]
    )
    distinct = int(key.nunique())
    return {
        "declared": True,
        "columns": present,
        "distinct": distinct,
        "distinct_ratio": round(distinct / n, 6) if n else 0.0,
        "duplicates": int(key.duplicated(keep="first").sum()),
        "run_lengths": _run_lengths(key, batch_size),
    }


def _identity_report(series: pd.Series, batch_size: int = 0) -> dict[str, Any]:
    n = len(series)
    distinct = int(series.nunique())
    non_empty = [v for v in series.tolist() if v != ""]
    return {
        "distinct": distinct,
        "distinct_ratio": round(distinct / n, 6) if n else 0.0,
        "duplicates": int(series.duplicated(keep="first").sum()),
        # Run-lengths on the identity column detect batch-replay even when no
        # primary key is declared (a per-row-unique column replayed in blocks
        # equal to the batch size is the clearest block-replay signature).
        "run_lengths": _run_lengths(series, batch_size),
        # A per-row-unique column that is also a plain autoincrement (1, 2,
        # 3, ...) is a strong "this is a real ID, not a generated one" or
        # "the generator copied the reference table's own PK" signal.
        "sequential": _is_sequential(non_empty),
    }


def _cross_overlap(
    frames: dict[str, pd.DataFrame], columns: list[str]
) -> dict[str, Any]:
    """Pairwise per-column value-set overlap (Jaccard) across every engine
    sample — a memorization proxy when the live source is not queried here."""
    names = list(frames)
    if len(names) < _MIN_ENGINES_FOR_OVERLAP:
        return {}
    out: dict[str, Any] = {}
    for name in columns:
        sets = {
            eng: set(frames[eng][name][frames[eng][name] != ""])
            for eng in names
            if name in frames[eng].columns
        }
        if len(sets) < _MIN_ENGINES_FOR_OVERLAP:
            continue
        pairwise: dict[str, Any] = {}
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if a not in sets or b not in sets:
                    continue
                sa, sb = sets[a], sets[b]
                union = len(sa | sb)
                pairwise[f"{a}|{b}"] = {
                    f"{a}_distinct": len(sa),
                    f"{b}_distinct": len(sb),
                    "shared": len(sa & sb),
                    "jaccard": round(len(sa & sb) / union, 6) if union else 0.0,
                }
        out[name] = pairwise
    return out


def _parse_csv_arg(items: list[str]) -> dict[str, Path]:
    frames: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--csv expects engine=path, got {item!r}")
        label, path = item.split("=", 1)
        frames[label.strip()] = Path(path.strip())
    return frames


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--csv",
        action="append",
        required=True,
        help="engine_label=path.csv (repeatable), e.g. b1_rag=.../sample.csv",
    )
    ap.add_argument("--schema", required=True, type=Path)
    ap.add_argument("--pk", default="", help="comma-separated primary-key columns")
    ap.add_argument(
        "--identity-cols",
        default="",
        help="comma-separated per-row-unique columns (e.g. UUID, id)",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="Beam/RunInference batch size, if known (0 = unknown; disables "
        "the equals_batch_size run-length flag)",
    )
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    schema = _load_schema(args.schema)
    schema_cols = [f["name"] for f in schema]
    pk_cols = [c.strip() for c in args.pk.split(",") if c.strip()]
    identity_cols = [c.strip() for c in args.identity_cols.split(",") if c.strip()]

    paths = _parse_csv_arg(args.csv)
    frames = {label: _read_csv(p) for label, p in paths.items()}

    report: dict[str, Any] = {
        "schema": str(args.schema),
        "schema_columns": len(schema_cols),
        "primary_key": pk_cols,
        "identity_columns": identity_cols,
        "engines": {
            label: {
                "file": str(paths[label]),
                **_analyze_one(
                    df, schema, pk_cols, identity_cols, args.batch_size
                ),
            }
            for label, df in frames.items()
        },
        "cross_sample_overlap": _cross_overlap(frames, schema_cols),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}  (engines={list(frames)})")
    return 0


main_with_args = main


if __name__ == "__main__":
    raise SystemExit(main())
