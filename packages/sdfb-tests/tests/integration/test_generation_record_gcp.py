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
"""AC-A2-5 probe: a Dataflow launch wrote the whole generation record
(ADR 0044). Needs GCP credentials; skipped on the laptop.

    SDFB_RECORD_TABLE=<project>.synthetic_data_quality.validation_runs \
    SDFB_RECORD_RUN_ID=<the launch's run id> \
    SDFB_RECORD_DAY=<YYYY-MM-DD, at or before the launch> \
    uv run --no-sync python3 -m pytest -m gcp \
      packages/sdfb-tests/tests/integration/test_generation_record_gcp.py

`SDFB_RECORD_DAY` is optional and recommended: it adds
`created_at >= TIMESTAMP(@day)`, so the query reads the launch's DAY
partitions only (a table with `require_partition_filter` needs it).

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import json
import os

import pytest
from sdfb_core.record import RECORD_COLUMNS

pytestmark = pytest.mark.gcp


def test_every_row_of_the_launch_carries_the_record():
  from google.cloud import bigquery  # pylint: disable=import-outside-toplevel  # GCP-only test

  table = os.environ["SDFB_RECORD_TABLE"]
  run_id = os.environ["SDFB_RECORD_RUN_ID"]
  day = os.environ.get("SDFB_RECORD_DAY")
  sql = f"SELECT * FROM `{table}` WHERE launch_run_id = @run_id"
  parameters = [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
  if day:
    sql += " AND created_at >= TIMESTAMP(@day)"
    parameters.append(bigquery.ScalarQueryParameter("day", "STRING", day))
  rows = list(bigquery.Client().query(
      sql,
      job_config=bigquery.QueryJobConfig(query_parameters=parameters)).result())
  assert rows, f"no validation_runs row has launch_run_id={run_id}"
  for row in rows:
    for column in RECORD_COLUMNS:
      if column in ("retrieval_method", "relationships_digest"):
        continue  # NULL by design for b2_library / a launch without a model
      assert row[column] is not None, (row["run_id"], column)
    snapshot = json.loads(row["config"])
    assert snapshot["launcher"]["version"] == row["generator_version"]
  assert len({row["config_digest"] for row in rows}) == 1
