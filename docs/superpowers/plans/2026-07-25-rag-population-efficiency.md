# RAG Population Efficiency & CUDA Embed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `--build_rag_layer` population branch write only what its consumers can read (row-doc prefix + distinct free-text values) and let embedding use the worker's idle T4 — cutting the branch from 1,506 s / 60.8 % of wall clock (2026-07-25 06:18 E2E) to a couple of concurrent minutes, without changing any read contract.

**Architecture:** Three changes. (1) Population writes `row_doc` chunks only for the first `MAX_ROW_DOC_ROWS`=1024 fingerprint-ordered rows — exactly the prefix `B1RagEngine._vectors_from_store` reads (all-or-nothing) — instead of all 10k. (2) `free_text_col` chunks dedupe to distinct `(column, value)` pairs (first-seen order, capped 1024/column) computed driver-side, since the branch already starts from the in-memory `reference_rows`; identity moves from per-row to per-value digests, transparent to the consumer (`_fetch_free_text_chunks` uses only `chunk_text`/`embedding`/`metadata.column`). Beam still does the heavy part in parallel: Flatten → Reshuffle → BatchElements → EmbedChunksDoFn fans embedding out across workers. (3) `BgeEmbedder` gains `device="auto"` (CUDA when available) plus `demote_to_cpu()` so both the population DoFn and the engine's in-setup embed use the idle T4, then release VRAM before vLLM sizes its KV cache.

**Tech Stack:** pure-Python `sdfb-core` (stdlib only at module scope), Apache Beam transforms in `sdfb-beam`, fake-torch unit tests in `sdfb-tests` (laptop, no GPU).

## Global Constraints

- Laptop-only: no `@pytest.mark.gpu`/`gcp`; CUDA behavior tested with fake `torch` modules injected via `monkeypatch.setitem(sys.modules, ...)`.
- Test command: `uv run --no-sync python3 -m pytest -m "not gpu and not gcp" -q`; lint `uv run --no-sync ruff check .` — run UNPIPED so exit codes propagate.
- `sdfb-core` must not import beam/torch/numpy at module scope; `torch` stays a lazy import inside `BgeEmbedder`.
- The engine read contract is the spec: `_vectors_from_store` needs a `row_doc` chunk for every row in `ctx.reference_rows[:1024]`; `_fetch_free_text_chunks` needs `free_text_col` chunks with `metadata["column"]`, `chunk_text`, `embedding`. Population must keep satisfying both.
- Transform names `RagReferenceRows`/`RagChunkRows`/`RagEmbedChunks`/`WriteRagChunks` are mined by reports — keep them; new names are additive.
- Milestone names `[a-z0-9_]+` only; never log reference values (counts/devices only).
- Commit messages end with the standard Co-Authored-By + Claude-Session lines.

## Evidence baseline

2026-07-25 06:18 E2E: population branch = 1,506.7 s (60.8 % of 2,479 s wall), 33,610 chunks = 10,000 row-docs (~0.26 s each on CPU; only 1,024 ever read back) + 23,610 per-occurrence value chunks (duplicates of ~a few thousand distinct values). Engine's own 1024-row embed = 266.9 s on CPU while both T4s idle. Pool fixes from 2026-07-25 validated same run (pool build 1552→346 s).

---

### Task 1: Chunking scope helpers (`MAX_ROW_DOC_ROWS`, distinct values, value chunks)

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/rag/chunking.py`
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (replace the local `_MAX_EMBED_ROWS = 1024` with an import alias)
- Test: `packages/sdfb-tests/tests/unit/rag/test_chunking.py` (append)

**Interfaces:**
- Produces: `MAX_ROW_DOC_ROWS = 1024`, `MAX_FREE_TEXT_VALUES_PER_COLUMN = 1024` (module constants in `chunking.py`).
- Produces: `distinct_free_text_values(rows: list[dict], free_text_columns: Sequence[str], cap: int = MAX_FREE_TEXT_VALUES_PER_COLUMN) -> dict[str, list[str]]` — first-seen order, skips `None`/`""`, values stringified.
- Produces: `chunk_free_text_value(column: str, value: str, *, source_fqn, reference_digest, embedder_id, embedder_version) -> Chunk` — identity from the value digest (`compute_row_digest({"column": ..., "value": ...})`), `chunk_index=0`, `metadata={"column": column}`.

- [ ] **Step 1: Write the failing tests** — append to `test_chunking.py`:

```python
# --- population scoped to consumers (2026-07-25 06:18 E2E) -----------------

