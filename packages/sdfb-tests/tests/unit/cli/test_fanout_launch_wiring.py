"""ADR 0036 launcher: roles -> measurement -> derived rows -> config."""

from __future__ import annotations

import pytest
from sdfb_beam.cli.run_pipeline import (
    in_set_parent_edges,
    resolve_driven_uniqueness_mode,
    resolve_fanout,
    resolve_table_rows,
)
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relationships import RelationshipRegistry

_MODEL = """
model: kw
tables:
  B_TABLE:
    pk: [D_COL_001]
  C_TABLE:
    pk: [D_COL_001, C_COL_002]
    fk:
      - cols: [D_COL_001, D_COL_024]
        ref: B_TABLE
        ref_cols: [D_COL_001, D_COL_024]
  A_TABLE:
    pk: [D_COL_024, X]
    fk:
      - cols: [D_COL_024]
        ref: C_TABLE
        ref_cols: [D_COL_024]
        drives: true
      - cols: [D_COL_024]
        ref: B_TABLE
        ref_cols: [D_COL_024]
"""
_REG = RelationshipRegistry.from_sources([("config/relationships/kw.yaml", _MODEL)])
_SCHEMA = TableSchema.model_validate(
    {"table_info": {"table_id": "p.src.C_TABLE"},
     "schema": [{"name": n, "type": "STRING", "mode": "REQUIRED"}
                for n in ("D_COL_001", "D_COL_024", "C_COL_002")]}
)
_ROWS = [{"D_COL_001": f"K{i}", "D_COL_024": "A", "C_COL_002": "xy"[i % 2]} for i in range(40)]


class _Store:
    def __init__(self):
        self.saved = {}

    def get(self, t, cols, sha):
        return self.saved.get((t, cols, sha))

    def put(self, t, cols, sha, payload):
        self.saved[(t, cols, sha)] = payload


def _measure(**kw):
    return {"histogram": {"0": 1, "2": 1}, "cells": {"cols": ["C_COL_002"], "rows": [["x"], ["y"]],
            "counts": [1.0, 1.0]}, "parents": 2, "children": 2}


def test_resolve_fanout_measures_the_driving_edge_and_caches(monkeypatch):
    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "measure_fanout", _measure)
    store = _Store()
    payload, roles = resolve_fanout(
        _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=_ROWS, table_schema=_SCHEMA, stats_store=store, bq_client=object(),
    )
    assert payload["driving_cols"] == ["D_COL_001", "D_COL_024"]
    assert payload["exact_cells"] is True and payload["cells"]["cols"] == ["C_COL_002"]
    assert list(roles.values()) == ["driving"]
    assert store.saved  # cached under the model sha
    # second call hits the cache: measure_fanout must not run
    monkeypatch.setattr(rp, "measure_fanout", lambda **kw: (_ for _ in ()).throw(AssertionError("measured twice")))
    again, _ = resolve_fanout(
        _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=_ROWS, table_schema=_SCHEMA, stats_store=store, bq_client=object(),
    )
    assert again == payload


def test_root_has_no_fanout():
    payload, roles = resolve_fanout(
        _REG, "proj.synthetic_data.B_TABLE", "proj.src.B_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
        reference_rows=[], table_schema=_SCHEMA, stats_store=None, bq_client=None,
    )
    assert payload is None and roles == {}


def test_edges_get_their_modes():
    roles = _REG.edge_roles("A_TABLE")
    edges = in_set_parent_edges(
        _REG, "proj.synthetic_data.A_TABLE",
        in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"}, key_sample_caps={},
        edge_roles=roles, keys_per_batch=250,
    )
    assert [(e.parent_landing.rsplit(".", 1)[-1], e.mode) for e in edges] == [
        ("C_TABLE", "fanout"), ("B_TABLE", "implied")]
    assert edges[0].keys_per_batch == 250


