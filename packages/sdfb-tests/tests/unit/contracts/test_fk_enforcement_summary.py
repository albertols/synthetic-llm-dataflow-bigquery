"""FK enforcement made loud BEFORE the GPU spends an hour (ADR 0031).

The 2026-08-23 run declared one FK edge, marked it `informational: true`,
grouped both tables into one 49-minute job on the strength of it, and
enforced nothing. Every fact needed to predict that was known at launch:
the edge is display-only, and every one of its columns exists on both
sides — i.e. it COULD have been enforced. The summary states that in the
launcher log, in the words of the fix.
"""

from __future__ import annotations

from sdfb_core.contracts.fk_enforcement import enforcement_summary
from sdfb_core.contracts.relational import parse_relational_contract

_CHILD = "proj.src.B_TABLE"
_COLUMNS = {
    "proj.src.B_TABLE": {"COL_005", "COL_006", "COL_008", "PK_COL"},
    "proj.landing.A_TABLE": {"COL_005", "COL_006", "COL_008", "PK_COL_2"},
}


def _contract(informational: bool) -> object:
    flag = ', "informational": true' if informational else ""
    return parse_relational_contract(
        '{"sdfb": 1, "pk": ["PK_COL"], "fk": [{"cols": '
        '["COL_005", "COL_006", "COL_008"], "ref": "landing.A_TABLE", '
        '"ref_cols": ["COL_005", "COL_006", "COL_008"]' + flag + "}]}"
    )


class TestInformationalButEnforceable:
    def test_counts_and_warning_level(self):
        s = enforcement_summary(_CHILD, _contract(True), _COLUMNS)
        assert (s.enforced, s.informational) == (0, 1)
        assert s.warn is True

    def test_names_the_edge_as_enforceable_and_the_exact_fix(self):
        text = enforcement_summary(_CHILD, _contract(True), _COLUMNS).text
        assert "COL_005,COL_006,COL_008" in text
        assert "ENFORCEABLE" in text
        assert '"informational": true' in text  # the literal edit to make
        assert "fk.orphan" in text  # what enforcing it buys

    def test_says_integrity_is_not_verified_this_run(self):
        text = enforcement_summary(_CHILD, _contract(True), _COLUMNS).text
        assert "NOT verified" in text


class TestEnforcedEdges:
    def test_an_enforced_edge_is_not_a_warning(self):
        s = enforcement_summary(_CHILD, _contract(False), _COLUMNS)
        assert (s.enforced, s.informational) == (1, 0)
        assert s.warn is False
        assert "1 enforced" in s.text

    def test_no_contract_no_summary(self):
        assert enforcement_summary(_CHILD, None, _COLUMNS) is None

    def test_contract_without_edges_no_summary(self):
        contract = parse_relational_contract('{"sdfb": 1, "pk": ["PK_COL"]}')
        assert enforcement_summary(_CHILD, contract, _COLUMNS) is None


class TestUnenforceableEdges:
    def test_a_join_key_absent_from_the_ddl_is_correctly_informational(self):
        """The legitimate use (ADR 0029): the join key is not a column,
        so nothing to nudge — it must NOT be reported as enforceable."""
        contract = parse_relational_contract(
            '{"sdfb": 1, "fk": [{"cols": ["JOIN_KEY"], '
            '"ref": "landing.A_TABLE", "ref_cols": ["JOIN_KEY"], '
            '"informational": true}]}'
        )
        s = enforcement_summary(_CHILD, contract, _COLUMNS)
        assert s.warn is False
        assert "ENFORCEABLE" not in s.text
        assert "JOIN_KEY" in s.text

    def test_unknown_parent_schema_does_not_guess(self):
        """Parent columns unknown (an external parent not scanned this
        launch) ⇒ report the child side only, never claim enforceable."""
        s = enforcement_summary(
            _CHILD, _contract(True), {_CHILD: _COLUMNS[_CHILD]}
        )
        assert "ENFORCEABLE" not in s.text
        assert "parent schema unknown" in s.text
