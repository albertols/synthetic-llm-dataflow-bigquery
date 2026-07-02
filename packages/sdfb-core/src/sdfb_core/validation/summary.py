"""Run-level summary + BLOCKER gate — one row of ``validation_runs``.

The Beam pipeline counts valid rows and DLQ entries (grouped by
``rule_id``), then builds a :class:`RunSummary`. If the fraction of
BLOCKER-severity failures exceeds ``blocker_failure_ratio`` the gate
raises :class:`BlockerThresholdExceeded`, which fails the Dataflow job
(skill: validation-mode-a §"Failing the job").

REF: .claude/skills/validation-mode-a.md
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from sdfb_core.validation.thresholds import Thresholds

# rule_ids whose failures count toward the BLOCKER gate. Mirrors the
# BLOCKER-severity rows in config/thresholds.yml.
BLOCKER_RULE_IDS = frozenset({"schema.types", "null.required", "pk.duplicate"})

STATUS_PASSED = "PASSED"
STATUS_FAILED_BLOCKER = "FAILED_BLOCKER"


class BlockerThresholdExceeded(RuntimeError):  # noqa: N818 — name fixed by validation-mode-a skill
    """Raised to FAIL the Dataflow job when the BLOCKER gate trips."""


class RunSummary(BaseModel):
    """One row of ``synthetic_data_quality.validation_runs``."""

    run_id: str
    reference_digest: str
    reference_table: str = ""
    landing_table: str = ""
    engine: str = ""
    model_uri: str = ""
    env: str = "dev"
    num_rows_requested: int = 0
    valid_count: int = 0
    dlq_count: int = 0
    blocker_count: int = 0
    dlq_by_rule: dict[str, int] = Field(default_factory=dict)
    blocker_failure_ratio: float = 0.0
    observed_blocker_ratio: float = 0.0
    status: str = STATUS_PASSED
    created_at: str = ""

    def to_bq_row(self) -> dict:
        """JSON-load-shaped row. ``dlq_by_rule`` is a JSON string so the
        column can be a plain STRING (robust under FILE_LOADS)."""
        row = self.model_dump()
        row["dlq_by_rule"] = json.dumps(row["dlq_by_rule"], sort_keys=True, default=str)
        return row


def build_run_summary(
    *,
    run_id: str,
    reference_digest: str,
    valid_count: int,
    dlq_by_rule: dict[str, int],
    thresholds: Thresholds,
    num_rows_requested: int = 0,
    reference_table: str = "",
    landing_table: str = "",
    engine: str = "",
    model_uri: str = "",
    created_at: str | None = None,
) -> RunSummary:
    """Fold counts + thresholds into a :class:`RunSummary` with a status."""
    dlq_count = sum(dlq_by_rule.values())
    blocker_count = sum(c for rid, c in dlq_by_rule.items() if rid in BLOCKER_RULE_IDS)
    total = valid_count + dlq_count
    observed = (blocker_count / total) if total else 0.0
    status = (
        STATUS_FAILED_BLOCKER
        if observed > thresholds.blocker_failure_ratio
        else STATUS_PASSED
    )
    return RunSummary(
        run_id=run_id,
        reference_digest=reference_digest,
        reference_table=reference_table,
        landing_table=landing_table,
        engine=engine,
        model_uri=model_uri,
        env=thresholds.env,
        num_rows_requested=num_rows_requested,
        valid_count=valid_count,
        dlq_count=dlq_count,
        blocker_count=blocker_count,
        dlq_by_rule=dict(dlq_by_rule),
        blocker_failure_ratio=thresholds.blocker_failure_ratio,
        observed_blocker_ratio=observed,
        status=status,
        created_at=created_at or datetime.now(tz=UTC).isoformat(),
    )


def evaluate_blocker_gate(summary: RunSummary) -> None:
    """Raise :class:`BlockerThresholdExceeded` if the summary failed the gate."""
    if summary.status == STATUS_FAILED_BLOCKER:
        total = summary.valid_count + summary.dlq_count
        raise BlockerThresholdExceeded(
            f"BLOCKER failures {summary.blocker_count}/{total} = "
            f"{summary.observed_blocker_ratio:.4f} exceeds gate "
            f"{summary.blocker_failure_ratio:.4f} (env={summary.env}, "
            f"run_id={summary.run_id})"
        )
