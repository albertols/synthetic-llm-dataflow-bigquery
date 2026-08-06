#!/usr/bin/env python
"""Release report generator: SemVer bump parsing, artifact discovery at git
refs, metric-delta computation (Task 6 — the "logic half"), and report
rendering, evolution charts, the redaction gate, and the CLI (Task 7 — the
"rendering half").

Usage (see `main()` / `--help` for the full flag set):

    # invoked by .github/workflows/release_tag_report.yaml to compute the tag
    python scripts/release/make_release_report.py \
        --print-next-version --title "<merge title>" [--prev vX.Y.Z]

    # invoked by the same workflow to render+commit the report
    python scripts/release/make_release_report.py \
        --version vX.Y.Z --date YYYY-MM-DD --head-ref <sha> [--base-ref vW.Y.Z] \
        --out-dir docs/releases

    # local dry run — prints the plan, writes nothing
    python scripts/release/make_release_report.py --dry-run

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

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless-safe: set before any pyplot import, module-wide.
import matplotlib.pyplot as plt  # must follow matplotlib.use("Agg")
import numpy as np

# Sibling import (scripts/e2e/ is not a package): the redaction gate reuses
# the exact Mapping/build_mapping/redact_text/leak_scan machinery
# e2e_bundle_export.py uses, extracted in Task 5 for this purpose.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
import redaction  # sibling import; path must be set up first

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


# ==========================================================================
# Rendering half (Task 7): report markdown, evolution charts, redaction
# gate, and the CLI. Everything above this line is pure (no matplotlib, no
# subprocess side effects beyond read-only `git show`/`git log`/`git tag`).
# ==========================================================================

# Direction of "improvement" is NOT uniform across metric names: quality
# metrics use `_max` suffixes for worst-case-HIGH ratios (e.g.
# `copy_fraction_max`, `entropy_gap_max` — lower is better) but
# `shape_recall_min` / `shape_precision_min` are worst-case-LOW (recall and
# precision — higher is better). `counter_yielded` (more generated rows) and
# `tokens_per_s` (throughput) are the other explicit higher-is-better
# metrics. Everything else (wall-clock `_seconds` timings, `counter_failed`,
# every `_max` quality ratio) defaults to lower-is-better.
_HIGHER_IS_BETTER = frozenset({"counter_yielded", "tokens_per_s"})

# The single "spine" metric used consistently as the release-over-release
# headline: the evolution chart, the index's "headline delta" column, and
# `_headline_delta()` are one source of truth pointing at this same
# (section, metric_name) pair. Wall-clock execution time is measured on
# every real E2E run and is universally comparable release to release.
_HEADLINE_METRIC = ("performance", "execution_seconds")


def _lower_is_better(name: str) -> bool:
    """`True` when a lower value of metric `name` is the improvement."""
    return not (name.endswith("_min") or name in _HIGHER_IS_BETTER)


def _delta_tag(name: str, delta: float | None) -> str:
    """"better" | "worse" | "unchanged" | "not measured" — direction-aware,
    per `_lower_is_better`, so an improvement reads as an improvement
    regardless of which way the raw number moved."""
    if delta is None:
        return "not measured"
    if delta == 0:
        return "unchanged"
    improved = (_lower_is_better(name) and delta < 0) or (
        not _lower_is_better(name) and delta > 0
    )
    return "better" if improved else "worse"


def _fmt(v: Any) -> str:
    """Render a metric leaf value; `None` always reads as the explicit
    "not measured" sentinel, never a bare "None" string."""
    if v is None:
        return "not measured"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _headline_delta(deltas: dict) -> str:
    section, name = _HEADLINE_METRIC
    m = (deltas.get(section) or {}).get(name) or {}
    delta = m.get("delta")
    tag = _delta_tag(name, delta)
    if delta is None:
        return f"{name}: not measured"
    return f"{name} Δ{delta:+.3g} ({tag})"


# --------------------------------------------------------------------------
# render_report — deterministic markdown, no matplotlib, no I/O
# --------------------------------------------------------------------------
_CHANGE_TYPE_ORDER = ("feat", "fix", "perf", "refactor", "docs", "test", "chore")
_CHANGE_TYPE_LABELS = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Performance",
    "refactor": "Refactors",
    "docs": "Docs",
    "test": "Tests",
    "chore": "Chores",
    "other": "Other",
}


def _render_change_summary(change_log: list[dict]) -> str:
    lines = ["## Change summary", ""]
    if not change_log:
        lines.append("_No commits recorded between base and head._")
        return "\n".join(lines)
    grouped: dict[str, list[str]] = {}
    for entry in change_log:
        change_type = entry.get("type") or "other"
        grouped.setdefault(change_type, []).append(entry.get("title") or "(untitled commit)")
    order = [t for t in _CHANGE_TYPE_ORDER if t in grouped]
    order += sorted(t for t in grouped if t not in order)
    for change_type in order:
        lines.append(f"### {_CHANGE_TYPE_LABELS.get(change_type, change_type.title())}")
        lines.extend(f"- {title}" for title in grouped[change_type])
        lines.append("")
    return "\n".join(lines)


def _metric_row(name: str, m: dict) -> str:
    base, head, delta = m.get("base"), m.get("head"), m.get("delta")
    tag = _delta_tag(name, delta)
    delta_cell = "not measured" if delta is None else f"{_fmt(delta)} ({tag})"
    return f"| {name} | {_fmt(base)} | {_fmt(head)} | {delta_cell} |"


_SECTION_HEADINGS = (("performance", "Performance"), ("vllm", "vLLM"), ("quality", "Quality"))


def _render_before_after(deltas: dict) -> str:
    lines = ["## Before / after", ""]
    missing = deltas.get("missing") or []
    # A missing side renders the explicit sentence below (never a silently
    # empty/zeroed table) — one bullet per missing side.
    for side in missing:
        lines.append(f"- **{side.capitalize()}:** no integration run landed for this release.")
    if missing:
        lines.append("")
    lines.append(
        f"Base job: `{deltas.get('base_job') or 'n/a'}` · "
        f"Head job: `{deltas.get('head_job') or 'n/a'}`"
    )
    lines.append("")
    for key, heading in _SECTION_HEADINGS:
        section = deltas.get(key) or {}
        lines.append(f"### {heading}")
        lines.append("")
        lines.append("| Metric | Base | Head | Delta |")
        lines.append("|---|---|---|---|")
        if not section:
            lines.append("| _(no metrics in this section)_ | | | |")
        else:
            lines.extend(_metric_row(name, m) for name, m in section.items())
        lines.append("")
    return "\n".join(lines)


_CHART_FILES = (
    ("step_time_before_after.png", "Step time — before vs after"),
    ("metric_evolution.png", "Metric evolution across releases"),
)


def _render_charts(history: list[tuple[str, dict]]) -> str:
    lines = ["## Charts", ""]
    for filename, caption in _CHART_FILES:
        lines.append(f"![{caption}](assets/{filename})")
        lines.append("")
    lines.append(
        f"_Evolution chart spans {len(history)} prior tagged release(s) plus this one "
        f"({_HEADLINE_METRIC[1]}, lower is better)._"
    )
    return "\n".join(lines)


def render_report(
    version: str,
    change_log: list[dict],
    deltas: dict,
    history: list[tuple[str, dict]],
) -> str:
    """Deterministic release-report markdown.

    `deltas["date"]` supplies the report date the same way `compute_deltas`
    already threads `base_job`/`head_job` through for the caller to label the
    report with (see that function's docstring) — `main()` stamps
    `deltas["date"]` from `--date` (never a hidden `datetime.now()`) before
    calling this function, so `render_report` itself stays a pure function of
    its four arguments. A fixture built by hand with no "date" key falls back
    to the explicit "unknown" sentinel rather than crashing.
    """
    date = deltas.get("date") or "unknown"
    parts = [
        f"# Release {version}",
        "",
        f"**Date:** {date}",
        "",
        _render_change_summary(change_log),
        "",
        _render_before_after(deltas),
        "",
        _render_charts(history),
        "",
        "---",
        "_Generated deterministically by `scripts/release/make_release_report.py` — "
        "insights layer: `.claude/skills/release-report/SKILL.md`._",
    ]
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# make_charts — matplotlib (Agg), dataviz-skill-conformant
# --------------------------------------------------------------------------
# Palette: the dataviz skill's validated default categorical slots 1 (blue)
# and 2 (orange) — `references/palette.md` in the `dataviz` skill.
# Re-validated for this exact pair via `scripts/validate_palette.py
# "#2a78d6,#eb6834" --mode light` before use: all five checks (lightness
# band, chroma floor, CVD separation, normal-vision floor, contrast) pass —
# worst adjacent CVD ΔE 24.7, normal-vision ΔE 33.6, well clear of the ≥8 /
# ≥15 gates. Charts are a static PNG embed (no dark-mode variant possible for
# a single raster), so they render on the light chart surface only — same
# convention as the existing `docs/designs/assets/` figure set.
_BLUE, _ORANGE = "#2a78d6", "#eb6834"
_INK, _MUTED, _GRID, _SURFACE = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"


def _style_axes(ax) -> None:
    ax.set_facecolor(_SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=9, length=0)
    ax.set_axisbelow(True)


def _title_axes(ax, text: str, sub: str | None = None) -> None:
    ax.set_title(text, color=_INK, fontsize=12.5, fontweight="600", loc="left", pad=26)
    if sub:
        ax.text(
            0, 1.025, sub, transform=ax.transAxes, color=_MUTED, fontsize=9, va="bottom"
        )


def _empty_axes(ax, message: str) -> None:
    """Shared placeholder for the "nothing measured yet" case — both charts
    must render a valid PNG even with zero data points (empty history, no
    metric measured on both sides), never crash or skip the file."""
    ax.set_facecolor(_SURFACE)  # matches the non-empty branch's _style_axes;
    # skipping it would leave matplotlib's default white axes rectangle
    # visible against the cream figure background.
    ax.text(
        0.5, 0.5, message, ha="center", va="center", color=_MUTED, fontsize=11,
        transform=ax.transAxes, wrap=True,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def _timing_rows(deltas: dict) -> list[tuple[str, float, float]]:
    """(metric_name, base_seconds, head_seconds) for every `*_seconds` metric
    measured on BOTH sides, from the sections that actually carry step/stage
    timings (performance + vllm) — quality ratios are not "time"."""
    rows: list[tuple[str, float, float]] = []
    for section in ("performance", "vllm"):
        for name, m in (deltas.get(section) or {}).items():
            if not name.endswith("_seconds"):
                continue
            base, head = m.get("base"), m.get("head")
            if base is None or head is None:
                continue
            rows.append((name, base, head))
    return rows


def _chart_step_time_before_after(deltas: dict, assets_dir: Path) -> Path:
    rows = _timing_rows(deltas)
    fig, ax = plt.subplots(figsize=(9, 5), facecolor=_SURFACE)
    if not rows:
        _empty_axes(ax, "no step timing measured on both sides for this release")
    else:
        labels = [r[0] for r in rows]
        base_vals = [r[1] for r in rows]
        head_vals = [r[2] for r in rows]
        x = np.arange(len(labels))
        width = 0.34
        bars_base = ax.bar(x - width / 2, base_vals, width, color=_BLUE, label="base", zorder=3)
        bars_head = ax.bar(x + width / 2, head_vals, width, color=_ORANGE, label="head", zorder=3)
        ax.bar_label(bars_base, fmt="%.1f", padding=3, color=_MUTED, fontsize=8)
        ax.bar_label(bars_head, fmt="%.1f", padding=3, color=_MUTED, fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=15, ha="right", fontsize=9, color=_INK)
        ax.set_ylabel("seconds", color=_MUTED, fontsize=9)
        ax.grid(axis="y", color=_GRID, linewidth=0.8, alpha=0.9)
        ax.legend(frameon=False, loc="upper right", fontsize=9, labelcolor=_INK)
        _style_axes(ax)
    _title_axes(ax, "Step time — before vs after", "lower is better")
    fig.tight_layout()
    out = assets_dir / "step_time_before_after.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def _evolution_points(
    deltas: dict, history: list[tuple[str, dict]]
) -> list[tuple[str, float]]:
    """(tag_or_"current", head_value) across every prior tagged release
    (oldest first) plus this release, for the headline metric — points with
    no measurement on either side are skipped (never plotted as a false 0)."""
    section, name = _HEADLINE_METRIC
    points: list[tuple[str, float]] = []
    for tag, d in history:
        value = ((d.get(section) or {}).get(name) or {}).get("head")
        if value is not None:
            points.append((tag, value))
    current = ((deltas.get(section) or {}).get(name) or {}).get("head")
    if current is not None:
        points.append(("current", current))
    return points


def _chart_metric_evolution(
    deltas: dict, history: list[tuple[str, dict]], assets_dir: Path
) -> Path:
    points = _evolution_points(deltas, history)
    fig, ax = plt.subplots(figsize=(9, 5), facecolor=_SURFACE)
    if not points:
        _empty_axes(ax, "no tagged release history yet")
    else:
        labels = [p[0] for p in points]
        values = [p[1] for p in points]
        x = np.arange(len(labels))
        if len(points) == 1:
            ax.scatter(x, values, s=64, color=_BLUE, zorder=3)
        else:
            ax.plot(
                x, values, color=_BLUE, linewidth=2, marker="o", markersize=6,
                markeredgecolor=_SURFACE, markeredgewidth=1.5, zorder=3,
            )
        ax.annotate(
            f"{values[-1]:.3g}", (x[-1], values[-1]), textcoords="offset points",
            xytext=(6, 6), color=_INK, fontsize=9,
        )
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=15, ha="right", fontsize=9, color=_INK)
        ax.set_ylabel(f"{_HEADLINE_METRIC[1]} (head, seconds)", color=_MUTED, fontsize=9)
        ax.grid(axis="y", color=_GRID, linewidth=0.8, alpha=0.9)
        _style_axes(ax)
    _title_axes(ax, "Metric evolution across releases", f"{_HEADLINE_METRIC[1]} · lower is better")
    fig.tight_layout()
    out = assets_dir / "metric_evolution.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def make_charts(deltas: dict, history: list[tuple[str, dict]], assets_dir: Path) -> list[Path]:
    """Write `step_time_before_after.png` (bar) and `metric_evolution.png`
    (line) into `assets_dir`, creating it if needed. Always returns both
    paths, existing on disk — an empty/single-point `history` or a `deltas`
    with nothing measured on both sides renders a labeled placeholder axes
    rather than raising or skipping a file."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    return [
        _chart_step_time_before_after(deltas, assets_dir),
        _chart_metric_evolution(deltas, history, assets_dir),
    ]


