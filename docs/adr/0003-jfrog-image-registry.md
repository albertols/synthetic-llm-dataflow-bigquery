# ADR 0003 — Corporate JFrog as the container registry

- **Status**: accepted (2026-05-19)

## Context

Dataflow GPU workers need a custom container image. Two natural homes: Google's **Artifact Registry** (`europe-west3-docker.pkg.dev/<project>/sdfb/sdfb-gpu:<tag>`) and the user's **corporate JFrog / Artifactory** (`${ARTIFACTORY_HOSTNAME}/dkr-public-local/${ARTIFACTORY_NAMESPACE}/sdfb-gpu:<tag>`). AR has lower image-pull latency to in-region Dataflow workers and integrates with VPC-SC. JFrog matches the user's prior SCIO pipeline pattern and corporate compliance.

## Decision

Push the GPU image to **corporate JFrog**, using the SCIO-pattern URL. Dataflow workers pull via the `--image-repository-username-secret-id` and `--image-repository-password-secret-id` flags pointing at two Secret Manager entries (`ARTIFACTORY_RELEASER_USER`, `ARTIFACTORY_RELEASER_PS`).

## Consequences

- **Enables**: alignment with the existing corporate publishing pipeline and audit trail. No need to provision and IAM-manage a new AR repo per project.
- **Costs**: slightly higher worker cold-start latency (cross-network image pull vs in-region AR). Requires Secret Manager wiring on the project and `roles/secretmanager.secretAccessor` on the Dataflow service account. Means the deliverable is not "out of the box" for orgs without a private Docker registry; the GPU container path will need an AR fallback at OSS release time.
- **Forbids**: hardcoding the JFrog hostname or namespace into committed files. Both flow through `.envrc` env vars (`ARTIFACTORY_HOSTNAME`, `ARTIFACTORY_NAMESPACE`); the example template lives in `.envrc.example`.

## Related

- `docs/GPU_CONTAINER.md` — operational runbook for build/push/probe.
- `scripts/build_gpu_image.sh` / `scripts/push_gpu_image.sh` — implementation.
- M3 candidate: add an AR fallback path so the OSS release works without JFrog.
