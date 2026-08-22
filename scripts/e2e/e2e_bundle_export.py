#!/usr/bin/env python
"""Bundle the E2E validation artifacts into a shareable, de-identified export.

Produces two sibling folders under ``<out-root>/<job_id>/`` (the primary
Dataflow job id of the deployment; falls back to the report timestamp when no
job id is available):

  * ``real/`` — verbatim copies of every metrics JSON + markdown doc + the
    report, plus a ``mapping.json`` decode key (so the internal team can read
    the real names). Sample CSVs are NOT copied — the parent-level CSV next
    to the bundle is the single copy; ``--csv`` only feeds the redaction
    mapping (header columns + cell values).
  * ``oss/``  — the SAME artifacts with every environment-specific and
    data-specific token deterministically replaced by a generic placeholder,
    safe to hand to the open-source team. A run whose ``oss/`` output still
    contained a real identifier would be a leak, so redaction covers three
    token classes:

      1. IDENTIFIERS — project / dataset / table / bucket / caller email /
         reference digests / file paths. Dataflow job ids and job names are
         deliberately KEPT verbatim (they name the bundle folder and carry no
         environment secrets).
      2. COLUMN NAMES — every field name becomes ``COL_NNN`` (primary-key and
         identity columns keep a role prefix: ``PK_COL`` / ``ID_COL``).
      3. DATA VALUES — concrete sampled values (top-value / run-length
         exemplars + every non-numeric CSV cell) become ``VAL_NNNN``.

Nothing is hard-coded to a table or environment: the mapping is derived
entirely from the input artifacts, so this works for any table and any future
integration test.

Usage:
    python scripts/e2e/e2e_bundle_export.py \
        --metrics gcp=integration_test/<JOB_ID>/e2e_gcp_metrics.json \
        --metrics offline=integration_test/<JOB_ID>/e2e_validation_metrics.json \
        --doc stats_diff=integration_test/<JOB_ID>/stats_diff.md \
        --doc freetext_crosscheck_report=integration_test/<JOB_ID>/freetext_crosscheck_report.md \
        --csv b1_rag=integration_test/<JOB_ID>/b1_rag_sample.csv \
        --report output/end_to_end_validation_report_2026_07_07_16_26.md \
        --out-root integration_test \
        --prune-inputs
        # --job-id <JOB_ID>             (default: first Dataflow job id in the
        #                                gcp metrics, else the report timestamp)
        # --no-redact-values            (keep real dev data values in oss/;
        #                                metadata is ALWAYS hidden either way)
        # --prune-inputs                (after a CLEAN leak scan, delete the
        #                                metrics/doc input files that live
        #                                directly in the bundle folder — real/
        #                                keeps the single canonical copy)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redaction  # sibling import; path must be set up first

# Thin aliases so existing/vendored callers (incl. the untouched unit test
# that loads this module via importlib) keep working unchanged. The real
# implementations now live in redaction.py.
Mapping = redaction.Mapping
build_mapping = redaction.build_mapping
_split_fqn = redaction._split_fqn
_collect_identifiers = redaction._collect_identifiers
_collect_columns = redaction._collect_columns
_collect_values = redaction._collect_values
_register_csv = redaction.register_csv
_redact_csv = redaction.redact_csv
_leak_scan = redaction.leak_scan


# --------------------------------------------------------------------------
# bundle writer
# --------------------------------------------------------------------------
def _derive_timestamp(report: Path | None) -> str:
    if report:
        mobj = re.search(r"(\d{4}_\d{2}_\d{2}_\d{2}_\d{2})", report.name)
        if mobj:
            return mobj.group(1)
    return datetime.now(UTC).strftime("%Y_%m_%d_%H_%M")


def _derive_bundle_name(
    job_id: str, metrics: dict[str, Any], report: Path | None
) -> str:
    """Bundle folder = the deployment's primary Dataflow job id."""
    if job_id:
        return job_id
    for job in (metrics.get("gcp") or {}).get("dataflow") or []:
        if job.get("job_id"):
            return str(job["job_id"])
    return _derive_timestamp(report)


def _parse_labeled(items: list[str], flag: str) -> dict[str, Path]:
    """`label=path` pairs (repeatable CLI flag) → {label: Path}."""
    paths: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"{flag} expects label=path, got {item!r}")
        label, path = item.split("=", 1)
        paths[label.strip()] = Path(path.strip())
    return paths


def _prune_inputs(paths: list[Path], base: Path) -> None:
    """Delete input files living directly in the bundle folder — their
    ``real/`` copies are the canonical location after a clean leak scan."""
    base_resolved = base.resolve()
    for p in paths:
        if p.resolve().parent == base_resolved:
            p.unlink()
            print(f"pruned duplicate input: {p}")


