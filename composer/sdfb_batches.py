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
"""The generation DAG's launch bodies, for both branches (ADR 0043, 0045).

The Composer DAGs cannot import sdfb-core (the Composer environment has
neither it nor pydantic), so this module does, with the standard library and
PyYAML only, what the DAG needs:

    load_layers        the DAG folder's generator.yaml (the defaults) and
                       sdfb_env.yaml (the env overlay workflow 3 fills), and
                       the yaml's digest (sdfb_core.config.defaults' rule)
    param_defaults     each DAG param's default: generator.yaml overlaid
                       with the overlay's `config:` section
    launch_overrides   the template parameters of a launch: only the keys
                       whose value differs from generator.yaml, plus the
                       launch identity and the yaml's digest
    explicit_body      the explicit branch's Flex launch body (master's
                       `start_sdfb`)
    evaluation_body    the chained evaluation's launch body
    resolve_tables_mode  which branch runs: a config file's launch.tables
                       wins over the param
    plan_batches       the batches of sdfb_core.launch.batches
                       (first-fit-decreasing over whole components)
    planning_inputs    the model file and K the batch launchers will plan
                       with: a config file's keys win over the DAG params,
                       as they do in the launcher (ADR 0043)
    whole_model_params the `all` branch's params: an empty source or
                       landing dataset from the overlay's default tables
    launch_plan        the `all` branch's batch launch bodies

Precedence: generator.yaml < sdfb_env.yaml < the trigger's values < a
config_uri file. A key the launch does not pass takes the image's
generator.yaml value, so the launch also passes the digest of the DAG's copy
and the launcher warns (`generator_defaults_mismatch`) when the two differ.
Parity tests pin `cli_text` to sdfb-core's text of every yaml value, the
digest to `defaults_digest`, an explicit launch to master's recorded launch
(packages/sdfb-tests/tests/unit/composer/test_launch_helpers.py), and
`plan_batches` to sdfb-core's on every committed model and on generated
forests of up to 120 tables (test_sdfb_batches.py).

The launcher stays authoritative: every batch job plans again from the same
model and K and stops when its count differs from `batch_count`. The planner
reads each table's `enabled` flag and its FK refs, nothing else. It refuses
the refs sdfb-core refuses at load (an edge to the table itself, a bare ref
the model does not declare), so a typo stops `route` rather than every
batch's Flex launch; the launcher validates the rest of the model when a
batch job starts.

The DAGs folder imports this file beside the DAG. It never names the
scheduler: a file in that folder that names it and says "dag" is parsed as
a DAG file.

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
# No `from __future__ import annotations`: string annotations make
# @dataclass look this module up in sys.modules, where a loader from the
# file (the tests, an exporter) has not put it.
import hashlib
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import yaml

__all__ = [
    "DEFAULT_MAX_TABLES_PER_JOB", "L4_ACCELERATOR", "T4_ACCELERATOR", "Layers",
    "PlanError", "TablesMode", "batch_job_name", "batch_parameters", "cli_text",
    "common_parameters", "dag_constants", "evaluation_body", "explicit_body",
    "landed_job_ids", "launch_environment", "launch_overrides", "launch_plan",
    "load_layers", "model_slug", "param_defaults", "plan_banner",
    "plan_batches", "planning_inputs", "resolve_tables_mode",
    "whole_model_params"
]

DEFAULT_MAX_TABLES_PER_JOB = 10
_JOB_NAME_MAX = 63
_MODEL_SUFFIXES = (".yaml", ".yml")
# Pydantic's lax bool vocabulary: the one the generator's model parser
# applies to `enabled:`.
_BOOL_TRUE = frozenset({"true", "yes", "on", "y", "t", "1"})
_BOOL_FALSE = frozenset({"false", "no", "off", "n", "f", "0"})
# The generation DAG's GPU experiments (its `additionalExperiments`).
L4_ACCELERATOR = (
    "worker_accelerator=type:nvidia-l4;count:1;install-nvidia-driver")
T4_ACCELERATOR = ("worker_accelerator=type:nvidia-tesla-t4;count:1;"
                  "install-nvidia-driver:5xx")
# The DAG folder's files (deploy workflow 3 copies generator.yaml beside the
# DAG and fills the overlay's markers).
GENERATOR_YAML = "generator.yaml"
ENV_OVERLAY = "sdfb_env.yaml"
_CONFIG_VERSION = 1
_OVERLAY_SECTIONS = ("dag", "infra", "config")
_DIGEST_HEX = 12
# The DAG params that set the generator.yaml key of the same name; the
# launcher takes each as the flag of that name.
GENERATOR_PARAMS = ("num_rows", "engine", "batch_size", "similarity",
                    "identity_cols", "pk_cols", "vllm_dtype",
                    "vllm_max_model_len", "client_type", "gpu", "seed",
                    "write_disposition", "create_if_not_exists",
                    "build_rag_layer", "rag_row_docs_limit", "build_pool_layer",
                    "sdk_containers", "initial_workers", "autoscaling",
                    "launcher_machine_type", "uniqueness_mode",
                    "freetext_expansion", "prompt_constraints", "prompt_debug",
                    "source_stats", "fk_parent_landing", "multi_table_mode",
                    "generate_fk_relationships", "pool_seed_strategy",
                    "relationships_uri", "source_dataset", "landing_dataset",
                    "tables", "max_tables_per_job", "prep_workers")
# The DAG param named otherwise than its generator.yaml key.
_TABLE_PARAM = "table_fqn"
_PARAM_KEYS = {_TABLE_PARAM: "reference_table"}
# The DAG's own params (no generator.yaml key) and their defaults, but
# evaluation_output_dataset, which is the validation-runs table's dataset.
_DAG_PARAM_DEFAULTS: dict[str, Any] = {
    "config_uri": "",
    "run_evaluation": True,
    "evaluation_mode": "",
    "evaluation_machine_type": "e2-standard-8",
    "evaluation_launcher_machine_type": "e2-standard-8",
    "evaluation_max_workers": 8,
}
_EVALUATION_OUTPUT_DATASET = "synthetic_data_quality"
# Keys a launch mode does not read (the launcher's launch_option_unused):
# never passed in that mode. Under all the model names every table and each
# schema is extracted live; an explicit launch names its tables, and its
# source_dataset param only tells the evaluation where the sources are.
_UNREAD_UNDER_ALL = frozenset({"reference_table", "landing_table", "ddl_uri"})
_UNREAD_UNDER_EXPLICIT = frozenset({"source_dataset", "landing_dataset"})
_TABLES_ALL = "all"
_TABLES_MODES = ("explicit", _TABLES_ALL)
# A launch's branch: what resolve_tables_mode returns.
TablesMode = Literal["explicit", "all"]
_QUALIFIED_TABLE_PARTS = 3  # project, dataset, table
# Names the DAG gives its jobs and itself (master's generation DAG).
_APP_DOMAIN, _APP_NAME = "synthetic", "sdfb"
_EVALUATION_NAME_MAX = 38
# The chained evaluation's constants (master's generation DAG): the workers'
# boot disk for the generation's multi-GB image, and the trigger the failure
# callback finds the evaluation's RUNNING row by.
EVALUATION_WORKER_DISK_GB = "200"
EVALUATION_TRIGGER = "chained"


class PlanError(ValueError):
  """The DAG cannot plan this launch; the message says what to change."""


@dataclass(frozen=True)
class Layers:
  """The DAG folder's defaults: `defaults` is generator.yaml as
  `{section: {key: value}}` (config_version checked and left out),
  `overlay` the env overlay (`dag`, `infra`, `config`), `digest` the
  generator.yaml digest (the first 12 hex of the sha256 of its bytes)."""
  defaults: dict[str, dict[str, Any]]
  overlay: dict[str, dict[str, Any]]
  digest: str


def _mapping(value: Any, where: str) -> dict[str, Any]:
  if value is None:
    return {}
  if not isinstance(value, Mapping):
    raise ValueError(f"{where} must be a mapping, got {type(value).__name__}")
  return {str(k): v for k, v in value.items()}


def load_layers(folder: str) -> Layers:
  """generator.yaml and sdfb_env.yaml of the DAG folder `folder`.

  The overlay's `config:` may only name keys generator.yaml has: a typo
  would otherwise never reach a launch."""
  with open(os.path.join(folder, GENERATOR_YAML), "rb") as handle:
    raw = handle.read()
  data = _mapping(yaml.safe_load(raw), GENERATOR_YAML)
  version = data.pop("config_version", None)
  if version != _CONFIG_VERSION or isinstance(version, bool):
    raise ValueError(f"{GENERATOR_YAML}: config_version must be "
                     f"{_CONFIG_VERSION}, got {version!r}")
  defaults = {
      section: _mapping(values, f"{GENERATOR_YAML}: section '{section}'")
      for section, values in data.items()
  }
  with open(os.path.join(folder, ENV_OVERLAY), encoding="utf-8") as handle:
    loaded = _mapping(yaml.safe_load(handle), ENV_OVERLAY)
  unknown = sorted(set(loaded) - set(_OVERLAY_SECTIONS))
  if unknown:
    raise ValueError(f"{ENV_OVERLAY}: unknown sections {unknown}; expected "
                     f"{list(_OVERLAY_SECTIONS)}")
  overlay = {
      name: _mapping(loaded.get(name), f"{ENV_OVERLAY}: {name}")
      for name in _OVERLAY_SECTIONS
  }
  config: dict[str, Any] = {}
  for section, values in overlay["config"].items():
    keys = _mapping(values, f"{ENV_OVERLAY}: config.{section}")
    missing = sorted(
        f"{section}.{k}" for k in keys if k not in defaults.get(section, {}))
    if missing:
      names = ", ".join(missing)
      raise ValueError(f"{ENV_OVERLAY}: {names} is no "
                       f"{GENERATOR_YAML} key")
    config[section] = keys
  overlay["config"] = config
  return Layers(
      defaults=defaults,
      overlay=overlay,
      digest=hashlib.sha256(raw).hexdigest()[:_DIGEST_HEX])


def cli_text(value: object) -> str:
  """A yaml value as the launcher's flag text, as sdfb-core's config model
  reads it: null is unset (empty), a boolean true/false, a list
  comma-separated, anything else its str()."""
  if value is None:
    return ""
  if isinstance(value, bool):
    return "true" if value else "false"
  if isinstance(value, (list, tuple)):
    return ",".join(str(v) for v in value)
  return str(value)


def _overlaid(layers: Layers) -> dict[str, Any]:
  """key -> value: generator.yaml with the overlay's `config:` over it."""
  out: dict[str, Any] = {}
  for section, values in layers.defaults.items():
    out.update(values)
    out.update(layers.overlay["config"].get(section, {}))
  return out