from sdfb_core.rag.chunking import (
    MAX_FREE_TEXT_VALUES_PER_COLUMN,
    MAX_ROW_DOC_ROWS,
    chunk_free_text_value,
    distinct_free_text_values,
)


def test_scope_constants_match_engine_read_contract():
    from sdfb_core.engines.b1_rag import engine as b1_engine

    assert MAX_ROW_DOC_ROWS == 1024
    assert b1_engine._MAX_EMBED_ROWS is MAX_ROW_DOC_ROWS
    assert MAX_FREE_TEXT_VALUES_PER_COLUMN == 1024


def test_distinct_free_text_values_dedupes_first_seen_and_caps():
    rows = (
        [{"notes": "beta", "code": "x"}]
        + [{"notes": "alpha", "code": "x"}] * 5
        + [{"notes": None, "code": "x"}, {"notes": "", "code": "x"}]
        + [{"notes": f"v{i}", "code": "x"} for i in range(10)]
    )
    out = distinct_free_text_values(rows, ["notes"], cap=4)
    assert out == {"notes": ["beta", "alpha", "v0", "v1"]}


def test_chunk_free_text_value_identity_is_value_keyed():
    kw = dict(
        source_fqn="p.d.t",
        reference_digest="dig",
        embedder_id="e",
        embedder_version="v1",
    )
    a1 = chunk_free_text_value("notes", "hello", **kw)
    a2 = chunk_free_text_value("notes", "hello", **kw)
    b = chunk_free_text_value("other", "hello", **kw)
    assert a1.chunk_id == a2.chunk_id            # same value -> same identity
    assert a1.chunk_id != b.chunk_id             # column participates
    assert a1.chunk_kind == "free_text_col"
    assert a1.chunk_text == "hello"
    assert a1.metadata == {"column": "notes"}
    assert a1.chunk_index == 0
    assert a1.source_pk is None
```

- [ ] **Step 2: Run to verify failure**

`uv run --no-sync python3 -m pytest packages/sdfb-tests/tests/unit/rag/test_chunking.py -q` — expect ImportError.

- [ ] **Step 3: Implement** — in `chunking.py` add after the `CHUNK_KIND_*` constants:

```python
# Population scope (2026-07-25 06:18 E2E postmortem). The only consumer of
# row_doc vectors is B1RagEngine._vectors_from_store, which reads EXACTLY
# the first MAX_ROW_DOC_ROWS fingerprint-ordered reference rows
# (all-or-nothing); embedding more is unreadable by design. The engine's
# _MAX_EMBED_ROWS aliases this constant — one source of truth for the
# write/read contract.
MAX_ROW_DOC_ROWS = 1024
# free_text_col chunks dedupe to distinct (column, value); a pathological
# near-unique column stays bounded here (consumers pick top-k=8 exemplars).
MAX_FREE_TEXT_VALUES_PER_COLUMN = 1024


