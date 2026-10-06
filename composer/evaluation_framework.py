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
"""Airflow DAG — evaluate a generation run (the optional, standalone path).

Runs in an Airflow/Cloud Composer environment. This file is a *template*:
the deploy workflow's `sed` substitutes build-time values at import. Runtime
values (the evaluation target, the mode, the worker shape) come from Airflow
DAG params — operators never re-import the DAG to change them.

The deployment does not need this DAG to evaluate a run: the generation DAG
(`composer/synthetic_beam_bigquery.py`) evaluates its own run when
`run_evaluation` is True. Import this one to evaluate a run that has already
finished (by job id, by run id, or by tables), and to have a launch that
waits for its job and closes the registry row when the job dies.

One image, one template (ADR 0041, amendment of 2026-10-06). The evaluation
launches from the SAME Flex Template as generation,
`sdfb-<PROJECT_VERSION>-template.json`, and passes the template parameter
`sdfb_job=evaluation`, which selects the evaluator's entry in the image.
Nothing else is built for it.

    To launch from an evaluator-only template instead (one built by
    `packages/sdfb-evaluation/deploy/build_flex_template.sh` on the same
    image), change ONE constant below:

        flex_template = "sdfb-evaluation-<version>-template.json"

    The launch body stays as it is: that template declares the same
    parameters, `sdfb_job` among them.

The DAG id is fixed (`sdfb_evaluation_framework`).

Substitution markers (all of them are ones the import workflow already
substitutes for the generation DAG; run the same substitution on this file):
  {{PROJECT_VERSION}}         project version the template was built for
                              (`sdfb-<version>-template.json`)
  {{ENV}}                     dev | uat | prd
  {{GCS_DATAFLOW_STAGING}}    <env>-…-dataflow-staging bucket name
  {{GCS_DATAFLOW_TEMPLATES}}  <env>-…-dataflow-templates bucket name

Runtime values: Airflow DAG params ({{ params.* }}) + Airflow Variables for
infra (PROJECT_ID, REGION, DATAFLOW_SUBNET, SA_DATAFLOW, and the optional
DATAFLOW_NETWORK_TAGS).

Task graph::

    begin ─► fan_out ─► wait_gate ─► wait_for_generation_job ─► start_evaluation
                └──────────────────────────────────────────────────▲
    (wait_for_generation false: the gate skips the sensor, fan_out still
    reaches the launch.)

Several generation jobs in one go: give `generation_job_ids` (a list) on one
manual run. `fan_out`, the first task, then starts one run of THIS DAG per
id (each with that id as its `generation_job_id` and the run's other params
unchanged) and skips the rest of its own run. Every job is so evaluated by
its own run, one Dataflow job per id, on the single-target path above: the
sensor, the launch and the failure callback never see a list. Without the
list `fan_out` passes and the run is the single-target run it always was.

    generation_job_ids   generation_job_id    what the run does
    ───────────────────  ───────────────────  ──────────────────────────────
    empty                empty or one id      evaluates its one target
    one id or more       empty or one id      starts one run per id (the
                                              list, then the single id;
                                              blanks and repeats dropped)
                                              and evaluates nothing itself

The runs go one after another: `max_active_runs` is 1. That is the knob for
evaluating several jobs at once; raising it to N means up to N evaluation
Dataflow jobs running at the same time, each with its own workers (quota and
cost), and two runs on the same target can then close each other's registry
row (see `_close_running_row`).

Like the rest of this DAG, the fan-out has not been parsed or run by Airflow.

The launcher writes the RUNNING registry row, mints the evaluation id and
submits the job; the pipeline writes the FINAL row. A job that dies after
submission leaves only the RUNNING row, so a failed launch task closes it
with a FAILED row (`_close_running_row`) — but only when the job cannot
write its own: the task can fail on the Airflow side (a deferral timeout, a
lost trigger, a cleared task) while the Dataflow job runs on, and a FAILED
row written then would be followed by the job's FINAL row. The callback
reads the job's state first (`_job_state`) and writes nothing while the job
is not in a terminal state other than done (`_callback_closes_row`).
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
from airflow.operators.empty import EmptyOperator
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
project_version = "{{PROJECT_VERSION}}"
env_name = "{{ENV}}"

# -----------------------------------------------------------------------------
# Runtime infra — Composer Variables, set once per env (not build-time-baked).
# -----------------------------------------------------------------------------
project_id = Variable.get("PROJECT_ID")
region = Variable.get("REGION")
subnetwork = Variable.get("DATAFLOW_SUBNET")
service_account = Variable.get("SA_DATAFLOW")
# Optional: semicolon-separated VPC network tags for the launcher VM and the
# workers. Empty ⇒ no tag experiments.
network_tags = Variable.get("DATAFLOW_NETWORK_TAGS", default_var="").strip()

app_domain = "synthetic"
app_name = "sdfb"
DAG_ID = "sdfb_evaluation_framework"
# The ONE template of the deployment, the generation DAG's own (header: how
# to point this at an evaluator-only template instead).
flex_template = f"sdfb-{project_version}-template.json"
# Dataflow job names: lowercase, digits, hyphens only.
_job_name_prefix = f"{app_name}-evaluation-v{project_version.replace('.', '-').lower()}"
# The workers run the image the template was built on. The shared image is
# multi-GB (it carries the generator's GPU libraries) and Dataflow's 25 GB
# default boot disk overflows while a worker unpacks it; the evaluator pins
# no disk size, so the launch passes Beam's own --disk_size_gb. 200 is what
# the generator pins for its own workers on that image.
EVALUATION_WORKER_DISK_GB = "200"

network_tag_experiments = ([
    f"use_network_tags={network_tags}",
    f"use_network_tags_for_flex_templates={network_tags}",
] if network_tags else [])

# The registry table of the evaluator's output dataset (D7). The dataset comes
# from the `output_dataset` DAG param, validated by `_DATASET_PATTERN` before it
# is part of SQL text; every other value reaches BigQuery as a query parameter.
_REGISTRY_TABLE = "evaluation_data_history"
_DATASET_PATTERN = r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)?"

# Appends the FAILED close of a RUNNING row whose job died after submission
# (Task 29): the launcher mints the evaluation id and
# its RUNNING row has no job id, so the row is found by launch target (the
# generation job id when given, else the base run id), trigger and DAG run
# start. Copying the row keeps every schema-required field. An evaluation that
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


def _fan_out_confs(params):
  """The runs to start for a run that names a list of generation jobs: one
  conf per job id, or none.

  The ids are `generation_job_ids` followed by `generation_job_id` when it is
  set, blanks dropped, a repeated id kept once, in that order. With no id in
  the list there is nothing to fan out and the result is empty: the run is a
  single-target run (by `generation_job_id`, `run_id` or `tables`). Otherwise
  each conf is the run's params with `generation_job_id` set to its one id
  and `generation_job_ids` emptied, so the run it starts never fans out again.

  A list together with `run_id` or `tables` is refused with one message
  (ValueError): each child run would otherwise start and its launcher refuse
  two targets.
  """
  listed = [
      str(job_id or "").strip()
      for job_id in params["generation_job_ids"] or []
  ]
  if not any(listed):
    return []
  other = [
      name for name in ("run_id", "tables") if str(params[name] or "").strip()
  ]
  if other:
    raise ValueError(
        f"generation_job_ids names {len([i for i in listed if i])} job(s) and "
        f"{' and '.join(other)} is also set: a run has one target. Empty "
        f"{' and '.join(other)}, or leave generation_job_ids empty.")
  job_ids = []
  for job_id in (*listed, str(params["generation_job_id"] or "").strip()):
    if job_id and job_id not in job_ids:
      job_ids.append(job_id)
  shared = {name: params[name] for name in params}
  return [{
      **shared, "generation_job_id": job_id,
      "generation_job_ids": []
  } for job_id in job_ids]


def _fan_out_run_id(job_id, ts_nodash):
  """The id of the run started for `job_id`: it names the job and the
  parent run's logical date, so it is unique within the parent run and the
  same when the parent's task runs again."""
  return f"fan_out__{ts_nodash}__{job_id}"