def _yaml_defaults(layers: Layers) -> dict[str, Any]:
  return {
      k: v for values in layers.defaults.values() for k, v in values.items()
  }


def _dataset(fqn: str, default: str = "") -> str:
  """The dataset of `project.dataset.table`, else `default`."""
  parts = fqn.split(".")
  return parts[1] if len(parts) == _QUALIFIED_TABLE_PARTS else default


def param_defaults(layers: Layers) -> dict[str, object]:
  """Every DAG param's default. A generator param is generator.yaml
  overlaid with the overlay's `config:`, as text (null is empty); the DAG's
  own params keep theirs."""
  values = _overlaid(layers)
  out: dict[str, object] = {
      _TABLE_PARAM: cli_text(values[_PARAM_KEYS[_TABLE_PARAM]])
  }
  for name in GENERATOR_PARAMS:
    out[name] = cli_text(values[name])
  out.update(_DAG_PARAM_DEFAULTS)
  out["evaluation_output_dataset"] = _dataset(
      cli_text(values["validation_runs_table"]), _EVALUATION_OUTPUT_DATASET)
  return out


def launch_overrides(params: Mapping[str, object],
                     layers: Layers) -> dict[str, str]:
  """A launch's template parameters: each key whose text differs from
  generator.yaml's (`cli_text`), whether the overlay or the trigger set it,
  plus `run_id`, `config_uri` when set and the yaml's digest.

  The keys generator.yaml leaves unset (reference_table, landing_table,
  dlq_table, model_uri) differ whenever they are set, so a launch passes
  them; a key the trigger sets back to generator.yaml's value is not passed,
  even where the overlay set another, since the launcher's default is that
  value. The keys the launch's mode does not read are left out
  (`_UNREAD_UNDER_ALL`, `_UNREAD_UNDER_EXPLICIT`)."""
  values = _overlaid(layers)
  for name, value in params.items():
    key = _PARAM_KEYS.get(name, name)
    if name in GENERATOR_PARAMS or name in _PARAM_KEYS:
      values[key] = value
  unread = (
      _UNREAD_UNDER_ALL
      if cli_text(values["tables"]) == _TABLES_ALL else _UNREAD_UNDER_EXPLICIT)
  yaml_values = _yaml_defaults(layers)
  out: dict[str, str] = {}
  for key, value in values.items():
    if key in unread:
      continue
    if cli_text(value) != cli_text(yaml_values[key]):
      out[key] = cli_text(value)
  run_id = cli_text(params.get("run_id")).strip()
  if run_id:
    out["run_id"] = run_id
  config_uri = cli_text(params.get("config_uri")).strip()
  if config_uri:
    out["config_uri"] = config_uri
  out["generator_defaults_digest"] = layers.digest
  return out


