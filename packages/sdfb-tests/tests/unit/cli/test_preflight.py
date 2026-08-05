"""Relational preflight P1-P5 (Task 13)."""

import pytest
from sdfb_beam.cli.preflight import preflight
from sdfb_core.contracts import TableSchema

_CONTRACT = (
    '{"sdfb": 1, "pk": ["ID"], '
    '"fk": [{"cols": ["CUST_ID"], "ref": "ds.customers", "ref_cols": ["ID"]}], '
    '"identity": ["ID"]}'
)


def _schema(desc: str, cols=("ID", "CUST_ID", "NOTES")) -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t", "description": desc},
            "schema": [
                {"name": c, "type": "STRING", "mode": "REQUIRED"} for c in cols
            ],
        }
    )


def _rows(n: int = 10) -> list[dict]:
    return [{"ID": f"id{i}", "CUST_ID": "c1", "NOTES": "x"} for i in range(n)]


def test_p1_invalid_contract_stops():
    with pytest.raises(SystemExit, match="preflight P1"):
        preflight(_schema('{"sdfb": 1, "pk": [BROKEN}'), (), (), _rows())


def test_p2_unknown_column_stops():
    bad = '{"sdfb": 1, "pk": ["NOPE"]}'
    with pytest.raises(SystemExit, match=r"preflight P2.*NOPE"):
        preflight(_schema(bad), (), (), _rows())


def test_p3_unresolved_fk_parent_stops():
    with pytest.raises(SystemExit, match=r"preflight P3.*customers"):
        preflight(
            _schema(_CONTRACT), (), (), _rows(),
            fk_parents_resolved={"ds.customers": False},
        )


def test_p3_resolved_parent_passes():
    result = preflight(
        _schema(_CONTRACT), (), (), _rows(),
        fk_parents_resolved={"ds.customers": True},
    )
    assert result.pk_cols == ("ID",)


def test_contract_fills_defaults_and_cli_wins():
    result = preflight(_schema(_CONTRACT), (), (), _rows())
    assert result.pk_cols == ("ID",)
    assert result.identity_cols == ("ID",)

    overridden = preflight(_schema(_CONTRACT), ("CUST_ID",), (), _rows())
    assert overridden.pk_cols == ("CUST_ID",)
    assert any("overrides contract" in w for w in overridden.warnings)


def test_p5_duplicate_pk_in_sample_warns_not_stops():
    rows = [*_rows(), {"ID": "id0", "CUST_ID": "c1", "NOTES": "dup"}]
    result = preflight(_schema(_CONTRACT), (), (), rows)
    assert any("not unique in the reference sample" in w for w in result.warnings)


def test_absent_contract_passthrough():
    result = preflight(_schema("just prose"), ("A",), (), [])
    assert result.contract is None
    assert result.pk_cols == ("A",)


# --------------------------------------------------------------------------- #
# prompt-constraint discovery (C5 — refinement, never a requirement)
# --------------------------------------------------------------------------- #
def _capture_milestones(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "sdfb_beam.cli.preflight.log_milestone",
        lambda name, **kw: calls.append((name, kw)),
    )
    return calls


def test_constraints_on_but_none_found_is_a_logged_noop(monkeypatch):
    calls = _capture_milestones(monkeypatch)
    result = preflight(_schema("just prose"), (), (), _rows())
    assert result.contract is None  # generation proceeds unchanged
    names = [n for n, _ in calls]
    assert "prompt_constraints_none" in names
    (_, kw) = next(c for c in calls if c[0] == "prompt_constraints_none")
    assert "no-op" in kw["note"]


def test_constraints_found_are_listed(monkeypatch):
    calls = _capture_milestones(monkeypatch)
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t"},
            "schema": [
                {"name": "ID", "type": "STRING", "mode": "REQUIRED"},
                {
                    "name": "NOTES",
                    "type": "STRING",
                    "mode": "NULLABLE",
                    "description": 'x {"llm_prompt_constraint": "SWIFT refs"}',
                },
            ],
        }
    )
    preflight(schema, (), (), [])
    (_, kw) = next(c for c in calls if c[0] == "prompt_constraints_found")
    assert kw["columns"] == "NOTES"
    assert kw["count"] == 1


def test_constraints_disabled_and_none_found_stays_silent(monkeypatch):
    calls = _capture_milestones(monkeypatch)
    preflight(_schema("just prose"), (), (), _rows(),
              prompt_constraints_enabled=False)
    assert "prompt_constraints_none" not in [n for n, _ in calls]


def test_malformed_column_constraint_stops_loudly():
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t"},
            "schema": [
                {
                    "name": "NOTES",
                    "type": "STRING",
                    "mode": "NULLABLE",
                    "description": '{"llm_prompt_constraint": BROKEN}',
                }
            ],
        }
    )
    with pytest.raises(SystemExit, match="NOTES"):
        preflight(schema, (), (), [])
