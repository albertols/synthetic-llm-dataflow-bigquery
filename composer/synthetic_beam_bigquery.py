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
"""Airflow DAG — the synthetic-dataflow-bigquery generation launch: one
table (`tables=explicit`) or every enabled table of a relationships model
(`tables=all`), each followed by its evaluation (ADR 0043, 0045 D5-D7).

The DAG file carries no deploy marker and no default of its own. Workflow
`3_import_dag.yaml` imports it with three files beside it in the DAGs
folder: `sdfb_batches.py` (the launch helpers; Composer cannot import
sdfb-core), `generator.yaml` (sdfb-core's defaults: the file
`config/generator.yaml` links to, copied byte for byte) and `sdfb_env.yaml`
(the env overlay: of these four files, the only one the workflow runs its
`sed` over; docs/RUN_PLAYBOOK.md §0b is the recipe). At parse time
`sdfb_batches.load_layers` reads the two YAML files from
this file's folder: every param default is generator.yaml overlaid with
`sdfb_env.yaml`'s `config:`, and the DAG's id
(`synthetic_sdfb_<dag_version>`), its Flex Template
(`sdfb-<project_version>-template.json`), its buckets and its job names come
from the overlay's `dag:` and `infra:`. Precedence: generator.yaml <
sdfb_env.yaml < the trigger's values < a `config_uri` file.

Runtime infra comes from Composer Variables, read at parse time:
PROJECT_ID, REGION, DATAFLOW_SUBNET, SA_DATAFLOW, the optional
DATAFLOW_NETWORK_TAGS (semicolon-separated VPC network tags for the launcher
VM and the workers) and the optional SDFB_MAX_PARALLEL_JOBS (the default of
the `max_parallel_jobs` param, how many batch jobs of a `tables=all` launch
run at once: the Variable when it is set, else launch.max_parallel_jobs of
generator.yaml overlaid by sdfb_env.yaml; never below 1,
`sdfb_batches.parallel_cap`). That default is read when the file is parsed,
so a change to either takes effect at the next parse, without a re-import;
a trigger's `max_parallel_jobs` wins for its run.

The two branches::

    route ─┬─► start_sdfb ─► run_evaluation_gate ─► wait_for_generation
           │   (explicit:    (skips the rest when    (reschedule-mode
           │    no wait)      run_evaluation is off)  sensor on the job)
           │       ─► trigger_evaluation ─► wait_for_evaluation_launch
           │
           └─► batch_bodies ─► bundle[b00 … bNN] ─┬─► fail_if_a_batch_failed
               (all: the       (mapped task group, │   (one_failed)
                planned         one per batch)     └─► fail_if_a_table_failed
                bodies)                                (all_done: the launch's
                                                        validation_runs rows)

    bundle[i]:  gate ─► launch ─► wait_generation ─► eval_body ─► trigger_eval
                (sensor,  (Flex,   (sensor, the job   (skips with    (the
                 waits     no      DONE; ends the     the evaluation  bundle's
                 for the   wait)   bundle's slot)     off)            evaluation)
                 cap)                          ─► wait_eval_launch

`route` (a `@task.branch`) resolves the launch's shape with
`sdfb_batches.resolve_tables_mode`: a `config_uri` file's `launch.tables`
wins over the `tables` param (ADR 0043), so a file saying `all` takes the
batch branch whatever the trigger says. `route` reads `config_uri` through
`GCSHook` only when the param is set: an explicit launch without it reads
nothing from GCS, as master's DAG read nothing (the launcher reads the
relationship model itself). The file's serving.client_type, serving.gpu,
serving.sdk_containers and serving.launcher_machine_type go over the params
before any launch body is built (`with_file_hardware`, Rulings R16, R16b):
the machine, the accelerator, the SDK-container experiment and the launcher
VM are the Flex launch's own, so a file's `gpu: t4` or `client_type: fake`
must reach them, not only the launcher. A file or a
plan the DAG cannot use (`sdfb_batches.PlanError`: bad YAML, a bad
`launch:` or `serving:` value, a missing or non-UTF-8 object, a model that
is no gs:// file, K below one) fails `route` with `AirflowFailException`,
which is never retried: the same file fails the same way every time. Any
other error (a transient GCS read) is retried as the environment says.
Explicit: `route` mints the run id (`<dag run id>-<8 hex>`: a retrigger of
the same logical date must not replay the same rows), pushes
`sdfb_batches.explicit_body` under `body` and returns `start_sdfb`. All: it
reads the model file (`planning_inputs`: a config file's
relational.relationships_uri and launch.max_tables_per_job win over the
params), plans with `sdfb_batches.launch_plan` (the plan each batch's
launcher recomputes and checks against `batch_count`), pushes the bodies, the
base run id, the batch count, the model file, the batches, the banner and the
verdict inputs (`sdfb_batches.verdict_inputs`), and returns `batch_bodies`.
That task only returns the bodies: Airflow maps a task only over another
task's return value, and a branch's return value is the branch it takes.

A launch passes only the keys whose value differs from generator.yaml, the
launch identity and the yaml's digest (`launch_overrides`): a key it does not
pass takes the image's generator.yaml value, and the launcher logs
`generator_defaults_mismatch` when the DAG's copy differs from the image's.
A `tables=all` launch whose `source_dataset` or `landing_dataset` is empty
takes the `project.dataset` of the overlay's default source table and landing
table (`whole_model_params`).

The explicit branch is master's launch. With `run_evaluation` False the gate
skips everything after the generation launch: the DAG runs `start_sdfb` and
nothing else. With it True the gate builds the evaluation's launch body
(`sdfb_batches.evaluation_body`, from the generation job id `start_sdfb`
returned and the branch `route` resolved), the sensor waits for the
generation job to reach JOB_STATE_DONE (a job that fails or is cancelled
fails the sensor, after the task's retries) and `trigger_evaluation`
launches the evaluation as a second, CPU-only Dataflow job from the same
Flex Template, with the template parameter `sdfb_job=evaluation` (one image,
one template, one DAG import; ADR 0041's amendment of 2026-10-06).
`wait_for_evaluation_launch` then waits until that job runs, which is when
its launch is over ("who writes the terminal row", below). No other DAG is
triggered. Neither sensor is deferrable, so no triggerer is needed:
`deferrable` is not passed, and the provider's default is off unless the
environment sets `[operators] default_deferrable`.

The `all` branch runs one bundle per batch, a mapped task group (ADR 0045
D5, D7): every task of `bundle[i]` shares the batch's map index. Its `gate`
waits for the cap (below), its `launch` starts one Flex Template job:
`tables=all`, `batch_index`, `batch_count`, `run_id=<base>`, the job named
`<job name>-bNN`, to which the provider appends `-<8 hex>` (its
`append_job_name`), and returns at once; its `wait_generation`
(`_GenerationWaitSensor`) waits for the job in reschedule mode, holding no
worker slot. A batch job that ends in any state but DONE fails its bundle's
wait, without a retry, and the other bundles run on; except a FAILED job
that ran no work item and whose messages name a capacity cause (a zone out
of GPUs, a quota exceeded: `sdfb_batches.is_capacity_failure`), which the
wait relaunches, the same body and run ids (nothing landed), named
`<job name>-bNN-rN-<8 hex>`, 10, 20, then 40 minutes after the failure, at
most the run's `capacity_retries` times (ADR 0045 D10). The bundle keeps its
slot of the cap meanwhile. The attempts are an XCom of the bundle's
`launch` (`ATTEMPT_KEY`): a reschedule-mode sensor's own XComs are cleared
at each poke. Once its
job is DONE, the bundle evaluates it: `eval_body` builds the evaluation of
that one job (`--run_id <base> --generation_job_ids <the job>`: the
evaluator reads that job's rows of `validation_runs`, so every table is
compared with the source its row names), named `<evaluation name>-bNN-<ts>`;
`trigger_eval` launches it on the CPU, outside the cap; `wait_eval_launch`
waits for its launch. A bundle whose generation failed never evaluates (its
evaluation tasks are upstream-failed), and no other bundle waits for it.
With `run_evaluation` off, `eval_body` skips and the evaluation with it.

A batch job ends DONE whatever its tables' gates found: each table's
verdict is its `validation_runs` row (PASSED, FAILED_BLOCKER or
FAILED_PREFLIGHT, with a `status_reason`). So `fail_if_a_table_failed`, once
every bundle's generation has ended, reads the launch's rows by the base run id
(`sdfb_batches.verdicts_query`, through `BigQueryHook`) and fails the run
with `sdfb_batches.verdict_banner`, naming each planned table whose newest
row is not PASSED or that has no row (its batch did not reach DONE). A
planned table is named in the landing dataset the launchers write to: a
config file's io.landing_dataset when it sets one. Under
`client_type=fake` a FAILED_BLOCKER is informational. A `validation_runs`
table without the `status_reason` column is read without it; a
`validation_runs_table` that is no `project.dataset.table` fails the leaf
without a retry, and any other read error is retried. With no
`validation_runs_table` (the overlay's, or a config file's io key) nothing
is recorded: the leaf skips with a warning, and the run's outcome is the
batch jobs' alone. The evaluation of each DONE batch runs either way.

Trigger rules, and why::

    task                              rule          why
    ────────────────────────────────  ────────────  ──────────────────────────
    route                             all_success   the first task
    start_sdfb, batch_bodies          all_success   the branch skips the one
                                                    not taken, and every
                                                    all_success task after it
    the explicit chain                all_success   skipped with start_sdfb
    bundle[i], every task             all_success   a bundle runs on its own,
                                                    each task after the one
                                                    before it in the same
                                                    bundle: one that fails
                                                    stops no other bundle
                                                    (the DAG sets no
                                                    fail_stop); skipped with
                                                    batch_bodies, so the
                                                    skipped branch never
                                                    launches or evaluates
    fail_if_a_batch_failed            one_failed    over every bundle's
                                                    wait_generation: fails
                                                    once a bundle's gate,
                                                    launch or wait failed;
                                                    skipped when every
                                                    generation succeeded, the
                                                    explicit branch ran, or
                                                    `route` failed (which
                                                    fails the run itself)
    fail_if_a_table_failed            all_done      over every bundle's
                                                    wait_generation; fails
                                                    when a planned
                                                    table's newest
                                                    validation_runs row is not
                                                    PASSED, or has no row;
                                                    skips when `route` planned
                                                    no batch or the record is
                                                    off; a FAILED_BLOCKER is
                                                    informational with
                                                    client_type=fake

The launch gate (ADR 0045 D5). Every bundle's `gate` starts at once: a
sensor in reschedule mode (`_LaunchGateSensor`) that reads the bundles'
`wait_generation` states every two minutes (`GATE_POKE_SECONDS`) and is done
once the wait of every bundle `cap` or more indexes below it has ended
(`sdfb_batches.may_launch`; `cap` is the run's `max_parallel_jobs` param).
Between two reads Airflow keeps it up for reschedule and it holds no worker
slot. So at most `cap` generation jobs run at once, a bundle waits only on
lower ones, and an evaluation holds no slot. It has to be a sensor: only a
sensor's task instances carry `ReadyToRescheduleDep`, so only a sensor's
reschedule date is honoured. The operator gate it replaces raised
AirflowRescheduleException from `pre_execute` and was queued again within
1 to 4 seconds, about 25,000 times over the 2026-11-03 launch; once the
executor reported such an instance finished while its state read queued,
and the scheduler failed it (b11, no job). A reschedule keeps the try. The
DAG sets no task-level limit (`max_active_tis_per_dag`,
`max_active_tis_per_dagrun`) on any task, and must not: with one, the
scheduler checks the serialized DAG for every instance it queues, and when
the DAG is missing from `serialized_dag` (the stale-DAG sweep of a standalone
DAG processor, `[scheduler] dag_stale_not_seen_duration`, deletes it until
the file is parsed again) it fails every SCHEDULED instance of the DAG, with
no start date and no retry. The 2026-11-02 launch lost 13 of its 14 batches
that way, parked in SCHEDULED by `max_active_tis_per_dag`. Without a
task-level limit the scheduler never makes that check, so an instance that
waits SCHEDULED (for a pool slot or the DAG's `max_active_tasks`, or between
a reschedule and its next poke) only waits. A waiting instance holds no
worker slot either: had it slept at the gate, the waiting instances could
take every slot of a full environment and leave the later ones QUEUED until
`[scheduler] task_queued_timeout` failed them.

Run slot: a run stays running until the generation job ends (the first
sensor waits for it, hours) and then until the evaluation's launch is over
(the second sensor): at most the launcher's twelve minutes and one 60-second
poke; on the failure path, plus the task's retries (below); and one hour, the
sensor's timeout, for a job that stays queued. A `tables=all` run holds it
until every bundle has ended: its generation job, then its evaluation's
launch. The DAG has `max_active_runs=1`, so every
later trigger queues behind it: launches run one at a time. `max_active_runs`
is the knob; raising it lets several launches run at once (mind the GPU
quota).

Retries: no task of the explicit chain passes `retries` and the DAG passes
no `default_args`, so the environment decides. Cloud Composer's default is
two retries, five minutes apart, for any task that fails. A sensor that
raises on a dead job is therefore "up for retry" first, and its failure
callback runs after the task's last retry: under that default about ten
minutes after the sensor first saw the dead job, with the registry row still
RUNNING meanwhile. That is kept on purpose (a transient error in one poke
must not end the watch). A sensor that ends on its timeout is not retried.
The same default launches a failed `trigger_evaluation` (and a bundle's
`trigger_eval`) again. A bundle's `launch` passes `retries=0`: a launch call
that was accepted and then failed (its status read raised) would launch the
whole batch again under the same base run id, which replays the same rows,
and an append launch would land them twice. Its `gate` and
`wait_generation` take the environment's retries: a poke that fails on a
transient error is tried again, and a retried wait reads the same job (its
attempts are the launch's XCom). A job that ends in any state but DONE fails
the wait with AirflowFailException, which is never retried, unless it is a
capacity failure the wait relaunches (above): that is the one relaunch the
DAG makes on its own, and only when no row can have landed. To run a failed batch again, fix the
cause and clear its bundle's `launch` with its downstream tasks (and its
`gate`, if the gate failed); its lower siblings have ended, so it
launches at once. Clear it only once the run has ended: cleared while a
higher bundle's job runs, it launches beside it, one job over the cap. No
task of a bundle holds an Airflow worker slot while it waits: the gate and
both waits are reschedule-mode sensors, holding a slot only for each poke's
few seconds, and the launch returns once the job is queued. Marking a
running `wait_generation` failed does not cancel its Dataflow job: cancel
it in Dataflow.

The explicit evaluation (`sdfb_batches.evaluation_body`, master's
parameters) evaluates what the launch generated: the launched table
(`seed_table`) and its enabled group of tables in the relationship model, or
that table alone; each landed table is read whole and compared with the table
of the same name in the source dataset. When the launch wrote its generation
record (a `validation_runs` table with the ADR 0044 columns), the evaluator
takes the tables, their datasets, the relationship model and
`reference_rows_limit` from that record instead, so a `config_uri` file that
changed them is evaluated as generated; this DAG's values are the fallback (no
record, a job the evaluator cannot read, a record spanning several datasets).
In short:

- It reads neither the generation job's log nor BigQuery job labels, so its
  service account needs no `roles/logging.viewer` and no
  `roles/bigquery.resourceViewer`. The generation job's id is passed as the
  launch's identity: the evaluator reads the Dataflow job resource and
  nothing else about the job (needs `roles/dataflow.viewer`; without it the
  run goes on with a warning and no window). With the window it also reads
  this launch's rows of the generator's `validation_runs` table (below).
- `scope=manual` reads each landing table WHOLE: with `write_disposition`
  "overwrite" that is this launch's rows, with "append" it also includes the
  rows of earlier launches.
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
  generation record on those rows replaces the value this DAG passes with
  the launch's own. Without that table, or when the job's window
  cannot be read, they come out "not evaluated" with the reason on the row.
  Every other metric is evaluated on all rows either way.
- A landing table named differently from its source (a custom landing
  table name) is not found: the evaluation looks for the source's name in
  the landing dataset.
- What stays lost: the launch's log, so a model the launch adjusted
  (ADR 0038) is not seen; and, without a generation record, the table list
  is the seed's, not the launch's own. The service account also needs read on
  the generator's `validation_runs` table.

The evaluator options neither evaluation passes keep their defaults: the
"Evaluation only" entries of docker/flex_template_metadata.json (and
`sdfb-eval run --help`, in
packages/sdfb-evaluation/src/sdfb_evaluation/cli/main.py): sample_rows,
privacy_sample_rows, detection_sample_rows, pair_max_columns,
row_flags_top_k, row_flags_source_keys, max_bytes_billed, max_shuffle_gb,
temp_dataset, sink, allow_contaminated, thresholds_uri, label_key_uri and
fail_on (no effect on a template launch: the entry submits and returns).

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
closes here. A bundle's evaluation is closed the same way by its
`wait_eval_launch` and `_close_bundle_running_row`, which find the row by
that bundle's generation job id (the one id its evaluation names), never by
the launch's base run id, which every bundle's evaluation shares.

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
only the count tells a closed row from a statement that matched nothing.

Where that ends. The row is found by launch target, trigger and the run's
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

What has run, and what has not. `scripts/composer/check_dags.py` imports
this folder into a real Airflow 2.10.5 with the Google provider 12.0.0 and
runs fourteen triggers through `dag.test()`, every Google call patched (the
`BigQueryHook` read of `fail_if_a_table_failed` is served the scenario's
rows): the branch, the trigger rules, the mapped batches, the XComs and the
launch bodies are Airflow's own. Not run against Google: the provider's sensor
raising when the job it watches ends in a state it was not told to expect
(JOB_STATE_FAILED for a dead launcher; read in its source; if an installed
provider only poked on, the sensor would fail at its one-hour timeout and
the callback, reading the job as failed, would close the row then); that
while the launcher runs the job is not yet running (Google's page of job
statuses lists a Flex Template job that is being launched as Queued; no
page names the state a launch that timed out ends in: if it is not one of
the four the callback closes, the sensor ends on its timeout and the
callback writes nothing, and the playbook has the one-line check); the
environment's retries; a sensor in reschedule mode getting its
`on_failure_callback` called as written; the callbacks' two hook calls
(the standalone DAG's own); and the leaf's read of `validation_runs`
(`insert_job` and `result()`, as the callbacks call them; BigQuery's text
for a missing column is the one `sdfb_batches.MISSING_REASON_COLUMN`
matches, from BigQuery's error messages, not from a run).
"""