def resolve_tables_mode(params: Mapping[str, object],
                        config_text: str | None) -> TablesMode:
  """The branch a launch takes: the config file's launch.tables when it sets
  one (a file's keys win, ADR 0043), else the `tables` param.

  The file is read as the launcher reads it (sdfb_core.config.resolve): an
  empty file or a `launch:` left empty sets nothing; a `launch:` that is no
  mapping, and a launch.tables that is neither explicit nor all (null
  included: its generator.yaml default is not null, so the launcher refuses
  it rather than reading it as unset), are refused. Every refusal is a
  PlanError: the same file fails the same way on every retry."""
  try:
    data = yaml.safe_load(config_text) if config_text else None
  except yaml.YAMLError as e:
    raise PlanError(f"config_uri: not valid YAML — {e}") from e
  data = {} if data is None else data
  if not isinstance(data, Mapping):
    raise PlanError("config_uri: the top level must be a mapping of sections")
  launch = data.get("launch")
  if launch is None:
    launch = {}
  if not isinstance(launch, Mapping):
    raise PlanError(f"config_uri: section 'launch' must be a mapping, got "
                    f"{launch!r}")
  if "tables" in launch:
    mode, where = launch["tables"], "config_uri's launch.tables"
  else:
    mode, where = params.get("tables", "explicit"), "the tables param"
  if mode == "explicit":
    return "explicit"
  if mode == _TABLES_ALL:
    return "all"
  raise PlanError(f"{where} must be one of {list(_TABLES_MODES)}, got "
                  f"{mode!r}")


