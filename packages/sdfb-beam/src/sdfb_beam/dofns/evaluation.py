"""Post-WriteLanding evaluation branch (WS3): stratified reservoir CombineFn
+ the single-worker EvaluationDoFn.

This module is the ONLY pipeline importer of the Tier 1/2 metric libraries
(matching how PanderaValidateBatchDoFn is the only pandera importer).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime

import apache_beam as beam
import pandas as pd
from sdfb_core.contracts import TableSchema
from sdfb_core.evaluation import metrics_t1, metrics_t2
from sdfb_core.evaluation.gate import evaluate_memorization_gate
from sdfb_core.evaluation.profile import StratificationPlan
from sdfb_core.evaluation.sampling import (
    ReservoirAccumulator,
    add_row,
    extract_sample,
    merge_accumulators,
)
from sdfb_core.observability import log_milestone
from sdfb_core.validation import Thresholds

logger = logging.getLogger(__name__)


def _json_sanitize(obj):
    """Recursively map float NaN/+-Inf -> None through dicts/lists.

    sdmetrics can emit NaN properties (e.g. Column Pair Trends on a constant
    column). ``json.dumps`` serializes NaN/Infinity as the bare tokens
    ``NaN``/``Infinity``, which are invalid JSON — BigQuery's JSON column
    load would fail on them. Everything else passes through untouched.
    """
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_sanitize(v) for v in obj]
    return obj


def _clean_metric(value: float | None) -> float | None:
    """Row-column counterpart of ``_json_sanitize`` for scalar metric fields."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


class StratifiedReservoirFn(beam.CombineFn):
    """Thin CombineFn over sdfb_core.evaluation.sampling's pure functions.
    One selection code path governs the real AND synthetic sides."""

    def __init__(
        self,
        plan: StratificationPlan,
        run_id: str,
        cap: int,
        overall_cap: int = 50_000,
    ):
        self._plan = plan
        self._run_id = run_id
        self._cap = cap
        self._overall_cap = overall_cap

    def create_accumulator(self) -> ReservoirAccumulator:
        return ReservoirAccumulator()

    def add_input(self, acc: ReservoirAccumulator, row: dict) -> ReservoirAccumulator:
        return add_row(acc, row, plan=self._plan, run_id=self._run_id, cap=self._cap)

    def merge_accumulators(self, accs) -> ReservoirAccumulator:
        return merge_accumulators(accs, cap=self._cap)

    def extract_output(self, acc: ReservoirAccumulator) -> list[dict]:
        return extract_sample(acc, cap=self._cap, overall_cap=self._overall_cap)


