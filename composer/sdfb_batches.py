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
"""Planning a whole-model launch for the Composer model DAG (ADR 0045).

`composer/synthetic_model_launch.py` cannot import sdfb-core (the Composer
environment has neither it nor pydantic), so this module repeats, with the
standard library and PyYAML only, what that DAG needs from it:

    plan_batches       the batches of sdfb_core.launch.batches
                       (first-fit-decreasing over whole components)
    planning_inputs    the model file and K the batch launchers will plan
                       with: a config file's keys win over the DAG params,
                       as they do in the launcher (ADR 0043)

and builds each batch's Flex Template launch body. The launcher stays
authoritative: every batch job plans again from the same model and K and
stops when its count differs from `batch_count`. A parity test pins
`plan_batches` to sdfb-core's on every committed model and on generated
forests of up to 120 tables
(packages/sdfb-tests/tests/unit/composer/test_sdfb_batches.py).

The planner reads each table's `enabled` flag and its FK refs, nothing else:
the launcher validates the rest of the model when a batch job starts.

The DAGs folder imports this file beside the DAG. It never names the
scheduler: a file in that folder that names it and says "dag" is parsed as
a DAG file.

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import yaml

__all__ = [
    "DEFAULT_MAX_TABLES_PER_JOB", "L4_ACCELERATOR", "T4_ACCELERATOR",
    "PlanError", "batch_job_name", "batch_parameters", "common_parameters",
    "landed_job_ids", "launch_environment", "launch_plan", "model_slug",
    "plan_banner", "plan_batches", "planning_inputs"
]

DEFAULT_MAX_TABLES_PER_JOB = 10
_JOB_NAME_MAX = 63
_MODEL_SUFFIXES = (".yaml", ".yml")
# Pydantic's lax bool vocabulary: the one the generator's model parser
# applies to `enabled:`.
_BOOL_TRUE = frozenset({"true", "yes", "on", "y", "t", "1"})
_BOOL_FALSE = frozenset({"false", "no", "off", "n", "f", "0"})
# The single-table DAG's GPU experiments (its `additionalExperiments`).
L4_ACCELERATOR = (
    "worker_accelerator=type:nvidia-l4;count:1;install-nvidia-driver")
T4_ACCELERATOR = ("worker_accelerator=type:nvidia-tesla-t4;count:1;"
                  "install-nvidia-driver:5xx")
# What the single-table DAG passes as reference_rows_limit; a config file's
# sampling.reference_rows_limit wins in the launcher.
_REFERENCE_ROWS_LIMIT = "10000"
# DAG params a batch forwards as typed: the single-table DAG's forwarded
# params without the ones a whole-model launch takes from the model
# (reference_table, landing_table, pk_cols, identity_cols) and the DDL pin,
# plus the whole-model keys.
_FORWARDED = ("source_stats", "freetext_expansion", "prompt_constraints",
              "prompt_debug", "fk_parent_landing", "generate_fk_relationships",
              "relationships_uri", "config_uri", "multi_table_mode",
              "uniqueness_mode", "pool_seed_strategy", "rag_row_docs_limit",
              "num_rows", "initial_workers", "autoscaling", "batch_size",
              "similarity", "engine", "client_type", "vllm_dtype",
              "vllm_max_model_len", "seed", "write_disposition",
              "create_if_not_exists", "source_dataset", "landing_dataset",
              "max_tables_per_job", "prep_workers")


class PlanError(ValueError):
  """The model DAG cannot plan this launch; the message says what to change."""


def _enabled(value: Any, where: str) -> bool:
  """`enabled:` read the way Pydantic's lax bool reads it."""
  if isinstance(value, bool):
    return value
  if isinstance(value, int) and value in (0, 1):
    return bool(value)
  if isinstance(value, float) and value in (0.0, 1.0):
    return bool(value)
  if isinstance(value, str):
    folded = value.casefold()
    if folded in _BOOL_TRUE:
      return True
    if folded in _BOOL_FALSE:
      return False
  raise PlanError(f"{where}: enabled must be a boolean, got {value!r}")


