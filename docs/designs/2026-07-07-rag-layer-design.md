# Design — Persistent RAG layer (`synthetic_rag.rag_chunks`)

- **Status**: proposed
- **Date**: 2026-07-07
- **Scope**: M2 candidate. No implementation in this document — design only.
- **Author context**: ACTION_4 from the M1→M2 planning pass (see project memory).

## 1. Goal & motivation

Today, every B.1 (`B1RagEngine`) worker re-embeds the entire reference sample from
scratch on every pipeline run, in `setup()`:

```python
# packages/sdfb-core/src/sdfb_core/engines/b1_rag/engine.py:123-129
if ctx.reference_rows:
    texts = serialize_rows(ctx.reference_rows, self._column_order)
    self._ref_vectors = self._embedder.embed(texts)
    self._index = build_index(self._ref_vectors, self._embedder.dim)
```

This is correct but wasteful in two ways:

1. **Redundant compute.** The reference-data skill (`.claude/skills/reference-data.md`)
   already establishes that reference rows are a *live SELECT* keyed by a
   `reference_digest` (SHA-256 over canonical-encoded rows, computed in
   `sdfb_beam/io/digest.py` per ADR 0005). When two runs happen to pull an
   identical reference sample (same `reference_digest`), or when the same run
   fans out to N workers, every worker pays the embedding cost again — CPU-bound
   `BgeEmbedder.embed()` (`packages/sdfb-core/src/sdfb_core/engines/b1_rag/embedder.py:150-175`)
   over up to 10k rows, per worker, per run.
2. **No downstream reuse.** The embeddings that condition B.1's free-text
   inference are thrown away in `teardown()` (`engine.py:136-149`). They have
   no life outside one worker's process. Any other consumer that wants
   semantically-searchable vectors over the same reference data — a chatbot
   grounded on the table, a knowledge-graph / entity-resolution pass, a
   generic RAG app, or a human debugging "why did the LLM pick this
   exemplar?" — has nothing to query. Today the *only* artifact that outlives
   a run is the synthetic rows themselves plus the `reference_digest` string
   in `validation_runs`.

**Goal:** persist the embeddings computed for one `reference_digest` exactly
once, in a canonical, queryable BigQuery table with BigQuery-native vector
search — so that (a) the synthesis pipeline stops re-embedding a reference
sample it has already embedded, and (b) any downstream GenAI / KG / chatbot
application can `VECTOR_SEARCH` the same table without needing its own
embedding pipeline or vector database. Generation-time behavior is otherwise
unchanged: B.1's local FAISS index is still built at worker `setup()` time —
it's just built *from* the persisted vectors instead of by re-embedding, when
they're available.

This is explicitly a **reuse-of-computation** design, not a retrieval-quality
change: B.1's retrieval semantics (row-as-document GReaT serialization,
exact cosine top-k, centroid-seeded exemplar retrieval) do not change. Only
*where the vectors come from* changes.

## 2. Storage model

### Dataset

`synthetic_rag` — a new BigQuery dataset, sibling to `synthetic_data` (landing)
and `synthetic_data_quality` (DLQ + validation_runs), created the same way
(`bq mk --dataset`, same region as the reference table — `europe-west3` per
ADR 0004). It is **cross-cutting**: unlike `synthetic_data`, which has one
table per source table, `synthetic_rag.rag_chunks` is a single table shared
across every source table the pipeline has ever run against, distinguished by
`source_fqn`.

### Table: `synthetic_rag.rag_chunks`

