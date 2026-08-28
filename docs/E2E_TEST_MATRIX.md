# E2E integration test matrix — M4 / Dataflow runs

Companion to [`docs/RUN_PLAYBOOK.md`](https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/vllm-setup-worker/docs/RUN_PLAYBOOK.md) §2. All runs are triggered via the Composer DAG (`composer/synthetic_beam_bigquery.py`); model selection is the `SDFB_MODEL_URI` Composer Variable pointed at the matching `gcs_uri` in [`config/models.yml`](https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/vllm-setup-worker/config/models.yml).

**Defaults (only overrides are listed per run):** `num_rows=1000`, `batch_size=16`, `similarity=0.5`, `seed=""` (derived per run_id/batch), `vllm_dtype=auto`, `client_type=vllm`, `identity_cols=<ID_COL>`, `pk_cols=<PK_COL>,<PK_COL_2>` (real columns of the target table), landing table truncated between runs (or fresh `run_id` verified in `validation_runs`).

**After every run:** `scripts/e2e/e2e_gcp_probe.py` → `scripts/e2e/e2e_validation_analysis.py` → `scripts/e2e/e2e_bundle_export.py`, then the report prompt. Check the **Dataflow job state** (`gcloud dataflow jobs describe <job_id>`), not Airflow green (DAG launches with `wait_until_finished=False`).

---

## Tier 0 — CPU smoke (no GPU quota burned)

| ID | Engine | Model | client_type / GPU | Overrides | Purpose |
|----|--------|-------|-------------------|-----------|---------|
| S0 | b1_rag | (none — fake client) | `client_type=fake` (e2-standard-8, no accelerator) | — | Full Dataflow→BigQuery write path, `validation_runs` row, DLQ schema, Flex Template params (`identity_cols`, `pk_cols`, `seed` all reach the job). Run FIRST, before any GPU run. |

**Pass:** job `JOB_STATE_DONE`; landing table has 1000 rows; `validation_runs.valid_count=1000`; `pk.duplicate` ~0.

---

## Tier 1 — Core playbook matrix (mandatory, RUN_PLAYBOOK §2)

| ID | Engine | Model (registry key) | GPU | Overrides | Purpose |
|----|--------|----------------------|-----|-----------|---------|
| R1' | b1_rag | `qwen3_4b_instruct_2507` | t4 | `vllm_dtype=float16` | Real vLLM lifecycle on cheap GPU: `vllm_ready` present, zero `freetext_llm_fallback`, strict mode crashes instead of falling back. |
| R2' | b1_rag | `qwen3_4b_instruct_2507` | t4 | `vllm_dtype=float16` (exact repeat of R1') | Anti-replay: output **differs** from R1' (salted run_id → fresh derived seeds). |
| R3' | b2_library | `qwen3_4b_instruct_2507` | t4 | `vllm_dtype=float16` | B.2 path (sdgx fit + LLM freetext patch) + `pk.duplicate` rule live. |
| R4' | b1_rag | `gemma4_e4b` (it) | l4 | — (`vllm_dtype=auto` → bf16) | Fidelity profile: bf16 on L4; the Turing guard must **NOT** fire. |

**Pass criteria (all four):** the 8 checks in RUN_PLAYBOOK §2 — milestones `model_client_setup_start/done`, `model_pull_*`, `vllm_spawn`, `vllm_ready` present; zero `freetext_llm_fallback`; `copy_ratio < 0.3` on non-constant STRING columns with `source_distinct > 100`; unique salted `run_id`; `pk.duplicate` ≈ 0; `b1_embed_done rows=10000`; Dataflow job state checked directly; `valid_count == num_rows`.

---

## Tier 2 — Cross-model / cross-engine coverage

