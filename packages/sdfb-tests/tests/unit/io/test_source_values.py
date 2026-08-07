"""BigQuery-backed `SourceValueStore` + warm-pool taint preflight.

2026-08-05/07 E2E findings: the B_TABLE R1 pool build memorized 33-99% of
10 columns (rejection ran against the profiled sample only), and the 10M
warm run then REPLAYED those tainted pools because the launcher's
`exists()` guard skips the rebuild. Two pieces close the loop:

  - `BigQuerySourceValueStore.fetch_distinct` feeds the engine's full-
    domain rejection at build time;
  - `pool_source_overlap` lets the launcher measure a persisted pool
    against the live source BEFORE trusting it, so a tainted warm store
    is deleted and rebuilt instead of replayed.

A fake client stands in for google.cloud.bigquery — nothing touches GCP.
"""

from __future__ import annotations

import pickle

import pytest
from sdfb_beam.io.source_values import (
    BigQuerySourceValueStore,
    pool_source_overlap,
)
from sdfb_core.pools import FreeTextPool, SourceValueStore


class _FakeBqClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, object]] = []

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        return self

    def result(self):
        return list(self.rows)


def test_fetch_distinct_returns_string_values() -> None:
    client = _FakeBqClient([{"v": "alpha"}, {"v": "beta"}])
    store = BigQuerySourceValueStore("p.d.source", client=client)
    assert store.fetch_distinct("notes") == frozenset({"alpha", "beta"})
    sql, _cfg = client.queries[0]
    assert "`p.d.source`" in sql
    assert "`notes`" in sql


def test_fetch_distinct_over_cap_returns_none() -> None:
    client = _FakeBqClient([{"v": str(i)} for i in range(4)])
    store = BigQuerySourceValueStore("p.d.source", cap=3, client=client)
    assert store.fetch_distinct("notes") is None


def test_fetch_distinct_rejects_non_identifier_column() -> None:
    store = BigQuerySourceValueStore(
        "p.d.source", client=_FakeBqClient([])
    )
    with pytest.raises(ValueError):
        store.fetch_distinct("notes`; DROP TABLE x")


def test_count_overlap_binds_values_as_array_parameter() -> None:
    client = _FakeBqClient([{"n": 7}])
    store = BigQuerySourceValueStore("p.d.source", client=client)
    assert store.count_overlap("notes", ["a", "b"]) == 7
    _sql, cfg = client.queries[0]
    (param,) = cfg.query_parameters
    assert list(param.values) == ["a", "b"]


def test_satisfies_the_core_protocol_and_pickles_without_client() -> None:
    store = BigQuerySourceValueStore("p.d.source", client=_FakeBqClient([]))
    assert isinstance(store, SourceValueStore)
    revived = pickle.loads(pickle.dumps(store))
    assert revived.table_fqn == "p.d.source"
    assert revived._client is None


class _FakePoolStore:
    def __init__(self, pools: list[FreeTextPool]) -> None:
        self.pools = pools

    def fetch(self, reference_digest: str, model_uri: str) -> list[FreeTextPool]:
        return [
            p
            for p in self.pools
            if p.reference_digest == reference_digest
            and p.model_uri == model_uri
        ]


class _FakeValueStore:
    """count_overlap fake: values named 'real-*' exist in source."""

    def count_overlap(self, column: str, values: list[str]) -> int:
        return sum(1 for v in values if v.startswith("real-"))


def _pool(column: str, values: tuple[str, ...]) -> FreeTextPool:
    return FreeTextPool(
        reference_digest="d1",
        model_uri="gs://b/m",
        column=column,
        target=len(values),
        values=values,
        stagnated=False,
        attempts=1,
    )


def test_pool_source_overlap_reports_tainted_columns_only() -> None:
    pools = [
        _pool("clean_col", ("nov-1", "nov-2")),
        _pool("tainted_col", ("real-1", "real-2", "nov-3")),
    ]
    overlap = pool_source_overlap(
        _FakePoolStore(pools), _FakeValueStore(), "d1", "gs://b/m"
    )
    assert overlap == {"tainted_col": 2}


def test_pool_source_overlap_empty_store_is_clean() -> None:
    overlap = pool_source_overlap(
        _FakePoolStore([]), _FakeValueStore(), "d1", "gs://b/m"
    )
    assert overlap == {}
