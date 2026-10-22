#!/usr/bin/env python
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
"""Generate every enabled table of one relationships model, batch by batch
(ADR 0045) — the CLI twin of the Composer model DAG.

It plans the batches exactly as each launcher will (it resolves the same
flags and config file through `run_pipeline.parse_args`), runs one launcher
per batch — `--tables=all --batch_index=i --batch_count=N` — at most
`launch.max_parallel_jobs` at a time, waits for each, and prints every
batch's Dataflow job id and the evaluation command that covers the batches
that landed.

    uv run --no-sync python3 scripts/launch/run_model.py --dry_run <launcher flags>
    uv run --no-sync python3 scripts/launch/run_model.py <launcher flags> --runner=DataflowRunner ...

Every flag except `--dry_run` and `--log_dir` is the launcher's and reaches
each batch unchanged, except that `--batch_index`, `--batch_count` and
`--job_name` are refused: they are set per batch. Each batch's output goes
to `<log_dir>/bNN.log` (default `runs/model-launch/<run_id>/`, git-ignored).
A failed batch does not stop the others. Ctrl-C (or any error) terminates
every launcher still running before the script ends.

    exit 0  every batch finished, or --dry_run
    exit 1  at least one batch failed; the others ran
    exit 2  a usage error: nothing was launched

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from apache_beam.options.pipeline_options import (GoogleCloudOptions,
                                                  PipelineOptions)
from sdfb_beam.cli import run_pipeline
from sdfb_beam.io.relationships import load_relationship_registry
from sdfb_core.contracts.relationships import RelationshipError
from sdfb_core.launch.batches import (ModelLaunch, ModelLaunchError,
                                      batch_label, batch_run_id, launch_banner,
                                      plan_model_launch)

_REPO = Path(__file__).resolve().parents[2]
# Beam's DataflowRunner logs this once it has created the job.
_JOB_CREATED = re.compile(r"Created job with id: \[([^\]]+)\]")
# Set per batch by the launcher: giving them would make parallel batches
# collide (one Dataflow job name) or disagree with the plan.
_PER_BATCH_FLAGS = ("--batch_index", "--batch_count", "--job_name")
# How long a terminated launcher gets to exit before it is killed.
_TERMINATE_GRACE_SECONDS = 30.0
_NOT_STARTED = -15
_COULD_NOT_START = 127
# project.dataset.table
_FQN_PARTS = 3


@dataclass(frozen=True)
class BatchResult:
  """One batch launcher's outcome."""
  index: int
  returncode: int
  job_id: str | None
  log: Path


