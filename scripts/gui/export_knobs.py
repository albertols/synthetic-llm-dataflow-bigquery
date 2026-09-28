#!/usr/bin/env python
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
"""Export the GUI's knob contract: every value IMPORTED from code.

    UV_PROJECT_ENVIRONMENT=.venv-gui uv run python scripts/gui/export_knobs.py
    UV_PROJECT_ENVIRONMENT=.venv-gui uv run python scripts/gui/export_knobs.py --check

Writes `gui/packages/contracts/generated/knobs.json`, which the Synthetic
Platform GUI (ADR 0042) renders as the CONFIG tab's knobs. Nothing here
retypes a number: CLI defaults come from `run_pipeline.parse_args` itself
(called with the minimal required flags), constants are imported from the
modules that own them, `thresholds.yml` is parsed, and every knob carries
the `path:line` of its definition, found by an AST (or text) search, plus
the ADRs its help text or comment block cites.

Three more sections:

- `annotations`: places where the docs and the code disagree. Each one is
  re-verified on every run against code anchors; an anchor that moved or
  vanished fails the export, so a stale "Docs differ" note cannot ship.
- `measured`: the `MEASURED` blocks of the throughput figure scripts, loaded
  with importlib, with the block header as provenance.
- the EVALUATION channel: read from `sdfb_evaluation/cli/main.py` by AST
  (never imported) when that file exists; until then the plan-documented
  knob list with `source: "planned"` (GUI Ruling G2).

`--check` exits 1 when the committed file differs from a fresh export.

Design: docs/DESIGN.md §12 Platform GUI
(ADR 0042).
"""

# Heavy or optional dependencies are imported lazily, where they are used.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import argparse
import ast
import functools
import importlib.util
import json
import math
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "gui" / "packages" / "contracts" / "generated" / "knobs.json"
EVAL_CLI = (
    REPO / "packages" / "sdfb-evaluation" / "src" / "sdfb_evaluation" / "cli" /
    "main.py")
_RUN_PIPELINE = REPO / "packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py"
_COMPOSER = REPO / "composer" / "synthetic_beam_bigquery.py"
_FLEX = REPO / "docker" / "flex_template_metadata.json"
_THRESHOLDS = REPO / "config" / "thresholds.yml"
_MEASURED_SCRIPTS = (
    REPO / "scripts" / "doc" / "make_throughput_figures.py",
    REPO / "scripts" / "doc" / "make_ws5_figures.py",
)
# The minimal flags `parse_args` insists on; their values never reach a knob.
_REQUIRED_ARGV = (
    "--reference_table",
    "project.dataset.source",
    "--landing_table",
    "project.dataset.landing",
    "--dlq_table",
    "project.dataset.dlq",
    "--num_rows",
    "1",
    "--run_id",
    "export-knobs",
    "--model_uri",
    "gs://bucket/synthetic/models/family/model/v1/",
)
# A 15-16 digit run the sensitive-content gate would Luhn-check
# (scripts/dsg/precheck.py `_CARD`). Floats are rounded so none appears.
CARD_LIKE = re.compile(r"(?<![\w-])\d{15,16}(?![\w-])")
_SIGNIFICANT = 10
_ADR = re.compile(r"ADR[ -]?(\d{4})")
_DOC_PATH = re.compile(r"\b(docs/[\w./-]+\.md)\b")
_MEASURED_HEADER = re.compile(r"^# --- (MEASURED.*?)\s*-*$")
_BLOCK_HEADER = re.compile(r"^# --- ")

CHANNELS = (
    ("sampling", "SAMPLING",
     "How much of the source the generator reads, and what the profiler "
     "keeps from it."),
    ("generation", "GENERATION",
     "Engine choice and the dials of the bulk sampler."),
    ("free_text", "FREE TEXT",
     "The bounded LLM pool ladder for free-text columns."),
    ("rag", "RAG", "Row documents, value chunks, embedders and retrieval."),
    ("guardrails", "GUARDRAILS",
     "Mode A gates: uniqueness, the DLQ and the BLOCKER ratio."),
    ("relational", "RELATIONAL",
     "Foreign-key generation across a relationship model."),
    ("serving", "SERVING", "vLLM on the worker and the Dataflow fleet."),
    ("evaluation", "EVALUATION",
     "The standalone evaluator (sdfb-evaluation) and its budgets."),
)

# (knob id suffix, default, choices, unit, label, purpose) — the evaluator's
# plan-documented flags, used until its CLI exists (Ruling G2).
_PLANNED_EVAL = (
    ("mode", "exact", ["exact", "sampled"], None, "Evaluation mode",
     "exact scans every row; sampled evaluates Bernoulli samples of "
     "sample_rows per side."),
    ("sample_rows", 200_000, None, "rows", "Sample rows per side",
     "Rows per side in sampled mode."),
    ("privacy_sample_rows", 50_000, None, "rows", "Privacy sample rows",
     "Synthetic rows the Gower nearest-neighbour privacy checks read."),
    ("detection_sample_rows", 50_000, None, "rows", "Detection sample rows",
     "Rows per side the classifier two-sample test reads."),
    ("pair_max_columns", 20, None, "columns", "Pair columns cap",
     "Columns whose pairs feed the correlation and contingency metrics."),
    ("topk_profile", 1000, None, "values", "Top-k profile size",
     "Values kept in each top-k profile (metrics still use every value)."),
    ("row_flags_top_k", 100, None, "rows", "Flagged rows per check",
     "Rows kept per privacy check in evaluation_row_flags."),
    ("row_flags_source_keys", "hashed", ["hashed",
                                         "raw"], None, "Flagged source keys",
     "hashed stores a salted hash of the matched source key; raw stores "
     "the key itself."),
    ("max_bytes_billed", 1_099_511_627_776, None, "bytes", "BigQuery bytes cap",
     "maximumBytesBilled on every evaluator query."),
    ("max_shuffle_gb", 500, None, "GB", "Shuffle budget",
     "Predicted Beam shuffle above which the evaluator refuses to launch."),
    ("scope", "auto", ["auto", "table", "as_of", "appends",
                       "manual"], None, "Evaluation scope",
     "Which synthetic rows count: the whole table, a snapshot, the rows the "
     "run appended, or a manual window."),
)

