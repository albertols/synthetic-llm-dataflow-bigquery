#  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""Airflow DAG — submit the synthetic-dataflow-bigquery Flex Template.

Runs in an Airflow/Cloud Composer environment. This file is the *template*:
workflow `3_import_dag.yaml` runs `sed` over it at deploy time to substitute
build-time values. Runtime values (table_fqn, num_rows, run_id) come from
Airflow DAG params and Airflow Variables — operators don't need to re-import
the DAG to change them.

Substitution markers (workflow 3 seds these at import time):
  {{PROJECT_VERSION}}         project version of the sdfb-beam package being deployed
  {{DAG_VERSION}}             {{PROJECT_VERSION}}_<ISO timestamp> (unique DAG id)
  {{ENV}}                     dev | uat | prd
  {{GCS_DATAFLOW_STAGING}}    <env>-…-dataflow-staging bucket name
  {{GCS_DATAFLOW_TEMPLATES}}  <env>-…-dataflow-templates bucket name
  {{GPU}}                     default of the `gpu` DAG param (l4 | t4; still runtime-overridable)
  {{SDFB_MODEL_URI}}          gs://<bucket>/synthetic/models/gemma4/…
  {{SDFB_EMBEDDER_URI}}       gs://<bucket>/synthetic/models/embedders/…
                              (B.1; empty ⇒ HashingEmbedder)
  {{SDFB_DEFAULT_TABLE_FQN}}  project.dataset.table
  {{SDFB_DDL_URI}}            gs://…/ddl.json (empty ⇒ operator omits ddl_uri;
                              live INFORMATION_SCHEMA extraction)
  {{SDFB_RAG_CHUNKS_TABLE}}   project.synthetic_rag.rag_chunks (B.1 chunk store;
                              empty ⇒ params omitted, no reuse/population)
  {{SDFB_FREETEXT_POOLS_TABLE}} project.synthetic_rag.freetext_pools (WS5 pool store;
                              empty ⇒ params omitted, pools rebuild per worker)
  {{SDFB_SOURCE_STATS_TABLE}}  project.synthetic_rag.source_table_stats (WS8 stats store;
                              empty ⇒ param omitted, stats land as milestone + JSON
                              artifact only)
  {{WRITE_DISPOSITION}}       default of the `write_disposition` DAG param (append | overwrite)
  {{SDFB_LANDING_TABLE}}      project.synthetic_data.<table> (defaults to the source table name)
  {{SDFB_DLQ_TABLE}}          project.synthetic_data_quality.dlq
  {{SDFB_VALIDATION_RUNS_TABLE}} project.synthetic_data_quality.validation_runs

Runtime values: Airflow DAG params ({{ params.* }}: table_fqn, num_rows,
engine, batch_size, similarity, client_type) + Airflow Variables for infra
(PROJECT_ID, REGION, DATAFLOW_SUBNET, SA_DATAFLOW, and the optional
DATAFLOW_NETWORK_TAGS) — changeable without re-importing. client_type=fake
selects a CPU smoke (no L4) — see below.

Opt-in evaluation (`run_evaluation`, default False), chained in THIS DAG::

    start_sdfb ─► run_evaluation_gate ─► wait_for_generation ─► trigger_evaluation
    (launch,      (skips the rest         (reschedule-mode        (launch, SAME
     no wait)      unless opted in)        sensor on the job)      template)

With `run_evaluation` False the gate skips everything after the generation
launch: the DAG runs `start_sdfb` and nothing else, as before. With it True
the sensor waits for the generation job to reach JOB_STATE_DONE (a job that
fails or is cancelled fails the sensor, at once or at its one-day timeout)
and `trigger_evaluation` launches the evaluation as a second, CPU-only
Dataflow job from the same Flex Template, with the template parameter
`sdfb_job=evaluation` (one image, one template, one DAG import; ADR 0041's
amendment of 2026-10-06). No other DAG is triggered, and no marker or Airflow
Variable is added for it. The sensor is not deferrable, so no triggerer is
needed.

Run slot: with `run_evaluation` true a run of this DAG stays running until the
generation job ends (the sensor waits for it, hours), and the DAG has
`max_active_runs=1`, so every later trigger queues behind it: launches run one
generation at a time. `max_active_runs` is the knob; raising it lets several
generation jobs run at once (mind the GPU quota). It is unchanged here.

`trigger_evaluation` evaluates what the launch generated: the launched table
(`seed_table`) and its enabled group of tables in the relationship model, or
that table alone; each landed table is read whole and compared with the table
of the same name in the source dataset. The comments inside its `parameters`
say, for every parameter, where the value comes from and what the evaluator
does with it, and the block above the operator lists the evaluator's options
this DAG leaves at their defaults. In short:

