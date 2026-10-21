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
"""`job_id` → "what did generation job X write, with which parameters".

Five sources, each answering what it can:

    source                       answers                                 lost when
    ───────────────────────────  ──────────────────────────────────────  ─────────────────────
    1 validation_runs record     the resolved config snapshot, job id,   a table or launch
      (ADR 0044)                 generator version, run ids, digests      before ADR 0044
    2 BigQuery JOBS labels       tables written + commit windows + rows  no resourceViewer
    3 launch_config log entry    every launch argument, table order,     log retention, or a
                                 run ids (+ relationships_loaded sha,    launch predating it
                                 model_adjustment_model uri)
    4 Dataflow job parameters    the launch arguments (display data)     job retention (~30 d)
    5 manual                     anything still missing                  —

`params` (→ the registry's typed filters) is filled in that order, each
source filling only the keys still missing: the record (its snapshot's
`values`, or its typed columns on a row without a snapshot) → the
`launch_config` log, else the Dataflow display data → manual. A manual value
that disagrees with a resolved one is ignored and warned about. The
caller's tables are never replaced here. `params_source`, in the
registry's vocabulary, is `validation_runs` when the record supplied the
snapshot, or the only params besides manual input (ADR 0044);
otherwise the launch's own source, as before the record:
`jobs_labels+logs` (the `launch_config` log, with JOBS labels resolved),
`logs` (the log without them), `dataflow_params` (the display data) or
`manual`. With a snapshot, `generation_params` is the snapshot itself.
When rows of the launch's tables name their Dataflow job, only the job's
own rows are its runs. Every fallback taken is a warning on the context —
nothing is dropped silently.

One path reads less: `resolve_launch(job_only=True)`, for a job id given
next to hand-named tables (`--seed_table T --generation_job_id J`), reads
only source 4's job resource for the job's id, name, region and window — no
log, no JOBS labels — and takes the rest from the job's `validation_runs`
record and from `manual`. Given the generator's `validation_runs_table` and
with the window known, it reads this launch's rows of that table (one
query), which carry the reference digests and, since ADR 0044, the record.

`tables_in_order` never silently narrows a relational launch to its
`--landing_table` target:

    launch_config resolved list ──(run_ids[i] ends -{i:02d}-{table}? no →
          │                        discarded as line-reordered, fall back)
          ▼ absent
    validation_runs positions B-NN-<table> (matched by landing table OR by
          │                        run-id base; NN gaps are warned)
          ▼ plus
    labelled writes to non-side tables the above missed (write-end order,
          │                        warned by name)
          ▼ plus
    --landing_table targets not seen anywhere else

Side tables are the ones the launch parameters name. When they do not
name all six, a write into `synthetic_data_quality` / `synthetic_rag` or
under a side-table name (`dlq`, `validation_runs`, …) is a side table too
(Ruling R50), and the guess is a warning naming the tables.

A Dataflow job that is not found (wrong region, or past Dataflow's
retention) is not fatal on its own: JOBS labels, validation_runs and
manual input are still tried, and `JobNotFoundError` is raised only when
none of them resolves a table.

Design: docs/designs/2026-07-07-evaluation-framework-design.md

Design: docs/DESIGN.md §7 Validation and the dead-letter queue;
§8 Configuration
(ADR 0043, 0044).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sdfb_evaluation.context.bq import BqApiError
from sdfb_evaluation.context.gcp import (
    JOB_NOT_FOUND_HINT,
    DataflowJobs,
    GcpApiError,
    JobNotFoundError,
    LogMilestones,
    parse_milestone_fields,
    parse_pretty_milestone,
)
from sdfb_evaluation.context.jobs import (
    JobWrite,
    parse_timestamp,
    writes_by_beam_job,
)
from sdfb_evaluation.context.runs import of_job, runs_for, split_run_id

__all__ = [
    "GenerationRecord",
    "LaunchContext",
    "RunRecord",
    "UnresolvedLaunchError",
    "record_of",
    "record_params",
    "resolve_launch",
]

_logger = logging.getLogger(__name__)

# Launch arguments that name the generator's own side tables (never a
# landing table).
_AUX_TABLE_PARAMS = (
    "dlq_table",
    "validation_runs_table",
    "freetext_pools_table",
    "rag_chunks_table",
    "fk_fanout_stats_table",
    "source_stats_table",
)
# Ruling R50: when the launch parameters do not name every side table, a
# written table is one anyway if it lives in the generator's quality/RAG
# dataset or carries one of its side-table names.
_SIDE_DATASETS = frozenset({"synthetic_data_quality", "synthetic_rag"})
_SIDE_NAMES = frozenset({
    "dlq",
    "validation_runs",
    "freetext_pools",
    "rag_chunks",
    "fk_fanout_stats",
    "source_table_stats",
})
_MANUAL_KEYS = frozenset({
    "generation_job_id",
    "job_name",
    "region",
    "started_at",
    "finished_at",
    "base_run_id",
    "run_ids",
    "tables_in_order",
    "reference_table",
    "write_disposition",
    "relationships_uri",
    "model_sha",
    "model_name",
    "adjusted_model_uri",
    "model_adjusted",
    "params",
})
_SEQUENCE_KEYS = frozenset({"run_ids", "tables_in_order"})
_TIMESTAMP_KEYS = frozenset({"started_at", "finished_at"})
_BOOL_KEYS = frozenset({"model_adjusted"})
# Where a BigQuery source can fail without failing the resolution.
_BQ_SOURCE_ERRORS = (PermissionError, LookupError, ValueError, BqApiError)


class UnresolvedLaunchError(ValueError):
  """No source resolved a single landing table."""


# `sdfb_core.rag.embedding.embedder_identity("")`: the dependency-free
# HashingEmbedder's fixed identity.
_DEFAULT_EMBEDDER_ID = "hashing-384"
_ID_VERSION_SEGMENTS = 2  # …/embedders/{id}/{version}/
_NOT_WRITTEN = "(not written)"


def _text(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _raw_text(value: Any) -> str | None:
  """Text kept as written: "" is a value (no embedder, relationships off)."""
  return None if value is None else str(value)


def _dotted(table: str) -> str:
  return table.strip().replace(":", ".", 1)


def _as_int(value: Any) -> int:
  if isinstance(value, bool):
    raise ValueError("a boolean is not a count")
  if isinstance(value, int):
    return value
  if isinstance(value, float) and value.is_integer():
    return int(value)
  return int(str(value).strip())


def _as_float(value: Any) -> float:
  if isinstance(value, bool):
    raise ValueError("a boolean is not a number")
  number = float(value if isinstance(value, (int, float)) else str(value))
  if not math.isfinite(number):
    raise ValueError("not finite")
  return number


def _optional_float(value: Any) -> float | None:
  try:
    return None if value is None else _as_float(value)
  except (TypeError, ValueError):
    return None


def _snapshot(value: Any) -> dict[str, Any] | None:
  """`validation_runs.config` parsed: a JSON object, else None."""
  if isinstance(value, Mapping):
    return dict(value)
  if not isinstance(value, str) or not value.strip():
    return None
  try:
    parsed = json.loads(value)
  except ValueError:
    return None
  return parsed if isinstance(parsed, dict) else None


def _embedder_id(uri: Any) -> str:
  """Mirror of `sdfb_core.rag.embedding.embedder_identity(uri)[0]`: the
  second-to-last path segment of `…/embedders/{id}/{version}/`."""
  text = str(uri or "")
  if not text:
    return _DEFAULT_EMBEDDER_ID
  parts = [s for s in text.replace("gs://", "").split("/") if s]
  return parts[-2] if len(parts) >= _ID_VERSION_SEGMENTS else parts[0]


def _typed(params: Mapping[str, Any], write_disposition: str | None,
           notes: list[str] | None) -> dict[str, Any]:
  """The registry's generation-filter columns, in schema order."""

  def coerce(column: str, key: str, cast: Callable[[Any], Any]) -> Any:
    value = params.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
      return None
    try:
      return cast(value)
    except (TypeError, ValueError):
      if notes is not None:
        notes.append(f"generation parameter {key}={value!r} is not a valid "
                     f"{column}; the registry filter stays NULL (the raw "
                     f"value is kept in generation_params)")
      return None

  seed = params.get("seed")
  return {
      "engine":
          _text(params.get("engine")),
      "llm_model_uri":
          _text(params.get("model_uri")),
      "embedder_id":
          _embedder_id(params["embedder_uri"])
          if "embedder_uri" in params else None,
      "seed": (None if "seed" not in params else _text(seed) or "derived"),
      "similarity":
          coerce("similarity", "similarity", _as_float),
      "retrieval_method":
          _text(params.get("pool_seed_strategy")),
      "reference_rows_limit":
          coerce("reference_rows_limit", "reference_rows_limit", _as_int),
      "num_rows_requested":
          coerce("num_rows_requested", "num_rows", _as_int),
      "uniqueness_mode":
          _text(params.get("uniqueness_mode")),
      "freetext_expansion":
          _text(params.get("freetext_expansion")),
      "source_stats_tier":
          _text(params.get("source_stats")),
      "profiler_version":
          _text(params.get("profiler_version")),
      "write_disposition":
          write_disposition,
      "env":
          _text(params.get("env")),
      "client_type":
          _text(params.get("client_type")),
      "vllm_dtype":
          _text(params.get("vllm_dtype")),
  }


