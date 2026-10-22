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
"""`scripts/launch/run_model.py` (ADR 0045): plan like the launcher, run one
launcher per batch at most `max_parallel_jobs` at a time, report each job
and the evaluation that covers the landed ones. The launcher processes are
faked; nothing reaches GCP.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[5]
_FOREST = """model: forest_test
tables:
  T_ACCOUNTS: {pk: [ACC_ID]}
  T_EVENTS_LOG: {}
  T_RATES: {source_table: other_ds.RATES_V2, num_rows: 7}
  T_LEGACY: {enabled: false}
"""


def _load():
  spec = importlib.util.spec_from_file_location(
      "run_model", _REPO / "scripts" / "launch" / "run_model.py")
  module = importlib.util.module_from_spec(spec)
  sys.modules["run_model"] = module
  spec.loader.exec_module(module)
  return module


RM = _load()


@pytest.fixture(name="argv")
def fixture_argv(tmp_path):
  logs = tmp_path / "logs"
  model = tmp_path / "forest.yaml"
  model.write_text(_FOREST, encoding="utf-8")
  return [
      "--dlq_table=p.q.dlq", "--num_rows=10", "--run_id=r1",
      "--model_uri=gs://b/synthetic/models/m/v1/", "--source_dataset=p.src",
      "--landing_dataset=p.land", f"--relationships_uri={model}",
      "--validation_runs_table=p.synthetic_data_quality.validation_runs",
      "--project=p", "--runner=DataflowRunner", f"--log_dir={logs}"
  ]


class _Proc:
  """A fake launcher process: `wait()` writes Beam's job-created line and
  returns the batch's code; a hung one blocks until `terminate()`."""

  def __init__(self, owner, cmd, stdout, index):
    self.owner = owner
    self.cmd = cmd
    self.stdout = stdout
    self.index = index
    self.returncode = None
    self.terminated = False
    self.killed = False
    self._ended = threading.Event()

  def poll(self):
    return self.returncode

  def terminate(self):
    self.terminated = True
    self.returncode = -15
    self._ended.set()

  def kill(self):
    self.killed = True
    self.terminate()

  def wait(self, timeout=None):
    del timeout
    if self.index in self.owner.hang:
      self.owner.arrive()
      self._ended.wait(120)
      return self.returncode
    time.sleep(self.owner.pause)
    if self.index not in self.owner.no_id:
      self.stdout.write(f"INFO Created job with id: [job-{self.index}]\n")
    with self.owner.lock:
      self.owner.running -= 1
    self.returncode = self.owner.codes.get(self.index, 0)
    return self.returncode


class _Launchers:
  """A fake `subprocess.Popen`: records the commands, the processes and the
  peak number running at once."""

  def __init__(self,
               codes=None,
               pause=0.0,
               hang=(),
               refuse=(),
               no_id=(),
               signal_after=None):
    self.codes = codes or {}
    self.no_id = set(no_id)
    # (signal, n): sent to this process once n hung launchers are waiting
    self.signal_after = signal_after
    self.arrived = 0
    self.pause = pause
    self.hang = set(hang)
    self.refuse = set(refuse)
    self.commands: list[list[str]] = []
    self.procs: list[_Proc] = []
    self.running = 0
    self.peak = 0
    self.lock = threading.Lock()

  def __call__(self, cmd, *, stdout, stderr):
    del stderr
    index = int(
        next(a for a in cmd if a.startswith("--batch_index=")).split("=")[1])
    if index in self.refuse:
      raise OSError("no such interpreter")
    with self.lock:
      self.commands.append(cmd)
      self.running += 1
      self.peak = max(self.peak, self.running)
      proc = _Proc(self, cmd, stdout, index)
      self.procs.append(proc)
    if index in self.hang and index not in self.no_id:
      # a hung launcher has already created its Dataflow job
      stdout.write(f"INFO Created job with id: [job-{index}]\n")
    return proc

  def arrive(self):
    """A hung launcher is waiting: send the signal once all are. Sent from
    here, the main thread is certainly still inside `main`."""
    with self.lock:
      self.arrived += 1
      fire = (
          self.signal_after is not None and
          self.arrived == self.signal_after[1])
    if fire:
      os.kill(os.getpid(), self.signal_after[0])


