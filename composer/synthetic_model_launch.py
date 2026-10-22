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
"""Airflow DAG — generate every enabled table of one relationships model
(ADR 0045).

A template like `composer/synthetic_beam_bigquery.py`: workflow
`3_import_dag.yaml` seds the same build-time markers into it (this file
adds none) and imports it with `composer/sdfb_batches.py`, the planner this
DAG calls — it cannot import sdfb-core (RUN_PLAYBOOK §0b). Runtime values
come from the DAG params and the house Airflow Variables, plus one of its
own:

    SDFB_MAX_PARALLEL_JOBS   how many batch jobs run at once (default 1).
                             Read when Composer parses this file: Airflow
                             fixes a mapped task's concurrency then, so a
                             change takes effect at the next parse, without
                             a re-import.

Manual trigger only: the DAG has no schedule. Composer imports it with
`relationships_uri` empty, so a run started by the import would fail in
`plan`.

The chain::

    plan ─► start_batch[b00 … bNN] ─┬─► collect_landed ─► trigger_evaluation
            mapped: one Flex launch │   the ids of the    one CPU job:
            per batch, at most      │   jobs that         --run_id <base>
            SDFB_MAX_PARALLEL_JOBS  │   reached DONE      --generation_job_ids
            at once, each waiting   │                            │
            for its own job         │                            ▼
                                    │               wait_for_evaluation_launch
                                    └─► fail_if_a_batch_failed

`plan` reads the model file — and the config file, whose keys win — from
GCS, plans the batches with `sdfb_batches` (the plan each batch's launcher
recomputes and checks against `batch_count`), mints the launch's base run
id and builds one Flex Template launch body per batch: `tables=all`,
`batch_index`, `batch_count`, `run_id=<base>`, the job named
`<job_name>-bNN`, to which the provider appends `-<8 hex>` (its
`append_job_name`, as on the single-table DAG's launches). The bodies are
`plan`'s return value, which `start_batch` maps over; the base run id, the
batch count, the model file, the batches and the banner are pushed under
their own keys. A batch job that ends in any state but DONE fails its own
mapped instance; the other batches run on. `collect_landed` runs after
every batch and hands the evaluation the ids of the jobs that landed. The
evaluation reads those jobs' rows of `validation_runs` by the base run id,
so every table is compared with the source its row names, a `source_table`
override included. With `run_evaluation` off, or no batch landed, the
evaluation is skipped.

Trigger rules, and why::

    task                        rule          why
    ──────────────────────────  ────────────  ──────────────────────────────
    plan                        all_success   the default
    start_batch[i]              all_success   mapped instances run on their
                                              own: one that fails stops no
                                              other (the DAG sets no
                                              fail_stop)
    collect_landed              all_done      runs once every instance has
                                              ended, the failed ones too;
                                              only an instance whose job
                                              reached DONE returned a job id
    trigger_evaluation          all_success   skipped with collect_landed
    wait_for_evaluation_launch  all_success   (evaluation off, nothing
                                              landed)
    fail_if_a_batch_failed      one_failed    runs and fails once an instance
                                              has failed (or `plan` has, which
                                              leaves start_batch
                                              upstream-failed); skipped when
                                              every instance succeeded

Airflow ends a run once none of its task instances is unfinished, and fails
it when a leaf failed. The leaves are `wait_for_evaluation_launch` and
`fail_if_a_batch_failed`: after a failed batch the watcher fails at once,
the run stays running until the evaluation's launch has been waited for,
and then it ends failed. With every batch landed the watcher is skipped and
the run ends as the evaluation's launch did.

Retries: `plan` and `start_batch` pass `retries=0`; every other task
leaves them to the environment (Cloud Composer's default is two, five
minutes apart), as the single-table DAG does. A PlanError (the model, the
config file, K) fails the same way every time, so the run fails at once. A
batch instance waits for its job, so a retry would launch the whole batch
again: under the same base run id, which replays the same rows, and after a
BLOCKER, which fails the job once its rows have loaded, so an append launch
would land them twice. To run a failed batch again, fix the cause and clear
its `start_batch` instance with its downstream tasks (the same body again:
under append, rows the failed job landed stay).

Each running instance holds an Airflow worker slot until its job ends: the
operator is not deferrable (`deferrable` is not passed; the provider's
default is off unless the environment sets `[operators]
default_deferrable`). Killing a running instance — a Composer worker
restart (an environment update, a pod eviction), or marking it failed —
cancels that batch's Dataflow job: the operator's `on_kill` cancels the job
it waits for. With `retries=0` the instance then stays failed.

The evaluation's failure close (`_close_running_row`, the RUNNING row of a
launch that died) is the single-table DAG's, with the row found by the
launch's base run id: its statement, constants and helpers are a COPY,
pinned equal by test — Composer imports each DAG file on its own.

Unverified against an installed provider and Airflow (read in their
documentation and source, not run): that Airflow parses this file at all
(the tests read it with `ast`); a mapped `DataflowStartFlexTemplateOperator`
with `wait_until_finished=True` holding its slot until its job ends and
failing on any other terminal state; `xcom_pull(task_ids="start_batch")`
returning the mapped return values in map order without the failed
instances; `all_done` and `one_failed` counting every mapped instance of
`start_batch`. The corp run of RUN_PLAYBOOK §2e checks them.
"""

