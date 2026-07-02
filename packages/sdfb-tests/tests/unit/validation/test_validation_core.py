"""Unit tests for the pure-Python Mode-A validation core (M1 §12)."""

from __future__ import annotations

import json

import pytest
from sdfb_core.validation import (
    BLOCKER_RULE_IDS,
    STATUS_FAILED_BLOCKER,
    STATUS_PASSED,
    BlockerThresholdExceeded,
    Thresholds,
    build_run_summary,
    evaluate_blocker_gate,
    load_thresholds,
    normalize_dlq_record,
)


def _thresholds(ratio: float = 0.05, env: str = "dev") -> Thresholds:
    return Thresholds(env=env, blocker_failure_ratio=ratio)


class TestThresholds:
    def test_from_mapping_resolves_env(self):
        data = {
            "defaults": {"blocker_failure_ratio": {"dev": 0.2, "prd": 0.01}},
            "rules": {"schema.types": {"severity": "BLOCKER"}},
        }
        t = Thresholds.from_mapping(data, "prd")
        assert t.env == "prd"
        assert t.blocker_failure_ratio == 0.01
        assert t.rules == {"schema.types": {"severity": "BLOCKER"}}

    def test_from_mapping_scalar_ratio(self):
        t = Thresholds.from_mapping({"defaults": {"blocker_failure_ratio": 0.1}}, "dev")
        assert t.blocker_failure_ratio == 0.1

    def test_from_mapping_missing_env_raises(self):
        with pytest.raises(ValueError, match="env="):
            Thresholds.from_mapping(
                {"defaults": {"blocker_failure_ratio": {"dev": 0.2}}}, "prd"
            )

    def test_load_thresholds_from_file(self, tmp_path):
        p = tmp_path / "t.yml"
        p.write_text("defaults:\n  blocker_failure_ratio:\n    dev: 0.2\n")
        assert load_thresholds(p, "dev").blocker_failure_ratio == 0.2


class TestRunSummary:
    def test_critical_failures_do_not_count_as_blocker(self):
        s = build_run_summary(
            run_id="r", reference_digest="d", valid_count=100,
            dlq_by_rule={"schema.batch": 5}, thresholds=_thresholds(0.05),
        )
        assert s.blocker_count == 0
        assert s.dlq_count == 5
        assert s.status == STATUS_PASSED

    def test_blocker_ratio_exceeded_fails(self):
        s = build_run_summary(
            run_id="r", reference_digest="d", valid_count=90,
            dlq_by_rule={"schema.types": 10}, thresholds=_thresholds(0.05),
        )
        assert s.blocker_count == 10
        assert s.observed_blocker_ratio == pytest.approx(0.10)
        assert s.status == STATUS_FAILED_BLOCKER

    def test_boundary_equal_is_pass(self):
        # exactly at the ratio is NOT strictly greater -> pass
        s = build_run_summary(
            run_id="r", reference_digest="d", valid_count=95,
            dlq_by_rule={"schema.types": 5}, thresholds=_thresholds(0.05),
        )
        assert s.observed_blocker_ratio == pytest.approx(0.05)
        assert s.status == STATUS_PASSED

    def test_empty_run_is_pass(self):
        s = build_run_summary(
            run_id="r", reference_digest="d", valid_count=0,
            dlq_by_rule={}, thresholds=_thresholds(0.05),
        )
        assert s.observed_blocker_ratio == 0.0
        assert s.status == STATUS_PASSED

    def test_to_bq_row_serializes_dlq_map(self):
        s = build_run_summary(
            run_id="r", reference_digest="d", valid_count=1,
            dlq_by_rule={"schema.types": 2}, thresholds=_thresholds(1.0),
        )
        row = s.to_bq_row()
        assert isinstance(row["dlq_by_rule"], str)
        assert json.loads(row["dlq_by_rule"]) == {"schema.types": 2}
        assert row["created_at"]


class TestBlockerGate:
    def test_raises_on_failed_blocker(self):
        s = build_run_summary(
            run_id="r", reference_digest="d", valid_count=0,
            dlq_by_rule={"schema.types": 1}, thresholds=_thresholds(0.0),
        )
        assert s.status == STATUS_FAILED_BLOCKER
        with pytest.raises(BlockerThresholdExceeded):
            evaluate_blocker_gate(s)

    def test_passes_silently(self):
        s = build_run_summary(
            run_id="r", reference_digest="d", valid_count=10,
            dlq_by_rule={}, thresholds=_thresholds(0.05),
        )
        evaluate_blocker_gate(s)


class TestDlqNormalize:
    def test_pydantic_envelope(self):
        raw = {
            "raw_record": {"a": 1}, "error_type": "pydantic",
            "error_detail": [{"loc": ["a"]}], "rule_id": "schema.types",
            "stage": "pre_write",
        }
        out = normalize_dlq_record(raw, run_id="r1")
        assert out["run_id"] == "r1"
        assert json.loads(out["raw_record"]) == {"a": 1}
        assert out["error_type"] == "pydantic"
        assert out["pipeline_step"] == "ValidateRecordDoFn"
        assert out["rule_id"] == "schema.types"
        assert out["stage"] == "pre_write"
        assert out["dlq_inserted_at"]

    def test_engine_envelope_uses_raw_request(self):
        raw = {
            "raw_request": {"batch_id": 0, "n": 5}, "error_type": "engine",
            "error_detail": "ValueError: boom", "rule_id": "engine_failure",
            "stage": "pre_write",
        }
        out = normalize_dlq_record(raw, run_id="r2")
        assert json.loads(out["raw_record"]) == {"batch_id": 0, "n": 5}
        assert out["pipeline_step"] == "GenerateRecordsDoFn"

    def test_explicit_inserted_at_and_step(self):
        out = normalize_dlq_record(
            {"error_type": "pandera"}, run_id="r",
            pipeline_step="X", inserted_at="2026-01-01T00:00:00+00:00",
        )
        assert out["dlq_inserted_at"] == "2026-01-01T00:00:00+00:00"
        assert out["pipeline_step"] == "X"


def test_blocker_rule_ids_constant():
    assert "schema.types" in BLOCKER_RULE_IDS
    assert "schema.batch" not in BLOCKER_RULE_IDS
