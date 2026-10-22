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

Design: docs/DESIGN.md §7 Validation and the dead-letter queue;
§8 Configuration
(ADR 0043, 0044).
"""
from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sdfb_core.config.model import (CONFIG_VERSION, IDENTITY_KEYS,
                                    GeneratorConfig)
from sdfb_core.observability import sha12

if TYPE_CHECKING:  # pragma: no cover - typing only
  from sdfb_core.config.resolve import ConfigResolution
  from sdfb_core.contracts.relationships import RelationshipRegistry

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


_DERIVED_SEED = "derived"


def canonical_json(value: object) -> str:
  """Sorted keys, no spaces: the form every digest of the record hashes."""
  return json.dumps(value, sort_keys=True, separators=(",", ":"))


def config_values(config: GeneratorConfig) -> dict[str, dict[str, Any]]:
  """The snapshot's `values`: every section as JSON, launch identity left out.

    Alone, it is a valid --config_uri file that resolves to `config` again
    (the run id is a flag); it holds the derived-default keys as `null`,
    which a file reads as "not pinned" (ADR 0043)."""
  values: dict[str, dict[str, Any]] = config.model_dump(
      mode="json", exclude={"config_version"})
  for dotted in IDENTITY_KEYS.values():
    section, key = dotted.split(".", 1)
    values[section].pop(key, None)
  return values


def config_digest(config: GeneratorConfig) -> str:
  """Same digest, same generator config (launch identity excluded)."""
  return sha12(canonical_json(config_values(config)))


def relationships_block(registry: RelationshipRegistry, uri: str,
                        tables: Sequence[str]) -> dict[str, Any]:
  """The snapshot's `relationships`: this launch's tables as their model
    declares them, embedded because a gs:// model file can be overwritten
    after the run; `sha12` is the registry's (the relationships_loaded
    milestone's)."""
  declared: dict[str, Any] = {}
  models: list[str] = []
  for table in tables:
    model = registry.model_for(table)
    relations = registry.relations(table)
    if model is None or relations is None:
      continue
    if model.model not in models:
      models.append(model.model)
    declared[table.rsplit(".", 1)[-1]] = relations.model_dump(mode="json")
  return {
      "uri": uri,
      "model": ",".join(models) or None,
      "sha12": registry.sha12() if registry.models else None,
      "tables": declared,
  }


def _jsonable(value: object) -> Any:
  return json.loads(json.dumps(value, default=str))


def build_snapshot(resolution: ConfigResolution,
                   *,
                   derived: Mapping[str, Any],
                   thresholds: Mapping[str, Any],
                   relationships: Mapping[str, Any],
                   beam: Mapping[str, Any],
                   launcher_version: str,
                   batch: Mapping[str, Any] | None = None) -> dict[str, Any]:
  """The `config` column: `values` replays the launch; the other blocks say
    where each value came from and what the launch derived and ran on.
    `batch` is a batched launch's {index, count, tables} (phase B)."""
  return {
      "config_version": CONFIG_VERSION,
      "values": config_values(resolution.config),
      "provenance": dict(resolution.provenance),
      "overridden": [{
          "key": o.key,
          "typed": _jsonable(o.typed),
          "file": _jsonable(o.file)
      } for o in resolution.overridden],
      "derived": _jsonable(dict(derived)),
      "thresholds": _jsonable(dict(thresholds)),
      "relationships": _jsonable(dict(relationships)),
      "beam": _jsonable(dict(beam)),
      "launcher": {
          "version": launcher_version
      },
      "batch": _jsonable(dict(batch)) if batch is not None else None,
  }


@dataclass(frozen=True)
class LaunchRecord:
  """One Dataflow job's record columns as the launcher knows them
    (`LAUNCHER_COLUMNS`); a worker adds `WORKER_COLUMNS`. Pickled into the
    summary DoFn."""
  columns: Mapping[str, Any]
  launcher_version: str
  read_job_id: bool


def launch_record(snapshot: Mapping[str, Any], *, job_name: str | None,
                  launch_run_id: str, launched_at: str,
                  relationships_digest: str | None,
                  read_job_id: bool) -> LaunchRecord:
  """The launcher's twelve record columns, derived from the snapshot."""
  values = snapshot["values"]
  generation = values["generation"]
  seed = str(generation["seed"]).strip()
  columns = {
      "job_name": job_name,
      "launch_run_id": launch_run_id,
      "launched_at": launched_at,
      "embedder_uri": values["rag"]["embedder_uri"],
      "retrieval_method": (values["free_text"]["pool_seed_strategy"]
                           if generation["engine"] == "b1_rag" else None),
      "similarity": generation["similarity"],
      "seed": seed or _DERIVED_SEED,
      "source_stats": values["sampling"]["source_stats"],
      "relationships_uri": values["relational"]["relationships_uri"],
      "relationships_digest": relationships_digest,
      "config_digest": sha12(canonical_json(values)),
      "config": canonical_json(snapshot),
  }
  return LaunchRecord(
      columns={c: columns[c] for c in LAUNCHER_COLUMNS},
      launcher_version=str(snapshot["launcher"]["version"]),
      read_job_id=read_job_id)