# Heavy or optional dependencies are imported lazily, where they are used.
# pylint: disable=import-outside-toplevel

# f-string fields keep single quotes: yapf 0.43 cannot parse PEP 701
# quote reuse, and pylint reads those quotes as inconsistent.
# pylint: disable=inconsistent-quotes

from __future__ import annotations

import os

from airflow import models
from airflow.decorators import task, task_group
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.models import Variable
from airflow.models.param import Param
from airflow.models.xcom_arg import XComArg
from airflow.operators.python import ShortCircuitOperator
from airflow.providers.google.cloud.operators.dataflow import (
    DataflowStartFlexTemplateOperator,)
from airflow.providers.google.cloud.sensors.dataflow import (
    DataflowJobStatusSensor,)
from airflow.sensors.base import BaseSensorOperator
from airflow.utils.dates import days_ago
from airflow.utils.trigger_rule import TriggerRule

# The launch helpers, imported from beside this file in the DAGs folder.
import sdfb_batches

# -----------------------------------------------------------------------------
# The DAG folder's defaults: generator.yaml and the env overlay beside this
# file (workflow 3 copies the one and fills the other's markers).
# -----------------------------------------------------------------------------
LAYERS = sdfb_batches.load_layers(os.path.dirname(os.path.abspath(__file__)))

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
# Optional: empty takes the overlaid yaml's launch.max_parallel_jobs.
max_parallel_jobs = Variable.get("SDFB_MAX_PARALLEL_JOBS", default_var="")
VARIABLES = {
    "PROJECT_ID": project_id,
    "REGION": region,
    "DATAFLOW_SUBNET": subnetwork,
    "SA_DATAFLOW": service_account,
    "DATAFLOW_NETWORK_TAGS": network_tags,
    "SDFB_MAX_PARALLEL_JOBS": max_parallel_jobs,
}
# How often a bundle waiting at the launch gate reads its siblings' states
# (ADR 0045 D5): a launch starts at most this long after the bundle it
# waited for ended. Rescheduled between reads, it holds no worker slot. The
# gate gives up after a week (a sensor's timeout), far past any launch.
GATE_POKE_SECONDS = 120
GATE_TIMEOUT_SECONDS = 7 * 86400

