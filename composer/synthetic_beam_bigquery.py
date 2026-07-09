"""Airflow DAG — submit the synthetic-dataflow-bigquery Flex Template.

This file is the *template*. Workflow `3_db_import_dag.yaml` runs `sed` over
it at deploy time to substitute build-time values. Runtime values
(table_fqn, num_rows, run_id) come from Airflow DAG params and Composer
Variables — operators don't need to re-import the DAG to change them.

Substitution markers (workflow 3 seds these at import time):
  {{PROJECT_VERSION}}         project version of the sdfb-beam package being deployed
  {{DAG_VERSION}}             {{PROJECT_VERSION}}_<ISO timestamp> (unique DAG id)
  {{ENV}}                     dev | uat | prd
  {{GCS_DATAFLOW_STAGING}}    db-<env>-…-dataflow-staging bucket name
  {{GCS_DATAFLOW_TEMPLATES}}  db-<env>-…-dataflow-templates bucket name
  {{SDFB_MODEL_URI}}          gs://<bucket>/synthetic/models/gemma4/…
  {{SDFB_EMBEDDER_URI}}       gs://<bucket>/synthetic/models/embedders/… (B.1; empty ⇒ HashingEmbedder)
  {{SDFB_DEFAULT_TABLE_FQN}}  project.dataset.table
  {{SDFB_DDL_URI}}            gs://…/ddl.json
  {{SDFB_LANDING_TABLE}}      project.synthetic_data.<table> (defaults to the source table name)
  {{SDFB_DLQ_TABLE}}          project.synthetic_data_quality.dlq
  {{SDFB_VALIDATION_RUNS_TABLE}} project.synthetic_data_quality.validation_runs

Runtime values: Airflow DAG params ({{ params.* }}: table_fqn, num_rows,
engine, batch_size, similarity, client_type) + Composer Variables for infra
(PROJECT_ID, REGION, DATAFLOW_SUBNET, SA_DATAFLOW) — changeable without
re-importing. client_type=fake selects a CPU smoke (no L4) — see below.

"""

from __future__ import annotations

from airflow import models
from airflow.models import Variable
from airflow.models.param import Param
from airflow.providers.google.cloud.operators.dataflow import (
    DataflowStartFlexTemplateOperator,
)
from airflow.utils.dates import days_ago

# -----------------------------------------------------------------------------
# Build-time values — sed-substituted by workflow 3 at import (see docstring).
# -----------------------------------------------------------------------------
bucket_path = "{{GCS_DATAFLOW_STAGING}}"        # …-dataflow-staging
templates_path = "{{GCS_DATAFLOW_TEMPLATES}}"   # …-dataflow-templates
model_uri = "{{SDFB_MODEL_URI}}"                # gs://<bucket>/synthetic/models/gemma4/e4b-it/v1/
embedder_uri = "{{SDFB_EMBEDDER_URI}}"          # B.1 embedder; empty ⇒ HashingEmbedder
default_table_fqn = "{{SDFB_DEFAULT_TABLE_FQN}}"
validation_runs_table = "{{SDFB_VALIDATION_RUNS_TABLE}}"  # synthetic_data_quality.validation_runs

# -----------------------------------------------------------------------------
# Runtime infra — Composer Variables, set once per env (not build-time-baked).
# -----------------------------------------------------------------------------
project_id = Variable.get("PROJECT_ID")
region = Variable.get("REGION")
subnetwork = Variable.get("DATAFLOW_SUBNET")
service_account = Variable.get("SA_DATAFLOW")

# -----------------------------------------------------------------------------
# Build-time substituted constants (replaced by sed in workflow 3).
# -----------------------------------------------------------------------------
app_domain = "synthetic"
app_name = "sdfb"
project_version = "{{PROJECT_VERSION}}"
dag_version = "{{DAG_VERSION}}"
env_name = "{{ENV}}"

def _model_slug(uri: str, max_len: int = 24) -> str:
    """Job-name-safe model slug from the baked SDFB_MODEL_URI.

    Dataflow job names must match ``[a-z]([-a-z0-9]{0,61}[a-z0-9])?`` —
    lowercase/digits/hyphens only, NO underscores or dots — so
    ``qwen2.5/7b-instruct`` becomes ``qwen2-5-7b-instruct``. Derived from the
    already-substituted `model_uri` constant: no new workflow sed marker.
    Returns "" (no suffix) when the URI is not a gs:// model path.
    """
    import re

    if not uri.startswith("gs://") or "synthetic/models/" not in uri:
        return ""
    parts = [p for p in uri.split("synthetic/models/", 1)[1].split("/") if p]
    if parts and re.fullmatch(r"v\d+", parts[-1]):
        parts = parts[:-1]          # drop the weights version (…/v1/)
    slug = "-".join(parts[:2]).lower()   # {family}-{model}
    slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", slug))
    return slug.strip("-")[:max_len].rstrip("-")


