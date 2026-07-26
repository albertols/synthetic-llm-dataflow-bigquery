"""Exact-local exemplar retrieval (WS2 §4a).

Centroid top-k moved out of `B1RagEngine._retrieve_exemplars`; the
per-column variant (Phase A §4b.3) retrieves representative VALUES of one
column so free-text exemplars are column-relevant instead of diluted
whole-row sentences. Always exact, local, deterministic — never BQ
VECTOR_SEARCH in the generation hot path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sdfb_core.rag.index import build_index

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from sdfb_core.rag.embedding import Embedder
    from sdfb_core.rag.index import ExactIPIndex


def centroid(vectors: list[list[float]]) -> list[float]:
    """Component-wise mean, pure Python (no numpy hard-dep)."""
    dim = len(vectors[0])
    total = [0.0] * dim
    for vec in vectors:
        for j in range(dim):
            total[j] += vec[j]
    return [t / len(vectors) for t in total]


def retrieve_centroid_top_k(
    index: ExactIPIndex,
    vectors: list[list[float]],
    items: Sequence,
    k: int,
) -> list:
    """The k items nearest the vectors' centroid (densest region).

    `items[i]` must correspond to `vectors[i]`. Deterministic given the
    index (descending score, ascending-index tie-break)."""
    ids = index.search(centroid(vectors), k)
    return [items[i] for i in ids]


def retrieve_column_exemplars(
    values: Sequence[str], embedder: Embedder, k: int
) -> list[str]:
    """Top-k most-representative values of one column: embed the values,
    build a throwaway exact index, centroid top-k."""
    values = list(values)
    if not values:
        return []
    if len(values) <= k:
        return values
    vectors = embedder.embed(values)
    index = build_index(vectors, embedder.dim)
    try:
        return retrieve_centroid_top_k(index, vectors, values, k)
    finally:
        index.release()


__all__ = ["centroid", "retrieve_centroid_top_k", "retrieve_column_exemplars"]
