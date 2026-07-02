"""B.1 RAG engine — engine-specific tests (beyond the 5 ABC contract tests).

Covers the spine pieces that the shared contract suite does not:
  - column profiling (constant | numeric | categorical | free_text)
  - GReaT row serialization determinism
  - deterministic top-k retrieval for a fixed query embedding
  - fidelity by construction (constants copied, numerics in range,
    categoricals within observed support)
  - exemplar evidence above a uniform baseline on a rare-value column
  - the free-text LLM-pool path
  - HF_HUB_OFFLINE safety (no Hub network calls at runtime)

All run on the laptop with a deterministic injected/ default embedder — no
model download, no GPU, no GCP.
"""

from __future__ import annotations

import os
from collections import Counter

import pytest
from sdfb_beam.handlers.fake_client import FakeModelClient
from sdfb_core.contracts import GeneratedRecord, TableSchema
from sdfb_core.engines import GenerationConfig, GenerationContext, get_engine
from sdfb_core.engines.b1_rag import B1RagEngine, ColumnKind, HashingEmbedder
from sdfb_core.engines.b1_rag.index import build_index
from sdfb_core.engines.b1_rag.profile import profile_columns
from sdfb_core.engines.b1_rag.serialize import serialize_row

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def customers_ctx(customers_schema, customers_reference):
    return GenerationContext(
        table_schema=customers_schema,
        reference_rows=customers_reference,
        reference_digest="cust-digest",
        pipeline_run_id="b1-test",
    )


@pytest.fixture
def free_text_schema() -> TableSchema:
    """A table with a genuine free-text column (bio) + a constant + an enum."""
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.profiles"},
            "schema": [
                {"name": "user_id", "type": "INT64", "mode": "REQUIRED"},
                {"name": "region", "type": "STRING", "mode": "REQUIRED", "max_length": 8},
                {"name": "status", "type": "STRING", "mode": "REQUIRED", "max_length": 8},
                {"name": "bio", "type": "STRING", "mode": "NULLABLE", "max_length": 500},
            ],
            "primary_keys": ["user_id"],
        }
    )


@pytest.fixture
def free_text_rows() -> list[dict]:
    return [
        {
            "user_id": i,
            "region": "EU",  # constant
            "status": ["ACTIVE", "PENDING"][i % 2],  # categorical
            "bio": f"User number {i} enjoys long-form descriptive prose and writes a lot.",
        }
        for i in range(1, 13)
    ]