@dataclass(frozen=True)
class RunRecord:
  """One table's `validation_runs` row: what the generation DAG measured,
  and — since ADR 0044 — the generation record (None on older rows)."""
  landing_table: str
  run_id: str
  reference_table: str | None = None
  reference_digest: str | None = None
  valid_count: int | None = None
  num_rows_requested: int | None = None
  status: str | None = None
  created_at: str | None = None
  engine: str | None = None
  model_uri: str | None = None
  env: str | None = None
  job_id: str | None = None
  job_name: str | None = None
  launch_run_id: str | None = None
  generator_version: str | None = None
  launched_at: str | None = None
  embedder_uri: str | None = None
  retrieval_method: str | None = None
  similarity: float | None = None
  seed: str | None = None
  source_stats: str | None = None
  relationships_uri: str | None = None
  relationships_digest: str | None = None
  config_digest: str | None = None
  config: Mapping[str, Any] | None = field(
      default=None, compare=False, hash=False, repr=False)

  @classmethod
  def from_row(cls, row: Mapping[str, Any]) -> RunRecord:

    def count(key: str) -> int | None:
      value = row.get(key)
      return None if value is None else int(value)

    def text(key: str) -> str | None:
      return _text(row.get(key))

    return cls(
        landing_table=_dotted(str(row["landing_table"])),
        run_id=str(row["run_id"]),
        reference_table=text("reference_table"),
        reference_digest=text("reference_digest"),
        valid_count=count("valid_count"),
        num_rows_requested=count("num_rows_requested"),
        status=text("status"),
        created_at=text("created_at"),
        engine=text("engine"),
        model_uri=text("model_uri"),
        env=text("env"),
        job_id=text("job_id"),
        job_name=text("job_name"),
        launch_run_id=text("launch_run_id"),
        generator_version=text("generator_version"),
        launched_at=text("launched_at"),
        embedder_uri=_raw_text(row.get("embedder_uri")),
        retrieval_method=text("retrieval_method"),
        similarity=_optional_float(row.get("similarity")),
        seed=text("seed"),
        source_stats=text("source_stats"),
        relationships_uri=_raw_text(row.get("relationships_uri")),
        relationships_digest=text("relationships_digest"),
        config_digest=text("config_digest"),
        config=_snapshot(row.get("config")))

  @property
  def base_and_index(self) -> tuple[str, int | None]:
    return split_run_id(self.run_id, self.landing_table)

  def written_by(self, job_id: str | None, job_name: str | None) -> bool:
    """This row's record names the Dataflow job (`runs.of_job`)."""
    return of_job(self.job_id, self.job_name, job_id, job_name)


_TRUTHY = frozenset({"true", "1", "yes"})


def _truthy(value: Any) -> bool:
  return value is not None and str(value).strip().lower() in _TRUTHY


def _is_snapshot(config: Mapping[str, Any] | None) -> bool:
  """A parsed `config` this reader can use: an object with a `values`
  block (ADR 0044). Anything else — another format, or a newer
  generator's — is no snapshot."""
  return config is not None and isinstance(config.get("values"), Mapping)