# --------------------------------------------------------------------------
# CLI plumbing: change-log discovery, tag history, index regeneration, main
# --------------------------------------------------------------------------
def _derive_date() -> str:
    """Deterministic report date when `--date` is not given: the HEAD
    commit's own committer date (`%cs`, `YYYY-MM-DD`) — never
    `datetime.now()`, which would make the report non-reproducible from the
    same git state."""
    out = _run_git("log", "-1", "--format=%cs", "HEAD")
    return (out or "").strip() or "unknown-date"


def _change_log_entry(title: str) -> dict:
    m = _TITLE_RE.match(title.strip())
    return {"type": m.group("type") if m else "other", "title": title.strip()}


def build_change_log(base_ref: str | None, head_ref: str) -> list[dict]:
    """Conventional-commit-typed subject lines between `base_ref` (exclusive)
    and `head_ref` (inclusive). `base_ref=None` -> just the head commit
    itself (first release, nothing to diff against). Any git failure (e.g.
    `head_ref` is a bare tree, not a commit) degrades to `[]`, never a
    crash — mirrors `_run_git`'s convention."""
    rng = f"{base_ref}..{head_ref}" if base_ref else head_ref
    out = _run_git("log", "--format=%s", rng)
    if not out:
        return []
    return [_change_log_entry(line) for line in out.splitlines() if line.strip()]


