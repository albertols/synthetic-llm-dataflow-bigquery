"""ADR 0036: the SOURCE fan-out histogram and PK cells, measured once,
cached by (source child, edge cols, model sha)."""

from __future__ import annotations

import logging

from sdfb_beam.io.fanout_stats import (
    BigQueryFanoutStatsStore,
    fanout_payload,
    measure_fanout,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class _Client:
    """Answers the three queries by shape of SQL."""

    def __init__(self):
        self.sql: list[str] = []
        self.loaded: list[list[dict]] = []

    def query(self, sql, job_config=None):
        self.sql.append(sql)
        if "AS k" in sql:
            return _Result([{"k": 1, "parents": 30}, {"k": 2, "parents": 20}])
        if "COUNT(DISTINCT" in sql or "APPROX_COUNT_DISTINCT" in sql:
            return _Result([{"parents": 100}])
        if "AS n FROM" in sql and "GROUP BY" in sql:  # cells
            return _Result([{"C2": "C0", "D18": "K0", "n": 40}, {"C2": "C1", "D18": "K1", "n": 20}])
        if "SELECT payload" in sql:
            return _Result([])
        raise AssertionError(sql)

    def load_table_from_json(self, rows, table, job_config=None):
        self.loaded.append(rows)
        return _Result([])


def test_measure_fanout_builds_histogram_with_zero_bucket_and_cells():
    client = _Client()
    out = measure_fanout(
        source_child="p.src.C_TABLE", child_cols=("D_COL_001",),
        source_parent="p.src.B_TABLE", ref_cols=("D_COL_001",),
        cell_cols=("C2", "D18"), client=client,
    )
    # 100 parents, 50 with children (30 x1 + 20 x2) -> 50 in the zero bucket
    assert out["histogram"] == {"0": 50, "1": 30, "2": 20}
    assert out["parents"] == 100 and out["children"] == 70
    assert out["cells"] == {"cols": ["C2", "D18"], "rows": [["C0", "K0"], ["C1", "K1"]],
                            "counts": [40.0, 20.0]}


def test_measure_fanout_builds_a_client_when_none_is_given(monkeypatch):
    fake = _Client()
    monkeypatch.setattr("google.cloud.bigquery.Client", lambda: fake)
    out = measure_fanout(
        source_child="p.src.C_TABLE", child_cols=("D_COL_001",),
        source_parent="p.src.B_TABLE", ref_cols=("D_COL_001",),
        cell_cols=(),
    )
    assert out["histogram"] == {"0": 50, "1": 30, "2": 20}


def test_measure_fanout_without_cell_columns():
    out = measure_fanout(
        source_child="p.src.C", child_cols=("K",), source_parent="p.src.P",
        ref_cols=("K",), cell_cols=(), client=_Client(),
    )
    assert out["cells"] is None


def test_store_round_trip_uses_a_load_job():
    client = _Client()
    store = BigQueryFanoutStatsStore("p.synthetic_data_quality.fk_fanout_stats", client=client)
    assert store.get("p.src.C", ("K",), "abc") is None
    store.put("p.src.C", ("K",), "abc", {"histogram": {"0": 1}, "cells": None, "parents": 1, "children": 0})
    (rows,) = client.loaded
    assert rows[0]["source_table"] == "p.src.C" and rows[0]["model_sha"] == "abc"
    assert '"histogram"' in rows[0]["payload"]


def test_fanout_payload_shape():
    payload = fanout_payload(
        {"histogram": {"0": 1, "2": 1}, "cells": None, "parents": 2, "children": 2},
        driving_cols=("K", "INH"), exact_cells=False,
    )
    assert payload == {"driving_cols": ["K", "INH"], "histogram": {"0": 1, "2": 1},
                       "cells": None, "exact_cells": False}


class _OrphanClient(_Client):
    """Same child histogram (50 parent tuples with children) but only 40
    parent tuples in the source parent — 10 child tuples are orphans."""

    def query(self, sql, job_config=None):
        if "COUNT(DISTINCT" in sql or "APPROX_COUNT_DISTINCT" in sql:
            self.sql.append(sql)
            return _Result([{"parents": 40}])
        return super().query(sql, job_config=job_config)


def test_source_orphans_are_announced_not_silently_clamped(caplog):
    """ADR 0036 review I6: `max(0, parents - with_children)` hid the case
    where the SOURCE child holds tuples its parent does not — the zero
    bucket is then unmeasurable and children/parents OVERSTATES the mean,
    which sizes the whole driven child."""
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        out = measure_fanout(
            source_child="p.src.C_TABLE", child_cols=("D_COL_001",),
            source_parent="p.src.B_TABLE", ref_cols=("D_COL_001",),
            cell_cols=(), client=_OrphanClient(), edge="C_TABLE(D_COL_001)->B_TABLE",
        )
    assert out["histogram"]["0"] == 0
    assert out["parents"] == 40 and out["children"] == 70
    assert "name=fk_fanout_source_orphans" in caplog.text
    assert "child_tuples=50" in caplog.text
    assert "parent_tuples=40" in caplog.text


def test_no_orphan_milestone_when_every_child_tuple_has_a_parent(caplog):
    with caplog.at_level(logging.WARNING, logger="sdfb.milestone"):
        measure_fanout(
            source_child="p.src.C_TABLE", child_cols=("D_COL_001",),
            source_parent="p.src.B_TABLE", ref_cols=("D_COL_001",),
            cell_cols=(), client=_Client(),
        )
    assert "fk_fanout_source_orphans" not in caplog.text
