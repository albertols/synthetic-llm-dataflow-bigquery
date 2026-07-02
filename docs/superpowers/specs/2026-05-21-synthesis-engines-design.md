# Design — B.1 RAG + B.2 library synthesis engines (M1 §7 / §6)

- **Date**: 2026-05-21
- **Status**: approved (design); implementation pending
- **Scope**: the two `GenerationEngine` implementations behind the shared ABC. Single-table only (M1).
- **Decision of record**: [ADR 0013](../../adr/0013-distribution-estimator-spine.md) — the LLM-as-distribution-estimator spine.

## 1. The critical decision — LLM as distribution-estimator, not row-generator

Generating ~1M synthetic rows by prompting the LLM **per row** does not scale and degrades fidelity:

- **Cost/throughput**: LLM tabular generators (GReaT, REaLTabFormer) emit rows token-by-token — reported ~9,500× slower than statistical sampling ([arXiv 2507.19334](https://arxiv.org/html/2507.19334v1)). 1M rows × ~200 output tokens ≈ 200M tokens; at ~40–120 tok/s on one L4 that is **hundreds of GPU-hours** (days). This is exactly the cost the project must avoid.
- **Fidelity**: cell-by-cell LLM generation samples by *linguistic token frequency*, not data frequency, collapsing distributions toward uniform ([arXiv 2505.02659](https://arxiv.org/html/2505.02659v2)). That would violate the hard requirement: *synthetic data must stay within observed probabilistic ranges* (e.g. a column that is constant across the reference must stay constant).

**Adopted approach (FASTGEN family — [arXiv 2507.15839](https://arxiv.org/pdf/2507.15839)): the LLM runs O(1) times, not O(N).** The LLM infers per-column distributions / classifies field types / handles hard columns **once** over the reference data. The bulk N rows are then sampled **vectorized in NumPy (CPU, seconds)**. LLM decoding (vLLM guided JSON) is reserved for genuinely generative free-text columns — and even there, generate a bounded unique pool then sample-with-replacement.

Result: **1M rows in minutes on a single L4 + CPU** instead of days, and fidelity is preserved *by construction* (constants copied, numeric ranges clipped to observed bounds, categorical frequencies sampled from the empirical table, simple joints from conditional distributions).

## 2. The shared engine spine

Both engines implement `sdfb_core.engines.base.GenerationEngine` (`setup` / `generate_batch` / `teardown`) and consume the `ModelClient` Protocol. Both share:

```
setup(model_client, ctx):            # once per Beam worker
    profile reference_rows → per-column type (constant | numeric | categorical | free_text)
    build the "distribution model" (engine-specific, see §3/§4)        # O(1) LLM use here
generate_batch(n, cfg):              # many times; cheap
    vectorized-sample n rows from the distribution model               # CPU, no GPU
    patch free_text columns from a bounded LLM-generated pool          # GPU only here
    yield GeneratedRecord            # Pydantic; Pandera + DLQ downstream in the DoFn
teardown(): release index / fitted model
```

- **LLM cost is O(1) in N**; **bulk sampling is O(N), vectorized, ~free**. The single GPU/free-text path is the only LLM cost.
- **Fidelity primitives**: constant detection (`nunique()==1` → literal), numeric clip-to-observed-range, categorical empirical-frequency sampling, and **dependency-aware** conditional sampling over correlated column groups (column-ordering / dependency graph — cf. NeMo Data Designer, LLM-TabLogic). Post-hoc enforcement is defense-in-depth on top of the Mode-A Pandera contract. **Kept local per engine** during parallel development; consolidate to a shared `engines/_fidelity.py` post-merge if duplication warrants (avoids a shared-file merge conflict across the two worktrees).
- **Sampling-backend seam**: bulk sampling runs behind a backend interface — **NumPy** on CPU (laptop/DirectRunner default, deterministic, used by the contract tests) or **cuDF/CuPy on the worker's L4 GPU** (Apache-2.0; the GPU is idle during the O(1) LLM phase, so this reclaims sunk cost — the path to **billions** of rows). Caveats: VRAM contention with vLLM (share the model via `MultiProcessShared`, size frames to remaining VRAM); CuPy RNG ≠ NumPy RNG bit-for-bit, so reproducibility tests pin the NumPy backend. cuDF is an M1-optional optimization behind the seam; NumPy is the baseline.
- **Determinism**: every sampling step is seeded (`cfg.seed`); FAISS retrieval is exact + single-threaded (B.1); library sampling is seeded (B.2). Satisfies `test_seed_reproducibility` (NumPy backend).

## 3. B.1 RAG engine (§7, `worktrees/b1-rag`)

Distribution model = **retrieval-conditioned + LLM-inferred**.

1. **Embed** ≤10k `reference_rows` behind an **`Embedder` seam** (real = `bge-small-en-v1.5`, 384-dim, CPU; tests inject a deterministic fake → contract tests run on the laptop with `HF_HUB_OFFLINE=1`, no download). Row→text serialization uses GReaT-style `"col is value, col is value, …"` ([arXiv 2210.06280](https://arxiv.org/abs/2210.06280)) with deterministic column order.
2. **Index**: FAISS `IndexFlatIP` (exact, no training, normalized vectors = cosine). Flat beats IVF below ~50k vectors and avoids IVF nondeterminism.
3. **Retrieve**: top-k = 5–10 neighbors for the conditioning context.
4. **Infer (LLM, once per column-group)**: classify field type; infer marginals + conditional joints from the retrieved exemplars (FASTGEN-style). Constants resolved in code, never sent to the LLM.
5. **Sample (vectorized)**: bulk rows from the inferred (conditional) distributions.
6. **Free-text (LLM, guided JSON)**: bounded unique pool, exemplar-conditioned, sampled with replacement.
7. **`similarity` mapping** (per the approved Q1 choice): retrieval-neighborhood tightness **+** sampling variance. similarity→1 = nearest exemplars + tight (mimic); similarity→0 = broader neighborhood + wider sampling (diverge). Always within observed support.

**Acceptance** (in addition to the 5 ABC tests): setup <60s on 10k rows (CPU embedder); deterministic top-k for a fixed query/seed; generated records show exemplar evidence above baseline; no HF Hub calls at runtime.

## 4. B.2 library engine (§6, `worktrees/b2-library`)

Distribution model = **fitted statistical library = `sdgx`** (hitsz-ids, Apache-2.0 — license-clean, already pinned in `[library]`). SDV's GaussianCopula is technically stronger (constraints/determinism) but is BSL-1.1; recorded as the **upgrade path pending corporate license sign-off** in `SPIKE_LIBRARY_CHOICE.md`, not adopted now.

1. **Fit** `sdgx` (CTGAN-family) on `reference_rows` in `setup()`; fitted model pickles across the worker boundary; fit once per worker.
2. **Sample (vectorized)** n rows; seeded for reproducibility.
3. **Constraint enforcement**: sdgx's metadata API is thinner than SDV's, so we add our own post-hoc clamps (constant, range, enum) via the shared fidelity helper + the Mode-A Pandera contract.
4. **Free-text hook**: per-column LLM patch (via `ModelClient`) for FREE_TEXT / JSON / very-high-cardinality string columns; respects `cfg.similarity`.
5. **`similarity` mapping**: library sampling temperature / perturbation.

**Acceptance**: 5 ABC tests; `SPIKE_LIBRARY_CHOICE.md` (sdgx selected, SDV deferred, fit-time/RAM/eyeball noted); fit time on 10k rows documented (>5 min triggers review); free-text hook exercised by ≥1 fixture column; `config/models.yml` gets a `b2_library` section with the pinned lib+version.

## 5. Cost / performance / Dataflow + BigQuery efficiency

- **GPU only on the free-text path.** Bulk sampling is CPU inside the DoFn. The vLLM guided-JSON call (XGrammar backend — near-free structured-decoding overhead, [vLLM blog](https://blog.vllm.ai/2025/01/14/struct-decode-intro.html)) is throughput-bound (~40–120 tok/s on L4), so it must never see the bulk — only the bounded free-text pool.
- **Batching**: `Reshuffle` + `BatchElements` before the LLM step to fill the GPU; `max_batch_size` on the handler (ADR 0011).
- **Writes**: `WriteToBigQuery` **FILE_LOADS** (batch-shaped, cheap), `WRITE_APPEND`, `CREATE_NEVER` — already wired in `run_pipeline.py`. Invalid rows → partitioned DLQ table.
- **Billions of rows — GPU-accelerated sampling**: run the O(N) sampling on the worker's L4 via **cuDF/CuPy** (Apache-2.0) behind the sampling-backend seam (§2). The GPU is idle during the O(1) LLM phase, so this is near-free throughput on hardware already provisioned — the scalability path beyond ~1M. NumPy stays the CPU/laptop baseline.
- **M2 optimization (noted, not built)**: split CPU workers (bulk sampling) from GPU workers (free-text) so GPUs aren't idle during sampling.

### Evaluated and rejected (NVlabs / NVIDIA review, 2026-05-21)
- **TensorRT-LLM / NIM** for the free-text LLM path: no clear L4 win, and since the LLM is **O(1)** here, inference is not the bottleneck — sampling is. NIM additionally needs a paid NVIDIA AI Enterprise license. **Stay on vLLM** (Beam's `VLLMCompletionsModelHandler`, ADR 0011).
- **NVlabs research repos** (e.g. `f-RAG`): NVIDIA-Source-Code-License (non-commercial) → unusable in a corporate/production context; also vision/molecular, not tabular.
- **NVIDIA RAG blueprints / Nemotron retrievers**: heavyweight document-RAG + paid NIM serving; our retrieval is lightweight exemplar lookup → keep FAISS + `bge-small` (permissive, simpler).
- **NeMo Data Designer** (Apache-2.0): not run inside Beam (it's an orchestrator), but its **dependency-aware column-ordering** pattern informs our conditional-sampling fidelity primitive (§2).

## 6. Infrastructure checklist (to run E2E on Dataflow)

| Resource | Detail | Action |
|---|---|---|
| Reference table | source `SELECT` (e.g. `CDH_dataset.KW860T_RR`) | exists |
| Landing table | `<proj>.<ds>.sdfb_landing`, schema = source DDL, `CREATE_NEVER` | **create before run** |
| DLQ table | `<proj>.<ds>.sdfb_dlq`, partitioned, `CREATE_NEVER` | **create before run** |
| `synthetic_data_quality.validation_runs` | run metadata + `reference_digest` provenance (§12) | **create + wire** |
| `config/models.yml` | add `b2_library` (sdgx + version); embedder `bge-small-en-v1.5` already listed | update |
| `config/thresholds.yml` | Mode-A gates — wiring not implemented (§12) | wire |
| Model upload | `gs://{bucket}/synthetic/models/gemma4/e4b-it/…`, `26b-a4b-awq/…`, **+ `embedders/bge-small-en-v1.5/…` for B.1** | upload |
| DDL | `scripts/extract_ddl.py` → `_ddl.json` → `gs://{bucket}/synthetic/ddls/`; **DDL fallback generator** when none exists | build fallback |
| Composer Variables | `SDFB_MODEL_URI`, `SDFB_DDL_URI`, `SDFB_LANDING_TABLE`, `SDFB_DLQ_TABLE`, staging/templates buckets, subnet, SA, network tags | set manually (no CI composer perms) |

## 7. Out of scope (M2+)
SDV/BSL upgrade; multi-table/FK retrieval (REaLTabFormer); online vector stores; CPU/GPU worker split; Mode B fidelity (SDMetrics/Evidently); per-engine head-to-head evaluation.

## 8. References (literature)
- FASTGEN — cost-efficient LLM tabular (distribution estimation): <https://arxiv.org/pdf/2507.15839>
- Statistically-accurate tabular gen with LLMs (token bias, probability-driven prompting): <https://arxiv.org/html/2505.02659v2>
- GReaT (NL row serialization): <https://arxiv.org/abs/2210.06280>
- Ultra-fast tabular gen / GReaT ~9500× slower: <https://arxiv.org/html/2507.19334v1>
- LLM-TabLogic (inter-column logical constraints): <https://arxiv.org/abs/2503.02161>
- Survey of synthetic tabular data generation (2025): <https://arxiv.org/html/2504.16506v2>
- SDV / GaussianCopula + constraints: <https://github.com/sdv-dev/SDV> · <https://datacebo.com/blog/eng-sdv-constraints/>
- sdgx (hitsz-ids, Apache-2.0): <https://github.com/hitsz-ids/synthetic-data-generator>
- XGrammar / vLLM structured decoding: <https://blog.vllm.ai/2025/01/14/struct-decode-intro.html>
- cuDF / RAPIDS GPU-accelerated sampling (Apache-2.0): <https://docs.rapids.ai/api/cudf/stable/> · <https://developer.nvidia.com/blog/processing-one-billion-rows-of-data-with-rapids-cudf-pandas-accelerator-mode/>
- NeMo Data Designer (dependency-aware synthetic structured data, Apache-2.0): <https://github.com/NVIDIA-NeMo/DataDesigner>
- Internal: [ADR 0013](../../adr/0013-distribution-estimator-spine.md) (this spine), [ADR 0011](../../adr/0011-adopt-beam-vllm-model-handler.md) (vLLM handler), [ADR 0006](../../adr/0006-generation-engine-abc.md) (engine ABC), `.claude/skills/reference-data.md`, `.claude/skills/model-handler.md`.