```sql
CREATE TABLE `{project}.synthetic_rag.rag_chunks` (
  chunk_id          STRING    NOT NULL
    OPTIONS(description="blake2b-256 hex digest of source_fqn || row_digest || chunk_index; deterministic primary key, dedupes retries."),
  source_fqn        STRING    NOT NULL
    OPTIONS(description="Fully-qualified source table this chunk was derived from, e.g. project.dataset.table (== TableSchema.fqn / table_info.table_id)."),
  source_pk         JSON
    OPTIONS(description="Primary-key column -> value map for the source row, e.g. {\"customer_id\": 4821}. NULL when the source table declares no primary_keys (TableSchema.primary_keys is None)."),
  row_digest        STRING    NOT NULL
    OPTIONS(description="SHA-256 of the canonical-encoded source row (same encoding as compute_canonical_digest in reference-data.md), independent of chunking — identifies the row content itself."),
  reference_digest  STRING    NOT NULL
    OPTIONS(description="SHA-256 over the whole reference sample this row was pulled as part of (sdfb_beam/io/digest.py). Groups chunks by 'which pipeline run's reference set produced them' and is the idempotency/lookup key for the generation-time read."),
  chunk_index       INT64     NOT NULL
    OPTIONS(description="0 for the row_doc chunk; 0..k-1 for free_text_col chunks when a row yields more than one (currently one per free-text column)."),
  chunk_kind        STRING    NOT NULL
    OPTIONS(description="'row_doc' (GReaT-style whole-row serialization) or 'free_text_col' (single free-text column's value)."),
  chunk_text        STRING    NOT NULL
    OPTIONS(description="The exact string that was embedded — reproduces serialize_row() output for row_doc, or the raw column value for free_text_col."),
  embedder_id       STRING    NOT NULL
    OPTIONS(description="Embedder family identifier, e.g. 'bge-small-en-v1.5'. Matches the embedder_uri path segment in MODEL_LAYOUT.md's embedders/ tree."),
  embedder_version  STRING    NOT NULL
    OPTIONS(description="Embedder weight version, e.g. 'v1' (the {version} path segment). (embedder_id, embedder_version) pins the vector space."),
  embedding         ARRAY<FLOAT64>
    OPTIONS(description="L2-normalized embedding vector, dim = embedder's native dim (384 for bge-small-en-v1.5). Normalized at write time so COSINE distance == inner product, matching build_index()'s FAISS IndexFlatIP convention."),
  metadata          JSON
    OPTIONS(description="Free-form extras: e.g. {\"column\": \"notes\"} for free_text_col chunks, {\"table_schema_version\": ...} for provenance. Never load-bearing for retrieval."),
  created_at        TIMESTAMP NOT NULL
    OPTIONS(description="Row write time (UTC). DAY partition key.")
)
PARTITION BY DATE(created_at)
CLUSTER BY reference_digest, source_fqn;
```

Column-by-column notes:

- **`chunk_id`** is the table's natural dedup key: `blake2b(f"{source_fqn}:{row_digest}:{chunk_index}".encode(), digest_size=32).hexdigest()`.
  Blake2b is chosen over SHA-256 purely for speed at write-time scale (millions
  of chunks across tables) — there is no cryptographic requirement here, only
  determinism and low collision probability, so any reasonable fast hash
  works; blake2b is the stdlib (`hashlib.blake2b`) choice. It is **not**
  declared `PRIMARY KEY` — BigQuery primary keys are unenforced metadata, and
  the idempotency check in §3 does its own existence query rather than
  relying on constraint enforcement.
