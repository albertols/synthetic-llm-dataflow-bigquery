"""§5a/§5b memorization gate — plain BLOCKER, compound trip, None never passes silently."""

import pytest
from sdfb_core.evaluation.gate import (
    MemorizationThresholdExceeded,
    evaluate_memorization_gate,
    raise_if_blocker,
)
from sdfb_core.validation import Thresholds

_THRESHOLDS = Thresholds(
    env="dev",
    blocker_failure_ratio=0.2,
    rules={
        "memorization.copy_ratio": {
            "dimension": "privacy",
            "severity": "BLOCKER",
            "threshold": 0.0,
        }
    },
)


def _gate(imr, ratios, thresholds=_THRESHOLDS):
    return evaluate_memorization_gate(
        identical_match_rate=imr, column_copy_ratios=ratios, thresholds=thresholds
    )


def test_clean_run_does_not_trip():
    out = _gate(0.0, {"id": 0.05})
    assert out["evaluated"] and not out["tripped"]
    raise_if_blocker(out, run_id="r1")  # no raise


def test_any_identical_match_trips():
    out = _gate(0.001, {})
    assert out["tripped"] and out["severity"] == "BLOCKER"
    with pytest.raises(MemorizationThresholdExceeded, match="r1"):
        raise_if_blocker(out, run_id="r1")


def test_column_copy_ratio_at_030_trips_even_when_rows_unique():
    # The 2026-07-20 failure mode: row-unique but column-verbatim.
    out = _gate(0.0, {"col_048": 1.0, "tier": 0.1})
    assert out["tripped"]
    assert out["column_copy_ratio_max"] == 1.0
    assert any("col_048" in r for r in out["reasons"])
    assert not _gate(0.0, {"col_048": 0.29})["tripped"]


def test_none_inputs_mean_not_evaluated_never_pass_never_trip():
    out = _gate(None, None)
    assert not out["evaluated"] and not out["tripped"]
    raise_if_blocker(out, run_id="r1")  # no raise


def test_severity_is_blocker_in_every_env_even_dev():
    for env in ("dev", "uat", "prd"):
        thresholds = _THRESHOLDS.model_copy(update={"env": env})
        assert _gate(0.5, {}, thresholds)["severity"] == "BLOCKER"


def test_missing_rule_defaults_to_blocker_zero():
    bare = Thresholds(env="dev", blocker_failure_ratio=0.2, rules={})
    out = _gate(0.1, {}, bare)
    assert out["tripped"] and out["severity"] == "BLOCKER" and out["threshold"] == 0.0


def test_non_blocker_severity_records_but_does_not_raise():
    major = Thresholds(
        env="dev",
        blocker_failure_ratio=0.2,
        rules={"memorization.copy_ratio": {"severity": "MAJOR", "threshold": 0.0}},
    )
    out = _gate(0.5, {}, major)
    assert out["tripped"] and out["severity"] == "MAJOR"
    raise_if_blocker(out, run_id="r1")  # MAJOR → metric only, no raise
