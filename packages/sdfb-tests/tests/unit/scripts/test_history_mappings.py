"""Persistent table/column alias registry (ADR 0029, report consistency).

One `integration_tests/history_mappings_replacement.json` (LOCAL-ONLY —
it is the decode key) replaces per-job `mapping.json`: a table keeps its
letter prefix and column aliases across every future run, and prefixes
scale past Z (A..Z, AA, AB, …) for the dozens of unrelated tables to
come.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = (
    Path(__file__).parents[5] / "scripts" / "e2e" / "history_mappings.py"
)
_spec = importlib.util.spec_from_file_location("history_mappings", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


class TestLetterPrefix:
    def test_single_letters(self):
        assert _mod.letter_prefix(0) == "A"
        assert _mod.letter_prefix(5) == "F"
        assert _mod.letter_prefix(25) == "Z"

    def test_rolls_over_to_double_letters(self):
        assert _mod.letter_prefix(26) == "AA"
        assert _mod.letter_prefix(27) == "AB"
        assert _mod.letter_prefix(51) == "AZ"
        assert _mod.letter_prefix(52) == "BA"
        assert _mod.letter_prefix(701) == "ZZ"
        assert _mod.letter_prefix(702) == "AAA"


class TestAssign:
    def test_first_table_gets_a_and_ddl_ordered_columns(self):
        h = _mod.HistoryMappings()
        entry = h.assign_table("p.d.limits", ["KEY", "AMT", "NOTE"])
        assert entry["alias"] == "A_TABLE"
        assert entry["columns"] == {
            "KEY": "A_COL_001", "AMT": "A_COL_002", "NOTE": "A_COL_003"
        }

    def test_assignment_is_idempotent(self):
        h = _mod.HistoryMappings()
        first = h.assign_table("p.d.limits", ["KEY", "AMT"])
        again = h.assign_table("p.d.limits", ["KEY", "AMT"])
        assert first == again

    def test_new_columns_append_after_existing(self):
        h = _mod.HistoryMappings()
        h.assign_table("p.d.limits", ["KEY", "AMT"])
        entry = h.assign_table("p.d.limits", ["KEY", "NEW_COL", "AMT"])
        assert entry["columns"]["KEY"] == "A_COL_001"
        assert entry["columns"]["AMT"] == "A_COL_002"
        assert entry["columns"]["NEW_COL"] == "A_COL_003"

    def test_second_table_gets_b(self):
        h = _mod.HistoryMappings()
        h.assign_table("p.d.limits", ["K"])
        assert h.assign_table("p.d.tx", ["K"])["alias"] == "B_TABLE"

    def test_retained_fields_recorded_not_aliased(self):
        h = _mod.HistoryMappings()
        h.assign_table("p.d.limits", ["K"])
        h.retain_field("p.d.limits", "JOIN_KEY")
        entry = h.assign_table("p.d.limits", ["K"])
        assert entry["retained"] == ["JOIN_KEY"]
        assert "JOIN_KEY" not in entry["columns"]


class TestPersistence:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "history_mappings_replacement.json"
        h = _mod.HistoryMappings()
        h.assign_table("p.d.limits", ["K", "V"])
        h.save(path)
        h2 = _mod.HistoryMappings.load(path)
        assert h2.assign_table("p.d.limits", ["K", "V"])["columns"] == {
            "K": "A_COL_001", "V": "A_COL_002"
        }
        # next table continues the sequence after reload
        assert h2.assign_table("p.d.other", ["X"])["alias"] == "B_TABLE"

    def test_load_missing_file_is_empty(self, tmp_path):
        h = _mod.HistoryMappings.load(tmp_path / "absent.json")
        assert h.assign_table("p.d.t", ["C"])["alias"] == "A_TABLE"


class TestSeedExample:
    def test_seed_creates_a_through_f_unbound(self):
        h = _mod.seed_example()
        aliases = [t["alias"] for t in h.tables]
        assert aliases == [
            "A_TABLE", "B_TABLE", "C_TABLE",
            "D_TABLE", "E_TABLE", "F_TABLE",
        ]
        assert all(t["real_fqn"] is None for t in h.tables)

    def test_adopt_binds_real_fqn_then_assign_reuses_it(self):
        h = _mod.seed_example()
        h.adopt("C_TABLE", "p.src.accounts_master")
        entry = h.assign_table("p.src.accounts_master", ["ID", "STATE"])
        assert entry["alias"] == "C_TABLE"
        assert entry["columns"]["ID"] == "C_COL_001"
        # a brand-new table continues AFTER the seeded block
        assert h.assign_table("p.src.brand_new", ["X"])["alias"] == "G_TABLE"


# --- registry-driven redaction (build_mapping presets) ------------------

_RED_SCRIPT = Path(__file__).parents[5] / "scripts" / "e2e" / "redaction.py"
_rspec = importlib.util.spec_from_file_location("redaction", _RED_SCRIPT)
_red = importlib.util.module_from_spec(_rspec)
sys.modules[_rspec.name] = _red
_rspec.loader.exec_module(_red)

_METRICS = {
    "gcp": {
        "bigquery": {
            "columns": {"KEY": "STRING", "AMT": "INT64", "NOTE": "STRING"},
            "pk_columns": ["KEY"],
        },
    },
    "offline": {"primary_key": ["KEY"], "engines": {}},
}


class TestPresetMapping:
    def test_preset_columns_win_over_role_naming(self):
        m = _red.build_mapping(
            _METRICS,
            redact_values=False,
            preset_columns={"KEY": "A_COL_001", "AMT": "A_COL_002",
                            "NOTE": "A_COL_003"},
        )
        assert m.columns["KEY"] == "A_COL_001"  # not PK_COL
        assert m.columns["NOTE"] == "A_COL_003"

    def test_unpreset_columns_still_get_generic_names(self):
        m = _red.build_mapping(
            _METRICS, redact_values=False,
            preset_columns={"KEY": "A_COL_001"},
        )
        assert m.columns["KEY"] == "A_COL_001"
        assert m.columns["AMT"].startswith("COL_")

    def test_preset_table_alias_replaces_target_table(self):
        metrics = {
            "gcp": {"bigquery": {"columns": {},
                                 "source_fqn": "p.src_ds.limits"}},
            "offline": {},
        }
        m = _red.build_mapping(
            metrics, redact_values=False, preset_table_alias="A_TABLE"
        )
        assert m.redact_scalar("p.src_ds.limits").endswith("A_TABLE")

    def test_ordered_columns_helper_matches_collect_order(self):
        cols = _red.ordered_columns(_METRICS)
        assert cols == ["KEY", "AMT", "NOTE"]