def _positive_int(value: Any, key: str) -> int:
  """A count from a DAG param (text) or a config file (an int)."""
  if isinstance(value, bool):
    raise PlanError(f"{key} must be a positive integer, got {value!r}")
  if isinstance(value, int):
    number = value
  elif isinstance(value, str) and value.strip().isdigit():
    number = int(value.strip())
  else:
    raise PlanError(f"{key} must be a positive integer, got {value!r}")
  if number < 1:
    raise PlanError(f"{key} must be a positive integer, got {value!r}")
  return number


def _tables(model_text: str) -> dict[str, tuple[bool, tuple[str, ...]]]:
  """table → (enabled, the in-model tables its FK edges name), in file
  order. An edge whose ref is dotted names another dataset and is left out."""
  raw = yaml.safe_load(model_text) or {}
  if not isinstance(raw, Mapping):
    raise PlanError("the model file's top level must be a mapping")
  tables = raw.get("tables") or {}
  if not isinstance(tables, Mapping):
    raise PlanError("the model file's `tables:` must be a mapping")
  out: dict[str, tuple[bool, tuple[str, ...]]] = {}
  for name, spec in tables.items():
    if not isinstance(spec, Mapping):
      raise PlanError(f"tables.{name}: expected a mapping, got "
                      f"{type(spec).__name__}")
    refs = []
    for edge in spec.get("fk") or ():
      ref = edge.get("ref") if isinstance(edge, Mapping) else None
      if isinstance(ref, str) and "." not in ref:  # in-model, not external
        refs.append(ref)
    out[str(name)] = (_enabled(spec.get("enabled", True),
                               f"tables.{name}"), tuple(refs))
  return out


def _components(
    tables: Mapping[str, tuple[bool, tuple[str,
                                           ...]]]) -> list[tuple[str, ...]]:
  """The enabled tables' components, walked as RelationshipRegistry.
  component walks them: undirected, over every in-model edge between two
  enabled tables (enforced or documented)."""
  adjacent: dict[str, set[str]] = {name: set() for name in tables}
  for name, (enabled, refs) in tables.items():
    if not enabled:
      continue
    for ref in refs:
      if ref in tables and tables[ref][0]:
        adjacent[name].add(ref)
        adjacent[ref].add(name)
  seen: set[str] = set()
  out: list[tuple[str, ...]] = []
  for name, (enabled, _) in tables.items():
    if not enabled or name in seen:
      continue
    component, frontier = {name}, [name]
    while frontier:
      reached = []
      for current in frontier:
        for neighbour in adjacent[current]:
          if neighbour not in component:
            component.add(neighbour)
            reached.append(neighbour)
      frontier = reached
    seen |= component
    out.append(tuple(sorted(component)))
  return out


def plan_batches(model_text: str,
                 max_tables_per_job: int | str) -> list[dict[str, Any]]:
  """The batches sdfb-core plans for this model and K:
  `[{"index", "label", "tables", "oversized"}]`, tables sorted."""
  k = _positive_int(max_tables_per_job, "max_tables_per_job")
  components = sorted(
      _components(_tables(model_text)), key=lambda c: (-len(c), c[0]))
  if not components:
    raise PlanError("the model enables no table — nothing to generate")
  bins: list[list[tuple[str, ...]]] = []
  for component in components:
    for members in bins:
      if sum(len(c) for c in members) + len(component) <= k:
        members.append(component)
        break
    else:
      bins.append([component])
  bins.sort(key=lambda members: min(t for c in members for t in c))
  return [{
      "index": index,
      "label": f"b{index:02d}",
      "tables": sorted(t for c in members for t in c),
      "oversized": sum(len(c) for c in members) > k,
  } for index, members in enumerate(bins)]


