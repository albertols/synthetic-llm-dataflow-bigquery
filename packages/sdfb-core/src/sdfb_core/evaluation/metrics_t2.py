"""Tier-2 evaluation — SDMetrics QualityReport / DiagnosticReport (WS3 §3).

sdmetrics is a BASE dependency: the 2026-07-21 audit confirmed 0.28.x
declares torch only under its optional [torch] extra, so sdfb-core's
no-torch rule holds (design §6 caveat resolved).
"""

from __future__ import annotations

import pandas as pd

from sdfb_core.contracts import TableSchema

_NUMERIC = {"INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC"}
_TEMPORAL = {"DATE", "DATETIME", "TIMESTAMP", "TIME"}
_BOOLEAN = {"BOOL", "BOOLEAN"}


def sdmetrics_metadata(table_schema: TableSchema, columns: list[str]) -> dict:
    """Single-table SDMetrics metadata dict derived from the BQ schema."""
    wanted = set(columns)
    sdtypes: dict[str, dict] = {}
    for field in table_schema.columns:
        if field.name not in wanted:
            continue
        if field.bq_type in _NUMERIC:
            sdtypes[field.name] = {"sdtype": "numerical"}
        elif field.bq_type in _TEMPORAL:
            sdtypes[field.name] = {"sdtype": "datetime"}
        elif field.bq_type in _BOOLEAN:
            sdtypes[field.name] = {"sdtype": "boolean"}
        else:
            sdtypes[field.name] = {"sdtype": "categorical"}
    # Columns present in the sample but absent from the schema (should not
    # happen) default to categorical so the report never KeyErrors.
    for name in wanted - sdtypes.keys():
        sdtypes[name] = {"sdtype": "categorical"}
    return {"columns": sdtypes}


def coerce_for_sdmetrics(df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    """Parse datetime-sdtype columns to tz-naive datetimes — BQ rows arrive
    as ISO strings / date objects, which SDMetrics' datetime handling rejects."""
    out = df.copy()
    for col, spec in metadata["columns"].items():
        if spec.get("sdtype") == "datetime" and col in out.columns:
            parsed = pd.to_datetime(out[col], errors="coerce", utc=True)
            out[col] = parsed.dt.tz_localize(None)
    return out


def sdmetrics_quality(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, metadata: dict
) -> dict:
    from sdmetrics.reports.single_table import QualityReport

    report = QualityReport()
    report.generate(real_df, synth_df, metadata, verbose=False)
    properties = report.get_properties()
    return {
        "score": float(report.get_score()),
        "properties": {
            str(row["Property"]): float(row["Score"])
            for _, row in properties.iterrows()
        },
    }


def sdmetrics_diagnostic(
    real_df: pd.DataFrame, synth_df: pd.DataFrame, metadata: dict
) -> dict:
    from sdmetrics.reports.single_table import DiagnosticReport

    report = DiagnosticReport()
    report.generate(real_df, synth_df, metadata, verbose=False)
    properties = report.get_properties()
    return {
        "score": float(report.get_score()),
        "properties": {
            str(row["Property"]): float(row["Score"])
            for _, row in properties.iterrows()
        },
    }
