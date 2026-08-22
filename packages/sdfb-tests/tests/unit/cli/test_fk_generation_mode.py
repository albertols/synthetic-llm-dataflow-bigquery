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

    def test_disabled_maps_to_skip_and_isolated(self):
        landing, mode = resolve_fk_mode(False, "p.landing")
        assert (landing, mode) == ("skip", "isolated")

    def test_disabled_with_empty_flag_still_isolated(self):
        landing, mode = resolve_fk_mode(False, "")
        assert (landing, mode) == ("skip", "isolated")


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
                ForeignKey(cols=("PARTY_KEY",), ref="ds.parties",
                           ref_cols=("PARTY_KEY",), informational=True),
            ),
        )

    def test_logs_mermaid_with_parents_and_dashed_edge(self, caplog):
        with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
            log_launcher_fk_model("p.src.orders", self._contract(),
                                  mode="relational")
        assert "name=fk_model_pretty" in caplog.text
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