# e.g. synthetic-sdfb-v0-5-0-qwen3-4b-instruct-2507 (63-char Dataflow cap).
_base_job_name = f"{app_domain}-{app_name}-v{project_version.replace('.', '-').lower()}"
_slug = _model_slug(model_uri)
job_name = f"{_base_job_name}-{_slug}"[:63].rstrip("-") if _slug else _base_job_name
flex_template = f"sdfb-{project_version}-template.json"
dag_id = f"{app_domain}_{app_name}_{dag_version}"

devnetproxy_tag = "dev-env-{{ENV}}-project-code-3-proxy"
netseg_network_tag = "dev-env-{{ENV}}-project-code-3-net"
artifactory_network_tag = "dev-env-{{ENV}}-project-code-2-artifactory"
gke_network_tag = "int-{{ENV}}-project-code-serna-gke"
dataflow_network_tag = "int-{{ENV}}-project-code-serna-dataflow"
network_tags_chain = (
    f"{dataflow_network_tag};{netseg_network_tag};{artifactory_network_tag};"
    f"{gke_network_tag};{devnetproxy_tag}"
)
# -----------------------------------------------------------------------------
# DAG params — runtime-overridable on every trigger.
# Using Param objects so {{ params.X }} resolves to the VALUE, not the definition.
# All values are strings because Dataflow Flex Template parameters are strings.
# -----------------------------------------------------------------------------
default_dag_params = {
    "table_fqn": Param(
        default=default_table_fqn,
        type="string",
        description="FQN of the source BigQuery table to clone.",
    ),
    "num_rows": Param(
        default="1000",
        type="string",
        description="Number of synthetic rows to generate.",
    ),
    "engine": Param(
        default="b1_rag",
        type="string",
        enum=["b1_rag", "b2_library"],
        description="Generation engine.",
    ),
    "batch_size": Param(
        default="16",
        type="string",
        description="Records per LLM batch.",
    ),
    "similarity": Param(
        default="0.5",
        type="string",
        description="Similarity to reference (0.0 random → 1.0 mimic).",
    ),
    "identity_cols": Param(
        default="",
        type="string",
        description="Comma-separated per-row-unique columns synthesized "
                    "fresh each row (PK/UUID); never sampled from reference "
                    "data. Empty disables identity-column synthesis.",
    ),
    "vllm_dtype": Param(
        default="auto",
        type="string",
        enum=["auto", "float16", "bfloat16"],
        description="vLLM --dtype override. auto = checkpoint dtype (bf16 for "
                    "Gemma/Qwen). Set float16 for gpu=t4 + Qwen: Qwen ships "
                    "bf16 checkpoints (bf16 needs SM>=8.0) but is fp16-safe, "
                    "so the explicit downcast is what makes it run on Turing. "
                    "The worker refuses float16 for gemma-family checkpoints "
                    "(fp16 Gemma silently emits empty output).",
    ),
    "client_type": Param(
        default="vllm",
        type="string",
        enum=["vllm", "fake"],
        description="vllm = real LLM on a GPU worker (GPU chosen by the `gpu` "
                    "param); fake = CPU smoke (no GPU, e2 machine) that "
                    "exercises the full Dataflow→BigQuery write path while L4 "
                    "capacity is short.",
    ),
    "gpu": Param(
        default="t4",
        type="string",
        enum=["l4", "t4"],
        description="GPU profile when client_type=vllm. l4 = g2-standard-8 + "
                    "NVIDIA L4 (24GB) — the ONLY GPU in europe-west3 that runs "
                    "Gemma 4. t4 = n1-standard-8 + NVIDIA T4 (16GB): plumbing "
                    "profile. Gemma CANNOT run on T4 (bf16/SM7.5 + shared-memory "
                    "limits — the vLLM client now fails fast, see "
                    "handlers/vllm_client.py ModelGpuIncompatibleError); pair t4 "
                    "with the qwen3_4b_instruct_2507 registry model via "
                    "SDFB_MODEL_URI. Ignored when client_type=fake.",
    ),
}

