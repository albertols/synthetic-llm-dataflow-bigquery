"""Standalone RAG package (WS2 §4a).

Chunking, embedding, exact-local indexing, chunk storage, and retrieval —
shared by `B1RagEngine`, the `--build_rag_layer` population stage, and any
future consumer of `synthetic_rag.rag_chunks`. Pure Python; heavy deps
(torch, faiss, numpy) stay deferred inside the seams exactly as before.
"""

from sdfb_core.rag.embedding import BgeEmbedder, Embedder, HashingEmbedder
from sdfb_core.rag.index import ExactIPIndex, build_index
from sdfb_core.rag.serialize import serialize_row, serialize_rows

__all__ = [
    "BgeEmbedder",
    "Embedder",
    "ExactIPIndex",
    "HashingEmbedder",
    "build_index",
    "serialize_row",
    "serialize_rows",
]
