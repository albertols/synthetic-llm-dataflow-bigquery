"""ADR 0038 — a model conflict PROVEN by a full-source measurement
adjusts the effective model and shouts; it no longer stops the launch.

The 2026-09-12 launch (…-6058498192553696658) stopped all five tables
because E_TABLE's declared `pk:` is its own driving FK edge and the
source repeats that value (median 2 rows, up to 13). The source is the
authority for what the data IS, so the PK is dropped from the effective
model, the fan-out histogram is left untouched, and the landing table
reproduces the source's key-repeat distribution by construction.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sdfb_beam.cli.preflight import preflight
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.model_adjustment import (
    REPEAT_SHARE_TOLERANCE,
    ModelAdjustment,
    adjusted_model_yaml,
    adjustment_banner,
    landing_repeat_share,
    source_repeat_share,
)
from sdfb_core.contracts.relationships import (
    RelationshipRegistry,
    parse_relationship_model,
)

# E_TABLE's shape: the declared PK IS the driving edge, one column.
_ONE_TO_ONE = """
model: m2
description: the E_TABLE shape
tables:
  parent:
    pk: [PID]
  child1to1:
    pk: [PID]
    fk:
      - cols: [PID]
        ref: parent
        ref_cols: [PID]
"""


def _reg(model: str = _ONE_TO_ONE) -> RelationshipRegistry:
    return RelationshipRegistry.from_sources(
        [("config/relationships/m2.yaml", model)]
    )


def _schema() -> TableSchema:
    return TableSchema.model_validate({
        "table_info": {"table_id": "p.d.child1to1"},
        "schema": [{"name": "PID", "type": "STRING", "mode": "REQUIRED"}],
    })


def _fanout(histogram: dict) -> dict:
    return {"driving_cols": ["PID"], "histogram": histogram,
            "cells": None, "exact_cells": True}


def _preflight(histogram: dict, **kw):
    reg = _reg()
    return preflight(
        _schema(), (), (), [], relations=reg.relations("child1to1"),
        num_rows=1_000, fk_parent_rows={"parent": 1_000},
        blocker_failure_ratio=0.2, fanout=_fanout(histogram),
        edge_roles=reg.edge_roles("child1to1"), **kw,
    )


# --- A. the adjustment ------------------------------------------------


def test_one_to_one_conflict_adjusts_instead_of_raising():
    """The declared PK equals the driving edge and the source fans out
    to 2 — that used to be `[preflight P4] … equals the driving edge
    exactly`. Now the PK is dropped and the table still generates."""
    result = _preflight({"0": 10, "2": 5})
    assert result.pk_cols == ()
    (adjustment,) = result.adjustments
    assert adjustment.change == "pk_dropped"
    assert adjustment.declared_pk == ("PID",)
    assert "PID" in adjustment.declared


def test_the_adjusted_table_keeps_its_derived_row_count():
    """The fan-out histogram is UNTOUCHED: dropping the PK must not cap,
    narrow or re-derive the row count (mean k = 10/15 = 0.6667)."""
    result = _preflight({"0": 10, "2": 5})
    assert result.derived_rows == round(1_000 * 10 / 15)


def test_on_model_conflict_stop_restores_the_raise():
    with pytest.raises(
        SystemExit, match=r"preflight P4.*equals the driving edge exactly"
    ):
        _preflight({"0": 10, "2": 5}, on_model_conflict="stop")


def test_a_table_with_no_conflict_is_untouched():
    result = _preflight({"0": 10, "1": 5})
    assert result.pk_cols == ("PID",)
    assert result.adjustments == ()


# --- C. the source repeat share --------------------------------------


def test_source_repeat_share_over_a_known_histogram():
    # 9 key values carrying 24 children -> 15 of 24 rows repeat a key.
    assert source_repeat_share(
        {"0": 10, "1": 5, "2": 3, "13": 1}
    ) == pytest.approx(1 - 9 / 24)


def test_source_repeat_share_matches_the_e_table_launch():
    """key_values=583,134 over children=1,172,025 (edge (D_COL_001)->
    B_TABLE, launch 2026-09-12_14_50_30) -> 0.5025."""
    assert source_repeat_share(
        {"1": 583_134 * 2 - 1_172_025 + 0, "2": 1_172_025 - 583_134}
    ) == pytest.approx(0.5025, abs=1e-4)


def test_source_repeat_share_is_none_without_mass():
    assert source_repeat_share({"0": 10}) is None


def test_the_adjustment_carries_the_source_repeat_share():
    result = _preflight({"0": 10, "2": 5})
    (adjustment,) = result.adjustments
    assert adjustment.source_repeat_share == pytest.approx(0.5)


def test_landing_repeat_share_divides_by_the_rows_generated():
    """`valid_count` is the DISTINCT row-digest count in streaming mode,
    so the rows generated are valid + row.duplicate; pk.duplicate over
    that is the landing key-repeat share."""
    assert landing_repeat_share(
        valid_count=80, dlq_by_rule={"row.duplicate": 20, "pk.duplicate": 50}
    ) == pytest.approx(0.5)
    assert landing_repeat_share(valid_count=0, dlq_by_rule={}) is None


def test_the_tolerance_is_stated():
    assert 0 < REPEAT_SHARE_TOLERANCE <= 0.1


# --- D. the banner and the milestone ---------------------------------


def test_the_banner_names_every_field_the_operator_needs():
    adjustments = (
        ModelAdjustment(
            table="p.d.child1to1",
            change="pk_dropped",
            declared="pk [PID]",
            measured="one key value carries up to 2 rows",
            consequence="the landing table repeats the key",
            declared_pk=("PID",),
            source_repeat_share=0.5,
        ),
    )
    banner = adjustment_banner(adjustments)
    assert "MODEL ADJUSTED" in banner
    assert "p.d.child1to1" in banner
    assert "pk [PID]" in banner
    assert "up to 2 rows" in banner
    assert "50.00%" in banner


# --- E. the effective model, as YAML ---------------------------------


def test_the_emitted_yaml_parses_and_has_the_pk_removed():
    (model,) = _reg().models
    adjustments = (
        ModelAdjustment(
            table="p.d.child1to1",
            change="pk_dropped",
            declared="pk [PID]",
            measured="median 2 rows per key value",
            consequence="the landing table repeats the key",
            declared_pk=("PID",),
            source_repeat_share=0.5,
        ),
    )
    text = adjusted_model_yaml(model, adjustments)
    assert "median 2 rows per key value" in text  # the reason, as a comment
    reparsed = parse_relationship_model(text, source="emitted.yaml")
    assert reparsed.tables["child1to1"].pk == ()
    assert reparsed.tables["parent"].pk == ("PID",)  # untouched
    # The edge survives: FK enforcement is not weakened by the drop.
    assert reparsed.tables["child1to1"].fk[0].ref == "parent"


def test_the_emitted_yaml_is_the_declared_model_when_nothing_adjusted():
    text = adjusted_model_yaml(_reg().models[0], ())
    reparsed = parse_relationship_model(text, source="emitted.yaml")
    assert reparsed.tables["child1to1"].pk == ("PID",)


def test_milestone_fires_once_per_adjustment(caplog):
    from sdfb_beam.cli.run_pipeline import log_model_adjustments

    adjustments = (
        ModelAdjustment(
            table="p.d.child1to1", change="pk_dropped", declared="pk [PID]",
            measured="median 2 rows per key value",
            consequence="the landing table repeats the key",
            declared_pk=("PID",), source_repeat_share=0.5,
        ),
    )
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        log_model_adjustments(adjustments)
    assert caplog.text.count("name=model_adjusted ") == 1
    assert "change=pk_dropped" in caplog.text
    assert "name=model_adjustments " in caplog.text  # the banner header
    assert "MODEL ADJUSTED" in caplog.text


# --- F. the launcher, end to end (no BigQuery) ------------------------

_E_SHAPE = """
model: kw
tables:
  B_TABLE:
    pk: [D_COL_001]
  E_TABLE:
    pk: [D_COL_001]
    fk:
      - cols: [D_COL_001]
        ref: B_TABLE
        ref_cols: [D_COL_001]