def test_driven_uniqueness_mode():
    assert resolve_driven_uniqueness_mode("streaming", driven=True, identity_cols=()) == "streaming"
    assert resolve_driven_uniqueness_mode("streaming", driven=True, identity_cols=("X",)) == "exact"
    assert resolve_driven_uniqueness_mode("streaming", driven=False, identity_cols=()) is None


def test_driven_child_without_derived_rows_stops():
    with pytest.raises(SystemExit, match="preflight P4"):
        resolve_table_rows(
            "proj.synthetic_data.C_TABLE", driven=True, derived_rows=None, launch_rows=1000,
        )
    assert resolve_table_rows(
        "proj.synthetic_data.C_TABLE", driven=True, derived_rows=40, launch_rows=1000,
    ) == 40
    assert resolve_table_rows(
        "proj.synthetic_data.C_TABLE", driven=False, derived_rows=None, launch_rows=1000,
    ) == 1000


def test_resolve_fanout_stops_on_unknown_driving_columns(monkeypatch):
    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(
        rp, "measure_fanout",
        lambda **kw: (_ for _ in ()).throw(AssertionError("measure_fanout must not run")),
    )
    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.src.C_TABLE"},
         "schema": [{"name": n, "type": "STRING", "mode": "REQUIRED"}
                    for n in ("D_COL_001", "C_COL_002")]}  # D_COL_024 missing
    )
    with pytest.raises(SystemExit, match="preflight P2"):
        resolve_fanout(
            _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
            in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
            reference_rows=_ROWS, table_schema=schema, stats_store=None, bq_client=object(),
        )


def test_resolve_fanout_reports_a_measurement_failure_as_a_preflight_stop(monkeypatch):
    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(
        rp, "measure_fanout",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(SystemExit, match="fan-out measurement"):
        resolve_fanout(
            _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
            in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
            reference_rows=_ROWS, table_schema=_SCHEMA, stats_store=None, bq_client=object(),
        )


class _BrokenStore:
    """A cache whose table does not exist: every call raises, as the
    BigQuery client does with NotFound."""

    def get(self, t, cols, sha):
        raise RuntimeError("404 Not found: Table p:synthetic_data_quality.fk_fanout_stats")

    def put(self, t, cols, sha, payload):
        raise RuntimeError("404 Not found: Table p:synthetic_data_quality.fk_fanout_stats")


def test_a_missing_cache_table_is_optional(monkeypatch, caplog):
    """The fk_fanout_stats table is a convenience, not a prerequisite: a
    cache that cannot be read or written warns once and the launch
    measures without it (2026-09-10 operator ask)."""
    import logging

    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "measure_fanout", _measure)
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        payload, _roles = resolve_fanout(
            _REG, "proj.synthetic_data.C_TABLE", "proj.src.C_TABLE",
            in_set_names={"B_TABLE", "C_TABLE", "A_TABLE"},
            reference_rows=_ROWS, table_schema=_SCHEMA, stats_store=_BrokenStore(),
            bq_client=object(),
        )
    assert payload is not None and payload["driving_cols"] == ["D_COL_001", "D_COL_024"]
    assert "name=fk_fanout_cache_unavailable" in caplog.text
    assert "name=fk_fanout_measured" in caplog.text and "source=measured" in caplog.text


# --- ADR 0037: the flag, the plan payload, the WARNING milestones -----

_DIAMOND = """
model: diamond
tables:
  TOP_TABLE:
    pk: [T]
  LEFT_TABLE:
    pk: [T, L]
    fk:
      - cols: [T]
        ref: TOP_TABLE
        ref_cols: [T]
  RIGHT_TABLE:
    pk: [T, R]
    fk:
      - cols: [T]
        ref: TOP_TABLE
        ref_cols: [T]
  BOTTOM_TABLE:
    pk: [T, L, R]
    fk:
      - cols: [T, L]
        ref: LEFT_TABLE
        ref_cols: [T, L]
      - cols: [T, R]
        ref: RIGHT_TABLE
        ref_cols: [T, R]
      - cols: [T]
        ref: ds.EXT_TABLE
        ref_cols: [T]
"""
_DIAMOND_REG = RelationshipRegistry.from_sources(
    [("config/relationships/diamond.yaml", _DIAMOND)]
)
_DIAMOND_IN_SET = frozenset(
    f"p.land.{t}"
    for t in ("TOP_TABLE", "LEFT_TABLE", "RIGHT_TABLE", "BOTTOM_TABLE")
)