def planning_inputs(params: Mapping[str, Any],
                    config_text: str | None) -> tuple[str, int]:
  """(the model file, K) the batch launchers will plan with: a key the
  config file sets wins over the DAG param, as in the launcher (ADR 0043)."""
  data = yaml.safe_load(config_text) if config_text else None
  data = {} if data is None else data
  if not isinstance(data, Mapping):
    raise PlanError("config_uri: the top level must be a mapping of sections")
  launch = data.get("launch") or {}
  relational = data.get("relational") or {}
  if not isinstance(launch, Mapping) or not isinstance(relational, Mapping):
    raise PlanError("config_uri: `launch` and `relational` must be mappings")
  tables = launch.get("tables", "all")
  if tables != "all":
    raise PlanError(
        f"config_uri sets launch.tables to {tables!r}; the model DAG "
        f"launches every enabled table (all). Remove the key, or launch the "
        f"table from the single-table DAG")
  uri = relational.get("relationships_uri")
  if uri is None:
    uri = params.get("relationships_uri")
  uri = str(uri or "").strip()
  if not (uri.startswith("gs://") and
          uri.rstrip("/").endswith(_MODEL_SUFFIXES)):
    raise PlanError(
        f"relationships_uri must name ONE model file on GCS "
        f"(gs://<bucket>/<path>/<model>.yaml) for a whole-model launch, got "
        f"{uri!r}")
  k = launch.get("max_tables_per_job")
  if k is None:
    k = params.get("max_tables_per_job", DEFAULT_MAX_TABLES_PER_JOB)
  return uri, _positive_int(k, "max_tables_per_job")


def model_slug(uri: str, max_len: int = 24) -> str:
  """Job-name-safe model slug from the model URI — the generation DAG's
  `_model_slug`, copied (a test pins the two equal)."""
  if not uri.startswith("gs://") or "synthetic/models/" not in uri:
    return ""
  parts = [p for p in uri.split("synthetic/models/", 1)[1].split("/") if p]
  if parts and re.fullmatch(r"v\d+", parts[-1]):
    parts = parts[:-1]  # drop the weights version (…/v1/)
  slug = "-".join(parts[:2]).lower()  # {family}-{model}
  slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", slug))
  return slug.strip("-")[:max_len].rstrip("-")


def batch_job_name(job_name: str, index: int) -> str:
  """A batch job's Dataflow name, `<job_name>-bNN`, inside Dataflow's 63
  characters: parallel jobs need distinct names."""
  suffix = f"-b{index:02d}"
  return job_name[:_JOB_NAME_MAX - len(suffix)].rstrip("-") + suffix


def launch_environment(params: Mapping[str, Any], *,
                       constants: Mapping[str, Any]) -> dict[str, Any]:
  """A batch job's Flex Template `environment`: the single-table DAG's
  generation environment, its Jinja conditionals resolved here (the GPU,
  the SDK containers, the machines)."""
  vllm = params["client_type"] == "vllm"
  l4 = params["gpu"] == "l4"
  experiments = ["use_runner_v2", "upload_graph", "enable_secure_boot"]
  if vllm:
    experiments += [
        L4_ACCELERATOR if l4 else T4_ACCELERATOR,
        "automatically_use_created_reservation"
    ]
    if params["sdk_containers"] == "single":
      experiments.append("no_use_multiple_sdk_containers")
  experiments += list(constants["network_tag_experiments"])
  machine = (("g2-standard-8" if l4 else "n1-standard-8")
             if vllm else "e2-standard-8")
  bucket = constants["bucket_path"]
  return {
      "tempLocation": f"gs://{bucket}/temp/",
      "stagingLocation": f"gs://{bucket}/staging",
      "subnetwork": constants["subnetwork"],
      "ipConfiguration": "WORKER_IP_PRIVATE",
      "serviceAccountEmail": constants["service_account"],
      "additionalExperiments": experiments,
      "additionalUserLabels": dict(constants["labels"]),
      "launcherMachineType": str(params["launcher_machine_type"]),
      "machineType": machine,
      "maxWorkers": 4,
      "workerRegion": constants["region"],
  }


