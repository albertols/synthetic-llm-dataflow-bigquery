"""WS2 §4a: Chunk construction for `synthetic_rag.rag_chunks`.

One `row_doc` chunk per row (chunk_index=0, byte-identical to the GReaT
serialization B.1 embeds) plus one `free_text_col` chunk per non-null
free-text column value (chunk_index 1..k in declared order)."""

from __future__ import annotations

import hashlib
import json

from sdfb_core.rag.chunking import (
    chunk_row,
    compute_chunk_id,
    compute_row_digest,
)
from sdfb_core.rag.serialize import serialize_row

_ROW = {"id": 7, "notes": "late delivery", "status": "OPEN", "comment": None}
_KW = dict(
    column_order=["id", "notes", "status", "comment"],
    free_text_columns=["notes", "comment"],
    source_fqn="proj.raw.tickets",
    reference_digest="refdig",
    embedder_id="bge-small-en-v1.5",
    embedder_version="v1",
)


def test_digests_are_deterministic_and_canonical():
    d = compute_row_digest(_ROW)
    assert d == hashlib.sha256(
        json.dumps(_ROW, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    assert compute_row_digest(dict(reversed(list(_ROW.items())))) == d
    cid = compute_chunk_id("proj.raw.tickets", d, 0)
    assert cid == hashlib.blake2b(
        f"proj.raw.tickets:{d}:0".encode(), digest_size=32
    ).hexdigest()


def test_chunk_row_emits_row_doc_then_free_text_cols():
    chunks = chunk_row(_ROW, pk_columns=["id"], **_KW)
    # comment is None → no chunk for it.
    assert [c.chunk_kind for c in chunks] == ["row_doc", "free_text_col"]
    row_doc, notes = chunks
    assert row_doc.chunk_index == 0
    assert row_doc.chunk_text == serialize_row(_ROW, _KW["column_order"])
    assert row_doc.source_pk == {"id": 7}
    assert row_doc.embedding is None
    assert notes.chunk_index == 1
    assert notes.chunk_text == "late delivery"
    assert notes.metadata == {"column": "notes"}
    d = compute_row_digest(_ROW)
    assert {c.row_digest for c in chunks} == {d}
    assert row_doc.chunk_id == compute_chunk_id("proj.raw.tickets", d, 0)
    assert notes.chunk_id == compute_chunk_id("proj.raw.tickets", d, 1)
    assert all(
        (c.reference_digest, c.embedder_id, c.embedder_version)
        == ("refdig", "bge-small-en-v1.5", "v1")
        for c in chunks
    )


def test_chunk_row_without_pk_has_null_source_pk():
    chunks = chunk_row(_ROW, **_KW)
    assert chunks[0].source_pk is None


def test_chunk_is_frozen():
    c = chunk_row(_ROW, **_KW)[0]
    try:
        c.chunk_text = "x"
        raise AssertionError("Chunk must be frozen")
    except AttributeError:
        pass