def _version_slug(version: str) -> str:
  """A version as Dataflow job names allow it (lower case, digits, -)."""
  return re.sub(r"[^a-z0-9]+", "-", version.lower()).strip("-")


def dag_constants(layers: Layers, variables: Mapping[str,
                                                     str]) -> dict[str, Any]:
  """What the launch bodies take from the overlay and the Composer
  Variables (PROJECT_ID, REGION, DATAFLOW_SUBNET, SA_DATAFLOW and the
  optional DATAFLOW_NETWORK_TAGS): `launch_environment`'s and `launch_plan`'s
  `constants`, the DAG's id and its jobs' names."""
  dag, infra, values = layers.overlay["dag"], layers.overlay["infra"], (
      _overlaid(layers))
  templates = cli_text(infra["templates_bucket"])
  version = cli_text(dag["project_version"])
  dag_version = cli_text(dag["dag_version"])
  dag_id = f"{_APP_DOMAIN}_{_APP_NAME}_{dag_version}"
  base = f"{_APP_DOMAIN}-{_APP_NAME}-v{_version_slug(version)}"
  slug = model_slug(cli_text(values["model_uri"]))
  tags = str(variables.get("DATAFLOW_NETWORK_TAGS", "") or "").strip()
  evaluation = f"{_APP_NAME}-evaluation-v{_version_slug(version)}"
  return {
      "project_id":
          variables["PROJECT_ID"],
      "region":
          variables["REGION"],
      "subnetwork":
          variables["DATAFLOW_SUBNET"],
      "service_account":
          variables["SA_DATAFLOW"],
      "network_tag_experiments": [
          f"use_network_tags={tags}",
          f"use_network_tags_for_flex_templates={tags}",
      ] if tags else [],
      "bucket_path":
          cli_text(infra["staging_bucket"]),
      "container_spec_gcs_path":
          f"gs://{templates}/synthetic/"
          f"sdfb-{version}-template.json",
      "dag_id":
          dag_id,
      "labels": {
          "app": _APP_NAME,
          "env": cli_text(values["env"]),
          "dag": dag_id
      },
      "job_name":
          (f"{base}-{slug}"[:_JOB_NAME_MAX].rstrip("-") if slug else base),
      "evaluation_job_name":
          evaluation[:_EVALUATION_NAME_MAX].rstrip("-"),
  }


