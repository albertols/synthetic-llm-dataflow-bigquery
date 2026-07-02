# M4 local smoke test — MLX backend

Fast iteration loop on the M4 against a **real** LLM, no Dataflow time, no GPU image. See [ADR 0010](adr/0010-m4-local-smoke-mlx.md) for why MLX (not vLLM) on Apple Silicon.

## TL;DR

```bash
# one-time per machine
uv sync --package sdfb-beam --extra mlx   # installs mlx + mlx-lm on Apple Silicon
mkdir -p models/gemma4/e4b-it/v1             # see MODEL_LAYOUT.md for the Kaggle download

# smoke test — uses your real _ddl.json
uv run python scripts/hello_synthetic_mlx.py \
    --ddl_path output/CDH_dataset/ddl_metadata_CDH_dataset_KW860T_RR.json \
    --reference_table CDH_dataset.KW860T_RR \
    --reference_limit 5 \
    --num_rows 10 \
    --model_path ./models/gemma4/e4b-it/v1/
```

Output: `output/<table>/hello_synthetic_mlx.jsonl` — one valid synthetic row per line.

## Three layers of local validation

The M4 supports a **3-layer ladder** of progressively-richer smoke tests; pick the one your iteration needs.

### L1 — pure-Python tests (no LLM)

```bash
uv run pytest -m "not gpu and not gcp" -q   # 80 tests, ~20s
```

Validates: contracts, codegen, engine ABC, DoFn behavior, BQ source mock, fake-client deterministic outputs. Catches structural regressions instantly. **Always run before pushing.**

### L2 — `hello_synthetic_mlx.py` (real LLM, no Beam)

```bash
uv run python scripts/hello_synthetic_mlx.py …
```

Validates: real LLM output is parseable as JSON, conforms to the derived Pydantic model, and survives Pandera column constraints. Catches prompt-engineering and schema-derivation bugs that L1 can't see.

Cost: ~30s model load + ~1–2s per row (on M4 24GB w/ Gemma 4 E4B).

### L3 — full DirectRunner + MLX through the pipeline

> **⚠️ NOT RUNNABLE YET — blocked on §6/§7.** `ENGINE_REGISTRY` is empty: the
> `b1_rag` / `b2_library` engines live in their worktrees (`worktrees/b1-rag`,
> `worktrees/b2-library`) and haven't merged to `main`. `get_engine("b1_rag")`
> raises `Unknown engine 'b1_rag'. Available: []` inside the generation DoFn's
> `setup()`. The command below is the **correct shape** for when an engine lands.

```bash
uv run python -m sdfb_beam.cli.run_pipeline \
    --runner DirectRunner \
    --client_type mlx \
    --ddl_uri output/CDH_dataset/ddl_metadata_CDH_dataset_KW860T_RR.json \
    --reference_table CDH_dataset.KW860T_RR \
    --landing_table <project>.<dataset>.sdfb_landing \
    --dlq_table <project>.<dataset>.sdfb_dlq \
    --num_rows 20 \
    --batch_size 4 \
    --run_id m4-smoke-001 \
    --engine b1_rag \
    --model_uri ./models/gemma4/e4b-it/v1/
```

Three things the doc's old command got wrong (now fixed above), worth understanding:
- `--reference_table` must be the **same table the DDL describes** (`CDH_dataset.KW860T_RR`), or the reference rows won't match the schema.
- `--landing_table` / `--dlq_table` are **real BigQuery tables** (`project.dataset.table`), not local paths — `run_pipeline.py` wires them into `WriteToBigQuery(create_disposition=CREATE_NEVER)`, so they must pre-exist. There is no local-file sink; even DirectRunner writes to BQ (needs creds).
- `--model_uri` is the **`-it`** variant (the base has no chat template).

Validates: end-to-end DAG including generation DoFn, ValidateRecordDoFn, PanderaValidateBatchDoFn, DLQ routing — but with a real LLM instead of FakeModelClient. Missing vs production: vLLM (CUDA-only), L4 GPU. Given it needs real BQ tables + an engine anyway, in practice the full DAG is validated on **Dataflow (§11)**, not DirectRunner-local.

## What this does NOT validate

