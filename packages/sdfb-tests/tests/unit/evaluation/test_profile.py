"""choose_stratification_column — first non-numeric column with 2..50 distinct wins."""

from sdfb_core.contracts import TableSchema
from sdfb_core.evaluation.profile import (
    StratificationPlan,
    choose_stratification_column,
    stratum_key,
)


def _schema(cols: list[tuple[str, str]]) -> TableSchema:
    return TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.t"},
            "columns": [{"name": n, "type": t} for n, t in cols],
        }
    )


def test_picks_first_qualifying_column_in_schema_order():
    schema = _schema([("amount", "FLOAT64"), ("tier", "STRING"), ("region", "STRING")])
    rows = [{"amount": i, "tier": f"t{i % 3}", "region": f"r{i % 4}"} for i in range(30)]
    plan = choose_stratification_column(schema, rows)
    assert plan.column == "tier"
    assert plan.values == ("t0", "t1", "t2")


def test_numeric_and_over_cardinality_columns_skipped():
    schema = _schema([("id", "INT64"), ("email", "STRING")])
    rows = [{"id": i, "email": f"u{i}@x.com"} for i in range(100)]  # 100 distinct > 50
    plan = choose_stratification_column(schema, rows)
    assert plan == StratificationPlan(column=None, values=())


def test_single_value_column_skipped():
    schema = _schema([("env", "STRING")])
    rows = [{"env": "prd"}] * 10  # 1 distinct < min_categories
    assert choose_stratification_column(schema, rows).column is None


def test_nulls_do_not_count_as_a_category():
    schema = _schema([("tier", "STRING")])
    rows = [{"tier": "a"}, {"tier": "b"}, {"tier": None}]
    plan = choose_stratification_column(schema, rows)
    assert plan.values == ("a", "b")


def test_stratum_key_fallback_and_column_mode():
    plan_none = StratificationPlan(column=None, values=())
    assert stratum_key(plan_none, {"x": 1}) == "__all__"
    plan = StratificationPlan(column="tier", values=("a",))
    assert stratum_key(plan, {"tier": "a"}) == "a"
    assert stratum_key(plan, {}) == "None"