# The DAG's id, template, buckets, labels and job names (the overlay's
# `dag:` and `infra:`, and the Variables above), and every param's default.
CONSTANTS = sdfb_batches.dag_constants(LAYERS, VARIABLES)
DEFAULTS = sdfb_batches.param_defaults(LAYERS, VARIABLES)
dag_id = CONSTANTS["dag_id"]
env_name = CONSTANTS["labels"]["env"]

# -----------------------------------------------------------------------------
# Chained evaluation (run_evaluation). Nothing below is used unless the gate
# lets the run through.
# -----------------------------------------------------------------------------
# The generation job's id: `start_sdfb` does not wait, so its XCom holds it.
generation_job_id = "{{ ti.xcom_pull(task_ids='start_sdfb')['id'] }}"
# How the sensor waits: one status read every two minutes, for at most a day
# (the longest generation runs take hours). Between reads the task is
# rescheduled and holds no worker slot.
GENERATION_POKE_SECONDS = 120
GENERATION_TIMEOUT_SECONDS = 86400
# The evaluation job's id, the same way: `trigger_evaluation` does not wait
# either, so its XCom holds the id of the job it launched.
evaluation_job_id = "{{ ti.xcom_pull(task_ids='trigger_evaluation')['id'] }}"
# ... and a bundle's (ADR 0045 D7): the job its own trigger launched, the
# XCom of the same map index.
bundle_evaluation_job_id = ("{{ ti.xcom_pull(task_ids='bundle.trigger_eval', "
                            "map_indexes=ti.map_index)['id'] }}")