- It reads neither the generation job's log nor BigQuery job labels, so its
  service account needs no `roles/logging.viewer` and no
  `roles/bigquery.resourceViewer`. The generation job's id is passed as the
  launch's identity: the evaluator reads the Dataflow job resource and
  nothing else about the job (needs `roles/dataflow.viewer`; without it the
  run goes on with a warning and no window). With the window it also runs
  ONE query on the generator's `validation_runs` table (below).
- `scope=manual` reads each landing table WHOLE: with this DAG's
  `write_disposition` "overwrite" that is this launch's rows, with "append"
  it also includes the rows of earlier launches.
- `reference_rows_limit` limits nothing that is compared. It is the size of
  the sample the generator learned from, passed so the evaluator can rebuild
  that sample (and an equal-size holdout) for the reference-based privacy
  metrics: the nearest-neighbour ones (row.dcr_train_holdout_share,
  row.dcr_p5_ratio, row.nndr_p5_ratio, row.density, row.coverage) and the
  panel-based match rates and lifts (row.memorization_lift,
  row.exposure_lift among them). The rebuilt sample is only trusted when it
  matches the digest the generator recorded in its `validation_runs` row, so
  the launch also passes `validation_runs_table`: the evaluator reads this
  launch's rows of it (landing table and the job's window, one query) and
  those metrics are evaluated. Without that table, or when the job's window
  cannot be read, they come out "not evaluated" with the reason on the row.
  Every other metric is evaluated on all rows either way.
- A landing table named differently from its source (a custom
  `SDFB_LANDING_TABLE` name) is not found: the evaluation looks for the
  source's name in the landing dataset.
- What stays lost: the launch's log, so a model the launch adjusted
  (ADR 0038) is not seen and the table list is the seed's, not the launch's
  own record. The service account also needs read on the generator's
  `validation_runs` table.

`trigger_evaluation` submits the job and does not wait: the evaluation job
writes its own FINAL registry row. A job that dies after it was launched
leaves its RUNNING row open; `composer/evaluation_framework.py`, the
standalone DAG, is the path that waits for the job and closes that row.

None of this has been parsed or run by Airflow.