- **`source_pk` + `row_digest`** together are the source-linkage story: given a
  `rag_chunks` row, `source_pk` lets a downstream consumer look the row back
  up in the live source table (`SELECT * FROM {source_fqn} WHERE {pk_col} =
  {pk_val} ...` for each key in the map) *if the row still exists there*, and
  `row_digest` is a content-addressed identity that survives even if the
  source row has since been updated or deleted — two chunks with the same
  `row_digest` are provably the same row content regardless of which run or
  which reference pull produced them. When `TableSchema.primary_keys` is
  `None` (no declared PK — legal per M1's schema contract), `source_pk` is
  `NULL` and `row_digest` is the *only* linkage back to row content (no
  guaranteed way to re-locate the live row, which is expected: PK-less tables
  already have this limitation everywhere else in the pipeline, e.g. identity
  columns in `sdfb_core/engines/identity.py`).
- **`reference_digest`** is reused verbatim from the existing provenance
  concept (`.claude/skills/reference-data.md`, ADR 0005) — it is the join key
  between `rag_chunks` and `validation_runs`, and the lookup key for the
  generation-time read in §4.
- **`embedding` as `ARRAY<FLOAT64>`** (not a fixed-size `ARRAY<FLOAT64>(384)`)
  — BigQuery's `VECTOR_SEARCH`/`CREATE VECTOR INDEX` accept variable-length
  `ARRAY<FLOAT64>` columns; fixed dimensionality is enforced by the writer
  (one embedder per table, `(embedder_id, embedder_version)` pinned — see §4),
  not by the column type.

### Vector index

```sql
CREATE VECTOR INDEX rag_chunks_embedding_idx
ON `{project}.synthetic_rag.rag_chunks`(embedding)
STORING (chunk_id, source_fqn, source_pk, row_digest, reference_digest,
         chunk_kind, chunk_text, embedder_id, embedder_version)
OPTIONS (
  index_type = 'IVF',
  distance_type = 'COSINE'
);
```

`STORING` is populated with every column a downstream consumer needs without a
second lookup (chunk_text for display, source_pk/row_digest for linkage,
embedder identifiers to filter mixed vector spaces) — `embedding` itself and
`metadata`/`created_at`/`chunk_index` are left out of `STORING` since they are
either the indexed column itself or rarely needed at query time (a consumer
can always join back to `rag_chunks` by `chunk_id` for the rest). IVF is
BigQuery's supported vector index type for this workload (approximate,
partitions vectors into lists); COSINE matches the normalized-embedding
convention already established by `build_index()`
(`packages/sdfb-core/src/sdfb_core/engines/b1_rag/index.py:1-17`, exact
`IndexFlatIP` over L2-normalized vectors == cosine). BigQuery requires a
minimum row count before an IVF index is actually built (silently falls back
to brute-force `VECTOR_SEARCH` below that threshold) — acceptable, since below
that threshold brute-force is fast anyway.

### Example downstream query

A GenAI app (chatbot, KG entity-linker, or any consumer outside this
pipeline) retrieving the top-8 semantically nearest chunks to a query vector,
scoped to one source table and one embedder version:

```sql
SELECT
  base.chunk_id,
  base.source_fqn,
  base.source_pk,
  base.chunk_text,
  base.chunk_kind,
  distance
FROM VECTOR_SEARCH(
  TABLE `{project}.synthetic_rag.rag_chunks`,
  'embedding',
  (SELECT @query_embedding AS embedding),
  top_k => 8,
  distance_type => 'COSINE',
  options => '{"fraction_lists_to_search": 0.05}'
)
WHERE base.source_fqn = 'my-proj.raw.customers'
  AND base.embedder_id = 'bge-small-en-v1.5'
  AND base.embedder_version = 'v1'
ORDER BY distance ASC;
```

`@query_embedding` is produced by embedding the caller's query text with the
*same* embedder (`bge-small-en-v1.5`) — this design does not prescribe how a
downstream GenAI app runs that embedding step; it only guarantees the vector
space it can compare against is documented (`embedder_id`/`embedder_version`)
and durable.

## 3. Population path

A new, **opt-in** Beam stage, gated behind a `--build_rag_layer` flag (default
`false`) on `sdfb_beam/cli/run_pipeline.py`'s existing argparse surface
(alongside `--ddl_uri`, `--reference_table`, etc. —
`packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py:64-95`). When set, it
adds one branch to the DAG built by `build_pipeline()`
(`packages/sdfb-beam/src/sdfb_beam/pipeline.py`), fed from the same
`reference_rows` PCollection the engine already consumes — no second BQ read.

