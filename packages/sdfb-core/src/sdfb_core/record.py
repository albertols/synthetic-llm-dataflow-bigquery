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
"""The generation record: what `validation_runs` records about a generation.

Fourteen NULLABLE columns follow the 21 of the run summary: the Dataflow job,
the launch's base run id, the worker's generator version, the launch time,
five generation settings, the relationships model's URI and digest, the
config digest, and `config` — the JSON snapshot of the resolved config. The
launcher knows twelve of them; a worker adds its job id and its version.

A table created before these columns gets today's 21-column row and one
WARNING carrying `bq_update_command` (ADR 0044).

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

from collections.abc import Collection

RECORD_COLUMNS: tuple[str, ...] = (
    "job_id",
    "job_name",
    "launch_run_id",
    "generator_version",
    "launched_at",
    "embedder_uri",
    "retrieval_method",
    "similarity",
    "seed",
    "source_stats",
    "relationships_uri",
    "relationships_digest",
    "config_digest",
    "config",
)
# Known only on a worker: the Dataflow job's id (GCE metadata) and the
# version of the code that wrote the rows.
WORKER_COLUMNS: tuple[str, ...] = ("job_id", "generator_version")
LAUNCHER_COLUMNS: tuple[str, ...] = tuple(
    c for c in RECORD_COLUMNS if c not in WORKER_COLUMNS)
SCHEMA_PATH = "config/bq_schema/synthetic_data_quality/validation_runs.schema.json"


def missing_record_columns(live: Collection[str]) -> list[str]:
  """The record columns a live table lacks, in schema order."""
  have = set(live)
  return [c for c in RECORD_COLUMNS if c not in have]


def bq_update_command(table_fqn: str) -> str:
  """The one command that adds the record columns to an existing table: the
    committed schema file is a superset, so `bq update` only appends."""
  table = table_fqn if ":" in table_fqn else table_fqn.replace(".", ":", 1)
  return f"bq update --schema {SCHEMA_PATH} {table}"
