"""`--build_rag_layer` population branch (WS2 §4b.1; 2026-07-07 design §3).

Chunk reference rows → embed → `rag_chunks` BQ rows. The embed stage is a
DoFn with a setup()-built embedder (the same GCS warm-pull + local-only
loading as `GenerateRecordsDoFn`) rather than a RunInference ModelHandler
— one embedding code path, one lifecycle pattern; the rewritten RAG
design doc records this delta from the 2026-07-07 diagram.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import apache_beam as beam
from sdfb_core.rag.chunking import Chunk, chunk_row


class ChunkReferenceRowsDoFn(beam.DoFn):
    """One reference row → its `Chunk`s (no embeddings yet). Pure."""

    def __init__(
        self,
        *,
        source_fqn: str,
        reference_digest: str,
        column_order: list[str],
        free_text_columns: list[str],
        pk_columns: list[str],
        embedder_id: str,
        embedder_version: str,
    ) -> None:
        super().__init__()
        self.source_fqn = source_fqn
        self.reference_digest = reference_digest
        self.column_order = column_order
        self.free_text_columns = free_text_columns
        self.pk_columns = pk_columns
        self.embedder_id = embedder_id
        self.embedder_version = embedder_version

    def process(self, row: dict):
        yield from chunk_row(
            row,
            column_order=self.column_order,
            free_text_columns=self.free_text_columns,
            source_fqn=self.source_fqn,
            reference_digest=self.reference_digest,
            embedder_id=self.embedder_id,
            embedder_version=self.embedder_version,
            pk_columns=self.pk_columns,
        )


class EmbedChunksDoFn(beam.DoFn):
    """Batch of `Chunk`s → `rag_chunks` BQ row dicts with embeddings.

    The embedder is built ONCE per worker in setup() (heavy), matching
    the engine-in-setup rule from `.claude/skills/beam-dofn.md`.
    """

    def __init__(self, embedder_uri: str) -> None:
        super().__init__()
        self.embedder_uri = embedder_uri
        self._embedder = None

    def setup(self):
        uri = self.embedder_uri
        if uri.startswith("gs://"):
            from sdfb_beam.dofns.generate import EMBEDDER_LOCAL_DIR
            from sdfb_beam.gcs import localize_gcs_prefix

            uri = localize_gcs_prefix(uri, EMBEDDER_LOCAL_DIR)
        if uri:
            from sdfb_core.rag.embedding import BgeEmbedder

            self._embedder = BgeEmbedder(uri)
        else:
            from sdfb_core.rag.embedding import HashingEmbedder

            self._embedder = HashingEmbedder(dim=384)

    def process(self, chunks: list[Chunk]):
        vectors = self._embedder.embed([c.chunk_text for c in chunks])
        created_at = datetime.now(UTC).isoformat()
        for chunk, vector in zip(chunks, vectors, strict=True):
            yield chunk_to_bq_row(chunk, vector, created_at)


def chunk_to_bq_row(chunk: Chunk, embedding: list[float], created_at: str) -> dict:
    """`Chunk` + embedding → the `rag_chunks` row dict (JSON cols encoded)."""
    return {
        "chunk_id": chunk.chunk_id,
        "source_fqn": chunk.source_fqn,
        "source_pk": None
        if chunk.source_pk is None
        else json.dumps(chunk.source_pk, sort_keys=True, default=str),
        "row_digest": chunk.row_digest,
        "reference_digest": chunk.reference_digest,
        "chunk_index": chunk.chunk_index,
        "chunk_kind": chunk.chunk_kind,
        "chunk_text": chunk.chunk_text,
        "embedder_id": chunk.embedder_id,
        "embedder_version": chunk.embedder_version,
        "embedding": [float(v) for v in embedding],
        "metadata": json.dumps(chunk.metadata, sort_keys=True, default=str),
        "created_at": created_at,
    }


__all__ = ["ChunkReferenceRowsDoFn", "EmbedChunksDoFn", "chunk_to_bq_row"]