def _version_key(tag: str) -> tuple[int, int, int]:
    try:
        parts = [int(x) for x in tag.lstrip("v").split(".")]
        return (parts[0], parts[1], parts[2])
    except (ValueError, IndexError):
        return (0, 0, 0)


def _sorted_version_tags() -> list[str]:
    out = _run_git("tag", "--list", "v*")
    tags = [t.strip() for t in (out or "").splitlines() if t.strip()]
    return sorted(tags, key=_version_key)


def build_history(tags: list[str]) -> list[tuple[str, dict]]:
    """`(tag, deltas)` for every already-tagged release, oldest first — each
    tag's own deltas are computed against the tag immediately before it (the
    same base/head shape `main()` uses for the current release), so the
    evolution chart is one consistent series across the full tag history."""
    history: list[tuple[str, dict]] = []
    prev_tag: str | None = None
    for tag in tags:
        prev_sets = discover_artifact_sets(prev_tag) if prev_tag else {}
        prev_job = latest_job(prev_sets)
        prev_artifacts = prev_sets.get(prev_job, {}) if prev_job else {}

        cur_sets = discover_artifact_sets(tag)
        cur_job = latest_job(cur_sets)
        cur_artifacts = cur_sets.get(cur_job, {}) if cur_job else {}

        d = compute_deltas(prev_artifacts, cur_artifacts, base_job=prev_job, head_job=cur_job)
        history.append((tag, d))
        prev_tag = tag
    return history