def distinct_free_text_values(
    rows: list[dict],
    free_text_columns: Sequence[str],
    cap: int = MAX_FREE_TEXT_VALUES_PER_COLUMN,
) -> dict[str, list[str]]:
    """First-seen distinct non-empty values per free-text column, capped.

    Driver-side dedupe for the population branch: the 2026-07-25 E2E
    embedded 23,610 per-occurrence value chunks where the distinct value
    count was a fraction of that — identical strings re-embedded per row.
    First-seen over the fingerprint-ordered sample keeps the pick
    deterministic."""
    out: dict[str, list[str]] = {c: [] for c in free_text_columns}
    seen: dict[str, set[str]] = {c: set() for c in free_text_columns}
    for row in rows:
        for column in free_text_columns:
            if len(out[column]) >= cap:
                continue
            value = row.get(column)
            if value in (None, ""):
                continue
            text = str(value)
            if text in seen[column]:
                continue
            seen[column].add(text)
            out[column].append(text)
    return out


def chunk_free_text_value(
    column: str,
    value: str,
    *,
    source_fqn: str,
    reference_digest: str,
    embedder_id: str,
    embedder_version: str,
) -> Chunk:
    """One deduped free_text_col `Chunk` for a distinct (column, value).

    Identity is VALUE-keyed (digest of {"column","value"}), not row-keyed:
    the consumer (`_fetch_free_text_chunks`) reads only chunk_text /
    embedding / metadata["column"], so per-row provenance bought nothing
    but duplicate embeds. chunk_index is fixed 0 — identity is carried by
    the value digest."""
    value_digest = compute_row_digest({"column": column, "value": value})
    return Chunk(
        chunk_id=compute_chunk_id(source_fqn, value_digest, 0),
        source_fqn=source_fqn,
        row_digest=value_digest,
        reference_digest=reference_digest,
        chunk_index=0,
        chunk_kind=CHUNK_KIND_FREE_TEXT_COL,
        chunk_text=value,
        embedder_id=embedder_id,
        embedder_version=embedder_version,
        source_pk=None,
        metadata={"column": column},
    )
```

Add both helpers + constants to `__all__`. In `engine.py` replace:

```python
_MAX_EMBED_ROWS = 1024
```

with an import at the top (extend the existing `sdfb_core.rag.chunking` import) and the alias comment:

```python
from sdfb_core.rag.chunking import (
    CHUNK_KIND_FREE_TEXT_COL,
    CHUNK_KIND_ROW_DOC,
    MAX_ROW_DOC_ROWS,
    compute_row_digest,
)
...
# Setup embeds at most this many reference rows (aliases the population
# write contract — see chunking.MAX_ROW_DOC_ROWS).
_MAX_EMBED_ROWS = MAX_ROW_DOC_ROWS
```

(keep the existing long rationale comment above it).

- [ ] **Step 4: Run tests** — chunking file + full rag/engines dirs green.
- [ ] **Step 5: Full suite + lint (unpiped), commit** — `perf(rag): scope population to consumers — row-doc prefix constant + distinct value chunks`

---

### Task 2: CUDA-aware `BgeEmbedder` (`device="auto"`) + `demote_to_cpu()`

**Files:**
- Modify: `packages/sdfb-core/src/sdfb_core/rag/embedding.py`
- Test: `packages/sdfb-tests/tests/unit/rag/test_embedding_device.py` (create)

**Interfaces:**
- Produces: `BgeEmbedder(model_path, *, dim=384, max_length=512, device="cpu")` where `device` accepts `"cpu" | "cuda" | "auto"`; `"auto"` resolves via `torch.cuda.is_available()` at construction.
- Produces: `BgeEmbedder.device -> str` property (resolved device) and `BgeEmbedder.demote_to_cpu() -> None` (moves weights to CPU, calls `torch.cuda.empty_cache()`, idempotent, no-op when already CPU).

- [ ] **Step 1: Write the failing tests** — create `test_embedding_device.py` with a fully fake torch (never the real one — deterministic on any machine):

```python
"""BgeEmbedder device selection + VRAM release (2026-07-25 E2E).

Both T4s sat idle while 33,610 chunks embedded on CPU (1,506 s). "auto"
uses CUDA when available; demote_to_cpu() releases VRAM afterward so
vLLM's ignition (which sizes its KV-cache budget from free memory) never
competes with a resident embedder. Fake torch modules keep this laptop-
runnable and deterministic.
"""

