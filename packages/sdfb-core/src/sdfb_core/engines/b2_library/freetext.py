"""Per-column free-text LLM hook for B.2 (design §4 step 4).

Columns the statistical library handles poorly — FREE_TEXT prose, JSON
blobs, very-high-cardinality strings (see ``fidelity._classify``) — are
patched here through the :class:`ModelClient` Protocol instead of being
sampled by the backend.

FASTGEN spine (ADR 0013): the LLM runs **O(1)**, not O(N). We ask the
client for a *bounded unique pool* of candidate values per free-text
column once, then sample-with-replacement (seeded) to fill the N rows. The
bulk N never hits the GPU.

``cfg.similarity`` is honored two ways:
  * it sets the LLM sampling ``temperature`` (similarity→1 ⇒ low temp,
    mimic the reference exemplars; similarity→0 ⇒ high temp, diverge), and
  * it biases the sample-with-replacement draw toward the observed
    reference pool (high similarity) vs. the freshly-generated pool (low
    similarity).

Engines import only the ``ModelClient`` Protocol — never ``vllm``.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from sdfb_core.engines.b2_library.fidelity import ColumnProfile
from sdfb_core.engines.base import (
    FreeTextEmptyYieldError,
    GenerationConfig,
    ModelClient,
    escalating_sampling,
)
from sdfb_core.engines.text_shapes import sample_identifier
from sdfb_core.observability import log_milestone

# Bounded pool size — the LLM emits at most this many unique candidates per
# free-text column regardless of N (the O(1) cost cap). Sized small so the
# guided-JSON call stays cheap; tune on the M4.
_DEFAULT_POOL_SIZE = 32


def similarity_to_temperature(similarity: float) -> float:
    """Map ``cfg.similarity`` ∈ [0,1] → LLM temperature ∈ [~0.1, ~1.3].

    similarity→1 ⇒ temp→0.1 (mimic exemplars closely); similarity→0 ⇒
    temp→1.3 (diverge). Linear; clamped to keep the LLM out of degenerate
    greedy/over-random regimes.
    """
    s = min(max(similarity, 0.0), 1.0)
    return round(1.3 - 1.2 * s, 4)


def _pool_schema(column_name: str) -> dict:
    """JSON schema for the bounded-pool guided-decoding call.

    Asks for an object with a ``values`` array of strings — the production
    vLLM client enforces this via guided JSON; the FakeModelClient ignores
    the schema and returns canned/echo dicts.
    """
    return {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
        "required": ["values"],
    }


class FreeTextHook:
    """Generates + caches a bounded value pool per free-text column.

    Built in the engine's ``setup`` (so the O(1) LLM call can happen once
    per worker per column the first time a column is sampled) and consumed
    in ``generate_batch``. The pool is cached keyed by ``(column,
    similarity)`` — NOT ``seed`` — so it is built genuinely **once per
    worker per column** (the FASTGEN O(1) guarantee, ADR 0013). Batch-to-
    batch value diversity does not come from rebuilding the pool: it comes
    from the per-batch-seeded with-replacement *draw* in :meth:`sample`
    (the ``rng`` argument), which is reseeded per batch by the caller
    (``derive_batch_seed(run_id, batch_id)`` in
    ``GenerateRecordsDoFn.process``) precisely so that repeated draws over
    the same pool do not repeat the same rows.

    Defect history (2026-07-20 b2 E2E run, JOB_STATE_FAILED): the cache used
    to be keyed ``(column, seed, similarity)``. Because ``cfg.seed`` is
    deliberately re-derived per batch (anti-replay design — a fixed seed
    across ~63 batches would replay the same draw every batch), that key
    never repeated, so the cache never hit: every one of ~63 batches
    rebuilt every FREE_TEXT column's pool via a fresh LLM call (63x the
    intended O(1) cost), and under ``strict_freetext`` each rebuild was a
    fresh chance for ``FreeTextEmptyYieldError`` to kill the whole batch
    (~60/63 batches failed). Dropping ``seed`` from the key fixes both: the
    cost regression and the failure amplification.

    A failed or empty-strict-yield build (see ``_generate_pool``'s
    exception path and its ``FreeTextEmptyYieldError`` raise) never reaches
    ``self._cache[key] = pool`` — the entry is left unset, so the next
    batch's call retries the build rather than being poisoned by a
    permanently-missing/empty cache entry.

    Caching contract (2026-07-21 review hardening): genuine LLM pools —
    full-size or undersized-but-nonempty — ARE cached, since they represent
    real (if degraded) novel generation. Exemplar-fallback results (the
    caught-exception path and the empty-novel-yield path in non-strict mode)
    are NEVER cached: a transient LLM hiccup on the first build must not
    permanently lock a column to exemplar-only values for the worker's
    lifetime — the next batch retries the build instead. The fallback value
    is still used for the *current* batch; only the caching is skipped.
    Concurrent DoFn threads on one worker race ``_pool_for``'s check-then-act
    over the plain-dict cache (see ``_SETUP_LOCK`` in
    ``sdfb_beam.handlers.vllm_client`` for the same class of race); ``_lock``
    double-checks under an instance lock so at most one genuine build happens
    per key. First-builder-wins is harmless regardless of which batch
    triggers the build: the pool build itself uses the batch-*independent*
    ``cfg.engine_specific["pool_seed"]`` (``GenerateRecordsDoFn.process`` sets
    it to the explicit base seed, or to a run_id-derived value at a reserved
    ``batch_id=-1`` namespace, never a per-batch seed) — so the pool's
    content does not depend on which batch happens to win the race. Explicit
    ``--seed`` reruns therefore reproduce the pool build (modulo LLM-server
    determinism) regardless of Beam's non-deterministic batch-to-worker
    scheduling (P6); derived-mode runs still vary run-to-run via the salted
    ``run_id``.
    """

    def __init__(
        self,
        model_client: ModelClient,
        *,
        pool_size: int = _DEFAULT_POOL_SIZE,
        strict: bool = False,
    ) -> None:
        self._client = model_client
        self._pool_size = pool_size
        self._strict = strict
        self._cache: dict[tuple[str, float], list[str]] = {}
        self._lock = threading.Lock()

    def __getstate__(self) -> dict:
        # `threading.Lock` is not picklable (Beam workers pickle the fitted
        # engine, e.g. across `generate_batch` boundaries in tests / bundle
        # snapshotting) — drop it from the pickled state and rebuild a fresh
        # one on unpickle rather than carrying lock state across processes.
        state = self.__dict__.copy()
        del state["_lock"]
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()

    def sample(
        self,
        profile: ColumnProfile,
        n: int,
        cfg: GenerationConfig,
        rng: np.random.Generator,
    ) -> list[str | None]:
        """Return ``n`` values for one free-text column.

        Builds (or reuses) the bounded pool via the LLM, then draws ``n``
        values with replacement. ``cfg.similarity`` biases the mix between
        the LLM-generated pool and the observed reference pool.

        Identifier-shaped columns never reach the LLM (or the reference
        blend — reference identifiers in the output are the leak): they
        generate format-preserving values per row from the profile's
        per-position template.
        """
        if profile.identifier_shape is not None:
            values = [
                sample_identifier(
                    profile.identifier_shape, lambda k: int(rng.integers(0, k))
                )
                for _ in range(n)
            ]
            if profile.nullable and profile.null_fraction > 0.0:
                null_mask = rng.random(n) < profile.null_fraction
                return [None if null_mask[i] else values[i] for i in range(n)]
            return values

        pool = self._pool_for(profile, cfg)
        ref_pool = list(profile.text_pool)

        # similarity high ⇒ favor the observed reference pool (mimic);
        # similarity low ⇒ favor the freshly-generated LLM pool (diverge).
        combined, probs = _blend_pools(pool, ref_pool, cfg.similarity)
        if not combined:
            return [None] * n

        picks = rng.choice(len(combined), size=n, p=probs)
        values: list[str | None] = [combined[int(i)] for i in picks]

        # Honor the marginal null-rate where the schema allows it.
        if profile.nullable and profile.null_fraction > 0.0:
            null_mask = rng.random(n) < profile.null_fraction
            values = [None if null_mask[i] else values[i] for i in range(n)]
        return values

    def _pool_for(self, profile: ColumnProfile, cfg: GenerationConfig) -> list[str]:
        key = (profile.name, round(cfg.similarity, 4))
        # Fast path: no lock. Safe because dict reads never race a dict
        # write in CPython, and a cached entry is never mutated in place.
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        with self._lock:
            # Re-check: a sibling thread may have built this key while we
            # waited on the lock (double-checked locking — pool builds are
            # seconds-long LLM calls; serializing duplicate builds is the
            # point, contention beyond that is negligible at ≤1 build per
            # column per worker).
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            pool, cacheable = self._generate_pool(profile, cfg)
            if cacheable:
                self._cache[key] = pool
            return pool

    def _generate_pool(
        self, profile: ColumnProfile, cfg: GenerationConfig
    ) -> tuple[list[str], bool]:
        exemplars = list(profile.text_pool[: self._pool_size])
        prompt = (
            f"You generate synthetic tabular data. First identify the exact "
            f"format of these example values for the column '{profile.name}' "
            f"(e.g. UUID, hexadecimal identifier, numeric code, date, "
            f"timestamp, natural-language text), then generate up to "
            f"{self._pool_size} NEW, distinct, fictitious values in exactly "
            f"that format. Never copy an example verbatim. Examples: "
            f"{exemplars}. Return JSON {{\"values\": [...]}}."
        )
        # Novelty filter: LLM values that equal observed reference values are
        # copies, not generations. The LLM pool is the "diverge" side of the
        # similarity blend — observed values reach the output only via the
        # reference pool, weighted by `cfg.similarity`. An empty NOVEL yield
        # (all copies / all parse-drops) is retried at escalating temperature
        # before falling back (2026-07-16 corp run: the model echoed the seed
        # exemplars verbatim at the base temperature).
        observed = set(profile.text_pool)
        shown = set(exemplars)
        pool: list[str] = []
        pool_seen: set[str] = set()
        seen: set[str] = set()
        n_parsed = 0
        n_copies = 0
        n_echoes = 0
        attempts = 0
        try:
            for level in escalating_sampling(
                similarity_to_temperature(cfg.similarity)
            ):
                attempts += 1
                responses = self._client.generate_json(
                    prompt=prompt,
                    json_schema=_pool_schema(profile.name),
                    max_tokens=2048,
                    temperature=level.temperature,
                    n=1,
                    seed=cfg.engine_specific.get("pool_seed", cfg.seed),
                    top_p=level.top_p,
                    top_k=level.top_k,
                )
                values = _extract_values(responses)
                novel = [v for v in values if v not in observed]
                n_parsed += len(values)
                n_copies += len(values) - len(novel)
                n_echoes += sum(1 for v in values if v in shown)
                seen.update(values)
                # Accumulate ACROSS levels until the pool target is met —
                # breaking on the first non-empty yield let one conservative
                # completion define the whole pool (2026-07-17 B.1 run: 4
                # distinct values over 1000 rows) and the unclamped retry
                # levels never executed.
                for v in novel:
                    if v not in pool_seen:
                        pool_seen.add(v)
                        pool.append(v)
                if len(pool) >= self._pool_size:
                    break
        except Exception as e:
            if self._strict:
                raise
            # Per-call generation failure: exemplar fallback is allowed, but
            # NEVER silently — a run where the LLM contributed nothing must be
            # visible in worker logs (E2E report §4.2: 100 % memorization).
            log_milestone(
                "freetext_llm_fallback",
                level=logging.WARNING,
                column=profile.name,
                error=type(e).__name__,
            )
            return exemplars, False

        if not pool:
            # The calls "succeeded" (no exception) yet yielded nothing usable.
            # Counts (never values — reference data must not leak into logs)
            # say WHY: parsed=0 means every choice was dropped at JSON parse;
            # low distinct with prompt_echoes == verbatim_copies means the
            # model parroted the shown exemplars; high distinct with low
            # prompt_echoes means in-format generations collided with the
            # full reference pool — a saturated key space. Exactly as loud as
            # the exception path: the 2026-07-15 E2E run memorized 100 % of
            # free-text values through this hole.
            diagnosis = (
                f"attempts={attempts}, parsed={n_parsed}, "
                f"distinct={len(seen)}, verbatim_copies={n_copies}, "
                f"prompt_echoes={n_echoes}, novel=0"
            )
            if self._strict:
                raise FreeTextEmptyYieldError(
                    f"LLM calls for free-text column {profile.name!r} "
                    f"yielded no usable values ({diagnosis})."
                )
            log_milestone(
                "freetext_llm_fallback",
                level=logging.WARNING,
                column=profile.name,
                error="EmptyYield",
                attempts=attempts,
                parsed=n_parsed,
                distinct=len(seen),
                verbatim_copies=n_copies,
                prompt_echoes=n_echoes,
            )
            return exemplars, False
        if len(pool) < self._pool_size:
            # Levels exhausted below target: the column lands with whatever
            # novelty the LLM delivered, but never silently.
            log_milestone(
                "freetext_pool_undersized",
                level=logging.WARNING,
                column=profile.name,
                pool_size=len(pool),
                target=self._pool_size,
                attempts=attempts,
                parsed=n_parsed,
                distinct=len(seen),
                verbatim_copies=n_copies,
                prompt_echoes=n_echoes,
            )
        # The LLM pool stays novel-only; `_blend_pools` already mixes the
        # observed reference pool back in proportionally to `cfg.similarity`,
        # so folding exemplars HERE double-counted them and turned the
        # "diverge" side of the blend into more memorization.
        return pool[: self._pool_size], True


def _extract_values(responses: list[dict]) -> list[str]:
    """Pull string values out of the client's JSON responses, tolerantly.

    Accepts the guided ``{"values": [...]}`` shape, a bare list, or echoed
    reference-row dicts (the FakeModelClient's reference-pool/canned modes).
    """
    out: list[str] = []
    for resp in responses:
        if isinstance(resp, dict) and "values" in resp and isinstance(resp["values"], list):
            out.extend(str(v) for v in resp["values"])
        elif isinstance(resp, dict):
            # Echoed reference row — take its string-valued fields.
            out.extend(str(v) for v in resp.values() if isinstance(v, str))
        elif isinstance(resp, str):
            out.append(resp)
    return [v for v in out if v]


def _blend_pools(
    llm_pool: list[str],
    ref_pool: list[str],
    similarity: float,
) -> tuple[list[str], np.ndarray]:
    """Combine the two pools into one value list + a probability vector.

    Mass ``similarity`` goes to the reference pool, ``1 - similarity`` to the
    LLM pool. Each pool's internal mass is uniform. Degenerate cases (one
    pool empty) put all mass on the non-empty pool.
    """
    s = min(max(similarity, 0.0), 1.0)
    combined = _dedupe_stable(ref_pool + llm_pool)
    if not combined:
        return [], np.asarray([])

    ref_set = set(ref_pool)
    has_ref = bool(ref_pool)
    has_llm = bool(llm_pool)
    if not has_ref:
        s = 0.0
    if not has_llm:
        s = 1.0

    n_ref = sum(1 for v in combined if v in ref_set)
    n_llm = len(combined) - n_ref
    probs = np.zeros(len(combined), dtype=float)
    for i, v in enumerate(combined):
        if v in ref_set:
            probs[i] = s / n_ref if n_ref else 0.0
        else:
            probs[i] = (1.0 - s) / n_llm if n_llm else 0.0
    total = probs.sum()
    probs = np.full(len(combined), 1.0 / len(combined)) if total <= 0 else probs / total
    return combined, probs


def _dedupe_stable(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
