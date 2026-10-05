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
"""Airflow DAG — launch the sdfb-evaluation Flex Template.

Runs in an Airflow/Cloud Composer environment. This file is the *template*:
workflow `3_import_dag.yaml` runs `sed` over it at deploy time to substitute
build-time values. Runtime values (the evaluation target, the mode, the
worker shape) come from Airflow DAG params — operators never re-import the
DAG to change them.

The DAG id is fixed (`sdfb_evaluation_framework`): the generation DAG's
opt-in `run_evaluation` task triggers it by that id.

Substitution markers:
  {{EVALUATOR_VERSION}}       version of the sdfb-evaluation package whose
                              template `sdfb-evaluation-<version>-template.json`
                              was built (deploy/build_flex_template.sh).
                              NEW: the deploy workflow's substitution list
                              must add it; without it the DAG would carry the
                              literal marker as its version.
  {{ENV}}                     dev | uat | prd
  {{GCS_DATAFLOW_STAGING}}    <env>-…-dataflow-staging bucket name
  {{GCS_DATAFLOW_TEMPLATES}}  <env>-…-dataflow-templates bucket name

Runtime values: Airflow DAG params ({{ params.* }}) + Airflow Variables for
infra (PROJECT_ID, REGION, DATAFLOW_SUBNET, SA_DATAFLOW, and the optional
DATAFLOW_NETWORK_TAGS).

Task graph::

    begin ─► wait_gate ─► wait_for_generation_job ─► start_evaluation
      └───────────────────────────────────────────────────▲
    (wait_for_generation false: the gate skips the sensor, begin still
    reaches the launch.)

The launcher writes the RUNNING registry row, mints the evaluation id and
submits the job; the pipeline writes the FINAL row. A job that dies after
submission leaves only the RUNNING row, so a failed launch task closes it
with a FAILED row (`_close_running_row`).
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
evaluator_version = "{{EVALUATOR_VERSION}}"
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
flex_template = f"sdfb-evaluation-{evaluator_version}-template.json"
# Dataflow job names: lowercase, digits, hyphens only.
_job_name_prefix = f"{app_name}-evaluation-v{evaluator_version.replace('.', '-').lower()}"

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


def _wait_for_generation(params, **_):
  """Gate: wait for the generation job only when asked and when it is known."""
  return bool(params["wait_for_generation"] and params["generation_job_id"])


def _close_running_row(context):
  """on_failure_callback: close this DAG run's RUNNING row with FAILED."""
  import logging
  import re

  from airflow.providers.google.cloud.operators.bigquery import (
      BigQueryInsertJobOperator,)

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
  launched = context["ti"].xcom_pull(task_ids="start_evaluation")
  job_id = launched.get("id", "") if isinstance(launched, dict) else ""
  exception = context.get("exception")
  reason = ("Dataflow evaluation job failed after launch: "
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

  BigQueryInsertJobOperator(
      task_id="close_running_row",
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
  ).execute(context)


# -----------------------------------------------------------------------------
# DAG params — runtime-overridable on every trigger. Exactly one target
# (generation_job_id | run_id | tables) must be non-empty; the launcher refuses
# otherwise. Empty means "not given" for the optional template parameters.
# -----------------------------------------------------------------------------
default_dag_params = {
    "generation_job_id":
        Param(
            default="",
            type="string",
            description="Target: the generation job's Dataflow id (the chained "
            "trigger passes it). Empty when targeting by run_id or tables.",
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
            "registry: composer = a manual run, chained = the generation "
            "DAG's run_evaluation trigger.",
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
    dag_id="sdfb_evaluation_framework",
    start_date=days_ago(1),
    schedule_interval=None,
    catchup=False,
    max_active_runs=1,
    tags=["SYNTHETIC", "Dataflow", "EVALUATION",
          env_name.upper()],
    params=default_dag_params,
) as dag:
  begin = EmptyOperator(task_id="begin")

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
                      "dag": "sdfb_evaluation_framework",
                  },
                  "machineType": "{{ params.machine_type }}",
                  "maxWorkers": "{{ params.max_workers }}",
                  "workerRegion": region,
              },
              # Names are the flex template's (deploy/flex_template_metadata.json).
              # The launcher supplies runner, project and region itself; the image
              # carries its worker image coordinate; fail_on and experiments are
              # never passed.
              "parameters": {
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
      # The sensor branch may be skipped; the launch needs begin's success.
      trigger_rule="none_failed_min_one_success",
      on_failure_callback=_close_running_row,
  )

  begin >> wait_gate >> wait_for_generation_job >> start_evaluation  # pylint: disable=pointless-statement  # Airflow dependency operator
  begin >> start_evaluation  # pylint: disable=pointless-statement  # Airflow dependency operator