# ---------------------------------------------------------------- helpers --


def _rel(path: Path) -> str:
  return path.resolve().relative_to(REPO).as_posix()


@functools.cache
def _lines(path: Path) -> tuple[str, ...]:
  return tuple(path.read_text(encoding="utf-8").splitlines())


@functools.cache
def _tree(path: Path) -> ast.Module:
  return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _source(path: Path, line: int) -> str:
  return f"{_rel(path)}:{line}"


def _assign_line(path: Path, name: str) -> int:
  """Line of the module-level assignment that binds `name`."""
  for node in _tree(path).body:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
      targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
      targets = [node.target]
    for target in targets:
      names = target.elts if isinstance(target, ast.Tuple) else [target]
      if any(isinstance(n, ast.Name) and n.id == name for n in names):
        return node.lineno
  raise LookupError(f"{name} is not assigned at module level in {_rel(path)}")


def _class_member_line(path: Path, cls: str, member: str) -> int:
  """Line of `member` (an attribute or a method) in class `cls`."""
  for node in ast.walk(_tree(path)):
    if isinstance(node, ast.ClassDef) and node.name == cls:
      for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(
            item.target, ast.Name) and item.target.id == member:
          return item.lineno
        if isinstance(item, ast.FunctionDef) and item.name == member:
          return item.lineno
  raise LookupError(f"{cls}.{member} not found in {_rel(path)}")


def _def_line(path: Path, name: str) -> int:
  for node in _tree(path).body:
    if isinstance(node, ast.FunctionDef) and node.name == name:
      return node.lineno
  raise LookupError(f"def {name} not found in {_rel(path)}")


def _text_line(path: Path, needle: str, *, after: int = 0) -> int:
  """First line (1-based) at or after `after` that contains `needle`."""
  for number, line in enumerate(_lines(path), start=1):
    if number >= after and needle in line:
      return number
  raise LookupError(f"{needle!r} not found in {_rel(path)}: the code changed; "
                    "re-verify the knob or annotation that anchors on it")


def _comment_above(path: Path, line: int) -> str:
  """The contiguous `#` comment block right above `line`, as one sentence."""
  lines = _lines(path)
  out: list[str] = []
  index = line - 2
  while index >= 0 and lines[index].lstrip().startswith("#"):
    out.append(lines[index].lstrip()[1:].strip())
    index -= 1
  return " ".join(reversed(out)).strip()


def _adrs(*texts: str) -> list[str]:
  found = set()
  for text in texts:
    found.update(_ADR.findall(text or ""))
  return sorted(found)


def _doc_paths(*texts: str) -> list[str]:
  found = set()
  for text in texts:
    for path in _DOC_PATH.findall(text or ""):
      if (REPO / path).is_file():
        found.add(path)
  return sorted(found)


def _round(value: float) -> float:
  if value == 0 or not math.isfinite(value):
    return value
  digits = _SIGNIFICANT - math.floor(math.log10(abs(value))) - 1
  return round(value, max(digits, 0))


def _jsonable(value: Any) -> Any:
  """Tuples → lists, floats rounded (see CARD_LIKE), keys → strings."""
  if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
    return value
  if isinstance(value, float):
    return _round(value)
  if isinstance(value, dict):
    return {str(k): _jsonable(v) for k, v in value.items()}
  if isinstance(value, (list, tuple, frozenset, set)):
    items = sorted(value) if isinstance(value, (set, frozenset)) else value
    return [_jsonable(v) for v in items]
  if hasattr(value, "_asdict"):
    return _jsonable(value._asdict())
  raise TypeError(f"not JSON-serializable: {value!r}")


def _module_file(module) -> Path:
  return Path(module.__file__).resolve()


def _knob(**fields: Any) -> dict[str, Any]:
  """One knob, with the optional keys dropped when empty."""
  base = {
      "id": fields.pop("id"),
      "channel": fields.pop("channel"),
      "group": fields.pop("group"),
      "label": fields.pop("label"),
      "value": _jsonable(fields.pop("value")),
      "unit": fields.pop("unit", None),
      "settable_via": fields.pop("settable_via"),
  }
  for key in ("cli_flag", "composer_param", "composer_default", "flex_param",
              "choices", "required", "help", "comment"):
    value = fields.pop(key, None)
    if value not in (None, "", [], False):
      base[key] = _jsonable(value)
  base["source"] = fields.pop("source")
  base["source_token"] = fields.pop("source_token")
  base["related_adrs"] = sorted(set(fields.pop("related_adrs", [])))
  base["docs"] = sorted(set(fields.pop("docs", [])))
  if fields:
    raise TypeError(f"unknown knob fields {sorted(fields)}")
  return base


# ----------------------------------------------------------- CLI surfaces --