def _diamond_schema(r_mode: str = "NULLABLE") -> TableSchema:
    return TableSchema.model_validate(
        {"table_info": {"table_id": "p.land.BOTTOM_TABLE"},
         "schema": [{"name": "T", "type": "STRING", "mode": "REQUIRED"},
                    {"name": "L", "type": "STRING", "mode": "REQUIRED"},
                    {"name": "R", "type": "STRING", "mode": r_mode}]}
)


_DIAMOND_ROWS = [
    {"T": f"t{i % 2}", "L": f"l{i // 2}", "R": f"r{i % 2}"} for i in range(40)
]


def _measure_one_to_one(**kw):
    """One child per parent, two PK-completing cells."""
    return {"histogram": {"1": 4}, "parents": 4, "children": 4,
            "cells": {"cols": ["R"], "rows": [["r0"], ["r1"]],
                      "counts": [0.5, 0.5]}}


def _diamond_args(landing: str, **over):
    from sdfb_beam.cli.run_pipeline import parse_args

    args, _ = parse_args([
        "--reference_table", "p.src.BOTTOM_TABLE",
        "--landing_table", landing,
        "--dlq_table", "p.dq.dlq",
        "--num_rows", "100",
        "--run_id", "adr0037",
        "--source_stats", "off",
        "--model_uri", "gs://b/models/m/v/",
    ])
    for key, value in over.items():
        setattr(args, key, value)
    return args


def test_fk_candidate_cap_is_a_flag_and_reaches_launch_config():
    from sdfb_beam.cli.run_pipeline import parse_args

    args, _ = parse_args([
        "--reference_table", "p.src.T", "--landing_table", "p.land.T",
        "--dlq_table", "p.dq.dlq", "--num_rows", "10", "--run_id", "r",
        "--model_uri", "gs://b/models/m/v/",
    ])
    assert args.fk_candidate_cap == 64
    # launch_config logs every non-private arg verbatim.
    logged = {
        k: v for k, v in sorted(vars(args).items())
        if not k.startswith("_") and k != "model_client"
    }
    assert logged["fk_candidate_cap"] == 64
    over, _ = parse_args([
        "--reference_table", "p.src.T", "--landing_table", "p.land.T",
        "--dlq_table", "p.dq.dlq", "--num_rows", "10", "--run_id", "r",
        "--model_uri", "gs://b/models/m/v/", "--fk_candidate_cap", "16",
    ])
    assert over.fk_candidate_cap == 16


def test_the_fanout_payload_carries_the_conditional_edges_and_the_cap(monkeypatch):
    """Ruling 1: BOTH keys land on the payload the workers read."""
    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "measure_fanout", _measure_one_to_one)
    args = _diamond_args("p.land.BOTTOM_TABLE", fk_candidate_cap=32)
    payload, roles = rp._resolve_table_fanout(
        args, _diamond_schema(), _DIAMOND_REG,
        {"TOP_TABLE", "LEFT_TABLE", "RIGHT_TABLE", "BOTTOM_TABLE"},
        _DIAMOND_ROWS,
    )
    assert payload["conditional"] == [
        {"id": "(T,R)->RIGHT_TABLE", "cols": ["R"], "nullable": True}
    ]
    assert payload["candidate_cap"] == 32
    assert sorted(roles.values()) == ["conditional", "driving", "external"]


def test_a_root_table_gets_no_conditional_payload(monkeypatch):
    import sdfb_beam.cli.run_pipeline as rp

    payload, _roles = rp._resolve_table_fanout(
        _diamond_args("p.land.TOP_TABLE"), _diamond_schema(), _DIAMOND_REG,
        {"TOP_TABLE", "LEFT_TABLE", "RIGHT_TABLE", "BOTTOM_TABLE"},
        _DIAMOND_ROWS,
    )
    assert payload is None