def test_a_dry_run_prints_the_plan_and_launches_nothing(argv, monkeypatch,
                                                        capsys):
  monkeypatch.setattr(RM.subprocess, "Popen",
                      lambda *a, **k: pytest.fail("launched a batch"))
  assert RM.main([*argv, "--dry_run", "--max_tables_per_job=2"]) == 0
  out = capsys.readouterr().out
  assert "MODEL LAUNCH forest_test" in out
  assert "  b00 |   2 tables | T_ACCOUNTS, T_EVENTS_LOG" in out
  assert "  b01 |   1 tables | T_RATES" in out


def test_each_batch_runs_the_launcher_with_its_index(argv, monkeypatch, capsys):
  launchers = _Launchers()
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  assert RM.main([*argv, "--max_tables_per_job=2"]) == 0
  commands = sorted(launchers.commands, key=str)
  assert len(commands) == 2
  for index, cmd in enumerate(commands):
    assert cmd[1:3] == ["-m", "sdfb_beam.cli.run_pipeline"]
    assert cmd[-3:] == [
        "--tables=all", f"--batch_index={index}", "--batch_count=2"
    ]
    assert "--dry_run" not in cmd
    assert not [a for a in cmd if a.startswith("--log_dir")]
  out = capsys.readouterr().out
  assert "r1-b00  OK  job job-0" in out and "r1-b01  OK  job job-1" in out
  assert ("sdfb-eval run --project=p --run_id=r1 "
          "--generation_job_ids=job-0,job-1 "
          "--output_dataset=synthetic_data_quality") in out
  assert " --scope=manual " in out


def test_at_most_max_parallel_jobs_run_at_once(argv, monkeypatch):
  launchers = _Launchers(pause=0.05)
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  assert RM.main([*argv, "--max_tables_per_job=1",
                  "--max_parallel_jobs=2"]) == 0
  assert len(launchers.commands) == 3 and launchers.peak == 2


def test_one_at_a_time_by_default(argv, monkeypatch):
  launchers = _Launchers(pause=0.02)
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  assert RM.main([*argv, "--max_tables_per_job=1"]) == 0
  assert launchers.peak == 1


def test_a_failed_batch_exits_1_and_the_rest_run(argv, monkeypatch, capsys):
  launchers = _Launchers(codes={1: 1})
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  assert RM.main([*argv, "--max_tables_per_job=1"]) == 1
  assert len(launchers.commands) == 3
  out = capsys.readouterr().out
  assert "r1-b01  FAILED rc=1" in out
  assert "--generation_job_ids=job-0,job-2 " in out


def test_a_launcher_that_cannot_start_fails_only_its_batch(
    argv, monkeypatch, capsys):
  launchers = _Launchers(refuse={0})
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  assert RM.main([*argv, "--max_tables_per_job=1"]) == 1
  assert len(launchers.commands) == 2
  assert "r1-b00  FAILED rc=127" in capsys.readouterr().out


def test_ctrl_c_terminates_every_running_launcher(argv, monkeypatch, capsys):
  launchers = _Launchers(hang={0, 1}, signal_after=(signal.SIGINT, 2))
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  with pytest.raises(KeyboardInterrupt):
    RM.main([*argv, "--max_tables_per_job=1", "--max_parallel_jobs=2"])
  # two ran and were stopped; the third never started
  assert len(launchers.procs) == 2
  assert all(p.terminated and p.returncode is not None for p in launchers.procs)
  # their Dataflow jobs outlive the launchers: the operator is told
  out = capsys.readouterr().out
  assert "job-0" in out and "job-1" in out and "b00.log" in out
  assert "may still be running" in out
  assert "gcloud dataflow jobs cancel" in out


def test_sigterm_takes_the_same_stop_path_and_restores_the_handler(
    argv, monkeypatch):
  before = signal.getsignal(signal.SIGTERM)
  launchers = _Launchers(hang={0, 1}, signal_after=(signal.SIGTERM, 2))
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  with pytest.raises(KeyboardInterrupt):
    RM.main([*argv, "--max_tables_per_job=1", "--max_parallel_jobs=2"])
  assert all(p.terminated for p in launchers.procs)
  assert signal.getsignal(signal.SIGTERM) == before


def test_an_interrupt_starts_no_further_batch(argv, monkeypatch):
  launchers = _Launchers(hang={0}, signal_after=(signal.SIGINT, 1))
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  with pytest.raises(KeyboardInterrupt):
    RM.main([*argv, "--max_tables_per_job=1"])
  assert [p.index for p in launchers.procs] == [0]
  assert launchers.procs[0].terminated


