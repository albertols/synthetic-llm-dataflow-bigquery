---
name: model-handler
description: Recipe for the vLLM-backed `ModelHandler` and `ModelClient` Protocol — how `apache_beam.ml.inference.RunInference` ties into the synthesis engines without exposing vLLM internals to engine code. Load when wiring vLLM, adding a new model family, or debugging structured-output behavior.
---

# Skill — `ModelHandler` + `ModelClient`

The engines never import `vllm`. They call `ModelClient.generate_json(prompt, json_schema)`. We use **Beam's native `VLLMCompletionsModelHandler`** (shipped since `apache-beam` 2.60.0) — see [ADR 0011](../../docs/adr/0011-adopt-beam-vllm-model-handler.md) for why we don't write our own.

```
engine.generate_batch(n)
    └─> model_client.generate_json(prompt, schema)            # our thin wrapper
            └─> RunInference(VLLMCompletionsModelHandler)     # Beam-owned
                    └─> subprocess: vllm.entrypoints.openai.api_server
                            └─> openai.OpenAI(base_url=localhost:<port>/v1)
                                    └─> completions.create(..., extra_body={"guided_json": schema})
```

## Files

- `packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py` — `ModelClient` impl that owns a `VLLMCompletionsModelHandler` instance and the `extra_body` formatting for guided JSON.
- `packages/sdfb-beam/src/sdfb_beam/handlers/fake_client.py` — fixture-driven test impl (laptop).
- `packages/sdfb-beam/src/sdfb_beam/handlers/mlx_client.py` — Apple Silicon smoke-test impl (M4 only) — see [ADR 0010](../../docs/adr/0010-m4-local-smoke-mlx.md).

We do **not** write a `vllm_handler.py` — Beam's handler is used as-is.

## vLLM guided JSON via Beam's handler

Beam's handler talks to vLLM via vLLM's OpenAI-compatible server, so guided JSON goes through the OpenAI client's `extra_body` (a vLLM-specific extension the server understands).

```python
from apache_beam.ml.inference.vllm_inference import VLLMCompletionsModelHandler

handler = VLLMCompletionsModelHandler(
    model_name="/local-ssd/model",  # local path after GCS warm-pull, NOT an HF id
    vllm_server_kwargs={
        "quantization": "awq",
        "max-model-len": "8192",
        "gpu-memory-utilization": "0.85",
        "max-num-seqs": "16",
        "dtype": "auto",
    },
    max_batch_size=16,
)
```

Inside `VLLMModelClient.generate_json(prompt, schema)`:

```python
# Pseudocode — the client owns the OpenAI call shape; the handler owns the subprocess.
result = openai_client.completions.create(
    model=model_name,
    prompt=prompt,
    temperature=0.7,
    max_tokens=2048,
    extra_body={
        "guided_json": schema,                     # JSON-schema-guided decoding
        "guided_decoding_backend": "outlines",     # vLLM's outlines backend
    },
)
return json.loads(result.choices[0].text)
```

REFs:
- vLLM structured outputs: https://docs.vllm.ai/en/latest/usage/structured_outputs.html
- vLLM OpenAI-server extra parameters: https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html#extra-parameters-for-completions-api
- Beam handler API: https://beam.apache.org/releases/pydoc/2.60.0/apache_beam.ml.inference.vllm_inference.html

## Gemma 4 native function calling

Gemma 4 ships with JSON-schema function calling trained into pretraining (not patched). Format prompts using Gemma's tool-use template (see https://ai.google.dev/gemma/docs/core). With guided decoding ON TOP, schema conformance should exceed 99% — we measure this as a CI gate.

REF: https://ai.google.dev/gemma/docs/core

## Model load — from GCS, not HF Hub