"""

# Heavy or optional dependencies are imported lazily, where they are used.
# pylint: disable=import-outside-toplevel

# f-string fields keep single quotes while Python 3.11 is supported;
# pylint on Python >= 3.12 reads those quotes as inconsistent.
# pylint: disable=inconsistent-quotes

from __future__ import annotations

from airflow import models
from airflow.models import Variable
from airflow.models.param import Param
from airflow.operators.python import ShortCircuitOperator
from airflow.providers.google.cloud.operators.dataflow import (
    DataflowStartFlexTemplateOperator,)
from airflow.providers.google.cloud.sensors.dataflow import (
    DataflowJobStatusSensor,)
from airflow.utils.dates import days_ago

# -----------------------------------------------------------------------------
# Build-time values — sed-substituted by workflow 3 at import (see docstring).
# -----------------------------------------------------------------------------
bucket_path = "{{GCS_DATAFLOW_STAGING}}"  # …-dataflow-staging
templates_path = "{{GCS_DATAFLOW_TEMPLATES}}"  # …-dataflow-templates
model_uri = "{{SDFB_MODEL_URI}}"  # gs://<bucket>/synthetic/models/gemma4/e4b-it/v1/
GPU_DEFAULT = "{{GPU}}"  # default of the `gpu` param (l4 | t4)
ENGINE_DEFAULT = "{{ENGINE}}"  # default of the `engine` param (b1_rag | b2_library)
embedder_uri = "{{SDFB_EMBEDDER_URI}}"  # B.1 embedder; empty ⇒ HashingEmbedder
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
# Optional: semicolon-separated VPC network tags for the launcher VM and the
# workers (firewall/egress rules keyed on tags). Empty ⇒ no tag experiments.
network_tags = Variable.get("DATAFLOW_NETWORK_TAGS", default_var="").strip()

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
    parts = parts[:-1]  # drop the weights version (…/v1/)
  slug = "-".join(parts[:2]).lower()  # {family}-{model}
  slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", slug))
  return slug.strip("-")[:max_len].rstrip("-")


# e.g. synthetic-sdfb-v0-5-0-qwen3-4b-instruct-2507 (63-char Dataflow cap).
_base_job_name = f"{app_domain}-{app_name}-v{project_version.replace('.', '-').lower()}"
_slug = _model_slug(model_uri)
job_name = f"{_base_job_name}-{_slug}"[:63].rstrip(
    "-") if _slug else _base_job_name
flex_template = f"sdfb-{project_version}-template.json"
dag_id = f"{app_domain}_{app_name}_{dag_version}"

network_tag_experiments = ([
    f"use_network_tags={network_tags}",
    f"use_network_tags_for_flex_templates={network_tags}",
] if network_tags else [])

# -----------------------------------------------------------------------------
# Opt-in evaluation (run_evaluation). Nothing below is used unless the gate
# lets the run through.
# -----------------------------------------------------------------------------
# The generation job's id: `start_sdfb` does not wait, so its XCom holds it.
generation_job_id = "{{ ti.xcom_pull(task_ids='start_sdfb')['id'] }}"
# The evaluation job's name. The launch appends the run's timestamp; capped
# so the whole name stays inside Dataflow's 63 characters.
_evaluation_job_base = f"{app_name}-evaluation-v{project_version.replace('.', '-').lower()}"
evaluation_job_name = _evaluation_job_base[:38].rstrip("-")
# How the sensor waits: one status read every two minutes, for at most a day
# (the longest generation runs take hours). Between reads the task is
# rescheduled and holds no worker slot.
GENERATION_POKE_SECONDS = 120
GENERATION_TIMEOUT_SECONDS = 86400
# Default of the `evaluation_output_dataset` param: the dataset of the
# validation-runs table above (`project.dataset.table`), so a deployment whose
# quality dataset has another name needs no extra setting.
_QUALIFIED_TABLE_PARTS = 3  # project, dataset, table
_validation_runs_parts = validation_runs_table.split(".")
evaluation_output_dataset_default = (
    _validation_runs_parts[1] if len(_validation_runs_parts)
    == _QUALIFIED_TABLE_PARTS else "synthetic_data_quality")
# The evaluation job's workers run the SAME image as the generation job's. It
# is multi-GB (torch, vLLM, the CUDA libraries) and Dataflow's 25 GB default
# boot disk overflows while a worker unpacks it; the generator pins 200 GB
# for its own workers in run_pipeline.py, the evaluator pins nothing, so this
# launch says it (Beam's own --disk_size_gb, passed through the template).
EVALUATION_WORKER_DISK_GB = "200"
# -----------------------------------------------------------------------------
# DAG params — runtime-overridable on every trigger.
# Using Param objects so {{ params.X }} resolves to the VALUE, not the definition.
# All values are strings because Dataflow Flex Template parameters are strings.
# -----------------------------------------------------------------------------
default_dag_params = {
    "table_fqn":
        Param(
            default=default_table_fqn,
            type="string",
            description="FQN of the source BigQuery table to clone.",
        ),
    "num_rows":
        Param(
            default="1000",
            type="string",
            description="Number of synthetic rows to generate.",
        ),
    "engine":
        Param(
            default=ENGINE_DEFAULT,
            type="string",
            enum=["b1_rag", "b2_library"],
            description="Generation engine.",
        ),
    "batch_size":
        Param(
            default="16",
            type="string",
            description="Records per LLM batch.",
        ),
    "similarity":
        Param(
            default="0.5",
            type="string",
            description="Similarity to reference (0.0 random → 1.0 mimic).",
        ),
    "identity_cols":
        Param(
            default="",
            type="string",
            description="Comma-separated per-row-unique columns synthesized "
            "fresh each row (PK/UUID); never sampled from reference "
            "data. Empty disables identity-column synthesis.",
        ),
    "pk_cols":
        Param(
            default="",
            type="string",
            description="Comma-separated declared primary-key columns. Duplicate "
            "PK tuples divert to the DLQ (rule_id=pk.duplicate, "
            "BLOCKER). Empty = PK undeclared, rule idle.",
        ),
    "vllm_dtype":
        Param(
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
    "vllm_max_model_len":
        Param(
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
    "client_type":
        Param(
            default="vllm",
            type="string",
            enum=["vllm", "fake"],
            description="vllm = real LLM on a GPU worker (GPU chosen by the `gpu` "
            "param); fake = CPU smoke (no GPU, e2 machine) that "
            "exercises the full Dataflow→BigQuery write path while L4 "
            "capacity is short.",
        ),
    "gpu":
        Param(
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
    "seed":
        Param(
            default="",
            type="string",
            description="Explicit base RNG seed (integer) for deliberate "
            "reproduction of a run. Empty (default) derives a fresh "
            "seed per (run_id, batch) — recommended.",
        ),
    "write_disposition":
        Param(
            default=WRITE_DISPOSITION_DEFAULT,
            type="string",
            enum=["append", "overwrite"],
            description="Landing-table write mode; overwrite = WRITE_TRUNCATE. "
            "Landing only — DLQ/quality/RAG tables always append.",
        ),
    "create_if_not_exists":
        Param(
            default="false",
            type="string",
            enum=["false", "true"],
            description="Create the landing table on first write "
            "(CREATE_IF_NEEDED + derived schema).",
        ),
    "build_rag_layer":
        Param(
            default="true",
            type="string",
            enum=["true", "false"],
            description="Populate the B.1 RAG chunk store from this run's "
            "reference sample (skipped in-launcher when the "
            "reference_digest already exists for the embedder "
            "id+version). Ignored when the build left "
            "SDFB_RAG_CHUNKS_TABLE empty.",
        ),
    "build_pool_layer":
        Param(
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
    "sdk_containers":
        Param(
            default="single",
            type="string",
            enum=["single", "multi"],
            description="SDK-container topology for vLLM launches (ADR 0034). "
            "single = one SDK process per worker "
            "(no_use_multiple_sdk_containers, RUN_PLAYBOOK §3): the "
            "generate stages run on ONE Python interpreter per worker "
            "(~1 of 8 vCPUs busy on the 2026-08-29 R6 pair). multi = "
            "Dataflow's default, one SDK process per vCPU; the "
            "vLLM client's cross-process spawn mutex keeps one "
            "server per worker. Ignored for client_type=fake.",
        ),
    "initial_workers":
        Param(
            default="",
            type="string",
            description="Initial Dataflow worker count (ADR 0034). Empty = "
            "Dataflow's default (2 on the 2026-08-29 R6 pair, "
            "autoscaled to 4 only ~4 min into the first generate "
            "stage). Pass '4' (= maxWorkers) for 10M-row runs so the "
            "first stage starts on the whole fleet. Never above "
            "maxWorkers.",
        ),
    "autoscaling":
        Param(
            default="auto",
            type="string",
            enum=["auto", "throughput", "fixed"],
            description="auto = a fleet sized by initial_workers stays that "
            "size (Dataflow autoscaling NONE), otherwise "
            "THROUGHPUT_BASED. fixed = same pin, initial_workers "
            "required. throughput = always scale (ADR 0034 D9: the "
            "2026-09-07/08 multi runs lost ~4 min per job to "
            "mid-job scale-downs between the parent and child "
            "stages).",
        ),
    "uniqueness_mode":
        Param(
            default="exact",
            type="string",
            enum=["exact", "exact_chained", "streaming"],
            description="exact = divert every duplicate to the DLQ behind ONE "
            "full-row shuffle barrier (PK/identity resolved from "
            "key-only groups, ADR 0034); no row lands until generation "
            "finishes. exact_chained = the pre-ADR-0034 three-barrier "
            "chain (row digest -> PK -> identity), kept for A/B runs. "
            "streaming = rows land AS GENERATED and the "
            "duplicate rate is measured instead of removed (WS6 W3). "
            "In streaming mode duplicate rows LAND — a failing gate "
            "still marks the run FAILED_BLOCKER; recover by "
            "re-triggering with write_disposition=overwrite.",
        ),
    "freetext_expansion":
        Param(
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
    "prompt_constraints":
        Param(
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
    "prompt_debug":
        Param(
            default="off",
            type="string",
            enum=["off", "redacted", "full"],
            description="Log each built pool prompt as a freetext_pool_prompt "
            "milestone (ADR 0024 §3c). redacted elides seed "
            "exemplars and adds a sha12 prompt hash; full logs "
            "verbatim prompts at WARNING — reference values reach "
            "Dataflow logs, short-lived debug runs only.",
        ),
    "source_stats":
        Param(
            default="sample",
            type="string",
            enum=["sample", "off"],
            description="Per-column source_table_stats from the reference "
            "sample, computed driver-side before the graph (WS8 "
            "spec WS-B; zero DAG cost). Ignored table-write-wise "
            "when the build left SDFB_SOURCE_STATS_TABLE empty.",
        ),
    "fk_parent_landing":
        Param(
            default="",
            type="string",
            description="EXPERT OVERRIDE only (ADR 0029): parents are assumed "
            "landed in the landing dataset and this derives "
            "automatically. Set only when parents land in a "
            "DIFFERENT project.dataset.",
        ),
    "multi_table_mode":
        Param(
            default="single_job",
            type="string",
            enum=["single_job", "sequential_jobs"],
            description="How a multi-table plan executes (ADR 0030). "
            "single_job (default): every planned table in ONE "
            "Dataflow job — one worker fleet, one vLLM ignition, "
            "in-DAG FK key handoff. sequential_jobs: one job per "
            "table, parents first (fallback/debugging).",
        ),
    "generate_fk_relationships":
        Param(
            default="true",
            type="string",
            enum=["true", "false"],
            description="true (default): declared relationships are honored — "
            "the launch expands to the table's whole FK component "
            "(parents first, derived automatically); tables with "
            "no relationships behave exactly as false. false: "
            "isolated generation — declared edges ignored LOUDLY, "
            "FK columns use marginals. ADR 0029.",
        ),
    "relationships_uri":
        Param(
            default="config/relationships",
            type="string",
            description="Where the relational models live (ADR 0032): a folder "
            "or a single YAML file, local or gs://. Default: the "
            "config/relationships folder packaged in the image. "
            "Point it at gs://... to change PK/FK/identity with no "
            "rebuild and no BigQuery metadata edit — it is the ONLY "
            "source of relational truth.",
        ),
    "run_evaluation":
        Param(
            default=False,
            type="boolean",
            description="Opt-in, off by default. False: the DAG only launches "
            "generation, as before. True: after the generation job reaches "
            "DONE this DAG launches the evaluator as a second, CPU-only "
            "Dataflow job from the same template. It evaluates the launched "
            "table and, with generate_fk_relationships true, the rest of "
            "its group of tables in the relationship model; each landed "
            "table is read whole (with write_disposition append that "
            "includes earlier launches' rows). While it waits the run holds "
            "the DAG's only active-run slot (max_active_runs=1), so launches "
            "of this DAG then run one generation at a time.",
        ),
    "evaluation_mode":
        Param(
            default="",
            type="string",
            enum=["", "exact", "sampled"],
            description="Only with run_evaluation. exact: every row of both "
            "sides is compared. sampled: a salted sample of a side that has "
            "more rows than the evaluator's sample_rows (200000). Empty "
            "(default): the evaluator's choice, exact on Dataflow.",
        ),
    "evaluation_machine_type":
        Param(
            default="e2-standard-8",
            type="string",
            description="Only with run_evaluation. Machine type of the "
            "evaluation job's workers. CPU only: the evaluator needs no GPU. "
            "Default e2-standard-8.",
        ),
    "evaluation_max_workers":
        Param(
            default=4,
            type="integer",
            minimum=1,
            description="Only with run_evaluation. Upper bound on the number "
            "of workers the evaluation job may use (Dataflow maxWorkers). "
            "Default 4.",
        ),
    "source_dataset":
        Param(
            default="",
            type="string",
            description="Only with run_evaluation. The dataset (or "
            "project.dataset) holding the SOURCE tables the landed ones are "
            "compared with: the launched table and, with "
            "generate_fk_relationships true, the rest of its group of "
            "tables, each under the name it landed with in the landing "
            "dataset (a landing table named differently from its source is "
            "not found). Empty (default): the dataset of table_fqn.",
        ),
    "evaluation_output_dataset":
        Param(
            default=evaluation_output_dataset_default,
            type="string",
            description="Only with run_evaluation. The dataset (or "
            "project.dataset) that receives the four evaluation_* tables, and "
            "where the evaluator keeps the expiring tables it makes while it "
            "runs (source snapshots, scopes, samples). The service account "
            "needs write access there. Default: the dataset of the "
            "validation-runs table, else synthetic_data_quality.",
        ),
    "pool_seed_strategy":
        Param(
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


def _run_evaluation_enabled(params, **_):
  """Gate for the opt-in evaluation: False skips every task after it."""
  return bool(params["run_evaluation"])


with models.DAG(
    dag_id=dag_id,
    start_date=days_ago(1),
    schedule_interval="@once",
    catchup=False,
    max_active_runs=1,
    tags=["SYNTHETIC", "Dataflow", env_name.upper()],
    params=default_dag_params,
) as dag:
  # The launch's parameters are kept one pair per line by hand, so the
  # formatter is off for this one statement (it would split each pair in two).
  # yapf: disable
  start_sdfb = DataflowStartFlexTemplateOperator(
      task_id=f"start_{app_name}",
      project_id=project_id,
      location=region,
      body={
          "launchParameter": {
              "containerSpecGcsPath":
                  f"gs://{templates_path}/synthetic/{flex_template}",
              "jobName":
                  job_name,
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
                      "{{ ('worker_accelerator=type:nvidia-l4;count:1;install-nvidia-driver' "
                      "if params.gpu == 'l4' else "
                      "'worker_accelerator=type:nvidia-tesla-t4;count:1;"
                      "install-nvidia-driver:5xx') "
                      "if params.client_type == 'vllm' else 'upload_graph' }}",
                      # Consumes a matching GPU reservation when one exists
                      # (on-demand otherwise). CPU smoke path: harmless dup.
                      "{{ 'automatically_use_created_reservation' "
                      "if params.client_type == 'vllm' else 'upload_graph' }}",
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
                      # ADR 0034: sdk_containers=multi lifts the pin —
                      # the vLLM client's cross-process spawn mutex
                      # (port 8001) keeps one server per worker while
                      # every vCPU gets its own Python interpreter.
                      "{{ 'no_use_multiple_sdk_containers' if (params.client_type == 'vllm' "
                      "and params.sdk_containers == 'single') else 'upload_graph' }}",
                      *network_tag_experiments,
                  ],
                  "additionalUserLabels": {
                      "app": app_name,
                      "env": env_name,
                      "dag": dag_id,
                  },
                  "machineType":
                      "{{ ('g2-standard-8' if params.gpu == 'l4' else 'n1-standard-8') "
                      "if params.client_type == 'vllm' else 'e2-standard-8' }}",
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
                  **({
                      "ddl_uri": _DDL_URI
                  } if _DDL_URI else {}),
                  # Omitted together when the build left the chunk-store
                  # marker empty — same off-state convention as ddl_uri.
                  **({
                      "rag_chunks_table": _RAG_CHUNKS_TABLE,
                      "build_rag_layer": "{{ params.build_rag_layer }}",
                  } if _RAG_CHUNKS_TABLE else {}),
                  # WS5 pool store — same off-state convention: both
                  # params omitted when the build left the marker empty.
                  **({
                      "freetext_pools_table": _FREETEXT_POOLS_TABLE,
                      "build_pool_layer": "{{ params.build_pool_layer }}",
                  } if _FREETEXT_POOLS_TABLE else {}),
                  # WS8 stats store — same off-state convention: the
                  # param is omitted when the build left the marker empty
                  # (stats still land as milestone + JSON artifact).
                  **({
                      "source_stats_table": _SOURCE_STATS_TABLE
                  } if _SOURCE_STATS_TABLE else {}),
                  "source_stats": "{{ params.source_stats }}",
                  "freetext_expansion": "{{ params.freetext_expansion }}",
                  "prompt_constraints": "{{ params.prompt_constraints }}",
                  "prompt_debug": "{{ params.prompt_debug }}",
                  "fk_parent_landing": "{{ params.fk_parent_landing }}",
                  "generate_fk_relationships": "{{ params.generate_fk_relationships }}",
                  "relationships_uri": "{{ params.relationships_uri }}",
                  "multi_table_mode": "{{ params.multi_table_mode }}",
                  "uniqueness_mode": "{{ params.uniqueness_mode }}",
                  "pool_seed_strategy": "{{ params.pool_seed_strategy }}",
                  "reference_table": "{{ params.table_fqn }}",
                  "reference_rows_limit": "10000",
                  "landing_table": "{{SDFB_LANDING_TABLE}}",
                  "dlq_table": "{{SDFB_DLQ_TABLE}}",
                  "num_rows": "{{ params.num_rows }}",
                  "initial_workers": "{{ params.initial_workers }}",
                  "autoscaling": "{{ params.autoscaling }}",
                  "batch_size": "{{ params.batch_size }}",
                  "similarity": "{{ params.similarity }}",
                  # Salted per trigger: retriggering the same logical date
                  # reuses dag_run.run_id, and seed=None derives batch seeds
                  # from run_id — identical run_id replayed identical data
                  # (E2E 2026-07-10). The uuid suffix also makes each
                  # validation_runs row uniquely attributable to one job.
                  "run_id":
                      "{{ dag_run.run_id }}-{{ macros.uuid.uuid4().hex[:8] }}",
                  "identity_cols":
                      "{{ params.identity_cols }}",
                  "pk_cols":
                      "{{ params.pk_cols }}",
                  "engine":
                      "{{ params.engine }}",
                  "model_uri":
                      model_uri,
                  "embedder_uri":
                      embedder_uri,
                  "validation_runs_table":
                      validation_runs_table,
                  "env":
                      env_name,
                  "client_type":
                      "{{ params.client_type }}",
                  "vllm_dtype":
                      "{{ params.vllm_dtype }}",
                  "vllm_max_model_len":
                      "{{ params.vllm_max_model_len }}",
                  "seed":
                      "{{ params.seed }}",
                  "write_disposition":
                      "{{ params.write_disposition }}",
                  "create_if_not_exists":
                      "{{ params.create_if_not_exists }}",
              },
          }
      },
      do_xcom_push=True,
      wait_until_finished=False,
  )
  # yapf: enable

  # Opt-in chaining (run_evaluation, default False): with it off the gate
  # skips every task below and the DAG behaves exactly as before. The launch
  # above does not wait, so its XCom holds the job id the sensor waits on.
  run_evaluation_gate = ShortCircuitOperator(
      task_id="run_evaluation_gate",
      python_callable=_run_evaluation_enabled,
  )

  # The landed tables are evaluated, so the generation job must be done. A
  # job that failed or was cancelled is expected to fail this task at once
  # (the provider's sensor raises on a terminal state it was not told to
  # expect; not verified against the installed provider). If it did not, the
  # task would fail at its timeout: either way nothing is evaluated. Not
  # deferrable (the default; `deferrable` is not passed, as older providers
  # lack that argument), so the environment needs no triggerer.
  wait_for_generation = DataflowJobStatusSensor(
      task_id="wait_for_generation",
      job_id=generation_job_id,
      expected_statuses={"JOB_STATE_DONE"},
      project_id=project_id,
      location=region,
      mode="reschedule",
      poke_interval=GENERATION_POKE_SECONDS,
      timeout=GENERATION_TIMEOUT_SECONDS,
  )

  # The evaluation: a second Dataflow job from the SAME template as the
  # generation above. `sdfb_job` selects the evaluator's entry in the image.
  # What to evaluate is named by the launched table and the relationship model
  # and read whole (scope manual). Submitted and not waited for, like the
  # generation: the job writes its own FINAL row.
  #
  # Evaluator options this launch does NOT pass (each keeps its default).
  # The template accepts them; add one to `parameters` below to change it.
  # The full list is the "Evaluation only" entries of
  # docker/flex_template_metadata.json (and `sdfb-eval run --help`, in
  # packages/sdfb-evaluation/src/sdfb_evaluation/cli/main.py):
  #   sample_rows 200000           sampled mode only: a side with more rows
  #                                than this is read as a salted sample
  #   privacy_sample_rows 50000    rows of the nearest-neighbour privacy sample
  #   detection_sample_rows 50000  rows per side of the real-vs-synthetic
  #                                classifier (detection) sample
  #   pair_max_columns 20          columns whose pairs are compared, per table
  #   row_flags_top_k 100          row flags kept per check and table
  #   row_flags_source_keys hashed the matched source key is written as a keyed
  #                                hash only (the one accepted value)
  #   max_bytes_billed 1 TiB       BigQuery bytes the evaluation may process
  #   max_shuffle_gb 500           shuffle the value census may use before it
  #                                is value-sampled
  #   temp_dataset                 where the expiring scope, snapshot and
  #                                sample tables go; empty: output_dataset
  #   sink bq                      the pipeline loads BigQuery itself (the
  #                                other sinks need a local runner)
  #   allow_contaminated false     refuse a scope another writer touched
  #   thresholds_uri               a gs:// YAML of warn/fail thresholds that
  #                                override the catalogue's; empty: catalogue
  #   label_key_uri                Secret Manager version or gs:// object for
  #                                the row-flag label key; empty: random, for
  #                                one run
  #   fail_on none                 no effect on a template launch (the entry
  #                                submits and returns); read the statuses
  #                                from the registry
  # Not passed because they are other ways to name a launch: run_id and
  # tables (alternatives to seed_table).
  trigger_evaluation = DataflowStartFlexTemplateOperator(
      task_id="trigger_evaluation",
      project_id=project_id,
      location=region,
      body={
          "launchParameter": {
              "containerSpecGcsPath":
                  f"gs://{templates_path}/synthetic/{flex_template}",
              "jobName":
                  f"{evaluation_job_name}-{{{{ ts_nodash | lower }}}}",
              # How Dataflow runs the job (not the evaluator's arguments).
              "environment": {
                  # Dataflow's scratch and staging locations; the staging
                  # bucket also receives the job graph, which embeds the
                  # reference panel rows of every table.
                  "tempLocation": f"gs://{bucket_path}/temp/",
                  "stagingLocation": f"gs://{bucket_path}/staging",
                  # The generation job's network, with private worker IPs.
                  "subnetwork": subnetwork,
                  "ipConfiguration": "WORKER_IP_PRIVATE",
                  # The generation job's service account (Variable
                  # SA_DATAFLOW): it needs the evaluator's roles.
                  "serviceAccountEmail": service_account,
                  # A CPU job: no accelerator, no reservation, no
                  # SDK-container pin. The evaluator adds its own
                  # launch experiments. use_runner_v2 and enable_secure_boot
                  # are the generation job's; the network tags are the
                  # DATAFLOW_NETWORK_TAGS Variable, as for generation.
                  "additionalExperiments": [
                      "use_runner_v2",
                      "enable_secure_boot",
                      *network_tag_experiments,
                  ],
                  # Labels on the Dataflow job, to find it in billing and the
                  # console.
                  "additionalUserLabels": {
                      "app": app_name,
                      "env": env_name,
                      "dag": dag_id,
                  },
                  # DAG params evaluation_machine_type / evaluation_max_workers
                  "machineType": "{{ params.evaluation_machine_type }}",
                  "maxWorkers": "{{ params.evaluation_max_workers }}",
                  "workerRegion": region,
              },
              # Names are the template's (docker/flex_template_metadata.json).
              # The launcher supplies runner, project and region itself and
              # the image carries its worker image coordinate.
              "parameters": {
                  # Selects the evaluator inside the shared image; without it
                  # the same template would start a generation job.
                  "sdfb_job":
                      "evaluation",
                  # The generation job's Dataflow id: the same expression the
                  # wait sensor uses (the XCom `id` of start_sdfb). It is the
                  # launch's IDENTITY, not a source of tables: the evaluator
                  # reads the Dataflow job resource (id, name, region, start
                  # and end) and nothing else about the job, never its log.
                  # The row keeps the id and the window, so the run is listed
                  # in the view evaluation_latest_per_job. The window also
                  # pins each source table as of the job's create time (a
                  # snapshot, within the table's time-travel window). If the
                  # job cannot be read (no roles/dataflow.viewer, or past
                  # retention) the run goes on with a warning and no window
                  # (and then validation_runs is not read either).
                  "generation_job_id":
                      generation_job_id,
                  # The launched table: the last part of the DAG param
                  # table_fqn. It names the tables to evaluate: with a
                  # relationship model, this table and its enabled group;
                  # else this table alone.
                  "seed_table":
                      "{{ params.table_fqn.rsplit('.', 1)[-1] }}",
                  # The relationship model, the DAG param of the same name
                  # (default: the config/relationships folder packaged in the
                  # image). The evaluator reads the seed's enabled group of
                  # tables from it, parents first. A folder with no model
                  # file, or no model naming the seed, evaluates the seed
                  # alone (with a warning). Empty when the DAG param
                  # generate_fk_relationships is "false": an isolated
                  # generation is evaluated as one table.
                  "relationships_uri":
                      "{{ params.relationships_uri "
                      "if params.generate_fk_relationships == 'true' "
                      "else '' }}",
                  # Where the synthetic tables landed: the project.dataset of
                  # the generation's landing table (marker SDFB_LANDING_TABLE,
                  # whatever table name it ends in). Each table is read as
                  # <landing_dataset>.<its source name>, so a landing table
                  # named differently from its source is not found.
                  "landing_dataset":
                      "{{ '{{SDFB_LANDING_TABLE}}'.rsplit('.', 1)[0] }}",
                  # Where the SOURCE tables are: the DAG param source_dataset,
                  # else the dataset of table_fqn. Each synthetic table is
                  # compared with the table of the same name here.
                  "reference_dataset":
                      "{{ params.source_dataset or params.table_fqn.rsplit('.', 1)[0] }}",
                  # How the landing rows are isolated. A named table has no
                  # write disposition to derive a scope from (auto would plan
                  # every table as "not evaluated"), so manual: each landing
                  # table is read WHOLE. With write_disposition overwrite
                  # that is this launch's rows; with append it also holds
                  # earlier launches' rows.
                  "scope":
                      "manual",
                  # The size of the sample the generator learned from (the
                  # `reference_rows_limit` start_sdfb passes: SELECT ... LIMIT
                  # N on the reference table). It limits NOTHING that is
                  # compared: in exact mode every landed row is compared with
                  # every source row. The evaluator uses it to rebuild that
                  # sample (R) and an equal-size holdout (H) from the source,
                  # for the reference-based privacy metrics: the
                  # nearest-neighbour ones (row.dcr_train_holdout_share,
                  # row.dcr_p5_ratio, row.nndr_p5_ratio, row.density,
                  # row.coverage) and the panel-based match rates and lifts
                  # (row.memorization_lift and row.exposure_lift among them).
                  # R and H must be the same size, so the source table needs
                  # at least twice this many rows; with fewer, the
                  # nearest-neighbour metrics are not evaluated. Each half
                  # needs at least 2000 rows, and the value must equal the
                  # generation's. The rebuilt sample is only
                  # trusted when it matches the generator's recorded digest;
                  # see validation_runs_table below.
                  "reference_rows_limit":
                      "10000",
                  # The generator's validation-runs table: the same value
                  # start_sdfb passes (the marker SDFB_VALIDATION_RUNS_TABLE).
                  # With the job's window known, the evaluator reads THIS
                  # launch's rows of it (the rows created for the landing
                  # tables inside the window): one query, no log. The
                  # reference_digest on a row is what verifies the rebuilt
                  # sample, which is what lets the reference-based privacy
                  # metrics (listed at reference_rows_limit) be evaluated;
                  # it also brings the run ids. Needs read on this table.
                  # If the table cannot be read, or the window is unknown
                  # (the Dataflow job could not be read), the run goes on
                  # with a warning and those metrics are "not evaluated"
                  # with the reason on the row. If the window holds rows of
                  # several launches, the latest launch's rows are used and
                  # a warning says so.
                  "validation_runs_table":
                      validation_runs_table,
                  # Recorded in the registry row: what started the
                  # evaluation (cli | composer | chained | agent).
                  "trigger":
                      "chained",
                  # The DAG param evaluation_mode. Empty: exact on Dataflow
                  # (every row of both sides); sampled: a salted sample of a
                  # side with more rows than sample_rows (200000).
                  "mode":
                      "{{ params.evaluation_mode }}",
                  # The DAG param evaluation_output_dataset: where the four
                  # evaluation_* tables are written, and (unless temp_dataset
                  # is set) the evaluator's expiring scope, snapshot and
                  # sample tables.
                  "output_dataset":
                      "{{ params.evaluation_output_dataset }}",
                  # Beam's own --disk_size_gb, passed through: the workers'
                  # boot disk in GB. The evaluation runs on the generation's
                  # multi-GB image and the evaluator pins no disk of its own.
                  "disk_size_gb":
                      EVALUATION_WORKER_DISK_GB,
              },
          }
      },
      do_xcom_push=True,
      wait_until_finished=False,
  )

  start_sdfb >> run_evaluation_gate >> wait_for_generation >> trigger_evaluation  # pylint: disable=pointless-statement  # Airflow chain
