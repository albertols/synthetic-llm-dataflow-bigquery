#!/usr/bin/env python
"""BQ-direct deterministic sample fetch for E2E validation.

Companion to ``e2e_gcp_probe.py`` / ``e2e_validation_analysis.py``: the E2E
validation prompt needs a handful of representative rows per engine run
(``integration_test/<JOB_ID>/<engine>_sample.csv``) for spot-checking and
free-text crosschecks. Historically these were exported by hand from the BQ
console; this script fetches them directly via a deterministic,
warehouse-side sample so the CSV is reproducible without a manual export
step.

Determinism: ordering by ``FARM_FINGERPRINT(TO_JSON_STRING(t))`` (a stable
hash of the row) rather than ``RAND()`` means the same table + row count
always yields the same sample, without needing a tuning constant like a
``MOD(...)`` bucket filter.

Usage:
    python scripts/e2e/e2e_fetch_samples.py \
        --landing-fqn project.synthetic_data.table \
        --project project \
        --job-id 2026-08-06_10_00_00-123 \
        --engine-label b1_rag=r-b1 \
        --engine-label b2_library=r-b2 \
        --rows 200 \
        --run-id-col run_id \
        --out-dir integration_test
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


# --------------------------------------------------------------------------
# auth / preflight (copied from e2e_gcp_probe.py:82-127 — small, deliberate
# duplication; scripts stay standalone)
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
            # Route API quota to the target project (higher limits) instead of
            # the default OAuth client, and set the billing/quota project.
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
# sample SQL + CSV writer (pure functions; no GCP imports)
# --------------------------------------------------------------------------
def build_sample_sql(
    fqn: str, *, rows: int, run_id_col: str | None, run_id: str | None
) -> str:
    """Deterministic hash-ordered sample query.

    Ordering by ``FARM_FINGERPRINT(TO_JSON_STRING(t))`` gives the same
    determinism guarantee as a ``MOD(...)`` bucket filter without a tuning
    constant, and without ``RAND()`` (which reshuffles on every call).
    """
    where = f"WHERE {run_id_col} = @run_id" if run_id_col and run_id else ""
    return (
        f"SELECT * FROM `{fqn}` t {where} "
        "ORDER BY FARM_FINGERPRINT(TO_JSON_STRING(t)) "
        f"LIMIT {int(rows)}"
    )


def write_csv(rows: list[dict], columns: list[str], path: Path) -> int:
    """Write ``rows`` to ``path`` as CSV with ``columns`` as header order.

    Refuses to write an empty CSV (a silently-empty sample file is worse
    than a loud failure — downstream crosschecks would read it as "zero
    rows generated" rather than "fetch produced nothing").
    """
    if not rows:
        sys.stderr.write(
            f"no rows fetched for {path.name} - refusing to write an empty CSV\n"
        )
        raise SystemExit(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _jsonify_row(row: dict) -> dict:
    """JSON-encode nested (dict/list) values so csv.DictWriter can serialize
    RECORD/REPEATED BigQuery columns as a single CSV cell."""
    out = {}
    for k, v in row.items():
        out[k] = json.dumps(v, default=str) if isinstance(v, dict | list) else v
    return out


def fetch_sample(
    client,
    fqn: str,
    *,
    rows: int,
    run_id_col: str | None,
    run_id: str | None,
) -> tuple[list[dict], list[str]]:
    """Run the deterministic sample query and return (rows, column_order)."""
    from google.cloud import bigquery

    sql = build_sample_sql(fqn, rows=rows, run_id_col=run_id_col, run_id=run_id)
    job_config = None
    if run_id_col and run_id:
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
        )
    result = client.query(sql, job_config=job_config).result()
    columns = [f.name for f in result.schema]
    fetched = [_jsonify_row(dict(r)) for r in result]
    return fetched, columns


def _parse_engine_labels(specs: list[str]) -> dict[str, str]:
    """Repeatable --engine-label label=run_id → {label: run_id}.

    A spec missing the ``=`` is a malformed CLI invocation, not something to
    silently drop — dropping it would leave that engine out of the fetch
    entirely with no diagnostic.
    """
    out: dict[str, str] = {}
    for spec in specs:
        if "=" not in spec:
            _die(f"--engine-label expects 'label=run_id', got {spec!r}")
        label, run_id = spec.split("=", 1)
        out[label.strip()] = run_id.strip()
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--landing-fqn", required=True, help="project.dataset.table")
    ap.add_argument("--project", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument(
        "--engine-label",
        action="append",
        default=[],
        dest="engine_labels",
        help="repeatable 'label=run_id' mapping; default is a single unfiltered "
        "fetch labeled 'sample'",
    )
    ap.add_argument("--rows", type=int, default=200)
    ap.add_argument("--run-id-col", default=None)
    ap.add_argument("--out-dir", default="integration_test")
    args = ap.parse_args(argv)

    engine_labels = _parse_engine_labels(args.engine_labels) or {"sample": None}

    # Fail fast, before touching BQ: an --engine-label carrying a run_id
    # with no --run-id-col would make `fetch_sample` build an unfiltered
    # query (build_sample_sql only filters when *both* run_id_col and
    # run_id are set) — every engine's CSV would then come back identical
    # despite the per-engine labels, silently invalidating the report's
    # "per-engine rows" claim.
    if not args.run_id_col and any(engine_labels.values()):
        _die(
            "--engine-label carries a run_id but --run-id-col was not given "
            "- the fetch would be unfiltered and every engine would receive "
            "the same unfiltered sample. Pass --run-id-col <column> (e.g. "
            "--run-id-col run_id)."
        )

    session, _ = preflight_adc(args.project)
    del session  # only needed to fail fast on missing/invalid ADC
    client = _bq_client(args.project)

    out_dir = Path(args.out_dir) / args.job_id
    written = 0
    for label, run_id in engine_labels.items():
        rows, columns = fetch_sample(
            client,
            args.landing_fqn,
            rows=args.rows,
            run_id_col=args.run_id_col,
            run_id=run_id,
        )
        out_path = out_dir / f"{label}_sample.csv"
        n = write_csv(rows, columns, out_path)
        print(f"wrote {n} rows -> {out_path}")
        written += n

    print(f"done: {written} total rows across {len(engine_labels)} sample file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
