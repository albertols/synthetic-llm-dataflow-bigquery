# ADR 0008 — Container image builds happen in CI, not on developer laptops

- **Status**: accepted (2026-05-19)
- **Supersedes**: nothing; refines [ADR 0003](0003-jfrog-image-registry.md) on the *where* of the build.

## Context

[ADR 0003](0003-jfrog-image-registry.md) locked the **registry** (corporate JFrog) but left the **build location** undefined. Earlier iteration assumed M4-local docker build → `gsutil` push, with `scripts/build_gpu_image.sh` and `scripts/push_gpu_image.sh` as the laptop-side workflow.

That approach has three real problems in a regulated bank context:
1. **Provenance**: developer-local builds bypass the SDLC audit trail. PRD images must trace to a workflow run with WIF + GSM + ServiceNow ITSK linkage.
2. **Determinism**: M4 Apple-Silicon emulating `linux/amd64` produces images bit-different from real Linux runners; reproducibility suffers.
3. **Speed**: full CUDA + cuDNN + uv-sync rebuild on M4 takes 15–25 minutes; on ARC self-hosted runners with JFrog cache-from, under 5 minutes.

## Decision

**All container builds and pushes happen in GitHub Actions workflows** running on ARC self-hosted runners (bank infrastructure). Authentication via Workload Identity Federation + GSM-resolved Artifactory credentials. The two scripts (`build_gpu_image.sh`, `push_gpu_image.sh`) are removed; developers cannot push images directly.

`scripts/probe_gpu_dataflow.sh` survives — but it now submits a Dataflow probe job using an image already pushed by CI (`--sdk_container_image=<JFrog>/...:<sha>`), it does **not** build anything.

## Consequences

- **Enables**: audited provenance (workflow run + ITSK + SHA in image tag), reproducible builds across machines, faster iteration via JFrog cache, GSM-managed registry creds (devs never see them).
- **Costs**: no fully-offline dev loop — a fresh image requires a workflow_dispatch. Smoke testing against a real model on M4 falls back to MLX (see [ADR 0010](0010-m4-local-smoke-mlx.md)).
- **Forbids**: committing `docker build` or `docker push` shell scripts; bypassing CI to publish images; treating manually-built images as deployable.

## Related

- `.github/workflows/1_build_python_beam.yaml` — implementation.
- `docs/CICD.md` — operational runbook.
- [ADR 0009](0009-single-flex-template-image.md) — what the image actually contains.