def _resolve_history_entry(ap, args, metrics):
    """Registry entry for this run (ADR 0029), or None (legacy naming).

    Assigns/extends the table's aliases in the persistent registry and
    saves it — the registry, not a per-job mapping.json, is the decode
    key when this path is active."""
    if not args.history_mappings:
        return None
    if not args.history_table_fqn:
        ap.error("--history-mappings requires --history-table-fqn")
    import history_mappings as _hm  # sibling import (path set above)

    registry = _hm.HistoryMappings.load(args.history_mappings)
    entry = registry.assign_table(
        args.history_table_fqn, redaction.ordered_columns(metrics)
    )
    registry.save(args.history_mappings)
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--metrics",
        action="append",
        required=True,
        help="label=path.json (repeatable). Use labels 'gcp' and 'offline' so "
        "the mapping can find FQNs/columns; extra labels are copied + redacted.",
    )
    ap.add_argument(
        "--csv",
        action="append",
        default=[],
        help="engine_label=path.csv (repeatable). Registers the sample CSV's "
        "header columns + cell values in the redaction mapping; the CSV "
        "itself is NOT copied into real/ or oss/ (the parent-level copy is "
        "the only one).",
    )
    ap.add_argument(
        "--doc",
        action="append",
        default=[],
        help="label=path.md (repeatable). Markdown artifact copied verbatim "
        "into real/<label>.md and redacted into oss/<label>.md.",
    )
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--out-root", type=Path, default=Path("integration_test"))
    ap.add_argument(
        "--job-id",
        default="",
        help="bundle folder name; default: first Dataflow job id found in the "
        "gcp metrics, else the report timestamp.",
    )
    ap.add_argument(
        "--redact-values",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="redact concrete data-sample values (VAL_NNNN) in the oss/ export. "
        "Metadata (project/dataset/table/columns) is ALWAYS hidden regardless. "
        "Use --no-redact-values to keep real dev data values in oss/.",
    )
    ap.add_argument(
        "--history-mappings",
        type=Path,
        default=None,
        help="path to the persistent alias registry (ADR 0029, "
        "integration_tests/history_mappings_replacement.json). When given, "
        "table/column aliases come from (and append to) the registry — one "
        "stable name per real column across every bundle — and the per-job "
        "mapping.json is NOT written (the registry IS the decode key). "
        "Requires --history-table-fqn.",
    )
    ap.add_argument(
        "--history-table-fqn",
        default="",
        help="the run's real source table FQN — the registry key used with "
        "--history-mappings.",
    )
    ap.add_argument(
        "--prune-inputs",
        action="store_true",
        help="after a CLEAN leak scan, delete the --metrics/--doc input files "
        "that live directly in the bundle folder (their real/ copies become "
        "the single canonical location). CSVs and the report are never "
        "pruned.",
    )
    args = ap.parse_args(argv)

    metrics_paths = _parse_labeled(args.metrics, "--metrics")
    csv_paths = _parse_labeled(args.csv, "--csv")
    doc_paths = _parse_labeled(args.doc, "--doc")

    metrics = {
        label: json.loads(p.read_text()) for label, p in metrics_paths.items()
    }
    csv_texts = {label: p.read_text() for label, p in csv_paths.items()}
    doc_texts = {label: p.read_text() for label, p in doc_paths.items()}
    report_text = args.report.read_text()

    history_entry = _resolve_history_entry(ap, args, metrics)

    mapping = build_mapping(
        metrics,
        redact_values=args.redact_values,
        preset_columns=history_entry["columns"] if history_entry else None,
        preset_table_alias=history_entry["alias"] if history_entry else None,
    )
    for text in csv_texts.values():
        _register_csv(mapping, text, redact_values=args.redact_values)

    base = args.out_root / _derive_bundle_name(args.job_id, metrics, args.report)
    real_dir, oss_dir = base / "real", base / "oss"
    real_dir.mkdir(parents=True, exist_ok=True)
    oss_dir.mkdir(parents=True, exist_ok=True)

    # real/ — verbatim + decode key. Sample CSVs are deliberately NOT copied:
    # the parent-level CSV next to the bundle stays the single copy.
    for label, p in metrics_paths.items():
        shutil.copyfile(p, real_dir / f"{label}_metrics.json")
    for label, p in doc_paths.items():
        shutil.copyfile(p, real_dir / f"{label}.md")
    shutil.copyfile(args.report, real_dir / "report.md")
    if history_entry is None:
        # Legacy per-job decode key; with a history registry the registry
        # itself is the (single, cross-run) decode key — ADR 0029.
        (real_dir / "mapping.json").write_text(
            json.dumps(mapping.to_dict(), indent=2, ensure_ascii=False)
        )

    # oss/ — redacted
    for label, obj in metrics.items():
        (oss_dir / f"{label}_metrics.json").write_text(
            json.dumps(mapping.redact_json(obj), indent=2, ensure_ascii=False)
        )
    for label, text in doc_texts.items():
        (oss_dir / f"{label}.md").write_text(mapping.redact_text(text))
    (oss_dir / "report.md").write_text(mapping.redact_text(report_text))

    leaked = _leak_scan(oss_dir, mapping)
    print(f"real bundle → {real_dir}")
    print(f"oss  bundle → {oss_dir}")
    print(
        f"values redacted: {args.redact_values} "
        "(metadata is always hidden)"
    )
    print(
        f"mapping: {len(mapping.columns)} columns, "
        f"{len(mapping.identifiers)} identifiers, {len(mapping.values)} values"
    )
    if leaked:
        print("WARNING: possible residual tokens in oss/:")
        for f, tok in leaked:
            print(f"  {f}: {tok!r}")
        return 1
    print("leak scan: clean ✅")

    if args.prune_inputs:
        # Sample CSVs (the only copy) and the report (lives under output/)
        # are never pruned — only ingested metrics JSONs + markdown docs.
        _prune_inputs([*metrics_paths.values(), *doc_paths.values()], base)
    return 0


main_with_args = main


if __name__ == "__main__":
    raise SystemExit(main())