@functools.cache
def _cli() -> tuple[dict[str, Any], dict[str, argparse.Action]]:
  """`run_pipeline.parse_args`'s parsed defaults and its argparse actions."""
  from sdfb_beam.cli import run_pipeline

  parsers: list[argparse.ArgumentParser] = []
  original = argparse.ArgumentParser.parse_known_args

  def spy(self, *args, **kwargs):
    parsers.append(self)
    return original(self, *args, **kwargs)

  with mock.patch.object(argparse.ArgumentParser, "parse_known_args", spy):
    args, _ = run_pipeline.parse_args(list(_REQUIRED_ARGV))
  actions = {
      action.dest: action
      for action in parsers[0]._actions  # pylint: disable=protected-access  # argparse keeps its actions private
      if action.option_strings
  }
  return vars(args), actions


@functools.cache
def _add_argument_lines() -> dict[str, int]:
  """flag → line of its `add_argument` call inside `parse_args`."""
  out: dict[str, int] = {}
  for node in ast.walk(_tree(_RUN_PIPELINE)):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and
        node.func.attr == "add_argument" and node.args and
        isinstance(node.args[0], ast.Constant)):
      out[str(node.args[0].value)] = node.args[0].lineno
  return out


@functools.cache
def _composer_params() -> dict[str, tuple[str, int]]:
  """DAG param → (default as written, line). Deploy-time markers stay raw."""
  tree = _tree(_COMPOSER)
  literals: dict[str, Any] = {}
  params: dict[str, tuple[str, int]] = {}
  for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
        node.targets[0], ast.Name):
      name = node.targets[0].id
      if name == "default_dag_params" and isinstance(node.value, ast.Dict):
        for key, value in zip(node.value.keys, node.value.values, strict=True):
          if not isinstance(key, ast.Constant):
            continue
          default = _param_default(value, literals)
          params[str(key.value)] = (default, key.lineno)
      else:
        try:
          literals[name] = ast.literal_eval(node.value)
        except ValueError:
          continue
  return params


def _param_default(call: ast.expr, literals: dict[str, Any]) -> str:
  if isinstance(call, ast.Call):
    for keyword in call.keywords:
      if keyword.arg == "default":
        node = keyword.value
        if isinstance(node, ast.Constant):
          return str(node.value)
        if isinstance(node, ast.Name) and node.id in literals:
          return str(literals[node.id])
        return ast.unparse(node)
  return ""


@functools.cache
def _flex_params() -> frozenset[str]:
  data = json.loads(_FLEX.read_text(encoding="utf-8"))
  return frozenset(p["name"] for p in data["parameters"])


def _cli_knob(
    dest: str,
    channel: str,
    group: str,
    label: str,
    unit: str | None = None,
    *,
    adrs: Iterable[str] = (),
    docs: Iterable[str] = ()) -> dict[str, Any]:
  """A launcher flag: default from `parse_args`, line from its AST."""
  defaults, actions = _cli()
  action = actions[dest]
  flag = action.option_strings[0]
  line = _add_argument_lines()[flag]
  help_text = " ".join((action.help or "").split())
  composer = _composer_params().get(dest)
  settable = ["cli"]
  if composer:
    settable.append("composer")
  if dest in _flex_params():
    settable.append("flex")
  value = None if action.required else defaults[dest]
  composer_default = None
  if composer and str(composer[0]) != str(value if value is not None else ""):
    composer_default = composer[0]
  return _knob(
      id=dest,
      channel=channel,
      group=group,
      label=label,
      value=value,
      unit=unit,
      settable_via=settable,
      cli_flag=flag,
      composer_param=dest if composer else None,
      composer_default=composer_default,
      flex_param=dest if dest in _flex_params() else None,
      choices=list(action.choices) if action.choices else None,
      required=action.required,
      help=help_text,
      source=_source(_RUN_PIPELINE, line),
      source_token=f'"{flag}"',
      related_adrs=[*_adrs(help_text), *adrs],
      docs=[*_doc_paths(help_text), *docs],
  )


def _const_knob(
    knob_id: str,
    module,
    name: str,
    channel: str,
    group: str,
    label: str,
    unit: str | None = None,
    *,
    adrs: Iterable[str] = (),
    docs: Iterable[str] = ()) -> dict[str, Any]:
  """A module constant: value imported, line and comment block from its AST."""
  path = _module_file(module)
  line = _assign_line(path, name)
  comment = _comment_above(path, line)
  return _knob(
      id=knob_id,
      channel=channel,
      group=group,
      label=label,
      value=getattr(module, name),
      unit=unit,
      settable_via=["constant"],
      comment=comment,
      source=_source(path, line),
      source_token=name,
      related_adrs=[*_adrs(comment), *adrs],
      docs=[*_doc_paths(comment), *docs],
  )


def _composer_only_knob(param: str,
                        channel: str,
                        group: str,
                        label: str,
                        unit: str | None,
                        *,
                        adrs: Iterable[str] = ()):
  default, line = _composer_params()[param]
  return _knob(
      id=param,
      channel=channel,
      group=group,
      label=label,
      value=default,
      unit=unit,
      settable_via=["composer"],
      composer_param=param,
      source=_source(_COMPOSER, line),
      source_token=f'"{param}"',
      related_adrs=list(adrs),
  )


# ----------------------------------------------------------------- knobs --


