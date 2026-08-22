"""Single-job relational pipeline (ADR 0030): parent keys flow to the
child as an in-DAG side input — one Dataflow job, one vLLM ignition,
referential integrity by construction (no BQ round-trip between
tables).
"""

from __future__ import annotations

import json
from pathlib import Path

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import (
    FkEdgeSpec,
    PipelineConfig,
    TableSpec,
    build_relational_pipeline,
)
from sdfb_core.contracts import TableSchema
from sdfb_tests.fakes import FakeModelClient

_CHILD_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "p.src.orders_flat"},
        "schema": [
            {"name": "ORDER_ID", "type": "STRING", "mode": "REQUIRED"},
            {"name": "CUST_ID", "type": "INT64", "mode": "REQUIRED"},
            {"name": "AMOUNT", "type": "INT64", "mode": "REQUIRED"},
        ],
    }
)


def _child_reference() -> list[dict]:
    # CUST_ID values deliberately DISJOINT from anything the parent can
    # land, so containment proves side-input propagation, not chance.
    return [
        {"ORDER_ID": f"ORD{i:05d}", "CUST_ID": 900000 + i, "AMOUNT": i * 3}
        for i in range(40)
    ]


def _read_jsonl(prefix: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(prefix.parent.glob(prefix.name + "*")):
        rows.extend(
            json.loads(line) for line in f.read_text().splitlines() if line
        )
    return rows


def test_child_fk_values_come_from_parent_landed_keys(
    tmp_path, customers_schema, customers_reference, caplog
):
    parent_cfg = PipelineConfig(
        table_schema=customers_schema,
        engine_name="b1_rag",
        model_client=FakeModelClient(reference_pool=customers_reference),
        num_rows=50,
        batch_size=25,
        run_id="rel-parent",
        landing_table="p.land.customers",
        log_table_prefix="customers",
        # PK-unique Pandera check on the fixture: identity synthesis keeps
        # every generated customer_id unique so all 50 land.
        identity_columns=("customer_id",),
    )
    child_cfg = PipelineConfig(
        table_schema=_CHILD_SCHEMA,
        engine_name="b1_rag",
        model_client=FakeModelClient(reference_pool=_child_reference()),
        num_rows=80,
        batch_size=40,
        run_id="rel-child",
        landing_table="p.land.orders_flat",
        log_table_prefix="orders_flat",
    )
    specs = [
        TableSpec(
            config=parent_cfg,
            reference_rows=customers_reference,
            landing_sink=WriteToJsonLines(str(tmp_path / "parent")),
            dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_parent")),
        ),
        TableSpec(
            config=child_cfg,
            reference_rows=_child_reference(),
            landing_sink=WriteToJsonLines(str(tmp_path / "child")),
            dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_child")),
            parent_edges=(
                FkEdgeSpec(
                    child_cols=("CUST_ID",),
                    ref_cols=("customer_id",),
                    parent_landing="p.land.customers",
                    parent_pk=("customer_id",),
                ),
            ),
        ),
    ]
    import logging as _logging

    options = PipelineOptions(["--runner=DirectRunner"])
    with (
        caplog.at_level(_logging.INFO, logger="sdfb.milestone"),
        beam.Pipeline(options=options) as p,
    ):
        results = build_relational_pipeline(p, specs)
    assert set(results) == {"p.land.customers", "p.land.orders_flat"}
    # Every engine milestone carries its table (ADR 0030): two tables
    # interleave in ONE worker log and stay attributable.
    assert "table=customers" in caplog.text
    assert "table=orders_flat" in caplog.text

    parent_rows = _read_jsonl(tmp_path / "parent")
    child_rows = _read_jsonl(tmp_path / "child")
    assert parent_rows and child_rows
    parent_keys = {r["customer_id"] for r in parent_rows}
    child_fks = {r["CUST_ID"] for r in child_rows}
    # Integrity by construction: every child FK value is a landed parent
    # key — and none of the child's own (disjoint) reference values leak.
    assert child_fks <= parent_keys
    assert not any(900000 <= v < 900100 for v in child_fks)


def test_independent_tables_share_one_pipeline(
    tmp_path, customers_schema, customers_reference
):
    def _cfg(run_id: str, landing: str) -> PipelineConfig:
        return PipelineConfig(
            table_schema=customers_schema,
            engine_name="b1_rag",
            model_client=FakeModelClient(reference_pool=customers_reference),
            num_rows=20,
            batch_size=10,
            run_id=run_id,
            landing_table=landing,
            identity_columns=("customer_id",),
        )

    specs = [
        TableSpec(
            config=_cfg("multi-a", "p.land.t_a"),
            reference_rows=customers_reference,
            landing_sink=WriteToJsonLines(str(tmp_path / "a")),
            dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_a")),
        ),
        TableSpec(
            config=_cfg("multi-b", "p.land.t_b"),
            reference_rows=customers_reference,
            landing_sink=WriteToJsonLines(str(tmp_path / "b")),
            dlq_sink=WriteToJsonLines(str(tmp_path / "dlq_b")),
        ),
    ]
    options = PipelineOptions(["--runner=DirectRunner"])
    with beam.Pipeline(options=options) as p:
        build_relational_pipeline(p, specs)  # unique labels: must not raise
    assert len(_read_jsonl(tmp_path / "a")) == 20
    assert len(_read_jsonl(tmp_path / "b")) == 20
