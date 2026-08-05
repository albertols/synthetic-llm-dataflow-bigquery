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
import csv
import io
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Leaf dict keys whose STRING value is a concrete sampled data value to redact.
_VALUE_LEAF_KEYS = frozenset({"value"})
# Numeric-looking data values are not sensitive on their own; skip them in the
# textual (report) pass to avoid corrupting counts. They are still redacted
# structurally in JSON only when they sit under a _VALUE_LEAF_KEYS field.
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
# Generic PII: any email address, redacted regardless of the derived mapping
# (the caller may appear in prose even when the metrics captured it as
# "unknown"). The OSS repo handle "org/repo" is not an email and is preserved.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMAIL_PLACEHOLDER = "analyst@example.org"
_FQN_PARTS = 3
# Minimum length for a data value to be replaced in prose (shorter tokens are
# only redacted when they are all-uppercase alpha, e.g. currency/country codes).
_MIN_TEXT_VALUE_LEN = 4


# --------------------------------------------------------------------------
# token collection (all derived from the artifacts — nothing table-specific)
# --------------------------------------------------------------------------
class Mapping:
    """Deterministic real→placeholder registries + application helpers."""

    def __init__(self) -> None:
        self.identifiers: dict[str, str] = {}  # substring replacements
        self.columns: dict[str, str] = {}      # exact field-name replacements
        self.values: dict[str, str] = {}       # exact data-value replacements
        self._col_n = 0
        self._val_n = 0

    # -- registration ------------------------------------------------------
    def add_identifier(self, real: str | None, placeholder: str) -> None:
        if real and real not in self.identifiers:
            self.identifiers[real] = placeholder

    def add_column(self, real: str, *, role: str | None = None) -> None:
        if not real or real in self.columns:
            return
        if role == "pk":
            n = sum(1 for v in self.columns.values() if v.startswith("PK_COL"))
            self.columns[real] = "PK_COL" if n == 0 else f"PK_COL_{n + 1}"
        elif role == "identity":
            n = sum(1 for v in self.columns.values() if v.startswith("ID_COL"))
            self.columns[real] = "ID_COL" if n == 0 else f"ID_COL_{n + 1}"
        else:
            self._col_n += 1
            self.columns[real] = f"COL_{self._col_n:03d}"

    def add_value(self, real: str) -> None:
        if real is None:
            return
        real = str(real)
        if real == "" or real in self.values:
            return
        self._val_n += 1
        self.values[real] = f"VAL_{self._val_n:04d}"

    # -- application -------------------------------------------------------
    def _ident_pairs(self) -> list[tuple[str, str]]:
        # Longest real first so e.g. ``synthetic_data_quality`` is replaced
        # before ``synthetic_data``, and full FQNs before their parts.
        return sorted(self.identifiers.items(), key=lambda kv: -len(kv[0]))

    def redact_scalar(self, s: str) -> str:
        if s in self.values:
            return self.values[s]
        if s in self.columns:
            return self.columns[s]
        for real, placeholder in self._ident_pairs():
            if real in s:
                s = s.replace(real, placeholder)
        return _EMAIL_RE.sub(_EMAIL_PLACEHOLDER, s)

    def redact_json(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for k, v in obj.items():
                new_key = self.columns.get(k, self.redact_scalar(k))
                if k in _VALUE_LEAF_KEYS and isinstance(v, str):
                    out[new_key] = self.values.get(v, self.redact_scalar(v))
                else:
                    out[new_key] = self.redact_json(v)
            return out
        if isinstance(obj, list):
            return [self.redact_json(v) for v in obj]
        if isinstance(obj, str):
            return self.redact_scalar(obj)
        return obj

    def redact_text(self, text: str) -> str:
        # 0) generic PII: any email address.
        text = _EMAIL_RE.sub(_EMAIL_PLACEHOLDER, text)
        # 1) identifiers: plain substring, longest-first.
        for real, placeholder in self._ident_pairs():
            text = text.replace(real, placeholder)
        # 2) columns + long/uppercase data values: whole-word, longest-first.
        word_tokens = list(self.columns.items())
        for real, placeholder in self.values.items():
            if _NUMERIC_RE.match(real):
                continue  # don't touch bare numbers in prose
            if len(real) >= _MIN_TEXT_VALUE_LEN or (real.isupper() and real.isalpha()):
                word_tokens.append((real, placeholder))
        for real, placeholder in sorted(word_tokens, key=lambda kv: -len(kv[0])):
            # allow an optional plural/possessive 's' (e.g. "UUIDs" → "ID_COL")
            text = re.sub(rf"(?<!\w){re.escape(real)}s?(?!\w)", placeholder, text)
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifiers": self.identifiers,
            "columns": self.columns,
            "values": self.values,
        }


def _split_fqn(fqn: str) -> tuple[str, str, str] | None:
    parts = fqn.split(".")
    return (parts[0], parts[1], parts[2]) if len(parts) == _FQN_PARTS else None