| ID | Engine | Model (registry key) | GPU | Overrides | Purpose |
|----|--------|----------------------|-----|-----------|---------|
| R5' | b1_rag | `qwen2_5_7b_instruct` | **l4 only** | — (`vllm_dtype=auto` → native bf16) | Cross-family 7B on L4. Qwen2.5 is **VRAM-limited to L4** (14.2 GB weights > T4's 13.6 GB budget — never pair with t4). Also exercises the no-`special_tokens_map.json` tokenizer path and the renamed `qwen2.5` local dir staging. |
| R6' | b2_library | `qwen3_4b_instruct_2507` | l4 | — (`vllm_dtype=auto` → bf16) | Same engine/model as R3' but on Ampere+ with **auto dtype**: proves the bf16 path and the dtype guard's compute-capability branch (R3' only proves the fp16 downcast). |
| R7' | b2_library | `gemma4_e4b` (it) | l4 | — | Engine × fidelity-model cross: B.2 freetext patching with Gemma's chat template / guided JSON (R4' only covers B.1 with Gemma). |
| R8' *(optional)* | b1_rag | `qwen2_5_7b_instruct` | l4 | `similarity=0.5`, compare vs R4'/R5' reports | Cross-model fidelity comparison input for the evaluation-framework spec (tiered metrics). Only if R5' passes and L4 capacity allows. |

---

## Tier 3 — Non-default inputs (run on the cheapest passing config: b1_rag + qwen3-4b + t4 + `vllm_dtype=float16`, unless stated)

| ID | Overrides | Purpose / what to assert |
|----|-----------|--------------------------|
| P1 | `similarity=0.9` | Memorization stress at high mimic: `copy_ratio` must **still** be < 0.3; identity/uniqueness gates hold; DLQ doesn't explode. |
| P2 | `similarity=0.1` | Low anchoring: Pandera pass-rate stays healthy (schema validity without strong exemplar signal); `valid_count == num_rows`. |
| P3 | `batch_size=4` | Fine-grained `engine_failure` weighting (lost-batch-size gate) + more vLLM roundtrips; assert per-batch milestones count = ceil(1000/4). |
| P4 | `batch_size=64` | KV/context pressure at `max_model_len=8192`; guided-JSON stability at batch scale; watch for vLLM OOM / truncation — must fail loudly if it can't fit, not degrade. |
| P5 | `num_rows=10000` | Scale: `valid_count=10000`, FILE_LOADS batching, DLQ ratio stable vs 1000-row runs, wall-clock for capacity planning. |
| P6 | `seed=42`, run **twice** | Explicit-seed reproducibility: non-identity columns identical across both runs; identity columns still unique per row; `run_id` still differs (salt is independent of seed). |
| P7 | `identity_cols=""` (disabled), `pk_cols` set | PK gate tested independently of identity synthesis: any collisions must land in DLQ as `pk.duplicate` (BLOCKER) — proves the gate isn't masked by identity synthesis. |
| P8 | `pk_cols=""` | Rule-idle path: **no** `pk.duplicate` entries in `dlq_by_rule`; run otherwise green. |

---

## Tier 4 — Negative / guard tests (expected: loud, fast failure — a "pass" here is a FAILED Dataflow job with the right error)

| ID | Config | Expected outcome |
|----|--------|------------------|
| N1 | `gemma4_e4b` + `gpu=t4` | `ModelGpuIncompatibleError` at `VLLMModelClient.setup()` **before** the vLLM subprocess spawns; Dataflow job FAILS; zero rows written; no 22-minute stall. |
| N2 | `gemma4_e4b` + `gpu=l4` + `vllm_dtype=float16` | Worker refuses fp16 for gemma-family checkpoints (silent-empty-output guard); job fails with the explicit refusal message. |
| N3 | `qwen2_5_7b_instruct` + `gpu=t4` (+ `vllm_dtype=float16`) | VRAM failure (weights exceed the 13.6 GB T4 budget) — must surface as a diagnosable startup failure in the probe output, not a hang. |
| N4 | `SDFB_MODEL_URI` → nonexistent GCS path | `model_pull_*` milestone fails → strict mode kills the job; **zero** fallback rows in the landing table; probe extracts the `error=` payload. |

---

## Suggested execution order

1. **S0** (free, validates plumbing + new Flex Template params).
2. **R1' → R2' → R3'** on T4 (cheap, holds no L4 capacity).
3. **N1, N4** on T4 (cheap negative tests while quota is warm).
4. **R4' → R5' → R6' → R7'** on L4 (per the L4 stockout strategy in RUN_PLAYBOOK §4 — batch them into one capacity window).
5. **P1–P8** on the T4 config once Tier 1 is green (P4/P5 last — they're the slow ones).
6. **N2, N3** opportunistically alongside the L4/T4 windows.
7. Report per run via the [report prompt](https://github.com/albertols/synthetic-llm-dataflow-bigquery/blob/vllm-setup-worker/.github/prompts/end_to_end_validation_report_generation.prompt.md); attach bundles from `e2e_bundle_export.py`.

## Minimal must-do set (if capacity is tight)

S0 · R1' · R2' · R3' · R4' · R5' (qwen2.5 × b1_rag) · R6' (qwen3 × b2_library, auto dtype) · N1 · P6 (seed repro) · P7 (pk gate isolated).
