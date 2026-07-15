"""Local fidelity primitives for the B.1 RAG engine.

These implement the spine's "fidelity by construction" guarantees (ADR
0013): constants are copied, numerics are clipped to the observed range,
categoricals are sampled at their empirical frequency, and free-text is
drawn from a bounded pool. Sampling is seeded → reproducible.

Kept LOCAL to `b1_rag/` per the spec (§2): "Kept local per engine during
parallel development; consolidate to a shared `engines/_fidelity.py`
post-merge if duplication warrants" — avoids a shared-file merge conflict
with the B.2 worktree.

**Sampling backend seam.** `NumPy` is the default when installed (vectorized,
the path the reproducibility tests pin). It is *deferred-imported*; when
absent the engine falls back to a pure-Python `random.Random` sampler that
produces the same *kind* of draws (seeded, in-range). The two backends are
not bit-identical to each other, but each is internally deterministic for a
fixed seed — which is what `test_seed_reproducibility` requires.

REF: spec §2 sampling-backend seam; cuDF/CuPy is the M1-optional GPU backend
(not implemented here — NumPy is the baseline).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sdfb_core.engines.b1_rag.profile import (
    ColumnKind,
    ColumnProfile,
    temporal_from_float,
    temporal_to_float,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence


def numpy_available() -> bool:
    """True if NumPy can be imported (selects the vectorized backend)."""
    try:
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


class ColumnSampler:
    """Samples one column's values for a batch, honoring its profile.

    `similarity` (0..1) widens or tightens the draw:
      - NUMERIC: similarity→1 keeps values near observed exemplars; →0
        spreads across the full observed range (always clipped to it).
      - CATEGORICAL: similarity→1 follows empirical frequencies; →0 flattens
        toward uniform over the observed support (never invents categories).
      - FREE_TEXT: handled by the engine via the LLM pool; this sampler only
        provides the null mask and a fallback draw from observed examples.
    """

    def __init__(self, profile: ColumnProfile) -> None:
        self.profile = profile
        self._temporal_floats: list[float] | None = None

    def _temporal_obs_floats(self) -> list[float]:
        """Observed TEMPORAL values on the float axis (computed once)."""
        if self._temporal_floats is None:
            self._temporal_floats = [
                f
                for f in (temporal_to_float(v) for v in self.profile.observed_values)
                if f is not None
            ]
        return self._temporal_floats

    # -- NumPy (vectorized) backend ----------------------------------------

    def sample_numpy(self, rng, n: int, similarity: float) -> list:
        import numpy as np

        p = self.profile
        if p.kind is ColumnKind.CONSTANT:
            return [p.constant_value] * n

        values = self._draw_numpy(np, rng, n, similarity)
        return self._apply_nulls_numpy(np, rng, values, n)

    def _draw_numpy(self, np, rng, n: int, similarity: float) -> list:
        p = self.profile
        if p.kind is ColumnKind.NUMERIC:
            return self._numeric_numpy(np, rng, n, similarity)
        if p.kind is ColumnKind.TEMPORAL:
            return self._temporal_numpy(np, rng, n, similarity)
        if p.kind is ColumnKind.CATEGORICAL:
            return self._categorical_numpy(np, rng, n, similarity)
        # FREE_TEXT fallback (engine normally patches these via the LLM pool).
        return self._from_pool_numpy(np, rng, p.text_examples or p.observed_values, n)

    def _blend_floats_numpy(
        self, np, rng, obs, lo: float, hi: float, n: int, similarity: float
    ) -> list:
        """Anchored/uniform blend of n floats within [lo, hi].

        similarity→1: sample observed values + small jitter (tight).
        similarity→0: uniform over [lo, hi] (wide). Out-of-range blends are
        REDRAWN uniformly inside the range, not clipped — clipping stacked
        ~5 % of draws exactly on the observed min/max (2026-07-15 E2E:
        single-value spikes at the bounds on COL_007/009/016/047/057).
        """
        if hi <= lo:
            return [lo] * n
        if not obs.size:
            return rng.uniform(lo, hi, size=n).tolist()
        idx = rng.integers(0, obs.size, size=n)
        anchored = obs[idx]
        spread = hi - lo
        jitter = rng.uniform(-0.5, 0.5, size=n) * spread * (1.0 - similarity)
        uniform = rng.uniform(lo, hi, size=n)
        blended = similarity * (anchored + jitter) + (1.0 - similarity) * uniform
        oob = (blended < lo) | (blended > hi)
        n_oob = int(oob.sum())
        if n_oob:
            blended[oob] = rng.uniform(lo, hi, size=n_oob)
        return blended.tolist()

    def _numeric_numpy(self, np, rng, n: int, similarity: float) -> list:
        p = self.profile
        lo = float(p.numeric_min if p.numeric_min is not None else 0.0)
        hi = float(p.numeric_max if p.numeric_max is not None else 0.0)
        obs = np.asarray(
            [float(x) for x in p.observed_values if _is_number(x)],
            dtype="float64",
        )
        base = self._blend_floats_numpy(np, rng, obs, lo, hi, n, similarity)
        return [self._coerce_numeric(v) for v in base]

    def _temporal_numpy(self, np, rng, n: int, similarity: float) -> list:
        p = self.profile
        lo = float(p.numeric_min if p.numeric_min is not None else 0.0)
        hi = float(p.numeric_max if p.numeric_max is not None else 0.0)
        obs = np.asarray(self._temporal_obs_floats(), dtype="float64")
        base = self._blend_floats_numpy(np, rng, obs, lo, hi, n, similarity)
        return [temporal_from_float(v, p.bq_type) for v in base]

    def _categorical_numpy(self, np, rng, n: int, similarity: float) -> list:
        p = self.profile
        cats = list(p.categories.keys())
        if not cats:
            return [None] * n
        counts = np.asarray([p.categories[c] for c in cats], dtype="float64")
        empirical = counts / counts.sum()
        uniform = np.full(len(cats), 1.0 / len(cats))
        probs = similarity * empirical + (1.0 - similarity) * uniform
        probs = probs / probs.sum()
        idx = rng.choice(len(cats), size=n, p=probs)
        return [cats[int(i)] for i in idx]

    def _from_pool_numpy(self, np, rng, pool: Sequence, n: int) -> list:
        pool = list(pool)
        if not pool:
            return [None] * n
        idx = rng.integers(0, len(pool), size=n)
        return [pool[int(i)] for i in idx]

    def _apply_nulls_numpy(self, np, rng, values: list, n: int) -> list:
        p = self.profile
        if not p.nullable or p.null_fraction <= 0.0:
            return values
        mask = rng.random(n) < p.null_fraction
        return [None if mask[i] else values[i] for i in range(n)]

    # -- pure-Python backend (no NumPy) ------------------------------------

    def sample_python(self, rng, n: int, similarity: float) -> list:
        p = self.profile
        if p.kind is ColumnKind.CONSTANT:
            return [p.constant_value] * n
        values = self._draw_python(rng, n, similarity)
        return self._apply_nulls_python(rng, values, n)

    def _draw_python(self, rng, n: int, similarity: float) -> list:
        p = self.profile
        if p.kind is ColumnKind.NUMERIC:
            return self._numeric_python(rng, n, similarity)
        if p.kind is ColumnKind.TEMPORAL:
            return self._temporal_python(rng, n, similarity)
        if p.kind is ColumnKind.CATEGORICAL:
            return self._categorical_python(rng, n, similarity)
        return self._from_pool_python(rng, p.text_examples or p.observed_values, n)

    def _blend_floats_python(
        self, rng, obs: list[float], lo: float, hi: float, n: int, similarity: float
    ) -> list[float]:
        """Pure-Python mirror of `_blend_floats_numpy` (same redraw-not-clip
        semantics; not bit-identical to the NumPy backend, but seeded)."""
        out: list[float] = []
        spread = hi - lo
        for _ in range(n):
            if hi <= lo:
                v = lo
            elif obs:
                anchored = rng.choice(obs)
                jitter = (rng.random() - 0.5) * spread * (1.0 - similarity)
                uniform = rng.uniform(lo, hi)
                v = similarity * (anchored + jitter) + (1.0 - similarity) * uniform
                if v < lo or v > hi:
                    v = rng.uniform(lo, hi)
            else:
                v = rng.uniform(lo, hi)
            out.append(v)
        return out

    def _numeric_python(self, rng, n: int, similarity: float) -> list:
        p = self.profile
        lo = float(p.numeric_min if p.numeric_min is not None else 0.0)
        hi = float(p.numeric_max if p.numeric_max is not None else 0.0)
        obs = [float(x) for x in p.observed_values if _is_number(x)]
        base = self._blend_floats_python(rng, obs, lo, hi, n, similarity)
        return [self._coerce_numeric(v) for v in base]

    def _temporal_python(self, rng, n: int, similarity: float) -> list:
        p = self.profile
        lo = float(p.numeric_min if p.numeric_min is not None else 0.0)
        hi = float(p.numeric_max if p.numeric_max is not None else 0.0)
        base = self._blend_floats_python(
            rng, self._temporal_obs_floats(), lo, hi, n, similarity
        )
        return [temporal_from_float(v, p.bq_type) for v in base]

    def _categorical_python(self, rng, n: int, similarity: float) -> list:
        p = self.profile
        cats = list(p.categories.keys())
        if not cats:
            return [None] * n
        counts = [p.categories[c] for c in cats]
        total = sum(counts)
        empirical = [c / total for c in counts]
        uniform = 1.0 / len(cats)
        weights = [
            similarity * empirical[i] + (1.0 - similarity) * uniform
            for i in range(len(cats))
        ]
        return rng.choices(cats, weights=weights, k=n)

    def _from_pool_python(self, rng, pool: Sequence, n: int) -> list:
        pool = list(pool)
        if not pool:
            return [None] * n
        return [pool[rng.randrange(len(pool))] for _ in range(n)]

    def _apply_nulls_python(self, rng, values: list, n: int) -> list:
        p = self.profile
        if not p.nullable or p.null_fraction <= 0.0:
            return values
        return [None if rng.random() < p.null_fraction else values[i] for i in range(n)]

    # -- shared --------------------------------------------------------------

    def _coerce_numeric(self, v: float):
        p = self.profile
        if p.is_integral:
            return round(v)
        if p.is_decimal:
            if _isnan(v):
                return None
            # Quantize to the column's DDL scale so the value satisfies the
            # derived model's `decimal_places` constraint exactly.
            scale = p.decimal_scale if p.decimal_scale is not None else 2
            quantum = Decimal(1).scaleb(-scale)  # e.g. scale=2 -> Decimal("0.01")
            return Decimal(str(v)).quantize(quantum)
        return float(v)


def _is_number(x: object) -> bool:
    if isinstance(x, bool):
        return False
    if isinstance(x, (int, float, Decimal)):
        return True
    if isinstance(x, str):
        try:
            Decimal(x)
            return True
        except Exception:
            return False
    return False


def _isnan(v: float) -> bool:
    return v != v  # noqa: PLR0124 — NaN check (NaN != NaN); robust for any value type


__all__ = ["ColumnSampler", "numpy_available"]
