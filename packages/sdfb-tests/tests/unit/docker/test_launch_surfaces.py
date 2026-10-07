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
"""Launch surfaces expose the flags the CLI accepts (ADR 0024 §3c).

A Dataflow flex-template launch validates parameters against
``docker/flex_template_metadata.json`` and the Composer DAG only forwards
declared ``Param``s — a flag missing from either surface is unreachable in
production even when ``run_pipeline.py`` supports it. ``--prompt_debug``
shipped in ADR 0024 without either exposure, so the milestone it gates
(``freetext_pool_prompt``) could never be turned on from a real run.

The composer check is static (the DAG imports airflow, which is not a
laptop dependency) — same trade-off the file's other referencers accept.
The DAG is read with ``ast``, never line-matched, so reformatting it
(yapf, wrapped string literals) cannot break or fake these contracts.

One template serves two jobs (ADR 0041, amendment of 2026-10-06): the
metadata is the union of the generator's parameters, the selector
``sdfb_job`` and the evaluator's parameters, and every parameter is
optional in it, because a launch for one job cannot be made to supply the
other's::

    docker/flex_template_metadata.json
      = run_pipeline's flags (minus the undeclared ones)
      + the selector, sdfb_job
      + packages/sdfb-evaluation/deploy/flex_template_metadata.json
        (the three names both jobs use appear once)

What a generation launch must supply is therefore the CLI's to say
(``sdfb_tests.launch_surface.generator_required``), and the DAG's launch is
checked against that. The same DAG now holds a second launch operator
(``trigger_evaluation``, behind the opt-in gate); every helper here reads
the generation one, ``start_sdfb``, which must carry nothing of the
evaluation: with the gate off it is the whole default path.
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest
from sdfb_tests.launch_surface import generator_flags, generator_required

_REPO_ROOT = Path(__file__).parents[5]
_METADATA = _REPO_ROOT / "docker" / "flex_template_metadata.json"
_COMPOSER_DAG = _REPO_ROOT / "composer" / "synthetic_beam_bigquery.py"
_FLEX_ENTRY = _REPO_ROOT / "docker" / "flex_entry.py"
_EVALUATOR_METADATA = (
    _REPO_ROOT / "packages" / "sdfb-evaluation" / "deploy" /
    "flex_template_metadata.json")

# Flags the generator's CLI accepts and the template never declared: they
# stay reachable only from a direct launch (ADR 0024 §3c precedent).
_UNDECLARED = frozenset({"pool_pattern_guidance"})
# The names both jobs use. Each appears once in the template.
_SHARED = frozenset({"relationships_uri", "run_id", "thresholds_uri"})
_SELECTOR = "sdfb_job"
# Beam's own option, declared so a launch may pass it: both entries hand
# what they do not parse to Beam. The evaluation job runs on this same
# multi-GB image and its entry pins no boot disk (`run_pipeline` pins its
# own), so an evaluation launch has to say it.
_BEAM_PASSTHROUGH = frozenset({"disk_size_gb"})


def _metadata_param(name: str) -> dict:
  metadata = json.loads(_METADATA.read_text())
  by_name = {p["name"]: p for p in metadata["parameters"]}
  assert name in by_name, f"{name} missing from flex_template_metadata.json"
  return by_name[name]


def _dict_entries(node: ast.AST):
  """``(key, value_node)`` for every string-keyed entry of every dict
    literal under ``node`` (``**`` unpackings have no key and are skipped)."""
  for sub in ast.walk(node):
    if isinstance(sub, ast.Dict):
      for key, value in zip(sub.keys, sub.values, strict=True):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
          yield key.value, value


def _is_call_to(node: ast.AST, name: str) -> bool:
  return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
          node.func.id == name)


def _composer_params() -> dict[str, ast.Call]:
  """Every ``"name": Param(...)`` the DAG declares, by name."""
  tree = ast.parse(_COMPOSER_DAG.read_text())
  return {
      name: value
      for name, value in _dict_entries(tree)
      if _is_call_to(value, "Param")
  }


def _param_keyword(param: ast.Call, keyword: str):
  (value,) = [kw.value for kw in param.keywords if kw.arg == keyword]
  return ast.literal_eval(value)


def _composer_launch_operator() -> ast.Call:
  """The GENERATION launch (`start_sdfb`). The DAG holds a second launch
    operator, the opt-in evaluation's, so the task id picks this one."""
  tree = ast.parse(_COMPOSER_DAG.read_text())
  (operator,) = [
      node for node in ast.walk(tree)
      if _is_call_to(node, "DataflowStartFlexTemplateOperator") and
      _task_id(node) == "f'start_{app_name}'"
  ]
  return operator


