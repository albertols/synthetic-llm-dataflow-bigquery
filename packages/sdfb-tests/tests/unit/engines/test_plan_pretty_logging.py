"""Pretty plan + relational-E2E log entries (ADR 0028 follow-up).

Operators inspecting a Dataflow run read the `generation_plan` milestone
as one 8k-char single-line JSON blob, and nothing in the worker log ties
the clauses + landing table + FK parents together. Two once-per-plan
pretty entries fix that, adjacent to the existing milestone: an
indent-2 `generation_plan_pretty` and a `relational_e2e` block naming
every relational table the run touches.
"""

from __future__ import annotations

import logging

import pytest
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext, get_engine
from sdfb_core.engines.b1_rag import B1RagEngine, HashingEmbedder
from sdfb_core.engines.generation_plan import clear_generation_plan_log

_E2F = (
    '{"llm_prompt_constraint": {"route": "llm", '
    '"pattern": "^(E2F[13][0-9A-F]{20}|2301[0-9A-F]{20})$"}}'
)


@pytest.fixture(autouse=True)
def _fresh_state():
    clear_generation_plan_log()
    yield
    clear_generation_plan_log()


_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "p.src.child_t"},
        "schema": [
            {
                "name": "KEY",
                "type": "STRING",
                "mode": "REQUIRED",
                "description": _E2F,
            },
            {"name": "PARENT_ID", "type": "STRING", "mode": "REQUIRED"},
        ],
    }
)


def _rows(n: int = 60) -> list[dict]:
    return [
        {"KEY": f"E2F3{i:020X}", "PARENT_ID": f"P{i % 7}"} for i in range(n)
    ]


class _StubClient:
    def generate_json(self, *, prompt: str, n: int = 1, **kwargs):
        return [{"values": [f"gen-{i}" for i in range(32)]}]


def _ctx() -> GenerationContext:
    return GenerationContext(
        table_schema=_SCHEMA,
        reference_rows=_rows(),
        reference_digest="pretty-digest",
        pipeline_run_id="pretty-run",
        pk_columns=["KEY"],
        landing_table="p.landing.child_t",
        fk_edges=[
            {
                "cols": ["PARENT_ID"],
                "ref": "src.parent_t",
                "parent_landing": "p.landing.parent_t",
            }
        ],
        fk_pools={"PARENT_ID": tuple(f"P{i}" for i in range(7))},
    )


def _b1_setup(caplog) -> None:
    engine = B1RagEngine(embedder=HashingEmbedder())
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        engine.setup(_StubClient(), _ctx())


class TestPrettyEntries:
    def test_plan_pretty_is_indented_json(self, caplog) -> None:
        _b1_setup(caplog)
        assert "name=generation_plan_pretty" in caplog.text
        # indent=2 body: nested keys start their own line.
        assert '\n  "columns"' in caplog.text or '\n  "plan"' in caplog.text

    def test_relational_e2e_names_every_table_and_clause(self, caplog) -> None:
        _b1_setup(caplog)
        assert "name=relational_e2e" in caplog.text
        assert "p.landing.child_t" in caplog.text
        assert "p.landing.parent_t" in caplog.text
        assert '"pk"' in caplog.text
        # The fetched clause itself, not just `constraint: true`.
        assert "E2F[13][0-9A-F]{20}" in caplog.text

    def test_fk_pool_size_and_activation_are_visible(self, caplog) -> None:
        _b1_setup(caplog)
        assert '"pool_size": 7' in caplog.text
        assert '"active": true' in caplog.text

    def test_pretty_entries_log_once_per_plan(self, caplog) -> None:
        _b1_setup(caplog)
        _b1_setup(caplog)  # same digest+table: the once-guard holds
        assert caplog.text.count("name=generation_plan_pretty") == 1
        assert caplog.text.count("name=relational_e2e") == 1


class TestWorkerFkModelPretty:
    def test_fk_model_pretty_renders_worker_side(self, caplog) -> None:
        _b1_setup(caplog)
        assert "name=fk_model_pretty" in caplog.text
        assert "flowchart" in caplog.text
        assert "parent_t" in caplog.text

    def test_fk_model_pretty_logs_once_per_plan(self, caplog) -> None:
        _b1_setup(caplog)
        _b1_setup(caplog)
        assert caplog.text.count("name=fk_model_pretty") == 1


def test_b2_engine_emits_the_same_pretty_entries(caplog) -> None:
    engine = get_engine("b2_library")(use_sdgx=False)
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        engine.setup(_StubClient(), _ctx())
    assert "name=generation_plan_pretty" in caplog.text
    assert "name=relational_e2e" in caplog.text