def _sampling() -> list[dict[str, Any]]:
  from sdfb_core.stats import source_stats

  group = "Profiler (source_table_stats)"
  return [
      _cli_knob(
          "reference_rows_limit",
          "sampling",
          "Reference sample",
          "Reference rows (n)",
          "rows",
          adrs=["0005", "0022"],
          docs=["docs/designs/2026-07-24-reference-sample-scaling.md"]),
      _cli_knob(
          "source_stats",
          "sampling",
          "Reference sample",
          "Source-stats tier",
          adrs=["0022"],
          docs=["docs/designs/2026-08-05-source-table-stats.md"]),
      _const_knob("profiler_version", source_stats, "PROFILER_VERSION",
                  "sampling", group, "Profiler version"),
      _const_knob("top_values_max_distinct", source_stats,
                  "_TOP_VALUES_MAX_DISTINCT", "sampling", group,
                  "Literal top values up to (distinct)", "values"),
      _const_knob("top_values_top_k", source_stats, "_TOP_VALUES_TOP_K",
                  "sampling", group, "Top values kept", "values"),
      _const_knob("shape_mix_top_k", source_stats, "_SHAPE_MIX_TOP_K",
                  "sampling", group, "Shape masks kept", "masks"),
      _const_knob("length_percentiles", source_stats, "_LEN_PCTS", "sampling",
                  group, "Length percentiles", "quantile"),
      _const_knob("null_pattern_max_cols", source_stats,
                  "_NULL_PATTERN_MAX_COLS", "sampling", group,
                  "Null-pattern column cap", "columns"),
      _const_knob("null_pattern_top_k", source_stats, "_NULL_PATTERN_TOP_K",
                  "sampling", group, "Null patterns kept", "patterns"),
  ]


def _generation() -> list[dict[str, Any]]:
  from sdfb_core.engines import base
  from sdfb_core.engines.b1_rag import profile as b1_profile
  from sdfb_core.engines.b2_library import fidelity as b2_fidelity

  base_path = _module_file(base)
  ladder_line = _def_line(base_path, "escalating_temperatures")
  config_line = _class_member_line(base_path, "GenerationConfig", "max_retries")
  fields = base.GenerationConfig.model_fields
  return [
      _cli_knob("engine", "generation", "Engine", "Engine", adrs=["0006"]),
      _cli_knob("num_rows", "generation", "Engine", "Rows to generate (M)",
                "rows"),
      _cli_knob("similarity", "generation", "Sampler", "Similarity", "0-1"),
      _cli_knob("seed", "generation", "Sampler", "Seed"),
      _cli_knob(
          "batch_size",
          "generation",
          "Sampler",
          "Rows per element",
          "rows",
          docs=["docs/designs/2026-07-26-ws5-generation-throughput.md"]),
      _knob(
          id="generation_max_retries",
          channel="generation",
          group="Sampler",
          label="Retries per batch",
          value=fields["max_retries"].default,
          unit="retries",
          settable_via=["constant"],
          source=_source(base_path, config_line),
          source_token="max_retries",
      ),
      _knob(
          id="temperature_ladder",
          channel="generation",
          group="Sampler",
          label="Free-text temperature ladder",
          value=list(base.escalating_temperatures()),
          unit="temperature",
          settable_via=["derived"],
          comment=" ".join((base.escalating_temperatures.__doc__ or
                            "").split()),
          source=_source(base_path, ladder_line),
          source_token="def escalating_temperatures",
      ),
      _knob(
          id="sampling_ladder",
          channel="free_text",
          group="Pool ladder",
          label="Sampling levels per retry",
          value=list(base.escalating_sampling()),
          unit=None,
          settable_via=["derived"],
          comment=" ".join((base.escalating_sampling.__doc__ or "").split()),
          source=_source(base_path, _def_line(base_path,
                                              "escalating_sampling")),
          source_token="def escalating_sampling",
      ),
      _const_knob(
          "temporal_max_age_years_b1",
          b1_profile,
          "_MAX_TEMPORAL_AGE_YEARS",
          "generation",
          "Temporal floor",
          "Temporal floor, b1 (now minus years)",
          "years",
      ),
      _const_knob(
          "temporal_max_age_years_b2",
          b2_fidelity,
          "_MAX_TEMPORAL_AGE_YEARS",
          "generation",
          "Temporal floor",
          "Temporal floor, b2 (now minus years)",
          "years",
      ),
  ]


def _free_text() -> list[dict[str, Any]]:
  from sdfb_beam.io import source_values
  from sdfb_core.engines import generation_plan
  from sdfb_core.engines.b1_rag import engine as b1
  from sdfb_core.engines.b1_rag import profile as b1_profile

  ladder = "Pool ladder"
  routing = "Free-text routing (b1)"
  return [
      _const_knob(
          "free_text_pool_max",
          generation_plan,
          "FREE_TEXT_POOL_MAX",
          "free_text",
          ladder,
          "Pool cap",
          "values",
          adrs=["0018", "0028"]),
      _const_knob(
          "pool_values_per_call",
          b1,
          "_POOL_VALUES_PER_CALL",
          "free_text",
          ladder,
          "Values per LLM call",
          "values",
          adrs=["0013"]),
      _const_knob("pool_parallel_choices", b1, "_POOL_PARALLEL_CHOICES",
                  "free_text", ladder, "Parallel choices (n)", "choices"),
      _const_knob("pool_stagnation_window", b1, "_POOL_STAGNATION_WINDOW",
                  "free_text", ladder, "Stagnation window", "attempts"),
      _const_knob("pool_stagnation_min_novel", b1, "_POOL_STAGNATION_MIN_NOVEL",
                  "free_text", ladder, "Stagnation: min novel per attempt",
                  "values"),
      _const_knob("pool_format_collapse_rounds", b1,
                  "_POOL_FORMAT_COLLAPSE_ROUNDS", "free_text", ladder,
                  "Format-collapse exit", "rounds"),
      _const_knob("pool_build_max_workers", b1, "_POOL_BUILD_MAX_WORKERS",
                  "free_text", ladder, "Pool ladders in parallel", "threads"),
      _const_knob(
          "source_domain_cap",
          source_values,
          "_DEFAULT_CAP",
          "free_text",
          "Source-domain rejection",
          "Source-domain rejection cap",
          "distinct values",
          adrs=["0023"]),
      _const_knob("free_text_unique_ratio", b1_profile,
                  "_FREE_TEXT_UNIQUE_RATIO", "free_text", routing,
                  "Unique ratio for free text", "ratio"),
      _const_knob("free_text_min_mean_len", b1_profile,
                  "_FREE_TEXT_MIN_MEAN_LEN", "free_text", routing,
                  "Mean length for free text", "chars"),
      _const_knob("free_text_max_categories", b1_profile,
                  "_FREE_TEXT_MAX_CATEGORIES", "free_text", routing,
                  "Categorical up to (distinct)", "values"),
      _cli_knob("freetext_expansion", "free_text", "Expansion and prompts",
                "Free-text expansion"),
      _cli_knob(
          "pool_seed_strategy",
          "free_text",
          "Expansion and prompts",
          "Pool seed strategy",
          docs=["docs/designs/2026-07-25-rag-retrieval-geometry-roadmap.md"]),
      _cli_knob(
          "prompt_constraints",
          "free_text",
          "Expansion and prompts",
          "Prompt constraints",
          adrs=["0024"],
          docs=["docs/designs/2026-08-10-prompt-constraints.md"]),
      _cli_knob("prompt_debug", "free_text", "Expansion and prompts",
                "Prompt debug logging"),
      _cli_knob("pool_pattern_guidance", "free_text", "Expansion and prompts",
                "Decode-time pattern guidance"),
      _cli_knob(
          "build_pool_layer",
          "free_text",
          "Persistence",
          "Build the pool layer",
          adrs=["0020"]),
  ]