# Heavy or optional dependencies are imported lazily, where they are used.
# pylint: disable=import-outside-toplevel

# f-string fields keep single quotes: yapf 0.43 cannot parse PEP 701
# quote reuse, and pylint reads those quotes as inconsistent.
# pylint: disable=inconsistent-quotes

from __future__ import annotations

import re

from airflow import models
from airflow.decorators import task
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.models import Variable
from airflow.models.param import Param
from airflow.providers.google.cloud.operators.dataflow import (
    DataflowStartFlexTemplateOperator,)
from airflow.providers.google.cloud.sensors.dataflow import (
    DataflowJobStatusSensor,)
from airflow.utils.dates import days_ago
from airflow.utils.trigger_rule import TriggerRule

# The planner, imported from beside this file in the DAGs folder.
import sdfb_batches

# -----------------------------------------------------------------------------
# Build-time values — sed-substituted by workflow 3 at import.
# -----------------------------------------------------------------------------
bucket_path = "{{GCS_DATAFLOW_STAGING}}"  # …-dataflow-staging
templates_path = "{{GCS_DATAFLOW_TEMPLATES}}"  # …-dataflow-templates
model_uri = "{{SDFB_MODEL_URI}}"  # gs://<bucket>/synthetic/models/gemma4/e4b-it/v1/
GPU_DEFAULT = "{{GPU}}"  # default of the `gpu` param (l4 | t4)
ENGINE_DEFAULT = "{{ENGINE}}"  # default of the `engine` param (b1_rag | b2_library)
embedder_uri = "{{SDFB_EMBEDDER_URI}}"  # B.1 embedder; empty ⇒ HashingEmbedder
# Not a source here: its dataset is the default of `source_dataset`.
default_table_fqn = "{{SDFB_DEFAULT_TABLE_FQN}}"
# Not a landing table here: its dataset is the default of `landing_dataset`.
landing_table_marker = "{{SDFB_LANDING_TABLE}}"
dlq_table = "{{SDFB_DLQ_TABLE}}"
validation_runs_table = "{{SDFB_VALIDATION_RUNS_TABLE}}"  # synthetic_data_quality.validation_runs
WRITE_DISPOSITION_DEFAULT = "{{WRITE_DISPOSITION}}"
# The three stores: empty ⇒ every batch omits the store's params, as the
# single-table DAG's launch does.
_RAG_CHUNKS_TABLE = "{{SDFB_RAG_CHUNKS_TABLE}}"
_FREETEXT_POOLS_TABLE = "{{SDFB_FREETEXT_POOLS_TABLE}}"
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
# ADR 0045: at most this many batch jobs at once. Each holds its workers'
# GPUs, so the regional GPU quota bounds it. Parse-time (see the header).
MAX_PARALLEL_JOBS = max(
    1, int(Variable.get("SDFB_MAX_PARALLEL_JOBS", default_var="1")))

# -----------------------------------------------------------------------------
# Build-time substituted constants (replaced by sed in workflow 3).
# -----------------------------------------------------------------------------
app_domain = "synthetic"
app_name = "sdfb"
project_version = "{{PROJECT_VERSION}}"
dag_version = "{{DAG_VERSION}}"
env_name = "{{ENV}}"

# The batch jobs' Dataflow name, before sdfb_batches.batch_job_name caps it
# and appends `-bNN`: lowercase letters, digits and hyphens only, as Dataflow
# requires and batch_job_name does not check. The version is reduced to
# them here; the model slug already is.
_version_slug = re.sub(r"[^a-z0-9]+", "-", project_version.lower()).strip("-")
_slug = sdfb_batches.model_slug(model_uri)
_base_job_name = f"{app_domain}-{app_name}-model-v{_version_slug}"
job_name = f"{_base_job_name}-{_slug}" if _slug else _base_job_name
flex_template = f"sdfb-{project_version}-template.json"
dag_id = f"{app_domain}_{app_name}_model_{dag_version}"

