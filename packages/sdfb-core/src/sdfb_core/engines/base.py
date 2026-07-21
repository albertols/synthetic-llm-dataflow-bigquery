"""The `GenerationEngine` ABC and its supporting types.

This is the single seam between the Beam pipeline and the synthesis
logic. The pipeline (Beam DAG, M1 §8) constructs an engine in
`DoFn.setup()` from a CLI flag and calls `generate_batch()` per request.
It never knows whether it's holding a B.1 RAG engine or a B.2
library-wrapper — both satisfy this interface.

REF: https://beam.apache.org/documentation/ml/large-language-modeling/
REF: https://beam.apache.org/releases/pydoc/current/apache_beam.ml.inference.base.html
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from sdfb_core.contracts import GeneratedRecord, TableSchema


class FreeTextEmptyYieldError(RuntimeError):
    """The LLM call for a free-text pool succeeded but yielded zero usable
    values (e.g. every choice was dropped at JSON parse).

    Raised under ``strict_freetext`` so a run whose LLM contributes nothing
    fails loudly instead of silently degrading to exemplar memorization —
    the 2026-07-15 E2E failure mode (all 8x32 guided-JSON choices dropped,
    gates PASSED, copy_ratio=1.0 on every free-text column).
    """


def escalating_temperatures(start: float = 0.7) -> tuple[float, ...]:
    """Sampling temperatures for free-text pool retries, ascending from
    ``start`` up to 1.3.

    An LLM pool call whose NOVEL yield is empty (every value a verbatim copy
    of a reference value — the 2026-07-16 corp run, where Qwen3-4B echoed
    the seed exemplars on all 32 choices) is retried at each successive
    temperature before the engine gives up. Higher temperature diversifies
    sampling away from the exemplar echoes; 1.3 is the ceiling B.2 already
    uses for maximum divergence (`similarity_to_temperature`).
    """
    ceiling = 1.3
    start = min(max(start, 0.0), ceiling)
    steps = (1.0, ceiling)
    return (start, *(t for t in steps if t > start + 1e-9))


class SamplingLevel(NamedTuple):
    """One free-text pool attempt's sampling configuration.

    ``None`` for ``top_p`` / ``top_k`` leaves the served model's own defaults
    (its ``generation_config.json``) in force; explicit values override them
    per-request.
    """

    temperature: float
    top_p: float | None = None
    top_k: int | None = None


def escalating_sampling(start: float = 0.7) -> tuple[SamplingLevel, ...]:
    """Sampling configurations for free-text pool retries.

    Temperature alone is NOT enough: a served model can pin sampling
    truncation via its shipped ``generation_config.json`` (Qwen3-4B:
    ``top_k=20, top_p=0.8`` — vLLM logs an override warning). When the model
    is confident in echoing an exemplar, that nucleus collapses to the echo
    token and temperature has nothing left to diversify — the 2026-07-16 corp
    run produced 96/96 verbatim copies at 0.7, 1.0 AND 1.3. So the first
    attempt keeps the vendor-tuned defaults, and every retry escalates
    temperature (via `escalating_temperatures`) with the truncation fully
    unclamped: ``top_p=1.0`` and ``top_k=0`` (vLLM: consider all tokens). A
    ``start`` already at the temperature ceiling still gets one unclamped
    retry — that is the retry that can actually change the outcome.
    """
    temps = escalating_temperatures(start)
    retries = [SamplingLevel(t, top_p=1.0, top_k=0) for t in temps[1:]]
    if not retries:
        retries = [SamplingLevel(temps[0], top_p=1.0, top_k=0)]
    return (SamplingLevel(temps[0]), *retries)


@runtime_checkable
class ModelClient(Protocol):
    """Thin facade engines call to invoke the LLM.

    Real implementation: `sdfb_beam.handlers.vllm_client.VLLMModelClient`
    (M1 §9, M4-only). Test implementation:
    `sdfb_tests.fakes.FakeModelClient`. Engines never know which is in
    use — they import only this Protocol.

    The contract is intentionally narrow: a JSON-schema-guided generation
    call. The vLLM backend (via Beam's `VLLMCompletionsModelHandler`)
    enforces the schema with vLLM's guided decoding; outlines /
    lm-format-enforcer are fallback knobs. See ADR 0011.

    REFs:
      - docs/adr/0011-adopt-beam-vllm-model-handler.md
      - https://docs.vllm.ai/en/latest/usage/structured_outputs.html
    """

    def generate_json(
        self,
        prompt: str,
        json_schema: dict,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        n: int = 1,
        seed: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
    ) -> list[dict]:
        """Return up to `n` JSON dicts conforming to `json_schema`.

        ``top_p`` / ``top_k`` are per-request truncation overrides: ``None``
        keeps the served model's defaults (its ``generation_config.json``);
        explicit values override them (``top_k=0`` = consider all tokens).
        See `escalating_sampling` for why retries must send them.
        """
        ...


class GenerationConfig(BaseModel):
    """Per-batch knobs for a generation call.

    `similarity` is engine-interpreted: 0.0 = pure random (within schema),
    1.0 = mimic reference closely. B.2 maps it to library sampling
    temperature; B.1 maps it to a retrieval-vs-perturbation balance.
    """

    model_config = ConfigDict(frozen=True)

    similarity: float = Field(default=0.5, ge=0.0, le=1.0)
    batch_size: int = Field(default=16, ge=1)
    max_retries: int = Field(default=2, ge=0)
    seed: int | None = None
    engine_specific: dict = Field(default_factory=dict)


class GenerationContext(BaseModel):
    """Per-worker setup context.

    Stable across all `generate_batch` calls within a single Beam
    worker's lifetime — built once from side inputs (DDL + reference
    rows + digest + run id) and handed to `setup()`.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    table_schema: TableSchema
    reference_rows: list[dict] = Field(default_factory=list)
    reference_digest: str = ""
    pipeline_run_id: str = ""
    # Model locations for engines that build their own backends. `model_uri`
    # is the LLM weights (the ModelClient also gets it); `embedder_uri` is
    # B.1's embedder. On the worker these are local paths (warm-pulled from
    # GCS by the DoFn); empty ⇒ the engine uses its dependency-free default
    # (e.g. HashingEmbedder), which is what the contract tests exercise.
    model_uri: str = ""
    embedder_uri: str = ""
    # Columns that must be per-row-unique and NEVER sampled from reference
    # data (PK / UUID / account-number style). See engines/identity.py.
    identity_columns: list[str] = Field(default_factory=list)
    # When True (real-LLM runs), a failed free-text LLM call re-raises
    # instead of silently falling back to reference exemplars. The 2026-07-10
    # E2E runs shipped 100% memorized identifiers because the fallback was
    # only a WARNING. Fake/mock clients keep the lenient default.
    strict_freetext: bool = False
    # --- RAG layer (WS2 §4b) -------------------------------------------
    # Requested synthetic row count — bounds the free-text pool target
    # (min(num_rows, column_distinct, _FREE_TEXT_POOL_MAX)). 0 = unknown.
    num_rows: int = 0
    # Pinned vector space for rag_chunks reads. Derived DRIVER-side from
    # the original embedder URI (the worker only sees the localized path).
    embedder_id: str = ""
    embedder_version: str = ""
    # FQN of synthetic_rag.rag_chunks; empty ⇒ the read path is off. The
    # live store object is attached worker-side (it cannot be pickled):
    # the DoFn does ctx.model_copy(update={"chunk_store": store}).
    rag_chunks_table: str = ""
    chunk_store: object | None = None