class Fleet:
  """The launcher processes this script started. `stop()` terminates the
  ones still running, waits for them, and refuses any later start, so no
  launcher outlives `main`."""

  def __init__(self) -> None:
    self._lock = threading.Lock()
    self._procs: list[subprocess.Popen] = []
    self._stopped = False

  def start(self, cmd: list[str], stdout: IO[str] | None):
    """The started process, or None once the fleet has been stopped."""
    with self._lock:
      if self._stopped:
        return None
      proc = subprocess.Popen(cmd, stdout=stdout, stderr=subprocess.STDOUT)
      self._procs.append(proc)
      return proc

  def stop(self) -> None:
    with self._lock:
      self._stopped = True
      live = [p for p in self._procs if p.poll() is None]
    for proc in live:
      proc.terminate()
    for proc in live:
      try:
        proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
      except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def split_argv(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
  """(this script's flags, the launcher's flags)."""
  own = argparse.ArgumentParser(
      description=(__doc__ or "").split("\n", 1)[0], allow_abbrev=False)
  own.add_argument(
      "--dry_run", action="store_true", help="print the plan; launch nothing")
  own.add_argument(
      "--log_dir",
      help="where each batch's output goes (default "
      "runs/model-launch/<run_id>)")
  ours, rest = own.parse_known_args(argv)
  refused = [a for a in rest if a.split("=", 1)[0] in _PER_BATCH_FLAGS]
  if refused:
    own.error(f"{', '.join(_PER_BATCH_FLAGS)} are set per batch by the "
              f"launcher; remove {refused[0].split('=', 1)[0]}")
  return ours, rest


def plan(rest: list[str]) -> tuple[argparse.Namespace, list[str], ModelLaunch]:
  """The launch as every batch's launcher will resolve and plan it."""
  args, beam_argv = run_pipeline.parse_args([*rest, "--tables=all"])
  if args.tables != "all":
    print(
        "[run_model] the config file sets launch.tables to explicit; this "
        "script launches every enabled table",
        file=sys.stderr)
    raise SystemExit(2)
  try:
    registry = load_relationship_registry(args.relationships_uri)
    return args, beam_argv, plan_model_launch(registry, args.max_tables_per_job)
  except (ModelLaunchError, RelationshipError) as exc:
    print(f"[run_model] {exc}", file=sys.stderr)
    raise SystemExit(2) from exc


def batch_command(rest: list[str], index: int, count: int) -> list[str]:
  """One batch's launcher: the same flags, plus the batch identity."""
  return [
      sys.executable, "-m", "sdfb_beam.cli.run_pipeline", *rest, "--tables=all",
      f"--batch_index={index}", f"--batch_count={count}"
  ]


def run_batch(rest: list[str],
              index: int,
              count: int,
              log_dir: Path,
              fleet: Fleet | None = None) -> BatchResult:
  """Run one batch's launcher to its end (DataflowRunner waits for the
  job), its output in `log_dir`. A launcher that cannot start, or that is
  stopped before it starts, is a failed batch, never an exception."""
  fleet = fleet or Fleet()
  log = log_dir / f"{batch_label(index)}.log"
  with log.open("w", encoding="utf-8") as out:
    try:
      proc = fleet.start(batch_command(rest, index, count), out)
    except OSError as exc:
      out.write(f"could not start the launcher: {exc}\n")
      return BatchResult(index, _COULD_NOT_START, None, log)
    if proc is None:
      return BatchResult(index, _NOT_STARTED, None, log)
    returncode = proc.wait()
  found = _JOB_CREATED.search(log.read_text(encoding="utf-8"))
  return BatchResult(index, returncode, found.group(1) if found else None, log)


def evaluation_command(args: argparse.Namespace, beam_argv: list[str],
                       results: list[BatchResult]) -> str | None:
  """The `sdfb-eval run` that covers the batches that landed (ADR 0045):
  the launch's base run id and their job ids. `--scope=manual`, as the model
  DAG passes: a `--run_id` run has no labelled writes, so `auto` would
  resolve every table to `unknown`."""
  landed = [r.job_id for r in results if r.returncode == 0 and r.job_id]
  if not landed:
    return None
  project = (
      PipelineOptions(beam_argv).view_as(GoogleCloudOptions).project or
      "<PROJECT>")
  parts = (args.validation_runs_table or "").split(".")
  dataset = parts[1] if len(parts) == _FQN_PARTS else "<QUALITY_DATASET>"
  return (f"uv run --project packages/sdfb-evaluation sdfb-eval run "
          f"--project={project} --run_id={args.run_id} "
          f"--generation_job_ids={','.join(landed)} "
          f"--output_dataset={dataset} --scope=manual --runner=DataflowRunner "
          f"--region=<REGION> --temp_location=gs://<BUCKET>/temp")


def main(argv: list[str] | None = None) -> int:
  ours, rest = split_argv(sys.argv[1:] if argv is None else argv)
  args, beam_argv, launch = plan(rest)
  print(launch_banner(launch))
  if ours.dry_run:
    return 0
  count = len(launch.batches)
  safe_run_id = re.sub(r"[^A-Za-z0-9._-]", "-", args.run_id)
  log_dir = (
      Path(ours.log_dir) if ours.log_dir else _REPO / "runs" / "model-launch" /
      safe_run_id)
  log_dir.mkdir(parents=True, exist_ok=True)
  fleet = Fleet()
  pool = ThreadPoolExecutor(max_workers=max(1, args.max_parallel_jobs))
  try:
    results = list(
        pool.map(lambda index: run_batch(rest, index, count, log_dir, fleet),
                 range(count)))
  finally:
    # Also on Ctrl-C or an error: no launcher outlives this script.
    fleet.stop()
    pool.shutdown(wait=True, cancel_futures=True)
  failed = [r for r in results if r.returncode != 0]
  for result in results:
    state = ("OK"
             if result.returncode == 0 else f"FAILED rc={result.returncode}")
    print(f"{batch_run_id(args.run_id, result.index)}  {state}  job "
          f"{result.job_id or '(not in the log)'}  {result.log}")
  command = evaluation_command(args, beam_argv, results)
  if command:
    print("Evaluate the batches that landed:\n  " + command)
  if failed:
    names = ", ".join(batch_run_id(args.run_id, r.index) for r in failed)
    print(f"[run_model] {len(failed)} batch(es) failed: {names}")
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