network_tag_experiments = ([
    f"use_network_tags={network_tags}",
    f"use_network_tags_for_flex_templates={network_tags}",
] if network_tags else [])

_QUALIFIED_TABLE_PARTS = 3  # project, dataset, table


def _dataset_of(fqn: str) -> str:
  """`project.dataset` of a `project.dataset.table` marker value, else ""."""
  parts = fqn.split(".")
  return ".".join(parts[:2]) if len(parts) == _QUALIFIED_TABLE_PARTS else ""


# What every batch's launch body is built from (sdfb_batches.launch_plan).
_CONSTANTS = {
    "bucket_path":
        bucket_path,
    "subnetwork":
        subnetwork,
    "service_account":
        service_account,
    "region":
        region,
    "network_tag_experiments":
        network_tag_experiments,
    "labels": {
        "app": app_name,
        "env": env_name,
        "dag": dag_id
    },
    "model_uri":
        model_uri,
    "embedder_uri":
        embedder_uri,
    "dlq_table":
        dlq_table,
    "validation_runs_table":
        validation_runs_table,
    "env":
        env_name,
    "rag_chunks_table":
        _RAG_CHUNKS_TABLE,
    "freetext_pools_table":
        _FREETEXT_POOLS_TABLE,
    "source_stats_table":
        _SOURCE_STATS_TABLE,
    "container_spec_gcs_path":
        f"gs://{templates_path}/synthetic/{flex_template}",
    "job_name":
        job_name,
}

# -----------------------------------------------------------------------------
# The evaluation — values copied from the single-table DAG.
# -----------------------------------------------------------------------------
# The evaluation job's name. The launch appends the run's timestamp; capped
# so the whole name stays inside Dataflow's 63 characters.
_evaluation_job_base = f"{app_name}-evaluation-v{_version_slug}"
evaluation_job_name = _evaluation_job_base[:38].rstrip("-")
# The evaluation job's id: `trigger_evaluation` does not wait, so its XCom
# holds the id of the job it launched.
evaluation_job_id = "{{ ti.xcom_pull(task_ids='trigger_evaluation')['id'] }}"
# How the sensor waits for the evaluation's LAUNCH to be over (the job
# running): one status read a minute, for at most an hour. The launcher gives
# up after about 12 minutes; the hour bounds a job that stays queued.
EVALUATION_LAUNCH_POKE_SECONDS = 60
EVALUATION_LAUNCH_TIMEOUT_SECONDS = 3600
# Default of the `evaluation_output_dataset` param: the dataset of the
# validation-runs table above (`project.dataset.table`).
_validation_runs_parts = validation_runs_table.split(".")
evaluation_output_dataset_default = (
    _validation_runs_parts[1] if len(_validation_runs_parts)
    == _QUALIFIED_TABLE_PARTS else "synthetic_data_quality")
# The evaluation job's workers run the generation's multi-GB image; Beam's own
# --disk_size_gb, passed through the template (see the single-table DAG).
EVALUATION_WORKER_DISK_GB = "200"
# What the launch records as the evaluation's trigger. The failure callback
# finds the launch's RUNNING row by it, so both read this one name.
EVALUATION_TRIGGER = "chained"

# The registry table of the evaluator's output dataset. The dataset comes from
# the `evaluation_output_dataset` DAG param, validated by `_DATASET_PATTERN`
# before it is part of SQL text; every other value reaches BigQuery as a query
# parameter. From here to `_ENDED_NOT_DONE`: a COPY of the generation DAG's
# constants (see the header).
_REGISTRY_TABLE = "evaluation_data_history"
_DATASET_PATTERN = r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)?"

# Appends the FAILED close of a RUNNING row whose launch died: the launcher
# mints the evaluation id and its RUNNING row has no job id, so the row is
# found by launch target (here the launch's base run id, the row's
# `base_run_id`), trigger and DAG run start. Copying the row keeps every
# schema-required field. An evaluation that already has a FINAL event is
# skipped; no match inserts nothing.
_CLOSE_RUNNING_SQL = """
INSERT INTO `{registry}`
SELECT * REPLACE (
  'FINAL' AS event,
  'FAILED' AS status,
  @reason AS status_reason,
  CURRENT_TIMESTAMP() AS recorded_at,
  CURRENT_TIMESTAMP() AS finished_at,
  COALESCE(NULLIF(@job_id, ''), running.evaluation_job_id) AS evaluation_job_id)
FROM `{registry}` AS running
WHERE running.event = 'RUNNING'
  AND running.trigger = @trigger
  AND (running.generation_job_id = @target OR running.base_run_id = @target)
  AND running.recorded_at >= @run_start
  AND NOT EXISTS (
    SELECT 1 FROM `{registry}` AS closed
    WHERE closed.evaluation_id = running.evaluation_id
      AND closed.event = 'FINAL')
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY running.evaluation_id ORDER BY running.recorded_at DESC) = 1
"""

