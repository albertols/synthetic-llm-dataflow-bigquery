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
"""AC-B-5 probe: a whole-model launch landed every batch inside the Flex
Template window and one evaluation covers every landed table (ADR 0045).
Needs GCP credentials; skipped on the laptop.

    SDFB_MODEL_LAUNCH_BASE=<the launch's base run id> \
    SDFB_RECORD_TABLE=<project>.synthetic_data_quality.validation_runs \
    SDFB_REGISTRY_TABLE=<project>.<eval dataset>.evaluation_latest \
    SDFB_DATAFLOW_REGION=<region> \
    SDFB_RECORD_DAY=<YYYY-MM-DD, at or before the launch> \
    uv run --no-sync python3 -m pytest -m gcp -s \
      packages/sdfb-tests/tests/integration/test_model_launch_gcp.py

`SDFB_RECORD_DAY` is optional and recommended: it adds
`created_at >= TIMESTAMP(@day)` to the validation_runs query and
`recorded_at >= TIMESTAMP(@day)` to the registry query (the evaluation
history is partitioned by `recorded_at`), so both read the launch's DAY
partitions only (a table with `require_partition_filter` needs it).

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

pytestmark = pytest.mark.gcp

# The Flex Template launcher's window (Dataflow troubleshooting: "Timeout in
# polling result file"): the graph must be submitted within it.
_FLEX_WINDOW_SECONDS = 12 * 60


def _query(table: str, select: str, where: str, base: str, time_column: str):
  """Rows of `table` for the launch `base`, limited to the day if one is set."""
  from google.cloud import bigquery  # pylint: disable=import-outside-toplevel  # GCP-only test

  sql = f"SELECT {select} FROM `{table}` WHERE {where} = @base"
  parameters = [bigquery.ScalarQueryParameter("base", "STRING", base)]
  day = os.environ.get("SDFB_RECORD_DAY")
  if day:
    sql += f" AND {time_column} >= TIMESTAMP(@day)"
    parameters.append(bigquery.ScalarQueryParameter("day", "STRING", day))
  return list(bigquery.Client().query(
      sql,
      job_config=bigquery.QueryJobConfig(query_parameters=parameters)).result())


def _job(project: str, region: str, job_id: str) -> dict:
  import google.auth  # pylint: disable=import-outside-toplevel  # GCP-only test
  from google.auth.transport.requests import AuthorizedSession  # pylint: disable=import-outside-toplevel  # GCP-only test

  credentials, _ = google.auth.default(
      scopes=["https://www.googleapis.com/auth/cloud-platform"])
  response = AuthorizedSession(credentials).get(
      f"https://dataflow.googleapis.com/v1b3/projects/{project}/locations/"
      f"{region}/jobs/{job_id}",
      timeout=30)
  response.raise_for_status()
  return response.json()


def _when(text) -> datetime:
  if isinstance(text, datetime):
    return text
  return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


@pytest.fixture(name="launch", scope="module")
def fixture_launch():
  base = os.environ["SDFB_MODEL_LAUNCH_BASE"]
  rows = _query(os.environ["SDFB_RECORD_TABLE"], "*", "launch_run_id", base,
                "created_at")
  assert rows, f"no validation_runs row has launch_run_id={base}"
  return base, rows


def test_every_batch_landed_its_record(launch):
  base, rows = launch
  batches = {json.loads(r["config"])["batch"]["index"] for r in rows}
  counts = {json.loads(r["config"])["batch"]["count"] for r in rows}
  assert len(batches) >= 2, "AC-B-5 needs a launch of at least two batches"
  assert counts == {max(batches) + 1}
  assert len({r["config_digest"] for r in rows}) == 1
  assert len({r["relationships_digest"] for r in rows}) == 1
  for row in rows:
    assert row["run_id"].startswith(f"{base}-b"), row["run_id"]


def test_every_batch_launched_inside_the_flex_window(launch):
  _, rows = launch
  project = os.environ["SDFB_RECORD_TABLE"].split(".", 1)[0]
  region = os.environ["SDFB_DATAFLOW_REGION"]
  by_job: dict = {}
  for row in rows:
    assert row["job_id"], (row["run_id"], "job_id is NULL — see RUN_PLAYBOOK "
                           "§2d step 8")
    by_job.setdefault(row["job_id"], []).append(_when(row["launched_at"]))
  for job_id, launched in sorted(by_job.items()):
    created = _when(_job(project, region, job_id)["createTime"])
    seconds = (min(launched) - created).total_seconds()
    print(f"{job_id}: launched {seconds:.0f} s after creation; margin "
          f"{_FLEX_WINDOW_SECONDS - seconds:.0f} s")
    assert seconds < _FLEX_WINDOW_SECONDS, job_id


def test_one_evaluation_covers_every_landed_table(launch):
  base, rows = launch
  evaluations = _query(os.environ["SDFB_REGISTRY_TABLE"],
                       "run_ids, generation_job_ids", "base_run_id", base,
                       "recorded_at")
  landed = {r["run_id"] for r in rows}
  assert any(landed <= set(e["run_ids"]) for e in evaluations), evaluations
  # A NULL job id is the generation_record_job_id source=none fallback (the
  # job id was not resolvable); it cannot be compared with the registry.
  jobs = {r["job_id"] for r in rows if r["job_id"]}
  if not jobs:
    pytest.skip("every row's job_id is NULL (generation_record_job_id "
                "source=none milestone): the job ids cannot be compared")
  assert any(
      landed <= set(e["run_ids"]) and jobs <= set(e["generation_job_ids"])
      for e in evaluations), evaluations
