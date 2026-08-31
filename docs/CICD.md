# CICD — build, deploy, run

End-to-end CICD for the Python Beam pipeline on Dataflow. **All container builds and deploys happen in GitHub Actions** (see [ADR 0008](adr/0008-ci-driven-builds.md)); developers do not push images from their laptops.

Operational siblings: [`M4_SETUP.md`](M4_SETUP.md) for laptop/M4 dev env, [`MODEL_LAYOUT.md`](MODEL_LAYOUT.md) for weights, [`M4_LOCAL_SMOKE.md`](M4_LOCAL_SMOKE.md) for the M4 MLX iteration loop.

## TL;DR

| Step | Trigger | Who | Outputs |
|---|---|---|---|
| Build image + wheel + push | `workflow_dispatch` on `1_build_python_beam.yaml` | CI on ARC runner | `<jfrog>/.../sdfb-python:<tag>` + sdfb_beam wheel in GCS |
| Deploy Flex Template JSON | `workflow_dispatch` on `2_deploy_flex_template_python_beam.yaml` | CI on ARC runner | `gs://…/synthetic/sdfb-<tag>-template.json` |
| Import DAG | `workflow_dispatch` on `3_import_dag.yaml` | CI on ARC runner | DAG file in Composer bucket |
| Run pipeline | Airflow DAG (manual or scheduled) | Composer | Dataflow job, synthetic rows in BQ |

## Locked decisions

| Concern | Value | ADR |
|---|---|---|
| Build location | CI (no laptop builds) | [0008](adr/0008-ci-driven-builds.md) |
| Image strategy | Single image for launcher AND workers | [0009](adr/0009-single-flex-template-image.md) |
| Build tool | `uv` (no Poetry migration) | (see this doc §1) |
| Registry | Dual-push: JFrog (archival, registry-of-record) + **Artifact Registry (the only runtime pull source** for launcher + workers) | [0003](adr/0003-jfrog-image-registry.md) amended by [0015](adr/0015-worker-image-via-artifact-registry.md) |
| Region | `europe-west3` (Frankfurt) | [0004](adr/0004-europe-west3-region.md) |
| Image name | `sdfb-python` (single tag space) | (see this doc §2) |
| App matrix | single-app for M1 (no `APP_NAME` input) | (deferred to M2) |

## §1 — Build tool: uv

The project is a uv workspace (`pyproject.toml` at root, 3 members, `uv.lock` committed). The Flex Template launcher reads `FLEX_TEMPLATE_PYTHON_REQUIREMENTS_FILE` but **we deliberately leave that env var unset** so the launcher skips its built-in `pip install -r` at job start; the venv is already baked in via `uv sync --frozen --no-dev`.

If we ever need to feed the launcher a `requirements.txt` (e.g. for `FLEX_TEMPLATE_PYTHON_EXTRA_PACKAGES`), generate it with:

```bash
uv export --frozen --no-dev --all-packages \
          --extra gpu --extra embedding --extra library \
          --no-hashes -o requirements.txt
```

That output is lockfile-faithful and pip-installable.

## §2 — Image: single `sdfb-python` tag

`docker/Dockerfile` produces ONE image that serves both Dataflow runtime contracts. Dataflow runs the image ENTRYPOINT for both roles and *appends* args (it does NOT override the entrypoint), so the ENTRYPOINT is a dispatch script (`docker/entrypoint.sh`):
- **Flex Template launcher**: no FnAPI flags appended → execs `/opt/google/dataflow/python_template_launcher`.
- **Dataflow worker**: Dataflow appends the Beam FnAPI boot flags (`--id`, `--logging_endpoint`, …) → the script execs `/opt/apache/beam/boot`.

Both binaries are present at their canonical paths. See the Dockerfile comment block and [ADR 0009](adr/0009-single-flex-template-image.md).

Tag scheme: `<sha>` for `Development` builds (branch-name-prefixed for `RELEASE_dbc_*` branches), or the project version for `Release` builds. `latest` is rolled on every build for cache-from purposes.

## §3 — Workflow 1: `1_build_python_beam.yaml`

```
on: workflow_dispatch (BUILD_TYPE: Development | Release, push_to_artifactory: bool, ITSK: optional)

jobs:
  unlock-env    # gates only on RELEASE_dbc_prd via reusable workflow
  build:
    - checkout
    - branch → ENV_NAME + GOOGLE_PROJECT_DBC + SERVICE_ACCOUNT_STRING
    - WIF auth → GSM secrets (artifactory creds)
    - install uv + sync workspace (--frozen --no-dev --all-packages --extra gpu+embedding+library)
    - pytest -m "not gpu and not gcp"   ← sanity gate; rejects broken code before docker build
    - derive PROJECT_VERSION (from sdfb-beam wheel metadata) + RELEASE_VERSION (branch-sha or version)
    - docker build -f docker/Dockerfile (single image)
    - docker push <jfrog>/.../sdfb-python:{RELEASE_VERSION,latest}
    - optionally push sdfb_beam wheel to JFrog pypi
    - upload wheel + config/ to gs://<bucket>-${ENV}-${SUFFIX}-synthetic/sdfb/${VERSION}/
```

## §4 — Workflow 2: `2_deploy_flex_template_python_beam.yaml`