def _rag() -> list[dict[str, Any]]:
  from sdfb_core.engines.b1_rag import engine as b1
  from sdfb_core.rag import chunking, embedding

  embed_path = _module_file(embedding)
  hashing = embedding.HashingEmbedder()
  bge = embedding.BgeEmbedder("/unused")
  return [
      _const_knob(
          "max_row_doc_rows",
          chunking,
          "MAX_ROW_DOC_ROWS",
          "rag",
          "Population",
          "Row documents embedded",
          "rows",
          adrs=["0019"]),
      _const_knob(
          "max_free_text_values_per_column",
          chunking,
          "MAX_FREE_TEXT_VALUES_PER_COLUMN",
          "rag",
          "Population",
          "Value chunks per free-text column",
          "values",
          adrs=["0019"]),
      _const_knob(
          "rag_top_k",
          b1,
          "_DEFAULT_TOP_K",
          "rag",
          "Retrieval",
          "Exemplars per prompt (top-k)",
          "values",
          docs=["docs/designs/2026-07-07-rag-layer-design.md"]),
      _knob(
          id="hashing_embedder_dim",
          channel="rag",
          group="Embedders",
          label="HashingEmbedder dimension",
          value=hashing.dim,
          unit="dims",
          settable_via=["constant"],
          source=_source(
              embed_path,
              _class_member_line(embed_path, "HashingEmbedder", "__init__")),
          source_token="dim: int = 384",
      ),
      _knob(
          id="bge_embedder_dim",
          channel="rag",
          group="Embedders",
          label="bge-small-en-v1.5 dimension",
          value=bge.dim,
          unit="dims",
          settable_via=["constant"],
          source=_source(
              embed_path,
              _text_line(
                  embed_path,
                  "dim: int = 384",
                  after=_class_member_line(embed_path, "BgeEmbedder",
                                           "__init__"))),
          source_token="dim: int = 384",
      ),
      _knob(
          id="bge_max_length",
          channel="rag",
          group="Embedders",
          label="bge max tokens",
          value=bge._max_length,  # pylint: disable=protected-access  # the default is only kept on the instance
          unit="tokens",
          settable_via=["constant"],
          source=_source(embed_path,
                         _text_line(embed_path, "max_length: int = 512")),
          source_token="max_length: int = 512",
      ),
      _const_knob(
          "embedder_min_free_vram",
          embedding,
          "_MIN_FREE_VRAM_BYTES",
          "rag",
          "Embedders",
          "Free VRAM before the embedder takes CUDA",
          "bytes",
          adrs=["0014", "0019"]),
      _knob(
          id="default_embedder_identity",
          channel="rag",
          group="Embedders",
          label="Embedder when --embedder_uri is empty",
          value=list(embedding._DEFAULT_EMBEDDER_IDENTITY),  # pylint: disable=protected-access  # shown as-is
          unit=None,
          settable_via=["constant"],
          source=_source(embed_path,
                         _assign_line(embed_path,
                                      "_DEFAULT_EMBEDDER_IDENTITY")),
          source_token="_DEFAULT_EMBEDDER_IDENTITY",
      ),
      _cli_knob("embedder_uri", "rag", "Embedders", "Embedder weights URI"),
      _cli_knob(
          "build_rag_layer",
          "rag",
          "Population",
          "Build the RAG layer",
          adrs=["0017", "0019"]),
  ]