def _fan_out(params, **context):
  """The run's first decision (ShortCircuit): True to go on as a
  single-target run, False once it has started one run of this DAG per
  listed job id, which skips every other task of this run.

  A run id Airflow already has means this task ran before and started that
  run then: it is left alone and the remaining ids are still started.
  """
  confs = _fan_out_confs(params)
  if not confs:
    return True
  import logging

  from airflow.api.common.trigger_dag import trigger_dag
  from airflow.exceptions import DagRunAlreadyExists

  for conf in confs:
    run_id = _fan_out_run_id(conf["generation_job_id"], context["ts_nodash"])
    try:
      # Airflow's own function (what TriggerDagRunOperator calls), not an
      # operator executed inside a callable. Microseconds are kept so that
      # runs started in the same second get distinct logical dates.
      trigger_dag(
          dag_id=DAG_ID,
          run_id=run_id,
          conf=conf,
          replace_microseconds=False,
      )
    except DagRunAlreadyExists:
      logging.info("evaluation run %s already exists: not started again",
                   run_id)
  return False


def _wait_for_generation(params, **_):
  """Gate: wait for the generation job only when asked and when it is known."""
  return bool(params["wait_for_generation"] and params["generation_job_id"])


def _callback_closes_row(job_id, state):
  """Whether the failure callback writes the FAILED row.

  Only when a job id is known and the job ended in a terminal state other
  than done. With no id the state was never read, so nothing is written: a
  launch that failed has already got its FAILED row from the launcher (or
  never wrote RUNNING), and a RUNNING row left open is the lesser harm than
  a FAILED row on a run that went on to succeed. A job still running, a job
  that is done and a state that could not be read (None) leave the row to
  the job.
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
  """on_failure_callback: close this DAG run's RUNNING row with FAILED.

  Nothing is written without a job id, nor while the launched job may still
  write its own FINAL row (`_callback_closes_row`): never two terminal rows
  for one evaluation, and never FAILED with the job's state unread.
  """
  import logging
  import re

  from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook

  params = context["params"]
  dataset = str(params["output_dataset"])
  if not re.fullmatch(_DATASET_PATTERN, dataset):
    logging.error("evaluation registry not updated: bad output_dataset %r",
                  dataset)
    return
  registry = (f"{dataset}.{_REGISTRY_TABLE}" if "." in dataset else
              f"{project_id}.{dataset}.{_REGISTRY_TABLE}")
  target = params["generation_job_id"] or params["run_id"]
  if not target:
    logging.warning(
        "evaluation registry not updated: a launch targeted by tables only "
        "has no generation_job_id or run_id to find its RUNNING row by")
    return
  ti = context["ti"]
  job_id = _launched_job_id(
      ti.xcom_pull(task_ids="start_evaluation"),
      ti.xcom_pull(task_ids="start_evaluation", key="dataflow_job_config"))
  if not job_id:
    logging.warning(
        "evaluation registry not updated: no Dataflow job id in XCom, so the "
        "job's state was not read. A launch that failed has its own FAILED "
        "row (or never wrote RUNNING); a RUNNING row is left open rather "
        "than closed over a job that may have succeeded")
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
  reason = (f"Dataflow evaluation job failed after launch{ended}: "
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
                  string("trigger", params["trigger"]),
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


# -----------------------------------------------------------------------------
# DAG params — runtime-overridable on every trigger. Exactly one target
# (generation_job_id | run_id | tables) must be non-empty; the launcher refuses
# otherwise. Empty means "not given" for the optional template parameters.
# `generation_job_ids` is not a target of a launch: it makes the run start one
# single-target run per id (header).
# -----------------------------------------------------------------------------
default_dag_params = {
    "generation_job_id":
        Param(
            default="",
            type="string",
            description="Target: the generation job's Dataflow id. Empty when "
            "targeting by run_id or tables.",
        ),
    "generation_job_ids":
        Param(
            default=[],
            type="array",
            items={"type": "string"},
            description="Several generation jobs' Dataflow ids. Each id is "
            "evaluated by its own run of this DAG, one Dataflow job per id, "
            "one after another; this run only starts them. Leave run_id and "
            "tables empty with it.",
        ),
    "run_id":
        Param(
            default="",
            type="string",
            description="Target: the generation launch's base run id.",
        ),
    "tables":
        Param(
            default="",
            type="string",
            description="Target: landing table names, comma-separated, parents "
            "first. Needs landing_dataset and reference_dataset.",
        ),
    "landing_dataset":
        Param(
            default="",
            type="string",
            description="With tables: the dataset the tables landed in.",
        ),
    "reference_dataset":
        Param(
            default="",
            type="string",
            description="With tables: the dataset of their sources.",
        ),
    "relationships_uri":
        Param(
            default="",
            type="string",
            description="The relationship model file or directory (gs://), "
            "when the launch's own cannot be found.",
        ),
    "mode":
        Param(
            default="",
            type="string",
            enum=["", "exact", "sampled"],
            description="exact reads every row; sampled reads a salted sample "
            "above the template's sample_rows. Empty: the evaluator's "
            "default.",
        ),
    "allow_contaminated":
        Param(
            default=False,
            type="boolean",
            description="Evaluate a scope another writer touched; it stays "
            "marked contaminated.",
        ),
    "output_dataset":
        Param(
            default="synthetic_data_quality",
            type="string",
            pattern=f"^{_DATASET_PATTERN}$",
            description="The dataset of the four evaluation tables, "
            "dataset or project.dataset.",
        ),
    "trigger":
        Param(
            default="composer",
            type="string",
            enum=["composer", "chained"],
            description="What started the evaluation, recorded in the "
            "registry: composer = a manual run of this DAG, chained = a run "
            "started for a generation that has just finished.",
        ),
    "wait_for_generation":
        Param(
            default=False,
            type="boolean",
            description="Wait (deferrable sensor) for the generation job to "
            "reach JOB_STATE_DONE before launching. Needs generation_job_id.",
        ),
    "machine_type":
        Param(
            default="e2-standard-8",
            type="string",
            description="Dataflow worker machine type (CPU; the evaluator "
            "needs no GPU).",
        ),
    "max_workers":
        Param(
            default=4,
            type="integer",
            minimum=1,
            description="Dataflow maxWorkers.",
        ),
}

with models.DAG(
    dag_id=DAG_ID,
    start_date=days_ago(1),
    schedule_interval=None,
    catchup=False,
    max_active_runs=1,
    tags=["SYNTHETIC", "Dataflow", "EVALUATION",
          env_name.upper()],
    params=default_dag_params,
) as dag:
  begin = EmptyOperator(task_id="begin")

  # Several job ids: start one run per id and skip the rest of this one.
  # The default downstream handling is wanted here: returning False skips
  # EVERY task below, the launch included, whatever its trigger rule.
  fan_out = ShortCircuitOperator(
      task_id="fan_out",
      python_callable=_fan_out,
  )

  wait_gate = ShortCircuitOperator(
      task_id="wait_gate",
      python_callable=_wait_for_generation,
      # Skip only the sensor: the launch still runs behind `begin`.
      ignore_downstream_trigger_rules=False,
  )

  wait_for_generation_job = DataflowJobStatusSensor(
      task_id="wait_for_generation_job",
      job_id="{{ params.generation_job_id }}",
      expected_statuses={"JOB_STATE_DONE"},
      project_id=project_id,
      location=region,
      deferrable=True,
  )

  start_evaluation = DataflowStartFlexTemplateOperator(
      task_id="start_evaluation",
      project_id=project_id,
      location=region,
      body={
          "launchParameter": {
              "containerSpecGcsPath":
                  f"gs://{templates_path}/synthetic/{flex_template}",
              "jobName":
                  f"{_job_name_prefix}-{{{{ ts_nodash | lower }}}}",
              "environment": {
                  "tempLocation": f"gs://{bucket_path}/temp/",
                  "stagingLocation": f"gs://{bucket_path}/staging",
                  "subnetwork": subnetwork,
                  "ipConfiguration": "WORKER_IP_PRIVATE",
                  "serviceAccountEmail": service_account,
                  "additionalExperiments": [
                      "use_runner_v2",
                      "enable_secure_boot",
                      *network_tag_experiments,
                  ],
                  "additionalUserLabels": {
                      "app": app_name,
                      "env": env_name,
                      "dag": DAG_ID,
                  },
                  "machineType": "{{ params.machine_type }}",
                  "maxWorkers": "{{ params.max_workers }}",
                  "workerRegion": region,
              },
              # Names are the flex template's (docker/flex_template_metadata.json;
              # the evaluator's own deploy/flex_template_metadata.json declares
              # the same ones). The launcher supplies runner, project and region
              # itself; the image carries its worker image coordinate; fail_on
              # and experiments are never passed.
              "parameters": {
                  # The image's one entry runs generation unless told otherwise.
                  "sdfb_job":
                      "evaluation",
                  "disk_size_gb":
                      EVALUATION_WORKER_DISK_GB,
                  "job_id":
                      "{{ params.generation_job_id }}",
                  "run_id":
                      "{{ params.run_id }}",
                  "tables":
                      "{{ params.tables }}",
                  "landing_dataset":
                      "{{ params.landing_dataset }}",
                  "reference_dataset":
                      "{{ params.reference_dataset }}",
                  "relationships_uri":
                      "{{ params.relationships_uri }}",
                  "mode":
                      "{{ params.mode }}",
                  "allow_contaminated":
                      "{{ params.allow_contaminated | string | lower }}",
                  "output_dataset":
                      "{{ params.output_dataset }}",
                  "trigger":
                      "{{ params.trigger }}",
              },
          }
      },
      # Wait (deferrably) so a job that fails after launch fails this task and
      # reaches the callback; the job's own FINAL row is written by the pipeline.
      wait_until_finished=True,
      deferrable=True,
      do_xcom_push=True,
      # The sensor branch may be skipped; the launch needs fan_out's success.
      trigger_rule="none_failed_min_one_success",
      on_failure_callback=_close_running_row,
  )

  begin >> fan_out  # pylint: disable=pointless-statement  # Airflow dependency operator
  fan_out >> wait_gate >> wait_for_generation_job >> start_evaluation  # pylint: disable=pointless-statement  # Airflow chain
  fan_out >> start_evaluation  # pylint: disable=pointless-statement  # Airflow dependency operator
