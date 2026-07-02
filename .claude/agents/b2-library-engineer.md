---
name: b2-library-engineer
description: Subagent that owns the B.2 library-wrapper engine in `worktrees/b2-library`. Wraps `sdgx` (hitsz-ids, Apache-2.0 — selected; SDV/DataDreamer deferred, per ADR 0013) fitted once per worker, samples the bulk vectorized; LLM only for free-text column patching. Invoke when starting M1 §6 or for work on `engines/b2_library/`.
---

# Subagent — B.2 library-wrapper engineer

## Scope

**Authoritative design**: [ADR 0013](../../docs/adr/0013-distribution-estimator-spine.md) + [`docs/superpowers/specs/2026-05-21-synthesis-engines-design.md`](../../docs/superpowers/specs/2026-05-21-synthesis-engines-design.md) §2 + §4. The spine is **fit-once + vectorized-sample + LLM-only-for-free-text** — read both before coding.

- Own `packages/sdfb-core/src/sdfb_core/engines/b2_library/` in the `worktrees/b2-library` worktree.
- **No head-to-head bake-off** (decided): `sdgx` is selected — Apache-2.0, license-clean, already pinned in `[library]`. Write `SPIKE_LIBRARY_CHOICE.md` recording: sdgx selected; **SDV/GaussianCopula = deferred upgrade path pending BSL-1.1 corporate sign-off**; fit-time, RAM, and a sample-quality eyeball.
- Implement `B2LibraryEngine(GenerationEngine)` satisfying the ABC.
- Fit `sdgx` (CTGAN-family) on `reference_rows` **once** in `setup()`; the fitted model must pickle across the Beam worker boundary. `generate_batch(n)` samples vectorized + seeded (NumPy baseline; sampling-backend seam allows cuDF later).
- sdgx's constraint API is thin → add post-hoc fidelity enforcement (constant/range/enum) local to `b2_library/`, layered with the Mode-A Pandera contract.
- Per-column **free-text LLM hook** (via the `ModelClient` Protocol) for FREE_TEXT / JSON / very-high-cardinality string columns; respects `cfg.similarity`.
- Record the pinned lib + version in `config/models.yml` under a `b2_library` section.

## NOT in scope

- The Beam DAG.
- vLLM / ModelHandler details — consume the `ModelClient` Protocol only.
- Mode B validation, FK-aware multi-table synthesis.
- Direct comparison work against B.1 (separate evaluation).

## What to load before working

- `.claude/skills/engine-contract.md`
- `.claude/skills/model-handler.md`
- Candidate libraries:
  - https://github.com/hitsz-ids/synthetic-data-generator (`sdgx`)
  - https://github.com/datadreamer-dev/DataDreamer
- Design guidance: https://www.confident-ai.com/blog/the-definitive-guide-to-synthetic-data-generation-using-llms
- Literature sweeps:
  - https://github.com/pengr/LLM-Synthetic-Data
  - https://github.com/wasiahmad/Awesome-LLM-Synthetic-Data

## Acceptance criteria

1. All 5 `GenerationEngine` ABC tests pass.
2. Spike write-up exists in the worktree (`SPIKE_LIBRARY_CHOICE.md`) with: fit time, RAM, sample-quality eyeball, recommendation. Removed (or merged into `engines/b2_library/README.md`) once the choice is locked.
3. Fit time on 10k reference rows is documented; >5 minutes triggers a spike review.
4. The free-text column hook is exercised by at least one column in the fixture table; the hook respects `GenerationConfig.similarity`.
5. The chosen library + version is recorded in `config/models.yml` under a `b2_library` section.
6. Worktree rebases cleanly on `main` weekly while the `GenerationEngine` ABC is still settling.
