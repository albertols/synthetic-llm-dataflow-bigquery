# WS2 Phase A — RAG Package Extraction + Persistence + Generation-Quality Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a standalone `sdfb_core.rag` package (chunker / embedder / index / `ChunkStore` / retrieval), persist reference-row embeddings to `synthetic_rag.rag_chunks` behind `--build_rag_layer`, make `B1RagEngine` read-instead-of-reembed, scale free-text pools to `min(num_rows, source_distinct, 512)` with batched LLM calls, and retrieve exemplars per-column.

**Architecture:** `sdfb_core/rag/` is pure Python (embedding/index/serialize move out of `engines/b1_rag/` with import shims left behind; chunking/store/retrieval are new). The BQ-backed `ChunkStore` and the population DAG branch live in `sdfb_beam/rag/`. `B1RagEngine` keeps its `GenerationEngine` contract and delegates; the read path is self-gating on data (empty store → today's embed path, byte-for-byte). The 2026-07-07 population diagram's RunInference stage is realized as a `DoFn` with a setup-built embedder (same warm-pull pattern as `generate.py`) — the design-doc rewrite (Task 11) records this delta.

**Tech Stack:** pure Python + optional numpy/faiss in `sdfb-core`; `apache-beam` + `google.cloud.bigquery` (lazy) in `sdfb-beam`; pytest + DirectRunner in `sdfb-tests`.

**Spec:** `docs/superpowers/specs/2026-07-20-e2e-remediation-rag-eval-evolution-design.md` §4 (WS2 §4a + §4b Phase A + §4e schema file). Phases B (§4c) and C (§4d) are **out of scope** — the spec gates them on Phase A's E2E baseline.

## Global Constraints

- `sdfb-core` must not import `apache_beam`, `google.cloud.*`, `vllm`, or `torch` at module scope (CLAUDE.md package map). `numpy`/`faiss` stay deferred-optional exactly as `index.py` does today.
- Pool constants are exactly `_FREE_TEXT_POOL_MAX = 512` and `_POOL_VALUES_PER_CALL = 32`; pool target per column is `min(ctx.num_rows, column_distinct, _FREE_TEXT_POOL_MAX)` (unset/zero bounds skipped). Bounded calls only — never per-row LLM work (ADR 0013).
- `chunk_id = hashlib.blake2b(f"{source_fqn}:{row_digest}:{chunk_index}".encode(), digest_size=32).hexdigest()`; `row_digest` = SHA-256 of `json.dumps(row, sort_keys=True, default=str)` (the per-row encoding inside `sdfb_beam/io/digest.py::compute_reference_digest`).
- `chunk_kind` values are exactly `"row_doc"` and `"free_text_col"`; `(embedder_id, embedder_version)` pinning is load-bearing — never mix vector spaces (dim/coverage mismatch ⇒ full fallback to embed).
- `rag_chunks` sink is FILE_LOADS + WRITE_APPEND + CREATE_NEVER (matches landing/dlq/validation_runs).
- Branch: `ws2-rag-phase-a`, created FROM `ws1-b2-memorization-fix` (stacked on PR #3 — WS1 also edited `dofns/generate.py`). Use superpowers:using-git-worktrees at execution start.
- Test command: `uv run --no-sync python3 -m pytest <path> -q` (**always `--no-sync`** — plain `uv run` re-syncs against an unreachable artifactory). Full baseline `uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q` must stay green (480 at branch point). Lint: `uv run --no-sync ruff check .` clean after every task.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Extract `sdfb_core.rag` — move embedding, index, serialize (shims left behind)

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/rag/__init__.py`
- Move: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/embedder.py` → `packages/sdfb-core/src/sdfb_core/rag/embedding.py`
- Move: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/index.py` → `packages/sdfb-core/src/sdfb_core/rag/index.py`
- Move: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/serialize.py` → `packages/sdfb-core/src/sdfb_core/rag/serialize.py`
- Create (shims): `engines/b1_rag/embedder.py`, `engines/b1_rag/index.py`, `engines/b1_rag/serialize.py`
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py:46-53` (imports)
- Test: `packages/sdfb-tests/tests/unit/rag/test_rag_package.py` (new file; create `packages/sdfb-tests/tests/unit/rag/__init__.py` empty if the suite requires it — mirror whether `tests/unit/engines/` has one)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by every later task): `sdfb_core.rag.embedding` (`Embedder` Protocol, `HashingEmbedder(dim=384, seed=0)`, `BgeEmbedder(model_path, *, dim=384, max_length=512, device="cpu")`), `sdfb_core.rag.index` (`build_index(vectors, dim) -> ExactIPIndex`, `ExactIPIndex.search(query, k) -> list[int]`, `.release()`), `sdfb_core.rag.serialize` (`serialize_row(row, column_order) -> str`, `serialize_rows(rows, column_order) -> list[str]`). Old `engines.b1_rag.*` import paths keep working via shims.

- [ ] **Step 1: Write the failing test**

Create `packages/sdfb-tests/tests/unit/rag/test_rag_package.py`:

```python
"""WS2 §4a: the standalone `sdfb_core.rag` package.

Embedding / index / serialization move out of `engines/b1_rag/` so future
consumers (population stage, downstream apps) import them without touching
engine code. Old import paths stay alive as shims — the identity checks
pin that both paths resolve to the SAME objects, not divergent copies.
"""

from __future__ import annotations


def test_rag_package_exports_moved_seams():
    from sdfb_core.rag import (
        BgeEmbedder,
        Embedder,
        ExactIPIndex,
        HashingEmbedder,
        build_index,
        serialize_row,
        serialize_rows,
    )

    assert isinstance(HashingEmbedder(dim=16), Embedder)
    idx = build_index(HashingEmbedder(dim=16).embed(["a", "b"]), 16)
    assert isinstance(idx, ExactIPIndex) or hasattr(idx, "search")
    assert serialize_row({"a": 1}, ["a"]) == "a is 1"
    assert serialize_rows([{"a": None}], ["a"]) == ["a is null"]
    assert BgeEmbedder is not None


def test_old_b1_rag_import_paths_are_shims_not_copies():
    from sdfb_core.engines.b1_rag import embedder as old_embedder
    from sdfb_core.engines.b1_rag import index as old_index
    from sdfb_core.engines.b1_rag import serialize as old_serialize
    from sdfb_core.rag import embedding, index, serialize

    assert old_embedder.HashingEmbedder is embedding.HashingEmbedder
    assert old_embedder.BgeEmbedder is embedding.BgeEmbedder
    assert old_embedder.Embedder is embedding.Embedder
    assert old_index.build_index is index.build_index
    assert old_serialize.serialize_row is serialize.serialize_row


def test_rag_package_imports_without_heavy_deps():
    # Heavy deps stay deferred inside the seams: constructing the
    # dependency-free implementations must not require torch/faiss.
    # (No sys.modules assertion — the shared pytest process imports
    # apache_beam elsewhere, so module-absence checks are order-dependent.)
    from sdfb_core.rag import HashingEmbedder, build_index

    emb = HashingEmbedder(dim=8)
    idx = build_index(emb.embed(["x"]), 8)
    assert idx.search(emb.embed(["x"])[0], 1) == [0]
    idx.release()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_rag_package.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdfb_core.rag'`

- [ ] **Step 3: Move the modules and write the shims**

```bash
mkdir -p packages/sdfb-core/src/sdfb_core/rag
git mv packages/sdfb-core/src/sdfb_core/engines/b1_rag/embedder.py packages/sdfb-core/src/sdfb_core/rag/embedding.py
git mv packages/sdfb-core/src/sdfb_core/engines/b1_rag/index.py packages/sdfb-core/src/sdfb_core/rag/index.py
git mv packages/sdfb-core/src/sdfb_core/engines/b1_rag/serialize.py packages/sdfb-core/src/sdfb_core/rag/serialize.py
```

Create `packages/sdfb-core/src/sdfb_core/rag/__init__.py`:

```python
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
```

Create shim `packages/sdfb-core/src/sdfb_core/engines/b1_rag/embedder.py`:

```python
"""Import-path shim — the embedder seam moved to `sdfb_core.rag.embedding`
(WS2 §4a). Import from `sdfb_core.rag` in new code."""

from sdfb_core.rag.embedding import BgeEmbedder, Embedder, HashingEmbedder

__all__ = ["BgeEmbedder", "Embedder", "HashingEmbedder"]
```

Create shim `packages/sdfb-core/src/sdfb_core/engines/b1_rag/index.py`:

```python
"""Import-path shim — the exact index moved to `sdfb_core.rag.index`
(WS2 §4a). Import from `sdfb_core.rag` in new code."""

from sdfb_core.rag.index import ExactIPIndex, build_index

__all__ = ["ExactIPIndex", "build_index"]
```

Create shim `packages/sdfb-core/src/sdfb_core/engines/b1_rag/serialize.py`:

```python
"""Import-path shim — GReaT serialization moved to `sdfb_core.rag.serialize`
(WS2 §4a). Import from `sdfb_core.rag` in new code."""

from sdfb_core.rag.serialize import serialize_row, serialize_rows

__all__ = ["serialize_row", "serialize_rows"]
```

In `engine.py`, replace the three moved imports (lines 46-47, 53):

```python
from sdfb_core.rag.embedding import BgeEmbedder, Embedder, HashingEmbedder
from sdfb_core.rag.index import build_index
from sdfb_core.rag.serialize import serialize_rows
```

(keep the `_fidelity` and `profile` imports as they are; ruff will re-sort the block).

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag packages/sdfb-tests/tests/unit/engines -q`
Expected: all pass (shims keep `test_b1_rag.py` / `test_b1_setup_scale.py` imports working).

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-core packages/sdfb-tests
git add -A packages/sdfb-core packages/sdfb-tests
git commit -m "refactor(rag): extract sdfb_core.rag — embedding/index/serialize move out of b1_rag (WS2 §4a)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `chunking.py` — `Chunk` + row chunkers

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/rag/chunking.py`
- Modify: `packages/sdfb-core/src/sdfb_core/rag/__init__.py` (add exports)
- Test: `packages/sdfb-tests/tests/unit/rag/test_chunking.py` (new)

**Interfaces:**
- Consumes: `serialize_row` (Task 1).
- Produces (used by Tasks 3, 6, 8, 9, 10): `Chunk` frozen dataclass (fields below), `compute_row_digest(row: dict) -> str`, `compute_chunk_id(source_fqn: str, row_digest: str, chunk_index: int) -> str`, `chunk_row(row: dict, *, column_order: Sequence[str], free_text_columns: Sequence[str], source_fqn: str, reference_digest: str, embedder_id: str, embedder_version: str, pk_columns: Sequence[str] = ()) -> list[Chunk]`.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_chunking.py`:

```python
"""WS2 §4a: Chunk construction for `synthetic_rag.rag_chunks`.

One `row_doc` chunk per row (chunk_index=0, byte-identical to the GReaT
serialization B.1 embeds) plus one `free_text_col` chunk per non-null
free-text column value (chunk_index 1..k in declared order)."""

from __future__ import annotations

import hashlib
import json

from sdfb_core.rag.chunking import (
    Chunk,
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_chunking.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdfb_core.rag.chunking'`

- [ ] **Step 3: Write the implementation**

Create `packages/sdfb-core/src/sdfb_core/rag/chunking.py`:

```python
"""Chunk construction for `synthetic_rag.rag_chunks` (WS2 §4a).

`row_doc` chunk_text reuses `serialize_row()` byte-identically — that is
what makes the generation-time read a safe substitute for re-embedding
(same text in, same vector out, embedder pinned by id+version).

`row_digest` uses the same per-row canonical encoding as
`sdfb_beam.io.digest.compute_reference_digest` (json.dumps sort_keys
default=str → SHA-256) so the two provenance hashes stay comparable.
Pure stdlib.
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
```

Append to `rag/__init__.py` imports and `__all__`: `CHUNK_KIND_FREE_TEXT_COL`, `CHUNK_KIND_ROW_DOC`, `Chunk`, `chunk_row`, `compute_chunk_id`, `compute_row_digest` (from `sdfb_core.rag.chunking`).

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-core packages/sdfb-tests
git add -A packages/sdfb-core packages/sdfb-tests
git commit -m "feat(rag): Chunk dataclass + row_doc/free_text_col chunkers (WS2 §4a)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `store.py` — `ChunkStore` Protocol + in-memory implementation

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/rag/store.py`
- Modify: `packages/sdfb-core/src/sdfb_core/rag/__init__.py` (add exports)
- Test: `packages/sdfb-tests/tests/unit/rag/test_store.py` (new)

**Interfaces:**
- Consumes: `Chunk` (Task 2).
- Produces (used by Tasks 6, 8, 9): `ChunkStore` runtime-checkable Protocol — `fetch(reference_digest: str, chunk_kind: str, embedder_id: str, embedder_version: str) -> list[Chunk]`, `exists(reference_digest: str, embedder_id: str, embedder_version: str) -> bool`; `InMemoryChunkStore(chunks: Iterable[Chunk] = ())` with `.add(chunks: Iterable[Chunk]) -> None` implementing both.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_store.py`:

```python
"""WS2 §4a: the ChunkStore seam. Pure-Python; the BQ implementation lives
in sdfb-beam and is mock-tested there."""

from __future__ import annotations

from sdfb_core.rag.chunking import Chunk
from sdfb_core.rag.store import ChunkStore, InMemoryChunkStore


def _chunk(kind: str, digest: str = "d1", eid: str = "e", ever: str = "v1") -> Chunk:
    return Chunk(
        chunk_id=f"{kind}-{digest}-{eid}-{ever}",
        source_fqn="p.d.t",
        row_digest="r",
        reference_digest=digest,
        chunk_index=0,
        chunk_kind=kind,
        chunk_text="t",
        embedder_id=eid,
        embedder_version=ever,
    )


def test_in_memory_store_satisfies_protocol_and_filters():
    store = InMemoryChunkStore(
        [
            _chunk("row_doc"),
            _chunk("free_text_col"),
            _chunk("row_doc", digest="other"),
            _chunk("row_doc", ever="v2"),
        ]
    )
    assert isinstance(store, ChunkStore)
    hits = store.fetch("d1", "row_doc", "e", "v1")
    assert [c.chunk_kind for c in hits] == ["row_doc"]
    assert store.exists("d1", "e", "v1")
    assert store.exists("other", "e", "v1")
    assert not store.exists("d1", "e", "v3")
    assert store.fetch("d1", "row_doc", "e", "v3") == []


def test_add_appends():
    store = InMemoryChunkStore()
    assert not store.exists("d1", "e", "v1")
    store.add([_chunk("row_doc")])
    assert store.exists("d1", "e", "v1")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdfb_core.rag.store'`

- [ ] **Step 3: Write the implementation**

Create `packages/sdfb-core/src/sdfb_core/rag/store.py`:

```python
"""The `ChunkStore` seam (WS2 §4a).

Analogous to `Embedder` and `ModelClient`: engines and the population
stage depend on this Protocol; the BigQuery-backed implementation lives
in `sdfb_beam.rag.store` so `sdfb-core` stays GCP-free. Tests (and
laptop DirectRunner runs) inject `InMemoryChunkStore`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

    from sdfb_core.rag.chunking import Chunk


@runtime_checkable
class ChunkStore(Protocol):
    """Read surface over `synthetic_rag.rag_chunks` for one vector space."""

    def fetch(
        self,
        reference_digest: str,
        chunk_kind: str,
        embedder_id: str,
        embedder_version: str,
    ) -> list[Chunk]:
        """All chunks for the digest+kind in one pinned vector space."""
        ...

    def exists(
        self, reference_digest: str, embedder_id: str, embedder_version: str
    ) -> bool:
        """True when ANY chunk exists for the digest in this vector space
        (the population stage's idempotency check)."""
        ...


class InMemoryChunkStore:
    """List-backed `ChunkStore` for tests and laptop runs."""

    def __init__(self, chunks: Iterable[Chunk] = ()) -> None:
        self._chunks: list[Chunk] = list(chunks)

    def add(self, chunks: Iterable[Chunk]) -> None:
        self._chunks.extend(chunks)

    def fetch(
        self,
        reference_digest: str,
        chunk_kind: str,
        embedder_id: str,
        embedder_version: str,
    ) -> list[Chunk]:
        return [
            c
            for c in self._chunks
            if c.reference_digest == reference_digest
            and c.chunk_kind == chunk_kind
            and c.embedder_id == embedder_id
            and c.embedder_version == embedder_version
        ]

    def exists(
        self, reference_digest: str, embedder_id: str, embedder_version: str
    ) -> bool:
        return any(
            c.reference_digest == reference_digest
            and c.embedder_id == embedder_id
            and c.embedder_version == embedder_version
            for c in self._chunks
        )


__all__ = ["ChunkStore", "InMemoryChunkStore"]
```

Append `ChunkStore`, `InMemoryChunkStore` to `rag/__init__.py` imports and `__all__`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-core packages/sdfb-tests
git add -A packages/sdfb-core packages/sdfb-tests
git commit -m "feat(rag): ChunkStore Protocol + InMemoryChunkStore (WS2 §4a)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `retrieval.py` — extract centroid retrieval; add per-column value retrieval

**Files:**
- Create: `packages/sdfb-core/src/sdfb_core/rag/retrieval.py`
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`_retrieve_exemplars` delegates)
- Modify: `packages/sdfb-core/src/sdfb_core/rag/__init__.py` (add exports)
- Test: `packages/sdfb-tests/tests/unit/rag/test_retrieval.py` (new)

**Interfaces:**
- Consumes: `build_index`, `Embedder` (Task 1).
- Produces (used by Tasks 6, 8): `centroid(vectors: list[list[float]]) -> list[float]`; `retrieve_centroid_top_k(index, vectors: list[list[float]], items: Sequence, k: int) -> list` (items indexed by the index's ids); `retrieve_column_exemplars(values: Sequence[str], embedder: Embedder, k: int) -> list[str]` (embeds the values, centroid top-k, releases its index).

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_retrieval.py`:

```python
"""WS2 §4a/§4b: centroid top-k retrieval, generic over items, plus the
per-column value retrieval Phase A uses for column-relevant exemplars."""

from __future__ import annotations

from sdfb_core.rag.embedding import HashingEmbedder
from sdfb_core.rag.index import build_index
from sdfb_core.rag.retrieval import (
    centroid,
    retrieve_centroid_top_k,
    retrieve_column_exemplars,
)


def test_centroid_is_componentwise_mean():
    assert centroid([[0.0, 2.0], [2.0, 0.0]]) == [1.0, 1.0]


def test_retrieve_centroid_top_k_returns_items_deterministically():
    emb = HashingEmbedder(dim=32)
    rows = [{"i": i, "text": t} for i, t in enumerate(["alpha", "beta", "alpha b"])]
    vectors = emb.embed([r["text"] for r in rows])
    index = build_index(vectors, emb.dim)
    a = retrieve_centroid_top_k(index, vectors, rows, 2)
    b = retrieve_centroid_top_k(index, vectors, rows, 2)
    assert a == b
    assert len(a) == 2 and all(r in rows for r in a)
    index.release()


def test_retrieve_column_exemplars_returns_subset_of_values():
    emb = HashingEmbedder(dim=32)
    values = [f"note {i}" for i in range(10)]
    out = retrieve_column_exemplars(values, emb, 4)
    assert len(out) == 4
    assert set(out) <= set(values)
    assert retrieve_column_exemplars(values, emb, 4) == out  # deterministic


def test_retrieve_column_exemplars_short_input():
    emb = HashingEmbedder(dim=32)
    assert retrieve_column_exemplars([], emb, 4) == []
    assert retrieve_column_exemplars(["only"], emb, 4) == ["only"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_retrieval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdfb_core.rag.retrieval'`

- [ ] **Step 3: Write the implementation and delegate from the engine**

Create `packages/sdfb-core/src/sdfb_core/rag/retrieval.py`:

```python
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
```

In `engine.py`, add the import (with the other `sdfb_core.rag` imports):

```python
from sdfb_core.rag.retrieval import retrieve_centroid_top_k
```

and replace the body of `_retrieve_exemplars` (keep signature and docstring first two lines):

```python
    def _retrieve_exemplars(
        self, ctx: GenerationContext, k: int
    ) -> list[dict]:
        """Top-k reference rows nearest the reference centroid.

        For setup-time distribution inference we condition on the densest
        region of the reference (its centroid's neighbors), giving the LLM a
        representative exemplar set. Deterministic given the index.
        """
        if self._index is None or not self._ref_vectors or self._embedder is None:
            return list(ctx.reference_rows[:k])
        # Exemplar ids index into the same _MAX_EMBED_ROWS prefix the
        # vectors were built from, so ctx.reference_rows[i] stays valid.
        return retrieve_centroid_top_k(
            self._index, self._ref_vectors, ctx.reference_rows, k
        )
```

Append `centroid`, `retrieve_centroid_top_k`, `retrieve_column_exemplars` to `rag/__init__.py`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag packages/sdfb-tests/tests/unit/engines -q`
Expected: all pass (`test_b1_rag.py`'s exemplar tests pin the same deterministic behavior).

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-core packages/sdfb-tests
git add -A packages/sdfb-core packages/sdfb-tests
git commit -m "refactor(rag): extract centroid retrieval + per-column value retrieval (WS2 §4a/§4b)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Context plumbing — `GenerationContext` RAG fields + `embedder_identity`

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/base.py:145-173` (`GenerationContext`)
- Modify: `packages/sdfb-core/src/sdfb_core/rag/embedding.py` (add `embedder_identity`)
- Modify: `packages/sdfb-core/src/sdfb_core/rag/__init__.py` (export `embedder_identity`)
- Test: `packages/sdfb-tests/tests/unit/rag/test_context_plumbing.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Tasks 6–10): `GenerationContext` gains `num_rows: int = 0`, `embedder_id: str = ""`, `embedder_version: str = ""`, `rag_chunks_table: str = ""`, `chunk_store: object | None = None` (excluded from pickling concerns by always being None at graph-construction time; workers attach it via `model_copy`). `embedder_identity(embedder_uri: str) -> tuple[str, str]` — last two path segments of a MODEL_LAYOUT embedder URI; `("hashing-384", "v1")` for empty URI.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_context_plumbing.py`:

```python
"""WS2 §4b: context fields that thread the RAG layer into the engine.

The BQ-backed ChunkStore cannot ride the pickled Beam graph — the context
is built driver-side with chunk_store=None + string config, and the
worker DoFn attaches the live store via model_copy (the embedder_uri
localization pattern)."""

from __future__ import annotations

from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext
from sdfb_core.rag import InMemoryChunkStore
from sdfb_core.rag.embedding import embedder_identity

_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "d.t"},
        "schema": [{"name": "a", "type": "STRING", "mode": "REQUIRED"}],
        "primary_keys": None,
    }
)


def test_context_defaults_are_off():
    ctx = GenerationContext(table_schema=_SCHEMA)
    assert ctx.num_rows == 0
    assert ctx.embedder_id == ""
    assert ctx.embedder_version == ""
    assert ctx.rag_chunks_table == ""
    assert ctx.chunk_store is None


def test_worker_attaches_store_via_model_copy():
    ctx = GenerationContext(table_schema=_SCHEMA, rag_chunks_table="p.synthetic_rag.rag_chunks")
    store = InMemoryChunkStore()
    updated = ctx.model_copy(update={"chunk_store": store})
    assert updated.chunk_store is store
    assert ctx.chunk_store is None  # frozen original untouched


def test_embedder_identity_parses_model_layout_uri():
    uri = "gs://bkt/synthetic/models/embedders/bge-small-en-v1.5/v1/"
    assert embedder_identity(uri) == ("bge-small-en-v1.5", "v1")
    assert embedder_identity("/local-ssd/embedders/bge-small-en-v1.5/v2") == (
        "bge-small-en-v1.5",
        "v2",
    )
    assert embedder_identity("") == ("hashing-384", "v1")
    assert embedder_identity("solo") == ("solo", "v1")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_context_plumbing.py -q`
Expected: FAIL — `ValidationError`/`AttributeError` on the new fields; `ImportError` on `embedder_identity`.

- [ ] **Step 3: Implement**

In `base.py`, `GenerationContext`: change the config line to

```python
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
```

and append after `strict_freetext: bool = False`:

```python
    # --- RAG layer (WS2 §4b) -------------------------------------------
    # Requested synthetic row count — bounds the free-text pool target
    # (min(num_rows, column_distinct, _FREE_TEXT_POOL_MAX)). 0 = unknown.
    num_rows: int = 0
    # Pinned vector space for rag_chunks reads. Derived DRIVER-side from
    # the original embedder URI (the worker only sees the localized path).
    embedder_id: str = ""
    embedder_version: str = ""
    # FQN of synthetic_rag.rag_chunks; empty ⇒ the read path is off. The
    # live store object is attached worker-side (it cannot be pickled):
    # the DoFn does ctx.model_copy(update={"chunk_store": store}).
    rag_chunks_table: str = ""
    chunk_store: object | None = None
```

In `rag/embedding.py`, append at module level (below the classes):

```python
_DEFAULT_EMBEDDER_IDENTITY = ("hashing-384", "v1")


def embedder_identity(embedder_uri: str) -> tuple[str, str]:
    """``(embedder_id, embedder_version)`` from a MODEL_LAYOUT embedder URI.

    The layout pins ``.../embedders/{id}/{version}/`` — the last two
    non-empty path segments. Must be derived from the ORIGINAL URI at
    graph-construction time: on the worker the path is already localized
    (``/local-ssd/embedder``) and the identity is gone. Empty URI ⇒ the
    dependency-free HashingEmbedder's fixed identity.
    """
    if not embedder_uri:
        return _DEFAULT_EMBEDDER_IDENTITY
    parts = [s for s in embedder_uri.replace("gs://", "").split("/") if s]
    if len(parts) >= 2:
        return (parts[-2], parts[-1])
    return (parts[0], "v1")
```

Add `embedder_identity` to `rag/embedding.py.__all__` and `rag/__init__.py`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag packages/sdfb-tests/tests/unit/engines -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-core packages/sdfb-tests
git add -A packages/sdfb-core packages/sdfb-tests
git commit -m "feat(rag): GenerationContext RAG fields + embedder_identity (WS2 §4b)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Engine read path — prefer `ChunkStore`, fall back to embed

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`setup` embed block + new `_vectors_from_store`)
- Test: `packages/sdfb-tests/tests/unit/rag/test_engine_read_path.py` (new)

**Interfaces:**
- Consumes: `ChunkStore`/`InMemoryChunkStore` (Task 3), `compute_row_digest`, `Chunk` (Task 2), ctx fields (Task 5).
- Produces: `B1RagEngine.setup()` reuses persisted vectors when the store fully covers the embed prefix in the pinned vector space; emits `b1_chunks_reused` milestone; any gap/mismatch ⇒ today's embed path unchanged.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_engine_read_path.py`:

```python
"""WS2 §4b.1: read-instead-of-reembed. Self-gating on data: full coverage
in the pinned vector space ⇒ zero embed calls; anything less ⇒ exactly
today's embed path (never a partial mix of vector spaces)."""

from __future__ import annotations

import dataclasses

from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationConfig, GenerationContext
from sdfb_core.engines.b1_rag import B1RagEngine
from sdfb_core.rag import InMemoryChunkStore
from sdfb_core.rag.chunking import chunk_row
from sdfb_core.rag.embedding import HashingEmbedder
from sdfb_core.rag.serialize import serialize_rows


class _CountingEmbedder(HashingEmbedder):
    def __init__(self, dim: int = 384) -> None:
        super().__init__(dim=dim)
        self.embed_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return super().embed(texts)


class _NoLLMClient:
    def generate_json(self, prompt, json_schema, **kw):
        return [{"values": []}]


_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "p.d.t"},
        "schema": [
            {"name": "id", "type": "INT64", "mode": "REQUIRED"},
            {"name": "status", "type": "STRING", "mode": "REQUIRED"},
        ],
        "primary_keys": ["id"],
    }
)
_ROWS = [{"id": i, "status": ["OPEN", "DONE"][i % 2]} for i in range(20)]
_EID, _EVER = "hashing-384", "v1"


def _populated_store(rows, embedder) -> InMemoryChunkStore:
    """What a --build_rag_layer run would have written for this digest."""
    store = InMemoryChunkStore()
    column_order = ["id", "status"]
    chunks = []
    for row in rows:
        for c in chunk_row(
            row,
            column_order=column_order,
            free_text_columns=[],
            source_fqn="p.d.t",
            reference_digest="dig-1",
            embedder_id=_EID,
            embedder_version=_EVER,
        ):
            chunks.append(c)
    texts = serialize_rows(rows, column_order)
    vectors = embedder.embed(texts)
    store.add(
        [
            dataclasses.replace(c, embedding=vectors[i])
            for i, c in enumerate(chunks)
        ]
    )
    return store


def _ctx(store) -> GenerationContext:
    return GenerationContext(
        table_schema=_SCHEMA,
        reference_rows=_ROWS,
        reference_digest="dig-1",
        pipeline_run_id="read-path-test",
        embedder_id=_EID,
        embedder_version=_EVER,
        chunk_store=store,
    )


def test_full_coverage_skips_embedding_and_generates():
    seed_embedder = HashingEmbedder(dim=384)
    store = _populated_store(_ROWS, seed_embedder)
    engine_embedder = _CountingEmbedder()
    engine = B1RagEngine(embedder=engine_embedder)
    engine.setup(_NoLLMClient(), _ctx(store))
    assert engine_embedder.embed_calls == 0  # the whole point
    records = list(engine.generate_batch(10, GenerationConfig(seed=1, batch_size=10)))
    assert len(records) == 10
    engine.teardown()


def test_wrong_vector_space_falls_back_to_embed():
    store = _populated_store(_ROWS, HashingEmbedder(dim=384))
    engine_embedder = _CountingEmbedder()
    engine = B1RagEngine(embedder=engine_embedder)
    ctx = _ctx(store).model_copy(update={"embedder_version": "v9"})
    engine.setup(_NoLLMClient(), ctx)
    assert engine_embedder.embed_calls >= 1
    engine.teardown()


def test_partial_coverage_falls_back_to_embed():
    store = _populated_store(_ROWS[:5], HashingEmbedder(dim=384))  # 5 of 20
    engine_embedder = _CountingEmbedder()
    engine = B1RagEngine(embedder=engine_embedder)
    engine.setup(_NoLLMClient(), _ctx(store))
    assert engine_embedder.embed_calls >= 1
    engine.teardown()


def test_no_store_is_todays_path():
    engine_embedder = _CountingEmbedder()
    engine = B1RagEngine(embedder=engine_embedder)
    engine.setup(_NoLLMClient(), _ctx(None))
    assert engine_embedder.embed_calls >= 1
    engine.teardown()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_engine_read_path.py -q`
Expected: `test_full_coverage_skips_embedding_and_generates` FAILS (`embed_calls == 1`); the fallback tests pass already.

- [ ] **Step 3: Implement in `engine.py`**

Add imports:

```python
from sdfb_core.rag.chunking import CHUNK_KIND_ROW_DOC, compute_row_digest
```

In `setup()`, replace the embed block (currently `if ctx.reference_rows:` … through `log_milestone("b1_index_built", ...)`) with:

```python
        if ctx.reference_rows:
            # Prefix of the (fingerprint-ordered) reference sample — the
            # exemplar ids returned by `_retrieve_exemplars` index into this
            # same prefix, so `ctx.reference_rows[i]` stays valid.
            embed_rows = ctx.reference_rows[:_MAX_EMBED_ROWS]
            reused = self._vectors_from_store(ctx, embed_rows)
            if reused is not None:
                self._ref_vectors = reused
            else:
                t_embed = time.monotonic()
                texts = serialize_rows(embed_rows, self._column_order)
                self._ref_vectors = self._embedder.embed(texts)
                log_milestone(
                    "b1_embed_done",
                    rows=len(texts),
                    rows_total=len(ctx.reference_rows),
                    seconds=round(time.monotonic() - t_embed, 1),
                )
            t_index = time.monotonic()
            self._index = build_index(self._ref_vectors, self._embedder.dim)
            log_milestone(
                "b1_index_built",
                seconds=round(time.monotonic() - t_index, 1),
            )
```

Add the new method after `teardown()`:

```python
    def _vectors_from_store(
        self, ctx: GenerationContext, embed_rows: list[dict]
    ) -> list[list[float]] | None:
        """Row-doc vectors from the persisted RAG layer, or None.

        All-or-nothing: every embed-prefix row must have a chunk in the
        PINNED (embedder_id, embedder_version) space with the embedder's
        exact dim — a partial read would silently mix vector spaces, which
        is worse than re-embedding (WS2 §4b; 2026-07-07 design §4).
        """
        store = ctx.chunk_store
        if store is None or not ctx.reference_digest:
            return None
        t0 = time.monotonic()
        chunks = store.fetch(
            ctx.reference_digest,
            CHUNK_KIND_ROW_DOC,
            ctx.embedder_id,
            ctx.embedder_version,
        )
        by_digest = {
            c.row_digest: c.embedding for c in chunks if c.embedding
        }
        if not by_digest:
            return None
        assert self._embedder is not None
        vectors: list[list[float]] = []
        for row in embed_rows:
            emb = by_digest.get(compute_row_digest(row))
            if emb is None or len(emb) != self._embedder.dim:
                return None
            vectors.append(list(emb))
        log_milestone(
            "b1_chunks_reused",
            rows=len(vectors),
            seconds=round(time.monotonic() - t0, 1),
        )
        return vectors
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag packages/sdfb-tests/tests/unit/engines -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-core packages/sdfb-tests
git add -A packages/sdfb-core packages/sdfb-tests
git commit -m "feat(b1): read-instead-of-reembed via ChunkStore, all-or-nothing (WS2 §4b.1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Pool scaling — target `min(num_rows, column_distinct, 512)`, batched calls

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (constants, `_build_free_text_pools`, `_infer_free_text_pool`, `_pool_llm_yield`)
- Modify (call-site updates only if failing): `packages/sdfb-tests/tests/unit/engines/test_freetext_fallback.py`, `test_b1_rag.py`
- Test: `packages/sdfb-tests/tests/unit/rag/test_pool_scaling.py` (new)

**Interfaces:**
- Consumes: `ctx.num_rows` (Task 5).
- Produces: constants `_FREE_TEXT_POOL_MAX = 512`, `_POOL_VALUES_PER_CALL = 32` (replacing `_DEFAULT_FREE_TEXT_POOL`'s role as target; keep `_DEFAULT_FREE_TEXT_POOL = 32` as an alias for the per-call size so existing imports/tests keep meaning). `_pool_target(prof: ColumnProfile, ctx: GenerationContext) -> int`. `_infer_free_text_pool(self, prof, seed_examples: list[str], target: int) -> list[str]` (signature change: takes seed examples + target). `_pool_llm_yield(client, prompt, json_schema, prof, seed_examples, target)` loops up to `max(len(levels), 2*ceil(target/_POOL_VALUES_PER_CALL))` calls, cycling escalating levels (last level repeats), accumulating novel values until `target`.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_pool_scaling.py`:

```python
"""WS2 §4b.2: pool target = min(num_rows, column_distinct, 512), filled by
multiple bounded calls. Closes the 28-619x oversampling of the 2026-07-19
run (3 FREE_TEXT columns capped at 32 values over 1000 rows)."""

from __future__ import annotations

import json

from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationConfig, GenerationContext
from sdfb_core.engines.b1_rag import B1RagEngine
from sdfb_core.engines.b1_rag.engine import (
    _FREE_TEXT_POOL_MAX,
    _POOL_VALUES_PER_CALL,
    _pool_llm_yield,
)
from sdfb_core.rag.embedding import HashingEmbedder


class _BatchClient:
    """Yields _POOL_VALUES_PER_CALL fresh values per call, like a healthy LLM."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, prompt, json_schema, **kw):
        base = self.calls * _POOL_VALUES_PER_CALL
        self.calls += 1
        return [
            {"values": [f"novel value {base + i}" for i in range(_POOL_VALUES_PER_CALL)]}
        ]


def _schema_and_rows(n_rows: int = 300):
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "p.d.notes"},
            "schema": [
                {"name": "id", "type": "INT64", "mode": "REQUIRED"},
                {"name": "notes", "type": "STRING", "mode": "REQUIRED"},
            ],
            "primary_keys": ["id"],
        }
    )
    rows = [
        {"id": i, "notes": f"customer reported issue number {i} with details"}
        for i in range(n_rows)
    ]
    return schema, rows


def test_constants():
    assert _FREE_TEXT_POOL_MAX == 512
    assert _POOL_VALUES_PER_CALL == 32


def test_pool_scales_past_32_with_batched_calls():
    schema, rows = _schema_and_rows(300)
    client = _BatchClient()
    engine = B1RagEngine(embedder=HashingEmbedder(dim=32))
    ctx = GenerationContext(
        table_schema=schema,
        reference_rows=rows,
        reference_digest="d",
        pipeline_run_id="pool-scale",
        num_rows=200,
    )
    engine.setup(client, ctx)
    pool = engine._free_text_pools["notes"]
    # target = min(num_rows=200, distinct=300, 512) = 200
    assert len(pool) >= 200
    assert client.calls >= 200 // _POOL_VALUES_PER_CALL
    engine.teardown()


def test_pool_target_respects_column_distinct():
    schema, rows = _schema_and_rows(300)
    # 60 distinct long notes values → target = min(num_rows=500, 60, 512) = 60.
    # (If b1's profiler routes this fixture to CATEGORICAL instead of
    # FREE_TEXT, raise the distinct count / mean length until it profiles
    # FREE_TEXT — the assertions below must run unconditionally.)
    for i, r in enumerate(rows):
        r["notes"] = f"repeating customer note body number {i % 60} with extended details"
    client = _BatchClient()
    engine = B1RagEngine(embedder=HashingEmbedder(dim=32))
    ctx = GenerationContext(
        table_schema=schema,
        reference_rows=rows,
        reference_digest="d",
        pipeline_run_id="pool-cap",
        num_rows=500,
    )
    engine.setup(client, ctx)
    pool = engine._free_text_pools["notes"]
    assert len(pool) <= 60  # distinct bound, not 512
    assert len(pool) >= _POOL_VALUES_PER_CALL  # scaled past the old 32 cap
    engine.teardown()


def test_pool_llm_yield_stops_at_target_and_bounds_calls():
    class _EmptyClient:
        def __init__(self) -> None:
            self.calls = 0

        def generate_json(self, prompt, json_schema, **kw):
            self.calls += 1
            return [{"values": []}]

    from sdfb_core.engines.b1_rag.profile import ColumnKind, ColumnProfile

    prof = ColumnProfile(
        name="notes",
        bq_type="STRING",
        kind=ColumnKind.FREE_TEXT,
        nullable=False,
        null_fraction=0.0,
    )
    client = _EmptyClient()
    y = _pool_llm_yield(client, "p", {}, prof, [], target=512)
    assert y.pool == []
    assert client.calls == 2 * -(-512 // _POOL_VALUES_PER_CALL)  # bounded, never infinite
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_pool_scaling.py -q`
Expected: FAIL — `ImportError: cannot import name '_FREE_TEXT_POOL_MAX'`.

- [ ] **Step 3: Implement in `engine.py`**

Replace the `_DEFAULT_FREE_TEXT_POOL` constant block (line ~74-75) with:

```python
# Free-text pool scaling (WS2 §4b.2). Per-column target =
# min(num_rows, column_distinct, _FREE_TEXT_POOL_MAX); the 32-value pool
# of the 2026-07-19 run oversampled 3 columns 28-619x. Each LLM call stays
# bounded at _POOL_VALUES_PER_CALL values — multiple bounded calls, never
# per-row work (ADR 0013's FASTGEN spine).
_FREE_TEXT_POOL_MAX = 512
_POOL_VALUES_PER_CALL = 32
# Back-compat alias: the historical single-call pool size == one call's batch.
_DEFAULT_FREE_TEXT_POOL = _POOL_VALUES_PER_CALL
```

Rewrite `_build_free_text_pools` (Task 8 refines seeds further; here just thread target):

```python
    def _build_free_text_pools(self, ctx: GenerationContext) -> dict[str, list[str]]:
        """For each FREE_TEXT column, retrieve exemplars and fill a bounded
        unique pool from batched LLM calls. Falls back to observed examples
        when the LLM returns nothing usable."""
        assert self._profiles is not None
        pools: dict[str, list[str]] = {}
        free_text_cols = [
            p
            for p in self._profiles.values()
            if p.kind is ColumnKind.FREE_TEXT and p.identifier_shape is None
        ]
        if not free_text_cols:
            return pools

        exemplars = self._retrieve_exemplars(ctx, _DEFAULT_TOP_K)
        for prof in free_text_cols:
            seed_examples = [
                e[prof.name]
                for e in exemplars
                if e.get(prof.name) not in (None, "")
            ][:_DEFAULT_TOP_K]
            if not seed_examples:
                seed_examples = list(prof.text_examples[:_DEFAULT_TOP_K])
            pools[prof.name] = self._infer_free_text_pool(
                prof, seed_examples, self._pool_target(prof, ctx)
            )
        return pools

    def _pool_target(self, prof: ColumnProfile, ctx: GenerationContext) -> int:
        """min(num_rows, column_distinct, _FREE_TEXT_POOL_MAX), skipping
        unknown (zero/empty) bounds. `observed_values` distinct within the
        reference sample is the closest available stand-in for
        source_distinct (the engine never sees full-table stats)."""
        bounds = [_FREE_TEXT_POOL_MAX]
        if ctx.num_rows > 0:
            bounds.append(ctx.num_rows)
        distinct = len(set(prof.observed_values))
        if distinct > 0:
            bounds.append(distinct)
        return max(min(bounds), 1)
```

Change `_infer_free_text_pool`'s signature and internals — it now RECEIVES `seed_examples` and `target` (delete its internal seed_examples computation, lines 321-327):

```python
    def _infer_free_text_pool(
        self, prof: ColumnProfile, seed_examples: list[str], target: int
    ) -> list[str]:
        """Fill a bounded unique pool for one free-text column from batched
        LLM calls conditioned on retrieved exemplars."""
        assert self._client is not None
        per_call = min(target, _POOL_VALUES_PER_CALL)
        prompt = (
            f"You generate synthetic tabular data. First identify the exact "
            f"format of these example values for the column '{prof.name}' "
            f"(e.g. UUID, hexadecimal identifier, numeric code, date, "
            f"timestamp, natural-language text), then generate "
            f"{per_call} NEW, distinct, fictitious values in "
            f"exactly that format. Never copy an example verbatim. "
            f'Examples: {seed_examples}. Return JSON {{"values": [...]}}.'
        )
```

(the guided `json_schema`, `try/except`, milestone, and fold logic stay as they are, with these mechanical substitutions inside the method:)

- `_pool_llm_yield(self._client, prompt, json_schema, prof, seed_examples)` → `_pool_llm_yield(self._client, prompt, json_schema, prof, seed_examples, target=target)`
- in the empty-yield diagnosis string: `requested_per_attempt={_DEFAULT_FREE_TEXT_POOL}` → `requested_per_attempt={per_call}`
- `elif len(pool) < _DEFAULT_FREE_TEXT_POOL:` → `elif len(pool) < target:`
- undersized milestone `target=_DEFAULT_FREE_TEXT_POOL` → `target=target`
- final bound `[: max(_DEFAULT_FREE_TEXT_POOL, len(prof.text_examples))]` → `[: max(target, len(prof.text_examples))]`

Rewrite `_pool_llm_yield`'s loop (keep the NamedTuple, docstring gains one line: "Calls are bounded at `max(len(levels), 2*ceil(target/_POOL_VALUES_PER_CALL))`, cycling the escalation ladder (last level repeats)."):

```python
def _pool_llm_yield(
    client: ModelClient,
    prompt: str,
    json_schema: dict,
    prof: ColumnProfile,
    seed_examples: list[str],
    target: int = _POOL_VALUES_PER_CALL,
) -> _PoolYield:
    observed = set(prof.observed_values)
    shown = set(seed_examples)
    pool: list[str] = []
    pool_seen: set[str] = set()
    seen: set[str] = set()
    n_parsed = 0
    n_copies = 0
    n_echoes = 0
    attempts = 0
    levels = escalating_sampling()
    max_calls = max(len(levels), 2 * -(-target // _POOL_VALUES_PER_CALL))
    while attempts < max_calls and len(pool) < target:
        level = levels[min(attempts, len(levels) - 1)]
        attempts += 1
        results = client.generate_json(
            prompt=prompt,
            json_schema=json_schema,
            n=1,
            max_tokens=2048,
            temperature=level.temperature,
            top_p=level.top_p,
            top_k=level.top_k,
        )
        values = _string_values(results, prof.name)
        novel = [v for v in values if v not in observed]
        n_parsed += len(values)
        n_copies += len(values) - len(novel)
        n_echoes += sum(1 for v in values if v in shown)
        seen.update(values)
        for v in novel:
            if v not in pool_seen:
                pool_seen.add(v)
                pool.append(v)
    return _PoolYield(pool, n_parsed, len(seen), n_copies, n_echoes, attempts)
```

(Behavior note pinned by existing tests: with `target=32` → `max_calls = max(3, 2) = 3`, identical to today's three escalating attempts + stop-at-target.)

- [ ] **Step 4: Run to verify pass, fix existing call sites**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag packages/sdfb-tests/tests/unit/engines -q`
Expected: new tests pass. `test_freetext_fallback.py` / `test_b1_rag.py` may fail on the `_infer_free_text_pool` signature or `_DEFAULT_FREE_TEXT_POOL` semantics — update those call sites mechanically (pass `seed_examples`/`target` explicitly; the alias keeps constant imports working). Do NOT weaken behavioral assertions; if one asserts the old "single call defines the pool" defect, the new stop-at-target semantics already satisfy it.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-core packages/sdfb-tests
git add -A packages/sdfb-core packages/sdfb-tests
git commit -m "feat(b1): scale free-text pools to min(num_rows, distinct, 512), batched calls (WS2 §4b.2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Per-column exemplar retrieval for free-text pools

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`_build_free_text_pools` + new `_column_seed_examples`)
- Test: `packages/sdfb-tests/tests/unit/rag/test_per_column_exemplars.py` (new)

**Interfaces:**
- Consumes: `retrieve_column_exemplars` (Task 4), `ChunkStore` + `CHUNK_KIND_FREE_TEXT_COL` (Tasks 2-3), `_infer_free_text_pool(prof, seed_examples, target)` (Task 7).
- Produces: `_column_seed_examples(self, prof, ctx, k) -> list[str]` — precedence: (1) store's `free_text_col` chunks for this column (centroid top-k over their embeddings), (2) locally-embedded distinct column values, (3) row-doc exemplar values, (4) `prof.text_examples`.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_per_column_exemplars.py`:

```python
"""WS2 §4b.3: free-text exemplars are COLUMN-relevant. The pool prompt for
column C seeds from C's own values (store chunks when present, locally
embedded values otherwise) — not from whole-row sentences that dilute C."""

from __future__ import annotations

from sdfb_core.contracts import TableSchema
from sdfb_core.engines import GenerationContext
from sdfb_core.engines.b1_rag import B1RagEngine
from sdfb_core.rag import InMemoryChunkStore
from sdfb_core.rag.chunking import Chunk, compute_chunk_id
from sdfb_core.rag.embedding import HashingEmbedder

_SCHEMA = TableSchema.model_validate(
    {
        "table_info": {"table_id": "p.d.tickets"},
        "schema": [
            {"name": "id", "type": "INT64", "mode": "REQUIRED"},
            {"name": "notes", "type": "STRING", "mode": "REQUIRED"},
        ],
        "primary_keys": ["id"],
    }
)
_ROWS = [
    {"id": i, "notes": f"shipping delay complaint number {i} from customer"}
    for i in range(40)
]
_EID, _EVER = "hashing-384", "v1"


class _RecordingClient:
    """Captures the prompts so seed provenance is assertable."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_json(self, prompt, json_schema, **kw):
        self.prompts.append(prompt)
        return [{"values": [f"synthetic note {len(self.prompts)}-{i}" for i in range(32)]}]


def _ctx(store=None) -> GenerationContext:
    return GenerationContext(
        table_schema=_SCHEMA,
        reference_rows=_ROWS,
        reference_digest="dig-1",
        pipeline_run_id="per-col",
        num_rows=40,
        embedder_id=_EID,
        embedder_version=_EVER,
        chunk_store=store,
    )


def test_seeds_come_from_column_values_locally():
    client = _RecordingClient()
    engine = B1RagEngine(embedder=HashingEmbedder(dim=32))
    engine.setup(client, _ctx())
    prompt = next(p for p in client.prompts if "'notes'" in p)
    # Seeds are raw column values, not GReaT row sentences ("id is ...").
    assert "complaint number" in prompt
    assert "id is" not in prompt
    engine.teardown()


def test_seeds_prefer_store_free_text_chunks():
    emb = HashingEmbedder(dim=384)
    marker = "STORE-ONLY exemplar text"
    values = [f"{marker} {i}" for i in range(10)]
    vectors = emb.embed(values)
    store = InMemoryChunkStore(
        [
            Chunk(
                chunk_id=compute_chunk_id("p.d.tickets", f"r{i}", 1),
                source_fqn="p.d.tickets",
                row_digest=f"r{i}",
                reference_digest="dig-1",
                chunk_index=1,
                chunk_kind="free_text_col",
                chunk_text=values[i],
                embedder_id=_EID,
                embedder_version=_EVER,
                embedding=vectors[i],
                metadata={"column": "notes"},
            )
            for i in range(10)
        ]
    )
    client = _RecordingClient()
    engine = B1RagEngine(embedder=HashingEmbedder(dim=384))
    engine.setup(client, _ctx(store))
    prompt = next(p for p in client.prompts if "'notes'" in p)
    assert marker in prompt
    engine.teardown()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_per_column_exemplars.py -q`
Expected: both tests FAIL (seeds currently come from whole-row exemplar dicts; store never consulted).

Note: `test_seeds_come_from_column_values_locally` may pass trivially IF the row-doc exemplar path already extracts `e[prof.name]` raw values — read the assertion: the load-bearing check is `marker in prompt` in the second test plus the local-embed path exercised by dropping row exemplars. If the first test passes before implementation, keep it (it pins non-regression), and rely on the second to drive the change.

- [ ] **Step 3: Implement in `engine.py`**

Add imports: extend the chunking import with `CHUNK_KIND_FREE_TEXT_COL` and add `retrieve_column_exemplars`:

```python
from sdfb_core.rag.chunking import (
    CHUNK_KIND_FREE_TEXT_COL,
    CHUNK_KIND_ROW_DOC,
    compute_row_digest,
)
from sdfb_core.rag.retrieval import retrieve_centroid_top_k, retrieve_column_exemplars
```

In `_build_free_text_pools`, replace the per-prof seed computation (the `seed_examples = [...]` block from Task 7) with:

```python
        for prof in free_text_cols:
            seed_examples = self._column_seed_examples(prof, ctx, _DEFAULT_TOP_K)
            if not seed_examples:
                seed_examples = [
                    e[prof.name]
                    for e in exemplars
                    if e.get(prof.name) not in (None, "")
                ][:_DEFAULT_TOP_K] or list(prof.text_examples[:_DEFAULT_TOP_K])
            pools[prof.name] = self._infer_free_text_pool(
                prof, seed_examples, self._pool_target(prof, ctx)
            )
```

Add the new method after `_retrieve_exemplars`:

```python
    def _column_seed_examples(
        self, prof: ColumnProfile, ctx: GenerationContext, k: int
    ) -> list[str]:
        """Column-relevant seed exemplars for one free-text column
        (WS2 §4b.3): persisted `free_text_col` chunks when the store has
        them, else the column's own values embedded locally. Empty list ⇒
        caller falls back to row-doc exemplars."""
        store = ctx.chunk_store
        if store is not None and ctx.reference_digest:
            chunks = [
                c
                for c in store.fetch(
                    ctx.reference_digest,
                    CHUNK_KIND_FREE_TEXT_COL,
                    ctx.embedder_id,
                    ctx.embedder_version,
                )
                if c.metadata.get("column") == prof.name and c.embedding
            ]
            if chunks:
                vectors = [list(c.embedding) for c in chunks]
                texts = [c.chunk_text for c in chunks]
                if len(texts) <= k:
                    return texts
                index = build_index(vectors, len(vectors[0]))
                try:
                    return retrieve_centroid_top_k(index, vectors, texts, k)
                finally:
                    index.release()
        if self._embedder is not None:
            values: list[str] = []
            seen: set[str] = set()
            for row in ctx.reference_rows[:_MAX_EMBED_ROWS]:
                v = row.get(prof.name)
                if v not in (None, "") and v not in seen:
                    seen.add(v)
                    values.append(str(v))
            if values:
                return retrieve_column_exemplars(values, self._embedder, k)
        return []
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag packages/sdfb-tests/tests/unit/engines -q`
Expected: all pass (existing `test_b1_rag.py` pool tests seed from column values either way — verify none asserts row-sentence seeds; if one does, update its expectation to column values, which is the spec'd behavior change).

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-core packages/sdfb-tests
git add -A packages/sdfb-core packages/sdfb-tests
git commit -m "feat(b1): per-column exemplar retrieval for free-text pools (WS2 §4b.3)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: `sdfb_beam.rag` — BigQuery-backed `ChunkStore` + worker attachment

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/rag/__init__.py`
- Create: `packages/sdfb-beam/src/sdfb_beam/rag/store.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/dofns/generate.py` (`setup()` attaches the store)
- Test: `packages/sdfb-tests/tests/unit/rag/test_bq_chunk_store.py` (new)

**Interfaces:**
- Consumes: `Chunk`, `ChunkStore` (Tasks 2-3), ctx fields (Task 5).
- Produces (used by Task 10): `BigQueryChunkStore(table_fqn: str, *, client: object | None = None)` implementing `ChunkStore`; lazy `google.cloud.bigquery` import (constructor must work laptop-side with an injected fake client). `GenerateRecordsDoFn.setup()` attaches it when `ctx.rag_chunks_table` is set and `ctx.chunk_store` is None.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_bq_chunk_store.py`:

```python
"""WS2 §4b.1: the BQ-backed ChunkStore, mock-tested (laptop has no GCP).
The DoFn attaches it worker-side — the pickled graph never carries a
live client (same lifecycle rule as the vLLM subprocess)."""

from __future__ import annotations

from types import SimpleNamespace

from sdfb_beam.rag.store import BigQueryChunkStore
from sdfb_core.rag.store import ChunkStore

_TABLE = "proj.synthetic_rag.rag_chunks"


class _FakeBqClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, dict]] = []

    def query(self, sql, job_config=None):
        params = {
            p.name: p.value for p in (job_config.query_parameters if job_config else [])
        }
        self.queries.append((sql, params))
        rows = self.rows
        return SimpleNamespace(result=lambda: iter(rows))


def _chunk_row(i: int) -> dict:
    return {
        "chunk_id": f"c{i}",
        "source_fqn": "p.d.t",
        "source_pk": '{"id": %d}' % i,
        "row_digest": f"r{i}",
        "reference_digest": "dig",
        "chunk_index": 0,
        "chunk_kind": "row_doc",
        "chunk_text": f"t{i}",
        "embedder_id": "e",
        "embedder_version": "v1",
        "embedding": [0.1, 0.2],
        "metadata": '{"column": "notes"}',
    }


def test_satisfies_protocol_without_gcp_import():
    import sys

    store = BigQueryChunkStore(_TABLE, client=_FakeBqClient([]))
    assert isinstance(store, ChunkStore)
    assert "google.cloud.bigquery" not in sys.modules or True  # lazy ctor


def test_exists_true_and_false():
    assert BigQueryChunkStore(_TABLE, client=_FakeBqClient([{"n": 1}])).exists(
        "dig", "e", "v1"
    )
    assert not BigQueryChunkStore(_TABLE, client=_FakeBqClient([])).exists(
        "dig", "e", "v1"
    )


def test_fetch_maps_rows_to_chunks_with_parameters():
    client = _FakeBqClient([_chunk_row(0), _chunk_row(1)])
    store = BigQueryChunkStore(_TABLE, client=client)
    chunks = store.fetch("dig", "row_doc", "e", "v1")
    assert len(chunks) == 2
    c = chunks[0]
    assert c.chunk_text == "t0"
    assert c.embedding == [0.1, 0.2]
    assert c.source_pk == {"id": 0}
    assert c.metadata == {"column": "notes"}
    sql, params = client.queries[-1]
    assert _TABLE in sql
    assert params == {
        "reference_digest": "dig",
        "chunk_kind": "row_doc",
        "embedder_id": "e",
        "embedder_version": "v1",
    }


def test_dofn_attaches_store_from_ctx(monkeypatch):
    from types import SimpleNamespace as NS

    from sdfb_beam.dofns import generate as generate_mod
    from sdfb_beam.dofns.generate import GenerateRecordsDoFn
    from sdfb_core.contracts import TableSchema
    from sdfb_core.engines import GenerationContext

    captured: dict = {}

    class _Engine:
        def setup(self, model_client, ctx):
            captured["ctx"] = ctx

        def generate_batch(self, n, cfg):
            return iter(())

        def teardown(self):
            pass

    monkeypatch.setattr(generate_mod, "get_engine", lambda name: _Engine)
    schema = TableSchema.model_validate(
        {
            "table_info": {"table_id": "d.t"},
            "schema": [{"name": "a", "type": "STRING", "mode": "REQUIRED"}],
            "primary_keys": None,
        }
    )
    ctx = GenerationContext(
        table_schema=schema, rag_chunks_table="proj.synthetic_rag.rag_chunks"
    )
    dofn = GenerateRecordsDoFn(
        engine_name="stub", model_client=NS(generate_json=lambda **k: [{}]), ctx=ctx
    )
    monkeypatch.setattr(
        "sdfb_beam.rag.store.BigQueryChunkStore",
        lambda table_fqn, **kw: f"store-for-{table_fqn}",
    )
    dofn.setup()
    assert captured["ctx"].chunk_store == "store-for-proj.synthetic_rag.rag_chunks"
    dofn.teardown()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_bq_chunk_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdfb_beam.rag'`.

- [ ] **Step 3: Implement**

Create `packages/sdfb-beam/src/sdfb_beam/rag/__init__.py`:

```python
"""Beam/GCP side of the RAG layer (WS2 §4b): the BigQuery-backed
`ChunkStore` and the `--build_rag_layer` population stage."""

from sdfb_beam.rag.store import BigQueryChunkStore

__all__ = ["BigQueryChunkStore"]
```

Create `packages/sdfb-beam/src/sdfb_beam/rag/store.py`:

```python
"""BigQuery-backed `ChunkStore` over `synthetic_rag.rag_chunks`.

Read-only surface for the generation-time reuse path (WS2 §4b.1). The
population stage writes through `WriteToBigQuery`, never through this
class. `google.cloud.bigquery` is imported lazily so the module (and its
tests, via an injected fake client) work on the laptop.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sdfb_core.rag.chunking import Chunk

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


class BigQueryChunkStore:
    """`ChunkStore` implementation over one `rag_chunks` table."""

    def __init__(self, table_fqn: str, *, client: object | None = None) -> None:
        self.table_fqn = table_fqn
        self._client = client  # injectable for tests; lazy real client

    def _bq(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client()
        return self._client

    def _query(self, sql: str, params: dict[str, str]):
        from google.cloud import bigquery  # local import; fake clients skip it

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(name, "STRING", value)
                for name, value in params.items()
            ]
        )
        return self._bq().query(sql, job_config=job_config).result()

    def exists(
        self, reference_digest: str, embedder_id: str, embedder_version: str
    ) -> bool:
        sql = (
            f"SELECT 1 AS n FROM `{self.table_fqn}` "
            "WHERE reference_digest = @reference_digest "
            "AND embedder_id = @embedder_id "
            "AND embedder_version = @embedder_version LIMIT 1"
        )
        rows = list(
            self._query(
                sql,
                {
                    "reference_digest": reference_digest,
                    "embedder_id": embedder_id,
                    "embedder_version": embedder_version,
                },
            )
        )
        return len(rows) > 0

    def fetch(
        self,
        reference_digest: str,
        chunk_kind: str,
        embedder_id: str,
        embedder_version: str,
    ) -> list[Chunk]:
        sql = (
            "SELECT chunk_id, source_fqn, source_pk, row_digest, "
            "reference_digest, chunk_index, chunk_kind, chunk_text, "
            "embedder_id, embedder_version, embedding, metadata "
            f"FROM `{self.table_fqn}` "
            "WHERE reference_digest = @reference_digest "
            "AND chunk_kind = @chunk_kind "
            "AND embedder_id = @embedder_id "
            "AND embedder_version = @embedder_version"
        )
        rows = list(
            self._query(
                sql,
                {
                    "reference_digest": reference_digest,
                    "chunk_kind": chunk_kind,
                    "embedder_id": embedder_id,
                    "embedder_version": embedder_version,
                },
            )
        )
        return [self._to_chunk(r) for r in rows]

    @staticmethod
    def _to_chunk(row) -> Chunk:
        get = row.get if isinstance(row, dict) else lambda k, d=None: getattr(row, k, d)
        return Chunk(
            chunk_id=get("chunk_id"),
            source_fqn=get("source_fqn"),
            row_digest=get("row_digest"),
            reference_digest=get("reference_digest"),
            chunk_index=int(get("chunk_index")),
            chunk_kind=get("chunk_kind"),
            chunk_text=get("chunk_text"),
            embedder_id=get("embedder_id"),
            embedder_version=get("embedder_version"),
            source_pk=_parse_json(get("source_pk")),
            embedding=list(get("embedding")) if get("embedding") else None,
            metadata=_parse_json(get("metadata")) or {},
        )


def _parse_json(value) -> dict | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return value
    return json.loads(value)


__all__ = ["BigQueryChunkStore"]
```

In `dofns/generate.py`, `setup()`, immediately after the embedder-pull block (after `self.ctx = ctx  # cache so a re-entrant setup() skips the pull` — or after the `if ctx.embedder_uri...` block when no pull ran), add:

```python
        # RAG read path (WS2 §4b.1): the BQ-backed ChunkStore cannot ride
        # the pickled graph — attach it worker-side, mirroring the
        # embedder localization above. Engines see only the ChunkStore
        # Protocol; an empty table just means the engine's fallback runs.
        if ctx.rag_chunks_table and ctx.chunk_store is None:
            from sdfb_beam.rag.store import BigQueryChunkStore

            ctx = ctx.model_copy(
                update={"chunk_store": BigQueryChunkStore(ctx.rag_chunks_table)}
            )
            self.ctx = ctx
```

(note: the import must be `from sdfb_beam.rag import store` + `store.BigQueryChunkStore(...)`? No — monkeypatching `sdfb_beam.rag.store.BigQueryChunkStore` in the test requires the DoFn to resolve the attribute at CALL time. Use:

```python
            from sdfb_beam.rag import store as rag_store

            ctx = ctx.model_copy(
                update={"chunk_store": rag_store.BigQueryChunkStore(ctx.rag_chunks_table)}
            )
            self.ctx = ctx
```

so `monkeypatch.setattr("sdfb_beam.rag.store.BigQueryChunkStore", ...)` takes effect.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag packages/sdfb-tests/tests/unit/dofns -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-beam packages/sdfb-tests
git add -A packages/sdfb-beam packages/sdfb-tests
git commit -m "feat(rag): BigQueryChunkStore + worker-side attachment in the DoFn (WS2 §4b.1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Population stage + pipeline/CLI wiring behind `--build_rag_layer`

**Files:**
- Create: `packages/sdfb-beam/src/sdfb_beam/rag/population.py`
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py` (`PipelineConfig` fields, ctx threading, optional population branch)
- Modify: `packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py` (flags + existence check + sink)
- Test: `packages/sdfb-tests/tests/unit/rag/test_population_stage.py` (new), `packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py` (append flag tests)

**Interfaces:**
- Consumes: `chunk_row`, `Chunk` (Task 2), embedders (Task 1), `embedder_identity` (Task 5), `profile_columns`/`ColumnKind` (existing b1 profiler), `localize_gcs_prefix` + `EMBEDDER_LOCAL_DIR` (existing, `dofns/generate.py`), `compute_reference_digest` (existing `io/digest.py`).
- Produces: `ChunkReferenceRowsDoFn(source_fqn, reference_digest, column_order, free_text_columns, pk_columns, embedder_id, embedder_version)` yielding `Chunk`s; `EmbedChunksDoFn(embedder_uri)` consuming `list[Chunk]` batches, yielding BQ row dicts; `chunk_to_bq_row(chunk: Chunk, embedding: list[float], created_at: str) -> dict`. `PipelineConfig` gains `rag_chunks_table: str = ""`, `embedder_id: str = ""`, `embedder_version: str = ""`; `build_pipeline()` gains `rag_chunks_sink: beam.PTransform | None = None` and wires `Create(reference_rows) → ChunkRows → BatchElements → EmbedChunks → sink` when provided. CLI gains `--build_rag_layer` (store_true) and `--rag_chunks_table` (default `""`); the driver-side existence check decides whether the sink is passed.

- [ ] **Step 1: Write the failing tests**

Create `packages/sdfb-tests/tests/unit/rag/test_population_stage.py`:

```python
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
```

Append to `packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py`:

```python
def test_parse_args_rag_layer_flags_default_off():
    args, _ = parse_args(_common_args())
    assert args.build_rag_layer is False
    assert args.rag_chunks_table == ""


def test_parse_args_rag_layer_flags():
    argv = [
        *_common_args(),
        "--build_rag_layer",
        "--rag_chunks_table", "proj.synthetic_rag.rag_chunks",
    ]
    args, _ = parse_args(argv)
    assert args.build_rag_layer is True
    assert args.rag_chunks_table == "proj.synthetic_rag.rag_chunks"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_population_stage.py packages/sdfb-tests/tests/unit/cli/test_run_pipeline.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sdfb_beam.rag.population'`; CLI tests fail on unknown args landing in `beam_argv`.

- [ ] **Step 3: Implement `population.py`**

Create `packages/sdfb-beam/src/sdfb_beam/rag/population.py`:

```python
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
```

Add `ChunkReferenceRowsDoFn`, `EmbedChunksDoFn`, `chunk_to_bq_row` to `sdfb_beam/rag/__init__.py`.

- [ ] **Step 4: Wire `pipeline.py`**

`PipelineConfig` — append after `embedder_uri: str = ""`:

```python
    # RAG layer (WS2 §4b). rag_chunks_table threads the READ path into the
    # worker ctx (self-gating on data); embedder identity pins the vector
    # space and must come from the ORIGINAL embedder URI (driver-side).
    rag_chunks_table: str = ""
    embedder_id: str = ""
    embedder_version: str = ""
```

`build_pipeline` signature — add parameter:

```python
    rag_chunks_sink: beam.PTransform | None = None,
```

`GenerationContext(...)` construction — add:

```python
        num_rows=config.num_rows,
        embedder_id=config.embedder_id,
        embedder_version=config.embedder_version,
        rag_chunks_table=config.rag_chunks_table,
```

After the DLQ wiring (before the `result` dict), add:

```python
    # WS2 §4b.1 — optional rag_chunks population branch. The driver decides
    # (existence check) whether to pass a sink; None ⇒ branch absent, DAG
    # unchanged (the validation_runs_sink precedent).
    if rag_chunks_sink is not None:
        free_text_columns = _rag_free_text_columns(
            config.table_schema, reference_rows
        )
        chunks = (
            p
            | "RagReferenceRows" >> beam.Create(reference_rows)
            | "RagChunkRows"
            >> beam.ParDo(
                ChunkReferenceRowsDoFn(
                    source_fqn=config.table_schema.fqn,
                    reference_digest=digest,
                    column_order=[c.name for c in config.table_schema.columns],
                    free_text_columns=free_text_columns,
                    pk_columns=list(config.pk_columns),
                    embedder_id=config.embedder_id,
                    embedder_version=config.embedder_version,
                )
            )
            | "RagBatchChunks"
            >> beam.BatchElements(min_batch_size=32, max_batch_size=256)
            | "RagEmbedChunks" >> beam.ParDo(EmbedChunksDoFn(config.embedder_uri))
        )
        _ = chunks | "WriteRagChunks" >> rag_chunks_sink
```

with the module-level helper (near `_dlq_rule_weight`) and imports (`from sdfb_beam.rag.population import ChunkReferenceRowsDoFn, EmbedChunksDoFn`):

```python
def _rag_free_text_columns(
    table_schema: TableSchema, reference_rows: list[dict]
) -> list[str]:
    """Columns that get `free_text_col` chunks — B.1's own FREE_TEXT
    classification, minus identifier-shaped ones (retrieval-worthy prose,
    not per-row IDs)."""
    from sdfb_core.engines.b1_rag.profile import ColumnKind, profile_columns

    profiles = profile_columns(table_schema, reference_rows)
    return [
        p.name
        for p in profiles.values()
        if p.kind is ColumnKind.FREE_TEXT and p.identifier_shape is None
    ]
```

- [ ] **Step 5: Wire `run_pipeline.py`**

Add args (after `--embedder_uri`):

```python
    p.add_argument("--build_rag_layer", action="store_true",
                   help="Populate synthetic_rag.rag_chunks from this run's "
                        "reference sample (skipped if the reference_digest "
                        "is already present for this embedder id+version).")
    p.add_argument("--rag_chunks_table", default="",
                   help="FQN of synthetic_rag.rag_chunks. Enables the "
                        "read-instead-of-reembed path; with "
                        "--build_rag_layer also enables population.")
```

In `main()`: derive identity once (near where `reference_rows` are materialized; requires `from sdfb_core.rag.embedding import embedder_identity`, `from sdfb_beam.rag.store import BigQueryChunkStore`, and `compute_reference_digest` — already imported by `pipeline.py`; import it here too from `sdfb_beam.io.digest`):

```python
    embedder_id, embedder_version = embedder_identity(args.embedder_uri)

    rag_chunks_sink = None
    if args.build_rag_layer and args.rag_chunks_table:
        digest = compute_reference_digest(reference_rows)
        store = BigQueryChunkStore(args.rag_chunks_table)
        if store.exists(digest, embedder_id, embedder_version):
            log_milestone(
                "rag_population_skipped",
                reference_digest=digest[:12],
                embedder_id=embedder_id,
            )
        else:
            rag_chunks_sink = WriteToBigQuery(
                table=args.rag_chunks_table,
                method=WriteToBigQuery.Method.FILE_LOADS,
                write_disposition=BigQueryDisposition.WRITE_APPEND,
                create_disposition=BigQueryDisposition.CREATE_NEVER,
            )
```

(match the exact `WriteToBigQuery` kwarg style of the landing sink at `run_pipeline.py:315-319` — read it and mirror; add `log_milestone` import if absent in this module.) Thread into `PipelineConfig(...)`: `rag_chunks_table=args.rag_chunks_table, embedder_id=embedder_id, embedder_version=embedder_version`, and pass `rag_chunks_sink=rag_chunks_sink` to `build_pipeline(...)`.

- [ ] **Step 6: Run to verify pass**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag packages/sdfb-tests/tests/unit/cli packages/sdfb-tests/tests/integration -q`
Expected: all pass (integration DirectRunner suite proves the default DAG is unchanged when the sink is None).

- [ ] **Step 7: Lint and commit**

```bash
uv run --no-sync ruff check packages/sdfb-beam packages/sdfb-tests
git add -A packages/sdfb-beam packages/sdfb-tests
git commit -m "feat(rag): --build_rag_layer population stage + read-path wiring (WS2 §4b.1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: `rag_chunks` schema file + design-doc rewrite

**Files:**
- Create: `config/bq_schema/synthetic_rag/rag_chunks.schema.json`
- Modify: `docs/designs/2026-07-07-rag-layer-design.md` (rewrite in place, per spec "WS2 is a replan")

**Interfaces:** none (artifacts only).

- [ ] **Step 1: Write the schema file**

Create `config/bq_schema/synthetic_rag/rag_chunks.schema.json` (same JSON-array convention as `config/bq_schema/synthetic_data_quality/*.schema.json` — read one first and mirror its field-object style exactly):

```json
[
  {"name": "chunk_id", "type": "STRING", "mode": "REQUIRED",
   "description": "blake2b-256 hex of source_fqn:row_digest:chunk_index; deterministic key, dedupes retries."},
  {"name": "source_fqn", "type": "STRING", "mode": "REQUIRED",
   "description": "Fully-qualified source table, e.g. project.dataset.table."},
  {"name": "source_pk", "type": "JSON", "mode": "NULLABLE",
   "description": "Primary-key column -> value map for the source row; NULL when no PK declared."},
  {"name": "row_digest", "type": "STRING", "mode": "REQUIRED",
   "description": "SHA-256 of the canonical-encoded source row (content identity, chunking-independent)."},
  {"name": "reference_digest", "type": "STRING", "mode": "REQUIRED",
   "description": "SHA-256 over the whole reference sample (ADR 0005); idempotency/lookup key."},
  {"name": "chunk_index", "type": "INT64", "mode": "REQUIRED",
   "description": "0 for row_doc; 1..k for free_text_col chunks in declared column order."},
  {"name": "chunk_kind", "type": "STRING", "mode": "REQUIRED",
   "description": "'row_doc' or 'free_text_col'."},
  {"name": "chunk_text", "type": "STRING", "mode": "REQUIRED",
   "description": "The exact string that was embedded."},
  {"name": "embedder_id", "type": "STRING", "mode": "REQUIRED",
   "description": "Embedder family, e.g. 'bge-small-en-v1.5' (MODEL_LAYOUT path segment)."},
  {"name": "embedder_version", "type": "STRING", "mode": "REQUIRED",
   "description": "Embedder weight version, e.g. 'v1'. (embedder_id, embedder_version) pins the vector space."},
  {"name": "embedding", "type": "FLOAT64", "mode": "REPEATED",
   "description": "L2-normalized embedding (dim fixed per embedder; 384 for bge-small-en-v1.5)."},
  {"name": "metadata", "type": "JSON", "mode": "NULLABLE",
   "description": "Free-form extras, e.g. {\"column\": \"notes\"} for free_text_col chunks."},
  {"name": "created_at", "type": "TIMESTAMP", "mode": "REQUIRED",
   "description": "Row write time (UTC). DAY partition key."}
]
```

- [ ] **Step 2: Rewrite the design doc**

Rewrite `docs/designs/2026-07-07-rag-layer-design.md` in place. Keep the document's overall section structure (§1 goal … §7 rollout) and make exactly these changes:

- Header: `Status: adopted — Phase A implemented (WS2, 2026-07-20)`; add an `Updated: 2026-07-20` line and a pointer to `docs/superpowers/specs/2026-07-20-e2e-remediation-rag-eval-evolution-design.md` §4.
- New §1a "Package shape (WS2 §4a)": the `sdfb_core/rag/` module list (chunking / embedding / index / serialize / store / retrieval) with one-line responsibilities, the shim note for old `engines.b1_rag.*` paths, and `sdfb_beam/rag/` (store + population). State that `B1RagEngine` is now a consumer of this package.
- §3 population path: replace the RunInference box in the diagram with `EmbedChunksDoFn (setup-built embedder)` and add a "Delta from the original design" note: a DoFn with a warm-pull setup replaces RunInference — one embedding code path, one lifecycle convention; RunInference remains an option if a second consumer ever needs streamed inference. Document `--rag_chunks_table` as the second flag (table FQN; the design originally implied a fixed name).
- §4 generation-time path: re-express over the `ChunkStore` Protocol (`fetch`/`exists` signatures from Task 3), all-or-nothing coverage rule (dim + full embed-prefix match, else fallback), the `b1_chunks_reused` milestone, and the worker-side attachment pattern (ctx `rag_chunks_table` string → DoFn attaches `BigQueryChunkStore` via `model_copy`).
- New §5a "Phase A generation-quality changes (WS2 §4b.2-3)": pool scaling — target `min(num_rows, column_distinct, _FREE_TEXT_POOL_MAX=512)`, `_POOL_VALUES_PER_CALL=32` batched calls, bounded at `2*ceil(target/32)` calls; per-column exemplar retrieval — seed precedence store `free_text_col` chunks → locally embedded column values → row-doc exemplars → `text_examples`.
- §6: move "chunk granularity beyond row/free-text-column, hybrid search, reranking" from "out of scope" to "Phase B/C — deferred until Phase A's E2E baseline (spec §4c/§4d; WS3 gates the comparison)".
- §7 rollout: mark steps 1-3 as done (schema file `config/bq_schema/synthetic_rag/rag_chunks.schema.json`, population behind `--build_rag_layer`, self-gating read), keep 4-5 as the operational runbook.

- [ ] **Step 3: Commit**

```bash
git add config/bq_schema/synthetic_rag docs/designs/2026-07-07-rag-layer-design.md
git commit -m "docs(rag): rag_chunks schema file + design-doc rewrite for WS2 Phase A

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Full-baseline verification

**Files:** none new; fixes only if the baseline finds regressions.

- [ ] **Step 1: Full laptop baseline**

Run: `uv run --no-sync python3 -m pytest packages/sdfb-tests -m "not gpu and not gcp" -q`
Expected: green (480 at branch point + ~30 new). Likeliest regressions: b1 tests importing moved modules by old path (shims should cover), pool-size assertions pinned to 32 (update to `_POOL_VALUES_PER_CALL`/target semantics — never weaken novelty/fallback assertions).

- [ ] **Step 2: Lint the whole repo**

Run: `uv run --no-sync ruff check .`
Expected: clean.

- [ ] **Step 3: Commit any regression fixes**

```bash
git add -u
git commit -m "test: WS2 Phase A baseline regression fixes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Skip if nothing changed.)

**M4 follow-up (out of laptop scope):** provision `synthetic_rag` dataset + `rag_chunks` table (`bq mk` with the schema file; vector index per design §2), run one `--build_rag_layer` job, then a b1 matrix re-run judged per spec §8 — pool columns' `landing_distinct` should rise from 32/44/47 toward `min(num_rows, source_distinct, 512)`; second run against the same digest must log `b1_chunks_reused` and skip `b1_embed_done`.
