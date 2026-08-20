"""Identity-like INT64 columns reject the full source domain (wave 4).

2026-08-20 A_TABLE R1: COL_009 (INT64, 34,622 source-distinct account
numbers) landed `copy_ratio_substantive = 0.52` — the inverse-CDF draw
interpolates between observed order statistics, and in a dense integer
band the rounded interpolant IS another real (rare) account number. The
STRING identifier route already rejects the full domain through the
ADR 0023 `source_value_store` seam; integral NUMERIC columns above the
memorization rule's cardinality floor (source_distinct > 100) now use the
same seam:

  - draws whose rounded value hits a rare source value redraw, then nudge
    to the nearest non-source integer (marginal moves by ±few units);
  - values the SAMPLE saw repeatedly (multi-knot: sample frequency >= 2)
    are enum mass under the probe's k-anonymity floor and stay exact —
    frequent codes keep their head fidelity.
"""

from __future__ import annotations

import logging

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext
from sdfb_core.engines.b1_rag import B1RagEngine, HashingEmbedder
from sdfb_core.engines.base import GenerationConfig

# Domain: every EVEN number in [1000, 3000) — 1000 distinct values with an
# odd-integer gap beside each, so a nudge always has somewhere to go.
_DOMAIN = frozenset(str(v) for v in range(1000, 3000, 2))
# Sample: 250 singleton evens plus one heavily repeated enum value.
_ENUM_VALUE = 2000
_SAMPLE_VALUES = [1000 + 2 * i for i in range(250)] + [_ENUM_VALUE] * 50


class _FakeSourceValueStore:
    def __init__(self, values_by_column):
        self.values_by_column = values_by_column
        self.calls: list[str] = []

    def fetch_distinct(self, column):
        self.calls.append(column)
        return self.values_by_column.get(column)


class _NoLLMClient:
    def generate_json(self, *, n=1, **kwargs):  # pragma: no cover - unused
        return [{"values": []} for _ in range(n)]


def _ctx(store=None) -> GenerationContext:
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "demo.accounts"},
            "schema": [
                {"name": "acct", "type": "INT64", "mode": "REQUIRED"},
                {"name": "code", "type": "INT64", "mode": "REQUIRED"},
            ],
        }
    )
    rows = [
        {"acct": v, "code": 100 + (i % 30)}
        for i, v in enumerate(_SAMPLE_VALUES)
    ]
    update = {}
    if store is not None:
        update["source_value_store"] = store
    return GenerationContext(
        table_schema=schema,
        reference_rows=rows,
        reference_digest="acct-digest",
        pipeline_run_id="acct-run",
    ).model_copy(update=update)


def _drawn(engine: B1RagEngine, n: int = 400) -> list[int]:
    records = list(engine.generate_batch(n, GenerationConfig(seed=3)))
    return [r.acct for r in records]  # type: ignore[attr-defined]


def test_numeric_draws_reject_rare_source_values(caplog) -> None:
    store = _FakeSourceValueStore({"acct": _DOMAIN})
    engine = B1RagEngine(embedder=HashingEmbedder())
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        engine.setup(_NoLLMClient(), _ctx(store))
    assert "acct" in store.calls
    text = "\n".join(r.message for r in caplog.records)
    assert "name=numeric_source_filter" in text
    drawn = _drawn(engine)
    assert drawn
    # Rare (singleton-knot) source values are scrubbed; the multi-knot enum
    # value is k-anonymous head mass and stays exact.
    collided = {v for v in drawn if str(v) in _DOMAIN}
    assert collided <= {_ENUM_VALUE}, sorted(collided)[:10]
    assert _ENUM_VALUE in collided  # head fidelity preserved
    # Marginal stays in the observed band (nudges are +/- a few units).
    assert min(drawn) >= 990 and max(drawn) <= 2510
    engine.teardown()


def test_small_domain_numeric_columns_skip_the_fetch() -> None:
    # `code` has 30 distinct values — below the memorization rule's
    # source_distinct > 100 floor. Collisions there are enum reuse, not a
    # privacy signal; no domain query is spent on it.
    store = _FakeSourceValueStore({"acct": _DOMAIN, "code": frozenset()})
    engine = B1RagEngine(embedder=HashingEmbedder())
    engine.setup(_NoLLMClient(), _ctx(store))
    assert "code" not in store.calls
    engine.teardown()


def test_without_store_numeric_behavior_is_unchanged() -> None:
    engine = B1RagEngine(embedder=HashingEmbedder())
    engine.setup(_NoLLMClient(), _ctx())
    drawn = _drawn(engine)
    assert drawn
    # Sample-bound inverse-CDF: in-range draws, no crash, no filtering.
    assert min(drawn) >= 1000 and max(drawn) <= 2498
    engine.teardown()


@pytest.mark.parametrize("n", [64])
def test_rejection_is_seed_reproducible(n: int) -> None:
    store = _FakeSourceValueStore({"acct": _DOMAIN})
    a = B1RagEngine(embedder=HashingEmbedder())
    a.setup(_NoLLMClient(), _ctx(store))
    first = [r.acct for r in a.generate_batch(n, GenerationConfig(seed=11))]  # type: ignore[attr-defined]
    a.teardown()
    b = B1RagEngine(embedder=HashingEmbedder())
    b.setup(_NoLLMClient(), _ctx(store))
    second = [r.acct for r in b.generate_batch(n, GenerationConfig(seed=11))]  # type: ignore[attr-defined]
    b.teardown()
    assert first == second
