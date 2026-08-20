#!/usr/bin/env python
"""Free-text pattern crosscheck: source vs synthetic BigQuery columns.

Generic, table-agnostic companion to ``e2e_gcp_probe.py`` focused purely on
**free-text / string columns**. Given a source table, a synthetic table and a
list of columns, it uses Application Default Credentials (ADC) to mine the
*structural patterns* of the real column and measure how faithfully the
synthetic generator reproduced them — so the gaps can be fed to Claude Code as
concrete synthetic-generation improvements.

For every requested column it computes, on both tables:

  * Cardinality / sparsity — row count, null fraction, empty (trimmed) fraction,
    distinct count, distinct ratio.
  * Length distribution — min / mean / max plus p05/p50/p95 from a sample.
  * Character-class profile — fraction of sampled values that contain
    digits / uppercase / lowercase / whitespace / punctuation.
  * **Shape signatures** — each value is masked to a character-class skeleton
    (digit→``9``, upper→``A``, lower→``a``, space→``␣``, punctuation kept
    literally), in two views: an exact per-character ``shape`` (captures fixed
    formats like ``9999-99-99``) and a run-collapsed ``shape_collapsed``
    (captures variable-length families like ``9+-9+``). Top-K of each is kept.
  * Top literal values (APPROX_TOP_COUNT) — for memorization / copy hints.

Then it *diffs* source vs synthetic and emits, per column:

  * ``missing_shapes`` — shapes with meaningful mass in the source but (near)
    absent in the synthetic → formats the generator failed to learn.
  * ``spurious_shapes`` — shapes the synthetic invents that don't exist in the
    source → formats the generator hallucinated.
  * ``shape_recall`` — source shape mass (weighted) reproduced by synthetic.
  * ``shape_precision`` — synthetic shape mass that exists in the source.
  * length / charclass / sparsity deltas.
  * literal ``copy_fraction`` — fraction of the synthetic sample whose exact
    value also appears in the source sample (crude memorization signal).
  * a ranked ``findings`` list (severity + message) — the improvement backlog.

Nothing is hard-coded to a table or environment: pass ``--source-fqn``,
``--synthetic-fqn`` and ``--columns`` and it works anywhere.

Usage:
    python scripts/e2e/freetext_crosscheck.py \
        --source-fqn    project.dataset.TABLE \
        --synthetic-fqn project.synthetic_data.TABLE \
        --columns COL_A,COL_B,COL_C \
        --sample-size 20000 --top-k 15 \
        --out-json output/freetext_crosscheck/metrics_YYYY_MM_DD_HH_mm.json \
        --out-md   output/freetext_crosscheck/report_YYYY_MM_DD_HH_mm.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from typing import Any

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_FQN_PARTS = 3

# Divergence thresholds — tuned to surface real generation gaps, not noise.
_SHAPE_MASS_MIN = 0.02        # a shape must hold >=2% of source mass to be "missing"
_SHAPE_RECALL_WARN = 0.90     # below this, the generator is missing real formats
_SHAPE_PRECISION_WARN = 0.90  # below this, the generator invents formats
_FRACTION_DELTA_WARN = 0.10   # null/empty/charclass delta that matters
_LEN_RATIO_WARN = 0.20        # relative mean-length divergence
_COPY_FRACTION_WARN = 0.30    # literal copy fraction that reads as memorization
# A synthetic value seen >= this many times in the source SAMPLE is enum
# mass, not a copy (mirrors the engines' _FREE_TEXT_HEAD_MIN_COUNT
# k-anonymity floor; 2026-08-11 B_TABLE R1 COL_015 sample-vs-probe split).
_ENUM_REUSE_MIN_COUNT = 10
# Above this source distinct-ratio a column counts as high-cardinality for
# the memorization / diversity findings (mirrors thresholds.yml
# freetext.distinct_floor `applies_above_source_distinct_ratio`).
_HIGH_CARDINALITY_RATIO = 0.5


# --------------------------------------------------------------------------
# auth / preflight
# --------------------------------------------------------------------------
def preflight_adc(project: str):
    try:
        import google.auth
        import google.auth.transport.requests as gtr
    except ImportError as e:  # pragma: no cover - env-dependent
        _die(f"missing GCP client libs ({e}). Install: pip install google-cloud-bigquery")
    try:
        creds, _ = google.auth.default(scopes=_SCOPES)
        creds.refresh(gtr.Request())
    except Exception as e:
        _die(
            f"Application Default Credentials not usable ({type(e).__name__}: {e}).\n"
            "Run:\n  gcloud auth application-default login\n"
            f"  gcloud auth application-default set-quota-project {project}"
        )
    return _caller_identity(creds)


def _caller_identity(creds) -> str:
    return getattr(creds, "service_account_email", None) or getattr(creds, "_account", None) or "unknown"


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


# --------------------------------------------------------------------------
# BigQuery helpers
# --------------------------------------------------------------------------
def _split_fqn(fqn: str) -> tuple[str, str, str]:
    parts = fqn.split(".")
    if len(parts) != _FQN_PARTS:
        _die(f"expected project.dataset.table, got {fqn!r}")
    return parts[0], parts[1], parts[2]


def _bq_client(project: str):
    from google.cloud import bigquery

    return bigquery.Client(project=project)


def _existing_columns(client, fqn: str) -> dict[str, str]:
    proj, ds, tbl = _split_fqn(fqn)
    from google.cloud import bigquery

    sql = f"""
        SELECT column_name, data_type
        FROM `{proj}.{ds}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = @tbl
    """
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("tbl", "STRING", tbl)]
        ),
    )
    return {r.column_name: r.data_type for r in job.result()}


def _row_count(client, fqn: str) -> int:
    return next(iter(client.query(f"SELECT COUNT(*) AS n FROM `{fqn}`").result())).n


def _aggregate(client, fqn: str, columns: list[str], top_k: int) -> dict[str, Any]:
    """One warehouse pass: per-column count/null/empty/distinct/length + top values."""
    selects = []
    for col in columns:
        c = f"`{col}`"
        s = f"CAST({c} AS STRING)"
        selects.append(
            f"""
            COUNT(*) AS `{col}__n`,
            COUNTIF({c} IS NULL) AS `{col}__null_n`,
            COUNTIF({s} = '' OR TRIM({s}) = '') AS `{col}__empty_n`,
            COUNT(DISTINCT {s}) AS `{col}__distinct_n`,
            MIN(LENGTH({s})) AS `{col}__len_min`,
            MAX(LENGTH({s})) AS `{col}__len_max`,
            AVG(LENGTH({s})) AS `{col}__len_avg`,
            TO_JSON_STRING(APPROX_TOP_COUNT({s}, {top_k})) AS `{col}__top`
            """
        )
    sql = "SELECT " + ",".join(selects) + f" FROM `{fqn}`"
    r = next(iter(client.query(sql).result()))
    out: dict[str, Any] = {}
    for col in columns:
        top = json.loads(getattr(r, f"{col}__top") or "[]")
        out[col] = {
            "n": getattr(r, f"{col}__n"),
            "null_n": getattr(r, f"{col}__null_n"),
            "empty_n": getattr(r, f"{col}__empty_n"),
            "distinct_n": getattr(r, f"{col}__distinct_n"),
            "len_min": getattr(r, f"{col}__len_min"),
            "len_max": getattr(r, f"{col}__len_max"),
            "len_avg": getattr(r, f"{col}__len_avg"),
            "top_values": [{"value": t.get("value"), "count": t.get("count")} for t in top],
        }
    return out


def _sample_sql(fqn: str, columns: list[str], nonempty_col: str | None = None) -> str:
    """Sample query; ``nonempty_col`` adds that column's non-empty predicate.

    `RAND() < p LIMIT lim` short-circuits on storage order, so a
    mostly-empty column can sample ALL-empty and its source shapes vanish
    from the report (2026-08-09 B_TABLE R1, COL_037: shape recall 0.00
    with `missing_shapes: []`). The targeted variant samples the column's
    substantive rows directly.
    """
    cols_sql = ",".join(f"CAST(`{c}` AS STRING) AS `{c}`" for c in columns)
    where = "WHERE RAND() < @p"
    if nonempty_col is not None:
        c = f"CAST(`{nonempty_col}` AS STRING)"
        where = (
            f"WHERE `{nonempty_col}` IS NOT NULL "
            f"AND TRIM({c}) != '' AND RAND() < @p"
        )
    return f"SELECT {cols_sql} FROM `{fqn}` {where} LIMIT @lim"


# Below this many sampled non-empty values the shape histogram is noise —
# re-sample the column's substantive rows directly (when the aggregates
# prove any exist).
_NONEMPTY_SAMPLE_FLOOR = 50


def _needs_nonempty_topup(sampled_nonempty: int, agg: dict[str, Any]) -> bool:
    substantive = (agg.get("n") or 0) - (agg.get("null_n") or 0) - (
        agg.get("empty_n") or 0
    )
    return sampled_nonempty < _NONEMPTY_SAMPLE_FLOOR and substantive > 0


def _sample(
    client,
    fqn: str,
    columns: list[str],
    sample_size: int,
    rows: int,
    nonempty_col: str | None = None,
) -> list[dict[str, Any]]:
    """Random-ish sample of the columns for in-Python shape mining."""
    from google.cloud import bigquery

    p = min(1.0, (sample_size * 3.0) / max(rows, 1))
    sql = _sample_sql(fqn, columns, nonempty_col=nonempty_col)
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("p", "FLOAT64", p),
                bigquery.ScalarQueryParameter("lim", "INT64", sample_size),
            ]
        ),
    )
    return [dict(r) for r in job.result()]


# --------------------------------------------------------------------------
# shape mining (pure Python)
# --------------------------------------------------------------------------
def shape_of(value: str) -> str:
    out = []
    for ch in value:
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha() and ch.isupper():
            out.append("A")
        elif ch.isalpha():
            out.append("a")
        elif ch.isspace():
            out.append("␣")
        else:
            out.append(ch)
    return "".join(out)


def collapse(mask: str) -> str:
    out, i, n = [], 0, len(mask)
    while i < n:
        ch = mask[i]
        j = i
        while j < n and mask[j] == ch:
            j += 1
        run = j - i
        if ch in "9Aa␣" and run > 1:
            out.append(f"{ch}+")
        else:
            out.append(mask[i:j])
        i = j
    return "".join(out)


def _charclasses(value: str) -> dict[str, bool]:
    return {
        "digit": any(c.isdigit() for c in value),
        "upper": any(c.isalpha() and c.isupper() for c in value),
        "lower": any(c.isalpha() and c.islower() for c in value),
        "space": any(c.isspace() for c in value),
        "punct": any(not c.isalnum() and not c.isspace() for c in value),
    }


def _profile_sample(values: list[str], top_k: int) -> dict[str, Any]:
    """Shape histograms, length percentiles, charclass profile from a sample."""
    vals = [v for v in values if v is not None and v != ""]
    n = len(vals)
    if n == 0:
        return {"sample_n": 0}
    shapes = Counter(shape_of(v) for v in vals)
    shapes_c = Counter(collapse(shape_of(v)) for v in vals)
    lengths = sorted(len(v) for v in vals)
    cc = Counter()
    for v in vals:
        for k, present in _charclasses(v).items():
            if present:
                cc[k] += 1

    def pct(p: float) -> int:
        return lengths[min(len(lengths) - 1, int(p * len(lengths)))]

    return {
        "sample_n": n,
        "shapes": {k: v for k, v in shapes.most_common(top_k)},
        "shapes_mass": {k: v / n for k, v in shapes.most_common(top_k)},
        "shapes_collapsed": {k: v for k, v in shapes_c.most_common(top_k)},
        "shapes_collapsed_mass": {k: v / n for k, v in shapes_c.most_common(top_k)},
        "_shape_counts_full": dict(shapes),  # internal, for diff
        "len_p05": pct(0.05),
        "len_p50": pct(0.50),
        "len_p95": pct(0.95),
        "charclass_fraction": {k: cc.get(k, 0) / n for k in ("digit", "upper", "lower", "space", "punct")},
        "sample_values": vals[:5],
    }


# --------------------------------------------------------------------------
# diff / findings
# --------------------------------------------------------------------------
def _fraction(num, den):
    if not den:
        return None
    return num / den


def _copy_fractions(src_prof, syn_prof) -> tuple[float | None, float | None]:
    """(carved copy_fraction, raw copy_fraction) from the sampled values.

    Enum-reuse carve-out (2026-08-11 B_TABLE R1, COL_015: this sample
    metric said 0.27 while the full-table probe's `copy_ratio_substantive`
    said 0.000 for the same column): a synthetic value observed >=
    `_ENUM_REUSE_MIN_COUNT` times in the source sample is a shared enum
    member — re-emitting it is categorical fidelity, not memorization (the
    WS8 §5b k-anonymity floor). Enum values leave numerator AND
    denominator; the raw number stays visible as `copy_fraction_raw`.
    """
    src_all_vals = src_prof.get("_all_values", [])
    src_sample_set = set(src_all_vals)
    src_value_counts = Counter(src_all_vals)
    syn_sample_vals = syn_prof.get("_all_values", [])
    if not src_sample_set or not syn_sample_vals:
        return None, None
    copied = sum(1 for v in syn_sample_vals if v in src_sample_set)
    raw = copied / len(syn_sample_vals)
    substantive = [
        v
        for v in syn_sample_vals
        if src_value_counts.get(v, 0) < _ENUM_REUSE_MIN_COUNT
    ]
    if not substantive:
        return 0.0, raw
    copied_sub = sum(1 for v in substantive if v in src_sample_set)
    return copied_sub / len(substantive), raw


def _diff_column(col: str, src_agg, syn_agg, src_prof, syn_prof) -> dict[str, Any]:
    findings: list[dict[str, str]] = []

    src_null = _fraction(src_agg["null_n"], src_agg["n"])
    syn_null = _fraction(syn_agg["null_n"], syn_agg["n"])
    src_empty = _fraction(src_agg["empty_n"], src_agg["n"])
    syn_empty = _fraction(syn_agg["empty_n"], syn_agg["n"])
    src_distinct_ratio = _fraction(src_agg["distinct_n"], src_agg["n"])
    syn_distinct_ratio = _fraction(syn_agg["distinct_n"], syn_agg["n"])

    # --- shape recall / precision (weighted by source/synthetic mass) ---
    src_counts = src_prof.get("_shape_counts_full", {})
    syn_counts = syn_prof.get("_shape_counts_full", {})
    src_total = sum(src_counts.values()) or 1
    syn_total = sum(syn_counts.values()) or 1
    src_mass = {k: v / src_total for k, v in src_counts.items()}
    syn_mass = {k: v / syn_total for k, v in syn_counts.items()}

    shape_recall = sum(m for s, m in src_mass.items() if s in syn_counts)
    shape_precision = sum(m for s, m in syn_mass.items() if s in src_counts)

    missing = sorted(
        ((s, m) for s, m in src_mass.items() if m >= _SHAPE_MASS_MIN and s not in syn_counts),
        key=lambda kv: -kv[1],
    )[:10]
    spurious = sorted(
        ((s, m) for s, m in syn_mass.items() if m >= _SHAPE_MASS_MIN and s not in src_counts),
        key=lambda kv: -kv[1],
    )[:10]

    copy_fraction, copy_fraction_raw = _copy_fractions(src_prof, syn_prof)

    # --- charclass deltas ---
    src_cc = src_prof.get("charclass_fraction", {})
    syn_cc = syn_prof.get("charclass_fraction", {})
    cc_delta = {k: (syn_cc.get(k, 0) - src_cc.get(k, 0)) for k in ("digit", "upper", "lower", "space", "punct")}

    # --- length delta ---
    src_len_avg = src_agg["len_avg"] or 0
    syn_len_avg = syn_agg["len_avg"] or 0
    len_ratio = abs(syn_len_avg - src_len_avg) / src_len_avg if src_len_avg else None

    # ---------------- findings ----------------
    def add(sev, msg):
        findings.append({"severity": sev, "message": msg})

    if shape_recall < _SHAPE_RECALL_WARN:
        add("HIGH", f"shape recall {shape_recall:.2f}: synthetic misses source formats "
                    f"(top missing: {', '.join(s for s, _ in missing[:3]) or 'n/a'})")
    if shape_precision < _SHAPE_PRECISION_WARN:
        add("HIGH", f"shape precision {shape_precision:.2f}: synthetic invents formats "
                    f"absent from source (top spurious: {', '.join(s for s, _ in spurious[:3]) or 'n/a'})")
    if src_null is not None and syn_null is not None and abs(syn_null - src_null) > _FRACTION_DELTA_WARN:
        add("MEDIUM", f"null fraction {syn_null:.2f} vs source {src_null:.2f} "
                      f"(delta {syn_null - src_null:+.2f})")
    if src_empty is not None and syn_empty is not None and abs(syn_empty - src_empty) > _FRACTION_DELTA_WARN:
        add("MEDIUM", f"empty fraction {syn_empty:.2f} vs source {src_empty:.2f} "
                      f"(delta {syn_empty - src_empty:+.2f})")
    if len_ratio is not None and len_ratio > _LEN_RATIO_WARN:
        add("MEDIUM", f"mean length {syn_len_avg:.1f} vs source {src_len_avg:.1f} "
                      f"(rel delta {len_ratio:.2f})")
    for k, d in cc_delta.items():
        if abs(d) > _FRACTION_DELTA_WARN:
            add("LOW", f"charclass '{k}' presence delta {d:+.2f} "
                       f"(source {src_cc.get(k, 0):.2f} → synthetic {syn_cc.get(k, 0):.2f})")
    if copy_fraction is not None and copy_fraction > _COPY_FRACTION_WARN and (src_distinct_ratio or 0) > _HIGH_CARDINALITY_RATIO:
        add("HIGH", f"literal copy fraction {copy_fraction:.2f} on a high-cardinality column "
                    f"(source distinct ratio {src_distinct_ratio:.2f}) — possible memorization")
    if (src_distinct_ratio or 0) > _HIGH_CARDINALITY_RATIO and (syn_distinct_ratio or 0) < 0.5 * (src_distinct_ratio or 0):
        add("MEDIUM", f"distinct ratio collapsed {syn_distinct_ratio:.3f} vs source "
                      f"{src_distinct_ratio:.3f} — synthetic under-diversifies")

    sev_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    findings.sort(key=lambda f: sev_rank[f["severity"]])

    return {
        "source": {
            "rows": src_agg["n"], "null_fraction": src_null, "empty_fraction": src_empty,
            "distinct": src_agg["distinct_n"], "distinct_ratio": src_distinct_ratio,
            "len_min": src_agg["len_min"], "len_avg": src_len_avg, "len_max": src_agg["len_max"],
            "len_p05": src_prof.get("len_p05"), "len_p50": src_prof.get("len_p50"),
            "len_p95": src_prof.get("len_p95"),
            "charclass_fraction": src_cc,
            "top_shapes": src_prof.get("shapes_mass", {}),
            "top_shapes_collapsed": src_prof.get("shapes_collapsed_mass", {}),
            "top_values": src_agg["top_values"][:8],
            "sample_values": src_prof.get("sample_values", []),
        },
        "synthetic": {
            "rows": syn_agg["n"], "null_fraction": syn_null, "empty_fraction": syn_empty,
            "distinct": syn_agg["distinct_n"], "distinct_ratio": syn_distinct_ratio,
            "len_min": syn_agg["len_min"], "len_avg": syn_len_avg, "len_max": syn_agg["len_max"],
            "len_p05": syn_prof.get("len_p05"), "len_p50": syn_prof.get("len_p50"),
            "len_p95": syn_prof.get("len_p95"),
            "charclass_fraction": syn_cc,
            "top_shapes": syn_prof.get("shapes_mass", {}),
            "top_shapes_collapsed": syn_prof.get("shapes_collapsed_mass", {}),
            "top_values": syn_agg["top_values"][:8],
            "sample_values": syn_prof.get("sample_values", []),
        },
        "diff": {
            "shape_recall": round(shape_recall, 4),
            "shape_precision": round(shape_precision, 4),
            "missing_shapes": [{"shape": s, "source_mass": round(m, 4)} for s, m in missing],
            "spurious_shapes": [{"shape": s, "synthetic_mass": round(m, 4)} for s, m in spurious],
            "charclass_delta": {k: round(v, 4) for k, v in cc_delta.items()},
            "mean_length_rel_delta": round(len_ratio, 4) if len_ratio is not None else None,
            "null_fraction_delta": round(syn_null - src_null, 4) if (src_null is not None and syn_null is not None) else None,
            "empty_fraction_delta": round(syn_empty - src_empty, 4) if (src_empty is not None and syn_empty is not None) else None,
            "copy_fraction": round(copy_fraction, 4) if copy_fraction is not None else None,
            "copy_fraction_raw": round(copy_fraction_raw, 4) if copy_fraction_raw is not None else None,
        },
        "findings": findings,
    }


def _column_score(entry: dict[str, Any]) -> float:
    """Higher = more divergent. Used to rank the backlog.

    Each term is bounded so no single artifact dominates: the mean-length
    ratio in particular blows up when the source is mostly-empty (mean length
    ~0), so it is capped at 1.0. The empty/null fraction deltas — the dominant
    systemic signal when a generator never reproduces mostly-empty columns —
    are weighted heavily.
    """
    d = entry["diff"]
    score = 0.0
    score += (1 - d["shape_recall"]) * 2
    score += (1 - d["shape_precision"]) * 2
    score += sum(abs(v) for v in d["charclass_delta"].values())
    score += min(d["mean_length_rel_delta"] or 0, 1.0)
    score += abs(d["empty_fraction_delta"] or 0) * 2
    score += abs(d["null_fraction_delta"] or 0) * 2
    score += (d["copy_fraction"] or 0)
    sev_weight = {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.2}
    score += sum(sev_weight[f["severity"]] for f in entry["findings"])
    return round(score, 4)


# --------------------------------------------------------------------------
# markdown report
# --------------------------------------------------------------------------
def _fmt_pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def _fmt_num(x):
    return "—" if x is None else (f"{x:.1f}" if isinstance(x, float) else str(x))


def _shape_table(mass: dict[str, float], limit: int = 6) -> str:
    if not mass:
        return "_none_"
    items = list(mass.items())[:limit]
    return "<br>".join(f"`{s}` ({m * 100:.1f}%)" for s, m in items)


def render_markdown(meta: dict[str, Any], columns: dict[str, Any]) -> str:  # noqa: PLR0915 — linear report assembly reads clearer unsplit
    ranked = sorted(columns.items(), key=lambda kv: -kv[1]["score"])
    lines: list[str] = []
    a = lines.append

    a(f"# Free-text pattern crosscheck — `{meta['table']}`")
    a("")
    a(f"> Source vs synthetic structural-pattern comparison for {len(columns)} free-text "
      "column(s), to surface concrete synthetic-generation improvements.")
    a("")
    a("| | |")
    a("|---|---|")
    a(f"| Source table | `{meta['source_fqn']}` ({meta['source_rows']:,} rows) |")
    a(f"| Synthetic table | `{meta['synthetic_fqn']}` ({meta['synthetic_rows']:,} rows) |")
    a(f"| Columns | {', '.join(f'`{c}`' for c in meta['columns'])} |")
    a(f"| Sample size / column | {meta['sample_size']:,} |")
    a(f"| Generated | {meta['generated_at']} |")
    a(f"| Caller | `{meta['caller']}` |")
    a("")

    # ---- systemic root-cause detection ----
    empty_gap = [
        c for c, e in columns.items()
        if (e["diff"].get("empty_fraction_delta") or 0) <= -_FRACTION_DELTA_WARN
    ]
    if len(empty_gap) >= max(2, len(columns) // 2):
        a("> **Dominant root cause — empty/null parity not modeled.** "
          f"{len(empty_gap)} of {len(columns)} columns are substantially empty in the "
          "source but ~0% empty in the synthetic table. The generator populates a value "
          "for every row instead of reproducing the source's mostly-empty distribution — "
          "this single gap also inflates mean length and distorts shape mass on those "
          "columns. Fix the empty/null-parity derivation once (codegen + engine sentinel "
          "handling) before chasing the per-column shape findings below.")
        a("")

    # ---- executive summary ----
    a("## Executive summary — improvement backlog (worst first)")
    a("")
    a("| Rank | Column | Score | Shape recall | Shape precision | Copy frac | Top issue |")
    a("|---|---|---|---|---|---|---|")
    for i, (col, e) in enumerate(ranked, 1):
        d = e["diff"]
        top = e["findings"][0]["message"] if e["findings"] else "—"
        a(f"| {i} | `{col}` | {e['score']} | {d['shape_recall']:.2f} | "
          f"{d['shape_precision']:.2f} | {_fmt_num(d['copy_fraction'])} | {top} |")
    a("")
    a("**Legend** — *shape recall*: source formats reproduced by synthetic (1.0 = all). "
      "*shape precision*: synthetic formats that exist in source (1.0 = no hallucinated formats). "
      "*copy frac*: sampled synthetic values found verbatim in the source sample.")
    a("")

    # ---- per-column detail ----
    a("## Per-column detail")
    a("")
    for col, e in ranked:
        s, y, d = e["source"], e["synthetic"], e["diff"]
        a(f"### `{col}`  ·  score {e['score']}")
        a("")
        a("| Metric | Source | Synthetic |")
        a("|---|---|---|")
        a(f"| Null fraction | {_fmt_pct(s['null_fraction'])} | {_fmt_pct(y['null_fraction'])} |")
        a(f"| Empty fraction | {_fmt_pct(s['empty_fraction'])} | {_fmt_pct(y['empty_fraction'])} |")
        a(f"| Distinct | {s['distinct']:,} ({_fmt_pct(s['distinct_ratio'])}) | "
          f"{y['distinct']:,} ({_fmt_pct(y['distinct_ratio'])}) |")
        a(f"| Length min/p50/max | {s['len_min']}/{_fmt_num(s['len_p50'])}/{s['len_max']} | "
          f"{y['len_min']}/{_fmt_num(y['len_p50'])}/{y['len_max']} |")
        a(f"| Mean length | {_fmt_num(s['len_avg'])} | {_fmt_num(y['len_avg'])} |")
        cc_s, cc_y = s["charclass_fraction"], y["charclass_fraction"]
        a(f"| Charclass (d/U/l/␣/p) | "
          f"{'/'.join(_fmt_pct(cc_s.get(k)) for k in ('digit','upper','lower','space','punct'))} | "
          f"{'/'.join(_fmt_pct(cc_y.get(k)) for k in ('digit','upper','lower','space','punct'))} |")
        a(f"| Top shapes (exact) | {_shape_table(s['top_shapes'])} | {_shape_table(y['top_shapes'])} |")
        a("")
        if d["missing_shapes"]:
            a("**Missing shapes** (in source, absent from synthetic): "
              + ", ".join(f"`{m['shape']}` ({m['source_mass'] * 100:.1f}%)" for m in d["missing_shapes"]))
            a("")
        if d["spurious_shapes"]:
            a("**Spurious shapes** (invented by synthetic, absent from source): "
              + ", ".join(f"`{m['shape']}` ({m['synthetic_mass'] * 100:.1f}%)" for m in d["spurious_shapes"]))
            a("")
        a(f"**Source examples:** {', '.join(repr(v) for v in s['sample_values']) or '—'}")
        a("")
        a(f"**Synthetic examples:** {', '.join(repr(v) for v in y['sample_values']) or '—'}")
        a("")
        if e["findings"]:
            a("**Findings:**")
            for f in e["findings"]:
                a(f"- **[{f['severity']}]** {f['message']}")
        else:
            a("_No material divergence detected._")
        a("")

    # ---- how to act ----
    a("## Feeding this to Claude Code")
    a("")
    a("Each finding is a candidate improvement for the free-text generation path "
      "(`sdfb_core/engines/*/freetext.py`, profile shape/length modeling, null/empty "
      "sentinel parity). Prioritize HIGH findings: *shape recall* gaps mean the "
      "generator never learned a real format; *shape precision* gaps mean it emits "
      "impossible formats; a high *copy fraction* on a high-cardinality column is a "
      "memorization / privacy concern. Attach this report plus the columns above and "
      "ask Claude to trace each finding to the profiling/sampling code and propose a fix.")
    a("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def run(args) -> dict[str, Any]:
    project = args.project or _split_fqn(args.source_fqn)[0]
    preflight_adc(project)
    client = _bq_client(project)
    try:
        caller = next(iter(client.query("SELECT SESSION_USER() AS u").result())).u or "unknown"
    except Exception:
        caller = "unknown"

    requested = [c.strip() for c in args.columns.split(",") if c.strip()]
    src_schema = _existing_columns(client, args.source_fqn)
    syn_schema = _existing_columns(client, args.synthetic_fqn)
    columns = [c for c in requested if c in src_schema and c in syn_schema]
    missing_cols = [c for c in requested if c not in columns]
    if missing_cols:
        print(f"WARNING: skipping columns absent from one/both tables: {missing_cols}", file=sys.stderr)
    if not columns:
        _die("no requested columns exist in both tables")

    src_rows = _row_count(client, args.source_fqn)
    syn_rows = _row_count(client, args.synthetic_fqn)

    print(f"[1/4] aggregates: source ({src_rows:,} rows)…", file=sys.stderr)
    src_agg = _aggregate(client, args.source_fqn, columns, args.top_k)
    print(f"[2/4] aggregates: synthetic ({syn_rows:,} rows)…", file=sys.stderr)
    syn_agg = _aggregate(client, args.synthetic_fqn, columns, args.top_k)
    print(f"[3/4] sampling {args.sample_size:,} rows/table…", file=sys.stderr)
    src_sample = _sample(client, args.source_fqn, columns, args.sample_size, src_rows)
    syn_sample = _sample(client, args.synthetic_fqn, columns, args.sample_size, syn_rows)

    print("[4/4] diffing patterns…", file=sys.stderr)
    columns_out: dict[str, Any] = {}
    for col in columns:
        src_vals = [r.get(col) for r in src_sample if r.get(col) not in (None, "")]
        syn_vals = [r.get(col) for r in syn_sample if r.get(col) not in (None, "")]
        # Mostly-empty columns can sample all-empty under the LIMIT
        # short-circuit — re-sample their substantive rows directly.
        if _needs_nonempty_topup(len(src_vals), src_agg[col]):
            a = src_agg[col]
            substantive = (a["n"] or 0) - (a["null_n"] or 0) - (a["empty_n"] or 0)
            print(f"[topup] {col}: source sampled {len(src_vals)} non-empty; "
                  f"re-sampling {substantive} substantive rows…", file=sys.stderr)
            rows_t = _sample(client, args.source_fqn, [col],
                             min(args.sample_size, 5000), substantive,
                             nonempty_col=col)
            src_vals = [r.get(col) for r in rows_t if r.get(col) not in (None, "")]
        if _needs_nonempty_topup(len(syn_vals), syn_agg[col]):
            a = syn_agg[col]
            substantive = (a["n"] or 0) - (a["null_n"] or 0) - (a["empty_n"] or 0)
            print(f"[topup] {col}: synthetic sampled {len(syn_vals)} non-empty; "
                  f"re-sampling {substantive} substantive rows…", file=sys.stderr)
            rows_t = _sample(client, args.synthetic_fqn, [col],
                             min(args.sample_size, 5000), substantive,
                             nonempty_col=col)
            syn_vals = [r.get(col) for r in rows_t if r.get(col) not in (None, "")]
        src_prof = _profile_sample(src_vals, args.top_k)
        syn_prof = _profile_sample(syn_vals, args.top_k)
        src_prof["_all_values"] = src_vals
        syn_prof["_all_values"] = syn_vals
        entry = _diff_column(col, src_agg[col], syn_agg[col], src_prof, syn_prof)
        entry["score"] = _column_score(entry)
        columns_out[col] = entry

    meta = {
        "table": _split_fqn(args.source_fqn)[2],
        "source_fqn": args.source_fqn,
        "synthetic_fqn": args.synthetic_fqn,
        "source_rows": src_rows,
        "synthetic_rows": syn_rows,
        "columns": columns,
        "skipped_columns": missing_cols,
        "sample_size": args.sample_size,
        "top_k": args.top_k,
        "caller": caller,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
    return {"meta": meta, "columns": columns_out}




def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-fqn", required=True)
    p.add_argument("--synthetic-fqn", required=True)
    p.add_argument("--columns", required=True, help="comma-separated free-text column names")
    p.add_argument("--project", default=None, help="defaults to the source project")
    p.add_argument("--sample-size", type=int, default=20000)
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument("--out-json", default=None)
    p.add_argument("--out-md", default=None)
    args = p.parse_args(argv)

    result = run(args)

    import os

    if args.out_json:
        os.makedirs(os.path.dirname(args.out_json) or ".", exist_ok=True)
        with open(args.out_json, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"wrote {args.out_json}")
    if args.out_md:
        os.makedirs(os.path.dirname(args.out_md) or ".", exist_ok=True)
        with open(args.out_md, "w") as f:
            f.write(render_markdown(result["meta"], result["columns"]))
        print(f"wrote {args.out_md}")

    # one-line stdout summary
    ranked = sorted(result["columns"].items(), key=lambda kv: -kv[1]["score"])
    worst = ranked[0] if ranked else None
    if worst:
        print(f"most divergent: {worst[0]} (score {worst[1]['score']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())