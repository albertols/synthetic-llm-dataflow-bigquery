"""WS2 §4b.1: the --build_rag_layer population branch, DirectRunner +
HashingEmbedder + in-memory sink. Chunk → embed → BQ-row shape."""

from __future__ import annotations

import apache_beam as beam
from apache_beam.testing.test_pipeline import TestPipeline
from sdfb_beam.rag.population import (
    ChunkReferenceRowsDoFn,
    EmbedChunksDoFn,
    chunk_to_bq_row,
)
from sdfb_core.rag.chunking import chunk_row

_ROWS = [
    {"id": 1, "notes": "first note", "code": "A"},
    {"id": 2, "notes": None, "code": "B"},
]
_DOFN_KW = dict(
    source_fqn="p.d.t",
    reference_digest="dig",
    column_order=["id", "notes", "code"],
    free_text_columns=["notes"],
    pk_columns=["id"],
    embedder_id="hashing-384",
    embedder_version="v1",
)


def test_population_branch_end_to_end_direct_runner():
    with TestPipeline() as p:
        rows = (
            p
            | beam.Create(_ROWS)
            | "Chunk" >> beam.ParDo(ChunkReferenceRowsDoFn(**_DOFN_KW))
            | "Batch" >> beam.BatchElements(min_batch_size=1, max_batch_size=10)
            | "Embed" >> beam.ParDo(EmbedChunksDoFn(embedder_uri=""))
            | "Collect" >> beam.combiners.ToList()
        )

        def _check(out):
            # row 1 → row_doc + notes; row 2 → row_doc only (notes null)
            assert len(out) == 3
            kinds = sorted(r["chunk_kind"] for r in out)
            assert kinds == ["free_text_col", "row_doc", "row_doc"]
            for r in out:
                assert len(r["embedding"]) == 384
                assert abs(sum(v * v for v in r["embedding"]) - 1.0) < 1e-6
                assert r["reference_digest"] == "dig"
                assert r["embedder_id"] == "hashing-384"
                assert r["created_at"]

        _ = rows | beam.Map(lambda x, f=_check: f(x))


def test_chunk_to_bq_row_shape():
    chunk = chunk_row(_ROWS[0], **{k: _DOFN_KW[k] for k in (
        "column_order", "free_text_columns", "source_fqn",
        "reference_digest", "embedder_id", "embedder_version", "pk_columns",
    )})[0]
    row = chunk_to_bq_row(chunk, [0.0] * 4, "2026-07-20T00:00:00+00:00")
    assert row["chunk_id"] == chunk.chunk_id
    assert row["source_pk"] == '{"id": 1}'
    assert row["metadata"] == "{}"
    assert row["embedding"] == [0.0] * 4
    assert set(row) == {
        "chunk_id", "source_fqn", "source_pk", "row_digest",
        "reference_digest", "chunk_index", "chunk_kind", "chunk_text",
        "embedder_id", "embedder_version", "embedding", "metadata",
        "created_at",
    }