def _guardrails() -> list[dict[str, Any]]:
  from sdfb_core.contracts import model_adjustment
  from sdfb_core.validation import summary

  thresholds = yaml.safe_load(_THRESHOLDS.read_text(encoding="utf-8"))
  ratios = thresholds["defaults"]["blocker_failure_ratio"]
  block_line = _text_line(_THRESHOLDS, "blocker_failure_ratio:")
  knobs = [
      _cli_knob("env", "guardrails", "Gate", "Threshold tier"),
      _cli_knob(
          "uniqueness_mode",
          "guardrails",
          "Uniqueness",
          "Uniqueness mode",
          adrs=["0034"],
          docs=[
              "docs/designs/2026-09-07-generation-throughput-where-time-goes.md"
          ]),
      _cli_knob(
          "driven_uniqueness_mode",
          "guardrails",
          "Uniqueness",
          "Uniqueness mode, driven children",
          adrs=["0036"]),
      _cli_knob("write_disposition", "guardrails", "Landing",
                "Write disposition"),
  ]
  for env in ("dev", "uat", "prd"):
    line = _text_line(_THRESHOLDS, f"{env}:", after=block_line)
    knobs.append(
        _knob(
            id=f"blocker_failure_ratio_{env}",
            channel="guardrails",
            group="Gate",
            label=f"BLOCKER ratio ({env})",
            value=ratios[env],
            unit="ratio",
            settable_via=["constant"],
            comment="Fraction of records failing any BLOCKER rule before the "
            "Dataflow job is failed (config/thresholds.yml).",
            source=_source(_THRESHOLDS, line),
            source_token=f"{env}:",
        ))
  rules = thresholds["rules"]
  blockers = sorted(
      rule for rule, spec in rules.items()
      if spec.get("severity") == "BLOCKER" and spec.get("scope") != "post_run")
  rules_line = _text_line(_THRESHOLDS, "rules:")
  knobs.append(
      _knob(
          id="blocker_rules_declared",
          channel="guardrails",
          group="Gate",
          label="BLOCKER rules (thresholds.yml)",
          value=blockers,
          unit=None,
          settable_via=["constant"],
          source=_source(_THRESHOLDS, rules_line),
          source_token="rules:",
      ))
  summary_path = _module_file(summary)
  knobs.append(
      _knob(
          id="blocker_rule_ids",
          channel="guardrails",
          group="Gate",
          label="BLOCKER rules counted by the gate",
          value=sorted(summary.BLOCKER_RULE_IDS),
          unit=None,
          settable_via=["constant"],
          comment=_comment_above(summary_path,
                                 _assign_line(summary_path,
                                              "BLOCKER_RULE_IDS")),
          source=_source(summary_path,
                         _assign_line(summary_path, "BLOCKER_RULE_IDS")),
          source_token="BLOCKER_RULE_IDS",
      ))
  knobs.append(
      _const_knob(
          "repeat_share_tolerance",
          model_adjustment,
          "REPEAT_SHARE_TOLERANCE",
          "guardrails",
          "Gate",
          "Key-repeat share tolerance",
          "share",
          adrs=["0038"]))
  return knobs


def _relational() -> list[dict[str, Any]]:
  from sdfb_beam.cli import run_pipeline
  from sdfb_core.engines import pk_capacity

  return [
      _cli_knob("generate_fk_relationships", "relational", "Model",
                "Generate FK relationships"),
      _cli_knob(
          "relationships_uri",
          "relational",
          "Model",
          "Relationship models",
          docs=["docs/designs/2026-08-24-relationships-as-config.md"]),
      _cli_knob("multi_table_mode", "relational", "Model", "Multi-table mode"),
      _cli_knob("on_model_conflict", "relational", "Model",
                "On a measured model conflict"),
      _cli_knob(
          "fk_candidate_cap",
          "relational",
          "Keys",
          "Candidates per shared key (Top-M)",
          "candidates",
          adrs=["0037"],
          docs=["docs/designs/2026-09-11-multi-parent-children.md"]),
      _const_knob(
          "fk_key_sample_floor",
          pk_capacity,
          "FK_KEY_SAMPLE_FLOOR",
          "relational",
          "Keys",
          "Parent keys sampled (floor)",
          "keys",
          adrs=["0030", "0035"]),
      _const_knob(
          "fk_key_sample_ceiling",
          pk_capacity,
          "FK_KEY_SAMPLE_CEILING",
          "relational",
          "Keys",
          "Parent keys sampled (ceiling)",
          "keys",
          adrs=["0035"]),
      _const_knob(
          "fk_key_sample_margin",
          pk_capacity,
          "FK_KEY_SAMPLE_MARGIN",
          "relational",
          "Keys",
          "Capacity margin over num_rows",
          "x",
          adrs=["0035"]),
      _const_knob(
          "max_conditional_values_per_request",
          run_pipeline,
          "_MAX_CONDITIONAL_VALUES_PER_REQUEST",
          "relational",
          "Keys",
          "Candidate values per request",
          "values",
          adrs=["0037"]),
  ]


def _serving() -> list[dict[str, Any]]:
  return [
      _cli_knob("client_type", "serving", "LLM", "Model client", adrs=["0014"]),
      _cli_knob("vllm_dtype", "serving", "LLM", "vLLM dtype"),
      _cli_knob("vllm_max_model_len", "serving", "LLM", "vLLM max model len",
                "tokens"),
      _cli_knob(
          "autoscaling", "serving", "Fleet", "Autoscaling", adrs=["0034"]),
      _cli_knob(
          "initial_workers",
          "serving",
          "Fleet",
          "Initial workers",
          "workers",
          adrs=["0034"]),
      _composer_only_knob("gpu", "serving", "Fleet", "GPU profile", None),
      _composer_only_knob(
          "sdk_containers",
          "serving",
          "Fleet",
          "SDK containers per worker",
          None,
          adrs=["0034"]),
  ]