def test_the_launcher_names_a_defaulted_driving_edge_and_an_external_overlap(
    monkeypatch, caplog
):
    import logging

    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "load_reference_rows", lambda **kw: _DIAMOND_ROWS)
    monkeypatch.setattr(rp, "measure_fanout", _measure_one_to_one)
    monkeypatch.setattr(
        rp, "load_fk_key_pools",
        lambda fks, landing: [
            {"cols": list(fk.cols), "keys": [tuple("t0" for _ in fk.cols)]}
            for fk in fks
        ],
    )
    args = _diamond_args("p.land.BOTTOM_TABLE")
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        result = rp._load_reference_and_preflight(
            args, _diamond_schema(), _DIAMOND_REG,
            in_set_landing=_DIAMOND_IN_SET,
        )
    fanout, edge_roles = result[5], result[6]
    assert fanout["conditional"] == [
        {"id": "(T,R)->RIGHT_TABLE", "cols": ["R"], "nullable": True}
    ]
    assert fanout["candidate_cap"] == 64
    assert sorted(edge_roles.values()) == ["conditional", "driving", "external"]

    lines = caplog.text.splitlines()
    defaulted = [ln for ln in lines if "name=fk_driving_edge_defaulted" in ln]
    assert len(defaulted) == 1
    assert "edge='(T,L)->LEFT_TABLE'" in defaulted[0]
    assert "mark drives: true to choose" in defaulted[0]
    external = [ln for ln in lines if "name=fk_edge_overlap_external" in ln]
    assert len(external) == 1
    assert "edge='(T)->ds.EXT_TABLE'" in external[0] and "overlap=T" in external[0]
    # Ruling 2: the existing per-edge role milestone gains overlap=.
    conditional = [
        ln for ln in lines
        if "name=fk_edge_role" in ln and "role=conditional" in ln
    ]
    assert len(conditional) == 1 and "overlap=T" in conditional[0]
    assert not any(
        "name=fk_edge_role" in ln and "role=driving" in ln and "overlap=" in ln
        for ln in lines
    )


def test_a_single_edge_child_is_not_warned_about(monkeypatch, caplog):
    import logging

    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "load_reference_rows", lambda **kw: _DIAMOND_ROWS)
    monkeypatch.setattr(rp, "measure_fanout", _measure_one_to_one)
    schema = TableSchema.model_validate(
        {"table_info": {"table_id": "p.land.LEFT_TABLE"},
         "schema": [{"name": "T", "type": "STRING", "mode": "REQUIRED"},
                    {"name": "L", "type": "STRING", "mode": "REQUIRED"}]}
    )
    args = _diamond_args("p.land.LEFT_TABLE")
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        result = rp._load_reference_and_preflight(
            args, schema, _DIAMOND_REG, in_set_landing=_DIAMOND_IN_SET,
        )
    assert result[5]["conditional"] == []
    assert "name=fk_driving_edge_defaulted" not in caplog.text
    assert "name=fk_edge_overlap_external" not in caplog.text


def test_fk_edge_metadata_names_the_dag_path_of_every_in_set_edge():
    """Task 8 reads `mode`/`overlap` off these dicts in the worker log."""
    import sdfb_beam.cli.run_pipeline as rp

    edges = rp.fk_edge_metadata(
        _DIAMOND_REG,
        "p.land.BOTTOM_TABLE",
        _DIAMOND_REG.relations("BOTTOM_TABLE"),
        "p.land",
        in_set_names={"TOP_TABLE", "LEFT_TABLE", "RIGHT_TABLE", "BOTTOM_TABLE"},
        edge_roles=_DIAMOND_REG.edge_roles("BOTTOM_TABLE"),
    )
    assert [(e["cols"], e.get("mode"), e.get("overlap")) for e in edges] == [
        (["T", "L"], "fanout", None),
        (["T", "R"], "conditional", ["T"]),
        (["T"], None, None),  # external: no mode key, the worker defaults
    ]
    assert edges[0]["parent_landing"] == "p.land.LEFT_TABLE"