def explicit_body(params: Mapping[str, object], layers: Layers, *,
                  job_name: str, variables: Mapping[str,
                                                    str]) -> dict[str, Any]:
  """The explicit branch's Flex launch body (master's `start_sdfb`): the
  launch's environment, and the parameters `launch_overrides` passes under
  launch.tables explicit (the branch `resolve_tables_mode` chose, whatever
  the `tables` param says). `params` holds the trigger's resolved values
  and the salted `run_id`; `variables` the Composer Variables
  (`dag_constants`)."""
  constants = dag_constants(layers, variables)
  return {
      "launchParameter": {
          "containerSpecGcsPath":
              constants["container_spec_gcs_path"],
          "jobName":
              job_name,
          "environment":
              launch_environment(params, constants=constants),
          "parameters":
              launch_overrides({
                  **params, "tables": "explicit"
              }, layers),
      }
  }


def _evaluation_parameters(params: Mapping[str, object], layers: Layers,
                           generation_job_ids: list[str] | None,
                           mode: TablesMode) -> dict:
  """The evaluator's template parameters (docker/flex_template_metadata.json
  names). Explicit: master's, the launched table its seed. All: the launch's
  base run id and the batch jobs that landed."""
  values = _overlaid(layers)
  common = {
      "scope": "manual",
      "trigger": EVALUATION_TRIGGER,
      "mode": cli_text(params["evaluation_mode"]),
      "output_dataset": cli_text(params["evaluation_output_dataset"]),
      "disk_size_gb": EVALUATION_WORKER_DISK_GB,
  }
  relationships_uri = (
      cli_text(params["relationships_uri"])
      if cli_text(params["generate_fk_relationships"]) == "true" else "")
  if mode not in _TABLES_MODES:
    raise ValueError(f"mode must be one of {list(_TABLES_MODES)}, got "
                     f"{mode!r}")
  if mode == _TABLES_ALL:
    out = {
        "sdfb_job": "evaluation",
        "run_id": cli_text(params["run_id"]),
    }
    if generation_job_ids:
      out["generation_job_ids"] = ",".join(generation_job_ids)
    return {**out, "relationships_uri": relationships_uri, **common}
  if not generation_job_ids or len(generation_job_ids) != 1:
    raise ValueError(f"an explicit launch is one generation job; got "
                     f"generation_job_ids={generation_job_ids!r}")
  table = cli_text(params[_TABLE_PARAM])
  source_dataset = cli_text(params["source_dataset"])
  return {
      "sdfb_job": "evaluation",
      "generation_job_id": generation_job_ids[0],
      "seed_table": table.rsplit(".", 1)[-1],
      "relationships_uri": relationships_uri,
      "landing_dataset": cli_text(values["landing_table"]).rsplit(".", 1)[0],
      "reference_dataset": source_dataset or table.rsplit(".", 1)[0],
      "reference_rows_limit": cli_text(values["reference_rows_limit"]),
      "validation_runs_table": cli_text(values["validation_runs_table"]),
      **common,
  }


