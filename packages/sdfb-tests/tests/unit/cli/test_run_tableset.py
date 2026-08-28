"""Parent-first table-set orchestration (Task 16), model-driven (ADR 0032).

The orchestrator no longer asks BigQuery what the relationships are: it
reads the same `config/relationships/` models the pipeline reads, so a
dry run on the laptop plans exactly what the launch will do.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from sdfb_core.contracts.relationships import (
    RelationshipError,
    RelationshipRegistry,
)

_SCRIPT = Path(__file__).parents[5] / "scripts" / "run_tableset.py"
_spec = importlib.util.spec_from_file_location("run_tableset", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


def _registry(text: str) -> RelationshipRegistry:
    return RelationshipRegistry.from_sources([("m.yaml", text)])


# orders -> customers, lines -> orders + customers (chain + diamond)
_CHAIN = """
model: sales
tables:
  customers:
    pk: [ID]
  orders:
    fk:
      - cols: [C0]
        ref: customers
        ref_cols: [ID]
  lines:
    fk:
      - cols: [C0]
        ref: orders
        ref_cols: [ID]
      - cols: [C1]
        ref: customers
        ref_cols: [ID]
"""

# two independent pairs: a_t <- b_t, c_t <- d_t
_TWO_PAIRS = """
model: pairs
tables:
  a_t:
    pk: [ID]
  b_t:
    fk:
      - cols: [C0]
        ref: a_t
        ref_cols: [ID]
  c_t:
    pk: [ID]
  d_t:
    fk:
      - cols: [C0]
        ref: c_t
        ref_cols: [ID]
"""

_T = ["p.ds.orders", "p.ds.customers", "p.ds.lines"]
_PAIR_TABLES = ["p.ds.a_t", "p.ds.b_t", "p.ds.c_t", "p.ds.d_t"]


def test_order_puts_parents_first_chain_and_diamond():
    order = _registry(_CHAIN).generation_order(
        ("orders", "customers", "lines")
    )
    assert order.index("customers") < order.index("orders")
    assert order.index("orders") < order.index("lines")


def test_cycle_raises_at_load():
    cyclic = (
        "model: c\ntables:\n"
        "  orders:\n    fk:\n      - cols: [A]\n        ref: customers\n"
        "        ref_cols: [A]\n"
        "  customers:\n    fk:\n      - cols: [A]\n        ref: orders\n"
        "        ref_cols: [A]\n"
    )
    with pytest.raises(RelationshipError, match="cycle"):
        _registry(cyclic)


def test_fk_outside_the_model_is_not_an_ordering_edge():
    registry = _registry(
        "model: x\ntables:\n  orders:\n    fk:\n      - cols: [A]\n"
        "        ref: other_ds.landed_elsewhere\n        ref_cols: [A]\n"
    )
    assert registry.generation_order(("orders",)) == ("orders",)


def _config(tables=None):
    return {
        "tables": tables or _PAIR_TABLES,
        "landing_dataset": "p.synthetic_data",
        "run_id": "set2",
        "common_args": {"num_rows": "10"},
    }


def test_plan_argv_fk_flag_only_for_children():
    config = {
        "tables": ["p.ds.orders", "p.ds.customers"],
        "landing_dataset": "p.synthetic_data",
        "run_id": "set1",
        "common_args": {"num_rows": "1000", "model_uri": "gs://m"},
    }
    plans = _mod.plan_tableset(config, _registry(_CHAIN))
    parent_cmd, child_cmd = plans[0], plans[1]
    assert "--reference_table=p.ds.customers" in parent_cmd
    assert not any(a.startswith("--fk_parent_landing") for a in parent_cmd)
    assert "--fk_parent_landing=p.synthetic_data" in child_cmd
    assert "--landing_table=p.synthetic_data.orders" in child_cmd
    assert "--num_rows=1000" in child_cmd


def test_every_argv_pins_the_same_model_files():
    """The child jobs must read the model the orchestrator planned from,
    or the two disagree about what is related."""
    plans = _mod.plan_tableset(_config(), _registry(_TWO_PAIRS))
    for cmd in plans:
        assert any(a.startswith("--relationships_uri=") for a in cmd)


def test_plan_waves_groups_independent_tables():
    waves = _mod.plan_tableset_waves(_config(), _registry(_TWO_PAIRS))
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
        _config(), _registry(_TWO_PAIRS), generate_fk_relationships=False
    )
    assert len(waves) == 1 and len(waves[0]) == 4
    for cmd in waves[0]:
        assert "--generate_fk_relationships=false" in cmd
        assert not any(a.startswith("--fk_parent_landing") for a in cmd)


def test_argv_carries_fk_flag_enabled():
    waves = _mod.plan_tableset_waves(_config(), _registry(_TWO_PAIRS))
    for level in waves:
        for cmd in level:
            assert "--generate_fk_relationships=true" in cmd


def test_documented_only_child_gets_no_parent_landing():
    registry = _registry(
        "model: solo\ntables:\n  a_t:\n    pk: [PK_X]\n  solo:\n    fk:\n"
        "      - cols: [PK_X]\n        ref: a_t\n        ref_cols: [PK_X]\n"
        "        enforced: false\n"
    )
    waves = _mod.plan_tableset_waves(_config(["p.ds.solo"]), registry)
    (cmd,) = waves[0]
    assert not any(a.startswith("--fk_parent_landing") for a in cmd)


def test_a_disabled_table_hands_out_no_parent_landing():
    registry = _registry(
        _TWO_PAIRS.replace("  a_t:\n    pk:", "  a_t:\n    enabled: false\n    pk:")
    )
    waves = _mod.plan_tableset_waves(_config(["p.ds.b_t"]), registry)
    (cmd,) = waves[0]
    assert not any(a.startswith("--fk_parent_landing") for a in cmd)


def test_emit_trigger_configs_ordered_files(tmp_path):
    paths = _mod.emit_trigger_configs(
        _config(), _registry(_TWO_PAIRS), tmp_path
    )
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
    registry = _registry(_TWO_PAIRS)
    path = _mod.write_model_artifact(registry, "a_t", tmp_path)
    assert path.parent == tmp_path
    assert path.suffix == ".mmd"
    assert path.read_text().startswith("flowchart")
    # idempotent: same model, same sha, same file
    assert _mod.write_model_artifact(registry, "a_t", tmp_path) == path
