# Architecture Decision Records

Durable record of significant decisions taken on synthetic-dataflow-bigquery. Each ADR captures one decision in immutable form: once recorded, an ADR is amended only with a "Superseded by …" note pointing at a successor, never silently rewritten.

## When to write an ADR

- Locking a technology choice that constrains future work (model family, registry, region).
- Rejecting a managed service or external dependency for non-obvious reasons.
- Establishing a project-wide convention that future contributors will need to understand.
- Reversing a previous decision (write a new ADR that supersedes the old one).

Do NOT write an ADR for:
- Implementation details documented in the code itself.
- Recipes that belong in `.claude/skills/`.
- Per-task notes — those live in `docs/ROADMAP.md` or the task tracker.

## Format

Each ADR is a single markdown file:

```
docs/adr/NNNN-short-kebab-title.md
```

with these sections:

- **Status** — `accepted` / `superseded by NNNN` / `proposed`.
- **Context** — what's the situation that forces a decision?
- **Decision** — the choice, in one sentence.
- **Consequences** — what this enables, what it costs, what it forbids.

Keep ADRs tight — half a page is plenty. Detail belongs in the code or in skills.

## Index

- [0001 — No managed GCP services in the serving path](0001-no-managed-gcp-services.md)
- [0002 — Gemma 4 family as the M1 model shortlist](0002-gemma-4-model-shortlist.md)
- [0003 — Corporate JFrog as the container registry](0003-jfrog-image-registry.md)
- [0004 — europe-west3 as the Dataflow region](0004-europe-west3-region.md)
- [0005 — Live BQ SELECT for reference rows, not cached parquet](0005-live-select-reference-data.md)
- [0006 — `GenerationEngine` ABC + `ModelClient` Protocol shape](0006-generation-engine-abc.md)
- [0007 — DRY across documentation and code](0007-dry-documentation-policy.md)
- [0008 — Container image builds happen in CI, not on developer laptops](0008-ci-driven-builds.md)
- [0009 — Single image for Flex Template launcher AND Dataflow workers](0009-single-flex-template-image.md)
- [0010 — M4 local smoke test via MLX (no DirectRunner-with-Docker)](0010-m4-local-smoke-mlx.md)
- [0011 — Adopt Beam's `VLLMCompletionsModelHandler` for §9](0011-adopt-beam-vllm-model-handler.md)
- [0012 — Enterprise-network constraints for the GPU image build + Dataflow run](0012-enterprise-image-build.md)
- [0013 — Synthesis engines use an LLM-as-distribution-estimator spine](0013-distribution-estimator-spine.md)
- [0014 — `VLLMModelClient` owns the vLLM OpenAI server (amends 0011)](0014-vllm-model-client-owns-server.md)
- [0015 — Dataflow worker image served via Artifact Registry (amends 0003)](0015-worker-image-via-artifact-registry.md)
