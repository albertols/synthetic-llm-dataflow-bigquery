---
name: gpu-dockerfile
description: Recipe for the L4 GPU Dataflow custom container — CUDA base + Beam SDK overlay + Flex Template launcher + vLLM, with model warm-pull from GCS at worker startup (not container build). Load when working on `docker/Dockerfile` or the CI workflows that build it.
---

# Skill — GPU Dockerfile

> **Source of truth** for the actual image is **`docker/Dockerfile`** in this repo. The CI build/push pipeline is **`.github/workflows/1_build_python_beam.yaml`** and the operational runbook is **[`docs/CICD.md`](../../docs/CICD.md)**. This skill is a recipe for working on those files — not a copy of their content.

## The two-contract trick

Dataflow runs the **same image ENTRYPOINT** in two different ways and *appends* args — it does **not** override the entrypoint. So the ENTRYPOINT is a dispatch script (`docker/entrypoint.sh`) that picks the binary from the appended args:

- **Flex Template launch** → no FnAPI flags → execs `/opt/google/dataflow/python_template_launcher`. The launcher reads `FLEX_TEMPLATE_PYTHON_PY_FILE` (set to `sdfb_beam.cli.run_pipeline`) and submits the job.
- **Dataflow workers** → Dataflow appends the Beam FnAPI boot flags (`--id`, `--logging_endpoint`, `--control_endpoint`, `--artifact_endpoint`, `--provision_endpoint`) → the script execs `/opt/apache/beam/boot`. (Letting the launcher binary receive these is what crash-looped `sdk-0-0` with `flag provided but not defined: -logging_endpoint`.)

Both binaries are copied into the final image via multi-stage `COPY --from`. See [ADR 0009](../../docs/adr/0009-single-flex-template-image.md) for the rationale.

## Launch flags

```bash
# REF: https://docs.cloud.google.com/dataflow/docs/gpu/use-gpus
# Official driver-version values are `default` or `latest` (NOT a literal
# `5xx` — that's a COS driver-branch shorthand from the Beam notebook).
# `latest` gives a 5xx-series driver, which is what vLLM needs (≥525 for L4).
--dataflow_service_options="worker_accelerator=type:nvidia-l4;count:1;install-nvidia-driver:latest"
--worker_machine_type="g2-standard-8"
--experiments=use_runner_v2
--sdk_container_image="${ARTIFACTORY_HOSTNAME}/dkr-public-local/${ARTIFACTORY_NAMESPACE}/sdfb-python:${VERSION}"
--worker_disk_type=pd-ssd
--worker_disk_size_gb=200
--image-repository-username-secret-id="projects/${PROJECT}/secrets/ARTIFACTORY_RELEASER_USERNAME"
--image-repository-password-secret-id="projects/${PROJECT}/secrets/ARTIFACTORY_RELEASER_PASSWORD"
```

`g2-standard-8` = 1×L4 (24 GB VRAM), 8 vCPU, 32 GB RAM. Upgrade to `g2-standard-24` if NVIDIA MPS is needed for multi-process GPU sharing.

**Base images come from JFrog**, not Docker Hub / gcr.io (ARC build runners are network-restricted). CUDA `com/db/awp/cuda:12.2.2-cudnn-runtime-ubuntu22.04`, Beam SDK `dkr-io/apache/beam_python3.11_sdk:2.71.0` (→ `apache-beam==2.71.0` pin). Networking prereqs (Private Google Access, Secure Boot vs driver, JFrog egress) and the full rationale: [ADR 0012](../../docs/adr/0012-enterprise-image-build.md).

## Model warm-pull

The image is intentionally weights-free. Model weights live in `gs://{bucket}/synthetic/models/{family}/{model}/{version}/` and are pulled once per worker lifetime inside the `ModelClient.setup()` method. We use the **`google-cloud-storage` Python client**, not `gsutil` — the CLI would force a `google-cloud-cli` apt install from `packages.cloud.google.com`, which ARC runners and private-IP workers can't reach. The client is already a transitive dep of `apache-beam[gcp]` and authenticates via ADC.

