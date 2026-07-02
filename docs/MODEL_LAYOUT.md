# Model storage layout

Where model weights live, in what format, for which runner.

## TL;DR

| Runner | Model source | Inference backend | Weight format |
|---|---|---|---|
| **DirectRunner (this laptop)** | `FakeModelClient` (no real model) | n/a — test fake | n/a |
| **DirectRunner (M4 — stretch / optional)** | `./models/{family}/{model}/{version}/` | MLX, llama.cpp, or vLLM-CPU | safetensors (or GGUF) |
| **Dataflow (L4 GPU workers)** | `gs://{bucket}/synthetic/models/{family}/{model}/{version}/` | vLLM with CUDA | safetensors + AWQ Q4 |

**For M1 §8 specifically** (the DAG end-to-end on DirectRunner), no real model is needed — `FakeModelClient` substitutes. Everything below is for §9–§11 when real LLMs come online.

---

## Canonical GCS layout (Dataflow workers)

```
gs://{bucket}/synthetic/models/
├── gemma4/
│   ├── e4b-it/v1/                  # Gemma 4 E4B (4.5B effective) — dev / cost-floor
│   │   ├── config.json
│   │   ├── tokenizer.json
│   │   ├── tokenizer_config.json
│   │   ├── special_tokens_map.json
│   │   ├── generation_config.json
│   │   ├── model-00001-of-00002.safetensors
│   │   ├── model-00002-of-00002.safetensors
│   │   └── model.safetensors.index.json
│   └── 26b-a4b-awq/v1/          # Gemma 4 26B A4B MoE, Q4-AWQ — primary production
│       ├── config.json
│       ├── tokenizer.json
│       ├── tokenizer_config.json
│       ├── special_tokens_map.json
│       ├── generation_config.json
│       ├── quant_config.json
│       └── model.safetensors    # AWQ-quantized; may be sharded depending on packer
├── qwen2.5/7b-it/v1/            # Optional cross-family check (Apache-2.0)
│   └── … (same layout)
└── embedders/
    └── bge-small-en-v1.5/v1/    # For B.1 RAG (M1 §7)
        ├── config.json
        ├── tokenizer.json
        └── model.safetensors
```

Rules:
- The addressable unit is the **`{family}/{model}/{version}/` triple**. The pipeline flag is `--model_uri=gs://{bucket}/synthetic/models/gemma4/e4b-it/v1/`.
- Version directories are **immutable**. New version = new directory. Never overwrite `v1/` — bump to `v2/`.
- Worker `setup()` runs **one** `gsutil -m cp -r {model_uri}/ /local-ssd/model/` per worker lifetime; vLLM loads from the local-SSD path.

## Local layout on the M4

Mirror the GCS structure under the repo's `./models/` (gitignored — see `.gitignore`):

```
~/IdeaProjects/synthetic-dataflow-bigquery/
└── models/                       # gitignored
    └── gemma4/
        ├── e4b-it/v1/               # ~9 GB at FP16 — fits comfortably on M4 24 GB
        └── 26b-a4b-awq/v1/       # ~13 GB at Q4-AWQ — tight but fits
```

The pipeline flag accepts both `gs://…` and local paths, so the same code path works for laptop/M4-local and Dataflow.

## Apple Silicon (M4) caveat

vLLM targets CUDA. On the M4's unified-memory GPU it will fall back to CPU (slow) — for real local inference on M4 you'd typically use one of:

- **MLX / mlx-lm** — Apple's framework, fast on M-series. Reads HuggingFace-layout safetensors directly.
- **llama.cpp / GGUF** — needs converted GGUF weights (`convert-hf-to-gguf.py`).
- **Ollama** — wraps llama.cpp.

A future `MLXModelClient` (out of M1 scope) would slot into the `ModelClient` Protocol exactly the same as `VLLMModelClient`. The Beam DAG doesn't care.

For M1 specifically, **on M4 you have two practical paths**:
1. `FakeModelClient` + DirectRunner — exercise the pipeline locally with no GPU.
2. `VLLMModelClient` on Dataflow with L4 workers — the production path (M1 §11).

A real-model DirectRunner run on M4 with MLX is a nice-to-have for §6 (B.2 spike) and §7 (B.1 spike), not a blocker.

## How to download (canonical procedure)

The non-HuggingFace source for Gemma is **Kaggle** (Google-hosted, license-clean).

1. On the M4, install the Kaggle CLI and authenticate:
   ```bash
   pip install kaggle
   # Drop ~/.kaggle/kaggle.json with your API token (Kaggle → Settings → API)
   chmod 600 ~/.kaggle/kaggle.json
   ```