# Where a bundle's wait keeps its attempts (ADR 0045 D10): an XCom of the
# bundle's `launch`, the one task of the bundle that never runs again. A
# reschedule-mode sensor's own XComs are cleared at each poke.
ATTEMPT_KEY = "generation_attempt"
# How the second sensor waits for the evaluation's LAUNCH to be over (the job
# running): one status read a minute, for at most an hour. The launcher gives
# up after about 12 minutes; the hour bounds a job that stays queued.
EVALUATION_LAUNCH_POKE_SECONDS = 60
EVALUATION_LAUNCH_TIMEOUT_SECONDS = 3600
# What a chained launch records as the evaluation's trigger (the helpers pass
# it as the template parameter `trigger`). The failure callbacks find the
# launch's RUNNING row by it, so all of them read this one name.
EVALUATION_TRIGGER = sdfb_batches.EVALUATION_TRIGGER

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
# DAG params — runtime-overridable on every trigger. Each default is
# `sdfb_batches.param_defaults`' (generator.yaml overlaid with sdfb_env.yaml):
# none is written here. Using Param objects so {{ params.X }} resolves to the
# VALUE, not the definition. A generator param is text, as Flex Template
# parameters are, and a launch passes it only where it differs from
# generator.yaml.
# -----------------------------------------------------------------------------
default_dag_params = {
    "table_fqn":
        Param(
            default=DEFAULTS["table_fqn"],
            type="string",
            description="FQN of the source BigQuery table to clone (with "
            "tables=explicit).",
        ),
    "tables":
        Param(
            default=DEFAULTS["tables"],
            type="string",
            enum=["explicit", "all"],
            description="explicit: generate table_fqn (and, with "
            "generate_fk_relationships, its FK group) as one launch. all: "
            "generate every enabled table of the ONE model file "
            "relationships_uri names, in batch jobs of at most "
            "max_tables_per_job tables (ADR 0045). A config_uri file's "
            "launch.tables wins over this param.",
        ),
    "num_rows":
        Param(
            default=DEFAULTS["num_rows"],
            type="string",
            description="Number of synthetic rows to generate (per table, "
            "unless the model sets num_rows).",
        ),
    "engine":
        Param(
            default=DEFAULTS["engine"],
            type="string",
            enum=["b1_rag", "b2_library"],
            description="Generation engine.",
        ),
    "batch_size":
        Param(
            default=DEFAULTS["batch_size"],
            type="string",
            description="Records per LLM batch.",
        ),
    "similarity":
        Param(
            default=DEFAULTS["similarity"],
            type="string",
            description="Similarity to reference (0.0 random → 1.0 mimic).",
        ),
    "identity_cols":
        Param(
            default=DEFAULTS["identity_cols"],
            type="string",
            description="Comma-separated per-row-unique columns synthesized "
            "fresh each row (PK/UUID); never sampled from reference "
            "data. Empty disables identity-column synthesis.",
        ),
    "pk_cols":
        Param(
            default=DEFAULTS["pk_cols"],
            type="string",
            description="Comma-separated declared primary-key columns. Duplicate "
            "PK tuples divert to the DLQ (rule_id=pk.duplicate, "
            "BLOCKER). Empty = PK undeclared, rule idle.",
        ),
    "vllm_dtype":
        Param(
            default=DEFAULTS["vllm_dtype"],
            type="string",
            enum=["", "auto", "float16", "bfloat16"],
            description="vLLM --dtype override. Empty (default): derived from "
            "gpu by the launcher, t4 → float16, l4 → bfloat16 (ADR 0043). "
            "auto = checkpoint dtype (bf16 for Gemma/Qwen). float16 is what "
            "makes Qwen's bf16 checkpoints run on Turing (bf16 needs "
            "SM>=8.0; Qwen is fp16-safe). The worker refuses float16 for "
            "gemma-family checkpoints (fp16 Gemma silently emits empty "
            "output).",
        ),
    "vllm_max_model_len":
        Param(
            default=DEFAULTS["vllm_max_model_len"],
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
            default=DEFAULTS["client_type"],
            type="string",
            enum=["vllm", "fake"],
            description="vllm = real LLM on a GPU worker (GPU chosen by the `gpu` "
            "param); fake = CPU smoke (no GPU, e2 machine) that "
            "exercises the full Dataflow→BigQuery write path while L4 "
            "capacity is short.",
        ),
    "gpu":
        Param(
            default=DEFAULTS["gpu"],
            type="string",
            enum=["l4", "t4"],
            description="GPU profile when client_type=vllm; the DAG picks the "
            "worker machine and accelerator from it. l4 = g2-standard-8 + "
            "NVIDIA L4 (24GB) — the ONLY GPU in europe-west3 that runs "
            "Gemma 4. t4 = n1-standard-8 + NVIDIA T4 (16GB): plumbing "
            "profile. Gemma CANNOT run on T4 (bf16/SM7.5 + shared-memory "
            "limits — the vLLM client now fails fast, see "
            "handlers/vllm_client.py ModelGpuIncompatibleError); pair t4 "
            "with the qwen3_4b_instruct_2507 registry model via "
            "the overlay's serving.model_uri. Ignored when client_type=fake.",
        ),
    "seed":
        Param(
            default=DEFAULTS["seed"],
            type="string",
            description="Explicit base RNG seed (integer) for deliberate "
            "reproduction of a run. Empty (default) derives a fresh "
            "seed per (run_id, batch) — recommended.",
        ),
    "write_disposition":
        Param(
            default=DEFAULTS["write_disposition"],
            type="string",
            enum=["append", "overwrite"],
            description="Landing-table write mode; overwrite = WRITE_TRUNCATE. "
            "Landing only — DLQ/quality/RAG tables always append.",
        ),
    "create_if_not_exists":
        Param(
            default=DEFAULTS["create_if_not_exists"],
            type="string",
            enum=["false", "true"],
            description="Create the landing table on first write "
            "(CREATE_IF_NEEDED + derived schema).",
        ),
    "build_rag_layer":
        Param(
            default=DEFAULTS["build_rag_layer"],
            type="string",
            enum=["true", "false"],
            description="Populate the B.1 RAG chunk store from this run's "
            "reference sample (skipped in-launcher when the "
            "reference_digest already exists for the embedder "
            "id+version). Ignored when the overlay leaves "
            "io.rag_chunks_table empty.",
        ),
    "rag_row_docs_limit":
        Param(
            default=DEFAULTS["rag_row_docs_limit"],
            type="string",
            description="B.1 RAG: how many reference rows (the first N of the "
            "fingerprint-ordered sample) are embedded as row documents "
            "— the in-worker exemplar index and the rag_chunks row_doc "
            "prefix. A positive integer, clamped to the reference "
            "sample. A chunk store written with a smaller "
            "value is topped up with the missing rows when "
            "build_rag_layer is true.",
        ),
    "build_pool_layer":
        Param(
            default=DEFAULTS["build_pool_layer"],
            type="string",
            enum=["true", "false"],
            description="Build free-text pools in their own branch and persist "
            "them to synthetic_rag.freetext_pools (skipped "
            "in-launcher when this reference_digest + model_uri is "
            "already present — so 'true' is safe to leave on). "
            "Ignored when the overlay leaves io.freetext_pools_table "
            "empty. The table is NEVER auto-created: bq mk it from "
            "config/bq_schema/synthetic_rag/freetext_pools.schema.json.",
        ),
    "sdk_containers":
        Param(
            default=DEFAULTS["sdk_containers"],
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
            default=DEFAULTS["initial_workers"],
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
            default=DEFAULTS["autoscaling"],
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
            default=DEFAULTS["launcher_machine_type"],
            type="string",
            minLength=1,
            description="Machine type of the Flex Template LAUNCHER VM of the "
            "generation launch (not the workers): it pulls the multi-GB "
            "image, then reads every table's reference sample, measures the "
            "source fan-outs and declared PKs, builds the graph and hands it "
            "over, all inside Google's 12-minute launch limit. A larger one "
            "pulls faster. The limit itself cannot be set from a DAG. "
            "Never empty: an empty value would override Dataflow's own.",
        ),
    "uniqueness_mode":
        Param(
            default=DEFAULTS["uniqueness_mode"],
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
            default=DEFAULTS["freetext_expansion"],
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
            default=DEFAULTS["prompt_constraints"],
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
            default=DEFAULTS["prompt_debug"],
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
            default=DEFAULTS["source_stats"],
            type="string",
            enum=["sample", "off"],
            description="Per-column source_table_stats from the reference "
            "sample, computed driver-side before the graph (WS8 "
            "spec WS-B; zero DAG cost). Ignored table-write-wise "
            "when the overlay leaves io.source_stats_table empty.",
        ),
    "fk_parent_landing":
        Param(
            default=DEFAULTS["fk_parent_landing"],
            type="string",
            description="EXPERT OVERRIDE only (ADR 0029): parents are assumed "
            "landed in the landing dataset and this derives "
            "automatically. Set only when parents land in a "
            "DIFFERENT project.dataset.",
        ),
    "multi_table_mode":
        Param(
            default=DEFAULTS["multi_table_mode"],
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
            default=DEFAULTS["generate_fk_relationships"],
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
            default=DEFAULTS["relationships_uri"],
            type="string",
            description="Where the relational models live (ADR 0032): a folder "
            "or a single YAML file, local or gs://. Default: the "
            "config/relationships folder packaged in the image. "
            "Point it at gs://... to change PK/FK/identity with no "
            "rebuild and no BigQuery metadata edit — it is the ONLY "
            "source of relational truth. With tables=all: ONE model file "
            "on GCS (gs://<bucket>/<path>/<model>.yaml), which this DAG "
            "reads to plan the batches.",
        ),
    "source_dataset":
        Param(
            default=DEFAULTS["source_dataset"],
            type="string",
            description="The project.dataset holding the SOURCE tables. With "
            "tables=all, every table's source is read from it under its own "
            "name, unless the model gives the table a source_table; empty: "
            "the dataset of the overlay's default source table. With "
            "tables=explicit, only the chained evaluation reads it: each "
            "landed table is compared with the table of its name there; "
            "empty: the dataset of table_fqn.",
        ),
    "landing_dataset":
        Param(
            default=DEFAULTS["landing_dataset"],
            type="string",
            description="With tables=all: the project.dataset every table "
            "lands in, under its own name. Empty: the dataset of the "
            "overlay's landing table. Not read with tables=explicit, which "
            "lands in the overlay's landing table.",
        ),
    "max_tables_per_job":
        Param(
            default=DEFAULTS["max_tables_per_job"],
            type="string",
            description="With tables=all: the most tables one batch job "
            "generates; a connected component is never split (ADR 0045).",
        ),
    "prep_workers":
        Param(
            default=DEFAULTS["prep_workers"],
            type="string",
            description="Threads a launcher reads schemas, reference samples "
            "and source stats on; 1 = in turn.",
        ),
    "pool_seed_strategy":
        Param(
            default=DEFAULTS["pool_seed_strategy"],
            type="string",
            enum=["centroid", "kcenter", "kcenter_rotate"],
            description="How the 8 free-text prompt seeds are chosen (WS5 §3). "
            "centroid = control (today). kcenter = seeds span the "
            "column's modes. kcenter_rotate = re-seeded per ladder "
            "attempt (forfeits vLLM prefix caching by design). "
            "Change ONE arm per run, with the pool digest cleared "
            "first (RUN_PLAYBOOK §6c), or the arm measures nothing.",
        ),
    "config_uri":
        Param(
            default=DEFAULTS["config_uri"],
            type="string",
            description="gs:// path to a generator config YAML (ADR 0043). "
            "Keys it sets win over every param of this DAG, launch.tables "
            "included (a file saying all takes the batch branch); params "
            "fill the keys it leaves out. Empty = no file, and the DAG reads "
            "nothing from GCS for an explicit launch. The chained evaluation "
            "(run_evaluation) follows the file: it takes the launch's "
            "tables, relationship model and reference_rows_limit from the "
            "generation record in validation_runs (ADR 0044). It falls back "
            "to this DAG's params only when the launch wrote no record (the "
            "table lacks the columns or its schema was unreadable; the "
            "launcher then logs config_sets_evaluation_input for each key "
            "the file set), or when the evaluation cannot read the "
            "generation job (no roles/dataflow.viewer; its warnings then say "
            "the record could not be read).",
        ),
    "run_evaluation":
        Param(
            default=DEFAULTS["run_evaluation"],
            type="boolean",
            description="On by default. False: the DAG launches generation "
            "and nothing else. True: once the generation job (or every batch "
            "job of tables=all) has ended this DAG launches the evaluator as "
            "a second, CPU-only Dataflow job from the same template. It "
            "evaluates what the launch generated: the tables, datasets, "
            "relationship model and reference_rows_limit its generation "
            "records in validation_runs name (ADR 0044), else the launched "
            "table and, with generate_fk_relationships true, the rest of its "
            "group of tables in the relationship model; each landed "
            "table is read whole (with write_disposition append that "
            "includes earlier launches' rows). While it waits (for the "
            "generation job, then for the evaluation's launch to be over) "
            "the run holds the DAG's only active-run slot "
            "(max_active_runs=1), so launches of this DAG then run one "
            "at a time. A dead evaluation launch fails the run.",
        ),
    "evaluation_mode":
        Param(
            default=DEFAULTS["evaluation_mode"],
            type="string",
            enum=["", "exact", "sampled"],
            description="Only with run_evaluation. exact: every row of both "
            "sides is compared. sampled: a salted sample of a side that has "
            "more rows than the evaluator's sample_rows (200000). Empty "
            "(default): the evaluator's choice, exact on Dataflow.",
        ),
    "evaluation_machine_type":
        Param(
            default=DEFAULTS["evaluation_machine_type"],
            type="string",
            description="Only with run_evaluation. Machine type of the "
            "evaluation job's workers. CPU only: the evaluator needs no GPU. "
            "Default e2-standard-8.",
        ),
    "evaluation_launcher_machine_type":
        Param(
            default=DEFAULTS["evaluation_launcher_machine_type"],
            type="string",
            description="Only with run_evaluation. Machine type of the Flex "
            "Template LAUNCHER VM of the evaluation launch: it pulls the "
            "multi-GB image and plans the evaluation before the Dataflow job "
            "exists, all inside Google's 12-minute launch limit. A larger one "
            "pulls faster. The limit itself cannot be set from a DAG. "
            "Default e2-standard-8.",
        ),
    "max_parallel_jobs":
        Param(
            default=DEFAULTS["max_parallel_jobs"],
            type="string",
            pattern=r"^\s*[1-9][0-9]*\s*$",
            description="Only with tables=all. How many batch jobs run at "
            "once (ADR 0045 D5); each holds up to 4 workers' GPUs, so stay "
            "inside the regional GPU quota divided by 4. Default: the "
            "Variable SDFB_MAX_PARALLEL_JOBS when set, else "
            "launch.max_parallel_jobs of generator.yaml overlaid by "
            "sdfb_env.yaml, read when the DAG file was parsed. A bundle's "
            "evaluation does not count against it.",
        ),
    "capacity_retries":
        Param(
            default=DEFAULTS["capacity_retries"],
            type="integer",
            minimum=0,
            description="Only with tables=all. How many times a bundle "
            "whose batch job FAILED for capacity (a zone out of GPUs, a "
            "quota exceeded) and ran no work item is relaunched with the "
            "same run ids, 10, 20, 40 … minutes after the failure "
            "(ADR 0045 D10). Any other failure is never relaunched. 0 "
            "turns it off.",
        ),
    "evaluation_max_workers":
        Param(
            default=DEFAULTS["evaluation_max_workers"],
            type="integer",
            minimum=1,
            description="Only with run_evaluation. Upper bound on the number "
            "of workers the evaluation job may use (Dataflow maxWorkers). "
            "Default 8.",
        ),
    "evaluation_output_dataset":
        Param(
            default=DEFAULTS["evaluation_output_dataset"],
            type="string",
            description="Only with run_evaluation. The dataset (or "
            "project.dataset) that receives the four evaluation_* tables, and "
            "where the evaluator keeps the expiring tables it makes while it "
            "runs (source snapshots, scopes, samples). The service account "
            "needs write access there. Default: the dataset of the "
            "validation-runs table, else synthetic_data_quality.",
        ),
}


