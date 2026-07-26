"""The pool-build branch (WS5 T7).

Runs the ladder once, in its own DAG branch, and writes rows that a later
run reads back instead of re-inferring.
"""

from __future__ import annotations

import apache_beam as beam
import pytest
from apache_beam.testing.test_pipeline import TestPipeline
from apache_beam.testing.util import assert_that, equal_to
from sdfb_beam.dofns.pools import BuildFreeTextPoolsDoFn
from sdfb_beam.pools.store import row_to_pool
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag.engine import clear_free_text_pool_cache
from sdfb_core.engines.base import GenerationContext
from sdfb_core.pools import FreeTextPool, InMemoryFreeTextPoolStore

_COLS = ["col_a", "col_b"]
_DIGEST = "digest-branch"
_MODEL = "gs://m/qwen3/v1"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_free_text_pool_cache()
    yield
    clear_free_text_pool_cache()


def _ctx(**overrides) -> GenerationContext:
    defaults = dict(
        table_schema=TableSchema.model_validate(
            {
                "table_info": {"table_id": "demo.t"},
                "schema": [
                    {"name": c, "type": "STRING", "mode": "REQUIRED"} for c in _COLS
                ],
            }
        ),
        reference_rows=[
            {c: f"{c} reference prose value number {i}" for c in _COLS}
            for i in range(60)
        ],
        reference_digest=_DIGEST,
        model_uri=_MODEL,
        pipeline_run_id="run-branch",
        num_rows=40,
    )
    defaults.update(overrides)
    return GenerationContext(**defaults)


class _StubClient:
    def __init__(self):
        self.call_count = 0

    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        self.call_count += 1
        col = next((c for c in _COLS if f"'{c}'" in prompt), "unknown")
        return [
            {"values": [f"{col}-gen-{choice}-{i}" for i in range(32)]}
            for choice in range(n)
        ]


def test_branch_emits_one_row_per_free_text_column():
    with TestPipeline() as p:
        columns = (
            p
            | beam.Create([None])
            | beam.ParDo(BuildFreeTextPoolsDoFn("b1_rag", _StubClient(), _ctx()))
            | beam.Map(lambda r: r["column"])
        )
        assert_that(columns, equal_to(_COLS))


def test_emitted_rows_carry_the_run_identity():
    dofn = BuildFreeTextPoolsDoFn("b1_rag", _StubClient(), _ctx())
    dofn.setup()
    rows = list(dofn.process(None))
    assert rows
    for row in rows:
        assert row["reference_digest"] == _DIGEST
        assert row["model_uri"] == _MODEL
        assert row["values"], "an empty pool must never be written"


def test_emitted_rows_round_trip_back_into_pools():
    """What the branch writes is exactly what the read path consumes."""
    dofn = BuildFreeTextPoolsDoFn("b1_rag", _StubClient(), _ctx())
    dofn.setup()
    pools = [row_to_pool(r) for r in dofn.process(None)]
    assert all(isinstance(p, FreeTextPool) for p in pools)
    store = InMemoryFreeTextPoolStore(pools)
    assert store.exists(_DIGEST, _MODEL) is True
    assert {p.column for p in store.fetch(_DIGEST, _MODEL)} == set(_COLS)


def test_branch_ignores_an_attached_store_so_it_cannot_short_circuit_itself():
    """If the branch read its own output it would emit nothing on a re-run
    and the artifact would never be refreshed."""
    prefilled = InMemoryFreeTextPoolStore(
        [
            FreeTextPool(
                reference_digest=_DIGEST,
                model_uri=_MODEL,
                column=c,
                target=2,
                values=("stale-1", "stale-2"),
            )
            for c in _COLS
        ]
    )
    client = _StubClient()
    dofn = BuildFreeTextPoolsDoFn(
        "b1_rag", client, _ctx(pool_store=prefilled, freetext_pools_table="p.d.t")
    )
    dofn.setup()
    rows = list(dofn.process(None))
    assert client.call_count > 0, "branch must build, not read"
    assert all("stale" not in v for r in rows for v in r["values"])


def test_dag_gains_the_branch_only_when_a_sink_is_passed():
    """None sink => DAG unchanged, exactly like rag_chunks (WS5 §2)."""
    from sdfb_beam.pipeline import PipelineConfig, build_pipeline

    def _labels(freetext_pools_sink):
        cfg = PipelineConfig(
            table_schema=_ctx().table_schema,
            engine_name="b1_rag",
            model_client=_StubClient(),
            num_rows=4,
            batch_size=4,
            run_id="r1",
            model_uri=_MODEL,
            freetext_pools_table="p.d.freetext_pools",
        )
        with TestPipeline() as p:
            build_pipeline(
                p,
                reference_rows=_ctx().reference_rows,
                config=cfg,
                landing_sink=beam.Map(lambda x: x),
                dlq_sink=beam.Map(lambda x: x),
                freetext_pools_sink=freetext_pools_sink,
            )
            return {str(t.full_label) for t in p.transforms_stack[0].parts}

    without = _labels(None)
    assert not any("BuildFreeTextPools" in lbl for lbl in without)

    with_sink = _labels(beam.Map(lambda r: r))
    assert any("BuildFreeTextPools" in lbl for lbl in with_sink)
    assert any("WriteFreeTextPools" in lbl for lbl in with_sink)
