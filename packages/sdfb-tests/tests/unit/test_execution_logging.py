"""Execution-visibility logging (ADR 0030): every enabled/disabled
config is one human-readable block; in multi-table runs every column
reference is landing-table-qualified so `_full_report.md` +
`worker_logs.jsonl` copy-paste straight into oss/ replacements.
"""

from __future__ import annotations

import logging

from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext
from sdfb_core.engines.b1_rag import B1RagEngine, HashingEmbedder
from sdfb_core.engines.generation_plan import clear_generation_plan_log
from sdfb_core.observability import log_milestone, milestone_scope

_E2F = (
    '{"llm_prompt_constraint": {"route": "llm", '
    '"pattern": "^(E2F[13][0-9A-F]{20}|2301[0-9A-F]{20})$"}}'
)


class TestMilestoneScope:
    def test_scope_tags_every_milestone_with_table(self, caplog):
        with (
            caplog.at_level(logging.INFO, logger="sdfb.milestone"),
            milestone_scope("ORDERS_FLAT"),
        ):
            log_milestone("batch_start", batch_id=1, n=10)
        assert "table=ORDERS_FLAT" in caplog.text
        assert "name=batch_start" in caplog.text

    def test_explicit_table_field_wins_over_scope(self, caplog):
        with (
            caplog.at_level(logging.INFO, logger="sdfb.milestone"),
            milestone_scope("WRONG"),
        ):
            log_milestone("x", table="RIGHT")
        assert "table=RIGHT" in caplog.text
        assert "WRONG" not in caplog.text

    def test_no_scope_no_table_field(self, caplog):
        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_milestone("plain_event", n=1)
        line = next(
            r.message for r in caplog.records if "plain_event" in r.message
        )
        assert "table=" not in line


class TestQualifiedPrettyColumns:
    def _setup(self, caplog, prefix: str):
        clear_generation_plan_log()
        schema = TableSchema.model_validate(
            {
                "table_info": {"table_id": "p.src.b_table"},
                "schema": [
                    {"name": "KEY", "type": "STRING", "mode": "REQUIRED",
                     "description": _E2F},
                ],
            }
        )
        rows = [{"KEY": f"E2F3{i:020X}"} for i in range(60)]
        ctx = GenerationContext(
            table_schema=schema,
            reference_rows=rows,
            reference_digest=f"qual-{prefix or 'off'}",
            pipeline_run_id="qual-run",
            landing_table="p.land.B_TABLE",
            log_table_prefix=prefix,
        )
        engine = B1RagEngine(embedder=HashingEmbedder())

        class _C:
            def generate_json(self, **kw):
                return [{"values": []}]

        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            engine.setup(_C(), ctx)
        clear_generation_plan_log()

    def test_multi_table_prefix_qualifies_columns(self, caplog):
        self._setup(caplog, prefix="B_TABLE")
        assert '"B_TABLE.KEY"' in caplog.text  # plan + constraints keys

    def test_single_table_keys_stay_bare(self, caplog):
        self._setup(caplog, prefix="")
        assert '"B_TABLE.KEY"' not in caplog.text
        assert '"KEY"' in caplog.text