- The GPU image — only CI builds + the Dataflow probe can prove that.
- Throughput / cost — single-process MLX is much slower than batched vLLM.
- Production fidelity — E4B (4.5B effective) is significantly smaller than the 26B-A4B MoE production target.
- BigQuery sinks — `WriteToBigQuery` requires real credentials + a real table.

## Setup

### 1. Install MLX extras

```bash
uv sync --package sdfb-beam --extra mlx
```

The `[mlx]` extra is defined on the `sdfb-beam` workspace **member**, not on the workspace root, so the `--package sdfb-beam` selector is required — a bare `uv sync --extra mlx` errors with "Extra `mlx` is not defined in the project's optional-dependencies table" because uv looks for extras on the root project only. (Same goes for `gpu` / `embedding` / `library`; the GPU Dockerfile uses `--all-packages` to scope all of them at once.)

The extra carries `sys_platform == 'darwin' and platform_machine == 'arm64'` markers, so it's a no-op on Linux/x86 (CI runners) and only installs on Apple Silicon.

### 2. Get Gemma 4 E4B weights

Follow [`MODEL_LAYOUT.md`](MODEL_LAYOUT.md) § "How to download" — pull from Kaggle, extract to `models/gemma4/e4b-it/v1/`. MLX reads the HF-layout safetensors directly.

### 3. Verify MLX can see the model

```bash
uv run python -c "
from mlx_lm import load
model, tok = load('./models/gemma4/e4b-it/v1/')
print('OK', type(model).__name__, type(tok).__name__)
"
```

If this errors with `safetensors index missing`, the download didn't include `model.safetensors.index.json` — re-download or pull only the single-shard variant.

## Gotchas

- **First run downloads tokenizer pieces** if `tokenizer.json` isn't bundled — usually it is for Gemma 4 from Kaggle.
- **MLX has no token-level guided JSON** (vLLM does). We rely on Gemma 4's native function-calling training + JSON-schema-in-prompt + post-validate. With thinking suppressed and a low temperature (see below), E4B reaches ~100% schema conformance on a 67-column table; the smoke script does **not** retry — a parse/validation failure just increments the counter.
- **Gemma 4 E4B-IT emits a chain-of-thought "thought" channel by default** (`<|channel>thought … <channel|>{json}`). It spends ~1–1.5k tokens enumerating the anchor before the JSON, which starves the token budget and truncates the object (every failure logs `[HIT max_tokens]`). Fixes, both now defaults: `MLXModelClient` passes `enable_thinking=False` to `apply_chat_template`, and `--max_tokens` defaults to 4096 with `--temperature` 0.2. Result: outputs drop to ~1.2k JSON-only tokens, ~58s/row, 10/10 valid. NB: the production vLLM/Dataflow path will hit the same thinking behavior and must suppress it too. The chat template lives in a standalone `chat_template.jinja` file (not inline in `tokenizer_config.json`), which is why `enable_thinking=False` is honored.
- **Memory**: E4B FP16 is ~9 GB. On M4 24 GB you have headroom; on M4 16 GB you may need Q4 weights.
- **No batching**: `MLXModelClient.generate_json(n=k)` calls `mlx_lm.generate` `k` times serially. For real benchmarks, you want vLLM on Dataflow.
- **Drift from production**: this loop tests the engine + validation chain, NOT the GPU image. After a successful smoke, you still need to push and run on Dataflow before declaring victory.
- **Gemma 4 E4B from Kaggle is the *multimodal* checkpoint** (`Gemma4ForConditionalGeneration`). Its safetensors carry redundant `k_proj` / `v_proj` / `k_norm` weights for the 18 shared-KV layers (config: `text_config.num_kv_shared_layers=18`); mlx-lm's Gemma 4 implementation omits them from the model graph. `MLXModelClient.setup()` calls `mlx_lm.utils.load_model(..., strict=False)` to accept those extras instead of letting `mlx_lm.load()` reject the checkpoint. The unused weights are never read at inference. This **only** affects M4 smoke — vLLM on Dataflow ingests the same checkpoint differently.

## When to use which layer

| Want to test | Use |
|---|---|
| Code change in `sdfb_core` (contracts, codegen, ABC) | L1 |
| Prompt engineering changes | L2 |
| End-to-end engine + validation chain | L3 |
| GPU image / Dataflow / vLLM / L4 | CI workflow 1 + probe |
| Production fidelity / throughput / cost | Dataflow probe with real model (M1 §11) |
