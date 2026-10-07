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
- the EVALUATION channel: the flags of `sdfb-eval plan|run`, read from
  `sdfb_evaluation/cli/main.py` by AST (never imported: the evaluator is a
  standalone project, ADR 0041). Default, choices, help text and line are
  the CLI's own; a listed flag the CLI no longer has fails the export.

Two sibling files, same run:

- `relationships.json`: the committed sample relationship models
  (`config/relationships/example_*.yaml`, `*_example.yaml`) parsed by
  `sdfb_core`'s own registry, every edge with its role (driving, implied,
  conditional, independent, external, documented). Real models are
  gitignored and never exported.
- `dlq_rules.json`: every DLQ rule_id with the error_type and stage the
  DoFn envelopes emit (AST scan of `sdfb_beam/dofns`), the pipeline_step
  `dlq.normalize_dlq_record` assigns, and the severity thresholds.yml
  declares.

Each file records `exported_from` (the commit its `path:line` links resolve
at, and any referenced file that had uncommitted edits).

`--check` exits 1 when a fresh export differs from a committed file in a
value, id, anchor token or path; a line number or the export commit may
move, so an unrelated Python edit that shifts a line passes. `--check-strict`
compares line numbers too. The check runs in `.github/workflows/gui.yml`,
not in the Python test suite.

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
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "gui" / "packages" / "contracts" / "generated"
_RELATIONSHIPS = REPO / "config" / "relationships"
_DOFNS = REPO / "packages" / "sdfb-beam" / "src" / "sdfb_beam" / "dofns"
# `path:LINE` anywhere in an export: the tolerant check compares paths only.
_SOURCE_REF = re.compile(r"^(?P<path>[\w./-]+\.(?:py|yml|yaml|json|md)):\d+$")
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

# (flag, unit, label) — the `sdfb-eval plan|run` flags the EVALUATION channel
# shows. Default, choices, help text and line come from the evaluator's CLI.
_EVAL_FLAGS = (
    ("mode", None, "Evaluation mode"),
    ("sample_rows", "rows", "Sample rows per side"),
    ("privacy_sample_rows", "rows", "Privacy sample rows"),
    ("detection_sample_rows", "rows", "Detection sample rows"),
    ("pair_max_columns", "columns", "Pair columns cap"),
    ("row_flags_top_k", "rows", "Flagged rows per check"),
    ("row_flags_source_keys", None, "Flagged source keys"),
    ("max_bytes_billed", "bytes", "BigQuery bytes cap"),
    ("max_shuffle_gb", "GB", "Shuffle budget"),
    ("scope", None, "Evaluation scope"),
    ("allow_contaminated", None, "Allow contaminated scopes"),
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


def _eval_cli_flags(
    tree: ast.Module) -> dict[str, tuple[int, dict[str, ast.expr]]]:
  """flag → (line of its own string, its argparse keywords as AST nodes).

  The evaluator's CLI declares a flag in three ways, all read here: an
  `add_argument` call, `_boolean(parser, flag, help)` (a boolean with an
  optional value, default False), and a `(flag, default, help)` row of
  `_COUNT_FLAGS`, which a loop adds.
  """
  found: dict[str, tuple[int, dict[str, ast.expr]]] = {}

  def declare(flag: ast.expr, keywords: dict[str, ast.expr]) -> None:
    if isinstance(flag, ast.Constant) and str(flag.value).startswith("--"):
      found[str(flag.value)[2:]] = (flag.lineno, keywords)

  for node in ast.walk(tree):
    if isinstance(node, ast.Call) and node.args:
      if (isinstance(node.func, ast.Attribute) and
          node.func.attr == "add_argument"):
        declare(node.args[0], {k.arg: k.value for k in node.keywords if k.arg})
      elif (isinstance(node.func, ast.Name) and node.func.id == "_boolean" and
            len(node.args) == 3):
        declare(node.args[1], {
            "default": ast.Constant(False),
            "help": node.args[2]
        })
    elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple) and
          any(
              isinstance(target, ast.Name) and target.id == "_COUNT_FLAGS"
              for target in node.targets)):
      for row in node.value.elts:
        if isinstance(row, ast.Tuple) and len(row.elts) == 3:
          declare(row.elts[0], {"default": row.elts[1], "help": row.elts[2]})
  return found


