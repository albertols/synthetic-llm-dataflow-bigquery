"""RelationalContract + llm_prompt_constraint parsing (Task 2)."""

import pytest

from sdfb_core.contracts.description_json import DescriptionJsonError
from sdfb_core.contracts.relational import (
    parse_llm_prompt_constraint,
    parse_relational_contract,
)


def test_absent_contract_is_none():
    assert parse_relational_contract("just prose") is None
    assert parse_relational_contract("") is None


def test_full_contract_parses():
    c = parse_relational_contract(
        'Ops table {"sdfb": 1, "pk": ["A", "B"], '
        '"fk": [{"cols": ["A"], "ref": "ds.parent", "ref_cols": ["A"]}], '
        '"identity": ["C"]}'
    )
    assert c is not None
    assert c.sdfb == 1
    assert c.pk == ("A", "B")
    assert c.fk[0].ref == "ds.parent"
    assert c.fk[0].cols == ("A",)
    assert c.identity == ("C",)


def test_minimal_contract_defaults():
    c = parse_relational_contract('{"sdfb": 1}')
    assert c.pk == () and c.fk == () and c.identity == ()


def test_marked_but_invalid_schema_raises():
    with pytest.raises(DescriptionJsonError):
        parse_relational_contract(
            '{"sdfb": 1, "fk": [{"cols": [], "ref": "x.y", "ref_cols": []}]}'
        )


def test_fk_arity_mismatch_raises():
    with pytest.raises(DescriptionJsonError):
        parse_relational_contract(
            '{"sdfb": 1, "fk": [{"cols": ["A"], "ref": "d.t", "ref_cols": ["A", "B"]}]}'
        )


def test_fk_ref_needs_a_dataset_qualifier():
    with pytest.raises(DescriptionJsonError):
        parse_relational_contract(
            '{"sdfb": 1, "fk": [{"cols": ["A"], "ref": "nodots", "ref_cols": ["A"]}]}'
        )


def test_constraint_absent_is_empty():
    assert parse_llm_prompt_constraint("plain column description") == ""
    assert parse_llm_prompt_constraint("") == ""


def test_constraint_parsed_normalized_capped():
    desc = 'Doc. {"llm_prompt_constraint": "ISO-4217 currency\\ncodes only"}'
    assert parse_llm_prompt_constraint(desc) == "ISO-4217 currency codes only"
    long = '{"llm_prompt_constraint": "' + "x" * 900 + '"}'
    assert len(parse_llm_prompt_constraint(long)) == 500


def test_constraint_non_string_value_is_empty():
    assert parse_llm_prompt_constraint('{"llm_prompt_constraint": 42}') == ""