def build_mapping(
    metrics: dict[str, Any],
    *,
    redact_values: bool = True,
) -> Mapping:
    """Derive a full redaction mapping from the collected metrics artifacts."""
    m = Mapping()
    gcp = metrics.get("gcp") or {}
    offline = metrics.get("offline") or {}
    bq = gcp.get("bigquery") or {}

    pk_cols = set(offline.get("primary_key") or []) | set(bq.get("pk_columns") or [])
    id_cols = set(offline.get("identity_columns") or [])

    _collect_identifiers(gcp, bq, m)
    _collect_columns(gcp, offline, pk_cols, id_cols, m)
    if redact_values:
        _collect_values(offline, m)
    return m


def _collect_identifiers(gcp: dict[str, Any], bq: dict[str, Any], m: Mapping) -> None:
    """Project / dataset / table / bucket / caller / job / digest tokens."""
    m.add_identifier(gcp.get("project"), "PROJECT_ID")
    caller = gcp.get("caller_identity")
    if caller and "@" in caller:  # skip sentinels like "unknown"
        m.add_identifier(caller, _EMAIL_PLACEHOLDER)

    dataset_roles: dict[str, str] = {}
    table_name: str | None = None
    for role, fqn in (
        ("SOURCE_DATASET", bq.get("source_fqn")),
        ("LANDING_DATASET", bq.get("landing_fqn")),
    ):
        parts = _split_fqn(fqn) if fqn else None
        if parts:
            dataset_roles.setdefault(parts[1], role)
            table_name = table_name or parts[2]

    for row in (gcp.get("quality") or {}).get("validation_runs") or []:
        m.add_identifier(row.get("reference_digest"), "REDACTED_DIGEST")
        mobj = re.match(r"gs://([^/]+)/", row.get("model_uri") or "")
        if mobj:
            m.add_identifier(mobj.group(1), "MODEL_BUCKET")
        for key, default_role in (
            ("reference_table", "SOURCE_DATASET"),
            ("landing_table", "LANDING_DATASET"),
        ):
            parts = _split_fqn(row.get(key, "")) if row.get(key) else None
            if not parts:
                continue
            ds = parts[1]
            role = "QUALITY_DATASET" if ds.endswith("_quality") else default_role
            dataset_roles.setdefault(ds, role)
            table_name = table_name or parts[2]

    for ds, role in dataset_roles.items():
        m.add_identifier(ds, role)
    m.add_identifier(table_name, "TARGET_TABLE")

    # Dataflow job ids / job names are intentionally NOT redacted: they name
    # the bundle folder and must stay correlatable in the oss/ artifacts.
    for job in gcp.get("dataflow") or []:
        img = (job.get("environment") or {}).get("worker_image")
        if img:
            m.add_identifier(img, "WORKER_IMAGE")


def _collect_columns(
    gcp: dict[str, Any],
    offline: dict[str, Any],
    pk_cols: set[str],
    id_cols: set[str],
    m: Mapping,
) -> None:
    """Field names → COL_NNN (schema order preferred; role columns first)."""
    bq_cols = (gcp.get("bigquery") or {}).get("columns") or {}
    ordered_cols: list[str] = list(bq_cols)
    for eng in (offline.get("engines") or {}).values():
        for c in eng.get("columns") or {}:
            if c not in ordered_cols:
                ordered_cols.append(c)
    for c in ordered_cols:
        if c in pk_cols:
            m.add_column(c, role="pk")
    for c in ordered_cols:
        if c in id_cols:
            m.add_column(c, role="identity")
    for c in ordered_cols:
        m.add_column(c)



def _collect_values(offline: dict[str, Any], m: Mapping) -> None:
    """Walk the offline metrics for concrete ``value`` leaves to redact."""

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k in _VALUE_LEAF_KEYS and isinstance(v, str):
                    m.add_value(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(offline)


def _register_csv(m: Mapping, text: str, *, redact_values: bool) -> None:
    """Register a sample CSV's header columns (+ cell values) in the mapping."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return
    for col in rows[0]:
        m.add_column(col)
    if not redact_values:
        return
    for row in rows[1:]:
        for cell in row:
            if cell and not _NUMERIC_RE.match(cell):
                m.add_value(cell)


def _redact_csv(m: Mapping, text: str) -> str:
    """Structurally redact a sample CSV (header via columns, cells via values)."""
    rows = list(csv.reader(io.StringIO(text)))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for i, row in enumerate(rows):
        if i == 0:
            writer.writerow(
                [m.columns.get(c, m.redact_scalar(c)) for c in row]
            )
        else:
            writer.writerow(
                [m.values.get(c, m.redact_scalar(c)) for c in row]
            )
    return out.getvalue()


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


def _leak_scan(oss_dir: Path, mapping: Mapping) -> list[tuple[str, str]]:
    """Fail-safe: confirm no real identifier/column survived into oss/."""
    reals = list(mapping.identifiers) + list(mapping.columns)
    hits: list[tuple[str, str]] = []
    for f in (f for f in oss_dir.rglob("*") if f.is_file()):
        text = f.read_text()
        for real in reals:
            if real and real in text:
                hits.append((f.name, real))
    return hits


if __name__ == "__main__":
    raise SystemExit(main())
