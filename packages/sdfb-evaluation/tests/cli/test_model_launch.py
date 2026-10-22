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
"""A whole-model launch evaluated by its base run id and its batch jobs
(ADR 0045): `--run_id` with `--generation_job_ids`.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import pytest

from sdfb_evaluation.cli import driver
from sdfb_evaluation.cli.main import main, parse_args

from .helpers import REGISTRY, check_row, make_env

_BASE = "manual__2026-10-21-abcd1234"
_LANDING = "demo-project.synthetic_data"


def _row(batch: int, index: int, table: str, job: str | None) -> dict:
  return {
      "run_id": f"{_BASE}-b{batch:02d}-{index:02d}-{table}",
      "landing_table": f"{_LANDING}.{table}",
      "reference_table": f"demo-project.src.{table}",
      "created_at": f"2026-10-21T10:{batch:02d}:{index:02d}.000000Z",
      "launch_run_id": _BASE,
      "job_id": job,
      "table_index": index,
      "base_run_id": f"{_BASE}-b{batch:02d}",
  }


def _args(*extra: str):
  args, _ = parse_args([
      "run", "--project=demo-project", f"--run_id={_BASE}",
      "--output_dataset=synthetic_data_quality", *extra
  ])
  return args


def test_the_model_launch_evaluates_only_the_listed_jobs_rows(bq, monkeypatch):
  rows = [
      _row(0, 0, "T_A", "job-0"),
      _row(0, 1, "T_B", "job-0"),
      _row(1, 0, "T_C", "job-1"),  # the batch that failed
      _row(2, 0, "T_D", "job-2"),
  ]
  monkeypatch.setattr(driver, "runs_for", lambda bq_, **kwargs: rows)
  ctx = driver._resolve(  # pylint: disable=protected-access
      _args("--generation_job_ids=job-0,job-2"), make_env(bq), bq)
  assert sorted(ctx.tables_in_order) == [
      f"{_LANDING}.T_A", f"{_LANDING}.T_B", f"{_LANDING}.T_D"
  ]
  assert ctx.generation_job_id == "job-0"
  assert ctx.generation_job_ids == ("job-0", "job-2")
  assert any("not in generation_job_ids" in w for w in ctx.warnings)


def test_an_unresolved_model_launch_names_its_jobs_on_the_failed_row(
    bq, monkeypatch, capsys):
  """None of the listed jobs wrote a row (a wrong id, or the wrong
  --output_dataset): the launch never resolves, and the FAILED row still
  records the jobs it was asked to evaluate, the first as
  generation_job_id."""
  rows = [_row(0, 0, "T_A", "job-0")]
  monkeypatch.setattr(driver, "runs_for", lambda bq_, **kwargs: rows)
  argv = [
      "run", "--project=demo-project", f"--run_id={_BASE}",
      "--output_dataset=synthetic_data_quality",
      "--generation_job_ids=job-7,job-8"
  ]
  assert main(argv, make_env(bq)) == 3
  assert "no landing table could be resolved" in capsys.readouterr().err
  written = bq.registry_rows()
  assert len(written) == 1
  failed = written[0]
  check_row(REGISTRY, failed)
  assert (failed["status"], failed["base_run_id"]) == ("FAILED", _BASE)
  assert failed["generation_job_id"] == "job-7"
  assert failed["generation_job_ids"] == ["job-7", "job-8"]


def test_without_job_ids_the_launch_is_every_batch(bq, monkeypatch):
  rows = [_row(0, 0, "T_A", "job-0"), _row(1, 0, "T_C", "job-1")]
  monkeypatch.setattr(driver, "runs_for", lambda bq_, **kwargs: rows)
  ctx = driver._resolve(_args(), make_env(bq), bq)  # pylint: disable=protected-access
  assert len(ctx.tables_in_order) == 2
  assert ctx.generation_job_ids == ()


@pytest.mark.parametrize("extra", [
    ("--tables=T_A", "--landing_dataset=l", "--reference_dataset=r"),
    ("--generation_job_id=job-9", "--region=europe-west3"),
])
def test_the_job_ids_go_with_run_id_alone(capsys, extra):
  with pytest.raises(SystemExit) as exc:
    _args("--generation_job_ids=job-0", *extra)
  assert exc.value.code == 2
  assert "--generation_job_ids goes with --run_id alone" in (
      capsys.readouterr().err)


def test_the_job_ids_need_a_run_id(capsys):
  with pytest.raises(SystemExit):
    parse_args([
        "run", "--project=demo-project", "--generation_job_ids=job-0",
        "--output_dataset=synthetic_data_quality"
    ])
  assert "--generation_job_ids goes with --run_id alone" in (
      capsys.readouterr().err)