def evaluation_body(params: Mapping[str, object], layers: Layers, *,
                    mode: TablesMode, generation_job_ids: list[str] | None,
                    variables: Mapping[str,
                                       str], ts_nodash: str) -> dict[str, Any]:
  """The chained evaluation's Flex launch body: a CPU-only job from the
  generation's template, master's for an explicit launch.

  `mode` is the branch `resolve_tables_mode` chose (a config file may
  have switched the `tables` param's), and picks the shape. Explicit:
  `generation_job_ids` is the one generation job. All: the `run_id` param
  is the launch's base run id, the `relationships_uri` param the planned
  model, and `generation_job_ids` the batch jobs that landed (None: every
  row of the launch). The job is named after the DAG
  run's `ts_nodash`; `variables` are the Composer Variables
  (`dag_constants`)."""
  constants = dag_constants(layers, variables)
  bucket, name = constants["bucket_path"], constants["evaluation_job_name"]
  return {
      "launchParameter": {
          "containerSpecGcsPath":
              constants["container_spec_gcs_path"],
          "jobName":
              f"{name}-{ts_nodash.lower()}",
          "environment": {
              "tempLocation":
                  f"gs://{bucket}/temp/",
              "stagingLocation":
                  f"gs://{bucket}/staging",
              "subnetwork":
                  constants["subnetwork"],
              "ipConfiguration":
                  "WORKER_IP_PRIVATE",
              "serviceAccountEmail":
                  constants["service_account"],
              "additionalExperiments": [
                  "use_runner_v2", "enable_secure_boot",
                  *constants["network_tag_experiments"]
              ],
              "additionalUserLabels":
                  dict(constants["labels"]),
              "launcherMachineType":
                  cli_text(params["evaluation_launcher_machine_type"]),
              "machineType":
                  cli_text(params["evaluation_machine_type"]),
              "maxWorkers":
                  cli_text(params["evaluation_max_workers"]),
              "workerRegion":
                  constants["region"],
          },
          "parameters":
              _evaluation_parameters(params, layers, generation_job_ids, mode),
      }
  }


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


def _check_refs(model: Any, refs: Mapping[str, Sequence[str]]) -> None:
  """The refs sdfb-core refuses when it loads the model
  (`contracts/relationships.py::_validate_refs`), refused in its words, less
  its file-name prefix: an edge to the table itself, and a bare ref that
  names no table of the model. The other load checks read what the planner
  does not (columns, `enforced`), so the launcher still makes them."""
  known = sorted(refs)
  for table, names in refs.items():
    for ref in names:
      if ref == table or ref.rsplit(".", 1)[-1] == table:
        raise PlanError(f"{table} references itself ({ref}) — an FK edge "
                        f"needs two tables")
      if "." not in ref and ref not in refs:
        raise PlanError(f"{table}'s fk.ref {ref!r} does not name a table in "
                        f"model {model!r} ({known}). Qualify it as "
                        f"dataset.table if the parent lives outside this "
                        f"model.")