@dataclass(frozen=True)
class GenerationRecord:
  """A generation job's `validation_runs` rows — every run it was given, in
  run order — and the config snapshot they carry (ADR 0044); every table of
  one job carries the same snapshot."""
  runs: tuple[RunRecord, ...]
  snapshot: Mapping[str, Any] = field(compare=False, hash=False, repr=False)

  def value(self, section: str, key: str) -> Any:
    values = self.snapshot.get("values")
    block = values.get(section) if isinstance(values, Mapping) else None
    return block.get(key) if isinstance(block, Mapping) else None

  @property
  def generates_relationships(self) -> bool:
    """The snapshot states `generate_fk_relationships` as true."""
    return _truthy(self.value("relational", "generate_fk_relationships"))

  @property
  def relationships_off(self) -> bool:
    """The snapshot states `generate_fk_relationships`, and not as true. A
    snapshot that does not state it says nothing about relationships."""
    flag = self.value("relational", "generate_fk_relationships")
    return flag is not None and not _truthy(flag)

  @property
  def relationships_uri(self) -> str | None:
    """The model the launch generated with; None when relationships were
    off (an isolated generation is evaluated as one) or when the snapshot
    names no model."""
    if self.relationships_off:
      return None
    return _text(self.value("relational", "relationships_uri"))

  @property
  def reference_rows_limit(self) -> int | None:
    value = self.value("sampling", "reference_rows_limit")
    try:
      return None if value is None else _as_int(value)
    except (TypeError, ValueError):
      return None

  @property
  def landing_tables(self) -> tuple[str, ...]:
    return tuple(r.landing_table for r in self.runs)

  def cache_key(self, cache: str) -> str | None:
    """The key the launch read and wrote that store under (A1 ruling R11)."""
    derived = self.snapshot.get("derived")
    keys = derived.get("cache_keys") if isinstance(derived, Mapping) else None
    return _text(keys.get(cache)) if isinstance(keys, Mapping) else None


def record_of(runs: Sequence[RunRecord]) -> GenerationRecord | None:
  """The record these runs carry: the first snapshot in run order."""
  ordered = sorted(
      runs, key=lambda r: (r.base_and_index[1] or 0, r.created_at or ""))
  for run in ordered:
    if run.config is not None and _is_snapshot(run.config):
      return GenerationRecord(runs=tuple(ordered), snapshot=run.config)
  return None


# Which tables a launch generated is said by the record's ROWS (each one's
# landing_table and reference_table) and, on the job-only path, by the
# caller — never by the launch-level flags, which name only the targets of a
# relational closure. Left out of the record's params so `_order` and the
# caller's tables decide exactly as before.
_RECORD_PARAM_EXCLUDED = frozenset({"landing_table", "reference_table"})


def record_params(runs: Sequence[RunRecord],
                  notes: list[str] | None = None) -> dict[str, Any]:
  """The generation parameters the record states, under launch-argument
  names: the snapshot's `values` flattened (a key keeps its CLI name inside
  its section, ADR 0043) minus the two table-naming flags, plus
  `derived.profiler_version`, with `relationships_uri` "" when the snapshot
  states relationships were off. A null value is a derived default the
  snapshot does not pin (ADR 0043), so it states nothing and a later source
  may fill the key. Without a snapshot, the first run's typed columns. A
  row that has a `config_digest` but no parseable `config`, or a `config`
  with no `values` block, is a note."""
  record = record_of(runs)
  if record is not None:
    params: dict[str, Any] = {}
    for block in record.snapshot["values"].values():
      if isinstance(block, Mapping):
        params.update({
            k: v
            for k, v in block.items()
            if k not in _RECORD_PARAM_EXCLUDED and v is not None
        })
    derived = record.snapshot.get("derived")
    if isinstance(derived, Mapping) and derived.get("profiler_version"):
      params["profiler_version"] = derived["profiler_version"]
    if record.relationships_off:
      params["relationships_uri"] = ""
    return params
  if notes is not None:
    for run in runs:
      if run.config is not None:
        notes.append(f"{run.landing_table}: validation_runs.config has no "
                     "values block (another format, or a newer generator's); "
                     "the record's typed columns are used")
      elif run.config_digest:
        notes.append(f"{run.landing_table}: validation_runs has a "
                     "config_digest but its config could not be parsed; the "
                     "record's typed columns are used")
  ordered = sorted(runs, key=lambda r: (r.base_and_index[1] or 0))
  if not ordered:
    return {}
  first = ordered[0]
  typed = {
      "engine": first.engine,
      "model_uri": first.model_uri,
      "env": first.env,
      "embedder_uri": first.embedder_uri,
      "pool_seed_strategy": first.retrieval_method,
      "similarity": first.similarity,
      "seed": first.seed,
      "source_stats": first.source_stats,
  }
  return {k: v for k, v in typed.items() if v is not None}


def _manual(manual: Mapping[str, Any] | None) -> tuple[dict, dict]:
  """(field values, params) of a manual mapping, strictly validated."""
  if not manual:
    return {}, {}
  unknown = sorted(set(manual) - _MANUAL_KEYS)
  if unknown:
    raise ValueError(f"unknown manual key(s) {unknown}; expected any of "
                     f"{sorted(_MANUAL_KEYS)}")
  fields: dict[str, Any] = {}
  params: dict[str, Any] = {}
  for key, value in manual.items():
    if value is None:
      continue
    if key == "params":
      if not isinstance(value, Mapping) or not all(
          isinstance(k, str) for k in value):
        raise ValueError("manual params must be a mapping of launch "
                         f"argument name → value, got {value!r}")
      params = dict(value)
    else:
      fields[key] = _manual_value(key, value)
  return fields, params


def _manual_value(key: str, value: Any) -> Any:
  """One manual field, type-checked; the ValueError names the field."""
  if key in _SEQUENCE_KEYS:
    if (isinstance(value, str) or not isinstance(value, Sequence) or
        not all(isinstance(v, str) and v.strip() for v in value)):
      raise ValueError(
          f"manual {key} must be a list of non-empty strings, got {value!r}")
    return tuple(v.strip() for v in value)
  if key in _BOOL_KEYS:
    if not isinstance(value, bool):
      raise ValueError(f"manual {key} must be true or false, got {value!r}")
    return value
  if not isinstance(value, str) or not value.strip():
    raise ValueError(f"manual {key} must be a non-empty string, got {value!r}")
  if key in _TIMESTAMP_KEYS:
    try:
      parse_timestamp(value)
    except ValueError as exc:
      raise ValueError(f"manual {key} must be an RFC 3339 timestamp, got "
                       f"{value!r}") from exc
  return value.strip()