def test_fk_edge_metadata_without_roles_is_todays_dict():
    import sdfb_beam.cli.run_pipeline as rp

    edges = rp.fk_edge_metadata(
        _DIAMOND_REG, "p.land.BOTTOM_TABLE",
        _DIAMOND_REG.relations("BOTTOM_TABLE"), "",
        in_set_names=set(), edge_roles={},
    )
    assert [set(e) for e in edges] == [
        {"cols", "ref", "ref_cols", "enforced", "parent_landing"}
    ] * 3


# --- ADR 0037 §6: PK members another edge supplies ---------------------

def test_resolve_fanout_does_not_measure_cells_over_a_conditional_member(
    monkeypatch,
):
    """`R` sits in BOTTOM_TABLE's PK but the conditional edge supplies
    it, so the launcher must not ask BigQuery for a cell table over it —
    the cells are the members nothing else fills."""
    import sdfb_beam.cli.run_pipeline as rp

    seen: dict = {}

    def _spy(**kw):
        seen.update(kw)
        return _measure_one_to_one(**kw)

    monkeypatch.setattr(rp, "measure_fanout", _spy)
    payload, _roles = resolve_fanout(
        _DIAMOND_REG, "p.land.BOTTOM_TABLE", "p.src.BOTTOM_TABLE",
        in_set_names={"TOP_TABLE", "LEFT_TABLE", "RIGHT_TABLE",
                      "BOTTOM_TABLE"},
        reference_rows=_DIAMOND_ROWS, table_schema=_diamond_schema(),
        stats_store=None, bq_client=object(),
    )
    assert seen["cell_cols"] == ()
    assert payload["exact_cells"] is True


_STAR37 = """
model: star37
tables:
  A_TABLE:
    pk: [A_ID]
  B_TABLE:
    pk: [B_ID]
  F_TABLE:
    pk: [A_ID, B_ID, SEQ]
    fk:
      - cols: [A_ID]
        ref: A_TABLE
        ref_cols: [A_ID]
        drives: true
      - cols: [B_ID]
        ref: B_TABLE
        ref_cols: [B_ID]
"""
_STAR37_REG = RelationshipRegistry.from_sources(
    [("config/relationships/star37.yaml", _STAR37)]
)
_STAR37_IN_SET = frozenset(
    f"p.land.{t}" for t in ("A_TABLE", "B_TABLE", "F_TABLE")
)
_STAR37_SCHEMA = TableSchema.model_validate(
    {"table_info": {"table_id": "p.land.F_TABLE"},
     "schema": [{"name": "A_ID", "type": "STRING", "mode": "REQUIRED"},
                {"name": "B_ID", "type": "STRING", "mode": "REQUIRED"},
                {"name": "SEQ", "type": "INT64", "mode": "REQUIRED"}]}
)
_STAR37_ROWS = [
    {"A_ID": f"A1B2{i:020X}", "B_ID": f"B{i % 6}", "SEQ": i} for i in range(40)
]


