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
"""The generation record's columns: appended to `validation_runs`, NULLABLE,
and split between what the launcher knows and what a worker reads.

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import json
from pathlib import Path

from sdfb_core.record import (LAUNCHER_COLUMNS, RECORD_COLUMNS, SCHEMA_PATH,
                              WORKER_COLUMNS, bq_update_command,
                              missing_record_columns)
from sdfb_core.validation.summary import RunSummary

_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA = _ROOT / SCHEMA_PATH
_N_SUMMARY = 21


def _fields() -> list[dict]:
  return json.loads(_SCHEMA.read_text(encoding="utf-8"))


def _by_name() -> dict[str, dict]:
  return {f["name"]: f for f in _fields()}


def test_the_schema_is_the_summary_then_the_record():
  names = [f["name"] for f in _fields()]
  assert len(RunSummary.model_fields) == _N_SUMMARY
  assert set(names[:_N_SUMMARY]) == set(RunSummary.model_fields)
  assert names[_N_SUMMARY:] == list(RECORD_COLUMNS)


def test_record_columns_are_nullable_typed_and_documented():
  by_name = _by_name()
  for column in RECORD_COLUMNS:
    assert by_name[column]["mode"] == "NULLABLE", column
    assert "ADR 0044" in by_name[column]["description"], column
  assert by_name["launched_at"]["type"] == "TIMESTAMP"
  assert by_name["similarity"]["type"] == "FLOAT"
  assert {
      by_name[c]["type"]
      for c in RECORD_COLUMNS
      if c not in ("launched_at", "similarity")
  } == {"STRING"}


def test_worker_and_launcher_columns_split_the_record():
  assert set(WORKER_COLUMNS) | set(LAUNCHER_COLUMNS) == set(RECORD_COLUMNS)
  assert not set(WORKER_COLUMNS) & set(LAUNCHER_COLUMNS)
  assert list(LAUNCHER_COLUMNS) == [
      c for c in RECORD_COLUMNS if c not in WORKER_COLUMNS
  ]


def test_a_legacy_table_misses_every_record_column():
  names = [f["name"] for f in _fields()]
  assert missing_record_columns(names[:_N_SUMMARY]) == list(RECORD_COLUMNS)
  assert missing_record_columns(names) == []
  assert missing_record_columns([*names[:_N_SUMMARY],
                                 "job_id"])[0] == ("job_name")


def test_the_update_command_names_the_schema_file_and_the_table():
  assert bq_update_command("p.synthetic_data_quality.validation_runs") == (
      "bq update --schema "
      "config/bq_schema/synthetic_data_quality/validation_runs.schema.json "
      "p:synthetic_data_quality.validation_runs")
  assert bq_update_command("p:d.t").endswith(" p:d.t")


def test_descriptions_name_the_dlq_table_and_the_tolerance_key():
  by_name = _by_name()
  assert "dlq.run_id" in by_name["run_id"]["description"]
  assert "dead_letter" not in by_name["run_id"]["description"]
  tolerance = by_name["repeat_share_within_tolerance"]["description"]
  assert "guardrails.repeat_share_tolerance" in tolerance
  assert "REPEAT_SHARE_TOLERANCE" not in tolerance


def test_store_key_descriptions_name_the_settings_salt():
  for path, column in (
      ("synthetic_rag/freetext_pools.schema.json", "model_uri"),
      ("synthetic_rag/rag_chunks.schema.json", "embedder_version"),
      ("synthetic_rag/source_table_stats.schema.json", "profiler_version"),
      ("synthetic_data_quality/fk_fanout_stats.schema.json", "model_sha"),
  ):
    fields = json.loads((_SCHEMA.parents[1] / path).read_text(encoding="utf-8"))
    (field,) = [f for f in fields if f["name"] == column]
    assert "+cfg.<salt>" in field["description"], path