def test_stop_gives_every_launcher_one_shared_grace_period(monkeypatch):
  grace = 0.3

  class Slow:
    """Outlives the grace period; only kill() ends it."""
    returncode = None
    killed = False

    def poll(self):
      return self.returncode

    def terminate(self):
      pass

    def kill(self):
      self.killed = True
      self.returncode = -9

    def wait(self, timeout=None):
      if self.killed:
        return self.returncode
      time.sleep(timeout)
      raise subprocess.TimeoutExpired("x", timeout)

  procs = [Slow(), Slow(), Slow()]
  fleet = RM.Fleet()
  monkeypatch.setattr(RM, "_TERMINATE_GRACE_SECONDS", grace)
  monkeypatch.setattr(RM.subprocess, "Popen", lambda *a, **k: procs.pop())
  started = [fleet.start(["x"], stdout=None) for _ in range(3)]
  begin = time.monotonic()
  fleet.stop()
  elapsed = time.monotonic() - begin
  assert all(p.killed for p in started)
  assert elapsed < 2 * grace


def test_no_evaluation_command_when_no_batch_landed(argv, monkeypatch, capsys):
  monkeypatch.setattr(RM.subprocess, "Popen", _Launchers(codes={0: 1, 1: 1}))
  assert RM.main([*argv, "--max_tables_per_job=2"]) == 1
  out = capsys.readouterr().out
  assert "sdfb-eval" not in out
  assert "no evaluation command" in out


def test_the_failed_batches_are_named_in_the_closing_line(
    argv, monkeypatch, capsys):
  monkeypatch.setattr(RM.subprocess, "Popen", _Launchers(codes={0: 1, 2: 3}))
  assert RM.main([*argv, "--max_tables_per_job=1"]) == 1
  assert "2 batch(es) failed: r1-b00, r1-b02" in capsys.readouterr().out


def test_a_landed_batch_without_a_job_id_marks_the_command_partial(
    argv, monkeypatch, capsys):
  monkeypatch.setattr(RM.subprocess, "Popen", _Launchers(no_id={1}))
  assert RM.main([*argv, "--max_tables_per_job=1"]) == 0
  out = capsys.readouterr().out
  assert "r1-b01  OK" in out
  assert "# PARTIAL: r1-b01" in out
  assert "--generation_job_ids=job-0,job-2 " in out


def test_no_command_when_no_landed_batch_has_a_job_id(argv, monkeypatch,
                                                      capsys):
  monkeypatch.setattr(RM.subprocess, "Popen", _Launchers(no_id={0, 1}))
  assert RM.main([*argv, "--max_tables_per_job=2"]) == 0
  out = capsys.readouterr().out
  assert "sdfb-eval" not in out
  assert "no evaluation command" in out and "r1-b00" in out


def test_a_stubborn_launcher_is_killed(monkeypatch):
  fleet = RM.Fleet()

  class Stubborn:
    """Ignores terminate(); only kill() ends it."""
    returncode = None
    killed = False

    def poll(self):
      return self.returncode

    def terminate(self):
      pass

    def kill(self):
      self.killed = True
      self.returncode = -9

    def wait(self, timeout=None):
      if self.killed:
        return self.returncode
      raise subprocess.TimeoutExpired("x", timeout)

  proc = Stubborn()
  monkeypatch.setattr(RM, "_TERMINATE_GRACE_SECONDS", 0.01)
  monkeypatch.setattr(RM.subprocess, "Popen", lambda *a, **k: proc)
  assert fleet.start(["x"], stdout=None) is proc
  fleet.stop()
  assert proc.killed
  assert fleet.start(["x"], stdout=None) is None


@pytest.mark.parametrize(
    "flags", [["--batch_index=0"], ["--batch_count=2"], ["--job_name=m"],
              ["--batch_ind=0"], ["--batch_c", "2"], ["--job_n=x"]])
def test_the_script_owns_the_per_batch_flags(argv, flags, capsys):
  with pytest.raises(SystemExit) as exc:
    RM.main([*argv, *flags])
  assert exc.value.code == 2
  assert "set per batch by the launcher" in capsys.readouterr().err


def test_a_malformed_launch_is_a_usage_error(argv):
  with pytest.raises(SystemExit) as exc:
    RM.main([a for a in argv if not a.startswith("--source_dataset")])
  assert exc.value.code == 2
