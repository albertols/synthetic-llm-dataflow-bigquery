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
  {{PROJECT_VERSION}}         the version the template is named after
                              (sdfb-<version>-template.json); a Release build
                              takes GENERATOR_VERSION_BASE from
                              sdfb_core/version.py (ADR 0044, RUN_PLAYBOOK §0a)
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

Chained evaluation (`run_evaluation`, default True), in THIS DAG::

    start_sdfb ─► run_evaluation_gate ─► wait_for_generation ─►
    (launch,      (skips the rest         (reschedule-mode
     no wait)      when turned off)        sensor on the job)

        ─► trigger_evaluation ─► wait_for_evaluation_launch
           (launch, SAME         (reschedule-mode sensor on the
            template, no wait)    evaluation job, until it runs)

With `run_evaluation` False the gate skips everything after the generation
launch: the DAG runs `start_sdfb` and nothing else, as before. With it True
the sensor waits for the generation job to reach JOB_STATE_DONE (a job that
fails or is cancelled fails the sensor, after the task's retries) and
`trigger_evaluation` launches the evaluation as a second, CPU-only
Dataflow job from the same Flex Template, with the template parameter
`sdfb_job=evaluation` (one image, one template, one DAG import; ADR 0041's
amendment of 2026-10-06). `wait_for_evaluation_launch` then waits until that
job runs, which is when its launch is over ("who writes the terminal row",
below). No other DAG is triggered, and no marker or Airflow Variable is added
for any of it. Neither sensor is deferrable, so no triggerer is needed:
`deferrable` is not passed, and the provider's default is off unless the
environment sets `[operators] default_deferrable`.

Run slot: with `run_evaluation` true (the default) a run stays running until the
generation job ends (the first sensor waits for it, hours) and then until the
evaluation's launch is over (the second sensor). The second wait follows from
the code, it was not measured: at most the launcher's twelve minutes and one
60-second poke, whether the job then runs or the launcher has died; on the
failure path, plus the task's retries (below); and one hour, the sensor's
timeout, for a job that stays queued. The DAG has `max_active_runs=1`, so
every later trigger queues behind it: launches run one generation at a time.
`max_active_runs` is the knob; raising it lets several generation jobs run at
once (mind the GPU quota). It is unchanged here.

Retries: no task here passes `retries` and the DAG passes no `default_args`,
so the environment decides. Cloud Composer's default is two retries, five
minutes apart, for any task that fails. A sensor that raises on a dead job is
therefore "up for retry" first, and its failure callback runs after the
task's last retry: under that default about ten minutes after the sensor
first saw the dead job, with the registry row still RUNNING meanwhile. That
is kept on purpose (a transient error in one poke must not end the watch). A
sensor that ends on its timeout is not retried. The same default launches a
failed `trigger_evaluation` again.

`trigger_evaluation` evaluates what the launch generated: the launched table
(`seed_table`) and its enabled group of tables in the relationship model, or
that table alone; each landed table is read whole and compared with the table
of the same name in the source dataset. When the launch wrote its generation
record (a `validation_runs` table with the ADR 0044 columns), the evaluator
takes the tables, their datasets, the relationship model and
`reference_rows_limit` from that record instead, so a `config_uri` file that
changed them is evaluated as generated; this DAG's values are the fallback (no
record, a job the evaluator cannot read, a record spanning several datasets).
The comments inside its `parameters`
say, for every parameter, where the value comes from and what the evaluator
does with it, and the block above the operator lists the evaluator's options
this DAG leaves at their defaults. In short:

- It reads neither the generation job's log nor BigQuery job labels, so its
  service account needs no `roles/logging.viewer` and no
  `roles/bigquery.resourceViewer`. The generation job's id is passed as the
  launch's identity: the evaluator reads the Dataflow job resource and
  nothing else about the job (needs `roles/dataflow.viewer`; without it the
  run goes on with a warning and no window). With the window it also reads
  this launch's rows of the generator's `validation_runs` table (below).
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
  launch's rows of it (the job's window) and those metrics are evaluated. A
  generation record on those rows replaces the 10000 this DAG passes with the
  launch's own value. Without that table, or when the job's window
  cannot be read, they come out "not evaluated" with the reason on the row.
  Every other metric is evaluated on all rows either way.
- A landing table named differently from its source (a custom
  `SDFB_LANDING_TABLE` name) is not found: the evaluation looks for the
  source's name in the landing dataset.