def _resolved_mismatch(launch_config: Mapping[str, Any]) -> str | None:
  """Why the launch_config's resolved table list and run ids disagree, or
  None. `plan_launch` names run i of a multi-table launch
  `{run_id}-{i:02d}-{table}`; a single table runs as `{run_id}` itself.
  Lines the launcher logged in the same instant can come back reordered
  and still parse — this is the check that catches it."""
  resolved = launch_config.get("resolved")
  if not isinstance(resolved, Mapping):
    return None
  tables = [str(t) for t in resolved.get("tables_in_order") or ()]
  run_ids = [str(r) for r in resolved.get("run_ids") or ()]
  if len(tables) != len(run_ids):
    return f"{len(tables)} tables but {len(run_ids)} run ids"
  base = _text(launch_config.get("run_id"))
  if len(tables) == 1:
    if base is not None and run_ids[0] != base:
      return f"the single run id {run_ids[0]!r} is not the run_id {base!r}"
    return None
  for index, (table, run_id) in enumerate(zip(tables, run_ids, strict=True)):
    short = table.rsplit(".", 1)[-1]
    suffix = f"-{index:02d}-{short}"
    if not run_id.endswith(suffix) or (base is not None and
                                       run_id != base + suffix):
      return (f"run_ids[{index}]={run_id!r} does not match "
              f"tables_in_order[{index}]={table!r} (expected …{suffix})")
  return None


def _pick_runs(records: list[RunRecord], base: str | None,
               notes: list[str]) -> tuple[str | None, list[RunRecord]]:
  """(base, this launch's records): other launches' rows are dropped
  loudly; with no base known, the most recently created one is taken."""
  if not records:
    return base, []
  if base is None:
    latest = max(records, key=lambda r: r.created_at or "")
    base = latest.base_and_index[0]
    bases = sorted({r.base_and_index[0] for r in records})
    if len(bases) > 1:
      notes.append(f"validation_runs holds rows of {len(bases)} launches "
                   f"({bases}) for these tables in the window and the "
                   f"launch's own run id is unknown; taking the latest, "
                   f"{base} — pass manual base_run_id to choose")
  own = [r for r in records if r.base_and_index[0] == base]
  others = sorted(r.run_id for r in records if r.base_and_index[0] != base)
  if others:
    notes.append(f"validation_runs rows of another launch in the window were "
                 f"ignored: {others} — they wrote to the same landing tables "
                 f"as {base}; check the scope for contamination")
  latest_by_table: dict[str, RunRecord] = {}
  for record in sorted(own, key=lambda r: r.created_at or ""):
    if record.landing_table in latest_by_table:
      notes.append(f"{record.landing_table}: several validation_runs rows "
                   f"for run {record.run_id}; the latest is used")
    latest_by_table[record.landing_table] = record
  return base, list(latest_by_table.values())


def _job_rows(records: list[RunRecord], job_id: str | None,
              job_name: str | None, notes: list[str]) -> list[RunRecord]:
  """The rows whose record names this Dataflow job (ADR 0044), when any
  does — another job's rows of the same tables in the window are dropped
  loudly, never taken for this launch's; else every row, as before the
  record (`_pick_runs` then decides by run id)."""
  mine = [r for r in records if r.written_by(job_id, job_name)]
  if not mine:
    return records
  others = sorted(
      r.run_id for r in records if not r.written_by(job_id, job_name))
  if others:
    notes.append(f"validation_runs rows written by another Dataflow job in "
                 f"the window were ignored: {others} — they wrote to the "
                 f"same landing tables as job {job_id or job_name}; check the "
                 "scope for contamination")
  return mine


def _milestone_fields(milestones: Mapping[str, Sequence[Mapping[str, str]]],
                      name: str) -> list[Mapping[str, str]]:
  return list(milestones.get(name) or [])