2. Accept the Gemma model license once on the Kaggle model page (https://kaggle.com/models/google/gemma-4).
3. Download:
   ```bash
   mkdir -p models/gemma4/e4b-it/v1
   kaggle models instances versions download google/gemma-4/transformers/e4b/1 \
     -p models/gemma4/e4b-it/v1
   # Kaggle delivers as a zip — extract in place
   unzip models/gemma4/e4b-it/v1/*.zip -d models/gemma4/e4b-it/v1/
   rm models/gemma4/e4b-it/v1/*.zip
   ```
4. Verify the file-level checklist below.
5. Upload to GCS for Dataflow workers:
   ```bash
   gsutil -m cp -r models/gemma4/e4b-it/v1/ gs://{bucket}/synthetic/models/gemma4/e4b-it/v1/
   ```

For Qwen 2.5 (not on Kaggle), use the official Qwen GitHub release tarball: https://github.com/QwenLM/Qwen2.5/releases (download → extract → same layout).

## File-level checklist

Every `{family}/{model}/{version}/` directory MUST contain, at minimum:

- `config.json` — model architecture + hyperparameters
- `tokenizer.json` (or `tokenizer.model` for SentencePiece-based tokenizers)
- `tokenizer_config.json` — tokenizer wrapper config
- `special_tokens_map.json` — BOS / EOS / PAD token ids
- One or more `*.safetensors` files — model weights
- `model.safetensors.index.json` — required when weights are sharded across multiple `*.safetensors` files

Recommended:
- `generation_config.json` — default sampling params (vLLM picks these up)
- `chat_template.jinja` or `chat_template.json` — for instruction-tuned chat models

For AWQ-quantized variants, additionally:
- `quant_config.json` — AWQ quantization parameters (`zero_point`, `q_group_size`, `w_bit`, etc.)

For GGUF (llama.cpp / Ollama path):
- Single `*.gguf` file is sufficient. No accompanying `config.json` — GGUF is self-describing. Tokenizer is embedded.

## Runtime load — Dataflow / vLLM

Per [ADR 0011](adr/0011-adopt-beam-vllm-model-handler.md), the serving path uses Beam's
`apache_beam.ml.inference.vllm_inference.VLLMCompletionsModelHandler`. Inside
`sdfb_beam/handlers/vllm_client.py` (M1 §9):

```python
def setup(self):
    # One-time per worker — copy from GCS to local SSD via the Python client
    # (NOT gsutil — the CLI would force a packages.cloud.google.com apt
    # install the enterprise build can't reach; see ADR 0012).
    from google.cloud import storage
    bucket_name, prefix = _split_gs_uri(self.model_uri)
    client = storage.Client()                          # ADC on worker
    for blob in client.list_blobs(bucket_name, prefix=prefix):
        rel = blob.name[len(prefix):]
        if rel:
            dest = Path("/local-ssd/model") / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(dest)
    # Beam's handler spawns `python -m vllm.entrypoints.openai.api_server`
    # under the hood. Pass server flags via vllm_server_kwargs.
    self.handler = VLLMCompletionsModelHandler(
        model_name="/local-ssd/model",
        vllm_server_kwargs={
            "quantization": "awq",
            "max-model-len": "8192",
            "gpu-memory-utilization": "0.85",
        },
        max_batch_size=16,
    )
```

`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set in `docker/Dockerfile` (M1 §10) so any accidental Hub call fails loudly. The model directory must be self-contained.

### §11 vLLM acceptance — what the Dataflow probe must confirm

GPU validation happens via the §11 Dataflow probe (`scripts/probe_gpu_dataflow.sh`) once the image is built — there's no separate laptop test (vLLM is CUDA-only). The probe (1-row job) must confirm the vLLM serving path:

- **Server loads the model** — `Gemma4ForConditionalGeneration` accepted (needs vLLM ≥ 0.21; see version note below).
- **Thinking channel suppressed** — pass `chat_template_kwargs={"enable_thinking": False}` via the **chat** endpoint (not raw completions); otherwise the model spends the token budget on chain-of-thought and truncates the JSON (see the Gemma 4 project memory).
- **Guided JSON conforms** — `extra_body={"guided_json": schema}` yields schema-valid output ([ADR 0011](adr/0011-adopt-beam-vllm-model-handler.md)).

> **Version requirement (resolved 2026-05-21):** Gemma 4 (`model_type=gemma4`) needs **transformers ≥ 5.5.0**, which vLLM only adopted in **v0.20.0** (v0.21.0 deprecates transformers v4). Older vLLM fails at config parse (`rope_scaling should have a 'rope_type' key`). The `[gpu]` extra pins `vllm>=0.21.0` and `[embedding]` `transformers>=5.5.0`. vLLM has full Gemma 4 support (MoE, multimodal, reasoning, tool-use) since v0.20 — no fallback model needed. Before the probe: `uv lock`, verify the JFrog mirror has these versions, and confirm the CUDA base (12.2.2) is recent enough for vLLM 0.21's torch.

## Runtime load — local M4 (stretch goal, MLX example)

```python
# An MLXModelClient would look roughly like this — not in M1 scope.
def setup(self):
    from mlx_lm import load
    self.model, self.tokenizer = load(str(self.local_model_path))
```

## What NOT to do

- ❌ Do not commit model weights — they're in `.gitignore` (`models/`, `*.safetensors`, `*.gguf`, `*.bin`).
- ❌ Do not call `from_pretrained("org/repo")` against the Hub at runtime. `HF_HUB_OFFLINE=1` in production is there to make this fail loudly.
- ❌ Do not store whole-archive model blobs in BigQuery / GCR / Artifact Registry. GCS-as-a-flat-directory-prefix is the contract; vLLM expects a directory layout.
- ❌ Do not mix multiple model versions in one directory. New version = new `vN/` subdirectory.
- ❌ Do not put models inside `packages/` — they're operational artifacts, not source code. `./models/` at the repo root is the convention.
