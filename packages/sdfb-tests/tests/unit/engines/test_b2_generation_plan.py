"""B.2 mirror of the `generation_plan` milestone (2026-07-29).

Same one-line-per-run contract as b1_rag's: every column mapped to its
generation strategy. B.2 differences: the bulk sampler is the fitted
statistical backend (reported via `backend=`), and free-text pools build
lazily per batch, so there is no `pool_sources` field at setup time.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import get_engine
from sdfb_core.engines.b2_library import engine as b2_engine_mod
from sdfb_core.engines.base import GenerationContext
from sdfb_core.engines.generation_plan import clear_generation_plan_log

_DIGEST = "digest-b2-plan"
_MODEL = "gs://m/qwen3/v1"


@pytest.fixture(autouse=True)
def _fresh_state():
    clear_generation_plan_log()
    yield
    clear_generation_plan_log()


_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "demo.b2_plan_t"},
        "schema": [
            {"name": "konst", "type": "STRING", "mode": "REQUIRED"},
            {"name": "code", "type": "STRING", "mode": "REQUIRED"},
            {"name": "amount", "type": "INTEGER", "mode": "REQUIRED"},
            {"name": "event_dt", "type": "DATE", "mode": "REQUIRED"},
            {"name": "ident", "type": "STRING", "mode": "REQUIRED"},
            {"name": "notes", "type": "STRING", "mode": "REQUIRED"},
        ],
    }
)


def _rows(n: int = 60) -> list[dict]:
    return [
        {
            "konst": "FIXED",
            "code": ["A", "B", "C"][i % 3],
            "amount": i * 7,
            "event_dt": date(2024, i % 12 + 1, i % 28 + 1),
            "ident": f"ID-{i:05d}",
            "notes": f"reference prose value number {i} long enough to be text",
        }
        for i in range(n)
    ]


def _ctx() -> GenerationContext:
    return GenerationContext(
        table_schema=_SCHEMA,
        reference_rows=_rows(),
        reference_digest=_DIGEST,
        model_uri=_MODEL,
        pipeline_run_id="run-b2-plan",
        num_rows=40,
    )


class _StubClient:
    def generate_json(self, prompt, json_schema, *, max_tokens=2048,
                      temperature=0.7, n=1, seed=None, top_p=None, top_k=None):
        return [{"values": [f"gen-{i}" for i in range(32)]}]


def _capture_plans(monkeypatch):
    captured: list[dict] = []
    real = b2_engine_mod.log_milestone

    def _spy(name, **kwargs):
        if name == "generation_plan":
            captured.append(kwargs)
        return real(name, **kwargs)

    monkeypatch.setattr(b2_engine_mod, "log_milestone", _spy)
    return captured


def test_b2_setup_logs_one_generation_plan(monkeypatch):
    plans = _capture_plans(monkeypatch)
    engine = get_engine("b2_library")(use_sdgx=False)
    engine.setup(_StubClient(), _ctx())

    assert len(plans) == 1
    kwargs = plans[0]
    assert kwargs["engine"] == "b2_library"
    assert kwargs["table"] == _SCHEMA.fqn
    assert kwargs["backend"] == "empirical"
    plan = json.loads(kwargs["plan"])
    assert plan == {
        "categorical": ["code"],
        "constant": ["konst"],
        "freetext_llm_pool": ["notes"],
        "numeric": ["amount"],
        "shaped_identifier": ["ident"],
        "temporal": ["event_dt"],
    }


def test_b2_plan_logged_once_per_digest(monkeypatch):
    plans = _capture_plans(monkeypatch)
    get_engine("b2_library")(use_sdgx=False).setup(_StubClient(), _ctx())
    get_engine("b2_library")(use_sdgx=False).setup(_StubClient(), _ctx())
    assert len(plans) == 1


def test_b1_and_b2_plans_do_not_dedupe_each_other(monkeypatch):
    """Same digest + table through BOTH engines must yield BOTH plans —
    the once-guard is keyed per engine."""
    from sdfb_core.engines.b1_rag import engine as b1_engine_mod
    from sdfb_core.engines.b1_rag.engine import clear_free_text_pool_cache

    clear_free_text_pool_cache()
    b2_plans = _capture_plans(monkeypatch)
    b1_plans: list[dict] = []
    real_b1 = b1_engine_mod.log_milestone

    def _b1_spy(name, **kwargs):
        if name == "generation_plan":
            b1_plans.append(kwargs)
        return real_b1(name, **kwargs)

    monkeypatch.setattr(b1_engine_mod, "log_milestone", _b1_spy)

    get_engine("b2_library")(use_sdgx=False).setup(_StubClient(), _ctx())
    get_engine("b1_rag")().setup(_StubClient(), _ctx())
    clear_free_text_pool_cache()
    assert len(b2_plans) == 1
    assert len(b1_plans) == 1


def test_b2_generate_for_keys_matches_the_b1_contract():
    """ADR 0036: both engines are driven the same way."""
    from sdfb_core.contracts import TableSchema
    from sdfb_core.engines import GenerationConfig, GenerationContext, get_engine

    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.src.child_t"},
         "schema": [
             {"name": "PID", "type": "STRING", "mode": "REQUIRED"},
             {"name": "CAT", "type": "STRING", "mode": "REQUIRED"},
             {"name": "AMT", "type": "INT64", "mode": "REQUIRED"},
         ]}
    )
    rows = [{"PID": f"P{i:04d}", "CAT": "abc"[i % 3], "AMT": i} for i in range(60)]
    ctx = GenerationContext(
        table_schema=schema, reference_rows=rows, reference_digest="b2-fanout",
        pipeline_run_id="b2-run", pk_columns=["PID", "CAT"],
        fanout={"driving_cols": ["PID"], "histogram": {"1": 1, "3": 1},
                "cells": {"cols": ["CAT"], "rows": [["a"], ["b"], ["c"]], "counts": [1, 1, 1]},
                "exact_cells": True},
    )

    class _Client:
        def generate_json(self, *, prompt, n=1, **kw):
            return [{"values": []}]

    engine = get_engine("b2_library")(use_sdgx=False)
    engine.setup(_Client(), ctx)
    keys = [(f"K{i}",) for i in range(30)]
    out = [r.model_dump() for r in engine.generate_for_keys(keys, GenerationConfig(seed=2, batch_size=8))]
    assert out and all((r["PID"],) in keys for r in out)
    per_key: dict = {}
    for r in out:
        per_key.setdefault(r["PID"], []).append(r["CAT"])
    assert all(len(v) == len(set(v)) for v in per_key.values())
    assert {len(v) for v in per_key.values()} <= {1, 3}


def test_b2_rest_columns_do_not_repeat_across_chunks():
    """ADR 0036: the per-chunk `rest` sampling must consume ONE rng across
    the whole call, not replay a fresh stream per chunk."""
    from sdfb_core.contracts import TableSchema
    from sdfb_core.engines import GenerationConfig, GenerationContext, get_engine

    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.src.child_t"},
         "schema": [
             {"name": "PID", "type": "STRING", "mode": "REQUIRED"},
             {"name": "CAT", "type": "STRING", "mode": "REQUIRED"},
             {"name": "AMT", "type": "INT64", "mode": "REQUIRED"},
         ]}
    )
    rows = [{"PID": f"P{i:04d}", "CAT": "abc"[i % 3], "AMT": i} for i in range(60)]
    ctx = GenerationContext(
        table_schema=schema, reference_rows=rows, reference_digest="b2-fanout",
        pipeline_run_id="b2-run", pk_columns=["PID", "CAT"],
        fanout={"driving_cols": ["PID"], "histogram": {"3": 1},
                "cells": {"cols": ["CAT"], "rows": [["a"], ["b"], ["c"]], "counts": [1, 1, 1]},
                "exact_cells": True},
    )

    class _Client:
        def generate_json(self, *, prompt, n=1, **kw):
            return [{"values": []}]

    engine = get_engine("b2_library")(use_sdgx=False)
    engine.setup(_Client(), ctx)
    keys = [(f"K{i}",) for i in range(20)]
    out = [
        r.model_dump()
        for r in engine.generate_for_keys(keys, GenerationConfig(seed=2, batch_size=6))
    ]
    amts = [r["AMT"] for r in out]
    assert len(amts) == 60
    assert amts[:6] != amts[6:12]
