"""Format-plausibility gate on LLM pool values (2026-07-25 10:52 E2E).

That run's CHG_MESS_CARR_ID pool accepted LLM hallucinations that were
"novel" but format-junk: an echo of the COLUMN NAME from the prompt
('CHG_MESS_CARR_67J8K'), an echo of the prompt's format-instruction
examples ('UUID-1a2b3c4d-…'), and low-entropy filler. The only acceptance
test was novelty (v not in observed). For identifier-ish columns (a
relaxed template exists), values must now also match an observed length
bucket, stay within the observed charset, and never contain the column
name. Prose columns (no template) skip the gate entirely.
"""

from __future__ import annotations

from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag.engine import B1RagEngine, _pool_llm_yield
from sdfb_core.engines.b1_rag.profile import ColumnKind, ColumnProfile
from sdfb_core.engines.base import GenerationContext
from sdfb_core.observability import parse_milestone

# Mixed-length identifier values (defeats the strict detector, relaxed
# templates exist): CHG + 6 digits (9 chars) and CHG + 13 digits (16 chars).
_ID_VALUES = tuple(
    [f"CHG{i:06d}" for i in range(40)] + [f"CHG{i:013d}" for i in range(40)]
)


def _id_profile() -> ColumnProfile:
    return ColumnProfile(
        name="CHG_MESS_CARR_ID",
        bq_type="STRING",
        kind=ColumnKind.FREE_TEXT,
        nullable=False,
        null_fraction=0.0,
        observed_values=_ID_VALUES,
        text_examples=_ID_VALUES[:2],
    )


def _prose_profile() -> ColumnProfile:
    vals = tuple(f"customer reported outage number {i}" for i in range(60))
    return ColumnProfile(
        name="notes",
        bq_type="STRING",
        kind=ColumnKind.FREE_TEXT,
        nullable=False,
        null_fraction=0.0,
        observed_values=vals,
        text_examples=vals[:2],
    )


class _HallucinatingClient:
    """Yields a fixed mix of in-format novel values and format junk."""

    def __init__(self):
        self.call_count = 0

    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        self.call_count += 1
        return [{"values": [
            "CHG900001",                        # in-format (9-char bucket)
            "CHG9000000000002",                 # in-format (16-char bucket)
            "UUID-1a2b3c4d-5e6f-7d8c",          # prompt-example echo
            "CHG_MESS_CARR_67J8K",              # column-name echo
            "CHG666666",                        # in-format (9-char bucket)
            "chg_mess_carr_id-77",              # column-name echo, lowercase
            "CHG12345678",                      # wrong length (11) -> reject
        ]} for _ in range(n)]


def test_gate_rejects_hallucinations_keeps_in_format():
    y = _pool_llm_yield(
        _HallucinatingClient(), "p", {}, _id_profile(), ["CHG000001"],
        target=64,
    )
    assert "CHG900001" in y.pool
    assert "CHG9000000000002" in y.pool
    assert "CHG666666" in y.pool
    for junk in ("UUID-1a2b3c4d-5e6f-7d8c", "CHG_MESS_CARR_67J8K",
                 "chg_mess_carr_id-77", "CHG12345678"):
        assert junk not in y.pool, junk
    assert y.format_rejected > 0


def test_gate_skips_prose_columns():
    class _ProseClient:
        def generate_json(self, prompt, json_schema, **kw):
            return [{"values": ["a fresh synthetic outage note entirely new"]}]

    y = _pool_llm_yield(_ProseClient(), "p", {}, _prose_profile(), [], target=8)
    assert "a fresh synthetic outage note entirely new" in y.pool
    assert y.format_rejected == 0


def test_format_rejected_surfaces_in_undersized_milestone(caplog):
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.t"},
            "schema": [
                {"name": "CHG_MESS_CARR_ID", "type": "STRING", "mode": "REQUIRED"}
            ],
        }
    )
    ctx = GenerationContext(
        table_schema=schema,
        reference_rows=[{"CHG_MESS_CARR_ID": v} for v in _ID_VALUES],
        pipeline_run_id="run-gate-1",
        num_rows=500,
    )
    engine = B1RagEngine()
    with caplog.at_level("WARNING"):
        engine.setup(_HallucinatingClient(), ctx)
    milestones = [
        m for m in (parse_milestone(r.getMessage()) for r in caplog.records) if m
    ]
    reported = [
        m for m in milestones
        if m["name"] in ("freetext_pool_undersized", "freetext_pool_stagnated")
        and "format_rejected" in m
    ]
    assert reported, "format_rejected must surface in pool-health milestones"
    assert any(int(m["format_rejected"]) > 0 for m in reported)


# --- pattern-guided decoding (opt-in; layer 2 of the hallucination fix) ----


def test_relaxed_shapes_pattern_matches_format_rejects_junk():
    import re

    from sdfb_core.engines.text_shapes import (
        build_relaxed_shapes,
        relaxed_shapes_pattern,
    )

    shapes = build_relaxed_shapes(list(_ID_VALUES))
    assert shapes is not None
    pattern = re.compile(relaxed_shapes_pattern(shapes))
    assert pattern.fullmatch("CHG900001")           # 9-char bucket
    assert pattern.fullmatch("CHG9000000000002")    # 16-char bucket
    assert not pattern.fullmatch("UUID-1a2b3c4d-5e6f-7d8c")
    assert not pattern.fullmatch("CHG_MESS_CARR_67J8K")
    assert not pattern.fullmatch("CHG12345678")     # wrong length


class _SchemaRecordingClient:
    def __init__(self):
        self.schemas: list[dict] = []

    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        self.schemas.append(json_schema)
        return [{"values": ["CHG900001"]} for _ in range(n)]


def _gate_ctx(**overrides) -> GenerationContext:
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.t"},
            "schema": [
                {"name": "CHG_MESS_CARR_ID", "type": "STRING", "mode": "REQUIRED"}
            ],
        }
    )
    defaults = dict(
        table_schema=schema,
        reference_rows=[{"CHG_MESS_CARR_ID": v} for v in _ID_VALUES],
        pipeline_run_id="run-pattern-1",
        num_rows=8,
    )
    defaults.update(overrides)
    return GenerationContext(**defaults)


def test_pattern_guidance_off_by_default():
    client = _SchemaRecordingClient()
    B1RagEngine().setup(client, _gate_ctx())
    assert client.schemas
    for s in client.schemas:
        assert "pattern" not in s["properties"]["values"]["items"]


def test_pattern_guidance_adds_items_pattern_when_enabled():
    client = _SchemaRecordingClient()
    B1RagEngine().setup(client, _gate_ctx(pool_pattern_guidance=True))
    assert client.schemas
    item_schema = client.schemas[0]["properties"]["values"]["items"]
    assert "pattern" in item_schema
    import re

    assert re.compile(item_schema["pattern"]).fullmatch("CHG900001")