def eval_knobs(cli_path: Path = EVAL_CLI) -> list[dict[str, Any]]:
  """The EVALUATION channel: the evaluator CLI by AST, else the plan list."""
  found: dict[str, tuple[Any, list[str] | None, int]] = {}
  if cli_path.is_file():
    tree = ast.parse(
        cli_path.read_text(encoding="utf-8"), filename=str(cli_path))
    for node in ast.walk(tree):
      if (isinstance(node, ast.Call) and
          isinstance(node.func, ast.Attribute) and
          node.func.attr == "add_argument" and node.args and
          isinstance(node.args[0], ast.Constant)):
        name = str(node.args[0].value).lstrip("-")
        default: Any = None
        choices: list[str] | None = None
        for keyword in node.keywords:
          try:
            if keyword.arg == "default":
              default = ast.literal_eval(keyword.value)
            elif keyword.arg == "choices":
              choices = list(ast.literal_eval(keyword.value))
          except ValueError:
            continue
        found[name] = (default, choices, node.lineno)
  out = []
  for name, planned_default, planned_choices, unit, label, purpose in (
      _PLANNED_EVAL):
    source, token = "planned", None
    default, choices = planned_default, planned_choices
    if name in found:
      default, found_choices, line = found[name]
      choices = found_choices or planned_choices
      try:
        source = f"{cli_path.resolve().relative_to(REPO).as_posix()}:{line}"
      except ValueError:
        source = f"{cli_path.as_posix()}:{line}"
      token = f"--{name}"
    out.append(
        _knob(
            id=f"eval_{name}",
            channel="evaluation",
            group="Evaluator",
            label=label,
            value=default,
            unit=unit,
            settable_via=["cli"],
            cli_flag=f"--{name}",
            choices=choices,
            help=purpose,
            source=source,
            source_token=token,
            related_adrs=[],
            docs=["docs/designs/2026-07-07-evaluation-framework-design.md"],
        ))
  return out


# ----------------------------------------------------------- annotations --


def _evidence(path: Path, needle: str, role: str, *, after: int = 0):
  line = _text_line(path, needle, after=after)
  return {"source": _source(path, line), "role": role, "excerpt": needle}


def _annotations() -> list[dict[str, Any]]:
  from sdfb_core.engines import base
  from sdfb_core.engines.b1_rag import _fidelity as b1_fidelity
  from sdfb_core.engines.b1_rag import engine as b1
  from sdfb_core.engines.b2_library import backends as b2_backends
  from sdfb_core.engines.b2_library import engine as b2
  from sdfb_core.engines.b2_library import fidelity as b2_fidelity
  from sdfb_core.engines.b2_library import freetext as b2_freetext
  from sdfb_core.rag import embedding
  from sdfb_core.validation import summary

  b1_fid = _module_file(b1_fidelity)
  out = [{
      "id": "similarity-semantics",
      "title": "similarity means different things in b1 and b2",
      "knobs": ["similarity"],
      "docs_say": "GenerationConfig and the b1 engine docstring: B.1 maps "
                  "similarity to retrieval-neighbourhood tightness and "
                  "sampling variance.",
      "code_does": "b1 uses it only to blend temporal draws (anchored vs "
                   "uniform); numeric and categorical draws and every "
                   "free-text pool ignore it. b2 maps it to a categorical "
                   "sampling temperature 2(1 - s) and an LLM temperature "
                   "1.3 - 1.2 s.",
      "evidence": [
          _evidence(
              _module_file(base),
              "B.1 maps it to a retrieval-vs-perturbation balance", "docs"),
          _evidence(
              _module_file(b1),
              "`similarity` (GenerationConfig) = retrieval-neighborhood tightness +",
              "docs"),
          _evidence(
              b1_fid,
              "blended = similarity * (anchored + jitter) + (1.0 - similarity) * uniform",
              "code"),
          _evidence(
              b1_fid,
              "in-range and novel-by-interpolation; `similarity` is unused here.",
              "code"),
          _evidence(
              _module_file(b1),
              "del similarity  # Unused: pool, routed and expansion draws ignore it.",
              "code"),
          _evidence(
              _module_file(b2), "return round(2.0 * (1.0 - s), 4)", "code"),
          _evidence(
              _module_file(b2_freetext), "return round(1.3 - 1.2 * s, 4)",
              "code"),
      ],
      "related_adrs": ["0013", "0025"],
  }]
  deciles = b2_fidelity._decile_points([float(i) for i in range(100)])  # pylint: disable=protected-access  # verified
  if len(
      deciles
  ) == 11:  # noqa: PLR2004 — the discrepancy holds only while b2 keeps 11 points
    out.append({
        "id": "inverse-cdf-resolution",
        "title": "Inverse-CDF resolution differs by engine",
        "knobs": ["engine", "reference_rows_limit"],
        "docs_say": "Both engines are described as inverse-CDF sampling of "
                    "the source marginal (ADR 0022, ADR 0025).",
        "code_does": "b1 interpolates across the whole sorted reference "
                     "sample (one knot per observed value); b2 interpolates "
                     "across 11 decile points (p0..p100), so b2 cannot "
                     "reproduce any shape inside a decile.",
        "evidence": [
            _evidence(b1_fid, "grid = np.linspace(0.0, 1.0, obs.size)", "code"),
            _evidence(
                _module_file(b2_fidelity),
                "return tuple(ordered[round(i * last / 10)] for i in range(11))",
                "code"),
            _evidence(
                _module_file(b2_backends),
                "draws = np.interp(rng.random(n), grid, np.asarray(p.quantiles))",
                "code"),
        ],
        "related_adrs": ["0022", "0025"],
    })
  embed_path = _module_file(embedding)
  article = REPO / "docs" / "articles" / "04-b1-rag-deep-dive.md"
  out.append({
      "id": "bge-pooling",
      "title": "bge-small is mean-pooled, not CLS-pooled",
      "knobs": ["bge_embedder_dim", "bge_max_length"],
      "docs_say": "The bge-small-en-v1.5 model card pools on the [CLS] token; "
                  "the class docstring calls mean pooling \"the bge recipe\".",
      "code_does": "BgeEmbedder mean-pools the last hidden state over the "
                   "attention mask, then L2-normalizes. Whether [CLS] picks "
                   "better exemplars here is unmeasured.",
      "evidence": [
          _evidence(embed_path, "Mean-pooled, L2-normalized CLS-free", "docs"),
          _evidence(embed_path,
                    "# bge uses mean pooling over the last hidden state.",
                    "code"),
          _evidence(article, "bge was trained for `[CLS]` pooling", "docs"),
      ],
      "links": [{
          "label": "bge-small-en-v1.5 model card",
          "url": "https://huggingface.co/BAAI/bge-small-en-v1.5",
      }],
      "related_adrs": ["0017"],
  })
  if "fk.orphan" not in summary.BLOCKER_RULE_IDS:
    summary_path = _module_file(summary)
    out.append({
        "id": "fk-orphan-not-blocker",
        "title": "fk.orphan is BLOCKER in thresholds.yml but not counted",
        "knobs": ["blocker_rule_ids", "blocker_rules_declared"],
        "docs_say": "config/thresholds.yml declares fk.orphan with severity "
                    "BLOCKER (ADR 0031).",
        "code_does": "BLOCKER_RULE_IDS, the set the run gate counts, omits "
                     "fk.orphan: orphan rows divert to the DLQ but never "
                     "move observed_blocker_ratio.",
        "evidence": [
            _evidence(_THRESHOLDS, "fk.orphan:", "docs"),
            _evidence(
                _THRESHOLDS,
                "severity: BLOCKER",
                "docs",
                after=_text_line(_THRESHOLDS, "fk.orphan:")),
            _evidence(summary_path, "BLOCKER_RULE_IDS = frozenset({", "code"),
        ],
        "related_adrs": ["0031"],
    })
  return out


