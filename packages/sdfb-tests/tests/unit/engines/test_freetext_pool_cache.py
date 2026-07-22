"""Pool-cache key regression tests (2026-07-20 b2 E2E BLOCKER).

Production defect: ``FreeTextHook._pool_for`` cached the bounded value pool
keyed ``(profile.name, cfg.seed, round(cfg.similarity, 4))``, but
``GenerateRecordsDoFn.process`` derives a fresh ``cfg.seed`` per batch
(``derive_batch_seed(run_id, batch_id)`` — deliberate anti-replay design).
The cache therefore never hit across batches: every one of ~63 batches in
the 2026-07-20 run rebuilt every FREE_TEXT column's pool via a fresh LLM
call (63x cost), and under ``strict_freetext`` each rebuild was a fresh
chance for ``FreeTextEmptyYieldError`` to kill the whole batch (~60/63
batches failed, job FAILED).

The fix drops ``seed`` from the cache key: the pool is built genuinely once
per worker per ``(column, similarity)`` (the FASTGEN O(1) guarantee).
Batch-to-batch value diversity comes from the per-batch-seeded
with-replacement *draw* in ``sample()`` (the ``rng`` argument), not from
rebuilding the pool.
"""

from __future__ import annotations

import numpy as np
import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b2_library.fidelity import profile_table
from sdfb_core.engines.b2_library.freetext import FreeTextHook
from sdfb_core.engines.base import GenerationConfig


@pytest.fixture
def wide_ctx_schema() -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.support_tickets"},
            "schema": [
                {"name": "ticket_id", "type": "INT64", "mode": "REQUIRED"},
                {"name": "region", "type": "STRING", "mode": "REQUIRED", "max_length": 8},
                {"name": "summary", "type": "STRING", "mode": "REQUIRED"},
            ],
            "primary_keys": ["ticket_id"],
        }
    )


@pytest.fixture
def wide_reference() -> list[dict]:
    return [
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


class _CountingClient:
    """Fake ``ModelClient`` that counts pool-build calls and returns a fresh
    batch of distinct novel values each call, so successive builds (if the
    cache wrongly misses) are distinguishable from a single cached build."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, *a, **k):
        self.calls += 1
        return [{"values": [f"novel-call{self.calls}-{i}" for i in range(32)]}]


class _RaiseThenSucceedClient:
    """Raises on the first pool-build call, succeeds on the second — models
    a transient LLM failure that must NOT poison the cache."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, *a, **k):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient boom")
        return [{"values": [f"novel-{i}" for i in range(32)]}]


def test_pool_built_once_across_batch_seeds(wide_ctx_schema, wide_reference):
    """Two GenerationConfigs identical except for cfg.seed (mirroring two
    Beam batches with derive_batch_seed-derived seeds) must share ONE pool
    build — the E2E defect was a fresh LLM call per batch."""
    profiles = profile_table(wide_ctx_schema, wide_reference)
    client = _CountingClient()
    hook = FreeTextHook(client)

    # similarity=0.0 puts all sampling mass on the LLM-generated pool (see
    # `_blend_pools`), so draws are attributable to a specific pool build.
    cfg1 = GenerationConfig(seed=1, similarity=0.0)
    cfg2 = GenerationConfig(seed=2, similarity=0.0)
    rng1 = np.random.default_rng(1)
    rng2 = np.random.default_rng(2)

    out1 = hook.sample(profiles["summary"], 5, cfg1, rng1)
    out2 = hook.sample(profiles["summary"], 5, cfg2, rng2)

    assert client.calls == 1, "pool must be built once, not once per batch seed"
    assert out1 and out2

    # Both draws must come from the SAME underlying pool (all "novel-call1-*").
    def _from_pool(values):
        return all(v is None or v.startswith("novel-call1-") for v in values)

    assert _from_pool(out1)
    assert _from_pool(out2)


def test_draws_differ_across_batches_from_same_pool(wide_ctx_schema, wide_reference):
    """Diversity comes from the per-batch-seeded draw, not from rebuilding
    the pool: different rng seeds over the same cached pool must yield
    different sampled value lists."""
    profiles = profile_table(wide_ctx_schema, wide_reference)
    client = _CountingClient()
    hook = FreeTextHook(client)

    cfg = GenerationConfig(seed=1)
    out1 = hook.sample(profiles["summary"], 20, cfg, np.random.default_rng(1))
    out2 = hook.sample(profiles["summary"], 20, cfg, np.random.default_rng(2))

    assert client.calls == 1, "second draw must reuse the cached pool"
    assert out1 != out2, "different rng draws over the same pool should differ"


def test_failed_strict_build_not_cached_retries_next_batch(
    wide_ctx_schema, wide_reference
):
    """A strict-mode pool build that fails (exception) must NOT populate the
    cache — the next batch's call retries the build rather than reusing a
    poisoned/absent entry forever."""
    profiles = profile_table(wide_ctx_schema, wide_reference)
    client = _RaiseThenSucceedClient()
    hook = FreeTextHook(client, strict=True)

    cfg = GenerationConfig(seed=1)
    with pytest.raises(RuntimeError, match="transient boom"):
        hook.sample(profiles["summary"], 5, cfg, np.random.default_rng(1))

    # Second batch (different seed) retries and succeeds.
    out = hook.sample(profiles["summary"], 5, GenerationConfig(seed=2), np.random.default_rng(2))
    assert out
    assert client.calls == 2, "exactly two pool-build calls: failed + retried"