def _read_gcs_text(uri: str) -> str:
  """One gs:// object's text: the config file, the model file. Downloaded
  as bytes (no local file).

  What fails the same way on every retry is a PlanError: a path that is no
  gs:// one, an object that does not exist, bytes that are not UTF-8. Any
  other error (a transient GCS failure) is raised as it is and retried as
  the environment says."""
  from airflow.providers.google.cloud.hooks.gcs import GCSHook
  from google.api_core.exceptions import NotFound

  if not uri.startswith("gs://"):
    raise sdfb_batches.PlanError(
        f"{uri!r}: the DAG reads config and model files from GCS (gs://…)")
  bucket, _, name = uri[len("gs://"):].partition("/")
  try:
    data = GCSHook().download(bucket_name=bucket, object_name=name)
  except NotFound as error:
    raise sdfb_batches.PlanError(f"{uri}: no such object — {error}") from error
  return sdfb_batches.decode_object(data, uri)


@task.branch(task_id="route")
def route(**context) -> str:
  """Which branch the launch takes, and its launch bodies.

  Reads `config_uri` only when it is set, and parses it once; its
  serving.client_type, gpu, sdk_containers and launcher_machine_type go over
  the params before any body is built (the hardware is the launch's own,
  Rulings R16, R16b).
  Explicit: pushes `body` and
  returns `start_sdfb`. All: plans the batches and pushes the bodies, the
  base run id, the batch count, the model file, the batches, the banner and
  `verdict_inputs` (what `fail_if_a_table_failed` reads with), and returns
  `batch_bodies`. Pushes `mode` (the resolved branch) either
  way, once everything else is pushed. A PlanError fails the task without a
  retry: the same inputs fail the same way every time.
  """
  import logging
  import uuid

  ti = context["ti"]
  params = dict(context["params"])
  config_uri = str(params.get("config_uri") or "").strip()
  # Salted per trigger: retriggering the same logical date reuses
  # dag_run.run_id, and seed=None derives batch seeds from run_id, so an
  # identical run_id replayed identical data (E2E 2026-07-10).
  run_id = f"{context['dag_run'].run_id}-{uuid.uuid4().hex[:8]}"
  try:
    config_text = _read_gcs_text(config_uri) if config_uri else None
    config = sdfb_batches.parse_config(config_text)
    mode = sdfb_batches.resolve_tables_mode(params, config)
    params = sdfb_batches.with_file_hardware(params, config)
    if mode == "explicit":
      pushed = {
          "body":
              sdfb_batches.explicit_body({
                  **params, "run_id": run_id
              },
                                         LAYERS,
                                         job_name=CONSTANTS["job_name"],
                                         variables=VARIABLES)
      }
      branch = "start_sdfb"
    else:
      relationships_uri, max_tables = sdfb_batches.planning_inputs(
          params, config)
      plan = sdfb_batches.launch_plan(
          sdfb_batches.whole_model_params(params, LAYERS),
          model_text=_read_gcs_text(relationships_uri),
          max_tables_per_job=max_tables,
          relationships_uri=relationships_uri,
          base_run_id=run_id,
          constants=CONSTANTS,
          layers=LAYERS)
      logging.info("%s", plan["banner"])
      pushed = {
          key: plan[key] for key in ("bodies", "base_run_id", "batch_count",
                                    "relationships_uri", "batches", "banner")
      }
      pushed["verdict_inputs"] = sdfb_batches.verdict_inputs(
          params, config, LAYERS)
      branch = "batch_bodies"
  except sdfb_batches.PlanError as error:
    raise AirflowFailException(str(error)) from error
  for key, value in pushed.items():
    ti.xcom_push(key=key, value=value)
  ti.xcom_push(key="mode", value=mode)
  return branch