def _task_id(operator: ast.Call) -> str:
  (task_id,) = [kw.value for kw in operator.keywords if kw.arg == "task_id"]
  return ast.unparse(task_id)


def _composer_forwarded() -> dict[str, str]:
  """The unconditional flex-template ``parameters`` the launch operator
    forwards, as ``name -> string literal``."""
  (parameters,) = [
      value for name, value in _dict_entries(_composer_launch_operator())
      if name == "parameters" and isinstance(value, ast.Dict)
  ]
  return {
      key.value: value.value
      for key, value in zip(parameters.keys, parameters.values, strict=True)
      if isinstance(key, ast.Constant) and isinstance(value, ast.Constant)
  }


def _composer_experiments() -> list[str]:
  """The launch operator's ``additionalExperiments`` string literals,
    whitespace-normalized (Jinja is whitespace-insensitive)."""
  (experiments,) = [
      value for name, value in _dict_entries(_composer_launch_operator())
      if name == "additionalExperiments" and isinstance(value, ast.List)
  ]
  return [
      " ".join(str(elt.value).split())
      for elt in experiments.elts
      if isinstance(elt, ast.Constant)
  ]


def test_flex_template_exposes_prompt_debug():
  param = _metadata_param("prompt_debug")
  assert param["isOptional"] is True
  assert param["regexes"] == ["^(off|redacted|full)?$"]


def test_flex_template_prompt_debug_matches_cli_choices():
  """The metadata regex and the argparse choices must not drift."""
  (regex,) = _metadata_param("prompt_debug")["regexes"]
  for choice in ("off", "redacted", "full", ""):
    assert re.fullmatch(regex, choice), choice
  assert not re.fullmatch(regex, "verbose")


def test_composer_dag_declares_and_forwards_prompt_debug():
  assert "prompt_debug" in _composer_params()
  assert _composer_forwarded()["prompt_debug"] == "{{ params.prompt_debug }}"


def test_uniqueness_mode_surfaces_accept_every_cli_mode():
  """ADR 0034 added `exact_chained`; the template regex, the Composer
    enum and argparse choices must agree or the mode is unreachable."""
  from sdfb_beam.dofns.uniqueness import UNIQUENESS_MODES

  (regex,) = _metadata_param("uniqueness_mode")["regexes"]
  for mode in (*UNIQUENESS_MODES, ""):
    assert re.fullmatch(regex, mode), mode
  dag_text = _COMPOSER_DAG.read_text()
  for mode in UNIQUENESS_MODES:
    assert f'"{mode}"' in dag_text, mode


def test_flex_template_and_composer_expose_initial_workers():
  """ADR 0034: the initial worker count is a per-trigger knob."""
  param = _metadata_param("initial_workers")
  assert param["isOptional"] is True
  (regex,) = param["regexes"]
  for ok in ("", "4", "16"):
    assert re.fullmatch(regex, ok), ok
  assert not re.fullmatch(regex, "four")
  assert "initial_workers" in _composer_params()
  assert (_composer_forwarded()["initial_workers"] ==
          "{{ params.initial_workers }}")


def test_composer_dag_exposes_sdk_containers_topology():
  """ADR 0034: `sdk_containers=multi` lifts `no_use_multiple_sdk_containers`
    for a vLLM launch; `single` (default) keeps the RUN_PLAYBOOK §3 pin."""
  params = _composer_params()
  assert "sdk_containers" in params
  assert _param_keyword(params["sdk_containers"], "enum") == ["single", "multi"]
  assert any("params.sdk_containers == 'single'" in experiment
             for experiment in _composer_experiments())


def test_flex_template_and_composer_expose_autoscaling():
  """ADR 0034 D9: auto (fixed when initial_workers is set) | throughput | fixed."""
  param = _metadata_param("autoscaling")
  assert param["isOptional"] is True
  (regex,) = param["regexes"]
  for ok in ("", "auto", "throughput", "fixed"):
    assert re.fullmatch(regex, ok), ok
  assert not re.fullmatch(regex, "none")
  assert "autoscaling" in _composer_params()
  assert _composer_forwarded()["autoscaling"] == "{{ params.autoscaling }}"