```python
# packages/sdfb-beam/src/sdfb_beam/handlers/vllm_client.py (real impl in M1 §9)
def setup(self):
    from google.cloud import storage
    bucket_name, prefix = _split_gs_uri(self.model_uri)   # gs://b/p/ -> (b, p/)
    client = storage.Client()                              # ADC on worker
    for blob in client.list_blobs(bucket_name, prefix=prefix):
        rel = blob.name[len(prefix):]
        if not rel:                                        # skip dir placeholder
            continue
        dest = Path("/local-ssd/model") / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(dest)                    # parallelize with a thread pool
```

This keeps the image weights-free (~3 GB vs ~15 GB baked-in) and removes the gcloud CLI dependency entirely. Worker cold-start budget: ≤ 3 min total, including this pull. See [`docs/MODEL_LAYOUT.md`](../../docs/MODEL_LAYOUT.md) for the GCS layout contract.

## NVIDIA MPS — when

Single-process per worker first. Move to MPS only if the Dataflow GPU metric `gpu_utilization` shows <60% at steady state.

REF: https://docs.cloud.google.com/dataflow/docs/gpu/use-nvidia-mps · https://docs.cloud.google.com/dataflow/docs/gpu/gpu-metrics

## Troubleshooting

If the job fails on driver install, check the satellite-images tutorial — it's the canonical working Python+GPU example:

REF: https://docs.cloud.google.com/dataflow/docs/tutorials/satellite-images-gpus

For other GPU job issues:

REF: https://docs.cloud.google.com/dataflow/docs/gpu/troubleshoot-gpus

## Current implementation

| Concern | File | Status |
|---|---|---|
| Single image (launcher + workers) | `docker/Dockerfile` | ✅ |
| Build context exclusions | `docker/.dockerignore` | ✅ |
| Flex Template parameter schema | `docker/flex_template_metadata.json` | ✅ |
| CI build + push workflow | `.github/workflows/1_build_python_beam.yaml` | ✅ |
| CI flex-template deploy | `.github/workflows/2_deploy_flex_template_python_beam.yaml` | ✅ |
| 1-row Dataflow probe (image already in JFrog) | `scripts/probe_gpu_dataflow.sh` | ✅ |
| Operational runbook | [`docs/CICD.md`](../../docs/CICD.md) | ✅ |

Per [ADR 0008](../../docs/adr/0008-ci-driven-builds.md), developers do not build images locally. The retired local-build scripts have been removed.

ADRs: [`0003`](../../docs/adr/0003-jfrog-image-registry.md), [`0004`](../../docs/adr/0004-europe-west3-region.md), [`0008`](../../docs/adr/0008-ci-driven-builds.md), [`0009`](../../docs/adr/0009-single-flex-template-image.md).

## References

- Overview: https://docs.cloud.google.com/dataflow/docs/gpu
- GPU support matrix: https://docs.cloud.google.com/dataflow/docs/gpu/gpu-support
- Develop with GPUs: https://docs.cloud.google.com/dataflow/docs/gpu/develop-with-gpus
- Use GPUs: https://docs.cloud.google.com/dataflow/docs/gpu/use-gpus
- L4-specific: https://docs.cloud.google.com/dataflow/docs/gpu/use-l4-gpus
- NVIDIA MPS: https://docs.cloud.google.com/dataflow/docs/gpu/use-nvidia-mps
- GPU metrics: https://docs.cloud.google.com/dataflow/docs/gpu/gpu-metrics
- Troubleshooting: https://docs.cloud.google.com/dataflow/docs/gpu/troubleshoot-gpus
- Tutorial: https://docs.cloud.google.com/dataflow/docs/tutorials/satellite-images-gpus
- vLLM install: https://docs.vllm.ai/en/latest/getting_started/installation.html
- Flex Templates with custom containers: https://cloud.google.com/dataflow/docs/guides/templates/configuring-flex-templates