from __future__ import annotations

import sys
import types

import pytest


def _fake_stack(monkeypatch, cuda_available: bool, log: dict):
    class _FakeModel:
        def to(self, device):
            log["moves"] = log.get("moves", []) + [device]
            return self

        def eval(self):
            return self

    class _Loader:
        @staticmethod
        def from_pretrained(path, **kwargs):
            return _FakeModel()

    transformers_mod = types.ModuleType("transformers")
    transformers_mod.AutoModel = _Loader
    transformers_mod.AutoTokenizer = _Loader
    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = types.SimpleNamespace(
        is_available=lambda: cuda_available,
        empty_cache=lambda: log.__setitem__("emptied", log.get("emptied", 0) + 1),
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers_mod)
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


def test_auto_resolves_to_cuda_when_available(monkeypatch, tmp_path):
    from sdfb_core.rag.embedding import BgeEmbedder

    log: dict = {}
    _fake_stack(monkeypatch, cuda_available=True, log=log)
    emb = BgeEmbedder(str(tmp_path), device="auto")
    assert emb.device == "cuda"
    assert log["moves"] == ["cuda"]


def test_auto_falls_back_to_cpu(monkeypatch, tmp_path):
    from sdfb_core.rag.embedding import BgeEmbedder

    log: dict = {}
    _fake_stack(monkeypatch, cuda_available=False, log=log)
    emb = BgeEmbedder(str(tmp_path), device="auto")
    assert emb.device == "cpu"


def test_demote_to_cpu_moves_and_frees(monkeypatch, tmp_path):
    from sdfb_core.rag.embedding import BgeEmbedder

    log: dict = {}
    _fake_stack(monkeypatch, cuda_available=True, log=log)
    emb = BgeEmbedder(str(tmp_path), device="auto")
    emb.demote_to_cpu()
    assert emb.device == "cpu"
    assert log["moves"] == ["cuda", "cpu"]
    assert log["emptied"] == 1
    emb.demote_to_cpu()  # idempotent — no second move/empty
    assert log["moves"] == ["cuda", "cpu"]
    assert log["emptied"] == 1


def test_explicit_cpu_never_touches_cuda(monkeypatch, tmp_path):
    from sdfb_core.rag.embedding import BgeEmbedder

    log: dict = {}
    _fake_stack(monkeypatch, cuda_available=True, log=log)
    emb = BgeEmbedder(str(tmp_path))
    assert emb.device == "cpu"
    emb.demote_to_cpu()
    assert log.get("emptied") is None
```

- [ ] **Step 2: Run to verify failure** — `device` kwarg unknown / no `device` property.
- [ ] **Step 3: Implement** in `embedding.py`:
  - `__init__(self, model_path, *, dim=384, max_length=512, device="cpu")`; inside the construction lock, after the lazy imports: `if device == "auto": device = "cuda" if torch.cuda.is_available() else "cpu"`.
  - Add `@property def device(self) -> str: return self._device`.
  - Add:

```python
    def demote_to_cpu(self) -> None:
        """Move weights to CPU and release the CUDA cache. Idempotent.

        Callers demote as soon as bulk embedding is done: vLLM's ignition
        sizes its KV-cache budget from free GPU memory, so a resident
        embedder must not still be holding VRAM by then (ADR 0019)."""
        if self._device != "cuda":
            return
        self._model = self._model.to("cpu")
        self._device = "cpu"
        cuda = getattr(self._torch, "cuda", None)
        if cuda is not None:
            cuda.empty_cache()
