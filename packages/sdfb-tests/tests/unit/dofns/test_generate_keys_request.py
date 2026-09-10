"""ADR 0036: the Generate DoFn drives the engine from parent keys."""

from __future__ import annotations

import logging

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.testing.util import assert_that
from sdfb_beam.dofns.generate import GenerateRecordsDoFn
from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext
from sdfb_tests.fakes import FakeModelClient

_SCHEMA = TableSchema.model_validate(
    {"table_info": {"table_id": "p.src.child_t"},
     "schema": [
         {"name": "PID", "type": "STRING", "mode": "REQUIRED"},
         {"name": "CAT", "type": "STRING", "mode": "REQUIRED"},
         {"name": "AMT", "type": "INT64", "mode": "REQUIRED"},
     ]}
)
_ROWS = [{"PID": f"P{i:04d}", "CAT": "abc"[i % 3], "AMT": i} for i in range(60)]


def _ctx() -> GenerationContext:
    return GenerationContext(
        table_schema=_SCHEMA, reference_rows=_ROWS, reference_digest="dofn-fanout",
        pipeline_run_id="dofn-run", pk_columns=["PID", "CAT"],
        fanout={"driving_cols": ["PID"], "histogram": {"2": 1},
                "cells": {"cols": ["CAT"], "rows": [["a"], ["b"], ["c"]], "counts": [1, 1, 1]},
                "exact_cells": True},
    )


def test_keys_request_yields_two_children_per_key(caplog):
    dofn = GenerateRecordsDoFn(
        engine_name="b1_rag", model_client=FakeModelClient(reference_pool=_ROWS), ctx=_ctx(),
    )
    request = {"batch_id": 0, "keys": [("K1",), ("K2",), ("K3",)]}

    def _check(rows):
        rows = list(rows)
        assert len(rows) == 6, rows
        assert {r["PID"] for r in rows} == {"K1", "K2", "K3"}
        for pid in ("K1", "K2", "K3"):
            cats = [r["CAT"] for r in rows if r["PID"] == pid]
            assert len(set(cats)) == 2

    with caplog.at_level(logging.INFO, logger="sdfb.milestone"), beam.Pipeline(
        options=PipelineOptions(["--runner=DirectRunner"])
    ) as p:
        out = p | beam.Create([request]) | beam.ParDo(dofn).with_outputs("failed", main="main")
        assert_that(out.main, _check)
    assert "name=batch_done" in caplog.text and "keys=3" in caplog.text


class _RaisingEngine:
    """Engine whose key-batch generation blows up mid-batch."""

    def generate_for_keys(self, keys, cfg):
        raise RuntimeError("cell draw exploded")
        yield  # pragma: no cover - makes this a generator function


def test_a_failed_key_batch_summarizes_its_keys_in_the_dlq():
    """ADR 0036 review I7: `raw_request: request` embedded the WHOLE key
    list — at `--keys_per_batch=10000` that is a multi-MB DLQ row per
    crashed batch, written to BigQuery and read back by the gate. The
    envelope keeps a bounded sample plus the totals, and `n` so
    `_dlq_rule_weight` still weights the batch by its expected rows."""
    dofn = GenerateRecordsDoFn(
        engine_name="b1_rag", model_client=FakeModelClient(reference_pool=_ROWS),
        ctx=_ctx(),
    )
    dofn._engine = _RaisingEngine()
    keys = [(f"K{i}",) for i in range(25)]
    request = {"batch_id": 7, "keys": keys, "n": 50}

    out = list(dofn.process(request))
    assert len(out) == 1
    envelope = out[0].value
    assert envelope["rule_id"] == "engine_failure"
    raw = envelope["raw_request"]
    assert raw["keys_total"] == 25
    assert len(raw["keys"]) == 10
    assert raw["keys"][0] == ["K0"]
    assert raw["n"] == 50  # the gate's expected-lost-rows weight
    assert raw["batch_id"] == 7
