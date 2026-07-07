"""Loud fallback taxonomy — per-call generation failures still fall back to
exemplars, but must never do so silently.

Production defect (E2E report §4.2): when the LLM fails (e.g. vLLM can't
load Gemma on a T4), both B.2's ``FreeTextHook`` and B.1's
``_infer_free_text_pool`` silently fell back to copying observed reference
exemplars — 100% memorization that nobody noticed. Both except-paths must
now emit a WARNING milestone ``freetext_llm_fallback`` (Task 1's
``log_milestone``) before returning the exemplar fallback.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationConfig, GenerationContext
from sdfb_core.engines.b1_rag import B1RagEngine, HashingEmbedder
from sdfb_core.engines.b2_library.fidelity import profile_table
from sdfb_core.engines.b2_library.freetext import FreeTextHook


class _BoomClient:
    """A `ModelClient` whose `generate_json` always raises."""

    def generate_json(self, *a, **k):
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Fixtures — mirrors of the shapes in test_b2_library.py / test_b1_rag.py,
# kept local since those modules define theirs as file-local fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def wide_ctx() -> GenerationContext:
    ddl = {
        "table_info": {"table_id": "demo.support_tickets"},
        "schema": [
            {"name": "ticket_id", "type": "INT64", "mode": "REQUIRED"},
            {"name": "region", "type": "STRING", "mode": "REQUIRED", "max_length": 8},
            {"name": "summary", "type": "STRING", "mode": "REQUIRED"},
        ],
        "primary_keys": ["ticket_id"],
    }
    reference = [
        {
            "ticket_id": 1001,
            "region": "EMEA",
            "summary": "Customer reports the export job hangs at 90 percent for large tables.",
        },
        {
            "ticket_id": 1002,
            "region": "AMER",
            "summary": "User cannot reset password; the reset email never arrives.",
        },
        {
            "ticket_id": 1003,
            "region": "APAC",
            "summary": "Dashboard widgets render blank after the latest browser update.",
        },
    ]
    return GenerationContext(
        table_schema=TableSchema.model_validate(ddl),
        reference_rows=reference,
        reference_digest="wide-digest",
        pipeline_run_id="b2-wide-run",
    )


@pytest.fixture
def free_text_ctx() -> GenerationContext:
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.profiles"},
            "schema": [
                {"name": "user_id", "type": "INT64", "mode": "REQUIRED"},
                {"name": "region", "type": "STRING", "mode": "REQUIRED", "max_length": 8},
                {"name": "bio", "type": "STRING", "mode": "NULLABLE", "max_length": 500},
            ],
            "primary_keys": ["user_id"],
        }
    )
    rows = [
        {
            "user_id": i,
            "region": "EU",
            "bio": f"User number {i} enjoys long-form descriptive prose and writes a lot.",
        }
        for i in range(1, 13)
    ]
    return GenerationContext(
        table_schema=schema,
        reference_rows=rows,
        reference_digest="ft-digest",
        pipeline_run_id="b1-ft",
    )


# ---------------------------------------------------------------------------
# B.2 — FreeTextHook._generate_pool
# ---------------------------------------------------------------------------


def test_b2_fallback_emits_warning_milestone(caplog, wide_ctx):
    profiles = profile_table(wide_ctx.table_schema, wide_ctx.reference_rows)
    hook = FreeTextHook(_BoomClient())
    rng = np.random.default_rng(7)
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        pool = hook.sample(profiles["summary"], 5, GenerationConfig(seed=7), rng)
    assert pool  # exemplar fallback still returns values
    assert any(v is not None for v in pool)
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=freetext_llm_fallback" in text
    assert "error=RuntimeError" in text


# ---------------------------------------------------------------------------
# B.1 — B1RagEngine._infer_free_text_pool (invoked via setup()).
# ---------------------------------------------------------------------------


def test_b1_fallback_emits_warning_milestone(caplog, free_text_ctx):
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        engine.setup(_BoomClient(), free_text_ctx)
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=freetext_llm_fallback" in text
    assert "error=RuntimeError" in text
    # The pool still falls back to observed exemplars — not empty.
    assert engine._free_text_pools["bio"]
