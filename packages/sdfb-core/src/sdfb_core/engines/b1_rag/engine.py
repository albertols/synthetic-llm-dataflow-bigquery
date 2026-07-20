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
import time
from typing import TYPE_CHECKING, NamedTuple

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
from sdfb_core.engines.text_shapes import sample_identifier
from sdfb_core.observability import log_milestone
from sdfb_core.rag.chunking import (
    CHUNK_KIND_FREE_TEXT_COL,
    CHUNK_KIND_ROW_DOC,
    compute_row_digest,
)
from sdfb_core.rag.embedding import BgeEmbedder, Embedder, HashingEmbedder
from sdfb_core.rag.index import build_index
from sdfb_core.rag.retrieval import retrieve_centroid_top_k, retrieve_column_exemplars
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
# Back-compat alias: the historical single-call pool size == one call's batch.
_DEFAULT_FREE_TEXT_POOL = _POOL_VALUES_PER_CALL
# Setup embeds at most this many reference rows. The index those vectors
# feed serves ONLY centroid top-k exemplar retrieval in M1 (generation
# samples marginals — no per-batch retrieval), so embedding the full 10k
# reference sample bought nothing but wall-clock: the 2026-07-16 corp run
# spent 26-92 min PER Dataflow bundle attempt in `embedder.embed`. The
# reference SELECT is fingerprint-ordered (deterministic spread), so a
# prefix is a representative sample.
_MAX_EMBED_ROWS = 1024


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
        self._index = None
        self._embedder: Embedder | None = None
        self._ref_vectors: list[list[float]] = []
        self._free_text_pools: dict[str, list[str]] = {}
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
            self._embedder = BgeEmbedder(ctx.embedder_uri)
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
        return pools

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
        # ONE completion carrying the whole pool as an array — not n parallel
        # single-value choices. Each of n>1 choices is blind to its siblings,
        # so "distinct" is unsatisfiable per completion and vLLM collapsed
        # all 32 into the identical modal exemplar echo at every sampling
        # level (2026-07-16 runs: distinct=1, prompt_echoes=96). Inside one
        # array completion the model sees what it already wrote.
        json_schema = {
            "type": "object",
            "properties": {
                "values": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["values"],
        }
        try:
            y = _pool_llm_yield(
                self._client, prompt, json_schema, prof, seed_examples, target=target
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
        else:
            pool = y.pool
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
                    f"prompt_echoes={y.prompt_echoes}, novel=0"
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
                )
            elif len(pool) < target:
                # Every escalation level ran and the pool is still short of
                # target: the column lands with whatever novelty the LLM
                # delivered, but never silently — the 2026-07-17 E2E run
                # accepted a 4-value pool for COL_048 without a trace.
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
                )

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
        return list(seen.keys())[: max(target, len(prof.text_examples))]

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


def _pool_llm_yield(
    client: ModelClient,
    prompt: str,
    json_schema: dict,
    prof: ColumnProfile,
    seed_examples: list[str],
    target: int = _DEFAULT_FREE_TEXT_POOL,
) -> _PoolYield:
    """Run the pool call at escalating sampling levels, accumulating novel
    values until the pool reaches ``target``. Breaking on the FIRST
    non-empty yield let one conservative completion (still under the served
    model's generation_config truncation pin) define the whole pool — the
    2026-07-17 E2E run landed a 4-value pool over 1000 rows and the
    unclamped retry levels never executed.

    Calls are bounded at `max(len(levels), 2*ceil(target/_POOL_VALUES_PER_CALL))`,
    cycling the escalation ladder (last level repeats).

    No request seed: a pinned seed with n>1 collapses all n vLLM choices
    into one completion (2026-07-15 run: identical choice lengths per
    request → at most one distinct pool value).
    """
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


__all__ = ["B1RagEngine"]