# -------------------------------------------------------------- measured --


def _load_script(path: Path):
  spec = importlib.util.spec_from_file_location(f"gui_measured_{path.stem}",
                                                path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _blocks(path: Path) -> list[tuple[str, int, int]]:
  """(header, first line, last line) of each `# --- MEASURED …` block."""
  lines = _lines(path)
  blocks: list[tuple[str, int, int]] = []
  current: tuple[str, int] | None = None
  for number, line in enumerate(lines, start=1):
    starts_def = line.startswith(("def ", "class "))
    if _BLOCK_HEADER.match(line) or starts_def:
      if current:
        blocks.append((current[0], current[1], number - 1))
        current = None
      match = _MEASURED_HEADER.match(line)
      if match:
        current = (match.group(1).rstrip(" -"), number)
    if starts_def and blocks:
      break
  return blocks


def _measured_sources() -> list[dict[str, str]]:
  """Each figure script with its docstring's provenance paragraph."""
  out = []
  for path in _MEASURED_SCRIPTS:
    doc = (ast.get_docstring(_tree(path)) or "").strip().split("\n\n")
    provenance = " ".join((doc[1] if len(doc) > 1 else doc[0]).split())
    out.append({"script": _rel(path), "provenance": provenance})
  return out


def _measured() -> list[dict[str, Any]]:
  out = []
  for path in _MEASURED_SCRIPTS:
    module = _load_script(path)
    blocks = _blocks(path)
    for node in _tree(path).body:
      if not isinstance(node, ast.Assign):
        continue
      names = [
          n.id
          for target in node.targets
          for n in (target.elts if isinstance(target, ast.Tuple) else [target])
          if isinstance(n, ast.Name)
      ]
      header = next((h for h, lo, hi in blocks if lo <= node.lineno <= hi),
                    None)
      for name in names:
        if header is None and not name.endswith("_MEASURED"):
          continue
        line = _lines(path)[node.lineno - 1]
        inline = line.split("#", 1)[1].strip() if "#" in line else ""
        out.append({
            "id": f"{path.stem}.{name}",
            "script": _rel(path),
            "name": name,
            "value": _jsonable(getattr(module, name)),
            "block": header or "MEASURED (named)",
            "comment": inline or _comment_above(path, node.lineno),
            "source": _source(path, node.lineno),
        })
  return out


# ------------------------------------------------------------------ main --


def build() -> dict[str, Any]:
  knobs = [
      *_sampling(),
      *_generation(),
      *_free_text(),
      *_rag(),
      *_guardrails(),
      *_relational(),
      *_serving(),
      *eval_knobs(),
  ]
  return {
      "generated_by":
          "scripts/gui/export_knobs.py",
      "note":
          "Generated; do not edit. Re-run the exporter (see its docstring).",
      "channels": [{
          "id": cid,
          "label": label,
          "description": description,
      } for cid, label, description in CHANNELS],
      "knobs":
          knobs,
      "annotations":
          _annotations(),
      "measured_sources":
          _measured_sources(),
      "measured":
          _measured(),
  }


def render(doc: dict[str, Any]) -> str:
  return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
  parser.add_argument(
      "--check",
      action="store_true",
      help="exit 1 when the file differs from a fresh export")
  parser.add_argument("--out", type=Path, default=OUT)
  args = parser.parse_args(argv)
  text = render(build())
  if CARD_LIKE.search(text):
    print(
        "knobs.json would carry a card-number-shaped digit run",
        file=sys.stderr)
    return 1
  if args.check:
    current = args.out.read_text(encoding="utf-8") if args.out.is_file() else ""
    if current != text:
      print(
          f"{args.out}: drift — re-run scripts/gui/export_knobs.py",
          file=sys.stderr)
      return 1
    return 0
  args.out.parent.mkdir(parents=True, exist_ok=True)
  args.out.write_text(text, encoding="utf-8")
  return 0


if __name__ == "__main__":
  sys.exit(main())