class EvaluationDoFn(beam.DoFn):
    """ONE invocation per run (Create([None]) seed + AsSingleton side inputs —
    the _build_validation_run_row shape). ALWAYS yields exactly one
    validation_data_history row; empty samples yield the skipped row."""

    def __init__(
        self,
        *,
        table_schema: TableSchema,
        run_id: str,
        execution_id: str,
        engine: str,
        engine_version: str,
        feature_flag_tags: list[str],
        thresholds: Thresholds | None,
        free_text_columns: list[str],
        num_rows: int,
        landing_table: str,
        history_table: str = "",
        validation_runs_table: str = "",
        bq_client_factory: Callable | None = None,
    ):
        self._table_schema = table_schema
        self._run_id = run_id
        self._execution_id = execution_id
        self._engine = engine
        self._engine_version = engine_version
        self._feature_flag_tags = list(feature_flag_tags)
        self._thresholds = thresholds
        self._free_text_columns = list(free_text_columns)
        self._num_rows = num_rows
        self._landing_table = landing_table
        self._history_table = history_table
        self._validation_runs_table = validation_runs_table
        self._bq_client_factory = bq_client_factory

    def process(  # noqa: PLR0915 — one linear tier-1/tier-2/gate pass; splitting
        # it would scatter the "always build this one row" invariant across
        # helpers that each need the same row/raw state.
        self, _seed, real_sample: list[dict], synth_sample: list[dict]
    ):
        row: dict = {
            "execution_id": self._execution_id,
            "execution_timestamp": datetime.now(UTC).isoformat(),
            "run_id": self._run_id,
            "engine": self._engine,
            "engine_version": self._engine_version,
            "feature_flag_tags": list(self._feature_flag_tags),
            "sample_rows_real": len(real_sample),
            "sample_rows_synthetic": len(synth_sample),
            "fidelity_overall_score": None,
            "avg_dcr": None,
            "nndr": None,
            "identical_match_rate": None,
            "max_psi": None,
            "corr_diff_frobenius": None,
            "tstr_f1_delta": None,  # reserved for TSTR (design §3) — always NULL
        }
        if not real_sample or not synth_sample:
            reason = "empty_real_sample" if not real_sample else "empty_synthetic_sample"
            logger.warning("evaluation skipped: %s (run_id=%s)", reason, self._run_id)
            gate = evaluate_memorization_gate(
                identical_match_rate=None,
                column_copy_ratios=None,
                thresholds=self._thresholds,
            )
            row["raw_metrics_json"] = json.dumps(
                _json_sanitize(
                    {
                        "status": "skipped_insufficient_sample",
                        "reason": reason,
                        "memorization_gate": gate,
                    }
                ),
                default=str,
            )
            yield row
            return

        real_df = pd.DataFrame(real_sample)
        synth_df = pd.DataFrame(synth_sample)
        raw: dict = {"status": "evaluated", "columns": {}, "tier3": "not_installed"}

        numeric, _ = metrics_t1.numeric_and_categorical_columns(real_df)
        distributions: dict[str, dict] = {}
        for col in real_df.columns:
            if col not in synth_df.columns:
                continue
            entry: dict = {}
            if col in numeric:
                entry["ks"] = metrics_t1.ks_statistic(real_df[col], synth_df[col])
                entry["wasserstein"] = metrics_t1.wasserstein(
                    real_df[col], synth_df[col]
                )
            else:
                entry["tvd"] = metrics_t1.tvd(real_df[col], synth_df[col])
            raw["columns"][col] = entry
            distributions[col] = metrics_t1.binned_frequencies(synth_df[col])
        # Load-bearing: the NEXT run's PSI/JSD diffs against these (§4 notes).
        raw["distributions"] = distributions

        row["corr_diff_frobenius"] = _clean_metric(
            metrics_t1.corr_diff_frobenius(real_df, synth_df)
        )
        raw["corr_diff_frobenius_spearman"] = metrics_t1.corr_diff_frobenius(
            real_df, synth_df, method="spearman"
        )
        raw["mi_matrix_diff_frobenius"] = metrics_t1.mi_matrix_diff(real_df, synth_df)
        avg_dcr, nndr = metrics_t1.dcr_nndr(real_df, synth_df)
        row["avg_dcr"], row["nndr"] = _clean_metric(avg_dcr), _clean_metric(nndr)
        row["identical_match_rate"] = _clean_metric(
            metrics_t1.identical_match_rate(real_sample, synth_sample)
        )
        column_ratios = metrics_t1.column_copy_ratios(real_sample, synth_sample)
        raw["column_copy_ratios"] = column_ratios
        raw["cardinality_floor"] = metrics_t1.cardinality_floor(
            real_sample,
            synth_sample,
            free_text_columns=self._free_text_columns,
            num_rows=self._num_rows,
        )

        try:
            metadata = metrics_t2.sdmetrics_metadata(
                self._table_schema, list(real_df.columns)
            )
            real_coerced = metrics_t2.coerce_for_sdmetrics(real_df, metadata)
            synth_coerced = metrics_t2.coerce_for_sdmetrics(synth_df, metadata)
            quality = metrics_t2.sdmetrics_quality(real_coerced, synth_coerced, metadata)
            row["fidelity_overall_score"] = _clean_metric(quality["score"])
            quality["diagnostic"] = metrics_t2.sdmetrics_diagnostic(
                real_coerced, synth_coerced, metadata
            )
            raw["sdmetrics"] = quality
        except Exception as e:  # a Tier-2 library error must never sink the row
            logger.warning("tier-2 sdmetrics failed: %s", e)
            raw["sdmetrics"] = {"error": f"{type(e).__name__}: {e}"}

        previous = self._fetch_previous_distributions()
        if previous:
            psis: dict[str, float] = {}
            jsds: dict[str, float] = {}
            for col, prev_stats in previous.items():
                if col not in synth_df.columns:
                    continue
                edges = (
                    prev_stats.get("edges")
                    if prev_stats.get("kind") == "numeric"
                    else None
                )
                curr = metrics_t1.binned_frequencies(synth_df[col], edges=edges)
                psi_value = metrics_t1.psi(curr, prev_stats)
                jsd_value = metrics_t1.jsd(curr, prev_stats)
                if psi_value is not None:
                    psis[col] = psi_value
                if jsd_value is not None:
                    jsds[col] = jsd_value
            raw["psi"], raw["jsd"] = psis, jsds
            row["max_psi"] = _clean_metric(max(psis.values()) if psis else None)

        gate = evaluate_memorization_gate(
            identical_match_rate=row["identical_match_rate"],
            column_copy_ratios=column_ratios,
            thresholds=self._thresholds,
        )
        raw["memorization_gate"] = gate
        log_milestone(
            "evaluation_row_built",
            run_id=self._run_id,
            gate_tripped=gate["tripped"],
            sample_real=len(real_sample),
            sample_synth=len(synth_sample),
        )
        row["raw_metrics_json"] = json.dumps(_json_sanitize(raw), default=str)
        yield row

    def _fetch_previous_distributions(self) -> dict | None:
        """The ONE extra BQ read this design introduces: previous row per
        (landing_table, engine) via the validation_runs join — LIMIT 1, once
        per run. Any failure degrades to NULL max_psi, never a crash."""
        if not (
            self._history_table
            and self._validation_runs_table
            and self._landing_table
        ):
            return None
        try:
            from google.cloud import bigquery

            client = (
                self._bq_client_factory()
                if self._bq_client_factory
                else bigquery.Client()
            )
            sql = (
                f"SELECT h.raw_metrics_json FROM `{self._history_table}` h "
                f"JOIN `{self._validation_runs_table}` r USING (run_id) "
                "WHERE r.landing_table = @landing_table AND h.engine = @engine "
                "ORDER BY h.execution_timestamp DESC LIMIT 1"
            )
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter(
                        "landing_table", "STRING", self._landing_table
                    ),
                    bigquery.ScalarQueryParameter("engine", "STRING", self._engine),
                ]
            )
            rows = list(client.query(sql, job_config=job_config).result())
            if not rows:
                return None
            payload = rows[0]["raw_metrics_json"]
            data = json.loads(payload) if isinstance(payload, str) else dict(payload)
            return data.get("distributions") or None
        except Exception as e:
            logger.warning(
                "previous-row lookup failed (%s); max_psi stays NULL", e
            )
            return None