```

  - Update the class docstring line "wrapping `bge-small-en-v1.5` on CPU" → "on CPU or CUDA (`device='auto'`)".
- [ ] **Step 4: Run tests** — new file + `test_embedding_construction.py` still green (its fake torch lacks `cuda`; default `device="cpu"` never touches it).
- [ ] **Step 5: Full suite + lint (unpiped), commit** — `feat(rag): CUDA-aware BgeEmbedder with demote_to_cpu for vLLM coexistence`

---

### Task 3: Rewire population branch + GPU embed in DoFn and engine

**Files:**
- Modify: `packages/sdfb-beam/src/sdfb_beam/pipeline.py:210-233`
- Modify: `packages/sdfb-beam/src/sdfb_beam/rag/population.py` (`EmbedChunksDoFn` device + teardown)
- Modify: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py` (`device="auto"`, demote after index, `device=` field on embed milestones)
- Test: `packages/sdfb-tests/tests/unit/rag/test_population_stage.py` (extend), `packages/sdfb-tests/tests/unit/rag/test_b1_shape_fallback.py` (no change — regression guard via suite)

**Interfaces:**
- Consumes: Task 1's `MAX_ROW_DOC_ROWS`, `distinct_free_text_values`, `chunk_free_text_value`; Task 2's `device="auto"` / `demote_to_cpu()`.
- Produces: pipeline branch shape `RagReferenceRows(Create prefix rows) → RagChunkRows(row-doc only)` ∪ `RagValueChunks(Create (col,val) → Map)` → `RagFanout(Reshuffle) → RagBatchChunks → RagEmbedChunks → WriteRagChunks`.
- Produces: `EmbedChunksDoFn(embedder_uri, device="auto")` with `teardown()` demote.

- [ ] **Step 1: Write the failing tests** — extend `test_population_stage.py`:

```python
def test_population_branch_dedupes_values_and_caps_row_docs():
    """Wiring contract: row_docs only for the MAX_ROW_DOC_ROWS prefix,
    free_text_col chunks one per distinct (column, value)."""
    from sdfb_core.rag.chunking import (
        MAX_ROW_DOC_ROWS,
        chunk_free_text_value,
        distinct_free_text_values,
    )

    rows = [{"id": i, "notes": f"note {i % 3}", "code": "A"} for i in range(50)]
    values = distinct_free_text_values(rows, ["notes"])
    assert values == {"notes": ["note 0", "note 1", "note 2"]}

    with TestPipeline() as p:
        row_docs = (
            p
            | "Rows" >> beam.Create(rows[:MAX_ROW_DOC_ROWS])
            | "Chunk" >> beam.ParDo(
                ChunkReferenceRowsDoFn(**{**_DOFN_KW, "free_text_columns": []})
            )
        )
        value_chunks = (
            p
            | "Vals" >> beam.Create(
                [(c, v) for c, vs in values.items() for v in vs]
            )
            | "ValChunk" >> beam.MapTuple(
                lambda c, v: chunk_free_text_value(
                    c, v,
                    source_fqn=_DOFN_KW["source_fqn"],
                    reference_digest=_DOFN_KW["reference_digest"],
                    embedder_id=_DOFN_KW["embedder_id"],
                    embedder_version=_DOFN_KW["embedder_version"],
                )
            )
        )
        out = (
            (row_docs, value_chunks)
            | beam.Flatten()
            | "Fanout" >> beam.Reshuffle()
            | "Batch" >> beam.BatchElements(min_batch_size=1, max_batch_size=10)
            | "Embed" >> beam.ParDo(EmbedChunksDoFn(embedder_uri=""))
            | beam.combiners.ToList()
        )

        def _check(rows_out):
            kinds = [r["chunk_kind"] for r in rows_out]
            assert kinds.count("row_doc") == 50
            assert kinds.count("free_text_col") == 3  # deduped, not 50
            for r in rows_out:
                assert len(r["embedding"]) == 384

        _ = out | beam.Map(lambda x, f=_check: f(x))


def test_embed_dofn_teardown_demotes_gpu_embedder():
    class _FakeEmbedder:
        def __init__(self):
            self.demoted = 0

        def demote_to_cpu(self):
            self.demoted += 1

    dofn = EmbedChunksDoFn(embedder_uri="")
    dofn._embedder = _FakeEmbedder()
    fake = dofn._embedder
    dofn.teardown()
    assert fake.demoted == 1
    assert dofn._embedder is None
```

