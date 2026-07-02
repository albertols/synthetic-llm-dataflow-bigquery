---
name: engine-contract
description: Recipe for implementing a `GenerationEngine` subclass and the `ModelClient` Protocol that backs both B.1 (RAG) and B.2 (library-wrapper) engines. Load when adding or modifying any engine implementation in `sdfb_core/engines/`.
---

# Skill — implementing a `GenerationEngine`

Both engines must satisfy `sdfb_core.engines.base.GenerationEngine`. The ABC is the single seam between the Beam pipeline and the synthesis logic — break it and both engines must adapt. Treat it as a contract.

> **Synthesis spine ([ADR 0013](../../docs/adr/0013-distribution-estimator-spine.md))**: both engines are **LLM-as-distribution-estimator**, not per-row LLM generators. The LLM (B.1) or fitted library (B.2) models the distribution **once** in `setup()`; `generate_batch(n)` samples the bulk **vectorized** (NumPy baseline; cuDF optional). Guided-JSON LLM is used only for free-text columns. Per-row LLM generation does **not** scale to 1M+ rows and degrades fidelity. Full design + cost math + infra checklist: [`docs/superpowers/specs/2026-05-21-synthesis-engines-design.md`](../../docs/superpowers/specs/2026-05-21-synthesis-engines-design.md).

## Required surface

```python
class GenerationEngine(ABC):
    name: str

    def setup(self, model_client: ModelClient, ctx: GenerationContext) -> None: ...
    def generate_batch(self, n: int, cfg: GenerationConfig) -> Iterator[GeneratedRecord]: ...
    def teardown(self) -> None: ...
```

- `setup()` runs **once per Beam worker** (called from `DoFn.setup()`). Build the vector index (B.1) or fit the tabular library (B.2) here. Expensive — must NOT run per-record.
- `generate_batch(n)` returns *up to* n records. Returning fewer is allowed; the caller will request more if needed.
- `teardown()` releases worker resources (vector index, fitted model, GPU memory references). Called from `DoFn.teardown()`.

## The `ModelClient` Protocol

Never import `vllm` from inside an engine. Always go through:

```python
class ModelClient(Protocol):
    def generate_json(self, prompt: str, json_schema: dict, **kwargs) -> list[dict]: ...
```

Real implementation: `sdfb_beam.handlers.vllm_client.VLLMModelClient` — calls Beam's `RunInference` under the hood. Test implementation: `sdfb_beam.handlers.fake_client.FakeModelClient` — returns deterministic JSON from a fixture file. Engines never know which is in use.

REF: https://beam.apache.org/releases/pydoc/current/apache_beam.ml.inference.base.html

## Tests every engine must pass

In `packages/sdfb-tests/tests/unit/engines/`:

1. `test_setup_idempotent` — calling `setup()` twice doesn't double-fit / double-embed.
2. `test_batch_size_respected` — `generate_batch(n)` yields ≤ n records.
3. `test_schema_conformance` — yielded records pass `GeneratedRecord.model_validate(...)`.
4. `test_seed_reproducibility` — same `seed` + same `ctx` ⇒ same output stream.
5. `test_teardown_releases_state` — `teardown()` drops fitted state; `generate_batch` errors after.

These tests run on the laptop with `FakeModelClient`. No GPU, no GCP.

## When to write a new engine

- The new engine genuinely produces records by a different mechanism (e.g., diffusion-based tabular synthesis, GAN-only).
- A flag on an existing engine is sufficient ⇒ do NOT add a new engine, add the flag to `GenerationConfig.engine_specific`.

## Current implementation

| Concern | File | Status |
|---|---|---|
| ABC + types + registry | `packages/sdfb-core/src/sdfb_core/engines/base.py` | ✅ done |
| `ENGINE_REGISTRY` / `register_engine` / `get_engine` | `packages/sdfb-core/src/sdfb_core/engines/__init__.py` | ✅ done |
| Contract tests (5 + protocol sanity) | `packages/sdfb-tests/tests/unit/engines/test_abc_contract.py` | ✅ done |
| `MinimalEngine` test driver | `packages/sdfb-tests/src/sdfb_tests/fakes.py` | ✅ done |
| `B2LibraryEngine` (B.2) | `packages/sdfb-core/src/sdfb_core/engines/b2_library/` | ✅ M1 §6 (sdgx + NumPy spine) |
| `B1RagEngine` (B.1) | `packages/sdfb-core/src/sdfb_core/engines/b1_rag/` | ✅ M1 §7 (FAISS retrieval + distribution inference) |

ADR: [`docs/adr/0006-generation-engine-abc.md`](../../docs/adr/0006-generation-engine-abc.md).

## References

- ABC: `packages/sdfb-core/src/sdfb_core/engines/base.py`
- Reference impls: `packages/sdfb-core/src/sdfb_core/engines/b1_rag/`, `b2_library/`
- Beam RunInference: https://beam.apache.org/releases/pydoc/current/apache_beam.ml.inference.base.html
- Beam LLM guide: https://beam.apache.org/documentation/ml/large-language-modeling/
- Beam ML overview: https://beam.apache.org/documentation/ml/about-ml/