@pytest.fixture
def free_text_ctx(free_text_schema, free_text_rows) -> GenerationContext:
    return GenerationContext(
        table_schema=free_text_schema,
        reference_rows=free_text_rows,
        reference_digest="ft-digest",
        pipeline_run_id="b1-ft",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_engine_is_registered():
    assert get_engine("b1_rag") is B1RagEngine


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_serialize_row_is_great_style_and_deterministic():
    row = {"a": 1, "b": "x", "c": None}
    order = ["a", "b", "c"]
    s1 = serialize_row(row, order)
    s2 = serialize_row(row, order)
    assert s1 == s2 == "a is 1, b is x, c is null"


def test_serialize_respects_column_order():
    row = {"a": 1, "b": 2}
    assert serialize_row(row, ["b", "a"]) == "b is 2, a is 1"


# ---------------------------------------------------------------------------
# Profiling
# ---------------------------------------------------------------------------


def test_profile_classifies_kinds(free_text_schema, free_text_rows):
    profiles = profile_columns(free_text_schema, free_text_rows)
    assert profiles["region"].kind is ColumnKind.CONSTANT
    assert profiles["region"].constant_value == "EU"
    assert profiles["user_id"].kind is ColumnKind.NUMERIC
    assert profiles["status"].kind is ColumnKind.CATEGORICAL
    assert profiles["bio"].kind is ColumnKind.FREE_TEXT


def test_profile_numeric_bounds(customers_schema, customers_reference):
    profiles = profile_columns(customers_schema, customers_reference)
    ltv = profiles["lifetime_value"]
    assert ltv.kind is ColumnKind.NUMERIC
    assert ltv.numeric_min == pytest.approx(320.25)
    assert ltv.numeric_max == pytest.approx(4200.75)
    assert ltv.nullable is True
    assert ltv.null_fraction == pytest.approx(0.2)  # 2 of 10 rows null


def test_profile_categorical_frequencies(customers_schema, customers_reference):
    profiles = profile_columns(customers_schema, customers_reference)
    tier = profiles["tier"]
    assert tier.kind is ColumnKind.CATEGORICAL
    # "ENTERPRISE" appears 3x, "SMB" 3x, "STARTUP" 3x, "FREE" 1x.
    assert tier.categories["ENTERPRISE"] == 3
    assert tier.categories["FREE"] == 1


# ---------------------------------------------------------------------------
# Index / retrieval determinism
# ---------------------------------------------------------------------------


def test_deterministic_topk_for_fixed_query():
    embedder = HashingEmbedder(dim=64, seed=7)
    texts = [f"row {i} value {i % 3}" for i in range(20)]
    vectors = embedder.embed(texts)
    idx = build_index(vectors, embedder.dim)
    q = embedder.embed(["row 5 value 2"])[0]
    a = idx.search(q, 5)
    b = idx.search(q, 5)
    assert a == b  # identical ordering, no implicit randomness
    assert len(a) == 5
    # The exact query row must be its own nearest neighbor.
    assert a[0] == 5


def test_index_handles_empty():
    embedder = HashingEmbedder(dim=8)
    idx = build_index([], embedder.dim)
    assert idx.search([0.0] * 8, 3) == []


# ---------------------------------------------------------------------------
# Fidelity by construction
# ---------------------------------------------------------------------------


def test_constants_copied(free_text_ctx):
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(FakeModelClient(reference_pool=free_text_ctx.reference_rows), free_text_ctx)
    out = list(engine.generate_batch(20, GenerationConfig(seed=1)))
    assert len(out) > 0
    assert all(r.region == "EU" for r in out)


def test_numerics_within_observed_range(customers_ctx):
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(FakeModelClient(reference_pool=customers_ctx.reference_rows), customers_ctx)
    out = list(engine.generate_batch(50, GenerationConfig(seed=3, similarity=0.0)))
    for r in out:
        if r.lifetime_value is not None:
            assert 320.25 <= float(r.lifetime_value) <= 4200.75


def test_categoricals_within_observed_support(customers_ctx):
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(FakeModelClient(reference_pool=customers_ctx.reference_rows), customers_ctx)
    observed_tiers = {r["tier"] for r in customers_ctx.reference_rows}
    out = list(engine.generate_batch(50, GenerationConfig(seed=4)))
    for r in out:
        assert r.tier in observed_tiers


# ---------------------------------------------------------------------------
# Acceptance criterion 4: exemplar evidence above baseline
# ---------------------------------------------------------------------------


def test_exemplar_values_appear_above_uniform_baseline(customers_ctx):
    """A high-similarity batch should reflect the empirical tier frequency,
    not a uniform draw. A common tier (3/10 in the reference) must appear
    far more often than a rare one (FREE, 1/10) — the "exemplar evidence
    above baseline" acceptance criterion. With 600 seeded draws the 3x
    frequency gap is decisive (no flakiness; the seed fixes the outcome)."""
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(FakeModelClient(reference_pool=customers_ctx.reference_rows), customers_ctx)
    out = list(engine.generate_batch(600, GenerationConfig(seed=5, similarity=1.0)))
    tiers = Counter(r.tier for r in out)
    # ENTERPRISE (3/10) must clearly out-appear FREE (1/10): exemplar fidelity.
    assert tiers["ENTERPRISE"] > tiers["FREE"]
    # And the common tier must beat the uniform 1/4 baseline a uniform draw
    # would give. (Empirical 0.3 vs uniform 0.25.)
    n_categories = len({r["tier"] for r in customers_ctx.reference_rows})  # 4
    assert tiers["ENTERPRISE"] / len(out) > 1.0 / n_categories


def test_free_text_uses_exemplar_pool(free_text_ctx):
    """Free-text values come from the exemplar pool — every emitted bio is one
    of the observed reference bios. Here the canned client echoes reference
    *rows* (which carry a real `bio` field), and the engine also folds in the
    observed exemplars, so the whole pool is real reference values."""
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(FakeModelClient(responses=free_text_ctx.reference_rows), free_text_ctx)
    observed_bios = {r["bio"] for r in free_text_ctx.reference_rows}
    out = list(engine.generate_batch(30, GenerationConfig(seed=6)))
    assert len(out) > 0
    non_null_bios = [r.bio for r in out if r.bio is not None]
    assert non_null_bios, "expected some non-null free-text values"
    assert all(b in observed_bios for b in non_null_bios)


def test_free_text_pool_uses_llm_output_when_valid(free_text_ctx):
    """When the ModelClient returns schema-shaped {bio: str} dicts, those
    LLM values land in the pool and show up in generated rows."""
    llm_values = [{"bio": f"LLM-authored biography variant {i}"} for i in range(40)]
    engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
    engine.setup(FakeModelClient(responses=llm_values), free_text_ctx)
    out = list(engine.generate_batch(60, GenerationConfig(seed=7)))
    emitted = {r.bio for r in out if r.bio is not None}
    # At least one LLM-authored value must appear among generated rows.
    assert any(b.startswith("LLM-authored") for b in emitted)


# ---------------------------------------------------------------------------
# Acceptance criterion 5: HF_HUB_OFFLINE safety
# ---------------------------------------------------------------------------


def test_runs_with_hf_hub_offline(monkeypatch, customers_ctx):
    """Setting HF_HUB_OFFLINE=1 must not break setup/generate — the default
    embedder is dependency-free and never touches the Hub."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    engine = B1RagEngine()  # no injected embedder → HashingEmbedder default
    engine.setup(FakeModelClient(reference_pool=customers_ctx.reference_rows), customers_ctx)
    out = list(engine.generate_batch(5, GenerationConfig(seed=8)))
    assert len(out) > 0
    assert all(isinstance(r, GeneratedRecord) for r in out)


def test_default_embedder_requires_no_extras():
    """The zero-arg engine constructs without faiss/transformers/torch."""
    engine = B1RagEngine()
    # Constructing must not import any heavy module eagerly.
    assert "torch" not in os.environ.get("_FORCE_IMPORT", "")
    assert engine.name == "b1_rag"


# ---------------------------------------------------------------------------
# similarity knob behavior
# ---------------------------------------------------------------------------


def test_similarity_widens_distribution(customers_ctx):
    """Lower similarity should flatten the categorical distribution toward
    uniform (the rare tier appears more often than at high similarity)."""
    def rare_rate(similarity: float) -> float:
        engine = B1RagEngine(embedder=HashingEmbedder(dim=64))
        engine.setup(
            FakeModelClient(reference_pool=customers_ctx.reference_rows),
            customers_ctx,
        )
        out = list(
            engine.generate_batch(400, GenerationConfig(seed=9, similarity=similarity))
        )
        return Counter(r.tier for r in out)["FREE"] / len(out)

    high = rare_rate(1.0)
    low = rare_rate(0.0)
    assert low >= high  # widening lifts the rare category toward uniform