@dataclass(frozen=True)
class LaunchContext:
  """What generation job X wrote, with which parameters.

  The rev-1 fields plus `params_source` and `writes`; `runs` (the
  per-table `validation_runs` rows), `model_adjusted` (None = unknown)
  and `warnings` (every fallback taken) complete what the registry and
  the planner read.
  """
  generation_job_id: str | None
  job_name: str | None
  region: str | None
  started_at: str | None
  finished_at: str | None
  base_run_id: str | None
  run_ids: tuple[str, ...]
  tables_in_order: tuple[str, ...]  # landing FQNs, parents first
  reference_table: str | None
  write_disposition: str | None
  relationships_uri: str | None
  # → the typed filters; also generation_params when the record has no
  # snapshot
  params: dict[str, Any] = field(hash=False)
  model_sha: str | None
  model_name: str | None
  adjusted_model_uri: str | None
  # validation_runs | jobs_labels+logs | logs | dataflow_params | manual
  params_source: str
  writes: tuple[JobWrite, ...]
  runs: tuple[RunRecord, ...] = ()
  model_adjusted: bool | None = None
  warnings: tuple[str, ...] = ()

  def typed_filters(self) -> dict[str, Any]:
    """The registry's generation-filter columns (`engine` … `vllm_dtype`
    of `evaluation_data_history`), from `params`:

    `model_uri` → `llm_model_uri`; `embedder_uri` → `embedder_id` (the
    generator's `embedder_identity`; "" → the HashingEmbedder's id);
    `seed` "" → "derived"; `pool_seed_strategy` → `retrieval_method`;
    `num_rows` → `num_rows_requested`; `source_stats` →
    `source_stats_tier`. A value that does not coerce to the column type
    is NULL (and was warned about when the context was built).
    """
    return _typed(self.params, self.write_disposition, None)

  def run_for(self, landing_table: str) -> RunRecord | None:
    """The `validation_runs` record of one landing table, if read."""
    wanted = _dotted(landing_table)
    return next((r for r in self.runs if r.landing_table == wanted), None)

  @property
  def record(self) -> GenerationRecord | None:
    """The generation record of this launch's runs (ADR 0044), if any."""
    return record_of(self.runs)

  @property
  def generator_version(self) -> str | None:
    return next((r.generator_version for r in self.runs if r.generator_version),
                None)

  @property
  def config_digest(self) -> str | None:
    return next((r.config_digest for r in self.runs if r.config_digest), None)

  def cache_key(self, cache: str) -> str | None:
    record = self.record
    return record.cache_key(cache) if record is not None else None

  @classmethod
  def from_sources(
      cls,
      *,
      job: dict | None,
      launch_config: dict | None,
      writes: Sequence[JobWrite],
      manual: Mapping | None,
      runs: Sequence[Mapping[str, Any]] = (),
      milestones: Mapping[str, Sequence[Mapping[str, str]]] | None = None,
      warnings: Sequence[str] = (),
      record_runs: Sequence[RunRecord] | None = None,
  ) -> LaunchContext:
    """Assemble a context from whatever the sources returned.

    Args:
      job: the Dataflow job resource, or None.
      launch_config: the parsed `launch_config` payload, or None.
      writes: `writes_by_beam_job` output (empty when not read).
      manual: operator-supplied values keyed by field name, plus `params`.
      runs: `runs_for` rows (any launch; this one's are picked).
      milestones: header fields of `relationships_loaded` and
        `model_adjustment_model` entries; None when the launch log was not
        read (then `model_adjusted` stays unknown).
      warnings: notes already collected by the caller.
      record_runs: the runs whose record fills the params first; default:
        this launch's own runs, picked from `runs`.

    Raises:
      ValueError: a malformed manual mapping (the message names the
        field).
      UnresolvedLaunchError: no landing table from any source (the message
        says what to pass).
    """
    notes = list(warnings)
    manual_fields, manual_params = _manual(manual)
    writes_t = tuple(writes)
    problem = (
        _resolved_mismatch(launch_config)
        if launch_config is not None else None)
    if problem is not None:
      notes.append(
          f"launch_config discarded: its resolved table order and run ids "
          f"disagree ({problem}) — the launcher's log lines were likely "
          "reordered in Cloud Logging; falling back to the Dataflow job "
          "parameters, validation_runs and labelled writes")
      launch_config = None
    launch_params, source = _launch_params(job, launch_config, bool(writes_t))
    # the launch's run id, else manual's (manual fills only a missing key)
    run_id = (launch_params
              if "run_id" in launch_params else manual_params).get("run_id")
    # The runs are picked first (their record fills the params), but their
    # notes follow the params' notes, in the order a launch without a record
    # has always warned in.
    run_notes: list[str] = []
    identity = job or {}
    records = _job_rows([RunRecord.from_row(r) for r in runs],
                        _text(identity.get("id")), _text(identity.get("name")),
                        run_notes)
    base, own = _pick_runs(records, _text(run_id), run_notes)
    filled_from = own if record_runs is None else list(record_runs)
    params, source = _params(
        record_params(filled_from, notes),
        record_of(filled_from) is not None, launch_params, source,
        manual_params, notes)
    notes.extend(run_notes)
    resolved = (launch_config or {}).get("resolved")
    resolved = resolved if isinstance(resolved, Mapping) else {}
    tables, own, run_ids = _order(resolved, own, params, writes_t, notes)

    window = DataflowJobs.window(job) if job is not None else (None, None)
    job = job or {}
    values: dict[str, Any] = {
        "generation_job_id": _text(job.get("id")),
        "job_name": _text(job.get("name")),
        "region": _text(job.get("location")),
        "started_at": window[0],
        "finished_at": window[1],
        "base_run_id": base,
        "run_ids": run_ids,
        "tables_in_order": tables,
        "reference_table": _text(params.get("reference_table")),
        "write_disposition": _text(params.get("write_disposition")),
        "relationships_uri": _text(params.get("relationships_uri")),
    }
    values.update(_model_fields(milestones, notes))
    for key, value in manual_fields.items():
      current = values.get(key)
      if current in (None, ()):
        values[key] = value
      elif current != value:
        notes.append(f"manual {key}={value!r} ignored: resolved "
                     f"{current!r} is kept")
    if not values["tables_in_order"]:
      raise _unresolved(values, notes)
    written = {w.table for w in writes_t}
    notes.extend(f"{t}: no labelled BigQuery write by this job (it failed "
                 "before landing the table, or wrote nothing)"
                 for t in values["tables_in_order"]
                 if writes_t and t not in written)
    _typed(params, values["write_disposition"], notes)
    for note in notes:
      _logger.warning("launch context: %s", note)
    return cls(
        params=params,
        params_source=source,
        writes=writes_t,
        runs=tuple(own),
        warnings=tuple(notes),
        **values)


def _launch_params(job: Mapping[str, Any] | None,
                   launch_config: Mapping[str, Any] | None,
                   has_writes: bool) -> tuple[dict[str, Any], str]:
  """(params, source) of the launch's own records: the `launch_config` log,
  else the Dataflow display data; ({}, "manual") when neither answers."""
  job_params = DataflowJobs.params(job) if job is not None else {}
  if launch_config is not None:
    return dict(launch_config), ("jobs_labels+logs" if has_writes else "logs")
  if job_params:
    return dict(job_params), "dataflow_params"
  return {}, "manual"


def _params(record: Mapping[str, Any], full_record: bool,
            launch: Mapping[str, Any], source: str, manual: Mapping[str, Any],
            notes: list[str]) -> tuple[dict[str, Any], str]:
  """(generation params, params_source), filled in order (ADR 0044): the
  record → the launch's own records → manual, each only where a key is
  still missing. The record names the source when it carried the snapshot,
  or when nothing but manual input would have. A manual value that
  disagrees is a note naming the source that supplied the kept value — the
  launch's own when it states the same value, as before the record."""
  params = dict(record)
  supplied_by = dict.fromkeys(record, "validation_runs")
  for key, value in launch.items():
    if key not in params:
      params[key] = value
    if params[key] == value:
      supplied_by[key] = source
  if record and (full_record or source == "manual"):
    source = "validation_runs"
  if not params and not manual:
    notes.append("no generation parameters resolved from any source; "
                 "the registry's generation filters stay NULL")
  for key, value in manual.items():
    if key not in params:
      params[key] = value
    elif params[key] != value:
      notes.append(f"manual params.{key}={value!r} ignored: the "
                   f"{supplied_by[key]} value {params[key]!r} is kept")
  return params, source


