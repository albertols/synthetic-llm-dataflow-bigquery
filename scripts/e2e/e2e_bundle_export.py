#!/usr/bin/env python
"""Bundle the E2E validation artifacts into a shareable, de-identified export.

Produces two sibling folders under ``<out-root>/<job_id>/`` (the primary
Dataflow job id of the deployment; falls back to the report timestamp when no
job id is available):

  * ``real/`` — verbatim copies of every metrics JSON + sample CSV + the
    report, plus a ``mapping.json`` decode key (so the internal team can read
    the real names).
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
        --csv b1_rag=integration_test/<JOB_ID>/b1_rag_sample.csv \
        --report output/end_to_end_validation_report_2026_07_07_16_26.md \
        --out-root integration_test
        # --job-id <JOB_ID>             (default: first Dataflow job id in the
        #                                gcp metrics, else the report timestamp)
        # --no-redact-values            (keep real dev data values in oss/;
        #                                metadata is ALWAYS hidden either way)
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
        help="engine_label=path.csv (repeatable). Sample generated-data CSVs "
        "copied verbatim into real/ and redacted into oss/.",
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
    args = ap.parse_args(argv)

    metrics_paths: dict[str, Path] = {}
    for item in args.metrics:
        if "=" not in item:
            raise SystemExit(f"--metrics expects label=path, got {item!r}")
        label, path = item.split("=", 1)
        metrics_paths[label.strip()] = Path(path.strip())

    csv_paths: dict[str, Path] = {}
    for item in args.csv:
        if "=" not in item:
            raise SystemExit(f"--csv expects engine_label=path, got {item!r}")
        label, path = item.split("=", 1)
        csv_paths[label.strip()] = Path(path.strip())

    metrics = {
        label: json.loads(p.read_text()) for label, p in metrics_paths.items()
    }
    csv_texts = {label: p.read_text() for label, p in csv_paths.items()}
    report_text = args.report.read_text()

    mapping = build_mapping(metrics, redact_values=args.redact_values)
    for text in csv_texts.values():
        _register_csv(mapping, text, redact_values=args.redact_values)

    base = args.out_root / _derive_bundle_name(args.job_id, metrics, args.report)
    real_dir, oss_dir = base / "real", base / "oss"
    real_dir.mkdir(parents=True, exist_ok=True)
    oss_dir.mkdir(parents=True, exist_ok=True)

    # real/ — verbatim + decode key
    for label, p in metrics_paths.items():
        shutil.copyfile(p, real_dir / f"{label}_metrics.json")
    for label, p in csv_paths.items():
        shutil.copyfile(p, real_dir / f"{label}_sample.csv")
    shutil.copyfile(args.report, real_dir / "report.md")
    (real_dir / "mapping.json").write_text(
        json.dumps(mapping.to_dict(), indent=2, ensure_ascii=False)
    )

    # oss/ — redacted
    for label, obj in metrics.items():
        (oss_dir / f"{label}_metrics.json").write_text(
            json.dumps(mapping.redact_json(obj), indent=2, ensure_ascii=False)
        )
    for label, text in csv_texts.items():
        (oss_dir / f"{label}_sample.csv").write_text(_redact_csv(mapping, text))
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
    return 0


main_with_args = main


if __name__ == "__main__":
    raise SystemExit(main())