class GenerationEngine(ABC):
    """Abstract base for synthetic-data generation engines.

    Concrete subclasses live in `engines/b1_rag/` (RAG) and
    `engines/b2_library/` (library-wrapper). Both must pass the contract
    tests in `sdfb-tests/tests/unit/engines/test_abc_contract.py`.

    Lifecycle (called by the Beam `DoFn`):
      `DoFn.setup`     → `engine.setup(model_client, ctx)`
      `DoFn.process`   → `engine.generate_batch(n, cfg)` (many times)
      `DoFn.teardown`  → `engine.teardown()`
    """

    name: str = ""
    # Engine CODE version — bumped by hand when engine logic changes
    # materially. Distinct from validation_runs.model_uri (LLM weights).
    # Feeds validation_data_history.engine_version (WS3).
    version: str = "0.1.0"

    @abstractmethod
    def setup(self, model_client: ModelClient, ctx: GenerationContext) -> None:
        """Called once per worker before any `generate_batch`.

        Heavy init lives here: vector index build (B.1), tabular library
        fit (B.2). Must be idempotent — a second call with the same
        arguments is a no-op (does not re-fit / re-embed).
        """

    @abstractmethod
    def generate_batch(
        self,
        n: int,
        cfg: GenerationConfig,
    ) -> Iterator[GeneratedRecord]:
        """Yield up to `n` schema-conformant records.

        Yielding fewer than `n` is allowed; the caller will request more
        if needed. Candidates that fail the engine's internal validation
        (schema, repair-loop budget exhausted) are silently dropped here —
        the Beam DoFn routes Pydantic and Pandera failures to the DLQ
        from a downstream stage, not from inside the engine.

        Raises `RuntimeError` if `setup()` has not run, or has been
        followed by `teardown()`.
        """

    @abstractmethod
    def teardown(self) -> None:
        """Release worker resources (vector index, fitted model, GPU
        references). After `teardown()`, `generate_batch` must raise
        `RuntimeError` until `setup()` is called again."""