"""
# The 2026-09-12 measurement, scaled: 3 key values carry 6 rows, one of
# them 3 times -> the source repeats 50% of its rows.
_E_MEASURED = {"histogram": {"1": 1, "2": 1, "3": 1}, "cells": None,
               "parents": 3, "children": 6}


def _e_schema() -> TableSchema:
    return TableSchema.model_validate({
        "table_info": {"table_id": "p.src.E_TABLE"},
        "schema": [{"name": "D_COL_001", "type": "STRING", "mode": "REQUIRED"},
                   {"name": "E_VAL", "type": "INT64", "mode": "REQUIRED"}],
    })


def _e_args(**over):
    from sdfb_beam.cli.run_pipeline import parse_args

    args, _ = parse_args([
        "--reference_table", "p.src.E_TABLE",
        "--landing_table", "p.land.E_TABLE",
        "--dlq_table", "p.dq.dlq", "--num_rows", "100",
        "--run_id", "adr0038", "--source_stats", "off",
        "--model_uri", "gs://b/models/m/v/",
    ])
    for key, value in over.items():
        setattr(args, key, value)
    return args


def _run_launcher(monkeypatch, **over):
    import sdfb_beam.cli.run_pipeline as rp

    reg = RelationshipRegistry.from_sources(
        [("config/relationships/kw.yaml", _E_SHAPE)]
    )
    # The 2026-09-12 sample, in miniature: 1,000 reference rows with 5
    # duplicate keys (0.5%) — P5 warns and adjusts nothing, exactly as
    # the real launch's "40 duplicates of 10,000" did. The FULL-source
    # fan-out below is the only thing that can see the repeat.
    rows = [{"D_COL_001": f"K{min(i, 995):04d}", "E_VAL": i}
            for i in range(1_000)]
    monkeypatch.setattr(rp, "load_reference_rows", lambda **kw: rows)
    monkeypatch.setattr(rp, "measure_fanout", lambda **kw: _E_MEASURED)
    args = _e_args(**over)
    args._rows_by_landing = {"p.land.B_TABLE": 1_000}
    result = rp._load_reference_and_preflight(
        args, _e_schema(), reg,
        in_set_landing=frozenset({"p.land.B_TABLE", "p.land.E_TABLE"}),
    )
    return reg, result[1], result[5]  # registry, PreflightResult, fanout


def test_the_launcher_adjusts_the_e_table_shape_and_keeps_the_fanout(
    monkeypatch,
):
    """The whole 2026-09-12 stop, on the laptop: the declared PK IS the
    driving edge and the source repeats it, so the launch adjusts."""
    _, pf, fanout = _run_launcher(monkeypatch)
    (adjustment,) = pf.adjustments
    assert pf.pk_cols == ()
    assert adjustment.declared_pk == ("D_COL_001",)
    assert adjustment.source_repeat_share == pytest.approx(0.5)
    # The histogram is untouched and the cells stop keying the child.
    assert fanout["histogram"] == _E_MEASURED["histogram"]
    assert fanout["exact_cells"] is False
    # mean k = 6/3 = 2 -> 1000 parent rows x 2
    assert pf.derived_rows == 2_000


def test_the_launcher_stop_mode_refuses_the_same_launch(monkeypatch):
    with pytest.raises(SystemExit, match=r"preflight P4"):
        _run_launcher(monkeypatch, on_model_conflict="stop")


def test_on_model_conflict_defaults_to_adjust_and_reaches_launch_config():
    args = _e_args()
    assert args.on_model_conflict == "adjust"
    logged = {
        k: v for k, v in sorted(vars(args).items())
        if not k.startswith("_") and k != "model_client"
    }
    assert logged["on_model_conflict"] == "adjust"


def test_the_effective_model_is_written_beside_the_staged_artifacts(
    monkeypatch, tmp_path, caplog
):
    from apache_beam.options.pipeline_options import PipelineOptions
    from sdfb_beam.cli.run_pipeline import emit_effective_model

    reg, pf, _ = _run_launcher(monkeypatch)
    options = PipelineOptions([
        f"--staging_location={tmp_path}", "--runner=DirectRunner",
    ])
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        (written,) = emit_effective_model(
            reg, pf.adjustments, options, "adr0038"
        )
    assert written.startswith(f"{tmp_path}/model_adjustments/")
    text = Path(written).read_text()
    reparsed = parse_relationship_model(text, source=written)
    assert reparsed.tables["E_TABLE"].pk == ()
    assert reparsed.tables["B_TABLE"].pk == ("D_COL_001",)
    assert "name=model_adjustment_model " in caplog.text
    assert written in caplog.text


def test_the_reference_sample_warns_but_never_adjusts(monkeypatch, caplog):
    """Boundary (ADR 0038 §6): the 10,000-row sample is far too weak to
    drop a key on. It warns — and on the SAME rows, with no full-source
    fan-out to read, the declared PK stays exactly where the model put
    it. Only the measurement adjusts."""
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        _, pf, _ = _run_launcher(monkeypatch)
    assert "name=preflight_pk_not_unique_in_sample" in caplog.text
    assert pf.adjustments  # ...driven by the fan-out, not by the sample

    reg = RelationshipRegistry.from_sources(
        [("config/relationships/kw.yaml", _E_SHAPE)]
    )
    rows = [{"D_COL_001": f"K{min(i, 995):04d}", "E_VAL": i}
            for i in range(1_000)]
    undriven = preflight(
        _e_schema(), (), (), rows, relations=reg.relations("E_TABLE"),
        num_rows=100,
    )
    assert undriven.pk_cols == ("D_COL_001",)
    assert undriven.adjustments == ()
