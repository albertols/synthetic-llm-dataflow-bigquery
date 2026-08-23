"""--generate_fk_relationships flag + FK-model launcher logging (ADR 0029).

The flag is the user-facing switch between relational and isolated
generation; its state must be one greppable milestone, and the resolved
FK model must be pasteable mermaid in the launcher log.
"""

from __future__ import annotations

import logging

from sdfb_beam.cli.run_pipeline import (
    log_launcher_fk_model,
    resolve_fk_mode,
)
from sdfb_core.contracts.relational import ForeignKey, RelationalContract
from sdfb_core.observability import log_milestone_text


class TestResolveFkMode:
    def test_enabled_passes_parent_landing_through(self):
        landing, mode = resolve_fk_mode(True, "p.landing")
        assert (landing, mode) == ("p.landing", "relational")

    def test_disabled_clears_landing_and_is_isolated(self):
        landing, mode = resolve_fk_mode(False, "p.landing")
        assert (landing, mode) == ("", "isolated")

    def test_disabled_with_empty_flag_still_isolated(self):
        landing, mode = resolve_fk_mode(False, "")
        assert (landing, mode) == ("", "isolated")


class TestLogMilestoneText:
    def test_header_line_plus_raw_body(self, caplog):
        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_milestone_text("fk_model_pretty", "flowchart BT\n  a --> b",
                               table="p.d.t")
        assert "name=fk_model_pretty" in caplog.text
        assert "flowchart BT\n  a --> b" in caplog.text


class TestLauncherFkModel:
    def _contract(self) -> RelationalContract:
        return RelationalContract(
            sdfb=1,
            pk=("ID",),
            fk=(
                ForeignKey(cols=("CUST_ID",), ref="ds.customers",
                           ref_cols=("ID",)),
                ForeignKey(cols=("JOIN_KEY",), ref="ds.parties",
                           ref_cols=("JOIN_KEY",), informational=True),
            ),
        )

    def test_logs_mermaid_with_parents_and_dashed_edge(self, caplog):
        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_launcher_fk_model("p.src.orders", self._contract(),
                                  mode="relational")
        assert "name=fk_model_pretty" in caplog.text
        # glanceable ASCII first (2026-08-22 operator ask)...
        assert "relational model |" in caplog.text
        assert "-->" in caplog.text and "..>" in caplog.text
        # ...then the fenced mermaid for report recycling.
        assert "```mermaid" in caplog.text
        assert "flowchart" in caplog.text
        assert "customers" in caplog.text
        assert "-.->" in caplog.text  # informational edge stays visible

    def test_no_contract_logs_absent(self, caplog):
        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_launcher_fk_model("p.src.orders", None, mode="relational")
        assert "name=fk_model_absent" in caplog.text

    def test_mode_milestone_always_present(self, caplog):
        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_launcher_fk_model("p.src.orders", self._contract(),
                                  mode="isolated")
        assert "name=fk_generation_mode" in caplog.text
        assert "mode=isolated" in caplog.text


class TestLauncherEnforcementSummary:
    """ADR 0031 — the launcher states what it will ENFORCE, at WARNING
    level when the answer is "nothing it could have enforced"."""

    @staticmethod
    def _schema():
        from sdfb_core.contracts import TableSchema

        return TableSchema.model_validate(
            {
                "table_info": {"table_id": "p.src.B_TABLE"},
                "schema": [
                    {"name": "COL_005", "type": "INT64", "mode": "REQUIRED"},
                    {"name": "PK_COL", "type": "STRING", "mode": "REQUIRED"},
                ],
            }
        )

    @staticmethod
    def _contract(informational: bool):
        return RelationalContract(
            sdfb=1,
            pk=("PK_COL",),
            fk=(
                ForeignKey(
                    cols=("COL_005",),
                    ref="landing.A_TABLE",
                    ref_cols=("COL_005",),
                    informational=informational,
                ),
            ),
        )

    def test_informational_but_enforceable_warns_with_the_fix(self, caplog):
        from sdfb_beam.cli.run_pipeline import log_fk_enforcement

        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_fk_enforcement(
                "p.src.B_TABLE",
                self._contract(True),
                {"p.landing.A_TABLE": frozenset({"COL_005"})},
                self._schema(),
            )
        assert "name=fk_enforcement_summary" in caplog.text
        assert "enforceable_but_informational=1" in caplog.text
        assert "ENFORCEABLE" in caplog.text
        assert "WARNING" in caplog.text

    def test_enforced_edge_reports_without_warning(self, caplog):
        from sdfb_beam.cli.run_pipeline import log_fk_enforcement

        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_fk_enforcement(
                "p.src.B_TABLE",
                self._contract(False),
                {"p.landing.A_TABLE": frozenset({"COL_005"})},
                self._schema(),
            )
        assert "enforced=1" in caplog.text
        assert "WARNING" not in caplog.text

    def test_no_edges_logs_nothing(self, caplog):
        from sdfb_beam.cli.run_pipeline import log_fk_enforcement

        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_fk_enforcement(
                "p.src.B_TABLE",
                RelationalContract(sdfb=1, pk=("PK_COL",)),
                {},
                self._schema(),
            )
        assert "fk_enforcement_summary" not in caplog.text
