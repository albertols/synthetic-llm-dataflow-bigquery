"""WS2 §4b.2: pool target = min(num_rows, column_distinct, 512), filled by
multiple bounded calls. Closes the 28-619x oversampling of the 2026-07-19
run (3 FREE_TEXT columns capped at 32 values over 1000 rows)."""

from __future__ import annotations

from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext
from sdfb_core.engines.b1_rag import B1RagEngine
from sdfb_core.engines.b1_rag.engine import (
    _FREE_TEXT_POOL_MAX,
    _POOL_VALUES_PER_CALL,
    _pool_llm_yield,
)
from sdfb_core.rag.embedding import HashingEmbedder


class _BatchClient:
    """Yields _POOL_VALUES_PER_CALL fresh values per call, like a healthy LLM."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, prompt, json_schema, **kw):
        base = self.calls * _POOL_VALUES_PER_CALL
        self.calls += 1
        return [
            {"values": [f"novel value {base + i}" for i in range(_POOL_VALUES_PER_CALL)]}
        ]


def _schema_and_rows(n_rows: int = 300):
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.notes"},
            "schema": [
                {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                {"name": "notes", "type": "STRING", "mode": "REQUIRED"},
            ],
            "primary_keys": ["id"],
        }
    )
    rows = [
        {"id": i, "notes": f"customer reported issue number {i} with details"}
        for i in range(n_rows)
    ]
    return schema, rows


def test_constants():
    assert _FREE_TEXT_POOL_MAX == 512
    assert _POOL_VALUES_PER_CALL == 32


def test_pool_scales_past_32_with_batched_calls():
    schema, rows = _schema_and_rows(300)
    client = _BatchClient()
    engine = B1RagEngine(embedder=HashingEmbedder(dim=32))
    ctx = GenerationContext(
        table_schema=schema,
        reference_rows=rows,
        reference_digest="d",
        pipeline_run_id="pool-scale",
        num_rows=200,
    )
    engine.setup(client, ctx)
    pool = engine._free_text_pools["notes"]
    # target = min(num_rows=200, distinct=300, 512) = 200
    assert len(pool) >= 200
    assert client.calls >= 200 // _POOL_VALUES_PER_CALL
    engine.teardown()


def test_pool_target_respects_column_distinct():
    schema, rows = _schema_and_rows(300)
    # 60 distinct long notes values → target = min(num_rows=500, 60, 512) = 60.
    # (If b1's profiler routes this fixture to CATEGORICAL instead of
    # FREE_TEXT, raise the distinct count / mean length until it profiles
    # FREE_TEXT — the assertions below must run unconditionally.)
    for i, r in enumerate(rows):
        r["notes"] = f"repeating customer note body number {i % 60} with extended details"
    client = _BatchClient()
    engine = B1RagEngine(embedder=HashingEmbedder(dim=32))
    ctx = GenerationContext(
        table_schema=schema,
        reference_rows=rows,
        reference_digest="d",
        pipeline_run_id="pool-cap",
        num_rows=500,
    )
    engine.setup(client, ctx)
    pool = engine._free_text_pools["notes"]
    assert len(pool) <= 60  # distinct bound, not 512
    assert len(pool) >= _POOL_VALUES_PER_CALL  # scaled past the old 32 cap
    engine.teardown()


def test_pool_llm_yield_stops_at_target_and_bounds_calls():
    class _EmptyClient:
        def __init__(self) -> None:
            self.calls = 0

        def generate_json(self, prompt, json_schema, **kw):
            self.calls += 1
            return [{"values": []}]

    from sdfb_core.engines.b1_rag.profile import ColumnKind, ColumnProfile

    prof = ColumnProfile(
        name="notes",
        bq_type="STRING",
        kind=ColumnKind.FREE_TEXT,
        nullable=False,
        null_fraction=0.0,
    )
    client = _EmptyClient()
    y = _pool_llm_yield(client, "p", {}, prof, [], target=512)
    assert y.pool == []
    assert client.calls == 2 * -(-512 // _POOL_VALUES_PER_CALL)  # bounded, never infinite
