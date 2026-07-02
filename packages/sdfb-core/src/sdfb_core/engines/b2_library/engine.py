"""``B2LibraryEngine`` — the B.2 library-wrapper synthesis engine (M1 §6).

Implements the shared FASTGEN spine (ADR 0013, design §2/§4):

  setup(model_client, ctx):       # once per Beam worker, idempotent
      profile reference_rows → per-column ColumnProfile
      fit the statistical backend (sdgx CTGAN, NumPy-empirical fallback) ONCE
  generate_batch(n, cfg):         # many times; cheap
      vectorized-sample n rows from the fitted backend (seeded)
      enforce fidelity (constant/range/enum clamps, LOCAL to b2_library)
      patch FREE_TEXT / JSON / high-cardinality columns via the ModelClient hook
      yield GeneratedRecord
  teardown():                     # release the fitted model

The engine is built in ``DoFn.setup()`` and must pickle across the worker
boundary: all state is either plain data (profiles), a NumPy-only object
(EmpiricalBackend), or an sdgx ``Synthesizer`` (picklable). The bulk
sampling is CPU/NumPy — only the bounded free-text pool touches the GPU.

PURE sdfb-core: no ``apache_beam``, no ``vllm``, no GCP. ``sdgx`` (and
torch) are imported lazily inside the backend's ``fit()`` so importing this
module works on a laptop with only base deps installed.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from sdfb_core.codegen import derive_record_model
from sdfb_core.contracts import GeneratedRecord
from sdfb_core.engines.b2_library.backends import EmpiricalBackend, SdgxBackend
from sdfb_core.engines.b2_library.fidelity import (
    ColumnKind,
    enforce_value,
    profile_table,
)
from sdfb_core.engines.b2_library.freetext import FreeTextHook
from sdfb_core.engines.base import (
    GenerationConfig,
    GenerationContext,
    GenerationEngine,
    ModelClient,
)


class B2LibraryEngine(GenerationEngine):
    """Library-wrapper engine: fit-once statistical model + free-text LLM hook.

    The backend is chosen at ``setup`` time. ``use_sdgx`` (default ``True``)
    attempts the production ``sdgx`` CTGAN fit and transparently falls back
    to the pure-NumPy :class:`EmpiricalBackend` when ``sdgx`` is not
    installed (the laptop / offline case). Set ``use_sdgx=False`` to force
    the deterministic NumPy backend — this is what the ABC contract tests
    use so reproducibility is bit-for-bit.
    """

    name = "b2_library"

    def __init__(self, *, use_sdgx: bool = True) -> None:
        self._use_sdgx = use_sdgx
        self._ctx: GenerationContext | None = None
        self._record_model: type[GeneratedRecord] | None = None
        self._profiles: dict = {}
        self._free_text_cols: list[str] = []
        self._backend = None  # SamplingBackend | None
        self._freetext_hook: FreeTextHook | None = None
        self._fitted: bool = False

    # -- lifecycle ----------------------------------------------------------

    def setup(self, model_client: ModelClient, ctx: GenerationContext) -> None:
        if self._fitted:
            return  # idempotent — never re-fit / re-profile.

        self._ctx = ctx
        self._record_model = derive_record_model(ctx.table_schema)
        self._profiles = profile_table(ctx.table_schema, ctx.reference_rows)
        self._free_text_cols = [
            name
            for name, p in self._profiles.items()
            if p.kind is ColumnKind.FREE_TEXT
        ]
        self._freetext_hook = FreeTextHook(model_client)

        backend = self._make_backend()
        if ctx.reference_rows:
            backend.fit(ctx.reference_rows, self._profiles)
        self._backend = backend
        self._fitted = True

    def _make_backend(self):
        if self._use_sdgx:
            return SdgxBackend()
        return EmpiricalBackend()

    # -- pickling across the Beam worker boundary ---------------------------
    #
    # The dynamically-created Pydantic record model (`create_model`) is the
    # one piece of state that may not pickle reliably, so we drop it on dump
    # and rebuild it from the (picklable) `TableSchema` in `ctx` on load. The
    # fitted backend, profiles, and free-text hook are all plain data /
    # NumPy / picklable sdgx `Synthesizer` objects.

    def __getstate__(self) -> dict:
        state = self.__dict__.copy()
        state["_record_model"] = None
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        if self._record_model is None and self._ctx is not None:
            self._record_model = derive_record_model(self._ctx.table_schema)

    def teardown(self) -> None:
        self._backend = None
        self._freetext_hook = None
        self._record_model = None
        self._profiles = {}
        self._free_text_cols = []
        self._ctx = None
        self._fitted = False

    # -- generation ---------------------------------------------------------

    def generate_batch(
        self,
        n: int,
        cfg: GenerationConfig,
    ) -> Iterator[GeneratedRecord]:
        if not self._fitted or self._backend is None or self._record_model is None:
            raise RuntimeError(
                "B2LibraryEngine.generate_batch called before setup() "
                "(or after teardown())."
            )
        if self._ctx is None or not self._ctx.reference_rows or n <= 0:
            return

        rng = np.random.default_rng(cfg.seed)
        temperature = _similarity_to_sampling_temperature(cfg.similarity)

        # 1. Vectorized backend sample for all non-free-text columns.
        columns = self._backend.sample_columns(n, rng, temperature=temperature)

        # 2. Free-text columns via the bounded LLM pool hook.
        for name in self._free_text_cols:
            columns[name] = self._freetext_hook.sample(
                self._profiles[name], n, cfg, rng
            )

        # 3. Assemble rows, enforce fidelity, validate, yield.
        col_order = [c.name for c in self._ctx.table_schema.columns]
        for i in range(n):
            row = self._assemble_row(columns, col_order, i)
            try:
                yield self._record_model.model_validate(row)
            except Exception:
                continue

    def _assemble_row(
        self, columns: dict[str, list], col_order: list[str], i: int
    ) -> dict:
        row: dict = {}
        for name in col_order:
            profile = self._profiles.get(name)
            raw = columns[name][i] if name in columns else None
            row[name] = enforce_value(profile, raw) if profile is not None else raw
        return row


def _similarity_to_sampling_temperature(similarity: float) -> float:
    """Map ``cfg.similarity`` → backend categorical sampling temperature.

    similarity→1 ⇒ temperature→0 (collapse to the empirical mode: maximal
    mimicry); similarity→0 ⇒ temperature→~2 (flatten toward uniform within
    the observed support: maximal diversity). similarity 0.5 ≈ temperature
    1.0 (the empirical distribution itself).
    """
    s = min(max(similarity, 0.0), 1.0)
    return round(2.0 * (1.0 - s), 4)
