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
"""Load the Composer DAGs in a real Airflow and record the launches they make.

The workspace tests read the DAG files with `ast`, which misses parse-time
Airflow errors and never runs a trigger rule, a branch or a mapped task.
This harness imports the DAG folder into a real Airflow 2.10.5 `DagBag`
(exit 2 on any import error), then runs `dag.test(run_conf=...)` on the
generation DAG once per scenario with every Google call patched:

- `GCSHook.__init__` / `GCSHook.download`: no connection; each read is
  recorded in `gcs_reads` and served from the scenario's `gcs_objects`. A
  scenario without objects (every explicit one) makes any read raise, so an
  explicit launch that touches GCS fails its task (an IAM regression).
- `DataflowStartFlexTemplateOperator.execute`: records the rendered `body`
  and returns a fake job `{"id": "fakejob-<task_id>[-<map_index>]"}`; a
  launch whose `jobName` ends with one of the scenario's `fail_job_suffixes`
  raises instead (a batch job that fails).
- `DataflowJobStatusSensor.poke`: returns True (the job reached the state).

Output (`--out`), one entry per scenario, keys `OUTPUT_KEYS`:

    {scenario: {"task_states": {task_id[ "[map_index]"]: state},
                "bodies": [generation launch body, ...],
                "evaluation_body": {...} | null,
                "gcs_reads": ["gs://bucket/object", ...]}}

A launch is the evaluation when its parameters carry `sdfb_job=evaluation`;
every other launch is a generation body. Bodies are sorted by (task_id,
map_index). The file is written with sorted keys, so two runs of the same
folder give the same bytes.

Normalised (time- and run-dependent values; a later comparison must apply
`normalise` the same way, which this module exports):

- Each scenario runs at the logical date `logical_date(name)`: today's
  00:00 UTC plus the scenario's index in `SCENARIOS` in minutes. It cannot
  be a fixed past date: the DAGs start at `days_ago(1)`, and Airflow creates
  no task instance for a logical date before a task's start date. Distinct
  dates keep one scenario's XComs out of another's run.
- In every string of the output: the DAG run id (`manual__<iso date>`)
  becomes `<DAG_RUN_ID>`; the 8-hex salt the DAGs append to it
  (`<run id>-{{ macros.uuid.uuid4().hex[:8] }}`) becomes `<SALT8>`; the
  logical date's `ts_nodash` (either case, as the evaluation's `jobName`
  lowercases it) becomes `<TS_NODASH>`, its `ts` `<TS>` and its `ds`
  `<DS>`.

Deploy markers (`{{NAME}}`, upper-case, no spaces) are filled from
`--markers` (a JSON object NAME -> value; the committed one is
`packages/sdfb-tests/tests/fixtures/config_parity/dag_markers.json`) plus the
scenario's `markers` overrides, in every text file of a copy of `--dags`
(`*.py`, `*.yaml`, `*.yml`, `*.json`). A marker left unfilled is exit 2. The
Airflow Variables the DAGs read are `AIRFLOW_VARIABLES`, set by the script.
Airflow's own Jinja (`{{ params.x }}`) is not a marker and is left alone.

Scenarios (`SCENARIOS`): the first four (`MASTER_SCENARIOS`) are the ones
master's generation DAG can run; the rest need the single DAG with
`tables` (`explicit` | `all`) and `config_uri`.

Venv recipe (macOS 12 x86_64; this is what ran on 2026-10-27). Python 3.11:
3.12 and 3.14 with the latest providers fail, there is no `google-re2`
wheel for them on this platform. `UV_CONSTRAINT` with `uv run --with` was
ignored, so the constraints go in with `-c`:

    S=<scratchpad>
    curl -fsSL -o $S/constraints-2.10.5-3.11.txt \\
      https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.11.txt
    uv venv --python 3.11 $S/airflow_venv
    uv pip install --python $S/airflow_venv/bin/python \\
      -c $S/constraints-2.10.5-3.11.txt "apache-airflow==2.10.5" \\
      "apache-airflow-providers-google==12.0.0" \\
      apache-airflow-providers-apache-beam
    AIRFLOW_HOME=$S/home AIRFLOW__CORE__LOAD_EXAMPLES=False \\
      $S/airflow_venv/bin/airflow db migrate          # sqlite

Run (from the checkout whose `composer/` is tested; `--dags` may be any
folder holding the DAG files and what they read at parse time):

    AIRFLOW_HOME=$S/home $S/airflow_venv/bin/python \\
      scripts/composer/check_dags.py --dags composer \\
      --markers packages/sdfb-tests/tests/fixtures/config_parity/dag_markers.json \\
      --scenario explicit_l4 explicit_t4 explicit_fk_group explicit_eval_off \\
      --out $S/dag_bodies.json

`packages/sdfb-tests/tests/fixtures/config_parity/dag_bodies_master.json` is
that command's output with `--dags` a detached worktree of master `edcfdcd`
(`git worktree add --detach $S/master_wt edcfdcd`, then
`--dags $S/master_wt/composer`). It is a golden: never regenerated, never
hand-edited.

The script sets `AIRFLOW__CORE__DAGS_FOLDER` to the filled copy (which puts
`sdfb_batches` on sys.path, as Composer does) and
`AIRFLOW__CORE__LOAD_EXAMPLES=False` before importing Airflow. Airflow is
imported inside functions only: the workspace's tests import this module
(without Airflow) for `SCENARIOS` and `OUTPUT_KEYS`.

Exit status: 0 when every scenario ran (a scenario whose DAG run fails on
purpose still counts), 2 on an import error or an unfilled marker, 1 on a
usage error or a harness failure.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from unittest import mock

# The generation DAG's file; the folder's other DAGs are imported (an import
# error in any of them is exit 2) but not run.
GENERATION_DAG_FILE = "synthetic_beam_bigquery.py"
OUTPUT_KEYS = ("task_states", "bodies", "evaluation_body", "gcs_reads")
MARKER = re.compile(r"\{\{([A-Z_]+)\}\}")
_FILLED_SUFFIXES = (".py", ".yaml", ".yml", ".json")
# The Airflow Variables the DAGs read (anonymised; DATAFLOW_NETWORK_TAGS and
# SDFB_MAX_PARALLEL_JOBS keep their in-DAG defaults).
AIRFLOW_VARIABLES = {
    "PROJECT_ID": "sdfb-proj",
    "REGION": "europe-west3",
    "DATAFLOW_SUBNET": "regions/europe-west3/subnetworks/sdfb-subnet",
    "SA_DATAFLOW": "sdfb-dataflow@example.com",
}

_TABLE = "sdfb-proj.src_ds.X_TABLE"
_CHILD_TABLE = "sdfb-proj.src_ds.Y_TABLE"
_MODEL_URI = "gs://sdfb-bucket/synthetic/relationships/model.yaml"
_CONFIG_URI = "gs://sdfb-bucket/synthetic/config/launch_all.yaml"
_MODEL_TEXT = ("model: sdfb_harness_model\n"
               "tables:\n"
               "  X_TABLE: {pk: [COL_001]}\n"
               "  Y_TABLE: {}\n")
_CONFIG_SAYS_ALL = ("launch:\n"
                    "  tables: all\n"
                    "relational:\n"
                    f"  relationships_uri: {_MODEL_URI}\n")


def _scenario(
    run_conf: Mapping[str, Any],
    *,
    markers: Mapping[str, str] | None = None,
    gcs_objects: Mapping[str, str] | None = None,
    fail_job_suffixes: Iterable[str] = ()
) -> dict[str, Any]:
  return {
      "run_conf": dict(run_conf),
      "markers": dict(markers or {}),
      "gcs_objects": dict(gcs_objects or {}),
      "fail_job_suffixes": tuple(fail_job_suffixes),
  }


# Insertion order is the plan's order; a scenario's index fixes its logical
# date. `markers` overrides `--markers` for that scenario only.
SCENARIOS: dict[str, dict[str, Any]] = {
    # An edited launch on the default GPU (the GPU marker is l4).
    "explicit_l4":
        _scenario({
            "table_fqn": _TABLE,
            "num_rows": "2000",
            "similarity": "0.7"
        }),
    # The same table on a build whose GPU marker is t4 (master's only way
    # to change the gpu param's default); vllm_dtype left at its default.
    "explicit_t4":
        _scenario({
            "table_fqn": _TABLE,
            "num_rows": "2000"
        },
                  markers={"GPU": "t4"}),
    # An FK group: a child table launched with its relationship model on GCS
    # (the launcher reads it; Airflow does not) and the evaluation chained.
    "explicit_fk_group":
        _scenario({
            "table_fqn":
                _CHILD_TABLE,
            "num_rows":
                "1000",
            "generate_fk_relationships":
                "true",
            "relationships_uri":
                "gs://sdfb-bucket/synthetic/relationships/multi_fk.yaml",
            "source_dataset":
                "sdfb-proj.src_ds",
        }),
    # Evaluation off, on the CPU smoke client: only the launch runs.
    "explicit_eval_off":
        _scenario({
            "table_fqn": _TABLE,
            "client_type": "fake",
            "run_evaluation": False
        }),
    # The trigger says explicit; the config_uri file's launch.tables says
    # all, and the file wins: the batch chain must run.
    "explicit_param_file_says_all":
        _scenario({
            "tables": "explicit",
            "config_uri": _CONFIG_URI
        },
                  gcs_objects={
                      _CONFIG_URI: _CONFIG_SAYS_ALL,
                      _MODEL_URI: _MODEL_TEXT
                  }),
    "all_one_batch":
        _scenario(
            {
                "tables": "all",
                "relationships_uri": _MODEL_URI,
                "max_tables_per_job": "10"
            },
            gcs_objects={_MODEL_URI: _MODEL_TEXT}),
    "all_two_batches":
        _scenario(
            {
                "tables": "all",
                "relationships_uri": _MODEL_URI,
                "max_tables_per_job": "1"
            },
            gcs_objects={_MODEL_URI: _MODEL_TEXT}),
    # The second batch's job fails; the first is still evaluated.
    "all_batch_fails":
        _scenario(
            {
                "tables": "all",
                "relationships_uri": _MODEL_URI,
                "max_tables_per_job": "1"
            },
            gcs_objects={_MODEL_URI: _MODEL_TEXT},
            fail_job_suffixes=("-b01",)),
    # K = 0 is refused by the planner: nothing launches.
    "all_plan_fails":
        _scenario(
            {
                "tables": "all",
                "relationships_uri": _MODEL_URI,
                "max_tables_per_job": "0"
            },
            gcs_objects={_MODEL_URI: _MODEL_TEXT}),
}
MASTER_SCENARIOS = ("explicit_l4", "explicit_t4", "explicit_fk_group",
                    "explicit_eval_off")


class HarnessError(Exception):
  """A failure of the harness itself, not of a DAG run."""


def logical_date(name: str, today: dt.date | None = None) -> dt.datetime:
  """The logical date scenario `name` runs at (see the module docstring)."""
  day = today or dt.datetime.now(dt.UTC).date()
  start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC)
  return start + dt.timedelta(minutes=list(SCENARIOS).index(name))


def normalise(value: Any, *, run_id: str, when: dt.datetime) -> Any:
  """`value` with the run- and time-dependent parts of its strings replaced
  (see the module docstring); containers are walked, other values kept."""
  if isinstance(value, Mapping):
    return {
        key: normalise(item, run_id=run_id, when=when)
        for key, item in value.items()
    }
  if isinstance(value, list | tuple):
    return [normalise(item, run_id=run_id, when=when) for item in value]
  if not isinstance(value, str):
    return value
  text = value.replace(run_id, "<DAG_RUN_ID>")
  text = re.sub(r"<DAG_RUN_ID>-[0-9a-f]{8}\b", "<DAG_RUN_ID>-<SALT8>", text)
  ts_nodash = when.strftime("%Y%m%dT%H%M%S")
  for raw, token in ((ts_nodash, "<TS_NODASH>"), (ts_nodash.lower(),
                                                  "<TS_NODASH>"),
                     (when.isoformat(), "<TS>"), (when.strftime("%Y-%m-%d"),
                                                  "<DS>")):
    text = text.replace(raw, token)
  return text


def fill_markers(source: Path, target: Path, markers: Mapping[str,
                                                              str]) -> None:
  """Copy `source`'s files into `target`, filling every deploy marker.

  Raises HarnessError naming each marker left unfilled.
  """
  target.mkdir(parents=True, exist_ok=True)
  missing: set[str] = set()
  for path in sorted(source.iterdir()):
    if not path.is_file():
      continue
    destination = target / path.name
    if path.suffix not in _FILLED_SUFFIXES:
      shutil.copyfile(path, destination)
      continue
    text = path.read_text(encoding="utf-8")
    missing.update(m for m in MARKER.findall(text) if m not in markers)
    destination.write_text(
        MARKER.sub(lambda m: markers.get(m.group(1), m.group(0)), text),
        encoding="utf-8")
  if missing:
    names = ", ".join(sorted(missing))
    raise HarnessError(f"unfilled deploy markers in {source}: {names}")


class _Recorder:
  """What one scenario's DAG run launched and read."""

  def __init__(self, scenario: Mapping[str, Any]):
    self.scenario = scenario
    self.launches: list[tuple[str, int, dict[str, Any]]] = []
    self.gcs_reads: list[str] = []

  def download(self, bucket_name: str, object_name: str, *_: Any,
               **__: Any) -> bytes:
    uri = f"gs://{bucket_name}/{object_name}"
    self.gcs_reads.append(uri)
    objects = self.scenario["gcs_objects"]
    if not objects:
      raise RuntimeError(f"check_dags: this scenario reads nothing from GCS, "
                         f"but the DAG read {uri}")
    if uri not in objects:
      raise RuntimeError(f"check_dags: no such object {uri}")
    return objects[uri].encode("utf-8")

  def execute(self, task: Any, context: Mapping[str, Any]) -> dict[str, Any]:
    ti = context["ti"]
    map_index = int(getattr(ti, "map_index", -1))
    body = json.loads(json.dumps(task.body, default=str))
    self.launches.append((task.task_id, map_index, body))
    job_name = str(body.get("launchParameter", {}).get("jobName", ""))
    if any(job_name.endswith(s) for s in self.scenario["fail_job_suffixes"]):
      raise RuntimeError(f"check_dags: job {job_name} fails (scenario)")
    suffix = f"-{map_index}" if map_index >= 0 else ""
    return {"id": f"fakejob-{task.task_id}{suffix}", "name": job_name}

  def split(self) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """(generation bodies, the evaluation body or None)."""
    bodies, evaluations = [], []
    for launch in sorted(self.launches, key=lambda x: (x[0], x[1])):
      body = launch[2]
      parameters = body.get("launchParameter", {}).get("parameters", {})
      if parameters.get("sdfb_job") == "evaluation":
        evaluations.append(body)
      else:
        bodies.append(body)
    if len(evaluations) > 1:
      raise HarnessError(f"{len(evaluations)} evaluation launches in one run")
    return bodies, (evaluations[0] if evaluations else None)


