"""Standalone RAG package (WS2 §4a).

Chunking, embedding, exact-local indexing, chunk storage, and retrieval —
shared by `B1RagEngine`, the `--build_rag_layer` population stage, and any
future consumer of `synthetic_rag.rag_chunks`. Pure Python; heavy deps
(torch, faiss, numpy) stay deferred inside the seams exactly as before.
"""

from sdfb_core.rag.chunking import (
    CHUNK_KIND_FREE_TEXT_COL,
    CHUNK_KIND_ROW_DOC,
    Chunk,
    chunk_row,
    compute_chunk_id,
    compute_row_digest,
)
from sdfb_core.rag.embedding import BgeEmbedder, Embedder, HashingEmbedder
from sdfb_core.rag.index import ExactIPIndex, build_index
from sdfb_core.rag.serialize import serialize_row, serialize_rows

__all__ = [
    "CHUNK_KIND_FREE_TEXT_COL",
    "CHUNK_KIND_ROW_DOC",
    "BgeEmbedder",
    "Chunk",
    "Embedder",
    "ExactIPIndex",
    "HashingEmbedder",
    "build_index",
    "chunk_row",
    "compute_chunk_id",
    "compute_row_digest",
    "serialize_row",
    "serialize_rows",
]
