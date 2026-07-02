"""Sampling-backend seam for the B.2 library engine (design §2).

Two backends sit behind one interface:

* :class:`EmpiricalBackend` — pure NumPy. Fits per-column empirical
  distributions and samples vectorized on CPU. Deterministic given a seed.
  This is the laptop / DirectRunner default and the offline fallback, and
  it is what the 5 ``GenerationEngine`` ABC contract tests pin (the spec
  fixes the NumPy backend for reproducibility — CuPy/CTGAN RNG is not
  bit-for-bit reproducible across machines).

* :class:`SdgxBackend` — wraps ``sdgx`` (hitsz-ids, Apache-2.0, CTGAN
  family). The ``sdgx`` import is **deferred** to ``fit()`` because it is
  heavy (pulls torch); importing this module must succeed with only
  ``sdfb-core``'s base deps. Used in production on the M4 / GPU workers.
  Falls back to :class:`EmpiricalBackend` if ``sdgx`` is not importable so
  the engine never hard-fails on the laptop.

Both fitted backends pickle across the Beam worker boundary: the engine is
built once in ``DoFn.setup()`` and must survive serialization. NumPy state
is plain data; ``sdgx``'s ``Synthesizer`` exposes ``save``/``load`` and the
underlying torch model is picklable.

Free-text columns are **excluded** from both backends — they are produced
by the ``ModelClient`` free-text hook (``freetext.py``), not sampled here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import numpy as np

from sdfb_core.engines.b2_library.fidelity import ColumnKind, ColumnProfile

if TYPE_CHECKING:
    import pandas as pd

# Below this temperature, categorical sampling collapses to the modal value
# (maximal mimicry) instead of softmax-reweighting — avoids divide-by-tiny.
_TEMP_EPSILON = 1e-9
# Smoothing added to weights before the log in temperature reweighting.
_LOG_SMOOTHING = 1e-12


class SamplingBackend(Protocol):
    """What the engine needs from a fitted distribution model.

    ``sample_columns(n, rng)`` returns one Python ``list`` per non-free-text
    column name → length-``n`` list of sampled values. Free-text columns are
    omitted (the LLM hook fills them). Seeded via the passed ``np.random``
    generator for reproducibility.
    """

    def fit(self, reference_rows: list[dict], profiles: dict[str, ColumnProfile]) -> None: ...

    def sample_columns(
        self,
        n: int,
        rng: np.random.Generator,
        *,
        temperature: float = 1.0,
    ) -> dict[str, list]:
        """Sample ``n`` values for every non-free-text column."""
        ...


def _samplable_profiles(
    profiles: dict[str, ColumnProfile],
) -> dict[str, ColumnProfile]:
    """Profiles the backend is responsible for (everything but free-text)."""
    return {
        name: p for name, p in profiles.items() if p.kind is not ColumnKind.FREE_TEXT
    }


class EmpiricalBackend:
    """Pure-NumPy empirical sampler. Deterministic. The contract-test backend.

    Per the FASTGEN spine: constants are copied, numerics sampled uniformly
    within the observed ``[min, max]`` (a deliberately conservative,
    fidelity-by-construction choice that never escapes the support),
    categoricals drawn from the empirical frequency table. ``temperature``
    (from ``cfg.similarity``) widens categorical sampling toward uniform as
    it rises; at ``temperature == 0`` categorical draws collapse to the
    modal value (maximal mimicry).
    """

    def __init__(self) -> None:
        self._profiles: dict[str, ColumnProfile] = {}

    def fit(
        self, reference_rows: list[dict], profiles: dict[str, ColumnProfile]
    ) -> None:
        # Profiling already happened in the engine; the empirical backend is
        # stateless beyond the profiles it samples from.
        self._profiles = _samplable_profiles(profiles)

    def sample_columns(
        self,
        n: int,
        rng: np.random.Generator,
        *,
        temperature: float = 1.0,
    ) -> dict[str, list]:
        out: dict[str, list] = {}
        for name, p in self._profiles.items():
            out[name] = self._sample_one(p, n, rng, temperature)
        return out

    @staticmethod
    def _sample_one(
        p: ColumnProfile,
        n: int,
        rng: np.random.Generator,
        temperature: float,
    ) -> list:
        # Inject nulls first where the schema allows and the reference showed
        # them, so the marginal null-rate is preserved.
        null_mask = (
            rng.random(n) < p.null_fraction
            if (p.nullable and p.null_fraction > 0.0)
            else np.zeros(n, dtype=bool)
        )

        if p.kind is ColumnKind.CONSTANT:
            values: list = [p.constant_value] * n

        elif p.kind is ColumnKind.NUMERIC:
            lo = p.minimum if p.minimum is not None else 0.0
            hi = p.maximum if p.maximum is not None else lo
            draws = rng.uniform(lo, hi, size=n) if hi > lo else np.full(n, lo)
            values = [round(x) for x in draws] if p.is_integer else [float(x) for x in draws]

        elif p.kind is ColumnKind.CATEGORICAL:
            values = _sample_categorical(p, n, rng, temperature)

        else:  # FREE_TEXT shouldn't reach here (filtered in fit()).
            values = [None] * n

        return [None if null_mask[i] else values[i] for i in range(n)]


def _sample_categorical(
    p: ColumnProfile,
    n: int,
    rng: np.random.Generator,
    temperature: float,
) -> list:
    """Empirical-frequency categorical draw with temperature reweighting.

    ``temperature`` interpolates between the empirical distribution
    (``1.0``) and either uniform (``>1``) or the mode (``→0``). We map
    ``cfg.similarity`` so similarity→1 ⇒ temperature→0 (mimic) and
    similarity→0 ⇒ temperature→~2 (diverge) in the engine.
    """
    cats = list(p.categories)
    weights = np.asarray(p.weights, dtype=float)
    if weights.sum() <= 0:
        weights = np.ones(len(cats))
    weights = weights / weights.sum()

    if temperature <= _TEMP_EPSILON:
        # Collapse to the mode: maximal mimicry.
        idx = int(np.argmax(weights))
        return [cats[idx]] * n

    # Temperature reweighting in log space, then renormalize.
    logits = np.log(weights + _LOG_SMOOTHING) / temperature
    logits -= logits.max()
    probs = np.exp(logits)
    probs /= probs.sum()
    picks = rng.choice(len(cats), size=n, p=probs)
    return [cats[int(i)] for i in picks]


class SdgxBackend:
    """Production backend wrapping ``sdgx`` CTGAN. Deferred heavy import.

    Fits ``sdgx``'s :class:`Synthesizer` with a CTGAN-family model on the
    reference rows in :meth:`fit`, then :meth:`sample_columns` calls
    ``Synthesizer.sample``. Non-numeric/categorical columns are still routed
    through the engine's fidelity clamps afterward, and free-text columns
    are dropped here (the LLM hook owns them).

    On any failure to import or fit ``sdgx`` (e.g. the laptop, where the
    ``[library]`` extra is not installed), this backend transparently
    delegates to :class:`EmpiricalBackend` so the engine still runs. The
    fact of the fallback is recorded on ``self.used_fallback``.
    """

    def __init__(self, *, epochs: int = 100) -> None:
        self.epochs = epochs
        self._synthesizer = None  # sdgx.synthesizer.Synthesizer | None
        self._fallback: EmpiricalBackend | None = None
        self._profiles: dict[str, ColumnProfile] = {}
        self._free_text_cols: set[str] = set()
        self.used_fallback: bool = False

    def fit(
        self, reference_rows: list[dict], profiles: dict[str, ColumnProfile]
    ) -> None:
        self._profiles = _samplable_profiles(profiles)
        self._free_text_cols = {
            name
            for name, p in profiles.items()
            if p.kind is ColumnKind.FREE_TEXT
        }
        try:
            self._fit_sdgx(reference_rows)
        except Exception:
            self._synthesizer = None
            self._fallback = EmpiricalBackend()
            self._fallback.fit(reference_rows, profiles)
            self.used_fallback = True

    def _fit_sdgx(self, reference_rows: list[dict]) -> None:
        # Deferred heavy imports — only here, never at module load. ANY
        # failure (missing extra, version-skewed API) propagates to fit()'s
        # except clause, which falls back to the NumPy EmpiricalBackend.
        import pandas as pd
        from sdgx.data_connectors.dataframe_connector import DataFrameConnector
        from sdgx.data_loader import DataLoader
        from sdgx.models.ml.single_table.ctgan import CTGANSynthesizerModel
        from sdgx.synthesizer import Synthesizer

        frame = self._reference_frame(reference_rows, pd)
        connector = DataFrameConnector(df=frame)
        loader = DataLoader(connector)
        metadata = self._build_metadata(loader, frame)
        synthesizer = Synthesizer(
            model=CTGANSynthesizerModel(epochs=self.epochs),
            data_connector=connector,
            metadata=metadata,
        )
        synthesizer.fit()
        self._synthesizer = synthesizer

    @staticmethod
    def _build_metadata(loader, frame):
        """Build sdgx Metadata across known API shapes (version-tolerant).

        sdgx has moved metadata construction over releases; we try the
        documented ``Metadata.from_dataloader`` first, then
        ``from_dataframe``, then let the Synthesizer auto-infer (return
        ``None``). The outer ``fit()`` try/except catches any residual
        breakage and falls back to the empirical backend.
        """
        from sdgx.data_models.metadata import Metadata

        if hasattr(Metadata, "from_dataloader"):
            return Metadata.from_dataloader(loader)
        if hasattr(Metadata, "from_dataframe"):
            return Metadata.from_dataframe(frame)
        return None

    def _reference_frame(self, reference_rows: list[dict], pd_module) -> pd.DataFrame:
        """Reference rows → DataFrame, dropping free-text columns (the LLM
        hook owns them; feeding high-cardinality prose to CTGAN is wasteful
        and degrades the fit)."""
        keep = set(self._profiles)
        rows = [{k: v for k, v in row.items() if k in keep} for row in reference_rows]
        return pd_module.DataFrame(rows)

    def sample_columns(
        self,
        n: int,
        rng: np.random.Generator,
        *,
        temperature: float = 1.0,
    ) -> dict[str, list]:
        if self._synthesizer is None:
            assert self._fallback is not None
            return self._fallback.sample_columns(n, rng, temperature=temperature)

        # sdgx is not bit-for-bit reproducible across machines; we seed torch
        # best-effort and rely on the engine's post-hoc enforcement + the
        # NumPy backend for the reproducibility contract test.
        self._seed_torch(int(rng.integers(0, 2**31 - 1)))
        sampled = self._synthesizer.sample(n)  # pandas DataFrame
        out: dict[str, list] = {}
        for name, p in self._profiles.items():
            if name in sampled.columns:
                out[name] = list(sampled[name])
            else:
                # CTGAN dropped a column (e.g. constant) — fill from profile.
                out[name] = _fill_from_profile(p, n)
        return out

    @staticmethod
    def _seed_torch(seed: int) -> None:
        try:
            import torch

            torch.manual_seed(seed)
        except Exception:
            pass


def _fill_from_profile(p: ColumnProfile, n: int) -> list:
    """Deterministic constant fill for a column sdgx omitted."""
    if p.kind is ColumnKind.CONSTANT:
        return [p.constant_value] * n
    if p.kind is ColumnKind.NUMERIC:
        lo = p.minimum if p.minimum is not None else 0.0
        return [round(lo) if p.is_integer else float(lo)] * n
    if p.kind is ColumnKind.CATEGORICAL and p.categories:
        return [p.categories[0]] * n
    return [None] * n
