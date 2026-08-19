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
  {{GPU}}                     default of the `gpu` DAG param (l4 | t4; still runtime-overridable)
  {{SDFB_MODEL_URI}}          gs://<bucket>/synthetic/models/gemma4/…
  {{SDFB_EMBEDDER_URI}}       gs://<bucket>/synthetic/models/embedders/… (B.1; empty ⇒ HashingEmbedder)
  {{SDFB_DEFAULT_TABLE_FQN}}  project.dataset.table
  {{SDFB_DDL_URI}}            gs://…/ddl.json (empty ⇒ operator omits ddl_uri; live INFORMATION_SCHEMA extraction)
  {{SDFB_RAG_CHUNKS_TABLE}}   project.synthetic_rag.rag_chunks (B.1 chunk store; empty ⇒ params omitted, no reuse/population)
  {{SDFB_FREETEXT_POOLS_TABLE}} project.synthetic_rag.freetext_pools (WS5 pool store; empty ⇒ params omitted, pools rebuild per worker)
  {{SDFB_SOURCE_STATS_TABLE}}  project.synthetic_rag.source_table_stats (WS8 stats store; empty ⇒ param omitted, stats land as milestone + JSON artifact only)
  {{WRITE_DISPOSITION}}       default of the `write_disposition` DAG param (append | overwrite)
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
GPU_DEFAULT = "{{GPU}}"                         # default of the `gpu` param (l4 | t4)
ENGINE_DEFAULT = "{{ENGINE}}"                   # default of the `engine` param (b1_rag | b2_library)
embedder_uri = "{{SDFB_EMBEDDER_URI}}"          # B.1 embedder; empty ⇒ HashingEmbedder
default_table_fqn = "{{SDFB_DEFAULT_TABLE_FQN}}"
validation_runs_table = "{{SDFB_VALIDATION_RUNS_TABLE}}"  # synthetic_data_quality.validation_runs
# Landing write mode default — build-time marker (workflow 3 input), runtime
# overridable via the write_disposition DAG param on every trigger.
WRITE_DISPOSITION_DEFAULT = "{{WRITE_DISPOSITION}}"
# Empty ⇒ the DAG omits the ddl_uri Flex parameter entirely and the launcher
# live-extracts the schema from the source table (WS4 §6b).
_DDL_URI = "{{SDFB_DDL_URI}}"
# B.1 RAG chunk store (WS2). Empty ⇒ the DAG omits rag_chunks_table AND
# build_rag_layer entirely: the launcher re-embeds per worker, no persisted
# chunk reuse (the 2026-07-23 run spent 158s re-embedding for this reason).
_RAG_CHUNKS_TABLE = "{{SDFB_RAG_CHUNKS_TABLE}}"
# WS5/ADR 0020 free-text pool store. Empty ⇒ the DAG omits
# freetext_pools_table AND build_pool_layer entirely: every worker PROCESS
# then rebuilds its pools in DoFn.setup() — the 2026-07-27_10_42_52 run
# paid 45 rebuilds (~26 of 53 min) exactly this way, warning
# freetext_pool_store_absent 25 times.
_FREETEXT_POOLS_TABLE = "{{SDFB_FREETEXT_POOLS_TABLE}}"
# WS8 source_table_stats store (2026-08-05 spec WS-B). Empty ⇒ the DAG omits
# source_stats_table entirely: stats still compute driver-side and land as
# the source_table_stats milestone + optional JSON artifact — only the BQ
# persistence is skipped. The table is NEVER auto-created (bq mk from
# config/bq_schema/synthetic_rag/source_table_stats.schema.json).
_SOURCE_STATS_TABLE = "{{SDFB_SOURCE_STATS_TABLE}}"

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
        default=ENGINE_DEFAULT,
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
    "pk_cols": Param(
        default="",
        type="string",
        description="Comma-separated declared primary-key columns. Duplicate "
                    "PK tuples divert to the DLQ (rule_id=pk.duplicate, "
                    "BLOCKER). Empty = PK undeclared, rule idle.",
    ),
    "vllm_dtype": Param(
        default="float16",
        type="string",
        enum=["auto", "float16", "bfloat16"],
        description="vLLM --dtype override. auto = checkpoint dtype (bf16 for "
                    "Gemma/Qwen). Set float16 for gpu=t4 + Qwen: Qwen ships "
                    "bf16 checkpoints (bf16 needs SM>=8.0) but is fp16-safe, "
                    "so the explicit downcast is what makes it run on Turing. "
                    "The worker refuses float16 for gemma-family checkpoints "
                    "(fp16 Gemma silently emits empty output).",
    ),
    "vllm_max_model_len": Param(
        default="8192",
        type="string",
        description="vLLM --max-model-len cap. Without it vLLM sizes the KV "
                    "cache for the checkpoint's NATIVE context — Qwen3-2507 "
                    "ships 262K, which needs a 36GiB KV cache and kills the "
                    "T4 EngineCore at startup (E2E 2026-07-14). 8192 fits "
                    "every registry model/GPU pairing (config/models.yml "
                    "defaults) and dwarfs the synthesis prompts. Empty = no "
                    "cap (checkpoint-native context).",
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
        default=GPU_DEFAULT,
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
    "seed": Param(
        default="",
        type="string",
        description="Explicit base RNG seed (integer) for deliberate "
                    "reproduction of a run. Empty (default) derives a fresh "
                    "seed per (run_id, batch) — recommended.",
    ),
    "write_disposition": Param(
        default=WRITE_DISPOSITION_DEFAULT,
        type="string",
        enum=["append", "overwrite"],
        description="Landing-table write mode; overwrite = WRITE_TRUNCATE. "
                    "Landing only — DLQ/quality/RAG tables always append.",
    ),
    "create_if_not_exists": Param(
        default="false",
        type="string",
        enum=["false", "true"],
        description="Create the landing table on first write "
                    "(CREATE_IF_NEEDED + derived schema).",
    ),
    "build_rag_layer": Param(
        default="true",
        type="string",
        enum=["true", "false"],
        description="Populate the B.1 RAG chunk store from this run's "
                    "reference sample (skipped in-launcher when the "
                    "reference_digest already exists for the embedder "
                    "id+version). Ignored when the build left "
                    "SDFB_RAG_CHUNKS_TABLE empty.",
    ),
    "build_pool_layer": Param(
        default="true",
        type="string",
        enum=["true", "false"],
        description="Build free-text pools in their own branch and persist "
                    "them to synthetic_rag.freetext_pools (skipped "
                    "in-launcher when this reference_digest + model_uri is "
                    "already present — so 'true' is safe to leave on). "
                    "Ignored when the build left SDFB_FREETEXT_POOLS_TABLE "
                    "empty. The table is NEVER auto-created: bq mk it from "
                    "config/bq_schema/synthetic_rag/freetext_pools.schema.json.",
    ),
    "uniqueness_mode": Param(
        default="exact",
        type="string",
        enum=["exact", "streaming"],
        description="exact = divert every duplicate to the DLQ behind up to "
                    "three shuffle barriers; no row lands until generation "
                    "finishes. streaming = rows land AS GENERATED and the "
                    "duplicate rate is measured instead of removed (WS6 W3). "
                    "In streaming mode duplicate rows LAND — a failing gate "
                    "still marks the run FAILED_BLOCKER; recover by "
                    "re-triggering with write_disposition=overwrite.",
    ),
    "freetext_expansion": Param(
        default="identifiers",
        type="string",
        enum=["off", "identifiers", "all"],
        description="Shape-preserving expander (WS8 spec C3). off = pool "
                    "draws only, synthetic distinct is capped at the pool "
                    "size (the 2026-08-03 10M-run ceiling). identifiers "
                    "(default) = code-like columns (no whitespace, >=2 "
                    "varying positions) draw fresh values from their "
                    "observed shape mix — distinct scales with rows, shape "
                    "precision stays 1.0 by construction. all = additionally "
                    "mutates digit runs inside texty pool draws. Zero LLM "
                    "calls added on every setting.",
    ),
    "prompt_constraints": Param(
        default="on",
        type="string",
        enum=["on", "off"],
        description="Attach each column's llm_prompt_constraint (parsed "
                    "from its DDL description JSON) to the pool prompt as a "
                    "constant suffix (WS8 spec C5, prefix-cache-safe). "
                    "Columns without a constraint are untouched; with none "
                    "anywhere 'on' is a logged no-op — prompt refinement, "
                    "never a requirement.",
    ),
    "prompt_debug": Param(
        default="off",
        type="string",
        enum=["off", "redacted", "full"],
        description="Log each built pool prompt as a freetext_pool_prompt "
                    "milestone (ADR 0024 §3c). redacted elides seed "
                    "exemplars and adds a sha12 prompt hash; full logs "
                    "verbatim prompts at WARNING — reference values reach "
                    "Dataflow logs, short-lived debug runs only.",
    ),
    "source_stats": Param(
        default="sample",
        type="string",
        enum=["sample", "off"],
        description="Per-column source_table_stats from the reference "
                    "sample, computed driver-side before the graph (WS8 "
                    "spec WS-B; zero DAG cost). Ignored table-write-wise "
                    "when the build left SDFB_SOURCE_STATS_TABLE empty.",
    ),
    "fk_parent_landing": Param(
        default="",
        type="string",
        description="project.dataset of already-landed synthetic parent "
                    "tables (ADR 0021). Set by run_tableset.py-style "
                    "multi-table triggers so child FK columns sample the "
                    "parents' landed keys; empty = FK pools off.",
    ),
    "pool_seed_strategy": Param(
        default="centroid",
        type="string",
        enum=["centroid", "kcenter", "kcenter_rotate"],
        description="How the 8 free-text prompt seeds are chosen (WS5 §3). "
                    "centroid = control (today). kcenter = seeds span the "
                    "column's modes. kcenter_rotate = re-seeded per ladder "
                    "attempt (forfeits vLLM prefix caching by design). "
                    "Change ONE arm per run, with the pool digest cleared "
                    "first (RUN_PLAYBOOK §6c), or the arm measures nothing.",
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
                        # client_type=vllm (see docker/Dockerfile + ADR 0009):
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
                        # ONE SDK process per GPU worker (RUN_PLAYBOOK §3).
                        # Runner v2's default spawns one sibling SDK process
                        # per vCPU (8 on n1/g2-standard-8) and EVERY sibling
                        # runs DoFn setup(): its own 7.5GB weight pull, its
                        # own vLLM spawn into the single GPU (all but the
                        # first die with CUDA OOM), its own CPU embedding pass
                        # fighting the other seven for the same 8 vCPUs. Each
                        # Dataflow bundle retry lands on a DIFFERENT sibling
                        # and repeats the full ~1.5h setup — the 2026-07-16
                        # run burned 5.4h across 4 retries on exactly this
                        # (job ..._13_23_14-11053114042412770609). The CPU
                        # smoke keeps the default: siblings parallelize the
                        # fake path — harmless duplicate again.
                        "{{ 'no_use_multiple_sdk_containers' if params.client_type == 'vllm' else 'upload_graph' }}",
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
                    # Omitted entirely when the build left the DDL marker
                    # empty — the launcher then live-extracts (WS4 §6b). An
                    # empty-string value would fail the template's ddl_uri
                    # regex, so omission (not "") is the off state.
                    **({"ddl_uri": _DDL_URI} if _DDL_URI else {}),
                    # Omitted together when the build left the chunk-store
                    # marker empty — same off-state convention as ddl_uri.
                    **(
                        {
                            "rag_chunks_table": _RAG_CHUNKS_TABLE,
                            "build_rag_layer": "{{ params.build_rag_layer }}",
                        }
                        if _RAG_CHUNKS_TABLE
                        else {}
                    ),
                    # WS5 pool store — same off-state convention: both
                    # params omitted when the build left the marker empty.
                    **(
                        {
                            "freetext_pools_table": _FREETEXT_POOLS_TABLE,
                            "build_pool_layer": "{{ params.build_pool_layer }}",
                        }
                        if _FREETEXT_POOLS_TABLE
                        else {}
                    ),
                    # WS8 stats store — same off-state convention: the
                    # param is omitted when the build left the marker empty
                    # (stats still land as milestone + JSON artifact).
                    **(
                        {"source_stats_table": _SOURCE_STATS_TABLE}
                        if _SOURCE_STATS_TABLE
                        else {}
                    ),
                    "source_stats": "{{ params.source_stats }}",
                    "freetext_expansion": "{{ params.freetext_expansion }}",
                    "prompt_constraints": "{{ params.prompt_constraints }}",
                    "prompt_debug": "{{ params.prompt_debug }}",
                    "fk_parent_landing": "{{ params.fk_parent_landing }}",
                    "uniqueness_mode": "{{ params.uniqueness_mode }}",
                    "pool_seed_strategy": "{{ params.pool_seed_strategy }}",
                    "reference_table": "{{ params.table_fqn }}",
                    "reference_rows_limit": "10000",
                    "landing_table": "{{SDFB_LANDING_TABLE}}",
                    "dlq_table": "{{SDFB_DLQ_TABLE}}",
                    "num_rows": "{{ params.num_rows }}",
                    "batch_size": "{{ params.batch_size }}",
                    "similarity": "{{ params.similarity }}",
                    # Salted per trigger: retriggering the same logical date
                    # reuses dag_run.run_id, and seed=None derives batch seeds
                    # from run_id — identical run_id replayed identical data
                    # (E2E 2026-07-10). The uuid suffix also makes each
                    # validation_runs row uniquely attributable to one job.
                    "run_id": "{{ dag_run.run_id }}-{{ macros.uuid.uuid4().hex[:8] }}",
                    "identity_cols": "{{ params.identity_cols }}",
                    "pk_cols": "{{ params.pk_cols }}",
                    "engine": "{{ params.engine }}",
                    "model_uri": model_uri,
                    "embedder_uri": embedder_uri,
                    "validation_runs_table": validation_runs_table,
                    "env": env_name,
                    "client_type": "{{ params.client_type }}",
                    "vllm_dtype": "{{ params.vllm_dtype }}",
                    "vllm_max_model_len": "{{ params.vllm_max_model_len }}",
                    "seed": "{{ params.seed }}",
                    "write_disposition": "{{ params.write_disposition }}",
                    "create_if_not_exists": "{{ params.create_if_not_exists }}",
                },
            }
        },
        do_xcom_push=True,
        wait_until_finished=False,
    )