def _looks_like_side_table(table: str) -> bool:
  parts = _dotted(table).split(".")
  return parts[-1] in _SIDE_NAMES or (len(parts) > 1 and
                                      parts[-2] in _SIDE_DATASETS)


def _side_tables(params: Mapping[str, Any],
                 written: Sequence[str] = (),
                 notes: list[str] | None = None) -> set[str]:
  """The generator's own side tables (never landing tables).

  Those the launch named, plus — when its parameters do not name all six
  (`_AUX_TABLE_PARAMS`: a Dataflow display data list omits defaults, and
  manual input may carry none) — every `written` table in a side dataset
  (`synthetic_data_quality`, `synthetic_rag`) or under a side-table name
  (Ruling R50). A guess is a note naming the tables, never silent.
  """
  named = {
      _dotted(str(params[key]))
      for key in _AUX_TABLE_PARAMS
      if _text(params.get(key))
  }
  if all(key in params for key in _AUX_TABLE_PARAMS):
    return named
  guessed = sorted(
      {t for t in written if t not in named and _looks_like_side_table(t)})
  if guessed and notes is not None:
    notes.append(
        f"tables {guessed} were treated as the generator's side tables and "
        "are not evaluated: the launch parameters do not name its side "
        f"tables, and these live in {sorted(_SIDE_DATASETS)} or are named "
        f"one of {sorted(_SIDE_NAMES)}. Pass manual params (dlq_table, "
        "validation_runs_table, …) to override")
  return named | set(guessed)


def _order(
    resolved: Mapping[str, Any], own: Sequence[RunRecord],
    params: Mapping[str, Any], writes: Sequence[JobWrite], notes: list[str]
) -> tuple[tuple[str, ...], list[RunRecord], tuple[str, ...]]:
  """(tables_in_order, own runs in that order, run_ids).

  The launch_config's resolved list wins (a labelled write outside it is
  reported, not evaluated). Without it: validation_runs positions
  (`B-NN-<table>`), then every labelled write to a non-side table they
  missed in write-end order, then any `--landing_table` target not seen —
  with a warning naming each table added from the writes.
  """
  side = _side_tables(params, [w.table for w in writes], notes)
  written = list(
      dict.fromkeys(
          w.table
          for w in sorted(writes, key=lambda w: (w.end, w.job_id))
          if w.table not in side))
  listed = [_dotted(str(t)) for t in resolved.get("tables_in_order") or ()]
  if listed:
    tables = listed
    unlisted = [t for t in written if t not in listed]
    if unlisted:
      notes.append(f"this job also committed rows to {unlisted}, which the "
                   "launch_config table list does not name; they are not "
                   "evaluated")
  else:
    by_position = sorted(
        own, key=lambda r: (r.base_and_index[1] or 0, r.created_at or ""))
    known = list(dict.fromkeys(r.landing_table for r in by_position))
    targets = [
        _dotted(t)
        for t in str(params.get("landing_table") or "").split(",")
        if t.strip()
    ]
    added = [t for t in written if t not in (known or targets)]
    tables = (known + added) if known else list(written)
    tables += [t for t in targets if t not in tables]
    if added:
      notes.append(
          f"tables {added} were added from this job's labelled BigQuery "
          "writes, in write-end order: no launch_config or validation_runs "
          f"row listed them, and evaluating only {known or targets} would "
          "silently narrow a relational launch")
  _check_positions(own, len(tables), notes)
  position = {t: i for i, t in enumerate(tables)}
  ordered = sorted(
      own, key=lambda r: position.get(r.landing_table, len(tables)))
  run_ids = tuple(str(r) for r in resolved.get("run_ids") or ())
  return tuple(tables), ordered, run_ids or tuple(r.run_id for r in ordered)


def _check_positions(own: Sequence[RunRecord], n_tables: int,
                     notes: list[str]) -> None:
  """Warn when the launch's run positions (NN) have gaps, or point past
  the tables resolved — both mean tables of the launch are unaccounted
  for."""
  indices = sorted({
      index for index in (r.base_and_index[1] for r in own) if index is not None
  })
  if not indices:
    return
  gaps = sorted(set(range(indices[-1] + 1)) - set(indices))
  if gaps:
    notes.append(f"validation_runs has no row for position(s) {gaps} of this "
                 "launch (run ids …-NN-<table>): those tables' runs failed, "
                 "expired or were never recorded")
  beyond = sorted(
      r.run_id for r in own if (r.base_and_index[1] or 0) >= n_tables)
  if beyond:
    notes.append(f"run ids {beyond} sit at positions beyond the {n_tables} "
                 "table(s) resolved: the launch covered more tables than are "
                 "known")


def _unresolved(values: Mapping[str, Any],
                notes: Sequence[str]) -> UnresolvedLaunchError:
  job_id, region = values["generation_job_id"], values["region"]
  where = f" for Dataflow job {job_id} in region {region}" if job_id else ""
  return UnresolvedLaunchError(
      f"no landing table could be resolved{where}: the launch_config log "
      "entry, the Dataflow landing_table parameter, labelled BigQuery "
      "writes, validation_runs and manual input gave none. Pass a job_id "
      "(with --region where it ran), or manual={'tables_in_order': [...], "
      f"'params': {{...}}}}. Warnings: {list(notes)}")


def _model_fields(milestones: Mapping[str, Sequence[Mapping[str, str]]] | None,
                  notes: list[str]) -> dict[str, Any]:
  """model_sha / model_name / adjusted_model_uri / model_adjusted from
  the launch log's milestone header fields."""
  if milestones is None:
    return {
        "model_sha": None,
        "model_name": None,
        "adjusted_model_uri": None,
        "model_adjusted": None,
    }
  loaded = _milestone_fields(milestones, "relationships_loaded")
  shas = list(dict.fromkeys(m["sha"] for m in loaded if m.get("sha")))
  if len(shas) > 1:
    notes.append(f"relationships_loaded logged {len(shas)} different model "
                 f"shas {shas}; the first is used")
  adjusted = _milestone_fields(milestones, "model_adjustment_model")
  uris = list(
      dict.fromkeys(m["uri"]
                    for m in adjusted
                    if m.get("uri") and m["uri"] != _NOT_WRITTEN))
  if any(m.get("uri") == _NOT_WRITTEN for m in adjusted):
    notes.append("an adjusted relationship model was not written to storage; "
                 "it survives only in the job log (model_adjustment_model)")
  if len(uris) > 1:
    notes.append(f"{len(uris)} adjusted model files {uris}; the first is "
                 "recorded")
  return {
      "model_sha": shas[0] if shas else None,
      "model_name": _text(loaded[0].get("models")) if loaded else None,
      "adjusted_model_uri": uris[0] if uris else None,
      "model_adjusted": bool(adjusted),
  }