def _eval_cli_names(tree: ast.Module, cli_path: Path) -> dict[str, Any]:
  """The literal constants a flag of the CLI may name: the module's own and
  those it imports from its package (`from sdfb_evaluation.x import A as B`)."""
  names = _module_constants(tree)
  package = cli_path.resolve().parents[1]
  for node in tree.body:
    if not isinstance(node, ast.ImportFrom) or not node.module:
      continue
    head, _, rest = node.module.partition(".")
    module = package.joinpath(*rest.split(".")).with_suffix(".py")
    if head != package.name or not rest or not module.is_file():
      continue
    constants = _module_constants(_tree(module))
    for alias in node.names:
      if alias.name in constants:
        names[alias.asname or alias.name] = constants[alias.name]
  return names


def _literal(node: ast.expr, names: dict[str, Any]) -> Any:
  """`ast.literal_eval`, which also reads a name in `names` and `*name`
  inside a tuple or a list (`choices=("auto", *SCOPE_MODES)`)."""
  if isinstance(node, ast.Name) and node.id in names:
    return names[node.id]
  if isinstance(node, (ast.Tuple, ast.List)):
    out: list[Any] = []
    for item in node.elts:
      if isinstance(item, ast.Starred):
        out.extend(_literal(item.value, names))
      else:
        out.append(_literal(item, names))
    return out
  return ast.literal_eval(node)