def test_an_independent_pk_member_gets_a_sized_key_pool(monkeypatch):
    """The cap P4 counted as a per-key factor is the SAME number the
    composer broadcasts — it travels back out on `PreflightResult` and
    into `in_set_parent_edges`, exactly as the random-draw path's caps
    do (ADR 0035)."""
    import sdfb_beam.cli.run_pipeline as rp
    from sdfb_core.engines.pk_capacity import FK_KEY_SAMPLE_FLOOR

    monkeypatch.setattr(rp, "load_reference_rows", lambda **kw: _STAR37_ROWS)
    monkeypatch.setattr(rp, "measure_fanout", lambda **kw: {
        "histogram": {"2": 4}, "parents": 4, "children": 8, "cells": None})
    args = _diamond_args("p.land.F_TABLE", reference_table="p.src.F_TABLE")
    pf = rp._load_reference_and_preflight(
        args, _STAR37_SCHEMA, _STAR37_REG, in_set_landing=_STAR37_IN_SET,
    )[1]
    # --num_rows 100 is the upper bound on what this child lands (its
    # DERIVED count is the output of this very preflight), and B_TABLE
    # lands at most that, so the sized pool is bounded by the parent.
    assert pf.fk_key_sample_caps == {("B_ID",): 100}
    edges = in_set_parent_edges(
        _STAR37_REG, "p.land.F_TABLE",
        in_set_names={"A_TABLE", "B_TABLE", "F_TABLE"},
        key_sample_caps=pf.fk_key_sample_caps,
        edge_roles=_STAR37_REG.edge_roles("F_TABLE"),
    )
    assert [(e.mode, e.key_sample_cap) for e in edges] == [
        ("fanout", FK_KEY_SAMPLE_FLOOR), ("side_input", 100)]


# --- ADR 0037 fix round 1: metadata matching, cap validation, nullability ---

# An enforced composite edge and a DOCUMENTED prefix edge to the SAME
# parent: the declared `(T)` must never inherit the enforced `(T,R)`'s mode.
_PREFIX = """
model: prefix
tables:
  P_TABLE:
    pk: [T, R]
  CH_TABLE:
    pk: [T, R, S]
    fk:
      - cols: [T, R]
        ref: P_TABLE
        ref_cols: [T, R]
      - cols: [T]
        ref: P_TABLE
        ref_cols: [T]
        enforced: false
"""
_PREFIX_REG = RelationshipRegistry.from_sources(
    [("config/relationships/prefix.yaml", _PREFIX)]
)


def test_a_documented_prefix_edge_never_inherits_the_enforced_edges_mode():
    """`fk_edges` says which DAG path each edge took — a documented edge
    took none, so it must carry no mode at all (it renders as the
    worker's `side_input` default)."""
    import sdfb_beam.cli.run_pipeline as rp

    edges = rp.fk_edge_metadata(
        _PREFIX_REG,
        "p.land.CH_TABLE",
        _PREFIX_REG.relations("CH_TABLE"),
        "p.land",
        in_set_names={"P_TABLE", "CH_TABLE"},
        edge_roles=_PREFIX_REG.edge_roles("CH_TABLE"),
    )
    assert [(e["cols"], e["enforced"], e.get("mode")) for e in edges] == [
        (["T", "R"], True, "fanout"),
        (["T"], False, None),
    ]


def test_fk_candidate_cap_below_one_stops_the_launch():
    """0 divided the keys_per_batch bound; a negative cap emptied every
    candidate list silently."""
    from sdfb_beam.cli.run_pipeline import parse_args

    base = [
        "--reference_table", "p.src.T", "--landing_table", "p.land.T",
        "--dlq_table", "p.dq.dlq", "--num_rows", "10", "--run_id", "r",
        "--model_uri", "gs://b/models/m/v/",
    ]
    for bad in ("0", "-3"):
        with pytest.raises(SystemExit, match=r"--fk_candidate_cap"):
            parse_args([*base, "--fk_candidate_cap", bad])
    ok, _ = parse_args([*base, "--fk_candidate_cap", "1"])
    assert ok.fk_candidate_cap == 1


def _landing_schema(r_mode: str) -> TableSchema:
    return TableSchema.model_validate(
        {"table_info": {"table_id": "p.land.BOTTOM_TABLE"},
         "schema": [{"name": "T", "type": "STRING", "mode": "REQUIRED"},
                    {"name": "L", "type": "STRING", "mode": "REQUIRED"},
                    {"name": "R", "type": "STRING", "mode": r_mode}]}
    )