- [ ] **Step 2: Run to verify failure** — `EmbedChunksDoFn` has no `teardown`; the wiring test passes only after Task 1 (it uses Task-1 helpers, exercises the intended shape).
- [ ] **Step 3: Implement**

`population.py` — `EmbedChunksDoFn`:

```python
    def __init__(self, embedder_uri: str, device: str = "auto") -> None:
        super().__init__()
        self.embedder_uri = embedder_uri
        self.device = device
        self._embedder = None

    def setup(self):
        ...
        if uri:
            from sdfb_core.rag.embedding import BgeEmbedder

            # "auto" = the worker's GPU when present (2026-07-25 E2E: both
            # T4s idle while 33,610 chunks embedded on CPU for 25 min).
            self._embedder = BgeEmbedder(uri, device=self.device)
        ...

    def teardown(self):
        # Release VRAM before anything else (vLLM) sizes its budget.
        demote = getattr(self._embedder, "demote_to_cpu", None)
        if callable(demote):
            demote()
        self._embedder = None
```

Also update the module docstring's scope paragraph: row_docs cover the `MAX_ROW_DOC_ROWS` prefix (the exact read contract of `_vectors_from_store`), free-text chunks are distinct `(column, value)` pairs capped per column — reason: the 2026-07-25 06:18 run embedded 33,610 chunks of which ~90 % were unreadable-by-design or duplicates.

`pipeline.py` — replace the branch body (keep names, add new ones):

```python
    if rag_chunks_sink is not None:
        free_text_columns = _rag_free_text_columns(
            config.table_schema, reference_rows
        )
        # Row-doc chunks cover EXACTLY the engine's read prefix
        # (_vectors_from_store is all-or-nothing over rows[:1024]) — the
        # 2026-07-25 06:18 run embedded all 10k rows and 90% could never
        # be read back. Value chunks dedupe to distinct (column, value),
        # computed driver-side (reference_rows is already in memory here).
        distinct_values = distinct_free_text_values(
            reference_rows, free_text_columns
        )
        row_doc_chunks = (
            p
            | "RagReferenceRows"
            >> beam.Create(reference_rows[:MAX_ROW_DOC_ROWS])
            | "RagChunkRows"
            >> beam.ParDo(
                ChunkReferenceRowsDoFn(
                    source_fqn=config.table_schema.fqn,
                    reference_digest=digest,
                    column_order=[c.name for c in config.table_schema.columns],
                    free_text_columns=[],  # value chunks come deduped below
                    pk_columns=list(config.pk_columns),
                    embedder_id=config.embedder_id,
                    embedder_version=config.embedder_version,
                )
            )
        )
        value_chunks = (
            p
            | "RagDistinctValues"
            >> beam.Create(
                [(c, v) for c, vals in distinct_values.items() for v in vals]
            )
            | "RagValueChunks"
            >> beam.MapTuple(
                lambda column, value: chunk_free_text_value(
                    column,
                    value,
                    source_fqn=config.table_schema.fqn,
                    reference_digest=digest,
                    embedder_id=config.embedder_id,
                    embedder_version=config.embedder_version,
                )
            )
        )
        chunks = (
            (row_doc_chunks, value_chunks)
            | "RagAllChunks" >> beam.Flatten()
            # Spread the (now small) chunk set across workers so the embed
            # stage keeps Beam's embarrassing parallelism.
            | "RagFanout" >> beam.Reshuffle()
            | "RagBatchChunks"
            >> beam.BatchElements(min_batch_size=32, max_batch_size=256)
            | "RagEmbedChunks" >> beam.ParDo(EmbedChunksDoFn(config.embedder_uri))
        )
        _ = chunks | "WriteRagChunks" >> rag_chunks_sink
```

