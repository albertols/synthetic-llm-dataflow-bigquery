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
from sdfb_core.engines import (
    FreeTextEmptyYieldError,
    GenerationConfig,
    GenerationContext,
)
from sdfb_core.engines.b1_rag import B1RagEngine, HashingEmbedder
from sdfb_core.engines.b2_library.fidelity import profile_table
from sdfb_core.engines.b2_library.freetext import FreeTextHook


class _BoomClient:
    """A `ModelClient` whose `generate_json` always raises."""

    def generate_json(self, *a, **k):
        raise RuntimeError("boom")


class _EmptyYieldClient:
    """A `ModelClient` whose `generate_json` succeeds but yields nothing.

    Models the 2026-07-15 E2E failure: vLLM answered HTTP 200 but every
    choice was dropped at JSON parse, so the call returns `[]` without
    raising — the second silent-memorization path.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def generate_json(self, *a, **k):
        self.calls.append(k)
        return []


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


# ---------------------------------------------------------------------------
# strict_freetext=True — real-vLLM runs must fail loudly, never fall back.
# ---------------------------------------------------------------------------


def test_b2_strict_reraises(wide_ctx):
    profiles = profile_table(wide_ctx.table_schema, wide_ctx.reference_rows)
    hook = FreeTextHook(_BoomClient(), strict=True)
    rng = np.random.default_rng(7)
    with pytest.raises(RuntimeError, match="boom"):
        hook.sample(profiles["summary"], 5, GenerationConfig(seed=7), rng)


def test_b1_strict_reraises(free_text_ctx):
    strict_ctx = free_text_ctx.model_copy(update={"strict_freetext": True})
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with pytest.raises(RuntimeError, match="boom"):
        engine.setup(_BoomClient(), strict_ctx)


# ---------------------------------------------------------------------------
# Empty yield — generate_json returns [] without raising (all choices were
# dropped at parse). Must be as loud as an exception: milestone in lax mode,
# FreeTextEmptyYieldError under strict_freetext.
# ---------------------------------------------------------------------------


def test_b1_empty_yield_emits_fallback_milestone(caplog, free_text_ctx):
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        engine.setup(_EmptyYieldClient(), free_text_ctx)
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=freetext_llm_fallback" in text
    assert "error=EmptyYield" in text
    # Exemplar fallback still fills the pool — the run degrades, not crashes.
    assert engine._free_text_pools["bio"]


def test_b1_strict_raises_on_empty_yield(free_text_ctx):
    strict_ctx = free_text_ctx.model_copy(update={"strict_freetext": True})
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with pytest.raises(FreeTextEmptyYieldError, match="bio"):
        engine.setup(_EmptyYieldClient(), strict_ctx)


def test_b2_empty_yield_emits_fallback_milestone(caplog, wide_ctx):
    profiles = profile_table(wide_ctx.table_schema, wide_ctx.reference_rows)
    hook = FreeTextHook(_EmptyYieldClient())
    rng = np.random.default_rng(7)
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        pool = hook.sample(profiles["summary"], 5, GenerationConfig(seed=7), rng)
    assert pool
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=freetext_llm_fallback" in text
    assert "error=EmptyYield" in text


def test_b2_strict_raises_on_empty_yield(wide_ctx):
    profiles = profile_table(wide_ctx.table_schema, wide_ctx.reference_rows)
    hook = FreeTextHook(_EmptyYieldClient(), strict=True)
    rng = np.random.default_rng(7)
    with pytest.raises(FreeTextEmptyYieldError, match="summary"):
        hook.sample(profiles["summary"], 5, GenerationConfig(seed=7), rng)


# ---------------------------------------------------------------------------
# All-copies yield — the LLM "succeeds" but every value is a verbatim
# exemplar copy. After the novelty filter that is an empty yield: same
# milestone, same strict behavior.
# ---------------------------------------------------------------------------


class _CopyingClient:
    """A `ModelClient` that only echoes observed reference values."""

    def __init__(self, copies: list[dict]):
        self._copies = copies

    def generate_json(self, *a, **k):
        return list(self._copies)


def test_b1_all_copy_yield_counts_as_empty(caplog, free_text_ctx):
    copies = [{"bio": r["bio"]} for r in free_text_ctx.reference_rows[:4]]
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        engine.setup(_CopyingClient(copies), free_text_ctx)
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=freetext_llm_fallback" in text
    assert "error=EmptyYield" in text


# ---------------------------------------------------------------------------
# Escalating-temperature retries — an empty NOVEL yield (all verbatim copies
# or all parse-drops) retries the pool call at higher temperatures before
# giving up. 2026-07-16 corp run: Qwen3-4B echoed the seed exemplars verbatim
# for BUSI_CONTR_KEY on every choice → strict kill after a 90-min setup.
# ---------------------------------------------------------------------------


class _CopyThenNovelClient:
    """Echoes observed values on the first call, novel values afterwards."""

    def __init__(self, copies: list[dict], novel: list[dict]):
        self._copies = copies
        self._novel = novel
        self.calls: list[dict] = []

    def generate_json(self, *a, **k):
        self.calls.append(k)
        if len(self.calls) == 1:
            return list(self._copies)
        return list(self._novel)


def test_b1_retries_with_escalating_temperature_on_all_copies(free_text_ctx):
    copies = [{"bio": r["bio"]} for r in free_text_ctx.reference_rows[:4]]
    client = _CopyThenNovelClient(copies, [{"bio": "A brand-new synthetic bio."}])
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(client, free_text_ctx)
    assert len(client.calls) == 2, "expected a retry after the all-copies yield"
    temps = [c.get("temperature") for c in client.calls]
    assert all(t is not None for t in temps)
    assert temps[1] > temps[0], f"retry must escalate temperature, got {temps}"
    assert "A brand-new synthetic bio." in engine._free_text_pools["bio"]


def test_b1_stops_retrying_once_pool_is_novel(free_text_ctx):
    client = _CopyThenNovelClient([], [])

    class _NovelFirstClient(_CopyThenNovelClient):
        def generate_json(self, *a, **k):
            self.calls.append(k)
            return [{"bio": "Immediately novel."}]

    client = _NovelFirstClient([], [])
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(client, free_text_ctx)
    assert len(client.calls) == 1


def test_b1_strict_all_copy_message_reports_counts(free_text_ctx):
    strict_ctx = free_text_ctx.model_copy(update={"strict_freetext": True})
    copies = [{"bio": r["bio"]} for r in free_text_ctx.reference_rows[:4]]
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    # 3 escalation attempts x 4 parsed-but-copied values each: the error must
    # say WHY the yield was unusable (copies, not parse failures) — the
    # 2026-07-16 run's "0 of 32 choices parsed" message misdiagnosed itself.
    with pytest.raises(
        FreeTextEmptyYieldError, match=r"parsed=12.*verbatim_copies=12"
    ):
        engine.setup(_CopyingClient(copies), strict_ctx)


def test_b1_strict_empty_yield_message_reports_zero_parsed(free_text_ctx):
    strict_ctx = free_text_ctx.model_copy(update={"strict_freetext": True})
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with pytest.raises(FreeTextEmptyYieldError, match=r"parsed=0"):
        engine.setup(_EmptyYieldClient(), strict_ctx)


def test_b1_fallback_milestone_reports_counts(caplog, free_text_ctx):
    copies = [{"bio": r["bio"]} for r in free_text_ctx.reference_rows[:4]]
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        engine.setup(_CopyingClient(copies), free_text_ctx)
    text = "\n".join(r.message for r in caplog.records)
    assert "parsed=12" in text
    assert "verbatim_copies=12" in text
    assert "attempts=3" in text


def test_b2_retries_with_escalating_temperature_on_all_copies(wide_ctx):
    profiles = profile_table(wide_ctx.table_schema, wide_ctx.reference_rows)
    exemplar = profiles["summary"].text_pool[0]
    client = _CopyThenNovelClient(
        [{"values": [exemplar]}],
        [{"values": ["A clearly novel ticket summary."]}],
    )
    hook = FreeTextHook(client)
    pool = hook._pool_for(profiles["summary"], GenerationConfig(seed=1))
    assert "A clearly novel ticket summary." in pool
    assert len(client.calls) == 2
    temps = [c.get("temperature") for c in client.calls]
    assert temps[1] > temps[0], f"retry must escalate temperature, got {temps}"


def test_b2_strict_all_copy_message_reports_counts(wide_ctx):
    profiles = profile_table(wide_ctx.table_schema, wide_ctx.reference_rows)
    exemplar = profiles["summary"].text_pool[0]
    hook = FreeTextHook(_CopyingClient([{"values": [exemplar]}]), strict=True)
    with pytest.raises(
        FreeTextEmptyYieldError, match=r"parsed=3.*verbatim_copies=3"
    ):
        hook._pool_for(profiles["summary"], GenerationConfig(seed=1))


def test_b2_pool_excludes_verbatim_copies(wide_ctx):
    profiles = profile_table(wide_ctx.table_schema, wide_ctx.reference_rows)
    exemplar = profiles["summary"].text_pool[0]
    client = _CopyingClient(
        [{"values": [exemplar, "A fresh, clearly novel ticket summary."]}]
    )
    hook = FreeTextHook(client)
    pool = hook._pool_for(profiles["summary"], GenerationConfig(seed=1))
    assert "A fresh, clearly novel ticket summary." in pool
    # The LLM pool is the "diverge" side of B.2's similarity blend — observed
    # values reach the output via the reference pool, never via the LLM pool.
    assert exemplar not in pool


# ---------------------------------------------------------------------------
# B.1 pool inference must not pin a request seed — a fixed seed with n>1
# collapses all n vLLM choices to a single completion (2026-07-15 run:
# identical choice lengths per request).
# ---------------------------------------------------------------------------


def test_b1_pool_inference_does_not_pin_seed(free_text_ctx):
    class _RecordingClient(_EmptyYieldClient):
        def generate_json(self, *a, **k):
            self.calls.append(k)
            return [{"bio": f"generated value {i}"} for i in range(3)]

    client = _RecordingClient()
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(client, free_text_ctx)
    assert client.calls, "expected a pool-inference LLM call for 'bio'"
    for call in client.calls:
        assert call.get("seed") is None


# ---------------------------------------------------------------------------
# B.1 setup phase milestones — embed / index / pool timings
# ---------------------------------------------------------------------------


def test_b1_setup_emits_phase_milestones(caplog, free_text_ctx):
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        engine.setup(_BoomClient(), free_text_ctx)
    text = "\n".join(r.message for r in caplog.records)
    assert "SDFB_MILESTONE name=b1_embed_done" in text
    # Trailing space delimiter — a loose "rows=12" substring would also
    # match "rows=120" and silently stop catching a wrong row count.
    assert "rows=12 " in text
    assert "SDFB_MILESTONE name=b1_index_built" in text
    assert "SDFB_MILESTONE name=b1_pools_built" in text