Worker `setup()` does the GCS warm-pull via the **`google-cloud-storage` Python client** (not `gsutil` — see [ADR 0012](../../docs/adr/0012-enterprise-image-build.md): the CLI drags in a `packages.cloud.google.com` apt dependency the enterprise build can't reach), then constructs Beam's handler pointing at the local path:

```python
def setup(self):
    from google.cloud import storage
    bucket_name, prefix = _split_gs_uri(self.model_uri)
    client = storage.Client()                              # ADC on worker
    for blob in client.list_blobs(bucket_name, prefix=prefix):
        rel = blob.name[len(prefix):]
        if rel:
            dest = Path("/local-ssd/model") / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(dest)
    self.handler = VLLMCompletionsModelHandler(
        model_name="/local-ssd/model",
        vllm_server_kwargs=self.vllm_server_kwargs,  # from config/models.yml
        max_batch_size=16,
    )
    # Beam's handler.load_model() spawns the vLLM OpenAI server subprocess on
    # first inference; warmup is ~30–60 s on top of the GCS pull.
```

`model_name` is a local filesystem path — vLLM's server CLI accepts paths the same way it accepts HF identifiers, which is the contract that keeps us on the right side of [ADR 0001](../../docs/adr/0001-no-managed-gcp-services.md). `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` in the Dockerfile make any accidental Hub call fail fast.

## Constrained-decoding fallback chain

If vLLM guided decoding hits a schema edge case (deeply recursive types, complex `oneOf`/`anyOf`), the fallback chain is:

1. **vLLM guided JSON** (primary — fastest, schema-aware at the token level).
2. **`outlines`** regex/JSON-schema generator (secondary).
3. **Pydantic repair loop** (`max_retries=2`) — re-prompt with the validation error injected into the next prompt.

Don't reach for the fallback unless you've reproduced the failure with a fixture test.

## RunInference batching

`VLLMCompletionsModelHandler` accepts `min_batch_size` / `max_batch_size` / `max_batch_duration_secs` / `max_batch_weight` directly in its constructor — no separate `BatchElements` wrapper needed. Beam batches across bundles. Tune so each vLLM call sees ≥ 4 prompts — single-prompt calls leave the GPU mostly idle. Start with `max_batch_size=16` and measure GPU utilization in Cloud Monitoring (Dataflow GPU metrics).

REF: https://docs.cloud.google.com/dataflow/docs/gpu/gpu-metrics

## Current implementation

| Concern | File | Status |
|---|---|---|
| `ModelClient` Protocol | `packages/sdfb-core/src/sdfb_core/engines/base.py` | ✅ done |
| `FakeModelClient` (canned + echo modes) | `packages/sdfb-beam/src/sdfb_beam/handlers/fake_client.py` | ✅ done |
| `FakeModelClient` tests | `packages/sdfb-tests/tests/unit/handlers/test_fake_client.py` | ✅ done |
| `VLLMModelClient` (wraps Beam's `VLLMCompletionsModelHandler`) | `packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py` | 🔒 M1 §9 — see [ADR 0011](../../docs/adr/0011-adopt-beam-vllm-model-handler.md) |

Model registry (GCS URIs, vLLM args, license): `config/models.yml`. Layout / download procedure: [`docs/MODEL_LAYOUT.md`](../../docs/MODEL_LAYOUT.md).

## References

- Beam `vllm_inference` API (the handler we adopted): https://beam.apache.org/releases/pydoc/2.60.0/apache_beam.ml.inference.vllm_inference.html
- Beam example notebook (canonical recipe): https://github.com/apache/beam/blob/master/examples/notebooks/beam-ml/run_inference_vllm.ipynb
- Dataflow tutorial: https://cloud.google.com/dataflow/docs/notebooks/run_inference_vllm
- Beam Summit 2025 deck on serving with vLLM: https://beamsummit.org/slides/2025/how-beam-serves-models-with-vllm.pdf
- Performance baseline (Gemma 2B / T4): https://beam.apache.org/performance/vllmgemmabatchtesla/
- vLLM structured outputs: https://docs.vllm.ai/en/latest/usage/structured_outputs.html
- vLLM OpenAI-server extra parameters: https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html#extra-parameters-for-completions-api
- vLLM install: https://docs.vllm.ai/en/latest/getting_started/installation.html
- outlines: https://github.com/outlines-dev/outlines
- lm-format-enforcer: https://github.com/noamgat/lm-format-enforcer
- Beam RunInference: https://beam.apache.org/releases/pydoc/current/apache_beam.ml.inference.base.html
- Generative AI inference on Dataflow: https://docs.cloud.google.com/dataflow/docs/notebooks/run_inference_generative_ai
- Gemma 4: https://ai.google.dev/gemma/docs/core
- JSONSchemaBench (methodology): https://arxiv.org/pdf/2501.10868