#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
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
  ``sdfb-core``'s base deps. **It has never generated in production**:
  the locked sdgx 0.2.0 has no ``DataFrameConnector`` and the releases
  that have it (0.2.1-0.2.4) pin ``numpy<2``, which cannot install beside
  the workspace's Python 3.14 / numpy 2. A cheap probe
  (:func:`sdgx_incompatibility`) detects that once per process and the
  backend delegates to :class:`EmpiricalBackend`, with ONE
  ``b2_backend_fallback reason=sdgx_incompatible`` milestone per process
  instead of a failed import per table.

Both fitted backends pickle across the Beam worker boundary: the engine is
built once in ``DoFn.setup()`` and must survive serialization. NumPy state
is plain data; ``sdgx``'s ``Synthesizer`` exposes ``save``/``load`` and the
underlying torch model is picklable.

Free-text columns are **excluded** from both backends — they are produced
by the ``ModelClient`` free-text hook (``freetext.py``), not sampled here.

Design: docs/DESIGN.md §4 Relational generation; §5 Fidelity
(ADR 0022, 0036).
"""

# Heavy or optional dependencies are imported lazily, where they are used.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import logging
import threading
from importlib.util import find_spec
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np

from sdfb_core.engines.b2_library.fidelity import ColumnKind, ColumnProfile
from sdfb_core.engines.b2_library.temporal import sample_temporal
from sdfb_core.observability import log_milestone

if TYPE_CHECKING:
  import pandas as pd

# Below this temperature, categorical sampling collapses to the modal value
# (maximal mimicry) instead of softmax-reweighting — avoids divide-by-tiny.
_TEMP_EPSILON = 1e-9
# An inverse-CDF needs at least two quantile points to interpolate between.
_MIN_QUANTILE_POINTS = 2
# Smoothing added to weights before the log in temperature reweighting.
_LOG_SMOOTHING = 1e-12


class SamplingBackend(Protocol):
  """What the engine needs from a fitted distribution model.

    ``sample_columns(n, rng)`` returns one Python ``list`` per non-free-text
    column name → length-``n`` list of sampled values. Free-text columns are
    omitted (the LLM hook fills them). Seeded via the passed ``np.random``
    generator for reproducibility.
    """

  def fit(self, reference_rows: list[dict],
          profiles: dict[str, ColumnProfile]) -> None:
    ...

  def sample_columns(
      self,
      n: int,
      rng: np.random.Generator,
      *,
      temperature: float = 1.0,
  ) -> dict[str, list]:
    """Sample ``n`` values for every non-free-text column."""


def _samplable_profiles(
    profiles: dict[str, ColumnProfile],) -> dict[str, ColumnProfile]:
  """Profiles the backend is responsible for (everything but free-text)."""
  return {
      name: p
      for name, p in profiles.items()
      if p.kind is not ColumnKind.FREE_TEXT
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
      self,
      reference_rows: list[dict],  # pylint: disable=unused-argument
      profiles: dict[str, ColumnProfile]) -> None:
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
        rng.random(n) < p.null_fraction if
        (p.nullable and p.null_fraction > 0.0) else np.zeros(n, dtype=bool))

    if p.kind is ColumnKind.CONSTANT:
      values: list = [p.constant_value] * n

    elif p.kind is ColumnKind.NUMERIC:
      lo = p.minimum if p.minimum is not None else 0.0
      hi = p.maximum if p.maximum is not None else lo
      if len(p.quantiles) >= _MIN_QUANTILE_POINTS:
        # Inverse transform sampling over the empirical CDF: uniform
        # draws map through the observed decile vector, so a skewed
        # source marginal lands skewed. Plain uniform-in-range put
        # ~99% of a 90/10 heavy-tailed column above its true p90
        # (ADR 0022).
        grid = np.linspace(0.0, 1.0, len(p.quantiles))
        draws = np.interp(rng.random(n), grid, np.asarray(p.quantiles))
      elif hi > lo:
        draws = rng.uniform(lo, hi, size=n)
      else:
        draws = np.full(n, lo)
      values = [round(x) for x in draws
               ] if p.is_integer else [float(x) for x in draws]

    elif p.kind is ColumnKind.CATEGORICAL:
      values = _sample_categorical(p, n, rng, temperature)

    elif p.kind is ColumnKind.TEMPORAL:
      # TEMPORAL profiles always carry a value type (set beside kind).
      values = _inject_temporal_sentinels(
          p,
          sample_temporal(
              p.minimum,
              p.maximum,
              cast("str", p.temporal_value_type),
              p.temporal_format,
              n,
              rng,
              quantiles=p.quantiles,
          ),
          rng,
      )

    else:  # FREE_TEXT shouldn't reach here (filtered in fit()).
      values = [None] * n

    return [None if null_mask[i] else values[i] for i in range(n)]


def _inject_temporal_sentinels(
    p: ColumnProfile,
    values: list,
    rng: np.random.Generator,
) -> list:
  """Overwrite jittered temporal values with the profile's sentinel values
    at their observed fractions (0001-01-01 / 9999-12-31 style — excluded
    from the jitter [min, max] by the profiler, reproduced here instead)."""
  if not p.temporal_sentinels:
    return values
  draws = rng.random(len(values))
  out = list(values)
  for i, r in enumerate(draws):
    acc = 0.0
    for sentinel_value, fraction in p.temporal_sentinels:
      acc += fraction
      if r < acc:
        out[i] = sentinel_value
        break
  return out


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
  if not cats:
    # A driven child's inherited-edge placeholder (ADR 0036): the real
    # values are not known at fit time — `generate_for_keys` overwrites
    # this column from the parent keys right after sampling — so there
    # is nothing to draw from. Mirrors `_fill_from_profile`'s empty-
    # categories fallback; without this guard `np.asarray([])` on the
    # empty `cats` list below defaults to float64 (not bool), and the
    # sparsity mask's `~sparse` raises TypeError.
    return [None] * n
  weights = np.asarray(p.weights, dtype=float)
  if weights.sum() <= 0:
    weights = np.ones(len(cats))
  weights = weights / weights.sum()

  # Sparsity categories (empty/whitespace strings, None) keep their exact
  # empirical mass — empty parity is a hard fidelity metric and the
  # similarity blend flattening it failed `freetext.empty_parity` on the
  # 2026-08-09 B_TABLE R1 (B.1 parity: ColumnSampler._categorical_masses).
  # Reweighting applies only within the substantive remainder.
  sparse = np.asarray(
      [c is None or (isinstance(c, str) and not c.strip()) for c in cats])
  sub_mass = float(weights[~sparse].sum())
  if sparse.any() and sub_mass > 0:
    sub_w = weights[~sparse] / sub_mass
    if temperature <= _TEMP_EPSILON:
      reweighted = np.zeros_like(sub_w)
      reweighted[int(np.argmax(sub_w))] = 1.0
    else:
      logits = np.log(sub_w + _LOG_SMOOTHING) / temperature
      logits -= logits.max()
      reweighted = np.exp(logits)
      reweighted /= reweighted.sum()
    probs = weights.copy()
    probs[~sparse] = reweighted * sub_mass
    probs /= probs.sum()
    picks = rng.choice(len(cats), size=n, p=probs)
    return [cats[int(i)] for i in picks]

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


# The connector `_fit_sdgx` needs: absent from the locked sdgx 0.2.0.
_SDGX_CONNECTOR = "sdgx.data_connectors.dataframe_connector"
# The probe's verdict, computed once per process ({"reason": str | None}).
_SDGX_PROBE: dict[str, str | None] = {}
_SDGX_PROBE_LOCK = threading.Lock()


def _sdgx_version() -> str:
  from importlib.metadata import PackageNotFoundError, version

  try:
    return version("sdgx")
  except PackageNotFoundError:
    return "unknown"


def _probe_sdgx() -> tuple[str | None, str]:
  """(reason, detail): reason None when `_fit_sdgx` can import what it
    needs. ``find_spec`` on the dotted name imports ``sdgx`` and
    ``sdgx.data_connectors`` (pandas + pluggy in 0.2.0, no torch) but not
    the connector."""
  try:
    if find_spec("sdgx") is None:
      return "sdgx_not_installed", "no module named 'sdgx'"
    if find_spec(_SDGX_CONNECTOR) is None:
      return ("sdgx_incompatible",
              f"sdgx {_sdgx_version()} has no {_SDGX_CONNECTOR}")
  # The probe must never fail the engine: any error importing the parent
  # packages is an incompatibility, like the import failures fit() caught.
  except Exception as e:  # pylint: disable=broad-exception-caught
    return "sdgx_incompatible", f"{type(e).__name__}: {e}"[:160]
  return None, ""


def sdgx_incompatibility() -> str | None:
  """Why ``sdgx`` cannot be fitted in this process, or None when it can.

    Probed once per process; the first caller logs the one
    ``b2_backend_fallback`` milestone, so a job with many b2 tables says
    it once instead of failing the same import per table (2026-11-13:
    every b2 table logged ``No module named
    sdgx.data_connectors.dataframe_connector``)."""
  with _SDGX_PROBE_LOCK:
    if "reason" not in _SDGX_PROBE:
      reason, detail = _probe_sdgx()
      _SDGX_PROBE["reason"] = reason
      if reason is not None:
        log_milestone(
            "b2_backend_fallback",
            level=logging.WARNING,
            backend="empirical",
            reason=reason,
            scope="process",
            detail=detail,
        )
    return _SDGX_PROBE["reason"]


class SdgxBackend:
  """Production backend wrapping ``sdgx`` CTGAN. Deferred heavy import.

    Fits ``sdgx``'s :class:`Synthesizer` with a CTGAN-family model on the
    reference rows in :meth:`fit`, then :meth:`sample_columns` calls
    ``Synthesizer.sample``. Non-numeric/categorical columns are still routed
    through the engine's fidelity clamps afterward, and free-text columns
    are dropped here (the LLM hook owns them).

    When :func:`sdgx_incompatibility` says ``sdgx`` cannot be fitted here
    (not installed, or the locked 0.2.0 with no ``DataFrameConnector`` —
    every production run so far), the fit is not attempted and this
    backend delegates to :class:`EmpiricalBackend`. A fit that fails on a
    compatible ``sdgx`` falls back the same way, with a milestone per
    table. Either way ``self.used_fallback`` records it.
    """

  def __init__(self, *, epochs: int = 100) -> None:
    self.epochs = epochs
    self._synthesizer = None  # sdgx.synthesizer.Synthesizer | None
    self._fallback: EmpiricalBackend | None = None
    self._profiles: dict[str, ColumnProfile] = {}
    self._free_text_cols: set[str] = set()
    self.used_fallback: bool = False

  def fit(self, reference_rows: list[dict],
          profiles: dict[str, ColumnProfile]) -> None:
    self._profiles = _samplable_profiles(profiles)
    self._free_text_cols = {
        name for name, p in profiles.items() if p.kind is ColumnKind.FREE_TEXT
    }
    if sdgx_incompatibility() is not None:
      # Said once per process by the probe; per table, the engine's
      # generation_plan reports backend=sdgx_fallback_empirical.
      self._fall_back(reference_rows, profiles)
      return
    try:
      self._fit_sdgx(reference_rows)
    except Exception as e:  # pylint: disable=broad-exception-caught
      self._fall_back(reference_rows, profiles)
      # Loud, like every other fallback: the 2026-07-22 b2 E2E run's
      # worker logs could not tell which backend actually generated
      # (a 3 s "fit" is the fallback, but nothing said so).
      log_milestone(
          "b2_backend_fallback",
          level=logging.WARNING,
          backend="empirical",
          reason="sdgx_fit_failed",
          error=type(e).__name__,
          # The message names WHAT failed (e.g. the missing module) —
          # the 2026-07-22 re-run logged only the type, leaving the
          # actual sdgx import defect unknowable from worker logs.
          detail=str(e)[:160],
      )
    else:
      log_milestone("b2_backend_fitted", backend="sdgx")

  def _fall_back(self, reference_rows: list[dict],
                 profiles: dict[str, ColumnProfile]) -> None:
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

  def _reference_frame(self, reference_rows: list[dict],
                       pd_module) -> pd.DataFrame:
    """Reference rows → DataFrame, dropping free-text and temporal columns.

        Temporal columns are jitter-sampled from their profile, never fed
        to CTGAN — fitting raw timestamps makes the model resample the
        observed table (the 2026-07-20 memorization defect).
        """
    keep = {
        name for name, p in self._profiles.items()
        if p.kind is not ColumnKind.TEMPORAL
    }
    rows = [{
        k: v for k, v in row.items() if k in keep
    } for row in reference_rows]
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
      if p.kind is ColumnKind.TEMPORAL:
        values = _inject_temporal_sentinels(
            p,
            sample_temporal(p.minimum, p.maximum, p.temporal_value_type,
                            p.temporal_format, n, rng),
            rng,
        )
        if p.nullable and p.null_fraction > 0.0:
          null_mask = rng.random(n) < p.null_fraction
          values = [None if null_mask[i] else values[i] for i in range(n)]
        out[name] = values
      elif name in sampled.columns:
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
    except Exception:  # pylint: disable=broad-exception-caught
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
