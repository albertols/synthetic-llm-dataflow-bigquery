#!/usr/bin/env python
"""Generic GCP cross-validation + Dataflow observability probe.

Environment-agnostic companion to ``e2e_validation_analysis.py``. Given a
project, a source table, a landing (synthetic) table and one or more Dataflow
job ids, it uses Application Default Credentials (ADC) to:

  1. Preflight ADC + resolve the caller identity (fails fast with the exact
     ``gcloud auth application-default login`` hint when creds are missing).
  2. BigQuery cross-validation — schema is introspected from
     ``INFORMATION_SCHEMA``; no column names are hard-coded. For every column
     it computes source-vs-synthetic distinctness, top-value share
     (repetition), null/zero/sentinel share (sparsity), constant/singularity
     flags, and a memorization "copy ratio" (fraction of synthetic values that
     already exist in the source column).
  3. Quality tables — the ``validation_runs`` and ``dlq`` rows for the run(s),
     when the quality dataset is supplied.
  4. Dataflow observability — per job: wall-clock execution time, stage list +
     per-stage element throughput, job-message milestones, and worker-log-mined
     engine milestones (model / vLLM ignition time, embedder load time, first /
     last generate_batch, BigQuery load) discovered via configurable regexes.

Nothing here is specific to any one table or environment: pass ``--project``,
``--source-fqn``, ``--landing-fqn`` and ``--job-id`` and it works anywhere.

Usage:
    python scripts/e2e_gcp_probe.py \
        --project project \
        --source-fqn project.dataset.table \
        --landing-fqn project.synthetic_data.table \
        --quality-dataset project.synthetic_data_quality \
        --region europe-west3 \
        --job-id 2026-07-06_10_51_43-12540741799631893815 \
        --job-id 2026-07-07_02_16_54-16825996369190239543 \
        --out output/e2e_gcp_metrics.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

# Milestone regexes applied to worker log text. Each (label, pattern) marks the
# FIRST log line matching the pattern; durations between milestones are derived
# downstream. Patterns match the real sdfb worker log wording — override via
# --milestones for a different image/engine.
_DEFAULT_MILESTONES: list[tuple[str, str]] = [
    ("setup_packages_install", r"Installing setup packages"),
    ("embedder_warm_pull_start", r"Warm-pulling .*(embedder|embeddings)"),
    ("embedder_pulled", r"Pulled \d+ files"),
    ("model_weights_loading", r"Loading weights"),
    ("vllm_engine_init", r"(?i)vllm.*(engine|init)|LLM engine|EngineCore|Initializing a V\d"),
    ("vllm_ready", r"(?i)vllm.*(ready|initialized)|model loaded|Loading weights took"),
    ("sdgx_fit", r"(?i)Fitting|CTGAN.*epoch|sdgx.*synthesizer|Sampling \d+"),
    ("faiss_loaded", r"Loading faiss"),
    ("generation_stall", r"Bundle processor .* has been creating for at least"),
    (
        "vllm_error",
        r"Bfloat16 is only supported|out of resource: shared memory|"
        r"head size \d+ is not supported|CUDA out of memory",
    ),
]

# First-class contract with sdfb_core.observability.log_milestone: any line
# "SDFB_MILESTONE name=<x> ..." is captured generically, one timestamp per
# distinct name. Legacy wording regexes above remain as fallback for jobs
# that predate the instrumented image.
_SDFB_MILESTONE_RE = re.compile(r"SDFB_MILESTONE name=(?P<name>[a-z0-9_]+)")

_DATAFLOW_BASE = "https://dataflow.googleapis.com/v1b3"
_LOGGING_URL = "https://logging.googleapis.com/v2/entries:list"
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_FQN_PARTS = 3  # project.dataset.table


# --------------------------------------------------------------------------
# auth / preflight
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


# --------------------------------------------------------------------------
# BigQuery cross-validation (schema-introspected, table-agnostic)
# --------------------------------------------------------------------------
def _bq_client(project: str):
    from google.cloud import bigquery

    return bigquery.Client(project=project)


def _split_fqn(fqn: str) -> tuple[str, str, str]:
    parts = fqn.split(".")
    if len(parts) != _FQN_PARTS:
        _die(f"expected project.dataset.table, got {fqn!r}")
    return parts[0], parts[1], parts[2]


def _columns(client, fqn: str) -> list[dict[str, str]]:
    proj, ds, tbl = _split_fqn(fqn)
    sql = f"""
        SELECT column_name, data_type
        FROM `{proj}.{ds}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @tbl
        ORDER BY ordinal_position
    """
    from google.cloud import bigquery

    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("tbl", "STRING", tbl)]
        ),
    )
    return [{"name": r.column_name, "type": r.data_type} for r in job.result()]


def _quote(fqn: str) -> str:
    return "`" + fqn + "`"


def bq_cross_validation(
    client, source_fqn: str, landing_fqn: str, *, pk_columns: list[str]
) -> dict[str, Any]:
    """Per-column repetition / singularity / sparsity + memorization copy-ratio,
    computed in-warehouse (no row download) and generic across schemas."""
    cols = _columns(client, landing_fqn)
    src_cols = {c["name"] for c in _columns(client, source_fqn)}
    src_n = _scalar(client, f"SELECT COUNT(*) FROM {_quote(source_fqn)}")
    lnd_n = _scalar(client, f"SELECT COUNT(*) FROM {_quote(landing_fqn)}")

    per_col: dict[str, Any] = {}
    for c in cols:
        name = c["name"]
        col = f"`{name}`"
        is_numeric = c["type"] in {"INT64", "INTEGER", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
        zero_expr = f"COUNTIF(SAFE_CAST({col} AS FLOAT64) = 0)" if is_numeric else "0"
        # One pass over the landing table for the cheap marginals.
        agg = _row(
            client,
            f"""
            SELECT
              COUNT(*) AS n,
              COUNT(DISTINCT {col}) AS distinct_n,
              COUNTIF({col} IS NULL) AS null_n,
              {zero_expr} AS zero_n,
              APPROX_TOP_COUNT({col}, 1)[SAFE_OFFSET(0)].count AS top_count
            FROM {_quote(landing_fqn)}
            """,
        )
        n = agg["n"] or 0
        entry: dict[str, Any] = {
            "type": c["type"],
            "in_source_schema": name in src_cols,
            "landing_rows": n,
            "distinct": agg["distinct_n"],
            "distinct_ratio": _ratio(agg["distinct_n"], n),
            "null_fraction": _ratio(agg["null_n"], n),
            "zero_fraction": _ratio(agg["zero_n"], n) if is_numeric else None,
            "top_value_share": _ratio(agg["top_count"], n),  # repetition
            "is_constant": (agg["distinct_n"] or 0) <= 1,     # singularity
            "is_pk": name in pk_columns,
        }
        # Memorization: fraction of landing values that also exist in source.
        # Sentinel-aware (2026-07-23 b1_rag run): "0001-01-01"/"9999-12-31"
        # null-substitutes are re-injected at observed frequency BY DESIGN
        # (engine sentinel parity) and always exist in source, so the raw
        # copy_ratio conflates them with real copying — measure them apart.
        # `day_shaped_n` detects day-granularity values (dates render as
        # exactly `YYYY-MM-DD`) whose in-source collisions are a
        # domain-size artifact, not per-row memorization (see
        # `memorization_flags`). SAFE_CAST: BYTES columns must not kill the
        # probe on invalid UTF-8.
        if name in src_cols:
            sentinel_re = r"'^(0001|9999)-'"
            day_re = r"'^\d{4}-\d{2}-\d{2}$'"
            in_src = (
                f"{col} IN (SELECT DISTINCT {col} FROM {_quote(source_fqn)})"
            )
            mem = _row(
                client,
                f"""
                SELECT
                  COUNTIF({in_src}) AS copied,
                  COUNTIF(REGEXP_CONTAINS(
                      SAFE_CAST({col} AS STRING), {sentinel_re})) AS sentinel_n,
                  COUNTIF({in_src} AND NOT IFNULL(REGEXP_CONTAINS(
                      SAFE_CAST({col} AS STRING), {sentinel_re}), FALSE))
                    AS copied_nonsentinel,
                  COUNTIF(REGEXP_CONTAINS(
                      SAFE_CAST({col} AS STRING), {day_re})) AS day_shaped_n
                FROM {_quote(landing_fqn)}
                """,
            )
            src_distinct = _scalar(
                client, f"SELECT COUNT(DISTINCT {col}) FROM {_quote(source_fqn)}"
            )
            copied = mem["copied"]
            sentinel_n = mem["sentinel_n"] or 0
            non_null = n - (agg["null_n"] or 0)
            entry["source_distinct"] = src_distinct
            entry["copy_ratio"] = _ratio(copied, n)          # memorization
            entry["novelty_ratio"] = _ratio((n - (copied or 0)), n)
            entry["sentinel_fraction"] = _ratio(sentinel_n, n)
            entry["copy_ratio_nonsentinel"] = _ratio(
                mem["copied_nonsentinel"], n - sentinel_n
            )
            entry["temporal_day_granularity"] = bool(
                non_null > 0 and (mem["day_shaped_n"] or 0) >= 0.99 * non_null
            )
        per_col[name] = entry

    return {
        "source_fqn": source_fqn,
        "landing_fqn": landing_fqn,
        "source_rows": src_n,
        "landing_rows": lnd_n,
        "pk_columns": pk_columns,
        "pk_analysis": _pk_analysis(client, landing_fqn, pk_columns),
        "columns": per_col,
        "memorization_flags": memorization_flags(per_col),
    }


# A non-constant column whose source support is genuinely large (> 100
# distinct values — not an enum whose full coverage is by-design) must not
# land > 30 % verbatim source values. 2026-07-16 b1_rag run: 9 such columns
# sat at copy_ratio 0.475-0.939 while validation_runs said PASSED, because
# no rule anywhere scored memorization.
_MEM_MIN_SOURCE_DISTINCT = 100
_MEM_COPY_RATIO_THRESHOLD = 0.3


def memorization_flags(columns: dict[str, Any]) -> list[dict[str, Any]]:
    """Memorization findings from `bq_cross_validation` per-column entries:
    non-constant, source_distinct > 100, scored ratio >= 0.3. Sorted
    worst-first (CRITICAL before INFO). Columns without a measured
    copy_ratio (not in the source schema, or an empty landing table →
    `_ratio` returned None) are skipped — absence of measurement is not
    evidence of safety, but it is not a flag.

    Sentinel/temporal awareness (2026-07-23 b1_rag run: 6 date-shaped
    columns false-flagged CRITICAL at copy_ratio 0.60-0.97): the scored
    ratio excludes sentinel rows when measured (`copy_ratio_nonsentinel`)
    — sentinel parity is by-design fidelity, not copying — and
    day-granularity temporal columns downgrade to INFO: a calendar day
    drawn from the clamped ~3650-day window collides with a dense source
    by domain size, never identifying a source row."""
    flags = []
    for name, entry in columns.items():
        copy_ratio = entry.get("copy_ratio")
        scored = entry.get("copy_ratio_nonsentinel")
        if scored is None:
            scored = copy_ratio
        source_distinct = entry.get("source_distinct")
        if scored is None or source_distinct is None:
            continue
        if entry.get("is_constant"):
            continue
        if (
            source_distinct > _MEM_MIN_SOURCE_DISTINCT
            and scored >= _MEM_COPY_RATIO_THRESHOLD
        ):
            day_granularity = bool(entry.get("temporal_day_granularity"))
            base_rule = (
                f"copy_ratio >= {_MEM_COPY_RATIO_THRESHOLD} AND "
                f"source_distinct > {_MEM_MIN_SOURCE_DISTINCT}"
            )
            flags.append(
                {
                    "column": name,
                    "type": entry.get("type"),
                    "copy_ratio": copy_ratio,
                    "copy_ratio_nonsentinel": entry.get("copy_ratio_nonsentinel"),
                    "source_distinct": source_distinct,
                    "severity": "INFO" if day_granularity else "CRITICAL",
                    "rule": (
                        base_rule
                        + " (day-granularity temporal domain: in-source "
                        "collisions expected by domain size, not per-row "
                        "memorization)"
                        if day_granularity
                        else base_rule
                    ),
                }
            )
    return sorted(
        flags,
        key=lambda f: (f["severity"] == "CRITICAL", f["copy_ratio"] or 0),
        reverse=True,
    )


def _pk_analysis(client, landing_fqn: str, pk_columns: list[str]) -> dict[str, Any]:
    if not pk_columns:
        return {"declared": False}
    key = ", ".join(f"`{c}`" for c in pk_columns)
    row = _row(
        client,
        f"""
        WITH g AS (
          SELECT {key} AS k, COUNT(*) AS c
          FROM {_quote(landing_fqn)} GROUP BY {key}
        )
        SELECT COUNT(*) AS distinct_keys, SUM(c) AS total,
               MAX(c) AS max_repeat, COUNTIF(c > 1) AS dup_keys
        FROM g
        """,
    )
    total = row["total"] or 0
    return {
        "declared": True,
        "columns": pk_columns,
        "distinct_keys": row["distinct_keys"],
        "total_rows": total,
        "duplicate_keys": row["dup_keys"],
        "max_repeat": row["max_repeat"],
        "uniqueness_ratio": _ratio(row["distinct_keys"], total),
    }


def bq_quality(client, quality_dataset: str, run_ids: list[str]) -> dict[str, Any]:
    if not quality_dataset:
        return {}
    out: dict[str, Any] = {}
    proj, ds, _ = _split_fqn(quality_dataset + ".x")
    job_config = None
    if run_ids:
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("run_ids", "STRING", run_ids)]
        )
    for table in ("validation_runs", "dlq"):
        fqn = f"{proj}.{ds}.{table}"
        if run_ids:
            sql = (
                f"SELECT * FROM {_quote(fqn)} "
                "WHERE run_id IN UNNEST(@run_ids) ORDER BY 1 DESC LIMIT 50"
            )
        else:
            sql = f"SELECT * FROM {_quote(fqn)} ORDER BY 1 DESC LIMIT 50"
        try:
            rows = [dict(r) for r in client.query(sql, job_config=job_config).result()]
            out[table] = _jsonable(rows)
        except Exception as e:
            out[table] = {"error": f"{type(e).__name__}: {e}"}
    return out


# --------------------------------------------------------------------------
# Dataflow observability
# --------------------------------------------------------------------------
def dataflow_job(
    session, project: str, region: str, job_id: str, milestones: list[tuple[str, str]]
) -> dict[str, Any]:
    base = f"{_DATAFLOW_BASE}/projects/{project}/locations/{region}/jobs/{job_id}"
    j = session.get(base, params={"view": "JOB_VIEW_ALL"}).json()
    if "error" in j:
        return {"job_id": job_id, "error": j["error"]}

    timing = _job_timing(j)
    result = {
        "job_id": job_id,
        "name": j.get("name"),
        "type": j.get("type"),
        "state": j.get("currentState"),
        "timing": timing,
        "environment": _job_env(j),
        "parameters": _job_params(j),
        "step_count": len(j.get("steps", [])),
        "job_phases": _job_messages(session, base),
        "metrics": _job_metrics(session, base),
        "engine_milestones": _worker_log_milestones(
            session, project, job_id, milestones, timing
        ),
    }
    return result


def _job_timing(j: dict) -> dict[str, Any]:
    create = j.get("createTime")
    start = j.get("startTime") or create
    end = j.get("currentStateTime")
    return {
        "create_time": create,
        "start_time": start,
        "end_time": end,
        "execution_seconds": _duration(start, end),
        "state": j.get("currentState"),
    }


def _job_env(j: dict) -> dict[str, Any]:
    env = j.get("environment", {})
    pools = env.get("workerPools", [])
    experiments = env.get("experiments", [])
    accelerator = None
    for e in experiments:
        if isinstance(e, str) and e.startswith("worker_accelerator="):
            accelerator = e.split("=", 1)[1]
            break
    return {
        "machine_types": [p.get("machineType") for p in pools],
        "num_workers": [p.get("numWorkers") for p in pools],
        "max_workers": env.get("maxWorkers"),
        "accelerator": accelerator,  # e.g. type:nvidia-tesla-t4;count:1;...
        "worker_image": next(
            (
                e.split("=", 1)[1]
                for e in experiments
                if isinstance(e, str) and e.startswith("sdk_container_image=")
            ),
            None,
        ),
    }


def _job_params(j: dict) -> dict[str, Any]:
    """Launch/effective pipeline parameters from the job's display data.

    Custom flex-template options (`reference_rows_limit`, `pk_cols`,
    `identity_cols`, `seed`, ...) surface only here, under their options-class
    namespace — the 2026-07-16 report could not confirm what the run was
    launched with because the probe never extracted them. Two shapes exist:
    `environment.sdkPipelineOptions.display_data` entries carry a plain
    `value`; `pipelineDescription.displayData` entries carry typed fields
    (`strValue` / `int64Value` / `boolValue` / ...). First occurrence of a
    key wins."""
    entries: list = []
    env = j.get("environment") or {}
    sdk = env.get("sdkPipelineOptions") or {}
    entries.extend(sdk.get("display_data") or [])
    entries.extend((j.get("pipelineDescription") or {}).get("displayData") or [])
    params: dict[str, Any] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        key = e.get("key")
        if not key or key in params:
            continue
        for field in (
            "value",
            "strValue",
            "int64Value",
            "boolValue",
            "floatValue",
            "timestampValue",
            "durationValue",
            "javaClassValue",
            "shortStrValue",
        ):
            if e.get(field) is not None:
                params[str(key)] = e[field]
                break
    return params


# Job-message text markers → milestone label. Applied to JOB_MESSAGE_BASIC text
# to derive wall-clock phases (generic Dataflow wording; not table-specific).
_MSG_MARKERS: list[tuple[str, str]] = [
    ("launcher_start", r"Starting GCE instance.*launch the template"),
    ("worker_config", r"Worker configuration:"),
    ("workers_starting", r"Starting \d+ workers?"),
    ("workers_ready", r"All workers have finished the startup"),
    ("autoscale", r"(?i)autoscal"),
    ("cleanup", r"(?i)Cleaning up|Tearing down|Stopping worker pool"),
]


def _job_messages(session, base: str) -> dict[str, Any]:
    """Return derived phase milestones + the dominant (longest) fused stage."""
    raw: list[dict[str, str]] = []
    token = None
    for _ in range(12):  # bounded paging
        params = {"minimumImportance": "JOB_MESSAGE_BASIC", "pageSize": 100}
        if token:
            params["pageToken"] = token
        r = session.get(base + "/messages", params=params).json()
        for m in r.get("jobMessages", []):
            raw.append({"time": m.get("time"), "text": m.get("messageText", "")})
        token = r.get("nextPageToken")
        if not token:
            break
    raw.sort(key=lambda m: m["time"] or "")

    markers: dict[str, str] = {}
    compiled = [(lbl, re.compile(pat)) for lbl, pat in _MSG_MARKERS]
    exec_start: dict[str, str] = {}
    stage_durations: list[tuple[float, str, str, str]] = []
    for m in raw:
        txt, ts = m["text"], m["time"]
        for lbl, rx in compiled:
            if lbl not in markers and rx.search(txt):
                markers[lbl] = ts
        em = re.match(r"Executing operation (.+)", txt)
        fm = re.match(r"Finished operation (.+)", txt)
        if em:
            exec_start.setdefault(em.group(1), ts)
        elif fm:
            op = fm.group(1)
            if op in exec_start:
                d = _duration(exec_start[op], ts)
                if d is not None:
                    stage_durations.append((d, op[:80], exec_start[op], ts))
    stage_durations.sort(reverse=True)
    return {
        "message_count": len(raw),
        "phase_markers": markers,
        "dominant_stages": [
            {"seconds": d, "operation": op, "start": s, "end": e}
            for d, op, s, e in stage_durations[:5]
        ],
    }


# Dataflow metric names worth surfacing (resource + custom counters).
_RESOURCE_METRICS = frozenset(
    {
        "TotalVcpuTime",
        "TotalGpuTime",
        "TotalMemoryUsage",
        "TotalShuffleDataProcessed",
        "TotalStreamingDataProcessed",
        "TotalPdUsage",
    }
)
# Custom user counters emitted by the pipeline DoFns / RunInference.
_CUSTOM_COUNTERS = frozenset(
    {"pydantic_valid", "pydantic_invalid", "yielded", "failed", "generated"}
)


def _job_metrics(session, base: str) -> dict[str, Any]:
    r = session.get(base + "/metrics").json()
    resources: dict[str, Any] = {}
    counters: dict[str, Any] = {}
    element_counts: dict[str, Any] = {}
    exec_time: dict[str, Any] = {}
    for m in r.get("metrics", []):
        name = m.get("name", {})
        metric = name.get("name")
        ctx = name.get("context", {})
        if ctx.get("tentative") == "true":
            continue
        scalar = m.get("scalar")
        if metric in _RESOURCE_METRICS:
            resources[metric] = scalar
        elif metric in _CUSTOM_COUNTERS:
            key = f"{ctx.get('namespace', '')}.{metric}".strip(".")
            counters[key] = scalar
        elif metric == "ElementCount":
            out = ctx.get("output_user_name")
            if out and "/" not in out[8:]:  # keep top-level PCollections only
                element_counts[out] = scalar
        elif metric == "ExecutionTime_ProcessBundle":
            step = ctx.get("step")
            if step:
                exec_time[step] = scalar
    return {
        "resource_totals": resources,
        "custom_counters": counters,
        "top_element_counts": dict(
            sorted(element_counts.items(), key=lambda kv: -(kv[1] or 0))[:12]
        ),
        "process_bundle_msec_by_step": dict(
            sorted(exec_time.items(), key=lambda kv: -(kv[1] or 0))[:12]
        ),
    }


def _worker_log_milestones(
    session,
    project: str,
    job_id: str,
    milestones: list[tuple[str, str]],
    timing: dict[str, Any],
) -> dict[str, Any]:
    """Cloud Logging: first timestamp per milestone regex + derived durations.

    Filters on the Dataflow worker logName (the ``resource.type`` filter alone
    returns nothing in this org's log routing) and constrains to the job's time
    window — without a timestamp bound, Cloud Logging returns empty first pages
    with a continuation token, so we also page past empties."""
    lo = _shift(timing.get("create_time"), -120)
    hi = _shift(timing.get("end_time"), 180)
    time_clause = ""
    if lo and hi:
        time_clause = f' timestamp>="{lo}" timestamp<="{hi}"'
    log_filter = (
        f'logName="projects/{project}/logs/dataflow.googleapis.com%2Fworker" '
        f'resource.labels.job_id="{job_id}"{time_clause}'
    )
    body = {
        "resourceNames": [f"projects/{project}"],
        "filter": log_filter,
        "orderBy": "timestamp asc",
        "pageSize": 1000,
    }
    found: dict[str, str] = {}
    compiled = [(label, re.compile(pat)) for label, pat in milestones]
    stall_rx = re.compile(r"creating for at least ([\d.]+) seconds")
    pkg_rx = re.compile(r"^(vllm|sdgx|torch|faiss[-\w]*|transformers)==([\w.]+)")
    token = None
    scanned = 0
    stall_max = 0.0
    packages: dict[str, str] = {}
    for _ in range(15):  # bounded paging
        if token:
            body["pageToken"] = token
        r = _post_with_retry(session, _LOGGING_URL, body)
        if "error" in r:
            return {"error": r["error"].get("message", "logging error")}
        for e in r.get("entries", []):
            scanned += 1
            text = _entry_text(e)
            ts = e.get("timestamp")
            sm2 = _SDFB_MILESTONE_RE.search(text)
            if sm2:
                found.setdefault(f"sdfb.{sm2.group('name')}", ts)
            for label, rx in compiled:
                if label not in found and rx.search(text):
                    found[label] = ts
            sm = stall_rx.search(text)
            if sm:
                stall_max = max(stall_max, float(sm.group(1)))
            for line in text.splitlines():
                pm = pkg_rx.match(line.strip())
                if pm:
                    packages[pm.group(1)] = pm.group(2)
        token = r.get("nextPageToken")
        if not token:
            break
    return {
        "scanned_entries": scanned,
        "timestamps": found,
        "durations_seconds": _milestone_durations(found, [m[0] for m in milestones]),
        "generation_stall_max_seconds": round(stall_max, 1) if stall_max else None,
        "worker_packages": packages,
    }


def _post_with_retry(session, url: str, body: dict, *, attempts: int = 5) -> dict:
    """POST that backs off on transient 429/5xx (Logging read-quota etc.)."""
    import time

    delay = 2.0
    resp = None
    for i in range(attempts):
        resp = session.post(url, json=body)
        if resp.status_code in (429, 500, 503) and i < attempts - 1:
            time.sleep(delay)
            delay *= 2
            continue
        break
    return resp.json()


def _entry_text(e: dict) -> str:
    if "textPayload" in e:
        return e["textPayload"]
    jp = e.get("jsonPayload") or {}
    return jp.get("message") or json.dumps(jp)[:500]


def _milestone_durations(found: dict[str, str], order: list[str]) -> dict[str, float]:
    from itertools import pairwise

    # Chronological ordering (not the regex order) so derived gaps are the
    # real wall-clock deltas between successive observed milestones.
    present = sorted(found.items(), key=lambda kv: kv[1])
    out: dict[str, float] = {}
    for (a_lbl, a_ts), (b_lbl, b_ts) in pairwise(present):
        d = _duration(a_ts, b_ts)
        if d is not None:
            out[f"{a_lbl}->{b_lbl}"] = d
    return out


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _scalar(client, sql: str):
    return next(iter(client.query(sql).result()))[0]


def _row(client, sql: str) -> dict[str, Any]:
    r = next(iter(client.query(sql).result()))
    return {k: r[k] for k in r.keys()}  # noqa: SIM118 - BQ Row.keys(), not a dict


def _ratio(num, den) -> float | None:
    if not den:
        # An empty (or all-excluded) denominator means nothing was measured —
        # returning 0.0 here would read as "measured and found to be zero"
        # (e.g. copy_ratio=0.0 misreported as "no memorization" on an empty
        # landing table). None means "not computable", not "computed as zero".
        return None
    if num is None:
        return None
    return round(num / den, 6)


def _duration(start: str | None, end: str | None) -> float | None:
    from datetime import datetime

    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return round((e - s).total_seconds(), 3)
    except ValueError:
        return None


def _shift(ts: str | None, seconds: int) -> str | None:
    """Return an RFC3339 timestamp shifted by ``seconds`` (for log windows)."""
    from datetime import datetime, timedelta

    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00")) + timedelta(
            seconds=seconds
        )
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None



def _jsonable(obj):
    return json.loads(json.dumps(obj, default=str))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    ap.add_argument("--source-fqn", required=True, help="project.dataset.table")
    ap.add_argument("--landing-fqn", required=True, help="project.dataset.table")
    ap.add_argument("--quality-dataset", default="", help="project.dataset")
    ap.add_argument("--region", default="europe-west3")
    ap.add_argument("--job-id", action="append", default=[], dest="job_ids")
    ap.add_argument("--pk", default="", help="comma-separated PK columns")
    ap.add_argument("--run-id", action="append", default=[], dest="run_ids")
    ap.add_argument(
        "--engine-label",
        action="append",
        default=[],
        dest="engine_labels",
        help="repeatable 'label=job_id' mapping stamped onto the matching dataflow result",
    )
    ap.add_argument(
        "--milestones",
        default="",
        help="optional 'label=regex;label=regex' overrides for log mining",
    )
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    pk_columns = [c.strip() for c in args.pk.split(",") if c.strip()]
    milestones = _parse_milestones(args.milestones) or _DEFAULT_MILESTONES
    engine_labels = _parse_engine_labels(args.engine_labels)

    session, _ = preflight_adc(args.project)
    identity = _whoami(session)
    client = _bq_client(args.project)

    dataflow_results = [
        dataflow_job(session, args.project, args.region, jid, milestones)
        for jid in args.job_ids
    ]
    _annotate_engine_labels(dataflow_results, engine_labels)

    report: dict[str, Any] = {
        "project": args.project,
        "caller_identity": identity,
        "bigquery": bq_cross_validation(
            client, args.source_fqn, args.landing_fqn, pk_columns=pk_columns
        ),
        "quality": bq_quality(client, args.quality_dataset, args.run_ids),
        "dataflow": dataflow_results,
    }

    from pathlib import Path

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {out}  (caller={identity})")
    return 0


main_with_args = main


def _whoami(session) -> str:
    try:
        # userinfo rejects the x-goog-user-project quota header — send bare.
        r = session.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"x-goog-user-project": None},
        ).json()
        return r.get("email") or r.get("sub") or "unknown"
    except Exception:
        return "unknown"


def _parse_milestones(spec: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for part in spec.split(";"):
        if "=" in part:
            label, pat = part.split("=", 1)
            out.append((label.strip(), pat.strip()))
    return out


def _parse_engine_labels(specs: list[str]) -> dict[str, str]:
    """Repeatable --engine-label label=job_id → {job_id: label}."""
    out: dict[str, str] = {}
    for spec in specs:
        if "=" in spec:
            label, job_id = spec.split("=", 1)
            out[job_id.strip()] = label.strip()
    return out


def _annotate_engine_labels(results: list[dict[str, Any]], labels: dict[str, str]) -> None:
    """Stamp ``engine_label`` on each dataflow result whose job_id matches."""
    for r in results:
        job_id = r.get("job_id")
        if job_id in labels:
            r["engine_label"] = labels[job_id]


if __name__ == "__main__":
    raise SystemExit(main())