def _write_index(out_dir: Path) -> Path:
    """Regenerate `docs/releases/README.md` from every `<out_dir>/v*/
    summary.json` sidecar present on disk (one per release `main()` has
    written) — never hand-maintained, never accumulated in memory across
    runs, so the index always reflects exactly what's on disk."""
    rows = []
    for summary_path in out_dir.glob("v*/summary.json"):
        try:
            rows.append(json.loads(summary_path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    rows.sort(key=lambda r: _version_key(r.get("version", "v0.0.0")))

    lines = [
        "# Release history",
        "",
        "Generated by `scripts/release/make_release_report.py` (via "
        "`.github/workflows/release_tag_report.yaml`); this index is regenerated on "
        "every release — do not hand-edit rows.",
        "",
        "| Version | Date | Headline delta |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {r.get('version', '?')} | {r.get('date', '?')} | {r.get('headline_delta', '?')} |"
        for r in rows
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "README.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--print-next-version",
        action="store_true",
        help="print the computed next SemVer tag to stdout and exit — no other "
        "output (the release Action's version-compute step calls this).",
    )
    ap.add_argument("--title", default=None, help="merge-commit title, for --print-next-version")
    ap.add_argument("--prev", default=None, help="previous tag, for --print-next-version")
    ap.add_argument("--base-ref", default=None, help="default: previous tag reachable from HEAD^")
    ap.add_argument("--head-ref", default="HEAD")
    ap.add_argument("--version", default=None, help="default: computed from the head commit title")
    ap.add_argument(
        "--date",
        default=None,
        help="report date (YYYY-MM-DD); default: HEAD's own committer date, never "
        "datetime.now()",
    )
    ap.add_argument("--out-dir", type=Path, default=Path("docs/releases"))
    ap.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    return ap


def _resolve_base_ref(args: argparse.Namespace) -> str | None:
    """An explicit `--base-ref` (including a blank one) wins outright;
    omitted means "try to auto-detect the previous tag reachable from
    `head_ref^`", which itself degrades to `None` (first release) on any git
    failure."""
    if args.base_ref is not None:
        return args.base_ref or None
    # --match restricts the lookup to SemVer release tags (v1.2.3): without
    # it, any non-SemVer tag reachable from head_ref^ (e.g. a stray "latest"
    # or a third-party tool's tag) would win `--abbrev=0`'s "nearest tag"
    # search and crash next_version()'s "v".lstrip/split(".") parse.
    described = _run_git(
        "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", f"{args.head_ref}^"
    )
    return described.strip() if described and described.strip() else None


def _print_dry_run_plan(
    version: str, base_ref: str | None, args: argparse.Namespace, report_path: Path, assets_dir: Path
) -> None:
    print(f"version: {version}")
    print(f"base-ref: {base_ref or '(none — first release)'}")
    print(f"head-ref: {args.head_ref}")
    print(f"would write: {report_path}")
    print(f"would write: {assets_dir / 'step_time_before_after.png'}")
    print(f"would write: {assets_dir / 'metric_evolution.png'}")
    print(f"would write: {report_path.parent / 'summary.json'}")
    print(f"would regenerate: {args.out_dir / 'README.md'}")


def _discover_side(ref: str | None) -> tuple[str | None, dict]:
    if not ref:
        return None, {}
    sets = discover_artifact_sets(ref)
    job = latest_job(sets)
    return job, (sets.get(job, {}) if job else {})


def _write_redacted_report(report_path: Path, report_md: str, head_artifacts: dict) -> redaction.Mapping:
    """Build the mapping from the HEAD artifacts, redact the report, and
    write it — the first artifact `main()` puts on disk, so the redaction
    mapping exists before anything else does."""
    metrics_for_mapping = {"gcp": head_artifacts.get("gcp"), "offline": head_artifacts.get("offline")}
    mapping = redaction.build_mapping(metrics_for_mapping)
    report_path.write_text(redaction.redact_text(mapping, report_md))
    return mapping


def _write_summary_and_index(
    version_dir: Path, out_dir: Path, version: str, date: str, deltas: dict
) -> None:
    (version_dir / "summary.json").write_text(
        json.dumps(
            {"version": version, "date": date, "headline_delta": _headline_delta(deltas)},
            indent=2,
        )
        + "\n"
    )
    _write_index(out_dir)


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.print_next_version:
        if not args.title:
            print("--print-next-version requires --title", file=sys.stderr)
            return 2
        print(next_version(args.prev, parse_bump(args.title)))
        return 0

    base_ref = _resolve_base_ref(args)
    head_title = (_run_git("log", "-1", "--format=%s", args.head_ref) or "").strip()
    version = args.version or next_version(base_ref, parse_bump(head_title))
    date = args.date or _derive_date()

    version_dir = args.out_dir / version
    assets_dir = version_dir / "assets"
    report_path = version_dir / "report.md"

    if args.dry_run:
        _print_dry_run_plan(version, base_ref, args, report_path, assets_dir)
        return 0

    head_job, head_artifacts = _discover_side(args.head_ref)
    base_job, base_artifacts = _discover_side(base_ref)

    deltas = compute_deltas(base_artifacts, head_artifacts, base_job=base_job, head_job=head_job)
    deltas["date"] = date

    change_log = build_change_log(base_ref, args.head_ref)
    history = build_history(_sorted_version_tags())

    # Write order matches the brief exactly: render -> redact -> write
    # report -> write charts -> leak-scan the COMPLETE version dir (report +
    # charts, everything this generator is about to commit) -> on a hit,
    # exit 3 and remove the whole dir (nothing committable left; the index
    # is only touched after the gate passes, so a failed release never
    # contaminates it). `redaction.leak_scan` is binary-tolerant (decodes
    # with `errors="ignore"` instead of crashing on a PNG's byte signature),
    # so scanning the chart images alongside the report is safe — the gate
    # covers the whole committed artifact set, not just the text file.
    version_dir.mkdir(parents=True, exist_ok=True)
    report_md = render_report(version, change_log, deltas, history)
    mapping = _write_redacted_report(report_path, report_md, head_artifacts)
    make_charts(deltas, history, assets_dir)

    leaks = redaction.leak_scan(version_dir, mapping)
    if leaks:
        print("release report leak scan failed — nothing committed:", file=sys.stderr)
        for fname, token in leaks:
            print(f"  {fname}: {token!r}", file=sys.stderr)
        shutil.rmtree(version_dir, ignore_errors=True)
        return 3

    _write_summary_and_index(version_dir, args.out_dir, version, date, deltas)
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