with imports at the top of `pipeline.py`: `from sdfb_core.rag.chunking import MAX_ROW_DOC_ROWS, chunk_free_text_value, distinct_free_text_values`.

NOTE: the `lambda column, value: chunk_free_text_value(...)` closes over `config`/`digest` — both picklable driver-side values; if pickling complains, hoist into a module-level helper taking explicit kwargs via `functools.partial`.

`engine.py` — in `setup()`:
- `self._embedder = BgeEmbedder(ctx.embedder_uri, device="auto")`
- milestone: `log_milestone("b1_embed_done", ..., device=getattr(self._embedder, "device", "cpu"))`
- after the `b1_index_built` milestone (and in the `chunks_reused` path too), add:

```python
            # Bulk embedding is done — release VRAM before vLLM ignition
            # sizes its KV-cache budget (ADR 0019). Later seed-example
            # embeds are tiny and run fine on CPU.
            demote = getattr(self._embedder, "demote_to_cpu", None)
            if callable(demote):
                demote()
```

placed once, after the whole embed+index `if ctx.reference_rows:` block.

- [ ] **Step 4: Run tests** — population + rag + engines + dofns dirs, then full suite. Update `test_population_branch_end_to_end_direct_runner` if its 3-chunk expectation changes (it drives the DoFns directly, so it should still pass — `ChunkReferenceRowsDoFn` itself is unchanged).
- [ ] **Step 5: Full suite + lint (unpiped), commit** — `perf(rag): population writes read-contract scope only + GPU embed with VRAM demote`

---

### Task 4: ADR 0019 + schema description + memory

**Files:**
- Create: `docs/adr/0019-rag-population-scoped-to-consumers.md`
- Modify: `docs/adr/README.md` (index line)
- Modify: `config/bq_schema/synthetic_rag/rag_chunks.schema.json` (`chunk_index` + `row_digest` description text only — value chunks carry a value digest)

- [ ] **Step 1: Write ADR 0019** covering: context (06:18 E2E numbers), decision (prefix cap, value dedupe+cap, driver-side distinct + Beam Reshuffle fan-out, `device="auto"` + demote-before-vLLM policy, branch kept in-job now that it underruns setup), consequences (population ≈ 350–450 CPU-s → ~minutes concurrent; ~90 % embed waste removed; WRITE_APPEND duplicate semantics now value-stable), VRAM budget note (bge-small ≈150 MB + activations vs vLLM `gpu_memory_utilization` sizing — demote closes the window), and deferred options (population-only GPU-less job/template; same-run chunk consumption).
- [ ] **Step 2: Update schema JSON descriptions** — `row_digest`: "SHA-256 of the canonical-encoded source row (row_doc) or of {column,value} (free_text_col value chunks)"; `chunk_index`: "0 for row_doc and for deduped free_text_col value chunks (identity carried by row_digest)".
- [ ] **Step 3: Commit** — `docs(adr): 0019 — RAG population scoped to consumers, CUDA embed policy`

---

## Self-review notes

- User asks → tasks: cap row_docs (T1+T3), dedupe values (T1+T3), CUDA/"make the most of vLLM and CUDA" (T2+T3 — vLLM side was yesterday's n=4/parallel ladders, validated in the 06:18 run), Beam parallelism (T3 Reshuffle fan-out preserved/explicit), reliability (demote-to-CPU idempotent, teardown, read contracts pinned by tests), accuracy (distinct values improve exemplar diversity; first-seen deterministic).
- Type consistency: `distinct_free_text_values(rows, free_text_columns, cap)` (T1) used in T3; `chunk_free_text_value(column, value, *, source_fqn, reference_digest, embedder_id, embedder_version)` matches both call sites; `BgeEmbedder(uri, device=...)`/`demote_to_cpu()`/`.device` consistent across T2/T3.
- Known interaction: T3's wiring test imports T1 helpers — execute in order.