```
                         ┌─────────────────────────┐
 reference_rows ────────▶│ ReferenceDigestKnown?    │  (side input: existing-rows
 (existing PCollection,  │  skip if rows already    │   count for this reference_digest,
  side input today)      │  exist for this digest   │   read once at DAG construction)
                         └───────────┬─────────────┘
                                     │ (only when NOT already populated)
                                     ▼
                         ┌─────────────────────────┐
                         │ ChunkRowsDoFn            │  row-as-doc (chunk_index=0,
                         │  (sdfb-core, pure-Python)│  chunk_kind='row_doc') +
                         │                          │  one chunk per free-text column
                         └───────────┬─────────────┘  (chunk_kind='free_text_col')
                                     ▼
                         ┌─────────────────────────┐
                         │ RunInference             │  same EmbedderModelHandler
                         │ (EmbedderModelHandler)   │  wrapping BgeEmbedder — the
                         │                          │  embedder DoFn's setup() already
                         └───────────┬─────────────┘  does the GCS warm-pull (generate.py:66-91)
                                     ▼
                         ┌─────────────────────────┐
                         │ AttachIdsAndMetadata     │  chunk_id (blake2), source_pk,
                         │ DoFn                     │  row_digest, reference_digest,
                         │                          │  created_at
                         └───────────┬─────────────┘
                                     ▼
                         ┌─────────────────────────┐
                         │ WriteToBigQuery          │  FILE_LOADS, WRITE_APPEND,
                         │ (rag_chunks)             │  CREATE_NEVER (matches landing/
                         │                          │  dlq/validation_runs convention)
                         └─────────────────────────┘
```

Design notes:

- **Idempotency key is `reference_digest`, not `row_digest`.** Before running
  the chunk/embed/write branch, the pipeline driver issues one lightweight
  existence check —
  `SELECT COUNT(1) > 0 FROM synthetic_rag.rag_chunks WHERE reference_digest = @d
  AND embedder_id = @eid AND embedder_version = @ev LIMIT 1` — against the
  digest computed for *this run's* reference pull (the same digest already
  computed today by `sdfb_beam/io/digest.py` per ADR 0005, available before
  the DAG is constructed since the reference read + digest happen on the
  driver side per the reference-data skill). If rows already exist for that
  `(reference_digest, embedder_id, embedder_version)` triple, the branch is
  skipped entirely — no chunking, no `RunInference`, no write. This mirrors
  the "read reference live every run, but let the digest identify re-runs
  that saw the same data" pattern already established for `validation_runs`.
  Doing the check once at DAG-construction time (not per-worker) keeps it a
  single BQ query rather than N.
- **Why not `row_digest`-level dedup instead?** `reference_digest`-level dedup
  is coarser but matches the actual cost driver: the expensive step is
  the RunInference embedding pass, which today runs once per *pipeline run*
  regardless of row-level overlap between runs. Row-level dedup (skip
  individual rows already embedded under a *different* reference pull) is a
  legitimate future optimization but adds a per-row existence lookup that
  this design defers — out of scope per §6.
- **Chunker reuses existing pure-Python primitives.** Row-as-document chunking
  calls the identical `serialize_row()` used today
  (`packages/sdfb-core/src/sdfb_core/engines/b1_rag/serialize.py:20-31`), so
  `chunk_text` for `chunk_kind='row_doc'` is byte-identical to what B.1
  embeds in `setup()` today — this is what makes the generation-time
  substitution in §4 safe (same text in, same vector out, modulo the embedder
  being pinned by version). Free-text chunking is a new, small DoFn: for each
  column the DDL-derived schema profiles as free text (reusing
  `profile_columns` / `ColumnKind.FREE_TEXT` from
  `packages/sdfb-core/src/sdfb_core/engines/b1_rag/profile.py`), emit one
  chunk per non-null value with `chunk_text` = the raw column value,
  `chunk_kind='free_text_col'`, `metadata={"column": <col_name>}`.
