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


# --- ADR 0029: waves, flag semantics, trigger configs, model artifact ---

_SIX = {
    "p.ds.a_t": None,
    "p.ds.b_t": _contract(["ds.a_t"]),
    "p.ds.c_t": None,
    "p.ds.d_t": _contract(["ds.c_t"]),
}
_SIX_TABLES = list(_SIX)


def _config(tables=None):
    return {
        "tables": tables or _SIX_TABLES,
        "landing_dataset": "p.synthetic_data",
        "run_id": "set2",
        "common_args": {"num_rows": "10"},
    }


def test_plan_waves_groups_independent_tables():
    waves = _mod.plan_tableset_waves(_config(), _SIX)
    level_tables = [
        [a for cmd in level for a in cmd if a.startswith("--reference_table=")]
        for level in waves
    ]
    assert level_tables[0] == [
        "--reference_table=p.ds.a_t", "--reference_table=p.ds.c_t"
    ]
    assert level_tables[1] == [
        "--reference_table=p.ds.b_t", "--reference_table=p.ds.d_t"
    ]


def test_plan_waves_disabled_fk_is_one_level_with_flag():
    waves = _mod.plan_tableset_waves(
        _config(), _SIX, generate_fk_relationships=False
    )
    assert len(waves) == 1 and len(waves[0]) == 4
    for cmd in waves[0]:
        assert "--generate_fk_relationships=false" in cmd
        assert not any(a.startswith("--fk_parent_landing") for a in cmd)


def test_argv_carries_fk_flag_enabled():
    waves = _mod.plan_tableset_waves(_config(), _SIX)
    for level in waves:
        for cmd in level:
            assert "--generate_fk_relationships=true" in cmd


def test_informational_only_child_gets_no_parent_landing():
    contracts = {
        "p.ds.solo": parse_relational_contract(
            '{"sdfb": 1, "fk": [{"cols": ["PK_X"], "ref": "ds.a_t", '
            '"ref_cols": ["PK_X"], "informational": true}]}'
        ),
    }
    waves = _mod.plan_tableset_waves(_config(["p.ds.solo"]), contracts)
    (cmd,) = waves[0]
    assert not any(a.startswith("--fk_parent_landing") for a in cmd)


def test_emit_trigger_configs_ordered_files(tmp_path):
    paths = _mod.emit_trigger_configs(_config(), _SIX, tmp_path)
    names = [p.name for p in paths]
    assert names == [
        "00_a_t.json", "01_c_t.json", "02_b_t.json", "03_d_t.json"
    ]
    import json as _json
    child = _json.loads((tmp_path / "02_b_t.json").read_text())
    assert child["table_fqn"] == "p.ds.b_t"
    assert child["fk_parent_landing"] == "p.synthetic_data"
    assert child["generate_fk_relationships"] == "true"
    assert child["num_rows"] == "10"
    parent = _json.loads((tmp_path / "00_a_t.json").read_text())
    assert "fk_parent_landing" not in parent


def test_run_waves_parallel_and_abort(tmp_path):
    import sys as _sys
    ok = [_sys.executable, "-c",
          f"open(r'{tmp_path}/ok', 'a').write('x')"]
    fail = [_sys.executable, "-c", "raise SystemExit(3)"]
    never = [_sys.executable, "-c",
             f"open(r'{tmp_path}/never', 'w').write('x')"]
    rc = _mod.run_waves([[ok, fail], [never]], max_parallel=2)
    assert rc == 3
    assert (tmp_path / "ok").exists()
    assert not (tmp_path / "never").exists()


def test_write_model_artifact(tmp_path):
    from sdfb_core.contracts.fk_model import build_fk_model
    model = build_fk_model(_SIX_TABLES, _SIX)
    path = _mod.write_model_artifact(model, tmp_path)
    assert path.parent == tmp_path
    assert path.suffix == ".mmd"
    assert path.read_text().startswith("flowchart")
    # idempotent: same model, same file
    assert _mod.write_model_artifact(model, tmp_path) == path
