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
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from sdfb_core.contracts import GeneratedRecord, TableSchema


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
    ) -> list[dict]:
        """Return up to `n` JSON dicts conforming to `json_schema`."""
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

    model_config = ConfigDict(frozen=True)

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