- What stays lost: the launch's log, so a model the launch adjusted
  (ADR 0038) is not seen; and, without a generation record, the table list
  is the seed's, not the launch's own. The service account also needs read on
  the generator's `validation_runs` table.

`trigger_evaluation` submits the job and does not wait, and nothing in the
chain waits for the evaluation to finish: the evaluation job writes its own
FINAL registry row. The launcher writes the RUNNING row minutes into the
launch (after the image pull and planning, before it submits the pipeline),
so an evaluation that ends without a FINAL row keeps RUNNING as its last
event. Who writes the terminal row::

    what happens                          who closes the RUNNING row
    ────────────────────────────────────  ──────────────────────────────────
    the job finishes                      the job: its own FINAL row
    the launch CALL fails                 normally nobody needs to: no
      (`trigger_evaluation` fails)        launcher ran, so no RUNNING row
                                          was written
    the LAUNCHER dies (its time limit,    this DAG: the job ends FAILED,
      say, ends it after RUNNING was      which fails
      written), or the job ends some      `wait_for_evaluation_launch`
      other way before a poke saw it      after its retries; its failure
      running                             callback, `_close_running_row`,
                                          appends FINAL FAILED
    the job dies after a poke saw it      nobody in this DAG: the sensor
      running                             has succeeded and nothing waits
                                          for the job's end

"Normally", because the launch task can also fail after its call was
accepted (the status read that follows the launch raises, or the worker dies
in those seconds): a launcher then runs and writes RUNNING while the sensor
is upstream-failed, and no callback runs for it. And the last line is wider
than it looks: a Dataflow job reports running when it starts, before any
worker is up, so a job that dies while its workers start is one nobody
closes here.