@task(task_id="batch_bodies")
def batch_bodies(**context) -> list:
  """The batch launch bodies `route` planned, as this task's return value:
  Airflow maps `bundle` only over a return value (never a custom key),
  and `route`'s is the branch it took. `-> list` keeps multiple_outputs
  off."""
  return context["ti"].xcom_pull(task_ids="route", key="bodies")


class _LaunchGateSensor(BaseSensorOperator):
  """A bundle's launch gate (ADR 0045 D5): done once the `wait_generation`
  of every bundle `cap` or more indexes below it has ended
  (`sdfb_batches.may_launch`); `cap` is the run's `max_parallel_jobs`.

  A sensor, in reschedule mode: between two reads Airflow keeps it up for
  reschedule for `poke_interval` and it holds no worker slot. Only a
  sensor's task instances carry `ReadyToRescheduleDep`, so only a sensor's
  reschedule date is honoured: the AirflowRescheduleException of the
  operator this replaces was queued again within seconds, 25,000 times in
  the 2026-11-03 launch. `DagRun.get_task_instances` opens its own session,
  so each read sees the waits' states afresh.
  """

  def poke(self, context) -> bool:
    import logging

    map_index = context["ti"].map_index
    cap = sdfb_batches.parallel_cap(LAYERS, VARIABLES,
                                    context["params"]["max_parallel_jobs"])
    blocking = sdfb_batches.launch_blocker(
        map_index, cap,
        sdfb_batches.ti_states(context["dag_run"].get_task_instances(),
                               sdfb_batches.BUNDLE_WAIT))
    if blocking is None:
      return True
    logging.info("%s", sdfb_batches.gate_wait_line(map_index, blocking, cap))
    return False


