"""Chunk construction for `synthetic_rag.rag_chunks` (WS2 §4a).

`row_doc` chunk_text reuses `serialize_row()` byte-identically — that is
what makes the generation-time read a safe substitute for re-embedding
(same text in, same vector out, embedder pinned by id+version).

`row_digest` uses the same per-row canonical encoding as
`sdfb_beam.io.digest.compute_reference_digest` (json.dumps sort_keys
default=str → SHA-256) so the two provenance hashes stay comparable.
Pure stdlib.

Chunking input is the driver-loaded reference *sample* (default 10k rows,
deterministically fingerprint-ordered), never the full source table — see
`sdfb_beam.rag.population` for the scope rationale (provenance digest,
distribution-inference purpose, embed cost, bounded privacy surface).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sdfb_core.rag.serialize import serialize_row

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

CHUNK_KIND_ROW_DOC = "row_doc"
CHUNK_KIND_FREE_TEXT_COL = "free_text_col"


@dataclass(frozen=True)
class Chunk:
    """One `rag_chunks` row. `embedding` is None until the embed stage."""

    chunk_id: str
    source_fqn: str
    row_digest: str
    reference_digest: str
    chunk_index: int
    chunk_kind: str
    chunk_text: str
    embedder_id: str
    embedder_version: str
    source_pk: dict | None = None
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)


def compute_row_digest(row: dict) -> str:
    """SHA-256 of the canonical-encoded row — content identity independent
    of chunking, comparable across runs and reference pulls."""
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def compute_chunk_id(source_fqn: str, row_digest: str, chunk_index: int) -> str:
    """Deterministic chunk primary key (blake2b-256; dedupes retries)."""
    return hashlib.blake2b(
        f"{source_fqn}:{row_digest}:{chunk_index}".encode(), digest_size=32
    ).hexdigest()


def chunk_row(
    row: dict,
    *,
    column_order: Sequence[str],
    free_text_columns: Sequence[str],
    source_fqn: str,
    reference_digest: str,
    embedder_id: str,
    embedder_version: str,
    pk_columns: Sequence[str] = (),
) -> list[Chunk]:
    """`row_doc` chunk (index 0) + one `free_text_col` chunk per non-null
    free-text column value, indices 1..k in declared column order."""
    row_digest = compute_row_digest(row)
    source_pk = {c: row.get(c) for c in pk_columns} if pk_columns else None

    def _chunk(index: int, kind: str, text: str, metadata: dict) -> Chunk:
        return Chunk(
            chunk_id=compute_chunk_id(source_fqn, row_digest, index),
            source_fqn=source_fqn,
            row_digest=row_digest,
            reference_digest=reference_digest,
            chunk_index=index,
            chunk_kind=kind,
            chunk_text=text,
            embedder_id=embedder_id,
            embedder_version=embedder_version,
            source_pk=source_pk,
            metadata=metadata,
        )

    chunks = [
        _chunk(0, CHUNK_KIND_ROW_DOC, serialize_row(row, column_order), {})
    ]
    index = 1
    free_text = set(free_text_columns)
    for name in column_order:
        if name not in free_text:
            continue
        value = row.get(name)
        if value in (None, ""):
            continue
        chunks.append(
            _chunk(index, CHUNK_KIND_FREE_TEXT_COL, str(value), {"column": name})
        )
        index += 1
    return chunks


__all__ = [
    "CHUNK_KIND_FREE_TEXT_COL",
    "CHUNK_KIND_ROW_DOC",
    "Chunk",
    "chunk_row",
    "compute_chunk_id",
    "compute_row_digest",
]