Why a fifth task. `trigger_evaluation` cannot see a launcher die: with
`wait_until_finished=False` the operator returns as soon as the job it
launched is queued, seconds after the launch call and minutes before the
launcher writes the RUNNING row. Whatever happens to the launcher later, that
task has succeeded, so a failure callback on it would never run for a dead
launcher. `wait_for_evaluation_launch` is a second status sensor, the same
class and arguments as `wait_for_generation`, on the evaluation job (the id
in `trigger_evaluation`'s XCom). Its `expected_statuses` are
JOB_STATE_RUNNING and JOB_STATE_DONE: the launch is over when the job runs,
or has already finished between two pokes. A job that reaches any other
terminal state makes the sensor raise (the provider's sensor raises on a
terminal state it was not told to expect); once the task's retries are used
up it has failed, and its failure callback closes the row.

It waits for the launch, not for the job's end. The alternative,
`expected_statuses={"JOB_STATE_DONE"}` with a timeout as long as an
evaluation, would also close the row of a job that dies on the workers, at
the price of the DAG's one run slot (`max_active_runs=1`) for the whole
evaluation: the next generation launch would queue behind it. It is not
built.

What the wait costs: the run slot is held longer (Run slot, above); a dead
evaluation launch now marks the DAG run failed, where before the run was
green while its evaluation was dead; and a job that dies on the workers after
it started is still not closed by the chain.

`_close_running_row` finds the row by this run's generation job id (the XCom
of `start_sdfb`: what the launch passed as `generation_job_id`), the trigger
`chained` and the run's start. It writes only when the launched Dataflow job
is known and has ended without being done. With no job id in XCom the job's
state was never read; a job that is running or done writes its own row; a
job still queued when the sensor times out may yet run; a state that cannot
be read is unknown: in each case nothing is written. So for the launch that
died there are never two terminal rows, and never a FAILED row over a job
that may be running or done. After the statement it logs how many rows it
wrote, as a warning when none: the BigQuery job succeeds either way, and
only the count tells a closed row from a statement that matched nothing (the
standalone DAG's callback logs nothing there).

Where that ends. The row is found by generation job, trigger and the run's
start, not by its own id. Two evaluations of one generation job inside one
RUNNING DAG run can therefore close each other's row: a launch task retried
(the environment's retries make that the normal path for a launch task that
failed after its call was accepted) or cleared while the run is still
running, or a standalone evaluation of the same generation job started with
`trigger=chained` during the chained launch. If the later launch dies while
the earlier evaluation still runs, that one's row is closed as well, and its
job's own FINAL row follows. Clearing the tail of a FINISHED run is not such
a case: Airflow resets the run's start then, so the earlier evaluation's row
is older than the start and is not matched. For the same reason the callback
cannot close that older row either, when it is the one whose launcher then
dies: the statement matches nothing and the log says so.

The statement, its constants and its helpers are a COPY of the standalone
DAG's (`composer/evaluation_framework.py`): Composer imports the two files
separately, so each stays self-contained.

A job that dies after it started keeps its RUNNING row until someone closes
it by hand (`docs/RUN_PLAYBOOK.md`, "A RUNNING row that never closes"); the
GUI shows it as Stale once the event is more than 24 hours old. The
standalone DAG is the launch that waits for its job's end, and so closes the
row of a job that dies on the workers.

None of this has been parsed or run by Airflow. The close rests on two
things, both read and neither run (ADR 0041, note of 2026-10-11, has the
links). One: the status sensor raises when the job it watches ends in a
state it was not told to expect (JOB_STATE_FAILED for a dead launcher). That
is the provider's source; if an installed provider only poked on, the sensor
would fail at its one-hour timeout and the callback, reading the job as
failed, would close the row then. Two: while the launcher runs, the job is
not yet running. Google's page of job statuses lists a Flex Template job that
is being launched as Queued. That page names the console's statuses, and no
page names the state a launch that timed out ends in: if it is not one of the
four the callback closes, the sensor ends on its timeout and the callback
writes nothing (the playbook has the one-line check). Also unverified: the
environment's retries, that a sensor in reschedule mode gets its
`on_failure_callback` called as written, and the callback's two hook calls
(the standalone DAG's own). The evaluation job's id is not in question: it
is the return value of a launch task that succeeded.

"""

# Heavy or optional dependencies are imported lazily, where they are used.
# pylint: disable=import-outside-toplevel

# f-string fields keep single quotes: yapf 0.43 cannot parse PEP 701
# quote reuse, and pylint reads those quotes as inconsistent.
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
# Chained evaluation (run_evaluation). Nothing below is used unless the gate
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
# The evaluation job's id, the same way: `trigger_evaluation` does not wait
# either, so its XCom holds the id of the job it launched.
evaluation_job_id = "{{ ti.xcom_pull(task_ids='trigger_evaluation')['id'] }}"
# How the second sensor waits for the evaluation's LAUNCH to be over (the job
# running): one status read a minute, for at most an hour. The launcher gives
# up after about 12 minutes; the hour bounds a job that stays queued.
EVALUATION_LAUNCH_POKE_SECONDS = 60
EVALUATION_LAUNCH_TIMEOUT_SECONDS = 3600
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
# What the chained launch records as the evaluation's trigger. The failure
# callback finds the launch's RUNNING row by it, so both read this one name.
EVALUATION_TRIGGER = "chained"

# The registry table of the evaluator's output dataset. The dataset comes from
# the `evaluation_output_dataset` DAG param, validated by `_DATASET_PATTERN`
# before it is part of SQL text; every other value reaches BigQuery as a query
# parameter. From here to `_ENDED_NOT_DONE`: a COPY of the standalone
# evaluation DAG's constants (see the header: one file per import).
_REGISTRY_TABLE = "evaluation_data_history"
_DATASET_PATTERN = r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)?"

# Appends the FAILED close of a RUNNING row whose launch died: the launcher
# mints the evaluation id and its RUNNING row has no job id, so the row is
# found by launch target (the generation job id), trigger and DAG run start.
# Copying the row keeps every schema-required field. An evaluation that
# already has a FINAL event is skipped; no match inserts nothing.
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
    "rag_row_docs_limit":
        Param(
            default="1024",
            type="string",
            description="B.1 RAG: how many reference rows (the first N of the "
            "fingerprint-ordered sample) are embedded as row documents "
            "— the in-worker exemplar index and the rag_chunks row_doc "
            "prefix. A positive integer, clamped to the reference "
            "sample (10000 rows). A chunk store written with a smaller "
            "value is topped up with the missing rows when "
            "build_rag_layer is true. Default 1024.",
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
            default="multi",
            type="string",
            enum=["single", "multi"],
            description="SDK-container topology for vLLM launches (ADR 0034). "
            "single = one SDK process per worker "
            "(no_use_multiple_sdk_containers, RUN_PLAYBOOK §3): the "
            "generate stages run on ONE Python interpreter per worker "
            "(~1 of 8 vCPUs busy on the 2026-08-29 R6 pair). multi "
            "(default) = Dataflow's default, one SDK process per vCPU; the "
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
    "launcher_machine_type":
        Param(
            default="e2-standard-8",
            type="string",
            description="Machine type of the Flex Template LAUNCHER VM of the "
            "generation launch (not the workers): it pulls the multi-GB "
            "image, then reads every table's reference sample, measures the "
            "source fan-outs and declared PKs, builds the graph and hands it "
            "over, all inside Google's 12-minute launch limit. A larger one "
            "pulls faster. The limit itself cannot be set from a DAG. "
            "Default e2-standard-8.",
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
    "config_uri":
        Param(
            default="",
            type="string",
            description="gs:// path to a generator config YAML (ADR 0043). "
            "Keys it sets win over every param of this DAG; params fill "
            "the keys it leaves out. Empty = no file — the launch behaves "
            "exactly as before. The chained evaluation (run_evaluation) "
            "follows the file: it takes the launch's tables, relationship "
            "model and reference_rows_limit from the generation record in "
            "validation_runs (ADR 0044). It falls back to this DAG's params "
            "only when the launch wrote no record (the table lacks the "
            "columns or its schema was unreadable); the launcher then logs "
            "config_sets_evaluation_input for each key the file set.",
        ),
    "run_evaluation":
        Param(
            default=True,
            type="boolean",
            description="On by default. False: the DAG launches generation "
            "and nothing else. True: after the generation job reaches "
            "DONE this DAG launches the evaluator as a second, CPU-only "
            "Dataflow job from the same template. It evaluates what the "
            "launch generated: the tables, datasets, relationship model and "
            "reference_rows_limit its generation record in validation_runs "
            "names (ADR 0044), else the launched table and, with "
            "generate_fk_relationships true, the rest of its group of "
            "tables in the relationship model; each landed "
            "table is read whole (with write_disposition append that "
            "includes earlier launches' rows). While it waits (for the "
            "generation job, then for the evaluation's launch to be over) "
            "the run holds the DAG's only active-run slot "
            "(max_active_runs=1), so launches of this DAG then run one "
            "generation at a time. A dead evaluation launch fails the run.",
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
    "evaluation_launcher_machine_type":
        Param(
            default="e2-standard-8",
            type="string",
            description="Only with run_evaluation. Machine type of the Flex "
            "Template LAUNCHER VM of the evaluation launch: it pulls the "
            "multi-GB image and plans the evaluation before the Dataflow job "
            "exists, all inside Google's 12-minute launch limit. A larger one "
            "pulls faster. The limit itself cannot be set from a DAG. "
            "Default e2-standard-8.",
        ),
    "evaluation_max_workers":
        Param(
            default=8,
            type="integer",
            minimum=1,
            description="Only with run_evaluation. Upper bound on the number "
            "of workers the evaluation job may use (Dataflow maxWorkers). "
            "Default 8.",
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
  """Gate for the chained evaluation: False skips every task after it."""
  return bool(params["run_evaluation"])


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


def _close_running_row(context):
  """on_failure_callback of `wait_for_evaluation_launch`: close the RUNNING
  row of the evaluation this DAG run launched with FAILED.

  It runs on the sensor's task instance, not on the launch's: the ids it
  needs are pulled from the two launch tasks by name. Nothing is written
  without a job id, nor while the launched job may still write its own FINAL
  row (`_callback_closes_row`): never two terminal rows for one evaluation,
  and never FAILED with the job's state unread. A sensor that fails on its
  timeout while the job is still queued therefore writes nothing.
  """
  import logging
  import re

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
  # The launch target: the generation job's id as the launch read it (the
  # `id` of start_sdfb's return value; a callback has no Jinja to render).
  target = _launched_job_id(ti.xcom_pull(task_ids="start_sdfb"), None)
  if not target:
    logging.warning(
        "evaluation registry not updated: start_sdfb left no generation job "
        "id in XCom to find the chained launch's RUNNING row by")
    return
  job_id = _launched_job_id(
      ti.xcom_pull(task_ids="trigger_evaluation"),
      ti.xcom_pull(task_ids="trigger_evaluation", key="dataflow_job_config"))
  if not job_id:
    logging.warning(
        "evaluation registry not updated: no Dataflow job id in XCom for the "
        "chained launch, so the job's state was not read. A RUNNING row is "
        "left open rather than closed over a job that may have succeeded")
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
  reason = (
      f"Chained evaluation job ended while its launch was awaited{ended}: "
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
        "(generation job %s); how many rows it wrote is unknown", job_id,
        target)
  elif written:
    logging.info(
        "evaluation registry: closed %d RUNNING row(s) as FAILED for Dataflow "
        "job %s (generation job %s)", written, job_id, target)
  else:
    logging.warning(
        "evaluation registry not updated: the close statement matched no "
        "open RUNNING row for generation job %s, trigger %s, recorded since "
        "this DAG run's start. Either there was nothing to close (the "
        "launcher wrote its own FINAL row, or died before it wrote RUNNING), "
        "or the row is older than the run's start (the run was cleared), or "
        "it had not yet been loaded", target, EVALUATION_TRIGGER)


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
                  # The launcher VM, not the workers: the image pull and the
                  # whole driver-side preflight (one table after another)
                  # share Google's 12 minutes. On Dataflow's default launcher
                  # the five-table launch of 2026-10-12 spent 7 min 24 s
                  # pulling the image, had its graph ready 11 min 19 s in
                  # and was shut down 66 s into handing it over (`Timeout in
                  # polling result file`).
                  "launcherMachineType":
                      "{{ params.launcher_machine_type }}",
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
                  "config_uri": "{{ params.config_uri }}",
                  "multi_table_mode": "{{ params.multi_table_mode }}",
                  "uniqueness_mode": "{{ params.uniqueness_mode }}",
                  "pool_seed_strategy": "{{ params.pool_seed_strategy }}",
                  "reference_table": "{{ params.table_fqn }}",
                  "reference_rows_limit": "10000",
                  "rag_row_docs_limit": "{{ params.rag_row_docs_limit }}",
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

  # Chained evaluation (run_evaluation, default True): with it off the gate
  # skips every task below and the DAG behaves exactly as before. The launch
  # above does not wait, so its XCom holds the job id the sensor waits on.
  run_evaluation_gate = ShortCircuitOperator(
      task_id="run_evaluation_gate",
      python_callable=_run_evaluation_enabled,
  )

  # The landed tables are evaluated, so the generation job must be done. A
  # job that failed or was cancelled is expected to fail this task once its
  # retries are used up (the provider's sensor raises on a terminal state it
  # was not told to expect; read in its source, not run under the installed
  # provider). If it did not, the task would fail at its timeout: either way
  # nothing is evaluated. No `retries` here: the environment decides. Not
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
  # generation: the job writes its own FINAL row. This task returns as soon
  # as the job is queued, before the launcher has written the RUNNING row, so
  # it cannot see the launcher die: the sensor after it does (see the header).
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
                  "tempLocation":
                      f"gs://{bucket_path}/temp/",
                  "stagingLocation":
                      f"gs://{bucket_path}/staging",
                  # The generation job's network, with private worker IPs.
                  "subnetwork":
                      subnetwork,
                  "ipConfiguration":
                      "WORKER_IP_PRIVATE",
                  # The generation job's service account (Variable
                  # SA_DATAFLOW): it needs the evaluator's roles.
                  "serviceAccountEmail":
                      service_account,
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
                  # are the WORKERS. launcherMachineType is the launcher VM
                  # that pulls the image and plans before the Dataflow job
                  # exists; Google gives a launch 12 minutes in all, the pull
                  # included, and the public REST description has no field
                  # for that limit (gcloud has --launcher-vm-timeout-secs).
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
              # The launcher supplies runner, project and region itself and
              # the image carries its worker image coordinate.
              "parameters": {
                  # Selects the evaluator inside the shared image; without it
                  # the same template would start a generation job.
                  "sdfb_job":
                      "evaluation",
                  # The generation job's Dataflow id: the same expression the
                  # wait sensor uses (the XCom `id` of start_sdfb). It is the
                  # launch's IDENTITY: the evaluator reads the Dataflow job
                  # resource (id, name, region, start and end) and nothing else
                  # about the job, never its log; the job's validation_runs rows
                  # (validation_runs_table below) are found by it.
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
                  # table_fqn. It names the tables to evaluate when the
                  # generation wrote no record (ADR 0044): with a relationship
                  # model, this table and its enabled group; else this table
                  # alone. With a record, the record names them (the launch's
                  # tables, in run order), and a warning says so when they
                  # differ from this table's group.
                  "seed_table":
                      "{{ params.table_fqn.rsplit('.', 1)[-1] }}",
                  # The relationship model, the DAG param of the same name
                  # (default: the config/relationships folder packaged in the
                  # image). The evaluator reads the seed's enabled group of
                  # tables from it, parents first. A folder with no model
                  # file, or no model naming the seed, evaluates the seed
                  # alone (with a warning). Empty when the DAG param
                  # generate_fk_relationships is "false": an isolated
                  # generation is evaluated as one table. A generation record
                  # replaces it with the model the launch used, or with none
                  # when the launch generated without one.
                  "relationships_uri":
                      "{{ params.relationships_uri "
                      "if params.generate_fk_relationships == 'true' "
                      "else '' }}",
                  # Where the synthetic tables landed: the project.dataset of
                  # the generation's landing table (marker SDFB_LANDING_TABLE,
                  # whatever table name it ends in). Each table is read as
                  # <landing_dataset>.<its source name>, so a landing table
                  # named differently from its source is not found. A
                  # generation record's landing dataset replaces it.
                  "landing_dataset":
                      "{{ '{{SDFB_LANDING_TABLE}}'.rsplit('.', 1)[0] }}",
                  # Where the SOURCE tables are: the DAG param source_dataset,
                  # else the dataset of table_fqn. Each synthetic table is
                  # compared with the table of the same name here. A
                  # generation record's source dataset replaces it.
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
                  # needs at least 2000 rows. The value must equal the
                  # generation's: a generation record carries it and replaces
                  # this one (a config_uri file may have changed it), so 10000
                  # is the fallback for a launch without a record. The rebuilt
                  # sample is only trusted when it matches the generator's
                  # recorded digest; see validation_runs_table below.
                  "reference_rows_limit":
                      "10000",
                  # The generator's validation-runs table: the same value
                  # start_sdfb passes (the marker SDFB_VALIDATION_RUNS_TABLE).
                  # With the job's window known, the evaluator reads THIS
                  # launch's rows of it (the rows created for the landing
                  # tables inside the window, or that name the job): no log.
                  # The reference_digest on a row is what verifies the rebuilt
                  # sample, which is what lets the reference-based privacy
                  # metrics (listed at reference_rows_limit) be evaluated;
                  # it also brings the run ids. When the rows carry the
                  # generation record (the ADR 0044 columns), the record names
                  # what is evaluated: the tables, their datasets, the
                  # relationship model and reference_rows_limit, each value
                  # that differs from the one above a warning. Needs read on
                  # this table.
                  # If the table cannot be read, or the window is unknown
                  # (the Dataflow job could not be read), the run goes on
                  # with a warning and those metrics are "not evaluated"
                  # with the reason on the row. Rows that name another job
                  # are dropped, with a warning naming them; if rows that name
                  # no job (older rows) hold several launches, the latest
                  # launch's rows are used and a warning says so.
                  "validation_runs_table":
                      validation_runs_table,
                  # Recorded in the registry row: what started the
                  # evaluation (cli | composer | chained | agent). The
                  # failure callback looks for the row by this same name.
                  "trigger":
                      EVALUATION_TRIGGER,
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

  # The evaluation's LAUNCH is over when its job runs. Until then the
  # launcher (planning, the RUNNING row, the submission) can still die, and
  # the launch task above has long since succeeded. This sensor waits for the
  # job to run (or to be done already, between two pokes). A job that ended
  # any other way makes it raise (the provider's sensor raises on a terminal
  # state it was not told to expect; read in its source, not run under the
  # installed provider). The task is then retried as the environment says
  # (no `retries` here, on purpose: see Retries in the header), and after its
  # last retry the failure callback closes the RUNNING row. It does not wait
  # for the job's end: the job writes its own FINAL row, and the DAG's one
  # run slot is not held for the evaluation. The same class and arguments as
  # `wait_for_generation`, plus the callback.
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

  start_sdfb >> run_evaluation_gate >> wait_for_generation >> trigger_evaluation  # pylint: disable=pointless-statement  # Airflow chain
  trigger_evaluation >> wait_for_evaluation_launch  # pylint: disable=pointless-statement  # Airflow dependency operator
