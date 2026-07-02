"""Normalize heterogeneous failure envelopes into the dead_letter schema.

The three Mode-A DoFns emit slightly different dicts:

    generate.failed         {raw_request, error_type=engine,   error_detail:str,  rule_id, stage}
    validate_record.invalid {raw_record,  error_type=pydantic, error_detail:list, rule_id, stage}
    pandera_batch.invalid   {raw_record,  error_type=pandera,  error_detail:dict, rule_id, stage}

This maps all of them onto the uniform
``synthetic_data_quality.dead_letter`` row (skill: validation-mode-a).
JSON-ish payloads are serialized to strings so the columns stay plain
STRING and survive FILE_LOADS without nested-type surprises.

REF: .claude/skills/validation-mode-a.md
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

_STEP_BY_ERROR_TYPE = {
    "engine": "GenerateRecordsDoFn",
    "pydantic": "ValidateRecordDoFn",
    "pandera": "PanderaValidateBatchDoFn",
}


def normalize_dlq_record(
    raw: dict[str, Any],
    *,
    run_id: str,
    pipeline_step: str = "",
    inserted_at: str | None = None,
) -> dict[str, Any]:
    """Map one raw failure envelope to a dead_letter row."""
    error_type = raw.get("error_type", "unknown")
    payload = raw.get("raw_record", raw.get("raw_request"))
    return {
        "dlq_inserted_at": inserted_at or datetime.now(tz=UTC).isoformat(),
        "run_id": run_id,
        "raw_record": json.dumps(payload, sort_keys=True, default=str),
        "error_type": error_type,
        "error_detail": json.dumps(raw.get("error_detail"), default=str),
        "rule_id": raw.get("rule_id", ""),
        "pipeline_step": pipeline_step or _STEP_BY_ERROR_TYPE.get(error_type, ""),
        "stage": raw.get("stage", "pre_write"),
    }
