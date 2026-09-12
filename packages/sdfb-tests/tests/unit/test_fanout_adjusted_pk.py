"""ADR 0038 acceptance on the laptop: a driven child whose declared PK
REPEATS in the source (E_TABLE's shape) generates anyway, copies the
source's key-repeat share, keeps its FK tuples inside its parent — and
its OWN child still generates, once per distinct parent key.

Three tables: P_TABLE (root) -> E_TABLE (driven, PK adjusted away) ->
F_TABLE (driven by E_TABLE). The measured facts are hand-written, as in
`test_fanout_three_tables.py`; there is no BigQuery here.
"""

from __future__ import annotations

import json
from pathlib import Path

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.cli.run_pipeline import (
    adjusted_fanout_payload,
    resolve_driven_uniqueness_mode,
)
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import (
    FkEdgeSpec,
    PipelineConfig,
    TableSpec,
    build_relational_pipeline,
)
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.model_adjustment import (
    REPEAT_SHARE_TOLERANCE,
    ModelAdjustment,
    source_repeat_share,
)
from sdfb_tests.fakes import FakeModelClient

_ADJUSTMENT = (
    ModelAdjustment(
        table="p.land.E_TABLE", change="pk_dropped", declared="pk [PID]",
        measured="median 2 rows per key value", consequence="repeats land",
        declared_pk=("PID",), source_repeat_share=0.5,
    ),
)


# --- the launcher seams ----------------------------------------------


def test_an_adjusted_table_is_pinned_to_streaming_even_with_identity():
    """`exact` would DIVERT the duplicates the table is supposed to
    land. An adjusted table measures instead, always."""
    assert resolve_driven_uniqueness_mode(
        "exact", driven=True, identity_cols=("ID",), adjusted=True
    ) == "streaming"
    # Unadjusted behaviour is untouched.
    assert resolve_driven_uniqueness_mode(
        "streaming", driven=True, identity_cols=("ID",), adjusted=False
    ) == "exact"
    assert resolve_driven_uniqueness_mode(
        "streaming", driven=False, identity_cols=(), adjusted=False
    ) is None


def test_an_adjusted_table_loses_cell_exactness_but_keeps_its_cells():
    """`exact_cells` means "the cells must KEY the child". With the key
    gone they must not: `joint_key_draw` caps an exact plan at
    `min(k, capacity)`, which with no cells is ONE row per parent key —
    the fan-out the table was sized from, silently discarded."""
    measured = {"driving_cols": ["PID"], "histogram": {"1": 1, "2": 1},
                "cells": {"cols": ["C"], "rows": [["a"], ["b"]],
                          "counts": [3.0, 1.0]},
                "exact_cells": True}
    adjusted = adjusted_fanout_payload(measured, _ADJUSTMENT)
    assert adjusted["exact_cells"] is False
    assert adjusted["cells"] == measured["cells"]     # the marginal stays
    assert adjusted["histogram"] == measured["histogram"]  # untouched
    assert measured["exact_cells"] is True            # caller's dict intact
    assert adjusted_fanout_payload(measured, ()) is measured


# --- the DirectRunner shape -------------------------------------------


def _schema(table: str, cols: list[tuple[str, str]]) -> TableSchema:
    return TableSchema.model_validate(
        {"table_info": {"table_id": f"p.src.{table}"},
         "schema": [{"name": n, "type": t, "mode": "REQUIRED"}
                    for n, t in cols]}
    )