```
on: workflow_dispatch (BUILD_TYPE, ITSK)

jobs:
  unlock-env
  deploy:
    - checkout
    - branch → env
    - WIF auth → GSM secrets
    - gcloud dataflow flex-template build gs://<staging>/synthetic/sdfb-<VER>-template.json \
        --image=<jfrog>/.../sdfb-python:<VER> \
        --image-repository-username-secret-id=projects/<PROJECT>/secrets/ARTIFACTORY_RELEASER_USERNAME \
        --image-repository-password-secret-id=projects/<PROJECT>/secrets/ARTIFACTORY_RELEASER_PASSWORD \
        --sdk-language=PYTHON \
        --metadata-file=docker/flex_template_metadata.json \
        --staging-location=gs://…-dataflow-staging/staging \
        --temp-location=gs://…-dataflow-staging/temp \
        --dataflow-kms-key=projects/<KEYS_${ENV}>/locations/${REGION}/keyRings/gcp_dataflow/cryptoKeys/<KEY>
```

## §5 — Workflow 3: `3_import_dag.yaml`

```
on: workflow_dispatch (project_version, table_fqn, num_rows, ITSK)

jobs:
  unlock-env
  import:
    - checkout
    - branch → env
    - WIF auth
    - sed -i \
        -e "s/{{PROJECT_VERSION}}/$VER/g" \
        -e "s/{{DAG_VERSION}}/${VER}_$(date +%Y_%m_%d_%H_%M)/g" \
        -e "s/{{ENV}}/$ENV/g" \
        composer/synthetic_beam_bigquery.py
    - gcloud composer environments storage dags import \
        --environment=<composer>-${ENV}-<team>-composerv2-${REGION} \
        --location=${REGION} \
        --source=composer/synthetic_beam_bigquery.py
```

Build-time sed handles **env-specific** values (network tags, project IDs, version). **Runtime values** (`table_fqn`, `num_rows`, `run_id`) are Airflow DAG params — operators don't need to re-import the DAG to change them.

## §6 — Secrets & service accounts

| Type | Name | Purpose |
|---|---|---|
| Org secret | `DEFAULT_WIF_PROVIDER` | WIF provider resource path |
| Org secret | `PIPELINE_SERVICE_ACCOUNT_EMAILS` | JSON map `{env/sa-name: email}` |
| Org secret | `GOOGLE_PROJECT_DBC_{DEV,UAT,PRD}` | Per-env GCP project where GSM secrets live |
| GSM | `ARTIFACTORY_DEVELOPER_USERNAME/PASSWORD` | Read access to JFrog |
| GSM | `ARTIFACTORY_RELEASER_USERNAME/PASSWORD` | Write access for image push |
| GSM | `ARTIFACTORY_RELEASER_USERNAME/PASSWORD` | Same secrets referenced by `--image-repository-*-secret-id` for Dataflow worker pulls |

## §7 — Gotchas

- **Base images come from JFrog, not Docker Hub/gcr.io** ([ADR 0012](adr/0012-enterprise-image-build.md)): ARC runners are network-restricted. CUDA `com/db/awp/cuda:12.2.2-cudnn-runtime-ubuntu22.04` + Beam `dkr-io/apache/beam_python3.11_sdk:2.74.0`. The Beam SDK image version forces `apache-beam==2.74.0` in `packages/sdfb-beam/pyproject.toml` (Runner v2 needs the worker boot ↔ wheel versions to match). The Flex launcher base is still on gcr.io — mirror to JFrog if ARC can't reach gcr.io.
- **Private-IP Dataflow networking** ([ADR 0012](adr/0012-enterprise-image-build.md)): `WORKER_IP_PRIVATE` requires **Private Google Access** on the subnet (GCS, BigQuery, control plane, COS driver download). JFrog image pull rides the interconnect via the `artifactory`/`netsegcloudegress` tags + GSM secret auth; worker SA needs `secretmanager.secretAccessor`. **`enable_secure_boot` can block the unsigned NVIDIA driver module** — pre-flight, drop it for the GPU job if the driver won't load. Driver value is `install-nvidia-driver:latest` (not `:5xx`). The final image is dual-pushed and **workers/launcher pull from Artifact Registry only** ([ADR 0015](adr/0015-worker-image-via-artifact-registry.md)) — the kubelet SDK-harness pod can only authenticate GCR/AR via the worker SA, never a third-party registry.
- **No `FLEX_TEMPLATE_PYTHON_REQUIREMENTS_FILE`**: Unset on purpose. The launcher would otherwise `pip install -r` at every job start, adding latency and conflict risk on top of our baked-in `.venv`.
- **`save_main_session`**: Set `False` in the CLI when running on Dataflow (image bakes in deps); `True` only for ad-hoc DirectRunner.
- **KMS + L4**: known issues with some disk encryption configs and GPU machine families. Pre-flight: run the probe with `--dataflow-kms-key` set; if workers fail to start, drop KMS for the GPU job (document why in a new ADR).
- **JFrog pull on workers**: `--image-repository-{username,password}-secret-id` flags point at GSM, NOT at GitHub secrets. GSM is the runtime source of truth.
- **Cache-from is JFrog-backed**: `--cache-from <registry>:latest` works because JFrog is persistent. ARC runners are ephemeral; in-runner Docker cache does not survive.
- **Region**: `europe-west3` for both Dataflow and image pulls. Cross-region pulls add cold-start latency.

## §8 — Local smoke before pushing

Before triggering workflow 1, run the laptop sanity gate:

```bash
uv sync --group dev
uv run pytest -m "not gpu and not gcp" -q
uv run ruff check .
```

Same check runs in workflow 1; running it locally catches problems before burning a CI minute.

For real-LLM smoke testing on M4 without Dataflow, see [`M4_LOCAL_SMOKE.md`](M4_LOCAL_SMOKE.md).