def _tables(model_text: str) -> dict[str, tuple[bool, tuple[str, ...]]]:
  """table → (enabled, the in-model tables its FK edges name), in file
  order. An edge whose ref is dotted names another dataset and is left out;
  a ref the core refuses at load is refused (`_check_refs`)."""
  raw = yaml.safe_load(model_text) or {}
  if not isinstance(raw, Mapping):
    raise PlanError("the model file's top level must be a mapping")
  tables = raw.get("tables") or {}
  if not isinstance(tables, Mapping):
    raise PlanError("the model file's `tables:` must be a mapping")
  enabled: dict[str, bool] = {}
  refs: dict[str, list[str]] = {}
  for name, spec in tables.items():
    if not isinstance(spec, Mapping):
      raise PlanError(f"tables.{name}: expected a mapping, got "
                      f"{type(spec).__name__}")
    refs[str(name)] = []
    for edge in spec.get("fk") or ():
      ref = edge.get("ref") if isinstance(edge, Mapping) else None
      if isinstance(ref, str):
        refs[str(name)].append(ref)
    enabled[str(name)] = _enabled(spec.get("enabled", True), f"tables.{name}")
  # After every table's shape, as the core validates the refs after it.
  _check_refs(raw.get("model"), refs)
  return {
      name: (on, tuple(r for r in refs[name] if "." not in r))
      for name, on in enabled.items()
  }


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
        f"config_uri sets launch.tables to {tables!r}; a whole-model launch "
        f"launches every enabled table (all). Remove the key, or set "
        f"launch.tables to explicit for one table")
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


def whole_model_params(params: Mapping[str, Any],
                       layers: Layers) -> dict[str, Any]:
  """`params` for a `tables=all` launch: an empty `source_dataset` or
  `landing_dataset` takes the `project.dataset` of the overlay's
  io.reference_table or io.landing_table (the deployment's default source
  table and landing table), as the launcher refuses `tables=all` without
  them; a value the trigger set is kept. A config file that sets either
  still wins in the launcher (ADR 0043)."""
  values = _overlaid(layers)
  out = dict(params)
  for param, table in (("source_dataset", "reference_table"),
                       ("landing_dataset", "landing_table")):
    if not cli_text(out.get(param)).strip():
      parts = cli_text(values[table]).split(".")
      out[param] = (".".join(parts[:2])
                    if len(parts) == _QUALIFIED_TABLE_PARTS else "")
  return out


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
  characters: parallel jobs need distinct names. The launcher names a CLI
  batch job by the same rule (`run_pipeline.batch_job_name`, pinned equal by
  test)."""
  suffix = f"-b{index:02d}"
  return job_name[:_JOB_NAME_MAX - len(suffix)].rstrip("-") + suffix


def launch_environment(params: Mapping[str, Any], *,
                       constants: Mapping[str, Any]) -> dict[str, Any]:
  """A generation job's Flex Template `environment`: master's generation
  DAG's, its Jinja conditionals resolved here (the GPU, the SDK containers,
  the machines). Each conditional experiment is `upload_graph` when it does
  not apply, as master renders it (Dataflow drops the duplicate)."""
  vllm = params["client_type"] == "vllm"
  l4 = params["gpu"] == "l4"
  single = vllm and params["sdk_containers"] == "single"
  experiments = [
      "use_runner_v2",
      "upload_graph",
      "enable_secure_boot",
      (L4_ACCELERATOR if l4 else T4_ACCELERATOR) if vllm else "upload_graph",
      "automatically_use_created_reservation" if vllm else "upload_graph",
      "no_use_multiple_sdk_containers" if single else "upload_graph",
      *constants["network_tag_experiments"],
  ]
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
                      layers: Layers) -> dict[str, str]:
  """The template parameters every batch passes: `launch_overrides` under
  launch.tables all (only the keys that differ from generator.yaml, the
  keys that mode does not read left out); `batch_parameters` adds the
  batch's own and the run id."""
  out = launch_overrides({**params, "tables": _TABLES_ALL}, layers)
  out.pop("run_id", None)
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
                base_run_id: str, constants: Mapping[str, Any],
                layers: Layers) -> dict[str, Any]:
  """Everything the `all` branch plans: the batches, one Flex Template
  launch body per batch, and the banner. Plain JSON (an XCom). `constants`
  is `dag_constants`' (the environment, the template, the job name)."""
  batches = plan_batches(model_text, max_tables_per_job)
  environment = launch_environment(params, constants=constants)
  common = common_parameters(params, layers=layers)
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