def _airflow(name: str) -> Any:
  return importlib.import_module(name)


def _load_dagbag(folder: Path) -> Any:
  """The generation DAG of `folder`; HarnessError on any import error."""
  # Maps `ab_user`: without it, `dag.test` cannot delete the run a previous
  # harness run left at the same logical date (dag_run_note's foreign key).
  _airflow("airflow.providers.fab.auth_manager.models")
  dagbag = _airflow("airflow.models.dagbag").DagBag(
      dag_folder=str(folder), include_examples=False, read_dags_from_db=False)
  if dagbag.import_errors:
    lines = [f"{path}: {error}" for path, error in dagbag.import_errors.items()]
    raise HarnessError("DAG import errors:\n" + "\n".join(lines))
  for dag in dagbag.dags.values():
    if Path(dag.fileloc).name == GENERATION_DAG_FILE:
      return dag
  raise HarnessError(f"no DAG defined in {folder / GENERATION_DAG_FILE}")


def run_scenario(dag: Any, name: str) -> dict[str, Any]:
  """One DAG run of scenario `name`, normalised."""
  scenario = SCENARIOS[name]
  recorder = _Recorder(scenario)
  gcs = _airflow("airflow.providers.google.cloud.hooks.gcs").GCSHook
  operators = _airflow("airflow.providers.google.cloud.operators.dataflow")
  sensors = _airflow("airflow.providers.google.cloud.sensors.dataflow")

  # Airflow passes the operator's execute-guard sentinel as a keyword.
  def execute(task: Any, context: Mapping[str, Any],
              **_: Any) -> dict[str, Any]:
    return recorder.execute(task, context)

  def download(*args: Any, **kwargs: Any) -> bytes:  # args[0]: the hook
    return recorder.download(*args[1:], **kwargs)

  when = logical_date(name)
  with mock.patch.object(gcs, "__init__", lambda *_, **__: None), \
      mock.patch.object(gcs, "download", download), \
      mock.patch.object(operators.DataflowStartFlexTemplateOperator,
                        "execute", execute), \
      mock.patch.object(sensors.DataflowJobStatusSensor, "poke",
                        lambda *_, **__: True):
    dag_run = dag.test(
        execution_date=when, run_conf=copy.deepcopy(scenario["run_conf"]))
  states = {}
  for ti in dag_run.get_task_instances():
    key = ti.task_id if ti.map_index < 0 else f"{ti.task_id}[{ti.map_index}]"
    states[key] = str(
        ti.state.value if hasattr(ti.state, "value") else ti.state)
  if not states:
    raise HarnessError(f"{name}: the DAG run created no task instance")
  bodies, evaluation = recorder.split()
  result = {
      "task_states": dict(sorted(states.items())),
      "bodies": bodies,
      "evaluation_body": evaluation,
      "gcs_reads": recorder.gcs_reads,
  }
  return normalise(result, run_id=dag_run.run_id, when=when)


