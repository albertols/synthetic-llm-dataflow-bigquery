#!/usr/bin/env python
"""Source-vs-synthetic statistics diff, reusing the ADR 0022 profiler.

Companion to ``e2e_fetch_samples.py`` / ``freetext_crosscheck.py``: instead
of mining free-text structure or exporting spot-check CSVs, this script
answers "did the marginal distributions drift?" for EVERY column (not just
free-text ones) by running the same ``profile_source_table`` the E2E
milestone stats pipeline runs (``sdfb_core.stats.source_stats``, ADR 0022)
against a bounded sample of both the source and synthetic tables, then
diffing the two profiles column-by-column.

One computation, one set of key names: this script never invents its own
entropy/skew/null definitions — it reads the profiler's own entries
(``entropy_norm``, ``top1_share``, ``deciles``, ``null_fraction``,
``empty_fraction``) so a profiler upgrade only has to happen once.

Determinism: sample fetch reuses the ``e2e_fetch_samples.py`` pattern
(``ORDER BY FARM_FINGERPRINT(TO_JSON_STRING(t))`` rather than ``RAND()``) so
the same table + row count always yields the same sample.

Usage:
    python scripts/e2e/source_synthetic_stats_diff.py \
        --source-fqn project.dataset.source_table \
        --synthetic-fqn project.synthetic_data.landing_table \
        --project project \
        --rows 10000 \
        --out-json integration_test/<JOB_ID>/stats_diff.json \
        --out-md integration_test/<JOB_ID>/stats_diff.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Nested/repeated columns aren't representable by the simplified
# ``FieldSchema(name=..., bq_type=..., mode="NULLABLE")`` construction this
# script (and its test fixtures) use -- RECORD/STRUCT requires nested
# ``fields`` or the Pydantic validator rejects it. Skipped, never silently
# dropped: they land in ``diff["table"]["skipped"]``.
_UNSUPPORTED_BQ_TYPES = frozenset({"RECORD", "STRUCT"})

# Verdict thresholds (Step 3 of the task brief): "warn" at these values,
# "fail" at double. `decile_ks` is already non-negative; `entropy_gap` uses
# its raw (signed) value -- only synthetic collapsing *below* source entropy
# is a defect, a more-diverse synthetic column is not. "parity" bundles
# null_delta/empty_delta (the empty-parity seam, ADR 0022 / 2026-08-05
# fidelity design) via its max absolute magnitude.
_WARN_ENTROPY_GAP = 0.3
_WARN_DECILE_KS = 0.2
_WARN_PARITY_DELTA = 0.1
_FAIL_MULTIPLIER = 2.0

_LEN_PCTS = (0.05, 0.50, 0.95)

_VERDICT_RANK = {"fail": 2, "warn": 1, "ok": 0}

_CODE_AREA = {
    "entropy": "mode collapse in pool/sampling",
    "decile_ks": "inverse-CDF sampler",
    "parity": "the empty-parity seam",
}


# --------------------------------------------------------------------------
# auth / preflight (copied from e2e_gcp_probe.py:82-127 via e2e_fetch_samples.py
# -- small, deliberate duplication; scripts stay standalone)
# --------------------------------------------------------------------------
def preflight_adc(project: str) -> tuple[Any, str]:
    """Resolve ADC or fail with an actionable hint. Returns (session, token)."""
    try:
        import google.auth
        import google.auth.transport.requests as gtr
        import requests
    except ImportError as e:  # pragma: no cover - env-dependent
        _die(
            f"missing GCP client libs ({e}). Install: "
            "pip install google-auth google-cloud-bigquery requests"
        )
    try:
        creds, _ = google.auth.default(scopes=_SCOPES)
        creds.refresh(gtr.Request())
    except Exception as e:
        _die(
            "Application Default Credentials not usable "
            f"({type(e).__name__}: {e}).\nRun:\n"
            "  gcloud auth application-default login\n"
            f"  gcloud auth application-default set-quota-project {project}"
        )
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {creds.token}",
            "x-goog-user-project": project,
        }
    )
    return session, creds.token


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _bq_client(project: str):
    from google.cloud import bigquery

    return bigquery.Client(project=project)


# --------------------------------------------------------------------------
# sample SQL + fetch (Task 2 pattern: hash-ordered deterministic LIMIT)
# --------------------------------------------------------------------------
def build_sample_sql(fqn: str, *, rows: int) -> str:
    """Deterministic hash-ordered sample query (no ``RAND()``, no tuning
    constant) -- see ``e2e_fetch_samples.py::build_sample_sql``."""
    return (
        f"SELECT * FROM `{fqn}` t "
        "ORDER BY FARM_FINGERPRINT(TO_JSON_STRING(t)) "
        f"LIMIT {int(rows)}"
    )


def fetch_rows_and_schema(client, fqn: str, *, rows: int) -> tuple[list[dict], list]:
    """Run the deterministic sample query, return (rows, bq SchemaField list)."""
    result = client.query(build_sample_sql(fqn, rows=rows)).result()
    schema = list(result.schema)
    fetched = [dict(r) for r in result]
    return fetched, schema


def build_table_schema(fqn: str, schema_fields: list) -> tuple[Any, list[str]]:
    """BQ ``SchemaField`` list -> ``TableSchema``, per the brief's simplified
    construction (``mode="NULLABLE"`` always). RECORD/STRUCT columns are
    skipped (returned as the second tuple element) rather than crashing the
    Pydantic nested-fields validator."""
    from sdfb_core.contracts.schema import FieldSchema, TableInfo, TableSchema

    columns = []
    skipped = []
    for f in schema_fields:
        if f.field_type in _UNSUPPORTED_BQ_TYPES:
            skipped.append(f.name)
            continue
        columns.append(FieldSchema(name=f.name, bq_type=f.field_type, mode="NULLABLE"))
    schema = TableSchema(table_info=TableInfo(table_id=fqn), columns=columns)
    return schema, skipped


# --------------------------------------------------------------------------
# pure functions (no BQ import; laptop-safe)
# --------------------------------------------------------------------------
def decile_ks(a: list[float], b: list[float]) -> float:
    """Max abs difference between two piecewise-linear empirical CDFs built
    from 11-point decile vectors, evaluated on the union grid via
    ``numpy.interp``.

    No division anywhere in this computation, so a degenerate (all-equal)
    vector -- e.g. a collapsed synthetic column whose deciles are all the
    same value -- can't raise a div-by-zero; ``numpy.interp`` extrapolates
    a fully-flat ``xp`` to ``fp[0]``/``fp[-1]`` on either side, which is
    exactly the step-function CDF a constant column implies.
    """
    if not a or not b:
        return 0.0
    arr_a = np.asarray(a, dtype=float)
    arr_b = np.asarray(b, dtype=float)
    fp_a = np.linspace(0.0, 1.0, num=len(arr_a))
    fp_b = np.linspace(0.0, 1.0, num=len(arr_b))
    grid = np.union1d(arr_a, arr_b)
    f_a = np.interp(grid, arr_a, fp_a)
    f_b = np.interp(grid, arr_b, fp_b)
    return float(np.max(np.abs(f_a - f_b)))


def length_band(strings: list[str]) -> dict:
    """p05/p50/p95 string-length band from raw values.

    Mirrors ``source_stats.py``'s own len_p05/len_p50/len_p95 computation
    (same index-based percentile), but the profiler only ever emits those
    as flat per-column scalars -- it does not group them into a src/syn/
    drift comparison. ``main()`` calls this directly on the sampled rows
    for STRING columns so that comparison is computed here, once.
    """
    if not strings:
        return {}
    lengths = sorted(len(s) for s in strings)
    last = len(lengths) - 1
    return {f"p{int(p * 100):02d}": lengths[int(p * last)] for p in _LEN_PCTS}


def _get(d: dict, key: str, default: float = 0.0) -> float:
    """``dict.get`` that also treats an explicit ``None`` (e.g. an
    all-null/all-empty column never reaches ``_add_value_mix``) as the
    default, instead of propagating ``None`` into arithmetic."""
    v = d.get(key)
    return default if v is None else v


def _verdict(entropy_gap: float, ks: float | None, parity_gap: float) -> str:
    ks_val = ks or 0.0
    if (
        entropy_gap > _WARN_ENTROPY_GAP * _FAIL_MULTIPLIER
        or ks_val > _WARN_DECILE_KS * _FAIL_MULTIPLIER
        or parity_gap > _WARN_PARITY_DELTA * _FAIL_MULTIPLIER
    ):
        return "fail"
    if (
        entropy_gap > _WARN_ENTROPY_GAP
        or ks_val > _WARN_DECILE_KS
        or parity_gap > _WARN_PARITY_DELTA
    ):
        return "warn"
    return "ok"


def diff_profiles(src: dict[str, dict], syn: dict[str, dict]) -> dict:
    """Per-column drift between two ``profile_source_table`` outputs.

    Reads the profiler's own key names only -- never invents new ones:
    ``entropy_norm`` / ``top1_share`` (``_add_value_mix``), ``deciles``
    (``_add_numeric``), ``null_fraction`` / ``empty_fraction`` (top-level,
    every column). All reads go through ``_get`` so a column that never hit
    ``_add_value_mix`` (all-null) or ``_add_numeric`` (non-numeric type,
    empty ``deciles``) degrades to zero-signal instead of raising.

    ``length_band`` is left ``None`` here and filled in by ``main()`` for
    STRING columns -- this function only ever sees the two profile dicts,
    never the raw sampled rows ``length_band()`` needs.

    The ``__table__`` pseudo-column (row-level null-pattern mix) is never a
    diffable column; columns present in only one profile are intersected
    out and reported in ``["table"]["skipped"]``, never dropped silently.
    """
    from sdfb_core.stats.source_stats import TABLE_PSEUDO_COLUMN

    src_cols = {c for c in src if c != TABLE_PSEUDO_COLUMN}
    syn_cols = {c for c in syn if c != TABLE_PSEUDO_COLUMN}
    common = sorted(src_cols & syn_cols)
    skipped = sorted(src_cols ^ syn_cols)

    columns: dict[str, dict] = {}
    for col in common:
        s, y = src[col], syn[col]
        entropy_gap = round(_get(s, "entropy_norm") - _get(y, "entropy_norm"), 6)
        top1_delta = round(_get(y, "top1_share") - _get(s, "top1_share"), 6)
        ks = (
            decile_ks(s["deciles"], y["deciles"])
            if s.get("deciles") and y.get("deciles")
            else None
        )
        null_delta = round(_get(s, "null_fraction") - _get(y, "null_fraction"), 6)
        empty_delta = round(_get(s, "empty_fraction") - _get(y, "empty_fraction"), 6)
        parity_gap = max(abs(null_delta), abs(empty_delta))

        columns[col] = {
            "type": s.get("type") or y.get("type") or "",
            "entropy_gap": entropy_gap,
            "top1_delta": top1_delta,
            "decile_ks": ks,
            "null_delta": null_delta,
            "empty_delta": empty_delta,
            "length_band": None,
            "verdict": _verdict(entropy_gap, ks, parity_gap),
        }

    return {
        "columns": columns,
        "table": {
            "skipped": skipped,
            "source_rows": src.get(TABLE_PSEUDO_COLUMN, {}).get("sample_rows"),
            "synthetic_rows": syn.get(TABLE_PSEUDO_COLUMN, {}).get("sample_rows"),
        },
        "evaluation": None,
    }


def _score(entry: dict) -> float:
    parity = max(abs(entry.get("null_delta") or 0.0), abs(entry.get("empty_delta") or 0.0))
    return max(entry.get("entropy_gap") or 0.0, entry.get("decile_ks") or 0.0, parity)


def _issues(entry: dict) -> list[str]:
    """Map each signal that crossed a warn threshold to a generation code
    area, so the report tells you where to look, not just what drifted."""
    out = []
    if (entry.get("entropy_gap") or 0.0) > _WARN_ENTROPY_GAP:
        out.append(
            f"entropy gap {entry['entropy_gap']:.3f} -> {_CODE_AREA['entropy']}"
        )
    ks = entry.get("decile_ks")
    if ks is not None and ks > _WARN_DECILE_KS:
        out.append(f"decile KS {ks:.3f} -> {_CODE_AREA['decile_ks']}")
    parity = max(abs(entry.get("null_delta") or 0.0), abs(entry.get("empty_delta") or 0.0))
    if parity > _WARN_PARITY_DELTA:
        out.append(f"null/empty parity delta {parity:.3f} -> {_CODE_AREA['parity']}")
    return out


def render_md(diff: dict) -> str:
    """Ranked table (worst verdict + worst signal first), then one bullet
    per non-"ok" column mapping its signal(s) to a generation code area."""
    columns = diff.get("columns", {})
    ranked = sorted(
        columns.items(),
        key=lambda kv: (_VERDICT_RANK.get(kv[1].get("verdict", "ok"), 0), _score(kv[1])),
        reverse=True,
    )

    lines: list[str] = []
    a = lines.append
    a("# Source vs synthetic stats diff")
    a("")

    table = diff.get("table", {})
    if table.get("source_rows") is not None or table.get("synthetic_rows") is not None:
        a(
            f"> Source sample: {table.get('source_rows')} rows. "
            f"Synthetic sample: {table.get('synthetic_rows')} rows."
        )
        a("")
    if table.get("skipped"):
        a(f"> Skipped columns (absent from one side, or unsupported type): "
          f"{', '.join(table['skipped'])}")
        a("")

    a("## Ranked columns (worst first)")
    a("")
    a("| Rank | Column | Verdict | Entropy gap | Top1 delta | Decile KS | Null delta | Empty delta |")
    a("|---|---|---|---|---|---|---|---|")
    for i, (col, e) in enumerate(ranked, 1):
        ks = e.get("decile_ks")
        ks_s = f"{ks:.3f}" if ks is not None else "—"
        a(
            f"| {i} | `{col}` | {e.get('verdict', 'ok')} | "
            f"{e.get('entropy_gap', 0.0):.3f} | {e.get('top1_delta', 0.0):.3f} | "
            f"{ks_s} | {e.get('null_delta', 0.0):.3f} | {e.get('empty_delta', 0.0):.3f} |"
        )
    a("")

    flagged = [(c, e) for c, e in ranked if e.get("verdict") != "ok"]
    a("## Findings")
    a("")
    if not flagged:
        a("_No column exceeded warn/fail thresholds._")
    else:
        for col, e in flagged:
            issues = _issues(e) or ["flagged, but below the itemized signal thresholds"]
            for issue in issues:
                a(f"- `{col}` [{e.get('verdict')}]: {issue}")
    a("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def _string_length_band(rows: list[dict], col: str) -> dict:
    values = [
        str(v) for v in (r.get(col) for r in rows) if v is not None and str(v).strip() != ""
    ]
    return length_band(values)


def main(argv: list[str] | None = None) -> int:
    from sdfb_core.stats.source_stats import profile_source_table

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-fqn", required=True, help="project.dataset.table")
    ap.add_argument("--synthetic-fqn", required=True, help="project.dataset.table")
    ap.add_argument("--project", required=True)
    ap.add_argument("--rows", type=int, default=10000)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", default=None)
    args = ap.parse_args(argv)

    session, _ = preflight_adc(args.project)
    del session  # only needed to fail fast on missing/invalid ADC
    client = _bq_client(args.project)

    print(f"[1/3] fetching {args.rows:,} rows -> source ({args.source_fqn})…", file=sys.stderr)
    src_rows, src_fields = fetch_rows_and_schema(client, args.source_fqn, rows=args.rows)
    print(f"[2/3] fetching {args.rows:,} rows -> synthetic ({args.synthetic_fqn})…", file=sys.stderr)
    syn_rows, syn_fields = fetch_rows_and_schema(client, args.synthetic_fqn, rows=args.rows)

    src_schema, src_type_skipped = build_table_schema(args.source_fqn, src_fields)
    syn_schema, syn_type_skipped = build_table_schema(args.synthetic_fqn, syn_fields)

    print("[3/3] profiling + diffing…", file=sys.stderr)
    src_profile = profile_source_table(src_schema, src_rows)
    syn_profile = profile_source_table(syn_schema, syn_rows)

    diff = diff_profiles(src_profile, syn_profile)
    diff["table"]["skipped"] = sorted(
        set(diff["table"]["skipped"]) | set(src_type_skipped) | set(syn_type_skipped)
    )

    for col, entry in diff["columns"].items():
        if entry.get("type") != "STRING":
            continue
        src_band = _string_length_band(src_rows, col)
        syn_band = _string_length_band(syn_rows, col)
        drift = (
            abs(src_band["p50"] - syn_band["p50"])
            if src_band and syn_band
            else None
        )
        entry["length_band"] = {"src": src_band, "syn": syn_band, "drift": drift}

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(diff, indent=2, default=str))
    print(f"wrote {out_json}")

    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_md(diff))
        print(f"wrote {out_md}")

    ranked = sorted(
        diff["columns"].items(),
        key=lambda kv: (_VERDICT_RANK.get(kv[1].get("verdict", "ok"), 0), _score(kv[1])),
        reverse=True,
    )
    worst = ranked[0] if ranked else None
    if worst:
        print(f"most divergent: {worst[0]} (verdict {worst[1]['verdict']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