def _read_logs(logs: LogMilestones, job_id: str, window: tuple[str | None,
                                                               str | None],
               notes: list[str]) -> tuple[dict | None, dict[str, list] | None]:
  """(launch_config payload, milestone header fields) from the job log."""
  try:
    configs = logs.find(job_id, "launch_config", window)
    loaded = logs.find(job_id, "relationships_loaded", window)
    adjusted = logs.find(job_id, "model_adjustment_model", window)
  except (PermissionError, GcpApiError, ValueError) as exc:
    notes.extend(logs.warnings)
    notes.append(f"launch_config unavailable: the job log could not be read "
                 f"({exc}); launch parameters fall back to the Dataflow job "
                 f"parameters")
    return None, None
  notes.extend(logs.warnings)
  launch_config = None
  if not configs:
    notes.append("no launch_config entry in the job log (Cloud Logging "
                 "retention passed, the launch predates the milestone, it "
                 "was launched outside a flex template, or the launcher "
                 "stream was not matched); launch parameters fall back to the "
                 "Dataflow job parameters")
  else:
    if len(configs) > 1:
      notes.append(f"{len(configs)} launch_config entries in the job log; "
                   "the first is used")
    try:
      _, launch_config = parse_pretty_milestone(configs[0])
    except ValueError as exc:
      notes.append(f"launch_config entry could not be parsed ({exc}); "
                   "launch parameters fall back to the Dataflow job "
                   "parameters")
  if not (configs or loaded):
    return launch_config, None
  milestones: dict[str, list] = {}
  for name, texts in (("relationships_loaded", loaded),
                      ("model_adjustment_model", adjusted)):
    milestones[name] = []
    for text in texts:
      try:
        milestones[name].append(parse_milestone_fields(text))
      except ValueError as exc:
        notes.append(f"{name} entry could not be parsed ({exc})")
  return launch_config, milestones


def _landing_location(bq: Any, tables: Sequence[str],
                      notes: list[str]) -> str | None:
  for table in tables:
    try:
      location = bq.table(table).get("location")
    except _BQ_SOURCE_ERRORS as exc:
      notes.append(f"could not read the location of {table} ({exc})")
      continue
    if location:
      return str(location)
  return None


def _read_writes(bq: Any, job_id: str, window: tuple[str | None, str | None],
                 targets: Sequence[str],
                 notes: list[str]) -> tuple[JobWrite, ...]:
  location = bq.location or _landing_location(bq, targets, notes)
  if location is None:
    notes.append("BigQuery job labels not read: no BigQuery location is known "
                 "(no landing table resolved yet, and the Bq client has no "
                 "location)")
    return ()
  try:
    return tuple(
        writes_by_beam_job(
            bq, location=location, beam_job_id=job_id, window=window))
  except _BQ_SOURCE_ERRORS as exc:
    notes.append(f"BigQuery job labels unavailable: {exc}. Without them the "
                 "tables come from the launch log / validation_runs and no "
                 "write windows are known")
    return ()


def _read_runs(bq: Any, params: Mapping[str, Any], tables: Sequence[str],
               window: tuple[str | None, str | None], base: str | None,
               notes: list[str]) -> list[dict]:
  if "validation_runs_table" not in params:
    notes.append("validation_runs not read: the launch's "
                 "validation_runs_table is unknown")
    return []
  table = _text(params.get("validation_runs_table"))
  if table is None:
    notes.append("the launch wrote no validation_runs (validation_runs_table "
                 "was empty): no reference digests or valid counts")
    return []
  dataset, _, name = _dotted(table).rpartition(".")
  try:
    return runs_for(
        bq,
        quality_dataset=dataset,
        landing_tables=list(tables),
        window=window,
        table_name=name,
        base_run_id=base)
  except _BQ_SOURCE_ERRORS as exc:
    notes.append(f"validation_runs {table} could not be read ({exc})")
    return []


# The slice of a Dataflow job resource that says which job it is and when it
# ran. Its display data, which `DataflowJobs.params` reads, is left out: on
# the job-only path the tables and parameters are the caller's.
_JOB_IDENTITY_KEYS = ("id", "name", "location", "createTime", "currentState",
                      "currentStateTime")


def _job_identity(bq: Any, jobs: DataflowJobs, job_id: str, region: str,
                  manual: Mapping[str, Any] | None) -> LaunchContext:
  """The launch named by `manual`, belonging to the Dataflow job `job_id`:
  the job resource is read (its id, name, region and window); a job that
  cannot be read costs the window and one warning, never the launch.

  With the generator's `validation_runs_table` in `manual` params and the
  window known, this launch's rows of that table are read too — the one
  query, `runs_for` over the landing tables and the window, no run-id base
  — and carry the reference digests the privacy panel is verified against.
  Rows whose record names this job are kept over another job's in the same
  window (ADR 0044); among the rest, the caller's run id (`params.run_id`,
  else `base_run_id`) picks the launch, else the latest one does. They are
  attached to the context, and given to
  `from_sources` only as `record_runs`, whose record fills the params
  first: the run rows must not decide the table list (`_order` would
  narrow it to the tables that have a row); the caller's tables stay the
  tables. `params_source` is then `validation_runs` when the job's rows
  carry a record, else `manual`."""
  notes: list[str] = []
  job: dict[str, Any] = {"id": job_id, "location": region}
  window: tuple[str | None, str | None] = (None, None)
  try:
    read = jobs.get(job_id)
    job = {k: read[k] for k in _JOB_IDENTITY_KEYS if k in read}
    window = jobs.window(job)
  except (JobNotFoundError, PermissionError, GcpApiError) as exc:
    notes.append(f"Dataflow job {job_id} could not be read, so the "
                 f"generation window is unknown (the source is then read as "
                 f"it is now, not as the job saw it; validation_runs is not "
                 f"read without a window, so the reference sample cannot be "
                 f"verified and the metrics that compare with it are not "
                 f"evaluated): {exc}")
  fields, params = _manual(manual)
  tables = [_dotted(str(t)) for t in fields.get("tables_in_order") or ()]
  records: list[RunRecord] = []
  if window[0] and "validation_runs_table" in params:
    records = _job_rows([
        RunRecord.from_row(r)
        for r in _read_runs(bq, params, tables, window, None, notes)
    ], job_id, _text(job.get("name")), notes)
  # the caller's run id decides, as on the full path; else the latest launch
  base, own = _pick_runs(
      records,
      _text(params.get("run_id")) or fields.get("base_run_id"), notes)
  position = {t: i for i, t in enumerate(tables)}
  own.sort(key=lambda r: position.get(r.landing_table, len(tables)))
  _check_positions(own, len(tables), notes)
  context = LaunchContext.from_sources(
      job=job,
      launch_config=None,
      writes=(),
      manual=manual,
      warnings=notes,
      record_runs=own)
  return dataclasses.replace(
      context,
      base_run_id=base,
      runs=tuple(own),
      run_ids=tuple(r.run_id for r in own))