def eval_knobs(cli_path: Path = EVAL_CLI) -> list[dict[str, Any]]:
  """The EVALUATION channel: the evaluator CLI's flags, read by AST."""
  tree = _tree(cli_path)
  flags = _eval_cli_flags(tree)
  names = _eval_cli_names(tree, cli_path)
  try:
    path = _rel(cli_path)
  except ValueError:
    path = cli_path.as_posix()
  out = []
  for name, unit, label in _EVAL_FLAGS:
    if name not in flags:
      raise LookupError(
          f"--{name} is not a flag of {path}: the evaluator changed; "
          "re-verify the EVALUATION channel (_EVAL_FLAGS)")
    line, keywords = flags[name]
    default, choices, help_text = (
        _literal(keywords[key], names) if key in keywords else None
        for key in ("default", "choices", "help"))
    help_text = " ".join(str(help_text or "").split())
    in_flex = name in _flex_params()
    out.append(
        _knob(
            id=f"eval_{name}",
            channel="evaluation",
            group="Evaluator",
            label=label,
            value=default,
            unit=unit,
            settable_via=["cli", "flex"] if in_flex else ["cli"],
            cli_flag=f"--{name}",
            flex_param=name if in_flex else None,
            choices=choices,
            # argparse fills %(default)s in when it prints a help text.
            help=help_text.replace("%(default)s", str(default)),
            source=f"{path}:{line}",
            source_token=f'"--{name}"',
            related_adrs=["0041"],
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


# ------------------------------------------------------ relationships --


def build_relationships() -> dict[str, Any]:
  """The committed sample models (`example_*.yaml`, `*_example.yaml`), parsed
  by `sdfb_core`'s own registry: tables, keys, every edge with its role."""
  from sdfb_core.contracts.relationships import RelationshipRegistry

  models = []
  paths = sorted(
      p for p in _RELATIONSHIPS.glob("*.yaml")
      if p.name.startswith("example_") or p.stem.endswith("_example"))
  for path in paths:
    registry = RelationshipRegistry.from_sources([
        (_rel(path), path.read_text(encoding="utf-8"))
    ])
    model = registry.models[0]
    tables = []
    for name, relations in model.tables.items():
      roles = registry.edge_roles(name) if relations.enabled else {}
      edges = []
      for edge in relations.fk:
        drawn = registry.widened(name, edge)
        role = "documented" if not edge.enforced else roles.get(drawn, "")
        edges.append({
            "cols": list(edge.cols),
            "ref": edge.ref,
            "ref_cols": list(edge.ref_cols),
            "enforced": edge.enforced,
            "drives": edge.drives,
            "external": edge.external,
            "role": role or ("external" if edge.external else "disabled"),
            "drawn_cols": list(drawn.cols),
            "note": edge.note,
        })
      tables.append({
          "name": name,
          "pk": list(relations.pk),
          "identity": list(relations.identity),
          "enabled": relations.enabled,
          "note": relations.note,
          "fk": edges,
      })
    order: list[str] = []
    for name in model.tables:
      for table in registry.generation_order(registry.component(name)):
        if table not in order:
          order.append(table)
    models.append({
        "model": model.model,
        "description": model.description,
        "source": _rel(path),
        "sha12": registry.sha12(),
        "generation_order": order,
        "tables": tables,
    })
  return {
      "generated_by": "scripts/gui/export_knobs.py",
      "note": "The committed sample relationship models, parsed by "
              "sdfb_core.contracts.relationships. Real models are gitignored "
              "and never exported.",
      "models": models,
  }


# -------------------------------------------------------------- DLQ rules --


def _module_constants(tree: ast.Module) -> dict[str, Any]:
  out: dict[str, Any] = {}
  for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
        node.targets[0], ast.Name):
      try:
        out[node.targets[0].id] = ast.literal_eval(node.value)
      except ValueError:
        continue
  return out


def _dict_entry(node: ast.Dict, key: str) -> ast.expr | None:
  for k, v in zip(node.keys, node.values, strict=True):
    if isinstance(k, ast.Constant) and k.value == key:
      return v
  return None


def _emitted_rules() -> dict[str, dict[str, Any]]:
  """rule_id → (error_type, stage, path:line) from the DLQ envelopes the
  DoFns build (AST; no Beam import). An envelope that spreads a nested
  dict (`**safety`) lends its error_type to the rule_id inside it."""
  found: dict[str, dict[str, Any]] = {}
  for path in sorted(_DOFNS.glob("*.py")):
    tree = _tree(path)
    constants = _module_constants(tree)
    dicts = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)]
    spreads = [
        d for d in dicts if None in d.keys and _dict_entry(d, "error_type")
    ]
    for node in dicts:
      rule = _dict_entry(node, "rule_id")
      if rule is None:
        continue
      if isinstance(rule, ast.Constant) and isinstance(rule.value, str):
        rules = [rule.value]
      else:
        rules = [v for k, v in constants.items() if k.startswith("RULE_")]
      error_node = _dict_entry(node, "error_type")
      stage_node = _dict_entry(node, "stage")
      host = node if error_node is not None else (
          spreads[0] if spreads else None)
      if host is not None:
        error_node = _dict_entry(host, "error_type")
        stage_node = stage_node or _dict_entry(host, "stage")
      for rule_id in rules:
        found.setdefault(
            rule_id, {
                "error_type": getattr(error_node, "value", None),
                "stage": getattr(stage_node, "value", None),
                "emitted_by": _source(path, node.lineno),
            })
  return found


def build_dlq_rules() -> dict[str, Any]:
  """Every DLQ rule: what the code emits (error_type, stage, the step
  `normalize_dlq_record` assigns) and what thresholds.yml declares
  (severity, scope), and whether the BLOCKER gate counts it."""
  from sdfb_core.validation import dlq, summary

  declared = yaml.safe_load(_THRESHOLDS.read_text(encoding="utf-8"))["rules"]
  emitted = _emitted_rules()
  rules = []
  for rule_id in sorted(set(declared) | set(emitted)):
    spec = declared.get(rule_id) or {}
    code = emitted.get(rule_id)
    step = None
    if code:
      step = dlq.normalize_dlq_record(
          {
              "error_type": code["error_type"],
              "rule_id": rule_id
          },
          run_id="export")["pipeline_step"] or None
    rules.append({
        "rule_id": rule_id,
        "emitted": code is not None,
        "error_type": code["error_type"] if code else None,
        "pipeline_step": step,
        "stage": code["stage"] if code else None,
        "emitted_by": code["emitted_by"] if code else None,
        "declared": rule_id in declared,
        "severity": spec.get("severity"),
        "dimension": spec.get("dimension"),
        "scope": spec.get("scope", "in_dag") if rule_id in declared else None,
        "counted_in_blocker_gate": rule_id in summary.BLOCKER_RULE_IDS,
    })
  return {
      "generated_by": "scripts/gui/export_knobs.py",
      "note": "rule_id → what the pipeline code emits (sdfb_beam/dofns, "
              "sdfb_core/validation/dlq.py) and what config/thresholds.yml "
              "declares. post_run rules are scored offline, never DLQ'd.",
      "rules": rules,
  }


