---
name: b1-rag-engineer
description: Subagent that owns the B.1 RAG engine in `worktrees/b1-rag`. Embeds reference rows → FAISS index → retrieves top-k exemplars so the LLM infers per-column distributions ONCE (not per row, per ADR 0013), then samples the bulk vectorized; guided-JSON LLM only for free-text columns. Invoke when starting M1 §7 or iterating on B.1.
---

# Subagent — B.1 RAG engineer

## Scope

**Authoritative design**: [ADR 0013](../../docs/adr/0013-distribution-estimator-spine.md) + [`docs/superpowers/specs/2026-05-21-synthesis-engines-design.md`](../../docs/superpowers/specs/2026-05-21-synthesis-engines-design.md) §2–§3. The spine is **LLM-as-distribution-estimator (O(1)), NOT per-row LLM generation** — read both before coding.

- Own `packages/sdfb-core/src/sdfb_core/engines/b1_rag/` in the `worktrees/b1-rag` worktree.
- Implement `B1RagEngine(GenerationEngine)` satisfying the ABC contract.
- Embedder behind a **seam**: real = local-only `bge-small-en-v1.5` (CPU, weights from GCS); tests inject a **deterministic fake** so the 5 contract tests run on the laptop with `HF_HUB_OFFLINE=1` and no download. GReaT-style row→text serialization.
- Build a FAISS `IndexFlatIP` from `reference_rows` in `setup()`; retrieve top-k 5–10.
- LLM infers per-column-group distributions + field types **ONCE** from retrieved exemplars; the bulk N rows are **sampled vectorized** (NumPy baseline behind a sampling-backend seam; cuDF optional). Guided-JSON LLM is used **only** for free-text columns (bounded unique pool, sample-with-replacement).
- Fidelity primitives (constant→literal, numeric clip-to-range, categorical empirical frequency, dependency-aware conditional sampling) — keep local to `b1_rag/` for now.
- `similarity` = retrieval-neighborhood tightness + sampling variance (similarity→1 mimic, →0 diverge, always within observed support).

## NOT in scope

- The Beam DAG (delegate to `beam-pipeline-author`).
- The `ModelHandler` / vLLM integration (delegate to `gpu-image-builder`).
- Mode B validation, multi-table FK retrieval, online vector stores.
- Comparison work against B.2 (separate evaluation, not in this worktree).

## What to load before working

- `.claude/skills/engine-contract.md`
- `.claude/skills/model-handler.md`
- Beam RAG primitives:
  - https://beam.apache.org/releases/pydoc/current/apache_beam.ml.rag.html
  - https://github.com/apache/beam/tree/master/examples/notebooks/beam-ml/rag_usecase
  - https://cloud.google.com/dataflow/docs/notebooks/bigquery_vector_ingestion_and_search

## Acceptance criteria

1. All 5 `GenerationEngine` ABC tests pass (see `engine-contract.md`).
2. Setup time on a 10k-row reference is <60s with `bge-small-en-v1.5` (CPU embedder).
3. Retrieval returns deterministic top-k for a given query embedding (no implicit randomness; seed-respected).
4. Generated records show evidence of using retrieved exemplars — qualitative eyeball check on free-text columns and rare values plus an assertion that exemplar values appear at >baseline rates.
5. No HuggingFace Hub network calls at runtime — `HF_HUB_OFFLINE=1` does not break tests.
6. Worktree rebases cleanly on `main` weekly while the `GenerationEngine` ABC is still settling.