def resolve_launch(*,
                   bq: Any,
                   session_factory: Callable[[str], Any] | None,
                   project: str,
                   region: str,
                   job_id: str | None = None,
                   manual: Mapping[str, Any] | None = None,
                   log_pages: int = 10,
                   job_only: bool = False) -> LaunchContext:
  """Resolve a generation launch from its Dataflow `job_id`.

  Order: the Dataflow job (window, parameters) → the job log
  (`launch_config`, `relationships_loaded`, `model_adjustment_model`) →
  BigQuery JOBS labels (tables written, windows, rows) → `validation_runs`
  (run ids, digests, valid counts; matched by landing table and by the
  launch's run-id base) → `manual`; assembled by
  `LaunchContext.from_sources`, which fills the params record-first (the
  module docstring) and records `params_source`. Every
  BigQuery source failure (403, 404, 400, 5xx) degrades to a warning.

  Args:
    bq: a `Bq` in the project the generation's BigQuery jobs ran in.
    session_factory: `make_session`, or a fake; called with `project`.
    project: the project the Dataflow job ran in.
    region: the Dataflow region the job ran in.
    job_id: the Dataflow job id; None resolves from `manual` alone.
    manual: operator-supplied values (see `LaunchContext.from_sources`).
    log_pages: page budget per Cloud Logging list call.
    job_only: `job_id` is the identity of a launch `manual` already names
      (`sdfb-eval --seed_table T --generation_job_id J`): read the Dataflow
      job, for its id, name, region and window — not its log or BigQuery
      JOBS labels, not even as calls that may fail — and take everything
      else from `manual`. The one exception: with `validation_runs_table`
      in the manual params and the window known, this launch's rows of that
      table are read (one query) for the reference digests and the
      generation record, whose params come before `manual`'s (ADR 0044).
      A job that cannot be read is a warning and no window (and no
      `validation_runs` read). Needs `job_id` and tables in `manual`;
      `params_source` is then `validation_runs` when the job's rows carry
      a record, else `manual`.

  Raises:
    JobNotFoundError: the job is not in `region` (or past Dataflow's
      retention) AND no other source resolves a table; the message names
      the region, `--region` and the retention.
    PermissionError: the Dataflow job itself could not be read.
    ValueError: a malformed manual mapping, or nothing resolves a landing
      table (`UnresolvedLaunchError`, saying what to pass instead).
  """
  manual_fields, manual_params = _manual(manual)
  if job_id is None:
    if not manual:
      raise UnresolvedLaunchError(
          "nothing to resolve: pass a job_id (with --region where it ran), "
          "or manual={'tables_in_order': [...], 'params': {...}}")
    return LaunchContext.from_sources(
        job=None, launch_config=None, writes=(), manual=manual)
  if session_factory is None:
    raise ValueError(
        "resolving a job_id needs a session_factory (make_session)")
  notes: list[str] = []
  session = session_factory(project)
  jobs = DataflowJobs(session, project, region)
  if job_only:
    return _job_identity(bq, jobs, job_id, region, manual)
  missing: JobNotFoundError | None = None
  try:
    job: dict | None = jobs.get(job_id)
  except JobNotFoundError as exc:
    missing, job = exc, None
    notes.append(f"{exc} Falling back to BigQuery job labels, "
                 "validation_runs and manual input; the job log is not "
                 "searched (no job window, and it shares the job's "
                 "retention).")
  window: tuple[str | None, str | None] = (None, None)
  launch_config: dict | None = None
  milestones: dict[str, list] | None = None
  if job is not None:
    window = jobs.window(job)
    launch_config, milestones = _read_logs(
        LogMilestones(session, project, max_pages=log_pages), job_id, window,
        notes)
  job_params = jobs.params(job) if job is not None else {}
  known = launch_config or job_params or manual_params
  resolved = (launch_config or {}).get("resolved") or {}
  targets = [
      _dotted(str(t)) for t in resolved.get("tables_in_order") or
      str(known.get("landing_table") or "").split(",") if str(t).strip()
  ] or list(manual_fields.get("tables_in_order") or ())
  writes = _read_writes(bq, job_id, window, targets, notes)
  # No notes here: `LaunchContext.from_sources` (`_order`) warns once.
  side = _side_tables(known, [w.table for w in writes])
  candidates = list(
      dict.fromkeys(targets + [w.table for w in writes if w.table not in side]))
  base = _text(known.get("run_id")) or manual_fields.get("base_run_id")
  runs = _read_runs(bq, known, candidates, window, base, notes)
  try:
    return LaunchContext.from_sources(
        job=job if job is not None else {"id": job_id},
        launch_config=launch_config,
        writes=writes,
        manual=manual,
        runs=runs,
        milestones=milestones,
        warnings=notes)
  except UnresolvedLaunchError as exc:
    if missing is None:
      raise
    raise JobNotFoundError(
        f"Dataflow job {job_id} was not found in project {project}, region "
        f"{region}, and neither BigQuery job labels, validation_runs nor "
        f"manual input resolved its tables. {JOB_NOT_FOUND_HINT}") from exc