def test_flex_template_exposes_fk_candidate_cap():
  """ADR 0037: run_pipeline.py's `--fk_candidate_cap` (Top-M candidates
    per shared key on a conditional FK edge) had no flex-template metadata
    entry, so a UI/tiers.yaml launch could not discover or set it (the
    flag itself still passes through undeclared — ADR 0024 §3c
    precedent)."""
  param = _metadata_param("fk_candidate_cap")
  assert param["isOptional"] is True
  (regex,) = param["regexes"]
  for ok in ("", "1", "64", "1000"):
    assert re.fullmatch(regex, ok), ok
  assert not re.fullmatch(regex, "-1")
  assert not re.fullmatch(regex, "sixty-four")


def test_flex_template_exposes_fk_fanout_stats_table():
  """ADR 0036 cache table for the fan-out histogram; optional and may
    be empty (measure every launch, never cache)."""
  param = _metadata_param("fk_fanout_stats_table")
  assert param["isOptional"] is True
  (regex,) = param["regexes"]
  assert re.fullmatch(regex, "")
  assert re.fullmatch(regex, "proj.synthetic_data_quality.fk_fanout_stats")
  assert not re.fullmatch(regex, "not-a-fqn")


def test_flex_template_driven_uniqueness_mode_matches_cli_modes():
  """`--driven_uniqueness_mode` (ADR 0036) reuses the same UNIQUENESS_MODES
    choices as `--uniqueness_mode`; the metadata regex must not drift from
    them, same guard as `test_uniqueness_mode_surfaces_accept_every_cli_mode`."""
  from sdfb_beam.dofns.uniqueness import UNIQUENESS_MODES

  param = _metadata_param("driven_uniqueness_mode")
  assert param["isOptional"] is True
  (regex,) = param["regexes"]
  for mode in (*UNIQUENESS_MODES, ""):
    assert re.fullmatch(regex, mode), mode
  assert not re.fullmatch(regex, "verbose")


def test_flex_template_exposes_thresholds_uri():
  param = _metadata_param("thresholds_uri")
  assert param["isOptional"] is True


# --------------------------------------------------------------------------
# one template, two jobs
# --------------------------------------------------------------------------
def _parameters(path: Path = _METADATA) -> dict[str, dict]:
  parameters = json.loads(path.read_text(encoding="utf-8"))["parameters"]
  by_name = {p["name"]: p for p in parameters}
  assert len(by_name) == len(parameters), "a parameter is declared twice"
  return by_name


def _evaluator_parameters() -> dict[str, dict]:
  if not _EVALUATOR_METADATA.is_file():
    pytest.skip("packages/sdfb-evaluation is not part of this tree")
  return _parameters(_EVALUATOR_METADATA)


def test_the_template_is_the_generator_the_selector_and_the_evaluator():
  generator = generator_flags() - _UNDECLARED
  evaluator = set(_evaluator_parameters()) - {_SELECTOR}
  assert generator & evaluator == _SHARED
  assert evaluator >= _BEAM_PASSTHROUGH and not generator & _BEAM_PASSTHROUGH
  assert set(_parameters()) == generator | {_SELECTOR} | evaluator
  assert generator_flags() >= _UNDECLARED, "an undeclared flag is gone"


def test_every_template_parameter_is_optional():
  """A launch for one job cannot be made to supply the other's parameters;
    what each job needs is its own argument parser's to refuse."""
  required = [
      n for n, p in _parameters().items() if p.get("isOptional") is not True
  ]
  assert not required
  description = json.loads(_METADATA.read_text(encoding="utf-8"))["description"]
  assert "optional" in description and _SELECTOR in description


def test_what_the_generator_requires_is_said_where_the_template_cannot():
  required = generator_required()
  assert required == {
      "reference_table", "landing_table", "dlq_table", "num_rows", "run_id",
      "model_uri"
  }
  for name, parameter in _parameters().items():
    says_so = "required for generation" in parameter["helpText"].lower()
    assert says_so == (name in required), name


def test_the_generation_launch_supplies_every_flag_the_cli_requires():
  """The template used to refuse a launch without them; now only the
    launcher does, so the DAG's own launch is checked here."""
  (parameters,) = [
      value for name, value in _dict_entries(_composer_launch_operator())
      if name == "parameters" and isinstance(value, ast.Dict)
  ]
  passed = {
      key.value for key in parameters.keys if isinstance(key, ast.Constant)
  }
  assert generator_required() <= passed
  every = passed | {
      key for value in parameters.values for key, _ in _dict_entries(value)
  }
  assert every <= set(_parameters()), every - set(_parameters())


