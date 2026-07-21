"""Post-write memorization gate (WS3 §5a/§5b). Pure — no Beam imports.

Spec §5a: severity is plain BLOCKER in every env; the 2026-07-07 design's
per-env resolve_severity helper is deliberately gone. §5b: the gate trips
on identical_match_rate > threshold (0.0) OR max(column_copy_ratio) ≥ 0.3.
None inputs mean "not evaluated" — never an implicit pass, never a trip.
"""

from __future__ import annotations

from sdfb_core.validation import Thresholds

_RULE_ID = "memorization.copy_ratio"
_COLUMN_COPY_RATIO_MAX = 0.3
SEVERITY_BLOCKER = "BLOCKER"


class MemorizationThresholdExceeded(RuntimeError):  # noqa: N818 — mirrors BlockerThresholdExceeded
    """Fails the Dataflow job when the memorization gate trips at BLOCKER severity."""


def evaluate_memorization_gate(
    *,
    identical_match_rate: float | None,
    column_copy_ratios: dict[str, float] | None,
    thresholds: Thresholds | None,
    rule_id: str = _RULE_ID,
) -> dict:
    """Returns the outcome dict stored under raw_metrics_json.memorization_gate.

    Raising is a separate step (raise_if_blocker) so the eval row is ALWAYS
    written before the job is failed."""
    rules = (thresholds.rules if thresholds else {}) or {}
    rule = rules.get(rule_id, {})
    severity = str(rule.get("severity", SEVERITY_BLOCKER))
    threshold = float(rule.get("threshold", 0.0))
    max_column_ratio = (
        max(column_copy_ratios.values()) if column_copy_ratios else None
    )
    outcome = {
        "rule_id": rule_id,
        "severity": severity,
        "threshold": threshold,
        "column_copy_ratio_max": max_column_ratio,
        "evaluated": identical_match_rate is not None or max_column_ratio is not None,
        "tripped": False,
        "reasons": [],
    }
    if not outcome["evaluated"]:
        return outcome
    if identical_match_rate is not None and identical_match_rate > threshold:
        outcome["reasons"].append(
            f"identical_match_rate={identical_match_rate:.6f} > {threshold}"
        )
    if max_column_ratio is not None and max_column_ratio >= _COLUMN_COPY_RATIO_MAX:
        worst = max(column_copy_ratios, key=column_copy_ratios.get)
        outcome["reasons"].append(
            f"max(column_copy_ratio)={max_column_ratio:.4f} >= "
            f"{_COLUMN_COPY_RATIO_MAX} (column={worst})"
        )
    outcome["tripped"] = bool(outcome["reasons"])
    return outcome


def raise_if_blocker(outcome: dict, *, run_id: str) -> None:
    """MAJOR/other severities record only — same 'MAJOR → metric only'
    semantics as thresholds.yml's ladder."""
    if outcome.get("tripped") and outcome.get("severity") == SEVERITY_BLOCKER:
        raise MemorizationThresholdExceeded(
            f"run_id={run_id} memorization gate tripped: "
            + "; ".join(outcome.get("reasons", []))
        )