class _GenerationWaitSensor(BaseSensorOperator):
  """A bundle's wait for its generation job (ADR 0045 D7, D10), in
  reschedule mode: done, returning `{"id", "attempt"}`, once the job is
  JOB_STATE_DONE.

  A job that ends any other way fails the task without a retry (the same
  job fails the same way), except a FAILED job that ran no work item and
  whose messages name a capacity cause (`sdfb_batches.is_capacity_failure`):
  while the run's `capacity_retries` allows, the bundle is relaunched, the
  same body with the same run ids (nothing landed), named
  `<job>-bNN-rN-<8 hex>`, `capacity_backoff_seconds` after the failure, and
  the wait reads the new job. The attempts live in the bundle's `launch`
  XCom (`ATTEMPT_KEY`): this sensor's own are cleared at each poke. A
  relaunch whose job id was never recorded (the worker died during the
  call) fails the task rather than launch a second job beside a first that
  may run.
  """

  template_fields = ("body",)

  def __init__(self, *, body, **kwargs):
    super().__init__(**kwargs)
    self.body = body

  def poke(self, context):
    import datetime
    import logging

    from airflow.providers.google.cloud.hooks.dataflow import DataflowHook
    from airflow.sensors.base import PokeReturnValue
    from airflow.utils import timezone

    ti, params = context["ti"], context["params"]
    label = f"b{ti.map_index:02d}"
    attempt = self._attempt(ti)
    if attempt.get("launching"):
      raise AirflowFailException(
          f"{label}: relaunch {attempt['attempt']} may have started job "
          f"{attempt.get('job_name')} without its id being recorded; it is "
          f"not launched again (find it in Dataflow, then clear the bundle)")
    hook = DataflowHook()
    now = timezone.utcnow()
    if not attempt.get("id"):
      if now < datetime.datetime.fromisoformat(attempt["not_before"]):
        logging.info("%s: relaunch %d at %s (%s)", label, attempt["attempt"],
                     attempt["not_before"], attempt.get("reason"))
        return False
      self._relaunch(ti, attempt, hook)
      return False
    job = hook.get_job(
        job_id=attempt["id"], project_id=project_id, location=region)
    state = job.get("currentState") if isinstance(job, dict) else None
    if state == "JOB_STATE_DONE":
      return PokeReturnValue(
          is_done=True,
          xcom_value={
              "id": attempt["id"],
              "attempt": attempt["attempt"]
          })
    if state not in _ENDED_NOT_DONE:
      return False
    if (state == "JOB_STATE_FAILED" and
        attempt["attempt"] < int(params["capacity_retries"])):
      messages = [
          str(m.get("messageText", "")) for m in hook.fetch_job_messages_by_id(
              job_id=attempt["id"], project_id=project_id, location=region) or
          ()
      ]
      ran = sdfb_batches.ran_work(
          hook.fetch_job_metrics_by_id(
              job_id=attempt["id"], project_id=project_id, location=region))
      if sdfb_batches.is_capacity_failure(messages, ran):
        reason = sdfb_batches.capacity_reason(messages)
        number = attempt["attempt"] + 1
        wait_s = sdfb_batches.capacity_backoff_seconds(number)
        self._record(
            ti, {
                "attempt":
                    number,
                "id":
                    None,
                "not_before":
                    (now + datetime.timedelta(seconds=wait_s)).isoformat(),
                "reason":
                    reason,
                "failed": [*attempt.get("failed", []), attempt["id"]],
            })
        logging.warning(
            "%s: Dataflow job %s FAILED for capacity (%s) before it ran a "
            "work item; relaunch %d of %s in %d s, the same run ids", label,
            attempt["id"], reason, number, params["capacity_retries"], wait_s)
        return False
    raise AirflowFailException(
        f"{label}: Dataflow job {attempt['id']} ended {state} (attempt "
        f"{attempt['attempt']}); not relaunched: only a FAILED job that ran "
        f"no work item for a capacity cause is, at most capacity_retries "
        f"times")

  @staticmethod
  def _attempt(ti) -> dict:
    """The bundle's current attempt: the recorded one, else the launch's."""
    recorded = ti.xcom_pull(
        task_ids=sdfb_batches.BUNDLE_LAUNCH,
        key=ATTEMPT_KEY,
        map_indexes=ti.map_index)
    if isinstance(recorded, dict):
      return recorded
    launched = ti.xcom_pull(
        task_ids=sdfb_batches.BUNDLE_LAUNCH, map_indexes=ti.map_index)
    return {"attempt": 0, "id": _launched_job_id(launched, None)}

  @staticmethod
  def _record(ti, attempt: dict) -> None:
    from airflow.models.xcom import XCom

    XCom.set(
        key=ATTEMPT_KEY,
        value=attempt,
        dag_id=ti.dag_id,
        task_id=sdfb_batches.BUNDLE_LAUNCH,
        run_id=ti.run_id,
        map_index=ti.map_index)

  def _relaunch(self, ti, attempt: dict, hook) -> None:
    import logging
    import uuid

    body = sdfb_batches.relaunch_body(
        self.body,
        job_name=CONSTANTS["job_name"],
        attempt=attempt["attempt"],
        salt=uuid.uuid4().hex[:8])
    name = body["launchParameter"]["jobName"]
    # Recorded before the call: a worker that dies inside it leaves
    # `launching`, and the next poke fails instead of launching twice.
    self._record(ti, {**attempt, "launching": True, "job_name": name})
    job = hook.launch_job_with_flex_template(
        body=body, location=region, project_id=project_id)
    self._record(ti, {
        **attempt, "id": job["id"],
        "launching": False,
        "job_name": name
    })
    logging.warning("b%02d: relaunched as Dataflow job %s (%s)", ti.map_index,
                    job["id"], name)


@task(task_id="eval_body", multiple_outputs=False)
def bundle_evaluation_body(generation: dict, **context) -> dict:
  """After a bundle's generation job is DONE: the launch body of that
  bundle's evaluation, over its one job (ADR 0045 D7).

  Skips with the evaluation off. A bundle whose generation failed never
  gets here (upstream failed), and no other bundle waits for it. The
  launch reads the return value whole, so multiple_outputs is off, said
  here because the `dict` annotation would turn it on.
  """
  ti, params = context["ti"], context["params"]
  if not params["run_evaluation"]:
    raise AirflowSkipException("run_evaluation is off")
  return sdfb_batches.evaluation_body(
      {
          **params, "run_id":
              ti.xcom_pull(task_ids="route", key="base_run_id"),
          "relationships_uri":
              ti.xcom_pull(task_ids="route", key="relationships_uri")
      },
      LAYERS,
      mode=ti.xcom_pull(task_ids="route", key="mode"),
      generation_job_ids=[generation["id"]],
      variables=VARIABLES,
      ts_nodash=context["ts_nodash"],
      batch_index=ti.map_index)


@task(task_id="fail_if_a_batch_failed", trigger_rule=TriggerRule.ONE_FAILED)
def fail_if_a_batch_failed(**context) -> None:
  """A leaf that fails the run when a bundle's generation did.

  one_failed over every bundle's `wait_generation`: a failed gate, launch
  or wait fails the wait or leaves it upstream-failed. A failed `route`
  also reaches here (the bundles are then upstream-failed): nothing was
  launched, and route's own failure already fails the run, so this one
  skips. The message names each failed bundle and the task it failed in,
  and an instance that failed with no start date never ran: the ones to
  clear.
  """
  if not context["ti"].xcom_pull(task_ids="route", key="base_run_id"):
    raise AirflowSkipException("route planned no batch: no batch failed")
  raise AirflowFailException(
      sdfb_batches.batch_failure_message(
          context["dag_run"].get_task_instances()))


def _verdict_rows(table: str, base: str) -> tuple[list[dict], bool]:
  """(the launch's `validation_runs` rows, whether they carry
  `status_reason`).

  A table name `verdicts_query` refuses fails the task without a retry: the
  same name fails the same way every time (as `route` treats a PlanError).
  A table created before the `status_reason` column refuses the query that
  names it: it is read again without the column. Any other error is raised
  as it is, so the environment's retries apply.
  """
  from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook

  try:
    with_reason = sdfb_batches.verdicts_query(table, with_reason=True)
    without_reason = sdfb_batches.verdicts_query(table, with_reason=False)
  except ValueError as error:
    raise AirflowFailException(str(error)) from error

  def read(query: str) -> list[dict]:
    job = BigQueryHook().insert_job(
        project_id=project_id,
        configuration={
            "query": {
                "query":
                    query,
                "useLegacySql":
                    False,
                "queryParameters": [{
                    "name": "base",
                    "parameterType": {
                        "type": "STRING"
                    },
                    "parameterValue": {
                        "value": base
                    },
                }],
            }
        },
    )
    return [dict(row) for row in job.result()]

  try:
    return read(with_reason), True
  except Exception as error:  # pylint: disable=broad-exception-caught  # re-raised unless it is the missing column
    if not sdfb_batches.is_missing_reason_column(str(error)):
      raise
  return read(without_reason), False


@task(task_id="fail_if_a_table_failed", trigger_rule=TriggerRule.ALL_DONE)
def fail_if_a_table_failed(**context) -> None:
  """A leaf that fails the run when a planned table did not pass.

  all_done, so it judges the rows once every batch has ended, a failed one
  too: a planned table with no row fails (its batch did not reach DONE). It
  never reads a batch job's state, which is `fail_if_a_batch_failed`'s job.
  Skips when `route` planned no batch (an explicit launch, a failed route),
  and with a warning when the launch records no `validation_runs`.
  """
  import logging

  ti = context["ti"]
  base_run_id = ti.xcom_pull(task_ids="route", key="base_run_id")
  if not base_run_id:
    raise AirflowSkipException("route planned no batch: no table verdicts")
  inputs = ti.xcom_pull(task_ids="route", key="verdict_inputs")
  runs_table = inputs["validation_runs_table"]
  if not runs_table:
    logging.warning("per-table verdicts are not recorded: no "
                    "validation_runs_table; the run's outcome is the batch "
                    "jobs' alone")
    raise AirflowSkipException("no validation_runs_table: no table verdicts")
  batches = ti.xcom_pull(task_ids="route", key="batches")
  landing_dataset = inputs["landing_dataset"]
  tables = [f"{landing_dataset}.{t}" for b in batches for t in b["tables"]]
  rows, reason_column = _verdict_rows(runs_table, base_run_id)
  verdicts = sdfb_batches.table_verdicts(tables, rows)
  # A fake client's blocker is no model's: informational.
  fail_on_blocker = inputs["client_type"] != "fake"
  banner = sdfb_batches.verdict_banner(
      verdicts,
      batches=batches,
      landing_dataset=landing_dataset,
      fail_on_blocker=fail_on_blocker,
      reason_column=reason_column)
  logging.info("%s", banner)
  if sdfb_batches.failed_tables(verdicts, fail_on_blocker=fail_on_blocker):
    raise AirflowFailException(banner)


