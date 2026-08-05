"""Parent-first table-set orchestration (Task 16)."""

import importlib.util
import sys
from pathlib import Path

import pytest
from sdfb_core.contracts.relational import parse_relational_contract

_SCRIPT = Path(__file__).parents[5] / "scripts" / "run_tableset.py"
_spec = importlib.util.spec_from_file_location("run_tableset", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


def _contract(fk_refs: list[str]):
    fks = ", ".join(
        f'{{"cols": ["C{i}"], "ref": "{ref}", "ref_cols": ["ID"]}}'
        for i, ref in enumerate(fk_refs)
    )
    return parse_relational_contract(f'{{"sdfb": 1, "fk": [{fks}]}}')


_T = ["p.ds.orders", "p.ds.customers", "p.ds.lines"]


def test_topo_orders_parents_first_chain_and_diamond():
    contracts = {
        "p.ds.orders": _contract(["ds.customers"]),
        "p.ds.lines": _contract(["ds.orders", "ds.customers"]),
        "p.ds.customers": None,
    }
    order = _mod.topo_sort(_T, _mod.resolve_edges(_T, contracts))
    assert order.index("p.ds.customers") < order.index("p.ds.orders")
    assert order.index("p.ds.orders") < order.index("p.ds.lines")


def test_cycle_raises():
    contracts = {
        "p.ds.orders": _contract(["ds.customers"]),
        "p.ds.customers": _contract(["ds.orders"]),
    }
    tables = ["p.ds.orders", "p.ds.customers"]
    with pytest.raises(_mod.TableSetError, match="cycle"):
        _mod.topo_sort(tables, _mod.resolve_edges(tables, contracts))


def test_fk_outside_the_set_is_not_an_edge():
    contracts = {"p.ds.orders": _contract(["other_ds.landed_elsewhere"])}
    edges = _mod.resolve_edges(["p.ds.orders"], contracts)
    assert edges["p.ds.orders"] == set()


def test_plan_argv_fk_flag_only_for_children():
    config = {
        "tables": ["p.ds.orders", "p.ds.customers"],
        "landing_dataset": "p.synthetic_data",
        "run_id": "set1",
        "common_args": {"num_rows": "1000", "model_uri": "gs://m"},
    }
    contracts = {
        "p.ds.orders": _contract(["ds.customers"]),
        "p.ds.customers": None,
    }
    plans = _mod.plan_tableset(config, contracts)
    parent_cmd, child_cmd = plans[0], plans[1]
    assert "--reference_table=p.ds.customers" in parent_cmd
    assert not any(a.startswith("--fk_parent_landing") for a in parent_cmd)
    assert "--fk_parent_landing=p.synthetic_data" in child_cmd
    assert "--landing_table=p.synthetic_data.orders" in child_cmd
    assert "--num_rows=1000" in child_cmd