def _read(prefix: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(prefix.parent.glob(prefix.name + "*")):
        rows.extend(
            json.loads(line) for line in f.read_text().splitlines() if line
        )
    return rows


# Every parent key carries 1, 2 or 3 children: 3 key values, 6 rows.
_E_HISTOGRAM = {"1": 1, "2": 1, "3": 1}


def test_an_adjusted_child_copies_the_source_repeat_share(tmp_path):
    p_schema = _schema("P_TABLE", [("PID", "STRING"), ("AMT", "INT64")])
    p_ref = [{"PID": f"P{i:05d}", "AMT": i} for i in range(200)]
    e_schema = _schema(
        "E_TABLE",
        [("PID", "STRING"), ("E_CODE", "STRING"), ("E_VAL", "INT64")],
    )
    e_ref = [{"PID": f"P{i % 30:05d}", "E_CODE": "abcde"[i % 5], "E_VAL": i}
             for i in range(60)]
    f_schema = _schema("F_TABLE", [("PID", "STRING"), ("F_VAL", "INT64")])
    f_ref = [{"PID": f"P{i % 30:05d}", "F_VAL": i} for i in range(60)]

    def cfg(schema, ref, name, **kw):
        return PipelineConfig(
            table_schema=schema, engine_name="b1_rag",
            model_client=FakeModelClient(reference_pool=ref),
            run_id=f"adj-{name}", landing_table=f"p.land.{name}",
            log_table_prefix=name, batch_size=20, **kw,
        )

    # `streaming` so every parent row lands: the child's key space is
    # the point, and an `exact` barrier would collapse rows whose only
    # non-identity column collided.
    p_cfg = cfg(p_schema, p_ref, "P_TABLE", num_rows=200,
                pk_columns=("PID",), identity_columns=("PID",),
                uniqueness_mode="streaming")
    # E_TABLE, ADJUSTED: no effective PK, the declared one kept for
    # measurement, streaming so nothing is removed, cells inexact.
    e_cfg = cfg(
        e_schema, e_ref, "E_TABLE", num_rows=120,
        pk_columns=(), pk_measure_columns=("PID",),
        uniqueness_mode="streaming",
        gate_excluded_rules=("pk.duplicate",),
        source_repeat_share=source_repeat_share(_E_HISTOGRAM),
        fanout={"driving_cols": ["PID"], "histogram": _E_HISTOGRAM,
                "cells": None, "exact_cells": False},
    )
    f_cfg = cfg(
        f_schema, f_ref, "F_TABLE", num_rows=60, pk_columns=("PID",),
        uniqueness_mode="streaming",
        fanout={"driving_cols": ["PID"], "histogram": {"1": 1},
                "cells": None, "exact_cells": False},
    )

    def sinks(name):
        return dict(landing_sink=WriteToJsonLines(str(tmp_path / name)),
                    dlq_sink=WriteToJsonLines(str(tmp_path / f"dlq_{name}")))

    specs = [
        TableSpec(config=p_cfg, reference_rows=p_ref, **sinks("P_TABLE")),
        TableSpec(
            config=e_cfg, reference_rows=e_ref, **sinks("E_TABLE"),
            adjustments=_ADJUSTMENT,
            parent_edges=(FkEdgeSpec(
                child_cols=("PID",), ref_cols=("PID",),
                parent_landing="p.land.P_TABLE", parent_pk=("PID",),
                mode="fanout", keys_per_batch=10),),
        ),
        TableSpec(
            config=f_cfg, reference_rows=f_ref, **sinks("F_TABLE"),
            # The adjusted parent has NO PK, so the composer must
            # deduplicate its key stream before fanning out.
            parent_edges=(FkEdgeSpec(
                child_cols=("PID",), ref_cols=("PID",),
                parent_landing="p.land.E_TABLE", parent_pk=(),
                mode="fanout", keys_per_batch=10),),
        ),
    ]
    with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
        build_relational_pipeline(p, specs)

    parents = _read(tmp_path / "P_TABLE")
    children = _read(tmp_path / "E_TABLE")
    grandchildren = _read(tmp_path / "F_TABLE")
    assert parents and children and grandchildren

    # 1. the repeat share is copied within tolerance (200 parent keys,
    #    386 landed rows: 0.4819 against the source's 0.5000)
    e_keys = {r["PID"] for r in children}
    landed = 1 - len(e_keys) / len(children)
    assert landed == pytest.approx(
        source_repeat_share(_E_HISTOGRAM), abs=REPEAT_SHARE_TOLERANCE
    )
    # and the key genuinely repeats — not a share matched by accident
    assert len(children) > len(e_keys)

    # 2. FK integrity is untouched by the drop
    assert e_keys <= {r["PID"] for r in parents}
    assert not _read(tmp_path / "dlq_E_TABLE")

    # 3. the adjusted table's OWN child still generates, once per
    #    DISTINCT parent key (the composer's Distinct, armed by
    #    parent_pk=())
    assert {r["PID"] for r in grandchildren} <= e_keys
    assert len(grandchildren) == len(e_keys)