def test_resolve_schemas_returns_the_landing_schema_alongside(monkeypatch):
    import sdfb_beam.cli.run_pipeline as rp

    source, landing = _diamond_schema("NULLABLE"), _landing_schema("REQUIRED")
    monkeypatch.setattr(
        rp, "extract_table_schema",
        lambda fqn: source if fqn == "p.src.B" else landing,
    )
    generation, target = rp.resolve_schemas("", "p.src.B", "p.land.B")
    assert [c.mode for c in generation.columns] == ["REQUIRED", "REQUIRED", "NULLABLE"]
    assert target is landing
    # The one-schema wrapper every other call site uses is unchanged.
    assert rp.resolve_table_schema("", "p.src.B", "p.land.B").columns == generation.columns


def test_an_unreachable_landing_table_surfaces_no_schema(monkeypatch, caplog):
    import logging

    import sdfb_beam.cli.run_pipeline as rp

    source = _diamond_schema("NULLABLE")

    def _extract(fqn: str):
        if fqn == "p.src.B":
            return source
        raise RuntimeError("landing table not found")

    monkeypatch.setattr(rp, "extract_table_schema", _extract)
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        _generation, target = rp.resolve_schemas("", "p.src.B", "p.land.B")
    assert target is None


def test_nullable_reads_the_landing_modes_not_the_source(monkeypatch):
    """Design §4 ruling B: the LANDING schema decides. The source's modes
    mirror the lake table and are irrelevant to what the sink accepts."""
    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "measure_fanout", _measure_one_to_one)
    names = {"TOP_TABLE", "LEFT_TABLE", "RIGHT_TABLE", "BOTTOM_TABLE"}
    args = _diamond_args("p.land.BOTTOM_TABLE")
    # source R NULLABLE, landing R REQUIRED -> not nullable
    payload, _roles = rp._resolve_table_fanout(
        args, _diamond_schema("NULLABLE"), _DIAMOND_REG, names, _DIAMOND_ROWS,
        landing_schema=_landing_schema("REQUIRED"),
    )
    assert payload["conditional"] == [
        {"id": "(T,R)->RIGHT_TABLE", "cols": ["R"], "nullable": False}
    ]
    # source R REQUIRED, landing R NULLABLE -> nullable
    payload, _roles = rp._resolve_table_fanout(
        args, _diamond_schema("REQUIRED"), _DIAMOND_REG, names, _DIAMOND_ROWS,
        landing_schema=_landing_schema("NULLABLE"),
    )
    assert payload["conditional"] == [
        {"id": "(T,R)->RIGHT_TABLE", "cols": ["R"], "nullable": True}
    ]


def test_no_landing_schema_falls_back_to_the_source_and_says_so(
    monkeypatch, caplog
):
    import logging

    import sdfb_beam.cli.run_pipeline as rp

    monkeypatch.setattr(rp, "measure_fanout", _measure_one_to_one)
    args = _diamond_args("p.land.BOTTOM_TABLE")
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        payload, _roles = rp._resolve_table_fanout(
            args, _diamond_schema("NULLABLE"), _DIAMOND_REG,
            {"TOP_TABLE", "LEFT_TABLE", "RIGHT_TABLE", "BOTTOM_TABLE"},
            _DIAMOND_ROWS, landing_schema=None,
        )
    assert payload["conditional"] == [
        {"id": "(T,R)->RIGHT_TABLE", "cols": ["R"], "nullable": True}  # the SOURCE's mode
    ]
    fallback = [
        ln for ln in caplog.text.splitlines()
        if "name=fk_nullable_from_source" in ln
    ]
    assert len(fallback) == 1
    assert "edge='(T,R)->RIGHT_TABLE'" in fallback[0]
    assert "table=p.land.BOTTOM_TABLE" in fallback[0]


def test_nullability_schema_prefers_the_landing_one():
    import sdfb_beam.cli.run_pipeline as rp

    source, landing = _diamond_schema("NULLABLE"), _landing_schema("REQUIRED")
    assert rp.nullability_schema(landing, source) is landing
    assert rp.nullability_schema(None, source) is source


