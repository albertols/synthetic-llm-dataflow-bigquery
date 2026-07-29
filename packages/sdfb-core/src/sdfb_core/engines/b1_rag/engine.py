"""`B1RagEngine` — the B.1 retrieval-augmented synthesis engine.

Implements the LLM-as-distribution-estimator spine (ADR 0013) with a
retrieval-conditioned twist:

  setup(model_client, ctx):
    1. serialize each reference row (GReaT-style) and embed it (Embedder seam)
    2. build a FAISS IndexFlatIP (exact, normalized, seeded/single-threaded)
    3. profile columns → constant | numeric | categorical | free_text
    4. for free-text columns, retrieve top-k exemplars and ask the LLM ONCE
       (guided JSON) for a bounded unique value pool

  generate_batch(n, cfg):
    - vectorized-sample the bulk columns from the profiled distributions
      (NumPy backend when available, else seeded pure-Python); constants
      copied, numerics clipped to observed range, categoricals at empirical
      frequency
    - patch free-text columns from the bounded LLM pool (sampled w/ replacement)
    - validate each candidate through the derived Pydantic record model;
      drop on failure (DLQ routing happens downstream in the DoFn)

  teardown(): release the index + drop fitted state

`similarity` (GenerationConfig) = retrieval-neighborhood tightness +
sampling variance: →1 mimics nearest exemplars with tight draws; →0 widens
the neighborhood and the sampling spread (always within observed support).

M1 samples each column from its own marginal (constants / numeric range /
empirical categorical), with free-text retrieval-conditioned via the LLM
pool. Joint/conditional sampling over correlated column groups is the next
fidelity primitive (spec §2, NeMo dependency-aware ordering) — deferred.

Pure-Python module: NO `apache_beam` / `torch` / `vllm` / `faiss` / `numpy`
imports at module scope. Heavy deps are deferred into the seams.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import TYPE_CHECKING, Any, NamedTuple

from sdfb_core.codegen import derive_record_model
from sdfb_core.engines.b1_rag._fidelity import ColumnSampler, numpy_available
from sdfb_core.engines.b1_rag.profile import (
    ColumnKind,
    ColumnProfile,
    profile_columns,
)
from sdfb_core.engines.base import (
    FreeTextEmptyYieldError,
    GenerationEngine,
    escalating_sampling,
)
from sdfb_core.engines.text_shapes import (
    build_relaxed_shapes,
    relaxed_shape_charset,
    relaxed_shape_lengths,
    relaxed_shapes_pattern,
    sample_identifier,
    sample_relaxed_identifier,
)
from sdfb_core.observability import log_milestone
from sdfb_core.rag.chunking import (
    CHUNK_KIND_FREE_TEXT_COL,
    CHUNK_KIND_ROW_DOC,
    MAX_ROW_DOC_ROWS,
    compute_row_digest,
)
from sdfb_core.rag.embedding import BgeEmbedder, Embedder, HashingEmbedder
from sdfb_core.rag.index import build_index
from sdfb_core.rag.retrieval import retrieve_centroid_top_k, select_seed_examples
from sdfb_core.rag.serialize import serialize_rows

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator

    from sdfb_core.contracts import GeneratedRecord
    from sdfb_core.engines.base import (
        GenerationConfig,
        GenerationContext,
        ModelClient,
    )

# Top-k exemplars retrieved to condition the LLM's free-text inference.
_DEFAULT_TOP_K = 8
# Free-text pool scaling (WS2 §4b.2). Per-column target =
# min(num_rows, column_distinct, _FREE_TEXT_POOL_MAX); the 32-value pool
# of the 2026-07-19 run oversampled 3 columns 28-619x. Each LLM call stays
# bounded at _POOL_VALUES_PER_CALL values — multiple bounded calls, never
# per-row work (ADR 0013's FASTGEN spine).
_FREE_TEXT_POOL_MAX = 512
_POOL_VALUES_PER_CALL = 32
# Server-side parallel sampling (2026-07-25 perf fix): each pool HTTP round
# trip requests this many INDEPENDENT array-completions (`n=`) and de-dupes
# across them, multiplying per-call novel yield ~4x. Safe because pool
# requests are UNSEEDED — the 2026-07-16 collapse was n=32 single-value
# choices under a pinned seed, a different shape entirely. The prompt stays
# byte-identical across attempts (stable prefix ⇒ vLLM APC / LMCache-ready).
_POOL_PARALLEL_CHOICES = 4
# Stagnation exit (2026-07-23 E2E): 2 of 3 pool columns rode the full
# 2*ceil(target/32)=32-call budget (~30 min of T4 setup) while marginal
# novel yield had collapsed to cross-call duplicates and prompt echoes.
# Once every escalation level has run, _POOL_STAGNATION_WINDOW consecutive
# attempts each adding fewer than _POOL_STAGNATION_MIN_NOVEL novel values
# end the ladder — more retries at the ceiling level cannot outrun the
# yield decay.
_POOL_STAGNATION_WINDOW = 3
_POOL_STAGNATION_MIN_NOVEL = max(1, _POOL_VALUES_PER_CALL // 8)
# Ladders for different columns are independent — run them on a bounded
# thread pool. vLLM continuous-batches concurrent requests on the server
# side; the client (openai/httpx) is thread-safe; embedder work is NOT
# (HF "Already borrowed") and therefore finishes before any thread spawns.
_POOL_BUILD_MAX_WORKERS = 4
# Back-compat alias: the historical single-call pool size == one call's batch.
_DEFAULT_FREE_TEXT_POOL = _POOL_VALUES_PER_CALL
# Setup embeds at most this many reference rows. The index those vectors
# feed serves ONLY centroid top-k exemplar retrieval in M1 (generation
# samples marginals — no per-batch retrieval), so embedding the full 10k
# reference sample bought nothing but wall-clock: the 2026-07-16 corp run
# spent 26-92 min PER Dataflow bundle attempt in `embedder.embed`. The
# reference SELECT is fingerprint-ordered (deterministic spread), so a
# prefix is a representative sample. Aliases the population write contract
# (chunking.MAX_ROW_DOC_ROWS) — the persisted row_doc chunk set and this
# read prefix must stay the same set of rows.
_MAX_EMBED_ROWS = MAX_ROW_DOC_ROWS

# Process-level pool cache (2026-07-24 16:35 E2E): a strict failure in ONE
# column's ladder crashes DoFn.setup() and Dataflow retries the bundle with
# a FRESH engine in the SAME worker process — without this cache the retry
# rebuilt every sibling column's pool from scratch (~15-19 min/attempt).
# Keyed on (reference_digest, model_uri, column, target); disabled when the
# digest is empty. Deliberately process-lived (teardown() must not clear
# it) — the module-level vLLM server reuse (_SERVER_REFS) is the precedent.
_POOL_CACHE: dict[tuple[str, str, str, int], tuple[str, ...]] = {}
_POOL_CACHE_LOCK = threading.Lock()


def clear_free_text_pool_cache() -> None:
    """Drop all cached pools (tests / maintenance only)."""
    with _POOL_CACHE_LOCK:
        _POOL_CACHE.clear()


class B1RagEngine(GenerationEngine):
    """Retrieval-augmented, distribution-estimator synthesis engine (B.1)."""

    name = "b1_rag"

    def __init__(self, *, embedder: Embedder | None = None) -> None:
        # `embedder` lets tests inject a deterministic fake. Production wires
        # a `BgeEmbedder` (local weights). When None, we default to a
        # dependency-free `HashingEmbedder` so the engine is constructible and
        # the contract tests run on a bare laptop with HF_HUB_OFFLINE=1.
        self._injected_embedder = embedder

        self._client: ModelClient | None = None
        self._ctx: GenerationContext | None = None
        self._record_model: type[GeneratedRecord] | None = None
        self._profiles: dict[str, ColumnProfile] | None = None
        self._samplers: dict[str, ColumnSampler] | None = None
        # column -> (vectors, texts) captured during the SEQUENTIAL seed
        # phase, so the kcenter_rotate arm can re-seed per ladder attempt
        # without touching the (thread-unsafe) embedder from a worker
        # thread. Written before any ladder thread spawns; read-only after.
        self._seed_space: dict[str, tuple[list, list]] = {}
        self._index = None
        self._embedder: Embedder | None = None
        self._ref_vectors: list[list[float]] = []
        self._free_text_pools: dict[str, list[str]] = {}
        # column -> {"target", "attempts", "stagnated"} recorded by
        # `_infer_free_text_pool` so the pool-branch rows persist the REAL
        # build outcome (2026-07-29 postmortem: stored rows carried
        # attempts=0 / stagnated=false / target=achieved-size because these
        # were never recorded). Written from ladder threads — per-key dict
        # assignment, atomic under the GIL.
        self._pool_build_info: dict[str, dict[str, Any]] = {}
        self._column_order: list[str] = []
        self._ready: bool = False

    # -- lifecycle ----------------------------------------------------------

    def setup(self, model_client: ModelClient, ctx: GenerationContext) -> None:
        if self._ready:
            return  # idempotent — do not re-embed / re-fit / re-call the LLM.

        self._client = model_client
        self._ctx = ctx
        self._column_order = [c.name for c in ctx.table_schema.columns]
        self._record_model = derive_record_model(ctx.table_schema)

        # 3. profile columns (cheap O(N_ref) pass).
        self._profiles = profile_columns(ctx.table_schema, ctx.reference_rows)
        self._samplers = {
            name: ColumnSampler(prof) for name, prof in self._profiles.items()
        }

        # 1+2. embed + index (only meaningful with reference rows present).
        # Embedder precedence: injected (tests) > real BgeEmbedder if the
        # context carries a (worker-local) embedder path > dependency-free
        # HashingEmbedder default (bare laptop / contract tests).
        if self._injected_embedder is not None:
            self._embedder = self._injected_embedder
        elif ctx.embedder_uri:
            # "auto" = the worker's GPU when present — the bulk 1024-row
            # embed runs before vLLM ignition and demotes right after
            # (ADR 0019), so the two never contend for VRAM.
            self._embedder = BgeEmbedder(ctx.embedder_uri, device="auto")
        else:
            self._embedder = HashingEmbedder(dim=384)
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
                    device=getattr(self._embedder, "device", "cpu"),
                )
            t_index = time.monotonic()
            self._index = build_index(self._ref_vectors, self._embedder.dim)
            log_milestone(
                "b1_index_built",
                seconds=round(time.monotonic() - t_index, 1),
            )
        else:
            self._ref_vectors = []
            self._index = None
        # Bulk embedding is done — release VRAM before vLLM ignition sizes
        # its KV-cache budget (ADR 0019). Later seed-example embeds are
        # tiny and run fine on CPU.
        demote = getattr(self._embedder, "demote_to_cpu", None)
        if callable(demote):
            demote()

        # 4. infer free-text pools ONCE (the only O(1) LLM use in setup).
        t_pools = time.monotonic()
        self._free_text_pools = self._build_free_text_pools(ctx)
        log_milestone(
            "b1_pools_built",
            seconds=round(time.monotonic() - t_pools, 1),
            freetext_cols=len(self._free_text_pools),
        )

        self._ready = True

    def teardown(self) -> None:
        if self._index is not None:
            self._index.release()
        self._index = None
        self._client = None
        self._ctx = None
        self._record_model = None
        self._profiles = None
        self._samplers = None
        self._embedder = None
        self._ref_vectors = []
        self._free_text_pools = {}
        self._column_order = []
        self._ready = False

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

    # -- generation ---------------------------------------------------------

    def generate_batch(
        self, n: int, cfg: GenerationConfig
    ) -> Iterator[GeneratedRecord]:
        if not self._ready or self._record_model is None or self._samplers is None:
            raise RuntimeError(
                "B1RagEngine.generate_batch called before setup() "
                "(or after teardown())."
            )
        if n <= 0 or not self._ctx or not self._ctx.reference_rows:
            return

        similarity = float(cfg.similarity)
        columns = self._sample_columns(n, cfg, similarity)
        free_text = self._sample_free_text(n, cfg, similarity)
        columns.update(free_text)

        for i in range(n):
            raw = {name: columns[name][i] for name in self._column_order}
            try:
                yield self._record_model.model_validate(raw)
            except Exception:
                # Repair-loop budget / DLQ routing belong downstream in the
                # DoFn; the engine silently drops un-coercible candidates.
                continue

    # -- internals ----------------------------------------------------------

    def _sample_columns(
        self, n: int, cfg: GenerationConfig, similarity: float
    ) -> dict[str, list]:
        """Vectorized bulk sampling of all non-free-text columns."""
        assert self._samplers is not None
        use_numpy = numpy_available()
        rng = self._make_rng(cfg.seed, use_numpy)
        out: dict[str, list] = {}
        for name in self._column_order:
            sampler = self._samplers[name]
            if sampler.profile.kind is ColumnKind.FREE_TEXT:
                continue  # patched separately from the LLM pool
            if use_numpy:
                out[name] = sampler.sample_numpy(rng, n, similarity)
            else:
                out[name] = sampler.sample_python(rng, n, similarity)
        return out

    def _sample_free_text(
        self, n: int, cfg: GenerationConfig, similarity: float
    ) -> dict[str, list]:
        """Sample free-text columns from their bounded LLM pools (w/ repl)."""
        assert self._samplers is not None
        out: dict[str, list] = {}
        # A dedicated seeded RNG so free-text draws don't perturb the bulk
        # column RNG stream (keeps both reproducible & independent).
        rng = random.Random(_mix_seed(cfg.seed, "freetext"))
        for name in self._column_order:
            sampler = self._samplers[name]
            prof = sampler.profile
            if prof.kind is not ColumnKind.FREE_TEXT:
                continue
            null_frac = prof.null_fraction if prof.nullable else 0.0
            if prof.identifier_shape is not None:
                # Format-preserving per-row generation — a bounded pool
                # sampled with replacement collapses an identifier column's
                # distinctness (2026-07-17 E2E: ID_COL 30 distinct / 1000).
                out[name] = [
                    None
                    if null_frac > 0.0 and rng.random() < null_frac
                    else sample_identifier(prof.identifier_shape, rng.randrange)
                    for _ in range(n)
                ]
                continue
            pool = self._free_text_pools.get(name) or list(prof.text_examples)
            if not pool:
                out[name] = [None] * n
                continue
            drawn: list = []
            for _ in range(n):
                if null_frac > 0.0 and rng.random() < null_frac:
                    drawn.append(None)
                else:
                    drawn.append(pool[rng.randrange(len(pool))])
            out[name] = drawn
        return out

    def _stored_pools(self, ctx: GenerationContext) -> dict[str, list[str]]:
        """Pools already persisted for this (reference_digest, model_uri).

        Returns column → values. Empty on any of: no store attached, no
        digest to key on, or a store that raised — in every case the ladder
        runs exactly as it did before WS5.
        """
        store = getattr(ctx, "pool_store", None)
        if store is None or not ctx.reference_digest:
            return {}
        try:
            fetched = store.fetch(ctx.reference_digest, ctx.model_uri)
        except Exception as exc:
            log_milestone("freetext_pool_store_error", error=type(exc).__name__)
            return {}
        # An empty values array is not a usable pool — build instead of
        # silently generating from nothing.
        return {p.column: list(p.values) for p in fetched if p.values}

    def _resolve_stored_pools(
        self, ctx: GenerationContext, free_text_columns: int
    ) -> dict[str, list[str]]:
        """Persisted pools for this run, announcing the store's absence.

        The 2026-07-26 1M run spent 26 of its 53 minutes rebuilding pools
        per worker PROCESS purely because nobody passed
        ``--freetext_pools_table`` — and nothing in the logs said so. The
        absence was only discoverable by noticing that
        ``freetext_pool_store_*`` milestones never appeared, which is
        exactly the silence a milestone exists to break.
        """
        if getattr(ctx, "pool_store", None) is None:
            log_milestone(
                "freetext_pool_store_absent",
                level=logging.WARNING,
                free_text_columns=free_text_columns,
                num_rows=ctx.num_rows,
            )
            return {}
        return self._stored_pools(ctx)

    def _take_stored_pool(
        self,
        column: str,
        ctx: GenerationContext,
        stored: dict[str, list[str]],
        pools: dict[str, list[str]],
    ) -> bool:
        """Serve `column` from the persisted store; True when it was served."""
        hit = stored.get(column)
        if hit:
            pools[column] = list(hit)
            log_milestone(
                "freetext_pool_store_hit", column=column, pool_size=len(hit)
            )
            return True
        if getattr(ctx, "pool_store", None) is not None:
            log_milestone("freetext_pool_store_miss", column=column)
        return False

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

        # Tier 0 — the persisted store (WS5). Cross-PROCESS and
        # authoritative; `_POOL_CACHE` below stays as the intra-process tier
        # that survives setup() retries inside one worker. A store outage is
        # never fatal: pools are an optimisation, not a dependency.
        stored = self._resolve_stored_pools(ctx, len(free_text_cols))

        exemplars = self._retrieve_exemplars(ctx, _DEFAULT_TOP_K)
        chunks_by_column = self._fetch_free_text_chunks(ctx)
        # Phase 1 — sequential: seed-example retrieval touches the embedder
        # (HF fast tokenizers are not thread-safe), so it fully completes
        # before any ladder thread spawns.
        jobs: list[tuple[ColumnProfile, list[str], int]] = []
        for prof in free_text_cols:
            if self._take_stored_pool(prof.name, ctx, stored, pools):
                continue
            seed_examples = self._column_seed_examples(
                prof, ctx, _DEFAULT_TOP_K, chunks_by_column.get(prof.name)
            )
            if not seed_examples:
                seed_examples = [
                    e[prof.name]
                    for e in exemplars
                    if e.get(prof.name) not in (None, "")
                ][:_DEFAULT_TOP_K] or list(prof.text_examples[:_DEFAULT_TOP_K])
            pool_target = self._pool_target(prof, ctx)
            key = self._pool_cache_key(ctx, prof.name, pool_target)
            if key is not None:
                with _POOL_CACHE_LOCK:
                    cached = _POOL_CACHE.get(key)
                if cached is not None:
                    pools[prof.name] = list(cached)
                    log_milestone(
                        "freetext_pool_cache_hit",
                        column=prof.name,
                        pool_size=len(cached),
                    )
                    continue
            jobs.append((prof, seed_examples, pool_target))

        # Phase 2 — parallel: one bounded ladder per column. Collect EVERY
        # result before re-raising the first failure, so sibling columns'
        # completed builds are never discarded by one column's strict raise
        # (the 2026-07-24 16:35 E2E rebuilt all pools 3x for one column).
        if not jobs:
            return pools
        if len(jobs) == 1:
            prof, seed_examples, pool_target = jobs[0]
            pools[prof.name] = self._infer_free_text_pool(
                prof, seed_examples, pool_target
            )
            return pools
        from concurrent.futures import ThreadPoolExecutor

        first_error: Exception | None = None
        with ThreadPoolExecutor(
            max_workers=min(_POOL_BUILD_MAX_WORKERS, len(jobs)),
            thread_name_prefix="sdfb-pool",
        ) as executor:
            futures = [
                (
                    prof,
                    executor.submit(
                        self._infer_free_text_pool, prof, seed_examples, tgt
                    ),
                )
                for prof, seed_examples, tgt in jobs
            ]
            for prof, future in futures:
                try:
                    pools[prof.name] = future.result()
                except Exception as e:  # re-raised below, after all columns land
                    if first_error is None:
                        first_error = e
        if first_error is not None:
            raise first_error
        return pools

    def _fetch_free_text_chunks(self, ctx: GenerationContext) -> dict[str, list]:
        """Fetch persisted `free_text_col` chunks ONCE for all columns and
        group by `metadata["column"]` (WS2 review: `_column_seed_examples`
        used to issue one identical store query per free-text column — N
        redundant BQ reads per worker setup). Empty dict when there is no
        store or no reference digest to key the fetch."""
        store = ctx.chunk_store
        if store is None or not ctx.reference_digest:
            return {}
        chunks = store.fetch(
            ctx.reference_digest,
            CHUNK_KIND_FREE_TEXT_COL,
            ctx.embedder_id,
            ctx.embedder_version,
        )
        by_column: dict[str, list] = {}
        for c in chunks:
            column = c.metadata.get("column")
            if column is None or not c.embedding:
                continue
            by_column.setdefault(column, []).append(c)
        return by_column

    def _column_seed_examples(
        self,
        prof: ColumnProfile,
        ctx: GenerationContext,
        k: int,
        column_chunks: list | None,
    ) -> list[str]:
        """Column-relevant seed exemplars for one free-text column
        (WS2 §4b.3): persisted `free_text_col` chunks — pre-fetched ONCE for
        all columns by `_fetch_free_text_chunks` and passed in as
        ``column_chunks`` — when present, else the column's own values
        embedded locally. Empty list ⇒ caller falls back to row-doc
        exemplars."""
        strategy = getattr(ctx, "pool_seed_strategy", "centroid")
        if column_chunks:
            vectors = [list(c.embedding) for c in column_chunks]
            texts = [c.chunk_text for c in column_chunks]
            self._seed_space[prof.name] = (vectors, texts)
            return select_seed_examples(vectors, texts, k, strategy=strategy)
        if self._embedder is not None:
            values: list[str] = []
            seen: set[str] = set()
            for row in ctx.reference_rows[:_MAX_EMBED_ROWS]:
                v = row.get(prof.name)
                if v not in (None, "") and v not in seen:
                    seen.add(v)
                    values.append(str(v))
            if values:
                if len(values) <= k:
                    return values
                vectors = self._embedder.embed(values)
                self._seed_space[prof.name] = (vectors, values)
                return select_seed_examples(vectors, values, k, strategy=strategy)
        return []

    def _pool_cache_key(
        self, ctx: GenerationContext, column: str, target: int
    ) -> tuple[str, str, str, int] | None:
        if not ctx.reference_digest:
            return None
        return (ctx.reference_digest, ctx.model_uri, column, target)

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

    def _rotating_prompt(self, prof: ColumnProfile, per_call: int):
        """A per-attempt prompt builder for the `kcenter_rotate` arm, else
        None.

        `centroid` and `kcenter` keep a byte-identical prompt prefix so vLLM
        prefix caching still applies (ADR 0018); only this arm trades that
        away, which is cheap now a pool is built once per digest (WS5 §2).
        """
        if getattr(self._ctx, "pool_seed_strategy", "centroid") != "kcenter_rotate":
            return None
        space = self._seed_space.get(prof.name)
        if space is None:
            return None
        vectors, texts = space

        def _builder(attempt: int) -> tuple[str, list[str]]:
            rotated = select_seed_examples(
                vectors, texts, _DEFAULT_TOP_K,
                strategy="kcenter_rotate", attempt=attempt,
            )
            return _build_pool_prompt(prof.name, per_call, rotated), rotated

        return _builder

    def _infer_free_text_pool(
        self, prof: ColumnProfile, seed_examples: list[str], target: int
    ) -> list[str]:
        """Fill a bounded unique pool for one free-text column from batched
        LLM calls conditioned on retrieved exemplars."""
        assert self._client is not None
        t_column = time.monotonic()
        per_call = min(target, _POOL_VALUES_PER_CALL)
        prompt = _build_pool_prompt(prof.name, per_call, seed_examples)
        # ARRAY completions only — never n single-value choices. A choice is
        # blind to its siblings, so "distinct" is unsatisfiable per
        # single-value completion and vLLM collapsed all 32 into the
        # identical modal exemplar echo (2026-07-16 runs: distinct=1,
        # prompt_echoes=96). Inside one array completion the model sees what
        # it already wrote; _pool_llm_yield rides n such arrays per round
        # trip and de-dupes across them.
        items_schema: dict = {"type": "string"}
        pattern_guided = False
        if self._ctx is not None and self._ctx.pool_pattern_guidance:
            # Layer-2 hallucination fix (opt-in): constrain decoding itself
            # with a charset/length regex derived from the observed values,
            # so out-of-format junk is unrepresentable. Deliberately looser
            # than the per-position template (see relaxed_shapes_pattern) —
            # novelty pressure stays with the sampler, not the grammar.
            pattern_shapes = build_relaxed_shapes(
                [str(v) for v in prof.observed_values]
            )
            if pattern_shapes is not None:
                items_schema["pattern"] = relaxed_shapes_pattern(pattern_shapes)
                pattern_guided = True
        json_schema = {
            "type": "object",
            "properties": {
                "values": {"type": "array", "items": items_schema}
            },
            "required": ["values"],
        }
        # kcenter_rotate is the only arm that varies the prompt across
        # attempts; centroid/kcenter keep a byte-identical prefix so vLLM
        # prefix caching still applies (ADR 0018).
        prompt_for_attempt = self._rotating_prompt(prof, per_call)

        try:
            y = _pool_llm_yield(
                self._client, prompt, json_schema, prof, seed_examples,
                target=target, prompt_for_attempt=prompt_for_attempt,
            )
        except Exception as e:
            if self._ctx is not None and self._ctx.strict_freetext:
                raise
            # Per-call generation failure: exemplar fallback is allowed, but
            # NEVER silently — a run where the LLM contributed nothing must be
            # visible in worker logs (E2E report §4.2: 100 % memorization).
            log_milestone(
                "freetext_llm_fallback",
                level=logging.WARNING,
                column=prof.name,
                error=type(e).__name__,
            )
            pool = []
            format_rejected = 0
            self._pool_build_info[prof.name] = {
                "target": target, "attempts": 0, "stagnated": False,
            }
        else:
            pool = self._resolve_pool_yield(prof, y, per_call, target)
            format_rejected = y.format_rejected
            self._pool_build_info[prof.name] = {
                "target": target,
                "attempts": y.attempts,
                "stagnated": y.stagnated,
            }

        # Fold observed exemplars ONLY when the LLM delivered nothing (lax
        # mode) — loudly, via the fallback milestone emitted above. Every
        # FREE_TEXT column is high-cardinality by classification, so folding
        # real values on top of a delivered pool is memorization, not
        # fidelity: the earlier is_unique_valued-only guard left shared-key
        # columns folding 64 real exemplars each (2026-07-16 E2E: 9 columns
        # at copy_ratio 0.475-0.939; 2026-07-15: copy_ratio=1.0 on all 8
        # unique-valued ones).
        if not pool:
            for ex in prof.text_examples:
                if ex not in pool:
                    pool.append(ex)
        # De-dup, preserve order, bound the pool size.
        seen: dict[str, None] = {}
        for v in pool:
            if v not in seen:
                seen[v] = None
        final = list(seen.keys())[: max(target, len(prof.text_examples))]
        if final and self._ctx is not None:
            key = self._pool_cache_key(self._ctx, prof.name, target)
            if key is not None:
                with _POOL_CACHE_LOCK:
                    _POOL_CACHE[key] = tuple(final)
        # Per-column build summary — the only milestone that reports
        # format_rejected on a CLEAN build (stagnated/undersized/fallback
        # cover the unhealthy paths), and per-ladder seconds that
        # disaggregate b1_pools_built (whose total absorbs the lazy vLLM
        # ignition inside the first column's first call).
        log_milestone(
            "freetext_pool_built",
            column=prof.name,
            pool_size=len(final),
            target=target,
            format_rejected=format_rejected,
            pattern_guided=pattern_guided,
            seconds=round(time.monotonic() - t_column, 1),
        )
        return final

    def _resolve_pool_yield(
        self, prof: ColumnProfile, y: _PoolYield, per_call: int, target: int
    ) -> list[str]:
        """Turn one column's ladder outcome into its final pool: shape
        fallback for copy-saturated builds, shape top-up for undersized
        ones, a strict raise (or loud lax milestone) when nothing usable
        exists."""
        pool = y.pool
        if not pool and y.parsed > 0:
            # Copy-saturated: the model parsed values but every one was
            # an observed copy — deterministic on rebuild, so retrying or
            # failing the bundle buys nothing (the 2026-07-24 16:35 E2E
            # burned 2 full setup() retries exactly here). A relaxed
            # template can still generate verified-novel in-format values.
            shape_pool = self._shape_fallback_pool(prof, target, exclude=set())
            if shape_pool:
                log_milestone(
                    "freetext_pool_shape_fallback",
                    level=logging.WARNING,
                    column=prof.name,
                    pool_size=len(shape_pool),
                    target=target,
                    attempts=y.attempts,
                    parsed=y.parsed,
                    verbatim_copies=y.copies,
                    prompt_echoes=y.prompt_echoes,
                )
                return shape_pool
        if not pool:
            # The calls "succeeded" (no exception) yet yielded nothing
            # usable. Counts (never values — reference data must not
            # leak into logs) say WHY: parsed=0 means every choice was
            # dropped at JSON parse; low distinct with prompt_echoes ==
            # verbatim_copies means the model parroted the few exemplars
            # it was SHOWN (sampling/prompt defect); high distinct with
            # prompt_echoes ~ 0 means in-format generations collided with
            # the FULL reference sample the model never saw — a saturated
            # key space where per-column novelty is unattainable.
            diagnosis = (
                f"attempts={y.attempts}, requested_per_attempt="
                f"{per_call}, parsed={y.parsed}, "
                f"distinct={y.distinct}, verbatim_copies={y.copies}, "
                f"prompt_echoes={y.prompt_echoes}, "
                f"format_rejected={y.format_rejected}, novel=0"
            )
            if self._ctx is not None and self._ctx.strict_freetext:
                raise FreeTextEmptyYieldError(
                    f"LLM calls for free-text column {prof.name!r} "
                    f"yielded no usable values ({diagnosis})."
                )
            log_milestone(
                "freetext_llm_fallback",
                level=logging.WARNING,
                column=prof.name,
                error="EmptyYield",
                attempts=y.attempts,
                parsed=y.parsed,
                distinct=y.distinct,
                verbatim_copies=y.copies,
                prompt_echoes=y.prompt_echoes,
                format_rejected=y.format_rejected,
            )
        elif len(pool) < target:
            # Top up an undersized pool from the template before
            # accepting the shortfall — the 2026-07-24 16:35 run landed
            # CHANGE_USERID with 31 distinct values over 1000 rows
            # (diversity collapse).
            top_up = self._shape_fallback_pool(
                prof, target - len(pool), exclude=set(pool)
            )
            if top_up:
                log_milestone(
                    "freetext_pool_shape_topup",
                    level=logging.WARNING,
                    column=prof.name,
                    added=len(top_up),
                    pool_size=len(pool) + len(top_up),
                    target=target,
                    attempts=y.attempts,
                )
                pool = [*pool, *top_up]
            if len(pool) < target:
                # Every escalation level ran and the pool is still short
                # of target: the column lands with whatever novelty the
                # LLM delivered, but never silently — the 2026-07-17 E2E
                # run accepted a 4-value pool for COL_048 without a trace.
                log_milestone(
                    "freetext_pool_undersized",
                    level=logging.WARNING,
                    column=prof.name,
                    pool_size=len(pool),
                    target=target,
                    attempts=y.attempts,
                    parsed=y.parsed,
                    distinct=y.distinct,
                    verbatim_copies=y.copies,
                    prompt_echoes=y.prompt_echoes,
                    format_rejected=y.format_rejected,
                )
        return pool

    def _shape_fallback_pool(
        self, prof: ColumnProfile, count: int, exclude: set[str]
    ) -> list[str]:
        """Verified-novel values from a relaxed per-position template, or [].

        B.2-parity route (freetext.py:_shape_fallback_pool) for copy-saturated
        or undersized LLM builds: mixed-length identifier-ish columns the
        strict detector rejects still template per length bucket, and every
        emitted value is rejected against the observed reference values (and
        `exclude`, and itself) — nothing here can memorize. Prose columns
        (whitespace) return [] and the caller keeps its existing raise/
        fallback path. Deterministic per (run_id, column).
        """
        shapes = build_relaxed_shapes([str(v) for v in prof.observed_values])
        if shapes is None or count <= 0:
            return []
        run_id = self._ctx.pipeline_run_id if self._ctx is not None else ""
        rng = random.Random(_mix_seed(None, f"{run_id}:shape:{prof.name}"))
        observed = {str(v) for v in prof.observed_values}
        out: list[str] = []
        seen: set[str] = set()
        # Bounded rejection sampling: dense keyspaces stop at the cap
        # instead of spinning (same 40x budget as B.2).
        for _ in range(count * 40):
            v = sample_relaxed_identifier(shapes, rng.randrange)
            if v in observed or v in exclude or v in seen:
                continue
            seen.add(v)
            out.append(v)
            if len(out) >= count:
                break
        return out

    # -- helpers ------------------------------------------------------------

    def _make_rng(self, seed: int | None, use_numpy: bool):
        mixed = _mix_seed(seed, "bulk")
        if use_numpy:
            import numpy as np

            return np.random.default_rng(mixed)
        return random.Random(mixed)


class _PoolYield(NamedTuple):
    """Outcome counts of the escalating-sampling pool calls for one column.

    ``copies`` are values found anywhere in the FULL observed reference
    sample; ``prompt_echoes`` is the subset that was actually SHOWN to the
    model as a seed exemplar. The gap between the two separates parroting
    (sampling/prompt defect) from reference collisions (saturated key space).
    """

    pool: list[str]
    parsed: int
    distinct: int
    copies: int
    prompt_echoes: int
    attempts: int
    format_rejected: int = 0
    # True when the ladder exited on yield-decay (the stagnation break), as
    # opposed to reaching target or exhausting the attempt budget. Persisted
    # per column into `freetext_pools.stagnated` (2026-07-29: rows stored
    # hardcoded false because nothing recorded this).
    stagnated: bool = False


def _build_pool_prompt(column: str, per_call: int, seed_examples: list[str]) -> str:
    """The pool prompt. One definition — the kcenter_rotate arm rebuilds it
    per attempt with a different seed set, and the two must not drift."""
    return (
        f"You generate synthetic tabular data. First identify the exact "
        f"format of these example values for the column '{column}' "
        f"(e.g. UUID, hexadecimal identifier, numeric code, date, "
        f"timestamp, natural-language text), then generate "
        f"{per_call} NEW, distinct, fictitious values in "
        f"exactly that format. Never copy an example verbatim. "
        f'Examples: {seed_examples}. Return JSON {{"values": [...]}}.'
    )


def _pool_llm_yield(
    client: ModelClient,
    prompt: str,
    json_schema: dict,
    prof: ColumnProfile,
    seed_examples: list[str],
    target: int = _DEFAULT_FREE_TEXT_POOL,
    n_choices: int = _POOL_PARALLEL_CHOICES,
    prompt_for_attempt=None,
) -> _PoolYield:
    """Run the pool call at escalating sampling levels, accumulating novel
    values until the pool reaches ``target``. Breaking on the FIRST
    non-empty yield let one conservative completion (still under the served
    model's generation_config truncation pin) define the whole pool — the
    2026-07-17 E2E run landed a 4-value pool over 1000 rows and the
    unclamped retry levels never executed.

    Calls are bounded at `max(len(levels), 2*ceil(target/_POOL_VALUES_PER_CALL))`,
    cycling the escalation ladder (last level repeats). After every level has
    run once, `_POOL_STAGNATION_WINDOW` consecutive low-novelty attempts end
    the loop early (`freetext_pool_stagnated` milestone) — the 2026-07-23 E2E
    run burned 30 min of T4 setup on attempts that only re-emitted duplicates.

    No request seed: a pinned seed with n>1 collapses all n vLLM choices
    into one completion (2026-07-15 run). Unseeded, the n choices sample
    independently, so one round trip carries n distinct 32-value arrays —
    the call budget scales down by the same factor (`per_round`).
    """
    observed = set(prof.observed_values)
    shown = set(seed_examples)
    # Format-plausibility gate (2026-07-25 10:52 E2E): novelty alone let
    # hallucinated meta-tokens into the pool — an echo of the COLUMN NAME
    # from the prompt and an echo of the prompt's own format examples
    # ('UUID-…') are trivially "novel". For identifier-ish columns (a
    # relaxed template exists) a candidate must also match an observed
    # length bucket, stay within the observed charset, and never contain
    # the column name. Prose columns (no template) skip the gate.
    shapes = build_relaxed_shapes([str(v) for v in prof.observed_values])
    gate_lengths = relaxed_shape_lengths(shapes) if shapes else None
    gate_charset = relaxed_shape_charset(shapes) if shapes else None
    name_lower = prof.name.lower()

    def _in_format(v: str) -> bool:
        if gate_lengths is None or gate_charset is None:
            return True
        return (
            len(v) in gate_lengths
            and set(v) <= gate_charset
            and name_lower not in v.lower()
        )

    pool: list[str] = []
    pool_seen: set[str] = set()
    seen: set[str] = set()
    n_parsed = 0
    n_copies = 0
    n_echoes = 0
    n_format_rejected = 0
    attempts = 0
    levels = escalating_sampling()
    per_round = _POOL_VALUES_PER_CALL * max(1, n_choices)
    max_calls = max(len(levels), 2 * -(-target // per_round))
    stagnant = 0
    hit_stagnation = False
    while attempts < max_calls and len(pool) < target:
        level = levels[min(attempts, len(levels) - 1)]
        # kcenter_rotate (WS5 §3): re-seed the prompt each attempt so the
        # model sees a different region of the column's manifold. Seeds come
        # from a vector space captured BEFORE any thread spawned, so this
        # never touches the (thread-unsafe) embedder from here.
        call_prompt = prompt
        if prompt_for_attempt is not None:
            call_prompt, rotated_seeds = prompt_for_attempt(attempts)
            shown.update(rotated_seeds)
        attempts += 1
        results = client.generate_json(
            prompt=call_prompt,
            json_schema=json_schema,
            n=max(1, n_choices),
            max_tokens=2048,
            temperature=level.temperature,
            top_p=level.top_p,
            top_k=level.top_k,
        )
        parsed_values = _string_values(results, prof.name)
        values = [v for v in parsed_values if _in_format(v)]
        n_format_rejected += len(parsed_values) - len(values)
        novel = [v for v in values if v not in observed]
        n_parsed += len(parsed_values)
        n_copies += len(values) - len(novel)
        n_echoes += sum(1 for v in values if v in shown)
        seen.update(values)
        added = 0
        for v in novel:
            if v not in pool_seen:
                pool_seen.add(v)
                pool.append(v)
                added += 1
        stagnant = stagnant + 1 if added < _POOL_STAGNATION_MIN_NOVEL else 0
        if stagnant >= _POOL_STAGNATION_WINDOW and attempts >= len(levels):
            log_milestone(
                "freetext_pool_stagnated",
                level=logging.WARNING,
                column=prof.name,
                attempts=attempts,
                pool_size=len(pool),
                target=target,
                format_rejected=n_format_rejected,
            )
            hit_stagnation = True
            break
    return _PoolYield(
        pool, n_parsed, len(seen), n_copies, n_echoes, attempts,
        n_format_rejected, hit_stagnation,
    )


def _string_values(results: list, name: str) -> list[str]:
    """Extract non-empty string values from LLM pool results.

    Primary shape is the guided ``{"values": [...]}`` array; column-keyed
    dicts (the FakeModelClient's echo mode) are tolerated. Novelty filtering
    (dropping values that equal observed reference values — copies, not
    generations) happens in the caller so parsed-vs-copied counts stay
    visible: an all-copies response must be diagnosable as such, not
    misreported as a parse failure (2026-07-16 corp run)."""
    out: list[str] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        if isinstance(r.get("values"), list):
            out.extend(str(v) for v in r["values"] if v)
            continue
        val = r.get(name)
        if isinstance(val, str) and val:
            out.append(val)
    return out


def _mix_seed(seed: int | None, salt: str) -> int:
    """Derive a stable sub-stream seed from (seed, salt).

    Keeps independent RNG streams (bulk vs free-text) reproducible without
    them sharing state. A None seed maps to a fixed default so output stays
    deterministic across calls — the contract requires same-seed
    reproducibility, and a default makes the no-seed case stable too.
    """
    base = 0 if seed is None else int(seed)
    h = 1469598103934665603  # FNV offset basis (64-bit)
    for ch in f"{base}:{salt}":
        h = (h ^ ord(ch)) * 1099511628211
        h &= 0xFFFFFFFFFFFFFFFF
    return h


__all__ = ["B1RagEngine", "clear_free_text_pool_cache"]