with models.DAG(
        dag_id=dag_id,
        start_date=days_ago(1),
        schedule_interval="@once",
        catchup=False,
        max_active_runs=1,
        tags=["SYNTHETIC", "Dataflow", env_name.upper()],
        params=default_dag_params,
) as dag:
    DataflowStartFlexTemplateOperator(
        task_id=f"start_{app_name}",
        project_id=project_id,
        location=region,
        body={
            "launchParameter": {
                "containerSpecGcsPath": f"gs://{templates_path}/synthetic/{flex_template}",
                "jobName": job_name,
                "environment": {
                    "tempLocation": f"gs://{bucket_path}/temp/",
                    "stagingLocation": f"gs://{bucket_path}/staging",
                    "subnetwork": subnetwork,
                    "ipConfiguration": "WORKER_IP_PRIVATE",
                    "serviceAccountEmail": service_account,
                    "additionalExperiments": [
                        "use_runner_v2",
                        "upload_graph",
                        "enable_secure_boot",
                        # GPU accelerator, chosen by the `gpu` param when
                        # client_type=vllm (see docs/GPU_CONTAINER.md):
                        #   l4 → NVIDIA L4 (Gemma-4-capable),
                        #   t4 → NVIDIA T4 (plumbing smoke ONLY — Gemma 4 can't
                        #        run on Turing; see the `gpu` param docstring).
                        # The T4 profile pins `:5xx`, the driver the Dataflow
                        # vLLM notebook says is required to run vLLM jobs. In
                        # fake/CPU smoke mode this renders to a harmless
                        # duplicate of an existing experiment, so NO accelerator
                        # is requested (Dataflow dedupes it) — lets the BQ write
                        # path run on abundant CPU capacity when L4s are stocked
                        # out.
                        "{{ ('worker_accelerator=type:nvidia-l4;count:1;install-nvidia-driver' if params.gpu == 'l4' else 'worker_accelerator=type:nvidia-tesla-t4;count:1;install-nvidia-driver:5xx') if params.client_type == 'vllm' else 'upload_graph' }}",
                        # Capacity guarantee against the europe-west3 g2/L4
                        # STOCKOUT: consume a matching L4 reservation if one
                        # exists. ANY-reservation affinity → on-demand fallback
                        # when none matches, so this is INERT until the platform
                        # team creates the reservation (and an allowlist is
                        # granted for GPU-targeted Dataflow reservations). Rides
                        # additionalExperiments — the same proven channel as
                        # worker_accelerator above. CPU smoke path: harmless dup.
                        "{{ 'automatically_use_created_reservation' if params.client_type == 'vllm' else 'upload_graph' }}",
                        f"use_network_tags={network_tags_chain}",
                        f"use_network_tags_for_flex_templates={network_tags_chain}",
                    ],
                    "additionalUserLabels": {
                        "app": app_name,
                        "env": env_name,
                        "dag": dag_id,
                    },
                    "machineType": "{{ ('g2-standard-8' if params.gpu == 'l4' else 'n1-standard-8') if params.client_type == 'vllm' else 'e2-standard-8' }}",
                    "maxWorkers": 4,
                    # Worker boot disk is pinned in run_pipeline.configure_pipeline_options
                    # (_DEFAULT_WORKER_DISK_GB), NOT here: the Flex Template
                    # environment.diskSizeGb does not propagate to the worker harness
                    # (workers booted at the 25GB default despite a 200 here).
                    "workerRegion": region,
                },
                "parameters": {
                    "ddl_uri": "{{SDFB_DDL_URI}}",
                    "reference_table": "{{ params.table_fqn }}",
                    "reference_rows_limit": "10000",
                    "landing_table": "{{SDFB_LANDING_TABLE}}",
                    "dlq_table": "{{SDFB_DLQ_TABLE}}",
                    "num_rows": "{{ params.num_rows }}",
                    "batch_size": "{{ params.batch_size }}",
                    "similarity": "{{ params.similarity }}",
                    "run_id": "{{ dag_run.run_id }}",
                    "identity_cols": "{{ params.identity_cols }}",
                    "engine": "{{ params.engine }}",
                    "model_uri": model_uri,
                    "embedder_uri": embedder_uri,
                    "validation_runs_table": validation_runs_table,
                    "env": env_name,
                    "client_type": "{{ params.client_type }}",
                    "vllm_dtype": "{{ params.vllm_dtype }}",
                },
            }
        },
        do_xcom_push=True,
        wait_until_finished=False,
    )