# ------------------------------------------------------------------ main --


def _exported_from(docs: Sequence[dict[str, Any]]) -> dict[str, Any]:
  """The commit the `path:LINE` references resolve at, and any referenced
  file with uncommitted changes (links then point one edit behind)."""
  paths = sorted({
      m.group("path") for doc in docs for m in (_SOURCE_REF.match(v)
                                                for v in _strings(doc)) if m
  })
  try:
    commit = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True).stdout.strip()
    status = subprocess.run(
        ["git", "-C",
         str(REPO), "status", "--porcelain", "--", *paths],
        capture_output=True,
        text=True,
        check=True).stdout
    dirty = sorted(line[3:] for line in status.splitlines() if line.strip())
  except (OSError, subprocess.CalledProcessError):
    commit, dirty = None, []
  return {"commit": commit, "dirty": dirty}


def _strings(value: Any):
  if isinstance(value, str):
    yield value
  elif isinstance(value, dict):
    for v in value.values():
      yield from _strings(v)
  elif isinstance(value, list):
    for v in value:
      yield from _strings(v)


def _tolerant(value: Any) -> Any:
  """The export without line numbers and provenance: what `--check` compares."""
  if isinstance(value, str):
    match = _SOURCE_REF.match(value)
    return match.group("path") if match else value
  if isinstance(value, dict):
    return {k: _tolerant(v) for k, v in value.items() if k != "exported_from"}
  if isinstance(value, list):
    return [_tolerant(v) for v in value]
  return value


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


def build_all() -> dict[str, dict[str, Any]]:
  """Every file this exporter owns, keyed by name; `exported_from` stamped."""
  docs = {
      "knobs.json": build(),
      "relationships.json": build_relationships(),
      "dlq_rules.json": build_dlq_rules(),
  }
  provenance = _exported_from(list(docs.values()))
  for doc in docs.values():
    doc["exported_from"] = provenance
  return docs


def render(doc: dict[str, Any]) -> str:
  return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
  mode = parser.add_mutually_exclusive_group()
  mode.add_argument(
      "--check",
      action="store_true",
      help="exit 1 when a value, id, anchor token or path differs from a "
      "fresh export (line numbers and the export commit may move)")
  mode.add_argument(
      "--check-strict",
      action="store_true",
      help="exit 1 on any difference, line numbers included")
  parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
  args = parser.parse_args(argv)
  drift = []
  for name, doc in build_all().items():
    text = render(doc)
    if CARD_LIKE.search(text):
      print(
          f"{name} would carry a card-number-shaped digit run", file=sys.stderr)
      return 1
    path = args.out_dir / name
    if args.check or args.check_strict:
      current = (
          json.loads(path.read_text(
              encoding="utf-8")) if path.is_file() else None)
      fresh = json.loads(text)
      if args.check_strict:
        same = current is not None and {
            k: v for k, v in current.items() if k != "exported_from"
        } == {
            k: v for k, v in fresh.items() if k != "exported_from"
        }
      else:
        same = current is not None and _tolerant(current) == _tolerant(fresh)
      if not same:
        drift.append(path)
      continue
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
  for path in drift:
    print(
        f"{path}: drift — re-run scripts/gui/export_knobs.py", file=sys.stderr)
  return 1 if drift else 0


if __name__ == "__main__":
  sys.exit(main())
