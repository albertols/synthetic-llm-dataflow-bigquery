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
"""One evaluation, driven end to end: the registry events around the
pipeline (D7), written by the driver.

    mint evaluation_id  eval-<UTC yyyymmddThhmmssZ>-<8 hex>, fresh per
          │             attempt (temp tables carry it and collide for 24 h)
          ▼
    resolve the launch ─► load the models ─► build_plan
          │      any exception: no plan exists, so a FAILED row is built
          │      from the arguments (`planning_failed_row`), then re-raised
          ▼
    RUNNING row  ──────────────────────────────┐  the driver's own events:
          ▼                                    │  a BigQuery load job, or —
    prepare_evaluation (the plan's DDL;        │  `--sink local_json` — a
          │  a refused pin degrades)           │  shard in the evaluation's
          ▼                                    │  own directory
    pipeline(options from the PREPARED plan)   │
          │  bq         the pipeline writes its rows and the FINAL row
          │  bq_client  local NDJSON, then one load job per table, the
          │             registry's FINAL row last
          │  local_json local NDJSON only
          ▼                                    │
    any exception ─► FAILED row ◄──────────────┘  then the exception is
                                                  re-raised, never replaced

Every evaluation therefore leaves a RUNNING row and a final one, or a
FAILED row alone when planning never produced a plan. A FAILED row that
cannot itself be written is reported on stderr and the original error
still surfaces. `sdfb-eval run` waits for the pipeline, so its exit code
can carry the `--fail_on` gate (`cli.gate`); the flex-template entry
submits the Dataflow job and returns (a job that dies later leaves its
RUNNING row to the orchestrator's failure callback).

Not every FAILED comes with an exception: when no launch table could be
evaluated the pipeline itself writes a FINAL row reading FAILED and ends
normally. So once the run is over the driver reads the FINAL row back —
it needs it for the gate anyway — and a FAILED one exits 3
(`cli.gate.final_exit_code`), with no second row and whatever
`--fail_on` says.

Runner defaults: the DirectRunner evaluates samples and loads through
the client (`--mode sampled --sink bq_client`); Dataflow reads every row
and writes from the pipeline (`--mode exact --sink bq`). The pipeline
options come from `pipeline_options_defaults(runner, prepared plan)` —
the side-input cache is sized from the plan that will actually run —
with the operator's Beam arguments underneath: their experiments are
kept, `upload_graph` is always among them on Dataflow, and
`enable_data_sampling` is refused (it would sample the label key into
the monitoring UI, Ruling R68).

The label key is never read here: `--label_key_uri` travels into the
plan, a worker resolves it, the registry records only `operator` or
`ephemeral`, and nothing this module prints or writes names the URI.

`plan` builds the same plan and stops. Planning itself creates one kind
of table (Ruling R57): the zero-byte, 24-hour start snapshot of an
`as_of_diff` scope, because the scope's reads go through it. With
`--no_planning_snapshots` the planner is handed a BigQuery that refuses
every DDL statement, so nothing is created and each such table is
planned as scope `unknown` with that reason — unplanned.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import apache_beam as beam
from apache_beam.options.pipeline_options import (
    DebugOptions,
    GoogleCloudOptions,
    PipelineOptions,
    WorkerOptions,
)
from apache_beam.runners.runner import PipelineState

from sdfb_evaluation.beam.assemble import failed_row, running_row
from sdfb_evaluation.beam.census import FreeTextPool, read_pools
from sdfb_evaluation.beam.io import (
    BigQuerySinks,
    BigQuerySources,
    ClientLoadSinks,
    LocalJsonSinks,
    Sinks,
    Sources,
)
from sdfb_evaluation.beam.pipeline import (
    build_evaluation_pipeline,
    pipeline_options_defaults,
    prepare_evaluation,
)
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.cli.fixture import Fixture, load_fixture
from sdfb_evaluation.cli.gate import (
    EXIT_FAILED,
    final_exit_code,
    gate_counts,
)
from sdfb_evaluation.context.bq import Bq, BqApiError, normalize_fqn
from sdfb_evaluation.context.gcp import make_session
from sdfb_evaluation.context.launch import LaunchContext, resolve_launch
from sdfb_evaluation.context.plan import EvaluationPlan, Knobs, build_plan
from sdfb_evaluation.context.relationships import RelModel, load_models
from sdfb_evaluation.context.runs import runs_for
from sdfb_evaluation.report.store import METRICS, REGISTRY, read_bq
from sdfb_evaluation.schemas import load_schema
from sdfb_evaluation.version import EVALUATOR_VERSION

__all__ = [
    "EVALUATION_ID_RE",
    "SINKS",
    "Env",
    "execute_pipeline",
    "forbidden_experiments",
    "knobs_from_args",
    "launch_key",
    "launch_request",
    "mint_evaluation_id",
    "pipeline_options",
    "plan",
    "planning_failed_row",
    "run",
    "runner_defaults",
]

EVALUATION_ID_RE = re.compile(r"eval-\d{8}T\d{6}Z-[0-9a-f]{8}")
SINKS = ("bq", "bq_client", "local_json")
_TOKEN_RE = re.compile(r"[0-9a-f]{8}")
_TOKEN_BYTES = 4
_FORBIDDEN_EXPERIMENT = "enable_data_sampling"
_DATAFLOW = "dataflow"
_LOCAL = "local_json"
_FINAL = "FINAL"
_REDACTED = "<label key uri>"
_COUNT_KEYS = ("total", "pass", "warn", "fail", "info", "not_evaluated")
_SCORE_KEYS = ("overall", "fidelity", "privacy", "integrity", "diversity")
_POOL_ERRORS = (BqApiError, PermissionError, LookupError, ValueError)
_NO_SNAPSHOTS = (
    "--no_planning_snapshots: planning creates no table, so this as_of_diff "
    "scope has no start snapshot to read; plan or run without the flag to "
    "evaluate it")


def _utc_now() -> datetime:
  return datetime.now(UTC)


def _token() -> str:
  return secrets.token_hex(_TOKEN_BYTES)


def _default_bq(project: str) -> Any:
  return Bq(project)


def _pipeline(options: PipelineOptions) -> beam.Pipeline:
  return beam.Pipeline(options=options)


def execute_pipeline(pipeline: beam.Pipeline, wait: bool) -> str | None:
  """Run `pipeline`; with `wait`, block until it ends and raise unless it
  ended DONE. Returns the runner's job id when it assigns one.

  Raises:
    RuntimeError: the pipeline ended in any state but DONE.
  """
  result = pipeline.run()
  job_id = getattr(result, "job_id", None)
  job = str(job_id()) if callable(job_id) else None
  if wait:
    state = result.wait_until_finish()
    if state is not None and state != PipelineState.DONE:
      named = f" (job {job})" if job else ""
      raise RuntimeError(f"the evaluation pipeline{named} ended in state "
                         f"{state}, not DONE")
  return job


@dataclass
class Env:
  """What the CLI reaches the outside world through (tests replace it):
  the BigQuery client of a project, the REST session factory (None:
  Application Default Credentials, made when a job id is resolved), the
  clock and the random half of an evaluation id, the pipeline's sources,
  how a pipeline is made from its options (on Dataflow, Beam validates
  them against Cloud Storage right there) and how a built one is
  executed."""
  make_bq: Callable[[str], Any] = _default_bq
  session_factory: Callable[[str], Any] | None = None
  now: Callable[[], datetime] = _utc_now
  token: Callable[[], str] = _token
  make_sources: Callable[[], Sources] = BigQuerySources
  make_pipeline: Callable[[PipelineOptions], beam.Pipeline] = _pipeline
  execute: Callable[[beam.Pipeline, bool], str | None] = execute_pipeline


def mint_evaluation_id(now: datetime, token: str) -> str:
  """`eval-<UTC yyyymmddThhmmssZ>-<8 hex>` (Ruling R88c): the time makes
  ids sort, the random half keeps two attempts of one second apart.

  Raises:
    ValueError: `token` is not 8 lowercase hex digits.
  """
  if _TOKEN_RE.fullmatch(token) is None:
    raise ValueError(f"evaluation id token {token!r}: expected 8 hex digits")
  stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
  return f"eval-{stamp}-{token}"


def is_dataflow(runner: str) -> bool:
  return _DATAFLOW in runner.lower()


def runner_defaults(runner: str) -> tuple[str, str]:
  """(`--mode`, `--sink`) when the flags are not given (module
  docstring)."""
  return ("exact", "bq") if is_dataflow(runner) else ("sampled", "bq_client")


def qualified_dataset(dataset: str, project: str | None) -> str:
  """`project.dataset` from `dataset` or `project.dataset` (also
  `project:dataset`)."""
  text = dataset.replace(":", ".", 1)
  return text if "." in text else f"{project}.{text}"


def knobs_from_args(args: argparse.Namespace, evaluation_id: str) -> Knobs:
  """The plan's knobs from the planning flags.

  Raises:
    ValueError: a value `Knobs` refuses (the message names the knob).
  """
  temp = args.temp_dataset
  return Knobs(
      sample_rows=args.sample_rows,
      privacy_sample_rows=args.privacy_sample_rows,
      detection_sample_rows=args.detection_sample_rows,
      pair_max_columns=args.pair_max_columns,
      topk_profile=args.topk_profile,
      row_flags_top_k=args.row_flags_top_k,
      row_flags_source_keys=args.row_flags_source_keys,
      max_bytes_billed=args.max_bytes_billed,
      max_shuffle_gb=args.max_shuffle_gb,
      scope=args.scope,
      allow_contaminated=args.allow_contaminated,
      output_dataset=args.output_dataset,
      temp_dataset=qualified_dataset(temp, args.project) if temp else None,
      evaluation_id=evaluation_id)


# --------------------------------------------------------------------------
# the launch and the plan
# --------------------------------------------------------------------------
def launch_request(
    args: argparse.Namespace) -> tuple[str | None, dict[str, Any] | None]:
  """(`job_id`, `manual`) for `context.launch.resolve_launch` from the
  target flags: `--job_id`, or the tables of `--tables` in
  `--landing_dataset` with their sources in `--reference_dataset`
  (`reference_table` names the first one: the planner takes its dataset
  for every table), plus `--relationships_uri` and `--run_id` wherever
  they are given. Manual values only fill what the launch's own records
  leave open."""
  manual: dict[str, Any] = {}
  params: dict[str, Any] = {}
  if args.relationships_uri:
    manual["relationships_uri"] = args.relationships_uri
    params["relationships_uri"] = args.relationships_uri
  if args.run_id:
    manual["base_run_id"] = args.run_id
    params["run_id"] = args.run_id
  if args.tables:
    landing = qualified_dataset(args.landing_dataset, args.project)
    manual["tables_in_order"] = [f"{landing}.{name}" for name in args.tables]
    reference = qualified_dataset(args.reference_dataset, args.project)
    manual["reference_table"] = f"{reference}.{args.tables[0]}"
  if params:
    manual["params"] = params
  return args.job_id, (manual or None)


def _resolve(args: argparse.Namespace, env: Env, bq: Any) -> LaunchContext:
  job_id, manual = launch_request(args)
  if args.run_id and not args.job_id and not args.tables:
    # no job to ask: the launch is what validation_runs recorded under the
    # run id, in the generator's quality dataset (--output_dataset)
    runs = runs_for(
        bq,
        quality_dataset=qualified_dataset(args.output_dataset, args.project),
        landing_tables=[],
        window=(None, None),
        base_run_id=args.run_id)
    return LaunchContext.from_sources(
        job=None, launch_config=None, writes=(), manual=manual, runs=runs)
  factory = env.session_factory
  if job_id is not None and factory is None:
    factory = make_session
  return resolve_launch(
      bq=bq,
      session_factory=factory,
      project=args.project,
      region=args.region or "",
      job_id=job_id,
      manual=manual)


def _models(launch: LaunchContext) -> tuple[RelModel, ...]:
  """The model the launch applied: its adjusted copy when it adjusted
  one (ADR 0038), else the one it loaded; none when relationships were
  off."""
  adjusted = launch.adjusted_model_uri if launch.model_adjusted else None
  uri = adjusted or launch.relationships_uri
  return tuple(load_models(uri)) if uri else ()


class _ReadOnlyPlanning:
  """A `Bq` for a planning that may create nothing
  (`--no_planning_snapshots`): every read goes to the real client, every
  DDL statement is refused and recorded in `refused`. The planner treats
  the refusal as it treats BigQuery's own: that table's scope is
  `unknown` with the reason, the other tables are still planned."""

  def __init__(self, bq: Any):
    self._bq = bq
    self.refused: list[str] = []

  def __getattr__(self, name: str) -> Any:
    return getattr(self._bq, name)

  def execute(self,
              sql: str,
              params: Mapping[str, Any] | None = None,
              *,
              max_bytes: int | None = None) -> None:
    del params, max_bytes
    self.refused.append(sql)
    raise PermissionError(_NO_SNAPSHOTS)


@dataclass
class _Attempt:
  """One attempt's identity, and what planning learned before it failed
  or finished."""
  evaluation_id: str
  now: datetime
  launch: LaunchContext | None = None
  refused: list[str] = field(default_factory=list)


def _make_plan(args: argparse.Namespace, env: Env, bq: Any,
               fixture: Fixture | None, attempt: _Attempt) -> EvaluationPlan:
  knobs = knobs_from_args(args, attempt.evaluation_id)
  trigger = getattr(args, "trigger", "cli")
  if fixture is not None:
    return fixture.plan(
        knobs=knobs,
        mode=args.mode,
        trigger=trigger,
        runner=args.runner,
        now=attempt.now,
        evaluation_id=attempt.evaluation_id)
  attempt.launch = _resolve(args, env, bq)
  planning_bq = bq
  if getattr(args, "no_planning_snapshots", False):
    planning_bq = _ReadOnlyPlanning(bq)
    attempt.refused = planning_bq.refused
  return build_plan(
      launch=attempt.launch,
      models=_models(attempt.launch),
      bq=planning_bq,
      knobs=knobs,
      mode=args.mode,
      trigger=trigger,
      runner=args.runner,
      now=attempt.now)


def plan(args: argparse.Namespace,
         env: Env | None = None) -> tuple[EvaluationPlan, frozenset[str]]:
  """`sdfb-eval plan`: the plan of the target, never run (module
  docstring). Returns it with the landing tables left unplanned by
  `--no_planning_snapshots`. Nothing is written to the registry; a
  planning error naming DDL it had already run says so on stderr before
  it propagates."""
  env = env or Env()
  now = env.now()
  attempt = _Attempt(mint_evaluation_id(now, env.token()), now)
  fixture = load_fixture(args.fixture_dir) if args.fixture_dir else None
  bq = None if fixture is not None else env.make_bq(args.project)
  try:
    planned = _make_plan(args, env, bq, fixture, attempt)
  except Exception as exc:
    for statement in getattr(exc, "planning_ddl", ()) or ():
      print(
          f"sdfb-eval: planning had already run: {statement.sql}",
          file=sys.stderr)
    raise
  refused = set(attempt.refused)
  unplanned = frozenset(
      t.landing_table
      for t in planned.tables
      if not t.evaluated and refused & set(t.scope.planning_sql))
  return planned, unplanned


def launch_key(launch: LaunchContext, mode: str, knobs: Knobs) -> str:
  """`evaluation_key` as `build_plan` derives it (generation job, run
  ids, tables, catalogue and evaluator versions, mode, value knobs), for
  a launch that resolved but could not be planned.

  Raises:
    ValueError: a landing table that is not `project.dataset.table`.
  """
  payload = {
      "generation_job_id": launch.generation_job_id,
      "run_ids": list(launch.run_ids),
      "tables": [normalize_fqn(t) for t in launch.tables_in_order],
      "catalogue_version": load_catalogue().version,
      "evaluator_version": EVALUATOR_VERSION,
      "mode": mode,
      "knobs": knobs.key_dict(),
  }
  text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
  return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


def _request_key(args: argparse.Namespace, knobs: Knobs) -> str:
  """The key of a request whose launch never resolved: the same recipe
  over the target as it was asked for."""
  payload = {
      "unresolved_request": {
          "job_id": args.job_id,
          "run_id": args.run_id,
          "tables": list(args.tables or ()),
          "landing_dataset": args.landing_dataset,
          "reference_dataset": args.reference_dataset,
          "relationships_uri": args.relationships_uri,
          "fixture_dir": args.fixture_dir,
      },
      "catalogue_version": load_catalogue().version,
      "evaluator_version": EVALUATOR_VERSION,
      "mode": args.mode,
      "knobs": knobs.key_dict(),
  }
  text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
  return hashlib.blake2b(text.encode(), digest_size=16).hexdigest()


def _rfc3339(moment: datetime) -> str:
  return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def planning_failed_row(args: argparse.Namespace,
                        *,
                        evaluation_id: str,
                        evaluated_at: datetime,
                        exc: BaseException,
                        launch: LaunchContext | None = None,
                        project: str | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
  """The FINAL FAILED registry row of an attempt that never got a plan
  (Ruling R88b), in schema order: `assemble.failed_row` over a plan with
  no tables, built from the arguments — the mode, trigger, runner and
  knobs asked for, the launch when it had resolved (else only what the
  target flags name), the error as the reason and, in the warnings, any
  DDL planning had already run. What no plan measured stays NULL."""
  knobs = knobs_from_args(args, evaluation_id)
  resolved = launch is not None
  if launch is None:
    launch = LaunchContext(
        generation_job_id=args.job_id,
        job_name=None,
        region=args.region if args.job_id else None,
        started_at=None,
        finished_at=None,
        base_run_id=args.run_id,
        run_ids=(),
        tables_in_order=(),
        reference_table=None,
        write_disposition=None,
        relationships_uri=args.relationships_uri,
        params={},
        model_sha=None,
        model_name=None,
        adjusted_model_uri=None,
        params_source="manual",
        writes=())
  try:
    key = (
        launch_key(launch, args.mode, knobs) if resolved else _request_key(
            args, knobs))
  except ValueError:  # the malformed table name may be the failure itself
    key = _request_key(args, knobs)
  stub = EvaluationPlan(
      evaluation_id=evaluation_id,
      evaluation_key=key,
      evaluated_at=_rfc3339(evaluated_at),
      salt="",
      mode=args.mode,
      trigger=args.trigger,
      runner=args.runner,
      launch=launch,
      models=(),
      model_sha=None,
      tables=(),
      knobs=knobs,
      prepare_sql=(),
      bq_bytes_estimate=0,
      predicted_shuffle_gb=0.0,
      warnings=tuple(launch.warnings),
      catalogue_version=load_catalogue().version,
      temp_dataset=knobs.temp_dataset or
      f"{project or args.project}.{knobs.output_dataset}",
      label_key_uri=args.label_key_uri)
  row = _failed(stub, exc, now)
  row.update(bq_bytes_processed=None, predicted_shuffle_gb=None)
  if not resolved:
    row["params_source"] = None
  return row


def _failed(planned: EvaluationPlan, exc: BaseException,
            now: datetime | None) -> dict[str, Any]:
  """`assemble.failed_row`, the label key's URI kept out of the reason
  (an error may quote the secret's resource name)."""
  row = failed_row(planned, exc, now=now)
  uri = planned.label_key_uri
  if uri:
    row["status_reason"] = str(row["status_reason"]).replace(uri, _REDACTED)
    row["warnings"] = [str(w).replace(uri, _REDACTED) for w in row["warnings"]]
  return row


# --------------------------------------------------------------------------
# pipeline options
# --------------------------------------------------------------------------
def forbidden_experiments(beam_args: Sequence[str]) -> list[str]:
  """The experiments among `beam_args` this evaluator refuses
  (`enable_data_sampling`, however it is spelled: alone, with a value,
  or inside a comma-separated list)."""
  experiments = PipelineOptions(
      list(beam_args)).view_as(DebugOptions).experiments or []
  return [
      e for e in experiments
      if any(part.strip().split("=", 1)[0] == _FORBIDDEN_EXPERIMENT
             for part in str(e).split(","))
  ]


def pipeline_options(args: argparse.Namespace, beam_args: Sequence[str],
                     planned: EvaluationPlan,
                     evaluation_id: str) -> PipelineOptions:
  """The pipeline's options: the operator's Beam arguments, overridden
  by the evaluator's defaults for `planned` (module docstring) — the
  PREPARED plan, so the side-input cache fits what will run. A larger
  cache the operator asked for is kept.

  Raises:
    ValueError: `enable_data_sampling` is among the experiments.
  """
  flags = list(beam_args)
  refused = forbidden_experiments(flags)
  if refused:
    raise ValueError(f"experiment {_FORBIDDEN_EXPERIMENT} is refused "
                     f"({refused}): Dataflow would sample pipeline elements, "
                     "the label key among them, into its monitoring UI "
                     "(Ruling R68)")
  user = PipelineOptions(flags)
  overrides = pipeline_options_defaults(args.runner, planned)
  experiments = list(
      dict.fromkeys([
          *(user.view_as(DebugOptions).experiments or []),
          *overrides.pop("experiments", []),
      ]))
  if experiments:
    overrides["experiments"] = experiments
  asked = user.view_as(WorkerOptions).max_cache_memory_usage_mb or 0
  overrides["max_cache_memory_usage_mb"] = max(
      int(asked), overrides["max_cache_memory_usage_mb"])
  if args.project:
    overrides["project"] = args.project
  if args.region:
    overrides["region"] = args.region
  if is_dataflow(args.runner) and not user.view_as(GoogleCloudOptions).job_name:
    overrides["job_name"] = f"sdfb-{evaluation_id.lower()}"
  return PipelineOptions(flags, **overrides)


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
class _Registry:
  """Where the driver's own registry events go (module docstring)."""

  def __init__(self, sinks: Sinks, bq: Any, table: str, local: bool):
    self._sinks = sinks
    self._bq = bq
    self._table = table
    self._local = local

  def append(self, row: Mapping[str, Any], label: str) -> None:
    if self._local:
      assert isinstance(self._sinks, LocalJsonSinks)
      self._sinks.write_rows([row], REGISTRY, label=label)
    else:
      self._bq.load_json(self._table, [dict(row)], load_schema(REGISTRY))

  def record_failure(self, make_row: Callable[..., Mapping[str, Any]], *args:
                     Any, **kwargs: Any) -> None:
    """Append the FAILED row `make_row(*args, **kwargs)` builds; if
    building or writing it fails too, say so on stderr and return, so the
    evaluation's own error is the one that surfaces."""
    try:
      self.append(make_row(*args, **kwargs), "driver-failed")
    except Exception as exc:  # pylint: disable=broad-exception-caught  # whatever stops this write must not replace the evaluation's own error
      print(
          "sdfb-eval: the FAILED registry row could not be written "
          f"({type(exc).__name__}: {exc}); the evaluation's own error "
          "follows",
          file=sys.stderr)


def _free_text_pools(
    planned: EvaluationPlan,
    bq: Any) -> tuple[dict[str, dict[str, FreeTextPool]] | None, list[str]]:
  """The launch's free-text pools per table (`census.read_pools`) and a
  note for each table whose pools could not be read; None when the
  launch names no pools table (`field.pool_memorization_lift` then says
  so itself)."""
  params = planned.launch.params
  table = str(params.get("freetext_pools_table") or "").strip()
  if not table:
    return None, []
  pools: dict[str, dict[str, FreeTextPool]] = {}
  notes = []
  for entry in planned.tables:
    if not entry.evaluated or not entry.reference_digest:
      continue
    try:
      found = read_pools(
          bq,
          pools_table=table,
          reference_digest=entry.reference_digest,
          model_uri=str(params.get("model_uri") or ""),
          max_bytes=planned.budget.max_bytes_billed)
    except _POOL_ERRORS as exc:
      notes.append(f"{entry.name}: the free-text pools could not be read "
                   f"from {table} ({type(exc).__name__}: {exc}); "
                   "field.pool_memorization_lift is not evaluated")
      continue
    if found:
      pools[entry.name] = found
  return (pools or None), notes


def _local_final(sinks: Sinks) -> dict[str, Any]:
  """The FINAL row the pipeline wrote under a local sink.

  Raises:
    RuntimeError: there is none (the pipeline did not run to its end).
  """
  assert isinstance(sinks, LocalJsonSinks)
  finals = [r for r in sinks.read_rows(REGISTRY) if r.get("event") == _FINAL]
  if not finals:
    raise RuntimeError(
        "the pipeline ended without writing a FINAL registry row: its "
        "outputs are incomplete and nothing was loaded")
  return finals[-1]


def _score(value: Any) -> str:
  return "—" if value is None else f"{float(value):.3f}"


def _summary(final: Mapping[str, Any], counts: Mapping[str, Any], where: str,
             key_mode: str) -> list[str]:
  evaluation_id, status = final.get("evaluation_id"), final.get("status")
  lines = [f"evaluation {evaluation_id}: {status}"]
  reason = final.get("status_reason")
  if reason:
    lines.append(f"  {reason}")
  scores = ", ".join(
      name + " " + _score(final.get(name + "_score")) for name in _SCORE_KEYS)
  tally = ", ".join(
      f"{int(counts.get(key) or 0):,} {key}" for key in _COUNT_KEYS)
  lines += [
      f"  scores: {scores}", f"  metrics: {tally}", f"  label key: {key_mode}",
      f"  written to: {where}"
  ]
  return lines


@dataclass
class _Run:
  """One `run`'s wiring: the attempt, where it writes and how it reads
  its own result back."""
  args: argparse.Namespace
  env: Env
  attempt: _Attempt
  project: str
  fixture: Fixture | None
  sinks: Sinks
  bq: Any
  registry: _Registry
  directory: str | None
  temporary: str | None

  @property
  def where(self) -> str:
    if self.args.sink == _LOCAL:
      return str(self.directory)
    return f"{self.project}.{self.args.output_dataset}"

  def execute(self, planned: EvaluationPlan, beam_args: Sequence[str],
              wait: bool) -> tuple[str | None, dict[str, Any] | None]:
    """Build and run the pipeline of the prepared plan; with `wait` and
    a local sink, check its FINAL row and (`bq_client`) load the
    outputs. Returns (job id, the FINAL row when it is local)."""
    args, fixture = self.args, self.fixture
    pools: dict[str, dict[str, FreeTextPool]] | None = None
    notes: list[str] = []
    if fixture is None:
      pools, notes = _free_text_pools(planned, self.bq)
    if notes:
      planned = dataclasses.replace(
          planned, warnings=(*planned.warnings, *notes))
    options = pipeline_options(args, beam_args, planned,
                               self.attempt.evaluation_id)
    pipeline = self.env.make_pipeline(options)
    build_evaluation_pipeline(
        pipeline,
        planned,
        sources=(self.env.make_sources()
                 if fixture is None else fixture.sources()),
        sinks=self.sinks,
        stats_query=self.bq.query if fixture is None else None,
        pools=pools)
    job = self.env.execute(pipeline, wait)
    if not wait or args.sink == "bq":
      return job, None
    final = _local_final(self.sinks)
    if isinstance(self.sinks, ClientLoadSinks):
      self.sinks.load(self.bq)
    return job, final

  def verdict(
      self, final: dict[str, Any] | None,
      thresholds: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the FINAL row, the gate's counts), read back from the sink."""
    args = self.args
    evaluation_id = self.attempt.evaluation_id
    if final is None:
      stored = read_bq(
          self.bq,
          project=self.project,
          dataset=args.output_dataset,
          evaluation_id=evaluation_id,
          metrics=bool(thresholds))
      if stored.final is None:
        raise LookupError(
            f"evaluation {evaluation_id}: the pipeline ended DONE, but "
            f"{self.where}.{REGISTRY} holds no FINAL row for it")
      final, rows = stored.final, stored.metrics
    else:
      assert isinstance(self.sinks, LocalJsonSinks)
      rows = self.sinks.read_rows(METRICS) if thresholds else []
    if thresholds:
      return final, gate_counts(rows, thresholds)
    return final, {key: final.get(f"metrics_{key}") or 0 for key in _COUNT_KEYS}


def _wiring(args: argparse.Namespace, env: Env) -> _Run:
  now = env.now()
  attempt = _Attempt(mint_evaluation_id(now, env.token()), now)
  fixture = load_fixture(args.fixture_dir) if args.fixture_dir else None
  project = args.project or (fixture.project if fixture else "")
  directory: str | None = None
  temporary: str | None = None
  if args.sink != "bq":
    base = args.output_local
    if not base:
      base = temporary = tempfile.mkdtemp(prefix="sdfb-eval-")
    directory = os.path.join(base, attempt.evaluation_id)
  sinks: Sinks
  if args.sink == "bq":
    sinks = BigQuerySinks(project, args.output_dataset)
  elif args.sink == "bq_client":
    sinks = ClientLoadSinks(
        str(directory), project=project, dataset=args.output_dataset)
  else:
    sinks = LocalJsonSinks(str(directory))
  offline = fixture is not None and args.sink == _LOCAL
  bq = None if offline else env.make_bq(project)
  registry = _Registry(
      sinks,
      bq,
      f"{project}.{args.output_dataset}.{REGISTRY}",
      local=args.sink == _LOCAL)
  return _Run(
      args=args,
      env=env,
      attempt=attempt,
      project=project,
      fixture=fixture,
      sinks=sinks,
      bq=bq,
      registry=registry,
      directory=directory,
      temporary=temporary)


def run(args: argparse.Namespace,
        beam_args: Sequence[str],
        env: Env | None = None,
        *,
        wait: bool = True,
        gated: bool = True,
        thresholds: Mapping[str, Any] | None = None) -> int:
  """`sdfb-eval run`: one evaluation, start to registry (module
  docstring). `wait=False` submits the pipeline and returns (the flex
  entry on Dataflow); `gated=False` never applies `--fail_on`;
  `thresholds` are the gate's overrides (`cli.gate.load_thresholds`).

  Returns:
    0; 1 when the `--fail_on` gate trips; 3 when the run ended without
    an error but its FINAL row reads FAILED (no launch table could be
    evaluated: the pipeline wrote that row itself, so none is added).

  Raises:
    Whatever stopped the evaluation, after its FAILED row was appended.
  """
  this = _wiring(args, env or Env())
  attempt, registry = this.attempt, this.registry
  print(f"evaluation {attempt.evaluation_id}: planning "
        f"(mode {args.mode}, {args.runner}, sink {args.sink})")
  try:
    planned = _make_plan(args, this.env, this.bq, this.fixture, attempt)
  except Exception as exc:
    registry.record_failure(
        planning_failed_row,
        args,
        evaluation_id=attempt.evaluation_id,
        evaluated_at=attempt.now,
        exc=exc,
        launch=attempt.launch,
        project=this.project,
        now=this.env.now())
    raise
  planned = dataclasses.replace(planned, label_key_uri=args.label_key_uri)
  try:
    registry.append(running_row(planned), "driver-running")
    if this.fixture is None:
      planned = prepare_evaluation(planned, this.bq)
    job, final = this.execute(planned, beam_args, wait)
  except BaseException as exc:
    registry.record_failure(_failed, planned, exc, this.env.now())
    raise
  key_mode = "operator" if args.label_key_uri else "ephemeral"
  if not wait:
    named = f" as job {job}" if job else ""
    print(f"evaluation {attempt.evaluation_id}: submitted on {args.runner}"
          f"{named}, not waited for\n"
          f"  label key: {key_mode}\n"
          f"  written to: {this.where} (the pipeline writes the FINAL row; "
          "--fail_on is not applied here)")
    return 0
  final, counts = this.verdict(final, thresholds or {})
  if this.temporary:
    shutil.rmtree(this.temporary, ignore_errors=True)
  lines = _summary(final, counts, this.where, key_mode)
  if args.sink == "bq_client" and args.output_local:
    lines.append(f"  local copy: {this.directory}")
  code = final_exit_code(final, args.fail_on, counts, gated=gated)
  if code == EXIT_FAILED:
    lines.append(f"  the evaluation finished FAILED: exit {EXIT_FAILED} "
                 "(--fail_on does not apply; no further registry row is "
                 "written)")
  elif not gated:
    lines.append("  gate: --fail_on is not applied here")
  elif args.fail_on != "none":
    failing, warning = counts.get("fail") or 0, counts.get("warn") or 0
    outcome = "TRIPPED" if code else "passed"
    regraded = " under --thresholds_uri" if thresholds else ""
    lines.append(f"  gate (--fail_on {args.fail_on}): {outcome} — {failing} "
                 f"fail, {warning} warn{regraded}")
  print("\n".join(lines))
  return code