- **Embedder reuse, not duplication.** The population path uses the *same*
  `ModelHandler`/`RunInference` wrapper around `BgeEmbedder` that the
  `model-handler.md` skill and `generate.py`'s embedder warm-pull already
  establish (`packages/sdfb-beam/src/sdfb_beam/dofns/generate.py:36-91` pulls
  `ctx.embedder_uri` to `/local-ssd/embedder` before engine `setup()`) — this
  design does not introduce a second embedding code path, only a second
  *consumer* of the existing one (RunInference in a standalone stage instead
  of inline inside the engine's `setup()`).
- **Write matches the existing sink convention exactly**: `FILE_LOADS` +
  `WRITE_APPEND` + `CREATE_NEVER`
  (`packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py:252-269`) — the table
  must be pre-created via `bq mk --schema` against the DDL in §2, following
  the same provisioning story as `dlq`/`validation_runs` in
  `docs/DEPLOYMENT_PREREQUISITES.md` (§ "BigQuery — datasets & tables").
  `rag_chunks` is added to that document's provisioning table when this design
  is implemented — not part of this document's scope to edit, since M1
  contents are frozen; noted here as the follow-on doc change.

## 4. Generation-time path

`B1RagEngine.setup()` changes from *always embed* to *prefer read, fall back
to embed*:

```
setup(model_client, ctx):
  1. profile columns (unchanged)
  2. IF ctx.reference_rows:
       a. query synthetic_rag.rag_chunks WHERE reference_digest = ctx.reference_digest
          AND chunk_kind = 'row_doc'
          AND embedder_id = <configured embedder_id>
          AND embedder_version = <configured embedder_version>
       b. IF rows returned (count > 0):
            - build self._ref_vectors from the returned `embedding` arrays,
              self._row_texts from `chunk_text` (order joined back to
              ctx.reference_rows via row_digest, since BQ read order is not
              guaranteed to match ctx.reference_rows order)
            - build_index(self._ref_vectors, dim)   # same FAISS/py fallback as today
            - SKIP the embed-on-worker path entirely
          ELSE (table empty for this digest — first run, or --build_rag_layer
                was never turned on, or embedder version was bumped):
            - fall back to TODAY'S path exactly:
              texts = serialize_rows(...); self._embedder.embed(texts); build_index(...)
  3. free-text pools built exactly as today (unchanged — §4 does not touch
     _build_free_text_pools; that stays retrieval-conditioned on whichever
     index was built in step 2)
```

Key properties:

- **Correctness is order-independent of which branch ran.** Whether vectors
  came from BQ or from re-embedding, `_ref_vectors` and `_index` end up
  populated the same way (same dim, same normalization contract in
  `build_index()`), so every downstream engine method
  (`_retrieve_exemplars`, `_infer_free_text_pool`) is unaffected — this is
  purely a `setup()`-internal optimization, not a behavior change to
  `generate_batch()`.
- **`(embedder_id, embedder_version)` pinning is load-bearing.** Mixing
  vectors from `bge-small-en-v1.5/v1` with a future `v2` (retrained /
  re-quantized weights) in one FAISS index would silently corrupt cosine
  similarity — nearest-neighbor search across two different embedding spaces
  is meaningless even though the vectors are the same dimensionality. The
  read query in step 2a filters on both fields explicitly so a version bump
  is invisible to the read (it just naturally falls to the "no rows for this
  digest+version" branch → re-embed), never a silent mix.
- **The BQ read is a small side input, not a second big reference pull.**
  Reading `chunk_text` + `embedding` for one `reference_digest` is bounded by
  the reference sample size (same order of magnitude as the existing
  `--reference_rows_limit`, default 10k rows) and happens once per worker in
  `setup()` — the same lifecycle stage that pays the embed cost today, so
  there is no new per-batch cost.
- **No change to `GenerationContext`'s public shape is required beyond what
  already exists** — `ctx.reference_digest` is already a field
  (`packages/sdfb-core/src/sdfb_core/engines/base.py:86`). The engine gains an
  injectable "chunk reader" seam (a `Protocol`, analogous to `Embedder` and
  `ModelClient`) so `sdfb-core` stays Beam-free: production wires a BQ-backed
  reader constructed in `sdfb-beam` and passed to the engine the same way
  `ctx.embedder_uri` is threaded through today; tests inject a fake that
  returns canned chunk rows or an empty result (to exercise the fallback
  branch). This new seam is the one piece of *interface* surface this design
  introduces — no DDL, no Beam wiring changes beyond the new optional stage
  in §3.

## 5. Chunking & retrieval standards

- **Row-as-document serialization** (`chunk_kind='row_doc'`): identical to
  today's GReaT-style sentence — `"col is value, col is value, ..."` in
  declared schema column order, nulls rendered as `"is null"`
  (`serialize_row()`, `serialize.py:20-31`). One `row_doc` chunk per reference
  row (`chunk_index=0`).
- **Per-free-text-column chunks** (`chunk_kind='free_text_col'`): one chunk
  per non-null value of each column the DDL-derived profiler classifies as
  `ColumnKind.FREE_TEXT` (`profile.py`). `chunk_index` increments per row
  starting at 1 (0 is reserved for `row_doc`) in declared column order when a
  row has multiple free-text columns. `metadata` carries `{"column":
  "<name>"}` so a downstream consumer can filter to one column's chunks
  without a text parse. Rationale: free-text columns (product descriptions,
  notes, comments) carry retrieval-worthy semantic signal at the *column*
  granularity that the whole-row sentence dilutes — a query for "customer
  complaints about shipping" should match on the `notes` column's embedding,
  not compete against every numeric/categorical column also serialized into
  the row sentence.
- **Normalization**: all embeddings are L2-normalized before write (mirrors
  `HashingEmbedder`/`BgeEmbedder`'s existing normalize-on-embed behavior —
  `BgeEmbedder.embed()` calls `torch.nn.functional.normalize(..., p=2, dim=1)`
  at `embedder.py:173`), so `COSINE` distance in `VECTOR_SEARCH` and inner
  product both agree — consistent with `build_index()`'s
  `IndexFlatIP`-over-normalized-vectors convention
  (`index.py:1-17,96-100`).
- **Retrieval granularity, generation-time vs. downstream**: B.1's internal
  retrieval (exemplar lookup for free-text pool inference,
  `_retrieve_exemplars()` in `engine.py:244-265`) stays **exact** local FAISS
  `IndexFlatIP` search regardless of whether vectors were read from BQ or
  freshly embedded — never BigQuery `VECTOR_SEARCH` in the hot generation
  path (that would add network RPCs per worker `setup()` call and reintroduce
  the nondeterminism `IndexFlatIP` was chosen to avoid, per `index.py`'s
  "deterministic top-k" comment). BigQuery `VECTOR_SEARCH` (approximate, IVF)
  is exclusively the **downstream/external** query surface described in §2 —
  the two retrieval paths are deliberately different (exact-local for the
  pipeline's own generation, approximate-BQ for everyone else) because they
  have different latency/determinism requirements.
- **Top-k convention**: unchanged from today — `_DEFAULT_TOP_K = 8` exemplars
  retrieved to condition free-text pool inference
  (`engine.py:67`). This design does not introduce a new top-k parameter for
  the pipeline's own use; §2's example `VECTOR_SEARCH` query's `top_k => 8` is
  a suggested downstream default, not a contract.

## 6. Constraints & out of scope

Reaffirming CLAUDE.md's hard constraints as they apply here:

- **No Vertex AI.** Embeddings are computed exclusively by the existing
  self-hosted `BgeEmbedder` inside a Beam `RunInference` stage — this design
  adds no new inference backend, no Vertex Embeddings API, no Vertex Vector
  Search / Matching Engine. BigQuery's native `VECTOR_SEARCH` /
  `CREATE VECTOR INDEX` is a BigQuery storage/query feature, not a managed AI
  service call — it operates over vectors this pipeline already computed and
  wrote, no different in kind from any other `SELECT` against a BQ table.
- **No BigQuery remote models / `ML.GENERATE_EMBEDDING`.** BigQuery also
  offers `ML.GENERATE_EMBEDDING` via a remote model connection to Vertex —
  explicitly not used here; embeddings only ever come from the in-Beam
  `BgeEmbedder` DoFn, written by the pipeline, never computed by a BQ-side
  remote function.
- **No external vector databases** (Pinecone, Weaviate, pgvector, etc.) — BQ
  itself is the vector store, per the user's locked decision.
- **No HuggingFace Hub at runtime** — the population path's embedder DoFn
  reuses the exact same GCS-warm-pull + local-directory-only loading already
  in place (`generate.py:66-91`, `BgeEmbedder.__init__` with
  `local_files_only=True`, `embedder.py:138-143`). Nothing in this design
  calls `from_pretrained("org/repo")`.
- **Single-table M1 semantics preserved.** `rag_chunks` is schematically
  table-agnostic (any `source_fqn` can write into it) but this design makes
  **no** cross-table join, no multi-table graph, no entity-resolution logic —
  `source_fqn` is stored purely as a filter/partition dimension, not a
  relationship. Building a knowledge graph or cross-table entity linkage on
  top of `rag_chunks` is exactly the kind of downstream GenAI/KG use case
  this table is designed to *enable*, but doing so is explicitly **out of
  scope** for this pipeline — it would be a separate, external consumer
  reading `rag_chunks` via `VECTOR_SEARCH`, not new code in this repo.
- **No Dataplex, no Looker, no DQ dashboards** — this design adds a table and
  a query pattern, not an observability surface. If `rag_chunks` needs
  monitoring (row counts per digest, staleness), that is a
  `validation_runs`-style BigQuery row, not a dashboard, consistent with
  CLAUDE.md's existing prohibition.
- **Out of scope, deferred**:
  - Row-level (sub-`reference_digest`) dedup of unchanged rows across runs
    (§3 rationale).
  - A `--rebuild_rag_layer` / TTL-based refresh policy for stale chunks (this
    design is additive-only: old `reference_digest` rows are never deleted or
    updated by the pipeline).
  - Embedder version migration tooling (re-embedding all historical rows
    under a new `embedder_version` in bulk) — the read path in §4 handles a
    version bump gracefully (falls back to re-embed for that run) but nothing
    here re-backfills the table under the new version automatically.
  - Any change to B.1's retrieval *quality* (chunk granularity beyond
    row/free-text-column, hybrid keyword+vector search, reranking) — this is
    a storage/reuse design, not a retrieval-quality design.

## 7. Migration / rollout

Landing this incrementally, with **B.1's default behavior completely
unchanged** until an operator opts in:

1. **Land the schema, unused.** Add `config/bq_schema/synthetic_rag/rag_chunks.schema.json`
   (JSON array, same convention as `config/bq_schema/synthetic_data_quality/*.schema.json`)
   and document `synthetic_rag` dataset provisioning in
   `docs/DEPLOYMENT_PREREQUISITES.md` alongside `synthetic_data_quality`. No
   pipeline code changes yet. Nothing reads or writes the table.
2. **Land the population stage behind `--build_rag_layer` (default `false`).**
   When the flag is absent/false, `build_pipeline()` behaves exactly as it
   does today — the new branch in §3 is simply not added to the DAG. This is
   the same "opt-in, additive DAG branch" shape already used for
   `validation_runs_sink` (`if args.validation_runs_table: ...` in
   `run_pipeline.py:265-271`) — a precedent for gating an optional BQ sink
   behind an empty/false CLI default.
3. **Land the generation-time read behind the same principle, but
   self-gating on data, not a flag.** `B1RagEngine.setup()`'s new "try BQ
   read first" branch (§4) requires **no separate CLI flag** — it always
   tries the read when `ctx.reference_digest` is non-empty and a chunk-reader
   seam is wired, and falls back to today's embed path when the table has no
   matching rows. This means: until an operator has actually run a job with
   `--build_rag_layer=true` for a given `reference_digest`, every B.1 run
   behaves byte-for-byte as it does today (empty table → immediate fallback,
   same code path, same output). The very first run under a new digest is
   always a fallback-embed run; only the *second* run against the same
   digest (or any run after a `--build_rag_layer` population job) benefits.
   This ordering — read path lands before or alongside the write path, safe
   by construction because of the fallback — means the two can ship as one
   PR or two; there is no unsafe partial-rollout state.
4. **Operational rollout**: run one job with `--build_rag_layer=true` against
   a reference table's current `reference_digest` (a cheap, isolated
   "backfill" run — it can even skip the synthetic-generation branches
   entirely if desired, though this design does not require adding a
   generation-skip flag; the chunking/embedding stage is independent of
   whether the run also does generation), then subsequent generation runs
   against the same digest read instead of re-embed. Bumping the embedder
   version (`v1` → `v2`) is a no-code-change operational event: run
   `--build_rag_layer=true` again under the new version; old-version rows
   remain in the table (additive-only, per §6) until an explicit cleanup
   decision is made (not part of this design).
5. **Rollback**: setting `--build_rag_layer=false` again, or simply never
   running it, leaves the system in today's state — the read branch's
   fallback makes `rag_chunks` purely additive infrastructure that can be
   ignored, emptied, or dropped without touching engine code.