# Dataflow job states (the REST API's JobState) that are terminal and are
# not JOB_STATE_DONE: a job in one of them writes no FINAL row of its own,
# so the callback's FAILED row is the evaluation's one terminal row. In any
# other state — queued, pending, running, cancelling, draining, done, or one
# that could not be read — the job may still write (or has written) it.
_ENDED_NOT_DONE = frozenset({
    "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_UPDATED",
    "JOB_STATE_DRAINED"
})

# -----------------------------------------------------------------------------
# DAG params — runtime-overridable on every trigger. Text, as Flex Template
# parameters are; the defaults are the single-table DAG's (pinned by test),
# except relationships_uri (one gs:// model file) and source_dataset (the
# generator's here). What the model names per table — the table itself, its
# keys — is no param.
# -----------------------------------------------------------------------------
default_dag_params = {
    "relationships_uri":
        Param(
            default="",
            type="string",
            description="gs:// path to ONE relationships model file "
            "(ADR 0045): every enabled table of it is generated. Required.",
        ),
    "config_uri":
        Param(
            default="",
            type="string",
            description="gs:// path to a generator config YAML (ADR 0043). "
            "Keys it sets win over every param of this DAG, the planning "
            "ones (relational.relationships_uri, launch.max_tables_per_job) "
            "included; params fill the keys it leaves out. Empty = no file. "
            "The evaluation (run_evaluation) follows the file: it takes each "
            "table, its source, the relationship model and "
            "reference_rows_limit from the batches' generation records in "
            "validation_runs (ADR 0044). It falls back to this DAG's "
            "relationships_uri only when the launch wrote no record (the "
            "table lacks the columns or its schema was unreadable; each "
            "batch's launcher then logs config_sets_evaluation_input for "
            "each key the file set).",
        ),
    "source_dataset":
        Param(
            default=_dataset_of(default_table_fqn),
            type="string",
            description="project.dataset every table's source is read from "
            "under its own name, unless the model gives the table a "
            "source_table. Default: the dataset of the build's default "
            "source table.",
        ),
    "landing_dataset":
        Param(
            default=_dataset_of(landing_table_marker),
            type="string",
            description="project.dataset every table lands in. Default: the "
            "dataset of the build's landing table.",
        ),
    "max_tables_per_job":
        Param(
            default="10",
            type="string",
            description="The most tables one batch job generates; a "
            "connected component is never split (ADR 0045).",
        ),
    "prep_workers":
        Param(
            default="8",
            type="string",
            description="Threads each batch launcher reads schemas, "
            "reference samples and source stats on; 1 = in turn.",
        ),
    "num_rows":
        Param(
            default="1000",
            type="string",
            description="Rows per table, unless the model sets num_rows.",
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
    "vllm_dtype":
        Param(
            default="float16",
            type="string",
            enum=["auto", "float16", "bfloat16"],
            description="vLLM --dtype override (as in the single-table DAG).",
        ),
    "vllm_max_model_len":
        Param(
            default="8192",
            type="string",
            description="vLLM --max-model-len cap; empty = no cap.",
        ),
    "client_type":
        Param(
            default="vllm",
            type="string",
            enum=["vllm", "fake"],
            description="vllm = the LLM on GPU workers; fake = CPU smoke.",
        ),
    "gpu":
        Param(
            default=GPU_DEFAULT,
            type="string",
            enum=["l4", "t4"],
            description="GPU profile when client_type=vllm.",
        ),
    "seed":
        Param(
            default="",
            type="string",
            description="Explicit base RNG seed; empty derives one per run.",
        ),
    "write_disposition":
        Param(
            default=WRITE_DISPOSITION_DEFAULT,
            type="string",
            enum=["append", "overwrite"],
            description="Landing-table write mode.",
        ),
    "create_if_not_exists":
        Param(
            default="false",
            type="string",
            enum=["false", "true"],
            description="Create each landing table on first write.",
        ),
    "build_rag_layer":
        Param(
            default="true",
            type="string",
            enum=["true", "false"],
            description="Populate the B.1 RAG chunk store (when the build "
            "set it).",
        ),
    "rag_row_docs_limit":
        Param(
            default="1024",
            type="string",
            description="B.1 row documents per table.",
        ),
    "build_pool_layer":
        Param(
            default="true",
            type="string",
            enum=["true", "false"],
            description="Build and persist free-text pools (when the build "
            "set the store).",
        ),
    "sdk_containers":
        Param(
            default="multi",
            type="string",
            enum=["single", "multi"],
            description="SDK-container topology for vLLM launches (ADR 0034).",
        ),
    "initial_workers":
        Param(
            default="",
            type="string",
            description="Initial Dataflow worker count per batch job.",
        ),
    "autoscaling":
        Param(
            default="auto",
            type="string",
            enum=["auto", "throughput", "fixed"],
            description="Worker autoscaling per batch job (ADR 0034).",
        ),
    "launcher_machine_type":
        Param(
            default="e2-standard-8",
            type="string",
            description="Machine type of each batch's Flex Template launcher "
            "VM: it pulls the image and preps the batch's tables inside "
            "Google's 12-minute launch limit.",
        ),
    "uniqueness_mode":
        Param(
            default="exact",
            type="string",
            enum=["exact", "exact_chained", "streaming"],
            description="Duplicate handling (ADR 0034).",
        ),
    "freetext_expansion":
        Param(
            default="identifiers",
            type="string",
            enum=["off", "identifiers", "all"],
            description="Shape-preserving expander for free-text columns.",
        ),
    "prompt_constraints":
        Param(
            default="on",
            type="string",
            enum=["on", "off"],
            description="Attach each column's llm_prompt_constraint.",
        ),
    "prompt_debug":
        Param(
            default="off",
            type="string",
            enum=["off", "redacted", "full"],
            description="Log each built pool prompt (ADR 0024).",
        ),
    "source_stats":
        Param(
            default="sample",
            type="string",
            enum=["sample", "off"],
            description="Per-column source_table_stats from each sample.",
        ),
    "fk_parent_landing":
        Param(
            default="",
            type="string",
            description="EXPERT OVERRIDE (ADR 0029): where parents landed.",
        ),
    "multi_table_mode":
        Param(
            default="single_job",
            type="string",
            enum=["single_job", "sequential_jobs"],
            description="How a batch of several tables runs (ADR 0030).",
        ),
    "generate_fk_relationships":
        Param(
            default="true",
            type="string",
            enum=["true", "false"],
            description="Honour the model's FK edges (ADR 0029).",
        ),
    "pool_seed_strategy":
        Param(
            default="centroid",
            type="string",
            enum=["centroid", "kcenter", "kcenter_rotate"],
            description="How free-text prompt seeds are chosen.",
        ),
    "run_evaluation":
        Param(
            default=True,
            type="boolean",
            description="After every batch has ended, evaluate the batches "
            "that landed, as one CPU-only Dataflow job.",
        ),
    "evaluation_mode":
        Param(
            default="",
            type="string",
            enum=["", "exact", "sampled"],
            description="Only with run_evaluation. Empty: the evaluator's "
            "choice.",
        ),
    "evaluation_machine_type":
        Param(
            default="e2-standard-8",
            type="string",
            description="Only with run_evaluation. The evaluation workers.",
        ),
    "evaluation_launcher_machine_type":
        Param(
            default="e2-standard-8",
            type="string",
            description="Only with run_evaluation. The evaluation launcher.",
        ),
    "evaluation_max_workers":
        Param(
            default=8,
            type="integer",
            minimum=1,
            description="Only with run_evaluation. maxWorkers of the "
            "evaluation job.",
        ),
    "evaluation_output_dataset":
        Param(
            default=evaluation_output_dataset_default,
            type="string",
            description="Only with run_evaluation. The quality dataset: the "
            "evaluator reads the launch's validation_runs rows there and "
            "writes the evaluation_* tables there. Default: the dataset of "
            "the validation-runs table.",
        ),
}


def _callback_closes_row(job_id, state):
  """Whether the failure callback writes the FAILED row.

  Only when a job id is known and the job ended in a terminal state other
  than done. With no id the state was never read, so nothing is written: a
  RUNNING row left open is the lesser harm than a FAILED row on a run that
  went on to succeed. A job still running, a job that is done and a state
  that could not be read (None) leave the row to the job.
  """
  return bool(job_id) and state in _ENDED_NOT_DONE


def _launched_job_id(launched, job_config):
  """The launched Dataflow job's id, or "" when neither XCom entry has it.

  `launched` is the task's return value (`{"id": ...}`; Airflow pushes it
  only when the task returned, which a failed task has not). `job_config` is
  the entry the launch operator pushes under `dataflow_job_config` before
  it can fail (`{"job_id": ...}`); that key and shape are unverified against
  a real provider.
  """
  for entry, key in ((launched, "id"), (job_config, "job_id")):
    value = entry.get(key) if isinstance(entry, dict) else None
    if isinstance(value, str) and value:
      return value
  return ""


def _job_state(job_id):
  """The Dataflow job's current state, or None when it cannot be read."""
  import logging

  from airflow.providers.google.cloud.hooks.dataflow import DataflowHook

  try:
    job = DataflowHook().get_job(
        job_id=job_id, project_id=project_id, location=region)
  except Exception as exc:  # pylint: disable=broad-exception-caught  # any read failure means "unknown": the callback then writes nothing
    logging.warning("evaluation job %s: its state could not be read (%s)",
                    job_id, exc)
    return None
  return job.get("currentState") if isinstance(job, dict) else None


def _read_gcs_text(uri: str) -> str:
  """One gs:// object's text: the model file, the config file."""
  from airflow.providers.google.cloud.hooks.gcs import GCSHook

  if not uri.startswith("gs://"):
    raise sdfb_batches.PlanError(
        f"{uri!r}: the model DAG reads its files from GCS (gs://…)")
  bucket, _, name = uri[len("gs://"):].partition("/")
  return GCSHook().download(
      bucket_name=bucket, object_name=name).decode("utf-8")


@task(task_id="plan", retries=0)
def plan_batches(**context) -> list:
  """The launch's plan: returns one launch body per batch, and pushes the
  base run id, the batch count, the model file, the batches and the banner
  under their own keys.

  The bodies are the return value because Airflow maps only over a task's
  return value (`start_batch` expands over it); `-> list` keeps
  multiple_outputs off, which a `dict` annotation would turn on. No retries:
  a PlanError (the model, the config file, K) fails the same way every
  time. The base run id is salted like the single-table DAG's run id: a
  retrigger of the same logical date must not replay the same rows.
  """
  import logging
  import uuid

  params = context["params"]
  config_uri = str(params["config_uri"] or "").strip()
  config_text = _read_gcs_text(config_uri) if config_uri else None
  relationships_uri, max_tables = sdfb_batches.planning_inputs(
      params, config_text)
  plan = sdfb_batches.launch_plan(
      params,
      model_text=_read_gcs_text(relationships_uri),
      max_tables_per_job=max_tables,
      relationships_uri=relationships_uri,
      base_run_id=f"{context['dag_run'].run_id}-{uuid.uuid4().hex[:8]}",
      constants=_CONSTANTS)
  logging.info("%s", plan["banner"])
  ti = context["ti"]
  for key in ("base_run_id", "batch_count", "relationships_uri", "batches",
              "banner"):
    ti.xcom_push(key=key, value=plan[key])
  return plan["bodies"]


@task(
    task_id="collect_landed",
    trigger_rule=TriggerRule.ALL_DONE,
    multiple_outputs=False)
def collect_landed(**context) -> dict:
  """After every batch: the ids of the jobs that reached DONE.

  A failed mapped instance returned nothing, so its job is not listed.
  Skips the evaluation when it is off or nothing landed. The evaluation
  reads the return value (`…['job_ids']`), so multiple_outputs is off, said
  here because the `dict` annotation would turn it on.
  """
  if not context["params"]["run_evaluation"]:
    raise AirflowSkipException("run_evaluation is off")
  ids = sdfb_batches.landed_job_ids(
      context["ti"].xcom_pull(task_ids="start_batch"))
  if not ids:
    raise AirflowSkipException("no batch job reached JOB_STATE_DONE: nothing "
                               "to evaluate")
  return {"job_ids": ",".join(ids)}


@task(task_id="fail_if_a_batch_failed", trigger_rule=TriggerRule.ONE_FAILED)
def fail_if_a_batch_failed() -> None:
  """A leaf that fails the run when a batch did.

  The evaluation after `collect_landed` may succeed, and Airflow fails a run
  only by a failed leaf.
  """
  raise AirflowFailException(
      "a start_batch instance failed: its batch job did not reach "
      "JOB_STATE_DONE (or `plan` failed and no batch was launched); the "
      "batches that landed were evaluated")


def _close_running_row(context):
  """on_failure_callback of `wait_for_evaluation_launch`: close the RUNNING
  row of the evaluation this DAG run launched with FAILED.

  The single-table DAG's callback, with the row found by the launch's base
  run id (the evaluation's `base_run_id`) instead of a generation job id.
  It runs on the sensor's task instance: the ids it needs are pulled from
  `plan` and `trigger_evaluation` by name. Nothing is written without a job
  id, nor while the launched job may still write its own FINAL row
  (`_callback_closes_row`).
  """
  import logging

  from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook

  params = context["params"]
  dataset = str(params["evaluation_output_dataset"])
  if not re.fullmatch(_DATASET_PATTERN, dataset):
    logging.error(
        "evaluation registry not updated: bad evaluation_output_dataset %r",
        dataset)
    return
  registry = (f"{dataset}.{_REGISTRY_TABLE}" if "." in dataset else
              f"{project_id}.{dataset}.{_REGISTRY_TABLE}")
  ti = context["ti"]
  # The launch target: the base run id `plan` minted, which the evaluation
  # was launched with (a callback has no Jinja to render).
  target = str(ti.xcom_pull(task_ids="plan", key="base_run_id") or "")
  if not target:
    logging.warning(
        "evaluation registry not updated: plan left no base run id in XCom "
        "to find the launch's RUNNING row by")
    return
  job_id = _launched_job_id(
      ti.xcom_pull(task_ids="trigger_evaluation"),
      ti.xcom_pull(task_ids="trigger_evaluation", key="dataflow_job_config"))
  if not job_id:
    logging.warning(
        "evaluation registry not updated: no Dataflow job id in XCom for the "
        "evaluation launch, so the job's state was not read. A RUNNING row "
        "is left open rather than closed over a job that may have succeeded")
    return
  state = _job_state(job_id)
  if not _callback_closes_row(job_id, state):
    logging.warning(
        "evaluation registry not updated: Dataflow job %s is in state %s, "
        "not a terminal state other than JOB_STATE_DONE. The job is still "
        "running, or it finished: it writes its own FINAL row", job_id, state)
    return
  exception = context.get("exception")
  ended = f" (job state {state})" if state else ""
  reason = (f"Model-launch evaluation job ended while its launch was "
            f"awaited{ended}: "
            f"{str(exception)[:500] or type(exception).__name__}")

  def string(name, value):
    return {
        "name": name,
        "parameterType": {
            "type": "STRING"
        },
        "parameterValue": {
            "value": value
        },
    }

  # The hook, not an operator executed inside a callable. UNVERIFIED against
  # the installed provider: `insert_job(configuration=, project_id=)` and the
  # job's `result()` are written from the operator's documented behaviour.
  job = BigQueryHook().insert_job(
      project_id=project_id,
      configuration={
          "query": {
              "query":
                  _CLOSE_RUNNING_SQL.format(registry=registry),
              "useLegacySql":
                  False,
              "queryParameters": [
                  string("reason", reason),
                  string("job_id", job_id),
                  string("trigger", EVALUATION_TRIGGER),
                  string("target", target),
                  {
                      "name": "run_start",
                      "parameterType": {
                          "type": "TIMESTAMP"
                      },
                      "parameterValue": {
                          "value": context["dag_run"].start_date.isoformat()
                      },
                  },
              ],
          }
      },
  )
  job.result()
  # The BigQuery job succeeds whether or not the statement matched a row, so
  # say which. Read defensively: the attribute is the client library's.
  written = getattr(job, "num_dml_affected_rows", None)
  if not isinstance(written, int):
    logging.info(
        "evaluation registry: the close statement ran for Dataflow job %s "
        "(launch %s); how many rows it wrote is unknown", job_id, target)
  elif written:
    logging.info(
        "evaluation registry: closed %d RUNNING row(s) as FAILED for Dataflow "
        "job %s (launch %s)", written, job_id, target)
  else:
    logging.warning(
        "evaluation registry not updated: the close statement matched no "
        "open RUNNING row for launch %s, trigger %s, recorded since this DAG "
        "run's start. Either there was nothing to close (the launcher wrote "
        "its own FINAL row, or died before it wrote RUNNING), or the row is "
        "older than the run's start (the run was cleared), or it had not yet "
        "been loaded", target, EVALUATION_TRIGGER)


with models.DAG(
    dag_id=dag_id,
    # Manual trigger only, as the standalone evaluation DAG: with no
    # schedule the start date only has to precede a trigger. Never `@once`:
    # Composer would run it at import, with relationships_uri empty.
    start_date=days_ago(1),
    schedule_interval=None,
    catchup=False,
    max_active_runs=1,
    tags=["SYNTHETIC", "Dataflow", "MODEL",
          env_name.upper()],
    params=default_dag_params,
) as dag:
  # The launch bodies: `plan`'s return value, the only XCom Airflow maps over.
  planned = plan_batches()
  # One launch per batch, built in `plan`; each waits for its own job, so
  # `max_active_tis_per_dag` caps the jobs that run at once. No retries: a
  # retry would launch the whole batch again (see Retries in the header).
  start_batch = DataflowStartFlexTemplateOperator.partial(
      task_id="start_batch",
      project_id=project_id,
      location=region,
      wait_until_finished=True,
      do_xcom_push=True,
      retries=0,
      max_active_tis_per_dag=MAX_PARALLEL_JOBS,
  ).expand(body=planned)
  landed = collect_landed()

  # The evaluation: one CPU-only Dataflow job from the SAME template as the
  # batches, the single-table DAG's launch with another target. Submitted and
  # not waited for: the job writes its own FINAL row. The evaluator's options
  # this launch does not pass keep their defaults (the single-table DAG's
  # `trigger_evaluation` lists them).
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
              # How Dataflow runs the job: the single-table DAG's evaluation
              # environment, pinned equal by test.
              "environment": {
                  "tempLocation":
                      f"gs://{bucket_path}/temp/",
                  "stagingLocation":
                      f"gs://{bucket_path}/staging",
                  "subnetwork":
                      subnetwork,
                  "ipConfiguration":
                      "WORKER_IP_PRIVATE",
                  "serviceAccountEmail":
                      service_account,
                  "additionalExperiments": [
                      "use_runner_v2",
                      "enable_secure_boot",
                      *network_tag_experiments,
                  ],
                  "additionalUserLabels": {
                      "app": app_name,
                      "env": env_name,
                      "dag": dag_id,
                  },
                  "launcherMachineType":
                      "{{ params.evaluation_launcher_machine_type }}",
                  "machineType":
                      "{{ params.evaluation_machine_type }}",
                  "maxWorkers":
                      "{{ params.evaluation_max_workers }}",
                  "workerRegion":
                      region,
              },
              # Names are the template's (docker/flex_template_metadata.json).
              "parameters": {
                  # Selects the evaluator inside the shared image.
                  "sdfb_job":
                      "evaluation",
                  # The target: the launch's base run id. The evaluator reads
                  # every validation_runs row of the launch (`<base>` or
                  # `<base>-…`) from output_dataset — each table, its source
                  # and its sample digest.
                  "run_id":
                      "{{ ti.xcom_pull(task_ids='plan', key='base_run_id') }}",
                  # The batch jobs that landed: only their rows are evaluated,
                  # and the registry row records every id.
                  "generation_job_ids":
                      "{{ ti.xcom_pull(task_ids='collect_landed')['job_ids'] }}",
                  # The model the launch planned, when the launch's own
                  # records name none. Empty when the DAG param
                  # generate_fk_relationships is "false".
                  "relationships_uri":
                      "{{ ti.xcom_pull(task_ids='plan', key='relationships_uri') "
                      "if params.generate_fk_relationships == 'true' "
                      "else '' }}",
                  # Each landing table is read WHOLE: with write_disposition
                  # append that includes earlier launches' rows.
                  "scope":
                      "manual",
                  # Recorded in the registry row; the failure callback looks
                  # for the row by this same name.
                  "trigger":
                      EVALUATION_TRIGGER,
                  "mode":
                      "{{ params.evaluation_mode }}",
                  "output_dataset":
                      "{{ params.evaluation_output_dataset }}",
                  # Beam's own --disk_size_gb, passed through.
                  "disk_size_gb":
                      EVALUATION_WORKER_DISK_GB,
              },
          }
      },
      do_xcom_push=True,
      wait_until_finished=False,
  )

  # The single-table DAG's sensor: the evaluation's LAUNCH is over when its
  # job runs; a job that ended otherwise fails it and the callback closes
  # the RUNNING row.
  wait_for_evaluation_launch = DataflowJobStatusSensor(
      task_id="wait_for_evaluation_launch",
      job_id=evaluation_job_id,
      expected_statuses={"JOB_STATE_RUNNING", "JOB_STATE_DONE"},
      project_id=project_id,
      location=region,
      mode="reschedule",
      poke_interval=EVALUATION_LAUNCH_POKE_SECONDS,
      timeout=EVALUATION_LAUNCH_TIMEOUT_SECONDS,
      on_failure_callback=_close_running_row,
  )
  failed = fail_if_a_batch_failed()

  start_batch >> landed >> trigger_evaluation  # pylint: disable=pointless-statement  # Airflow chain
  trigger_evaluation >> wait_for_evaluation_launch  # pylint: disable=pointless-statement  # Airflow dependency operator
  start_batch >> failed  # pylint: disable=pointless-statement  # Airflow dependency operator
