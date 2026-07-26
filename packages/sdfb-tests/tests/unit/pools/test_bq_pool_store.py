"""Row <-> FreeTextPool mapping and the BQ-backed store (WS5 T5).

Mirrors tests/unit/rag/test_bq_chunk_store.py: a fake client stands in for
google.cloud.bigquery so nothing here touches GCP.
"""

from __future__ import annotations

from sdfb_beam.pools.store import BigQueryFreeTextPoolStore, pool_to_row, row_to_pool
from sdfb_core.pools import FreeTextPool


class _FakeBqClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, dict]] = []

    def query(self, sql, job_config=None):
        params = {
            p.name: p.value for p in (job_config.query_parameters if job_config else [])
        }
        self.queries.append((sql, params))
        return self

    def result(self):
        return list(self.rows)


def _pool() -> FreeTextPool:
    return FreeTextPool(
        reference_digest="d1",
        model_uri="gs://b/m",
        column="notes",
        target=64,
        values=("alpha", "beta"),
        stagnated=True,
        attempts=9,
    )


def test_row_round_trip_preserves_every_field():
    assert row_to_pool(pool_to_row(_pool())) == _pool()


def test_values_persist_as_a_repeated_field_not_a_blob():
    """REPEATED STRING keeps the pool queryable in BQ (UNNEST) instead of
    hiding it inside a JSON string."""
    assert pool_to_row(_pool())["values"] == ["alpha", "beta"]


def test_fetch_maps_rows():
    client = _FakeBqClient([pool_to_row(_pool())])
    store = BigQueryFreeTextPoolStore("p.synthetic_rag.freetext_pools", client=client)
    assert store.fetch("d1", "gs://b/m") == [_pool()]


def test_fetch_binds_digest_and_model_as_query_parameters():
    """Never string-interpolated — these come from run configuration."""
    client = _FakeBqClient([])
    store = BigQueryFreeTextPoolStore("p.synthetic_rag.freetext_pools", client=client)
    store.fetch("d1", "gs://b/m")
    _sql, params = client.queries[0]
    assert params == {"reference_digest": "d1", "model_uri": "gs://b/m"}


def test_exists_is_false_on_empty_result():
    client = _FakeBqClient([])
    store = BigQueryFreeTextPoolStore("p.synthetic_rag.freetext_pools", client=client)
    assert store.exists("d1", "gs://b/m") is False


def test_exists_is_true_when_a_row_comes_back():
    client = _FakeBqClient([{"n": 1}])
    store = BigQueryFreeTextPoolStore("p.synthetic_rag.freetext_pools", client=client)
    assert store.exists("d1", "gs://b/m") is True


def test_select_quotes_reserved_identifiers():
    """`values` is a BigQuery RESERVED keyword — unquoted it is a syntax
    error against the real service, which no fake client can surface."""
    client = _FakeBqClient([])
    store = BigQueryFreeTextPoolStore("p.synthetic_rag.freetext_pools", client=client)
    store.fetch("d1", "m")
    sql, _ = client.queries[0]
    assert "`values`" in sql
    assert " values," not in sql and " values " not in sql


def test_row_to_pool_tolerates_a_null_repeated_field():
    """BigQuery returns None, not [], for an empty REPEATED column."""
    row = pool_to_row(_pool())
    row["values"] = None
    assert row_to_pool(row).values == ()
