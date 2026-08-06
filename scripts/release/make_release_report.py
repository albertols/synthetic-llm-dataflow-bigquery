#!/usr/bin/env python
"""Release report logic: SemVer bump parsing, artifact discovery at git refs,
and metric-delta computation.

This is the "logic half" of the release report generator — pure functions
with no matplotlib, no BigQuery, no redaction. `render_report` / `make_charts`
/ the CLI `main` are Task 7's job and land in this same module later.

Artifact discovery reads committed `integration_test/<JOB_ID>/*.json` files
straight out of git (via `git ls-tree` + `git show` against an arbitrary
ref/tag/tree), so the release Action needs no GCP credentials and no working
copy of the artifacts — only the git history.

`compute_deltas` walks a fixed metric map grounded in the real producer
scripts (not guessed key names):
  * `scripts/e2e/e2e_gcp_probe.py` → `e2e_gcp_metrics.json` ("gcp"): a
    `dataflow` list of per-Dataflow-job dicts, each carrying
    `timing.execution_seconds`, `job_phases.dominant_stages[].seconds`,
    `metrics.custom_counters.{namespace}.{metric}` (e.g.
    `generation.yielded`, `generation.failed` — see
    `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py`; the probe
    namespaces every Beam counter as `f"{namespace}.{metric}"`, so lookups
    here match by bare metric-name suffix, not exact key), and
    `engine_milestones.durations_seconds["label_a->label_b"]`.
  * `scripts/e2e/e2e_validation_analysis.py` → `e2e_validation_metrics.json`
    ("offline"): `engines.<label>.full_row_duplicate_ratio` and
    `engines.<label>.columns.<col>.top_value_share`.
  * `scripts/e2e/freetext_crosscheck.py` → `freetext_crosscheck_metrics.json`
    ("crosscheck"): `columns.<col>.diff.{shape_recall,shape_precision,
    copy_fraction}`.
  * `scripts/e2e/source_synthetic_stats_diff.py` → `stats_diff.json`
    ("stats_diff"): `columns.<col>.{entropy_gap,decile_ks}`.

Every one of those reads goes through defensive `dict.get` chains: these
artifact schemas predate this script, vary release to release, and a missing
metric must render as `None` ("not measured"), never raise.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

# --------------------------------------------------------------------------
# SemVer bump parsing (Conventional Commits merge-commit title -> bump size)
# --------------------------------------------------------------------------
_TITLE_RE = re.compile(r"^(?P<type>[a-z]+)(\([^)]*\))?(?P<bang>!)?:")


def parse_bump(title: str) -> str:
    """"major" | "minor" | "patch" from a Conventional Commits title.

    `!` after the type/scope or a "BREAKING CHANGE" marker anywhere in the
    title forces "major"; `feat` is "minor"; everything else (including
    non-conventional titles) is "patch"."""
    m = _TITLE_RE.match(title.strip())
    if not m:
        return "patch"
    if m.group("bang") or "BREAKING CHANGE" in title:
        return "major"
    return "minor" if m.group("type") == "feat" else "patch"


def next_version(prev: str | None, bump: str) -> str:
    """Next "vMAJOR.MINOR.PATCH" tag. `prev=None` -> "v0.1.0" (first tag).

    Pre-1.0 (major == 0) demotes a "major" bump to "minor" — the project
    hasn't committed to a stable public API yet, so nothing before v1.0.0
    should auto-bump the major version."""
    if prev is None:
        return "v0.1.0"
    major, minor, patch = (int(x) for x in prev.lstrip("v").split("."))
    if major == 0 and bump == "major":
        bump = "minor"
    if bump == "major":
        return f"v{major + 1}.0.0"
    if bump == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


# --------------------------------------------------------------------------
# Artifact discovery at a git ref (tag, branch, commit, or bare tree sha)
# --------------------------------------------------------------------------
# "integration_test/<job_id>/<basename>" — minimum path-segment count for a
# discoverable artifact file.
_ARTIFACT_PATH_PARTS = 3

# basename -> key in the per-job artifact-set dict.
_ARTIFACT_BASENAMES: dict[str, str] = {
    "e2e_gcp_metrics.json": "gcp",
    "e2e_validation_metrics.json": "offline",
    "freetext_crosscheck_metrics.json": "crosscheck",
    "stats_diff.json": "stats_diff",
}


def _run_git(*args: str) -> str | None:
    """`check=True` subprocess wrapper; returns None (not a crash) on any
    git failure — a ref with no `integration_test` tree, an unreadable
    object, or a bad ref must degrade gracefully, never raise."""
    try:
        return subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return None


def discover_artifact_sets(ref: str) -> dict[str, dict]:
    """job_id -> {"gcp": ..., "offline": ..., "crosscheck": ..., "stats_diff":
    ...} for every `integration_test/<job_id>/<known_basename>.json` present
    at `ref`. Files that don't exist for a given job are simply absent from
    that job's dict (never a crash, never a placeholder). A `ref` with no
    `integration_test` tree at all (e.g. an early tag, or the empty tree)
    yields `{}`."""
    listing = _run_git("ls-tree", "-r", "--name-only", ref, "--", "integration_test")
    if not listing:
        return {}

    sets: dict[str, dict] = {}
    for line in listing.splitlines():
        path = line.strip()
        if not path:
            continue
        parts = path.split("/")
        if len(parts) < _ARTIFACT_PATH_PARTS or parts[0] != "integration_test":
            continue
        job_id, basename = parts[1], parts[-1]
        key = _ARTIFACT_BASENAMES.get(basename)
        if key is None:
            continue
        raw = _run_git("show", f"{ref}:{path}")
        if raw is None:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        sets.setdefault(job_id, {})[key] = parsed
    return sets


def latest_job(sets: dict) -> str | None:
    """Lexicographically greatest job id, or None for an empty artifact-set
    dict. Job ids start with a `YYYY-MM-DD_HH_MM_SS-...` timestamp, so
    lexicographic order is chronological order."""
    if not sets:
        return None
    return max(sets)


# --------------------------------------------------------------------------
# compute_deltas — pure metric-delta computation over two artifact sets
# --------------------------------------------------------------------------
def _metric(base_value: Any, head_value: Any) -> dict[str, Any]:
    delta = (
        head_value - base_value
        if base_value is not None and head_value is not None
        else None
    )
    return {"base": base_value, "head": head_value, "delta": delta}


def _first_job(gcp: dict | None) -> dict:
    """The primary Dataflow job dict from a "gcp" artifact. Real
    `e2e_gcp_metrics.json` output nests jobs under "dataflow"; simpler/older
    or hand-built fixtures may use a flat "jobs" list — support both."""
    gcp = gcp or {}
    jobs = gcp.get("jobs")
    if jobs is None:
        jobs = gcp.get("dataflow")
    jobs = jobs or []
    return jobs[0] if jobs else {}


def _execution_seconds(job: dict) -> float | None:
    v = job.get("execution_seconds")
    if v is not None:
        return v
    return (job.get("timing") or {}).get("execution_seconds")


def _dominant_stage_seconds(job: dict) -> float | None:
    stages = (job.get("job_phases") or {}).get("dominant_stages") or []
    return stages[0].get("seconds") if stages else None


def _custom_counter(job: dict, name: str) -> float | None:
    """Look up a Beam custom counter by its bare metric name, tolerant of the
    `f"{namespace}.{metric}"` key format `e2e_gcp_probe.py`'s `_job_metrics`
    actually writes (e.g. `Metrics.counter("generation", "failed")` lands as
    `"generation.failed"`, never bare `"failed"`). Exact match wins first
    (covers any future un-namespaced counter); otherwise the first key whose
    namespace-qualified suffix matches."""
    counters = (job.get("metrics") or {}).get("custom_counters") or {}
    if name in counters:
        return counters[name]
    return next(
        (v for k, v in counters.items() if k == name or k.endswith(f".{name}")),
        None,
    )


def _milestone_durations(job: dict) -> dict[str, float]:
    return (job.get("engine_milestones") or {}).get("durations_seconds") or {}


def _vllm_ignition_seconds(job: dict) -> float | None:
    return _milestone_durations(job).get("vllm_engine_init->vllm_ready")


def _tokens_per_s(job: dict) -> float | None:
    # No producer script emits a "tokens" custom counter today, so this is
    # None on nearly every real run — that's the correct "not measured"
    # answer, not a bug: it becomes a live metric the moment one does.
    tokens = _custom_counter(job, "tokens")
    duration = _vllm_ignition_seconds(job)
    # `is not None` (not `not duration`) matches this module's convention
    # elsewhere — a falsy-but-measured 0.0 must not silently read as
    # "unmeasured"; a zero duration is instead an explicit divide-by-zero
    # guard.
    if tokens is None or duration is None or duration == 0:
        return None
    return tokens / duration


def _offline_engines(offline: dict | None) -> dict:
    return (offline or {}).get("engines") or {}


def _dup_ratio_max(offline: dict | None) -> float | None:
    values = [
        e.get("full_row_duplicate_ratio")
        for e in _offline_engines(offline).values()
        if e.get("full_row_duplicate_ratio") is not None
    ]
    return max(values) if values else None


def _top_value_share_max(offline: dict | None) -> float | None:
    values = [
        col.get("top_value_share")
        for engine in _offline_engines(offline).values()
        for col in (engine.get("columns") or {}).values()
        if col.get("top_value_share") is not None
    ]
    return max(values) if values else None


def _crosscheck_columns(crosscheck: dict | None) -> dict:
    return (crosscheck or {}).get("columns") or {}


def _crosscheck_diff_values(crosscheck: dict | None, metric: str) -> list[float]:
    return [
        col.get("diff", {}).get(metric)
        for col in _crosscheck_columns(crosscheck).values()
        if col.get("diff", {}).get(metric) is not None
    ]


def _shape_recall_min(crosscheck: dict | None) -> float | None:
    values = _crosscheck_diff_values(crosscheck, "shape_recall")
    return min(values) if values else None


def _shape_precision_min(crosscheck: dict | None) -> float | None:
    values = _crosscheck_diff_values(crosscheck, "shape_precision")
    return min(values) if values else None


def _copy_fraction_max(crosscheck: dict | None) -> float | None:
    values = _crosscheck_diff_values(crosscheck, "copy_fraction")
    return max(values) if values else None


def _stats_columns(stats_diff: dict | None) -> dict:
    return (stats_diff or {}).get("columns") or {}


def _stats_metric_max(stats_diff: dict | None, metric: str) -> float | None:
    values = [
        col.get(metric)
        for col in _stats_columns(stats_diff).values()
        if col.get(metric) is not None
    ]
    return max(values) if values else None


def compute_deltas(
    base: dict | None,
    head: dict | None,
    *,
    base_job: str | None = None,
    head_job: str | None = None,
) -> dict:
    """Pure before/after metric diff between two artifact sets (each the
    per-job dict `discover_artifact_sets` returns: `{"gcp":..., "offline":
    ..., "crosscheck":..., "stats_diff":...}`).

    Returns `{"performance": {...}, "vllm": {...}, "quality": {...},
    "missing": [...], "base_job": ..., "head_job": ...}`. Every leaf metric
    is `{"base": x, "head": y, "delta": y - x}`, with `None` standing in for
    "not measured" anywhere a value is unavailable on one or both sides —
    never a crash, never a silently-invented zero. `missing` names which
    side(s) had no artifacts at all (`base` and/or `head`); `base_job` /
    `head_job` are passed through as-is for the caller to label the report
    with (this function never invents a job id).
    """
    missing = [name for name, side in (("base", base), ("head", head)) if not side]

    base_job_data = _first_job((base or {}).get("gcp"))
    head_job_data = _first_job((head or {}).get("gcp"))

    performance = {
        "execution_seconds": _metric(
            _execution_seconds(base_job_data), _execution_seconds(head_job_data)
        ),
        "dominant_stage_seconds": _metric(
            _dominant_stage_seconds(base_job_data), _dominant_stage_seconds(head_job_data)
        ),
        # "generated" is not a counter any DoFn emits — the real analog is
        # `generation.yielded` (packages/sdfb-beam/src/sdfb_beam/dofns/
        # generate.py:83); `_custom_counter` matches on the bare suffix, so
        # passing "yielded"/"failed" resolves the namespaced
        # "generation.yielded"/"generation.failed" keys the probe writes.
        "counter_yielded": _metric(
            _custom_counter(base_job_data, "yielded"),
            _custom_counter(head_job_data, "yielded"),
        ),
        "counter_failed": _metric(
            _custom_counter(base_job_data, "failed"), _custom_counter(head_job_data, "failed")
        ),
    }

    vllm = {
        "vllm_ignition_seconds": _metric(
            _vllm_ignition_seconds(base_job_data), _vllm_ignition_seconds(head_job_data)
        ),
        "tokens_per_s": _metric(_tokens_per_s(base_job_data), _tokens_per_s(head_job_data)),
    }

    base_offline, head_offline = (base or {}).get("offline"), (head or {}).get("offline")
    base_cross, head_cross = (base or {}).get("crosscheck"), (head or {}).get("crosscheck")
    base_stats, head_stats = (base or {}).get("stats_diff"), (head or {}).get("stats_diff")

    quality = {
        "dup_ratio_max": _metric(_dup_ratio_max(base_offline), _dup_ratio_max(head_offline)),
        "top_value_share_max": _metric(
            _top_value_share_max(base_offline), _top_value_share_max(head_offline)
        ),
        "shape_recall_min": _metric(
            _shape_recall_min(base_cross), _shape_recall_min(head_cross)
        ),
        "shape_precision_min": _metric(
            _shape_precision_min(base_cross), _shape_precision_min(head_cross)
        ),
        "copy_fraction_max": _metric(
            _copy_fraction_max(base_cross), _copy_fraction_max(head_cross)
        ),
        "entropy_gap_max": _metric(
            _stats_metric_max(base_stats, "entropy_gap"),
            _stats_metric_max(head_stats, "entropy_gap"),
        ),
        "decile_ks_max": _metric(
            _stats_metric_max(base_stats, "decile_ks"), _stats_metric_max(head_stats, "decile_ks")
        ),
    }

    return {
        "performance": performance,
        "vllm": vllm,
        "quality": quality,
        "missing": missing,
        "base_job": base_job,
        "head_job": head_job,
    }