def _run_evaluation_gate(params, ti, ts_nodash, **_):
  """Gate for the chained evaluation: False skips every task after it.

  True pushes the evaluation's launch body under `evaluation_body`: the
  generation job `start_sdfb` launched, the branch `route` resolved.
  """
  if not params["run_evaluation"]:
    return False
  ti.xcom_push(
      key="evaluation_body",
      value=sdfb_batches.evaluation_body(
          dict(params),
          LAYERS,
          mode=ti.xcom_pull(task_ids="route", key="mode"),
          generation_job_ids=[ti.xcom_pull(task_ids="start_sdfb")["id"]],
          variables=VARIABLES,
          ts_nodash=ts_nodash))
  return True


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


def _bundle_generation_job_id(body):
  """The generation job id a bundle's evaluation body names (one id: its
  `generation_job_ids`), or "" when `eval_body` left no body."""
  if not isinstance(body, dict):
    return ""
  parameters = body.get("launchParameter", {}).get("parameters", {})
  value = parameters.get("generation_job_ids")
  return value if isinstance(value, str) and "," not in value else ""


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


def _close_bundle_running_row(context):
  """on_failure_callback of a bundle's `wait_eval_launch`: the `all`
  branch's `_close_running_row`, for the evaluation of this bundle only.

  The row is found by the bundle's generation job id, the one id its
  evaluation was launched with (`eval_body`'s body), which the registry
  records as `generation_job_id`: never by the launch's base run id, which
  every bundle's evaluation shares, so one dead launcher closes no other
  bundle's row. The ids are pulled from this map index's `eval_body` and
  `trigger_eval`. Nothing is written without a job id, nor while the
  launched job may still write its own FINAL row (`_callback_closes_row`).
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
  # The launch target: the bundle's generation job id, the one id its
  # evaluation was launched with (a callback has no Jinja to render).
  target = _bundle_generation_job_id(
      ti.xcom_pull(task_ids="bundle.eval_body", map_indexes=ti.map_index))
  if not target:
    logging.warning(
        "evaluation registry not updated: eval_body left no generation job "
        "id in XCom to find the bundle's RUNNING row by")
    return
  job_id = _launched_job_id(
      ti.xcom_pull(task_ids="bundle.trigger_eval", map_indexes=ti.map_index),
      ti.xcom_pull(
          task_ids="bundle.trigger_eval",
          key="dataflow_job_config",
          map_indexes=ti.map_index))
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
  reason = (f"Bundle evaluation job ended while its launch was "
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
  routed = route()

  # -- explicit: master's chain --------------------------------------------
  # The launch body `route` built. Not waited for: its XCom holds the job id
  # the sensor waits on.
  start_sdfb = DataflowStartFlexTemplateOperator(
      task_id="start_sdfb",
      project_id=project_id,
      location=region,
      body=XComArg(routed.operator, key="body"),
      do_xcom_push=True,
      wait_until_finished=False,
  )

  # Chained evaluation (run_evaluation, default True): with it off the gate
  # skips every task below; with it on it builds the evaluation's body.
  run_evaluation_gate = ShortCircuitOperator(
      task_id="run_evaluation_gate",
      python_callable=_run_evaluation_gate,
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
  # generation, its body the gate's. Submitted and not waited for, like the
  # generation: the job writes its own FINAL row. This task returns as soon
  # as the job is queued, before the launcher has written the RUNNING row,
  # so it cannot see the launcher die: the sensor after it does.
  trigger_evaluation = DataflowStartFlexTemplateOperator(
      task_id="trigger_evaluation",
      project_id=project_id,
      location=region,
      body=XComArg(run_evaluation_gate, key="evaluation_body"),
      do_xcom_push=True,
      wait_until_finished=False,
  )

  # The evaluation's LAUNCH is over when its job runs. A job that ended any
  # other way makes it raise; after the task's last retry the failure
  # callback closes the RUNNING row. It does not wait for the job's end.
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

  # -- all: one mapped bundle per batch (ADR 0045 D5, D7) -----------------
  planned = batch_bodies()

  @task_group(group_id=sdfb_batches.BUNDLE_GROUP)
  def bundle(body):
    """One batch: the gate, its generation job, then its own evaluation.
    Every task of the group shares the batch's map index."""
    # Done once every bundle `cap` or more below has ended its generation:
    # at most `cap` generation jobs run at once. Never `max_active_tis_*`
    # (or any task-level limit): it makes the scheduler fail every waiting
    # instance whenever the DAG is swept as stale (ADR 0045 D5).
    gate = _LaunchGateSensor(
        task_id="gate",
        mode="reschedule",
        poke_interval=GATE_POKE_SECONDS,
        timeout=GATE_TIMEOUT_SECONDS,
    )
    # Not waited for: the job id is the XCom the wait reads. No retries: a
    # launch call that was accepted and then failed would launch the batch
    # twice under the same run ids (see Retries in the header).
    launch = DataflowStartFlexTemplateOperator(
        task_id="launch",
        project_id=project_id,
        location=region,
        body=body,
        do_xcom_push=True,
        wait_until_finished=False,
        retries=0,
    )
    # The generation's wait: rescheduled between reads, holding no worker
    # slot for the job's hours; it relaunches a job that failed for
    # capacity (ADR 0045 D10) and ends the bundle's slot of the cap.
    wait = _GenerationWaitSensor(
        task_id="wait_generation",
        body=body,
        mode="reschedule",
        poke_interval=GENERATION_POKE_SECONDS,
        timeout=GENERATION_TIMEOUT_SECONDS,
    )
    # The bundle's evaluation, of the attempt that was DONE: CPU only,
    # outside the cap.
    evaluation = bundle_evaluation_body(wait.output)
    trigger = DataflowStartFlexTemplateOperator(
        task_id="trigger_eval",
        project_id=project_id,
        location=region,
        body=evaluation,
        do_xcom_push=True,
        wait_until_finished=False,
    )
    launched = DataflowJobStatusSensor(
        task_id="wait_eval_launch",
        job_id=bundle_evaluation_job_id,
        expected_statuses={"JOB_STATE_RUNNING", "JOB_STATE_DONE"},
        project_id=project_id,
        location=region,
        mode="reschedule",
        poke_interval=EVALUATION_LAUNCH_POKE_SECONDS,
        timeout=EVALUATION_LAUNCH_TIMEOUT_SECONDS,
        on_failure_callback=_close_bundle_running_row,
    )
    gate >> launch >> wait >> evaluation >> trigger >> launched  # pylint: disable=pointless-statement  # Airflow chain
    # The group's value is its generation wait: the leaves below read it.
    return wait

  bundles = bundle.expand(body=planned)
  failed = fail_if_a_batch_failed()
  table_failed = fail_if_a_table_failed()

  routed >> planned  # pylint: disable=pointless-statement  # Airflow dependency operator
  bundles >> failed  # pylint: disable=pointless-statement  # Airflow dependency operator
  bundles >> table_failed  # pylint: disable=pointless-statement  # Airflow dependency operator
