"""build_plan_detail: per-column fidelity detail (Task 9)."""

from sdfb_core.contracts.schema import FieldSchema, TableSchema
from sdfb_core.engines.b1_rag.profile import profile_columns
from sdfb_core.engines.b2_library.fidelity import profile_column
from sdfb_core.engines.generation_plan import build_plan_detail


def _rows() -> list[dict]:
    rows = [
        {"NOTES": f"REF {i:04d} SETTLED PAYMENT ORDER", "FLAG": "Y"}
        for i in range(60)
    ]
    rows += [{"NOTES": "", "FLAG": "N"} for _ in range(40)]
    return rows


def test_detail_from_b1_profiles():
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t"},
            "schema": [
                {"name": "NOTES", "type": "STRING", "mode": "NULLABLE"},
                {"name": "FLAG", "type": "STRING", "mode": "REQUIRED"},
            ],
        }
    )
    detail = build_plan_detail(profile_columns(schema, _rows()))
    assert detail["NOTES"]["kind"] == "freetext_llm_pool"
    assert detail["NOTES"]["empty_fraction"] == 0.4
    assert detail["NOTES"]["shapes"] >= 1
    assert detail["NOTES"]["constraint"] is False
    assert detail["FLAG"]["kind"] == "categorical"


def test_detail_from_b2_profiles():
    fields = {
        name: FieldSchema.model_validate(
            {"name": name, "type": "STRING", "mode": "NULLABLE"}
        )
        for name in ("NOTES", "FLAG")
    }
    profiles = {
        name: profile_column(f, _rows()) for name, f in fields.items()
    }
    detail = build_plan_detail(profiles)
    assert detail["NOTES"]["empty_fraction"] == 0.4
    assert set(detail) == {"NOTES", "FLAG"}