_PARTIAL = """
model: partial
tables:
  A_TABLE:
    pk: [A_ID]
  B_TABLE:
    pk: [B_ID, X]
  F_TABLE:
    pk: [A_ID, B_ID]
    fk:
      - cols: [A_ID]
        ref: A_TABLE
        ref_cols: [A_ID]
        drives: true
      - cols: [B_ID, X]
        ref: B_TABLE
        ref_cols: [B_ID, X]
"""
_PARTIAL_REG = RelationshipRegistry.from_sources(
    [("config/relationships/partial.yaml", _PARTIAL)]
)
_PARTIAL_SCHEMA = TableSchema.model_validate(
    {"table_info": {"table_id": "p.land.F_TABLE"},
     "schema": [{"name": n, "type": "STRING", "mode": "REQUIRED"}
                for n in ("A_ID", "B_ID", "X")]}
)
_PARTIAL_ROWS = [
    {"A_ID": f"A1B2{i:020X}", "B_ID": f"B{i % 6}", "X": f"X{i % 4}"}
    for i in range(40)
]


def test_measure_and_check_agree_on_a_partially_contained_edge(monkeypatch):
    """Ruling 12: `resolve_fanout`'s `known` and `_check_driven_pk`'s are
    the SAME set — the launcher asks for no cell table over `B_ID`, and
    P4 must not then demand one (review finding B-1)."""
    import sdfb_beam.cli.run_pipeline as rp
    from sdfb_beam.cli.preflight import edge_supplied_members

    seen: dict = {}

    def _spy(**kw):
        seen.update(kw)
        return {"histogram": {"1": 4}, "parents": 4, "children": 4,
                "cells": None}

    monkeypatch.setattr(rp, "measure_fanout", _spy)
    payload, roles = resolve_fanout(
        _PARTIAL_REG, "p.land.F_TABLE", "p.src.F_TABLE",
        in_set_names={"A_TABLE", "B_TABLE", "F_TABLE"},
        reference_rows=_PARTIAL_ROWS, table_schema=_PARTIAL_SCHEMA,
        stats_store=None, bq_client=object(),
    )
    assert seen["cell_cols"] == ()
    assert payload["exact_cells"] is True
    # The very set P4 will use, from the one shared helper.
    assert edge_supplied_members(
        ("A_ID", "B_ID"), roles,
        rp.conditional_rest_of(_PARTIAL_REG, "p.land.F_TABLE", roles),
    ).known == ("B_ID", "X")


def test_a_high_fanout_child_gets_a_pool_sized_for_its_derived_rows(
    monkeypatch,
):
    """Finding B-2 end to end: --num_rows 100 with a mean fan-out of 30
    lands 3,000 rows, and the broadcast pool is sized for those."""
    import sdfb_beam.cli.run_pipeline as rp
    from sdfb_core.engines.pk_capacity import fk_key_sample_cap

    monkeypatch.setattr(rp, "load_reference_rows", lambda **kw: _STAR37_ROWS)
    monkeypatch.setattr(rp, "measure_fanout", lambda **kw: {
        "histogram": {"30": 4}, "parents": 4, "children": 120,
        "cells": None})
    args = _diamond_args("p.land.F_TABLE", reference_table="p.src.F_TABLE")
    # The relational runner carries the already-resolved parent counts.
    args._rows_by_landing = {"p.land.A_TABLE": 10_000,
                             "p.land.B_TABLE": 5_000_000}
    pf = rp._load_reference_and_preflight(
        args, _STAR37_SCHEMA, _STAR37_REG, in_set_landing=_STAR37_IN_SET,
    )[1]
    assert pf.derived_rows == 300_000  # 10k parents x mean 30
    assert pf.fk_key_sample_caps == {
        ("B_ID",): fk_key_sample_cap(300_000, 1)
    }
    # --num_rows 100 would have sized the same pool at the ADR 0035 floor.
    assert fk_key_sample_cap(args.num_rows, 1) != fk_key_sample_cap(300_000, 1)
