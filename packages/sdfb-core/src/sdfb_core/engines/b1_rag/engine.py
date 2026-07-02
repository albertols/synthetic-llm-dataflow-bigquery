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

import random
from typing import TYPE_CHECKING

from sdfb_core.codegen import derive_record_model
from sdfb_core.engines.b1_rag._fidelity import ColumnSampler, numpy_available
from sdfb_core.engines.b1_rag.embedder import BgeEmbedder, Embedder, HashingEmbedder
from sdfb_core.engines.b1_rag.index import build_index
from sdfb_core.engines.b1_rag.profile import (
    ColumnKind,
    ColumnProfile,
    profile_columns,
)
from sdfb_core.engines.b1_rag.serialize import serialize_rows
from sdfb_core.engines.base import GenerationEngine

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
# Bounded unique free-text pool size requested from the LLM (sampled w/ repl).
_DEFAULT_FREE_TEXT_POOL = 32


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
            texts = serialize_rows(ctx.reference_rows, self._column_order)
            self._ref_vectors = self._embedder.embed(texts)
            self._index = build_index(self._ref_vectors, self._embedder.dim)
        else:
            self._ref_vectors = []
            self._index = None

        # 4. infer free-text pools ONCE (the only O(1) LLM use in setup).
        self._free_text_pools = self._build_free_text_pools(ctx)

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
            if sampler.profile.kind is not ColumnKind.FREE_TEXT:
                continue
            pool = self._free_text_pools.get(name) or list(
                sampler.profile.text_examples
            )
            if not pool:
                out[name] = [None] * n
                continue
            null_frac = sampler.profile.null_fraction if sampler.profile.nullable else 0.0
            drawn: list = []
            for _ in range(n):
                if null_frac > 0.0 and rng.random() < null_frac:
                    drawn.append(None)
                else:
                    drawn.append(pool[rng.randrange(len(pool))])
            out[name] = drawn
        return out

    def _build_free_text_pools(self, ctx: GenerationContext) -> dict[str, list[str]]:
        """For each FREE_TEXT column, retrieve top-k exemplars and ask the
        LLM once for a bounded unique pool. Falls back to observed examples
        when the LLM returns nothing usable."""
        assert self._profiles is not None
        pools: dict[str, list[str]] = {}
        free_text_cols = [
            p for p in self._profiles.values() if p.kind is ColumnKind.FREE_TEXT
        ]
        if not free_text_cols:
            return pools

        exemplars = self._retrieve_exemplars(ctx, _DEFAULT_TOP_K)
        for prof in free_text_cols:
            pools[prof.name] = self._infer_free_text_pool(prof, exemplars)
        return pools

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
        dim = self._embedder.dim
        # Centroid query: reuse the row vectors built in setup() (no re-embed),
        # average them in pure Python to avoid a NumPy hard-dep here.
        vectors = self._ref_vectors
        centroid = [0.0] * dim
        for vec in vectors:
            for j in range(dim):
                centroid[j] += vec[j]
        centroid = [c / len(vectors) for c in centroid]
        ids = self._index.search(centroid, k)
        return [ctx.reference_rows[i] for i in ids]

    def _infer_free_text_pool(
        self, prof: ColumnProfile, exemplars: list[dict]
    ) -> list[str]:
        """Ask the LLM (guided JSON) for a bounded unique pool of values for
        one free-text column, conditioned on retrieved exemplars."""
        assert self._client is not None
        seed_examples = [
            e[prof.name]
            for e in exemplars
            if e.get(prof.name) not in (None, "")
        ][:_DEFAULT_TOP_K]
        if not seed_examples:
            seed_examples = list(prof.text_examples[:_DEFAULT_TOP_K])

        prompt = (
            f"Generate {_DEFAULT_FREE_TEXT_POOL} realistic, distinct values for "
            f"the column '{prof.name}'. Match the style and format of these "
            f"examples: {seed_examples}. Return JSON objects each with a "
            f"'{prof.name}' field."
        )
        json_schema = {
            "type": "object",
            "properties": {prof.name: {"type": "string"}},
            "required": [prof.name],
        }
        pool: list[str] = []
        try:
            results = self._client.generate_json(
                prompt=prompt,
                json_schema=json_schema,
                n=_DEFAULT_FREE_TEXT_POOL,
                max_tokens=256,
                seed=0,
            )
            for r in results:
                val = r.get(prof.name) if isinstance(r, dict) else None
                if isinstance(val, str) and val:
                    pool.append(val)
        except Exception:
            pool = []

        # Always fold in observed exemplars so fidelity holds even if the LLM
        # is unavailable / returns junk (the FakeModelClient canned-mode case).
        for ex in prof.text_examples:
            if ex not in pool:
                pool.append(ex)
        # De-dup, preserve order, bound the pool size.
        seen: dict[str, None] = {}
        for v in pool:
            if v not in seen:
                seen[v] = None
        return list(seen.keys())[: max(_DEFAULT_FREE_TEXT_POOL, len(prof.text_examples))]

    # -- helpers ------------------------------------------------------------

    def _make_rng(self, seed: int | None, use_numpy: bool):
        mixed = _mix_seed(seed, "bulk")
        if use_numpy:
            import numpy as np

            return np.random.default_rng(mixed)
        return random.Random(mixed)


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
