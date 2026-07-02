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

import numpy as np

from sdfb_core.engines.b2_library.fidelity import ColumnProfile
from sdfb_core.engines.base import GenerationConfig, ModelClient

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
    in ``generate_batch``. The pool is cached keyed by ``(column, seed,
    similarity)`` so repeated batches with the same config reuse it
    (idempotent, reproducible).
    """

    def __init__(
        self,
        model_client: ModelClient,
        *,
        pool_size: int = _DEFAULT_POOL_SIZE,
    ) -> None:
        self._client = model_client
        self._pool_size = pool_size
        self._cache: dict[tuple[str, int | None, float], list[str]] = {}

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
        """
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
        key = (profile.name, cfg.seed, round(cfg.similarity, 4))
        if key in self._cache:
            return self._cache[key]
        pool = self._generate_pool(profile, cfg)
        self._cache[key] = pool
        return pool

    def _generate_pool(
        self, profile: ColumnProfile, cfg: GenerationConfig
    ) -> list[str]:
        exemplars = list(profile.text_pool[: self._pool_size])
        prompt = (
            f"Generate up to {self._pool_size} realistic, fictitious but "
            f"plausible values for the column '{profile.name}'. Stay "
            f"consistent in style and format with these reference examples: "
            f"{exemplars}. Return JSON {{\"values\": [...]}}."
        )
        try:
            responses = self._client.generate_json(
                prompt=prompt,
                json_schema=_pool_schema(profile.name),
                max_tokens=2048,
                temperature=similarity_to_temperature(cfg.similarity),
                n=1,
                seed=cfg.seed,
            )
        except Exception:
            return exemplars

        pool = _extract_values(responses)
        # Always fold in observed exemplars so the pool is never empty and
        # the column stays plausibly in-distribution even if the LLM whiffs.
        merged = _dedupe_stable(pool + exemplars)
        return merged[: self._pool_size] if merged else exemplars


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
