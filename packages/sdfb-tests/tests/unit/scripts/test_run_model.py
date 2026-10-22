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
  model = tmp_path / "forest.yaml"
  model.write_text(_FOREST, encoding="utf-8")
  return [
      "--dlq_table=p.q.dlq", "--num_rows=10", "--run_id=r1",
      "--model_uri=gs://b/synthetic/models/m/v1/", "--source_dataset=p.src",
      "--landing_dataset=p.land", f"--relationships_uri={model}",
      "--validation_runs_table=p.synthetic_data_quality.validation_runs",
      "--project=p", "--runner=DataflowRunner", f"--log_dir={tmp_path / 'logs'}"
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
      self._ended.wait(120)
      return self.returncode
    time.sleep(self.owner.pause)
    self.stdout.write(f"INFO Created job with id: [job-{self.index}]\n")
    with self.owner.lock:
      self.owner.running -= 1
    self.returncode = self.owner.codes.get(self.index, 0)
    return self.returncode


class _Launchers:
  """A fake `subprocess.Popen`: records the commands, the processes and the
  peak number running at once."""

  def __init__(self, codes=None, pause=0.0, hang=(), refuse=()):
    self.codes = codes or {}
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
    return proc


def _interrupt_once_started(launchers, count):
  """Ctrl-C the main thread once `count` launchers are running."""

  def watch():
    deadline = time.monotonic() + 60
    while len(launchers.procs) < count and time.monotonic() < deadline:
      time.sleep(0.01)
    # a real SIGINT: it wakes the main thread out of a blocking wait
    os.kill(os.getpid(), signal.SIGINT)

  threading.Thread(target=watch, daemon=True).start()


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


def test_ctrl_c_terminates_every_running_launcher(argv, monkeypatch):
  launchers = _Launchers(hang={0, 1})
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  _interrupt_once_started(launchers, 2)
  with pytest.raises(KeyboardInterrupt):
    RM.main([*argv, "--max_tables_per_job=1", "--max_parallel_jobs=2"])
  # two ran and were stopped; the third never started
  assert len(launchers.procs) == 2
  assert all(p.terminated and p.returncode is not None for p in launchers.procs)


def test_an_interrupt_starts_no_further_batch(argv, monkeypatch):
  launchers = _Launchers(hang={0})
  monkeypatch.setattr(RM.subprocess, "Popen", launchers)
  _interrupt_once_started(launchers, 1)
  with pytest.raises(KeyboardInterrupt):
    RM.main([*argv, "--max_tables_per_job=1"])
  assert [p.index for p in launchers.procs] == [0]
  assert launchers.procs[0].terminated


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


@pytest.mark.parametrize("flag",
                         ["--batch_index=0", "--batch_count=2", "--job_name=m"])
def test_the_script_owns_the_per_batch_flags(argv, flag, capsys):
  with pytest.raises(SystemExit) as exc:
    RM.main([*argv, flag])
  assert exc.value.code == 2
  assert "set per batch by the launcher" in capsys.readouterr().err


def test_a_malformed_launch_is_a_usage_error(argv):
  with pytest.raises(SystemExit) as exc:
    RM.main([a for a in argv if not a.startswith("--source_dataset")])
  assert exc.value.code == 2