def test_evaluator_parameters_keep_their_own_regexes():
  """What the evaluator's own template would refuse or accept, this one
    does too: same pattern, or none where it has none."""
  template = _parameters()
  for name, parameter in _evaluator_parameters().items():
    assert template[name].get("regexes") == parameter.get("regexes"), name


def test_evaluator_only_parameters_say_so():
  template = _parameters()
  theirs = set(_evaluator_parameters()) - _SHARED - {_SELECTOR}
  for name in theirs - _BEAM_PASSTHROUGH:
    assert template[name]["helpText"].startswith("Evaluation only"), name
  for name in generator_flags() - _UNDECLARED - _SHARED:
    assert not template[name]["helpText"].startswith("Evaluation"), name


def test_a_shared_name_says_what_it_means_for_each_job():
  template = _parameters()
  for name in sorted(_SHARED):
    text = template[name]["helpText"]
    assert "Generation:" in text and "Evaluation:" in text, name
    # Both parsers take any text for these three (and Composer passes an
    # unset evaluation parameter as an empty string), so a pattern here
    # could only refuse a value one of the entries accepts.
    assert "regexes" not in template[name], name
  assert "different" in template["thresholds_uri"]["helpText"].lower()


def test_the_worker_disk_is_declared_for_both_jobs_and_never_empty():
  """`run_pipeline` keeps an explicit `--disk_size_gb` and otherwise pins
    its own; the evaluator pins none. The value goes to Beam, which reads
    an integer: the pattern refuses the empty string."""
  from apache_beam.options.pipeline_options import (
      PipelineOptions,
      WorkerOptions,
  )
  from sdfb_beam.cli import run_pipeline

  template = _parameters()
  for name in _BEAM_PASSTHROUGH:
    text = template[name]["helpText"]
    assert "Generation:" in text and "Evaluation:" in text, name
  (pattern,) = template["disk_size_gb"]["regexes"]
  assert re.fullmatch(pattern, "200") and not re.fullmatch(pattern, "")
  # the number an evaluation launch on this image passes is the generator's
  pinned = run_pipeline._DEFAULT_WORKER_DISK_GB  # pylint: disable=protected-access
  assert str(pinned) in template["disk_size_gb"]["helpText"]
  explicit = PipelineOptions(["--disk_size_gb=64"])
  run_pipeline.configure_pipeline_options(explicit, "DataflowRunner", "r1")
  assert explicit.view_as(WorkerOptions).disk_size_gb == 64


def test_the_selector_admits_exactly_the_dispatchers_two_jobs():
  spec = importlib.util.spec_from_file_location("sdfb_flex_entry", _FLEX_ENTRY)
  entry = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(entry)
  selector = _parameters()[_SELECTOR]
  assert entry.SELECTOR.removeprefix("--") == _SELECTOR
  (pattern,) = selector["regexes"]
  for value in (entry.GENERATION, entry.EVALUATION, ""):
    assert re.fullmatch(pattern, value), value
    assert entry.split_selector([f"{entry.SELECTOR}={value}"])[0] == (
        value or entry.GENERATION)
  for value in ("both", "Generation", "evaluation "):
    assert not re.fullmatch(pattern, value), value


def test_every_template_parameter_is_well_formed():
  for name, parameter in _parameters().items():
    assert re.fullmatch(r"[a-z][a-z0-9_]*", name), name
    assert parameter["label"] and parameter["helpText"], name
    for pattern in parameter.get("regexes", []):
      re.compile(pattern)


# --------------------------------------------------------------------------
# the default path knows nothing of the evaluation
# --------------------------------------------------------------------------
def test_the_generation_launch_knows_nothing_of_the_evaluation():
  """With `run_evaluation` False the DAG runs this task and skips the
    rest, so the default path is this call alone. It names no evaluation
    parameter and passes no job selector, so the image's entry runs
    generation. This holds for whatever launch a deployment's own DAG
    makes, which a pin of this repository's call could not."""
  launch = ast.unparse(_composer_launch_operator())
  assert _SELECTOR not in launch
  assert "evaluation" not in launch