def common_parameters(params: Mapping[str, Any], *,
                      constants: Mapping[str, Any]) -> dict[str, str]:
  """The template parameters every batch passes (all text, as the Flex
  Template takes them); `batch_parameters` adds the batch's own."""
  out: dict[str, str] = {}
  if constants["rag_chunks_table"]:
    out["rag_chunks_table"] = constants["rag_chunks_table"]
    out["build_rag_layer"] = str(params["build_rag_layer"])
  if constants["freetext_pools_table"]:
    out["freetext_pools_table"] = constants["freetext_pools_table"]
    out["build_pool_layer"] = str(params["build_pool_layer"])
  if constants["source_stats_table"]:
    out["source_stats_table"] = constants["source_stats_table"]
  for name in _FORWARDED:
    out[name] = str(params[name])
  out.update({
      "tables": "all",
      "reference_rows_limit": _REFERENCE_ROWS_LIMIT,
      "dlq_table": constants["dlq_table"],
      "model_uri": constants["model_uri"],
      "embedder_uri": constants["embedder_uri"],
      "validation_runs_table": constants["validation_runs_table"],
      "env": constants["env"],
  })
  return out


def batch_parameters(common: Mapping[str, str], *, base_run_id: str, index: int,
                     count: int) -> dict[str, str]:
  """One batch's template parameters: the launch base as `run_id` (the
  launcher forms `<base>-bNN`) and the batch identity."""
  return {
      **common,
      "run_id": base_run_id,
      "batch_index": str(index),
      "batch_count": str(count),
  }


def plan_banner(batches: Sequence[Mapping[str, Any]],
                max_tables_per_job: int) -> str:
  """The plan as the `plan` task logs it."""
  lines = [
      f"MODEL LAUNCH PLAN | {len(batches)} batch job(s) of <= "
      f"{max_tables_per_job} tables"
  ]
  for batch in batches:
    flag = ("   [OVERSIZED — one component]" if batch["oversized"] else "")
    label, tables = batch["label"], batch["tables"]
    joined = ", ".join(tables)
    lines.append(f"  {label} | {len(tables):>3} tables | {joined}{flag}")
  return "\n".join(lines)


def launch_plan(params: Mapping[str, Any], *, model_text: str,
                max_tables_per_job: int, relationships_uri: str,
                base_run_id: str, constants: Mapping[str,
                                                     Any]) -> dict[str, Any]:
  """Everything the model DAG's `plan` task returns: the batches, one Flex
  Template launch body per batch, and the banner. Plain JSON (an XCom)."""
  batches = plan_batches(model_text, max_tables_per_job)
  environment = launch_environment(params, constants=constants)
  common = common_parameters(params, constants=constants)
  bodies = [{
      "launchParameter": {
          "containerSpecGcsPath":
              constants["container_spec_gcs_path"],
          "jobName":
              batch_job_name(constants["job_name"], batch["index"]),
          "environment":
              environment,
          "parameters":
              batch_parameters(
                  common,
                  base_run_id=base_run_id,
                  index=batch["index"],
                  count=len(batches)),
      }
  } for batch in batches]
  return {
      "base_run_id": base_run_id,
      "batch_count": len(batches),
      "relationships_uri": relationships_uri,
      "batches": batches,
      "bodies": bodies,
      "banner": plan_banner(batches, max_tables_per_job),
  }


def landed_job_ids(launched: Iterable[Any] | None) -> list[str]:
  """The Dataflow job ids of the batch launches that returned (a launch
  that failed returns nothing), in batch order, each once."""
  ids: list[str] = []
  for entry in launched or ():
    value = entry.get("id") if isinstance(entry, Mapping) else None
    if isinstance(value, str) and value and value not in ids:
      ids.append(value)
  return ids
