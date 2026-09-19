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
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import ast
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[5]
_METADATA = _REPO_ROOT / "docker" / "flex_template_metadata.json"
_COMPOSER_DAG = _REPO_ROOT / "composer" / "synthetic_beam_bigquery.py"


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
  tree = ast.parse(_COMPOSER_DAG.read_text())
  (operator,) = [
      node for node in ast.walk(tree)
      if _is_call_to(node, "DataflowStartFlexTemplateOperator")
  ]
  return operator


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
  import re

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
  import re

  from sdfb_beam.dofns.uniqueness import UNIQUENESS_MODES

  (regex,) = _metadata_param("uniqueness_mode")["regexes"]
  for mode in (*UNIQUENESS_MODES, ""):
    assert re.fullmatch(regex, mode), mode
  dag_text = _COMPOSER_DAG.read_text()
  for mode in UNIQUENESS_MODES:
    assert f'"{mode}"' in dag_text, mode


def test_flex_template_and_composer_expose_initial_workers():
  """ADR 0034: the initial worker count is a per-trigger knob."""
  import re

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
  import re

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
  import re

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
  import re

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
  import re

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
