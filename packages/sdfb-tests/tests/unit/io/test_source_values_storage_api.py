"""`BigQuerySourceValueStore` pulls large domains through the BigQuery
Storage Read API (Arrow), not the REST row iterator (ADR 0034).

2026-08-29 R6 cold run, A_TABLE pool branch: `_fetch_identifier_domains`
paged 944,582 distinct `A_COL_005` values through
`google.cloud.bigquery` `RowIterator.__iter__` — the tabledata.list REST
path at ~2.9k rows/s — and the Beam harness reported the bundle as
"creating for at least 1078 s" (the report then read it as a generation
stall). `RowIterator.to_arrow()` uses the Storage Read API when the
result is large and the `google-cloud-bigquery-storage` client is
installed (both true on the worker image), and falls back to the cached
first page for small results. Nothing else changes: same SQL, same cap
semantics, same process cache.
"""

from __future__ import annotations

import pyarrow as pa
from sdfb_beam.io.source_values import (
    BigQuerySourceValueStore,
    clear_source_value_cache,
)


class _ArrowResult:
    """A `RowIterator` stand-in exposing `to_arrow()`; iterating it directly
    is the REST path we want to see avoided."""

    def __init__(self, values: list[str], *, arrow_fails: bool = False) -> None:
        self._values = values
        self.arrow_fails = arrow_fails
        self.arrow_calls = 0
        self.iterated = False

    def to_arrow(self, **kwargs):
        self.arrow_calls += 1
        if self.arrow_fails:
            raise RuntimeError("bqstorage unavailable")
        return pa.table({"v": pa.array(self._values, type=pa.string())})

    def __iter__(self):
        self.iterated = True
        return iter([{"v": v} for v in self._values])


class _Client:
    def __init__(self, result) -> None:
        self._result = result
        self.queries: list[str] = []

    def query(self, sql, job_config=None):
        self.queries.append(sql)
        return self

    def result(self):
        return self._result


def setup_function(_fn) -> None:
    clear_source_value_cache()


def test_fetch_distinct_reads_the_arrow_column_without_iterating_rows():
    result = _ArrowResult(["a", "b", "b", "c"])
    store = BigQuerySourceValueStore("p.d.t", client=_Client(result))
    assert store.fetch_distinct("col") == frozenset({"a", "b", "c"})
    assert result.arrow_calls == 1
    assert result.iterated is False


def test_fetch_distinct_over_cap_is_none_on_the_arrow_path():
    result = _ArrowResult([str(i) for i in range(5)])
    store = BigQuerySourceValueStore("p.d.t", cap=4, client=_Client(result))
    assert store.fetch_distinct("col") is None


def test_arrow_failure_falls_back_to_row_iteration(caplog):
    import logging

    result = _ArrowResult(["x", "y"], arrow_fails=True)
    store = BigQuerySourceValueStore("p.d.t", client=_Client(result))
    with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
        assert store.fetch_distinct("col") == frozenset({"x", "y"})
    assert result.iterated is True
    assert "name=source_values_arrow_fallback" in caplog.text


def test_fetch_frequent_uses_the_arrow_path_too():
    result = _ArrowResult(["2000", "35"])
    store = BigQuerySourceValueStore("p.d.t", client=_Client(result))
    assert store.fetch_frequent("acct", 10) == frozenset({"2000", "35"})
    assert result.arrow_calls == 1
    assert result.iterated is False


def test_arrow_nulls_are_dropped_like_the_rest_path_would():
    result = _ArrowResult(["a", None, "b"])  # type: ignore[list-item]
    store = BigQuerySourceValueStore("p.d.t", client=_Client(result))
    assert store.fetch_distinct("col") == frozenset({"a", "b"})
