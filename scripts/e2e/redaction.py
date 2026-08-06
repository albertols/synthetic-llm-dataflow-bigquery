"""Shared redaction + leak-scan machinery for the `scripts/e2e/` toolchain.

Extracted from `e2e_bundle_export.py` so other scripts (e.g. the release
report generator) can build a redaction `Mapping` from collected metrics,
apply it to text/CSV/JSON artifacts, and leak-scan a directory tree without
importing the bundle exporter's argparse/IO orchestration.

Token classes redacted (see `e2e_bundle_export.py`'s module docstring for the
full rationale):

  1. IDENTIFIERS — project / dataset / table / bucket / caller email /
     reference digests / file paths.
  2. COLUMN NAMES — every field name becomes ``COL_NNN`` (primary-key and
     identity columns keep a role prefix: ``PK_COL`` / ``ID_COL``).
  3. DATA VALUES — concrete sampled values become ``VAL_NNNN``.

Sibling import idiom for scripts under `scripts/e2e/`::

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import redaction
"""

from __future__ import annotations

import csv
import io
import re
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
# only replaced when they are all-uppercase alpha, e.g. currency/country codes).
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


def redact_text(m: Mapping, text: str) -> str:
    """Free-function form of `Mapping.redact_text` for sibling-script callers."""
    return m.redact_text(text)


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


def register_csv(m: Mapping, text: str, *, redact_values: bool) -> None:
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


def redact_csv(m: Mapping, text: str) -> str:
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


def leak_scan(oss_dir: Path, mapping: Mapping) -> list[tuple[str, str]]:
    """Fail-safe: confirm no real identifier/column survived into oss_dir.

    Binary-tolerant: reads every file as bytes and decodes with
    ``errors="ignore"`` rather than `Path.read_text()`'s strict UTF-8. Text
    files (the common case — JSON/CSV/md) decode identically either way, so
    behavior there is unchanged; a binary file (e.g. a chart PNG sitting
    alongside the report) no longer crashes the scan with
    `UnicodeDecodeError` and is instead scanned for any decodable token
    remnant, same as a text file.
    """
    reals = list(mapping.identifiers) + list(mapping.columns)
    hits: list[tuple[str, str]] = []
    for f in (f for f in oss_dir.rglob("*") if f.is_file()):
        text = f.read_bytes().decode("utf-8", errors="ignore")
        for real in reals:
            if real and real in text:
                hits.append((f.name, real))
    return hits
