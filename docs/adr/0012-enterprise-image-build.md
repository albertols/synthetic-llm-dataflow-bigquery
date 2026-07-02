# ADR 0012 — Enterprise-network constraints for the GPU image build + Dataflow run

- **Status**: accepted (2026-05-20)

## Context

The GPU image is built on the bank's GitHub ARC self-hosted runners and run on Dataflow workers in the GCP DEV landing zone. Both environments are network-restricted in ways the original [ADR 0009](0009-single-flex-template-image.md) Dockerfile did not account for:

- **ARC build runners cannot reach Docker Hub or (likely) gcr.io.** The original Dockerfile pulled `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`, `apache/beam_python3.11_sdk:2.73.0`, and `gcr.io/dataflow-templates-base/python311-template-launcher-base` — all unreachable.
- **JFrog Artifactory is the only reachable registry**, and it currently mirrors `com/db/awp/cuda:12.2.2-cudnn-runtime-ubuntu22.04` (and a 12.6.1/24.04) plus `dkr-io/apache/beam_python3.11_sdk:2.71.0` (**not** 2.73.0).
- **pypi / files.pythonhosted are blocked**; the JFrog `pypi-all` mirror is the package index (wired as the default `[[tool.uv.index]]`).
- **Dataflow workers run with `WORKER_IP_PRIVATE`** (no external IP) on a corporate subnet with a five-tag firewall chain.

## Decision

1. **All container base images come from JFrog**, injected as Dockerfile build-args (`CUDA_IMAGE`, `BEAM_SDK_IMAGE`, `LAUNCHER_IMAGE`) defaulting off `ARTIFACTORY_HOSTNAME_ARG`. CUDA base: `12.2.2-cudnn-runtime-ubuntu22.04` (satisfies L4's CUDA ≥ 12.0; vLLM/torch `cu121` wheels run on it under a ≥525 driver).
2. **`apache-beam[gcp]` is pinned `==2.71.0`** to match the only Beam SDK image in JFrog. Runner v2 requires the worker boot binary and the apache-beam wheel to be the same version. 2.71.0 still ships `apache_beam.ml.inference.vllm_inference` (added 2.60.0), so [ADR 0011](0011-adopt-beam-vllm-model-handler.md) holds. Bump in lockstep when a newer SDK image is mirrored.
3. **Model warm-pull uses the `google-cloud-storage` Python client, not `gsutil`.** This deletes the `google-cloud-cli` apt install (which reached `packages.cloud.google.com` at build time) and the entire `cloud-sdk` apt block. The client is a transitive dep of `apache-beam[gcp]` and authenticates via ADC on the worker.
4. **NVIDIA driver via `install-nvidia-driver:latest`** — the official documented values are `default`/`latest` (not the Beam notebook's `:5xx` COS shorthand). `latest` yields a 5xx-series driver, satisfying vLLM + L4 (≥525). The driver is installed on the host VM by Dataflow and mounted at `/usr/local/nvidia/`; it is not baked into the image.
5. **Python 3.11 from the internal apt mirror** if available; otherwise uv-managed Python via `UV_PYTHON_INSTALL_MIRROR` → a JFrog generic repo. uv itself is bootstrapped via `pip --index-url <jfrog pypi>`.

## Consequences

- **Enables**: a build that touches only JFrog (+ the internal apt mirror) — no Docker Hub, no `packages.cloud.google.com`, no PPA, no pypi.org.
- **Networking prerequisites for the Dataflow run** (private-IP workers):
  - **Private Google Access** must be enabled on the Dataflow subnet — workers reach GCS (model + staging), BigQuery, the Dataflow control plane, and the COS driver download only via PGA.
  - **JFrog image pull** rides the corporate interconnect via the `artifactory` + `netsegcloudegress` network tags; Dataflow authenticates with `--image-repository-{username,password}-secret-id` (GSM). The worker SA needs `secretmanager.secretAccessor`.
  - **`enable_secure_boot` may conflict with `install-nvidia-driver`** (unsigned kernel module). Pre-flight; if the driver won't load, drop Secure Boot for the GPU job.
  - **Consider an Artifact Registry mirror** of the final image for the Dataflow consumption path: pulling a ~6–8 GB GPU image cross-interconnect on every autoscale event (`maxWorkers`) is slow. JFrog stays the governance source; AR (pulled via PGA) would be the fast in-network path. This narrows [ADR 0003](0003-jfrog-image-registry.md) for the worker-pull case specifically — open for discussion, not yet decided.
- **Costs / open items** (must be confirmed before the first green build):
  - Exact JFrog pull strings for the CUDA + Beam images (the `com/db/awp/cuda/...` form is ambiguous on where the tag splits).
  - Whether `gcr.io` is reachable from ARC runners; if not, the launcher base must be mirrored to JFrog (ticket).
  - Whether the internal apt mirror carries `python3.11` (else uv-managed Python).
  - `NVIDIA_L4_GPUS` quota in `europe-west3` ≥ `maxWorkers`.
- **Forbids**: reintroducing Docker Hub / gcr.io / pypi.org / `packages.cloud.google.com` references into the build path.

## Related

- [ADR 0003](0003-jfrog-image-registry.md) — JFrog as the registry (this ADR narrows it for the worker-pull path).
- [ADR 0008](0008-ci-driven-builds.md) — CI-driven builds.
- [ADR 0009](0009-single-flex-template-image.md) — single image, two entrypoints.
- [ADR 0011](0011-adopt-beam-vllm-model-handler.md) — Beam vLLM handler (unaffected by the 2.71.0 pin).
- `docker/Dockerfile`, `.github/workflows/1_build_python_beam.yaml`, `docs/CICD.md` — implementation.
- `.claude/skills/gpu-dockerfile.md` — recipe.
