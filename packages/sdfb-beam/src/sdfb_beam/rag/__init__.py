"""Beam/GCP side of the RAG layer (WS2 §4b): the BigQuery-backed
`ChunkStore` and the `--build_rag_layer` population stage."""

from sdfb_beam.rag.store import BigQueryChunkStore

__all__ = ["BigQueryChunkStore"]