def _set_environment(folder: Path) -> None:
  os.environ["AIRFLOW__CORE__DAGS_FOLDER"] = str(folder)
  os.environ["AIRFLOW__CORE__LOAD_EXAMPLES"] = "False"
  for name, value in AIRFLOW_VARIABLES.items():
    os.environ[f"AIRFLOW_VAR_{name}"] = value
  if str(folder) not in sys.path:
    sys.path.insert(0, str(folder))


def _parse(argv: list[str] | None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
  parser.add_argument("--dags", required=True, type=Path)
  parser.add_argument("--markers", required=True, type=Path)
  parser.add_argument(
      "--scenario",
      nargs="+",
      default=list(SCENARIOS),
      choices=list(SCENARIOS),
      help="scenarios to run (default: all)")
  parser.add_argument("--out", required=True, type=Path)
  return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
  args = _parse(argv)
  if not os.environ.get("AIRFLOW_HOME"):
    print(
        "check_dags: set AIRFLOW_HOME to a migrated scratch home",
        file=sys.stderr)
    return 1
  markers = json.loads(args.markers.read_text(encoding="utf-8"))
  with tempfile.TemporaryDirectory(prefix="check_dags_") as work:
    folders: dict[str, Path] = {}
    try:
      for name in args.scenario:
        overrides = SCENARIOS[name]["markers"]
        key = json.dumps(overrides, sort_keys=True)
        if key not in folders:
          folders[key] = Path(work) / f"dags_{len(folders)}"
          fill_markers(args.dags, folders[key], {**markers, **overrides})
      _set_environment(next(iter(folders.values())))
      dags = {key: _load_dagbag(folder) for key, folder in folders.items()}
    except HarnessError as error:
      print(f"check_dags: {error}", file=sys.stderr)
      return 2
    output = {}
    try:
      for name in args.scenario:
        key = json.dumps(SCENARIOS[name]["markers"], sort_keys=True)
        output[name] = run_scenario(dags[key], name)
    except HarnessError as error:
      print(f"check_dags: {error}", file=sys.stderr)
      return 1
  args.out.write_text(
      json.dumps(output, sort_keys=True, indent=2) + "\n", encoding="utf-8")
  for name, result in output.items():
    launches, reads = len(result["bodies"]), len(result["gcs_reads"])
    evaluation = "yes" if result["evaluation_body"] else "no"
    print(f"{name}: {launches} generation launch(es), evaluation "
          f"{evaluation}, {reads} GCS read(s)")
  return 0


if __name__ == "__main__":
  sys.exit(main())
