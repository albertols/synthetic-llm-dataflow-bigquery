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
"""`GeneratorConfig`: one typed home per setting, defaults equal to master.

The model is specified by ADR 0043.

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import json
import types
import typing

import pytest
from pydantic import ValidationError
from sdfb_core.config.model import (CONSTANT_KEYS, DEFAULT_CONFIG, FLAG_KEYS,
                                    IDENTITY_KEYS, REQUIRED_FLAGS,
                                    SECTION_TYPES, GeneratorConfig, value_of)
from sdfb_tests import config_parity as cp

# Master's parser, with the deliberate changes applied (num_rows not
# required, the new defaults, the serving launch keys added).
_PARSER = cp.master_golden("parser")
_CONSTANTS = json.loads((cp.GOLDENS_DIR / "constants.json").read_text())

# ADR 0045 — the whole-model launch keys, new since master.
_MODEL_LAUNCH_FLAGS = {
    "source_dataset", "landing_dataset", "tables", "max_tables_per_job",
    "max_parallel_jobs", "prep_workers"
}
_MODEL_LAUNCH_IDENTITY = {"batch_index", "batch_count"}


def test_every_master_flag_has_one_field():
  master = set(_PARSER) - {"help"}
  assert set(FLAG_KEYS) | set(IDENTITY_KEYS) == (
      master | _MODEL_LAUNCH_FLAGS | _MODEL_LAUNCH_IDENTITY)
  assert len(FLAG_KEYS) == 55
  assert set(IDENTITY_KEYS) == {"run_id"} | _MODEL_LAUNCH_IDENTITY


@pytest.mark.parametrize("dest", sorted(set(_PARSER) - {"help"}))
def test_flag_default_and_choices_match_master(dest):
  dotted = FLAG_KEYS.get(dest) or IDENTITY_KEYS[dest]
  section, key = dotted.split(".")
  field = SECTION_TYPES[section].model_fields[key]
  master = _PARSER[dest]
  expected_default = None if master["required"] else master["default"]
  assert cp.jsonable(value_of(DEFAULT_CONFIG, dotted)) == expected_default
  if master["choices"] is not None:
    assert _choices(field.annotation) == master["choices"]


def _choices(annotation) -> list:
  """A Literal's values; an optional Literal's (`vllm_dtype`) without None."""
  if isinstance(annotation, types.UnionType):
    (annotation,) = [a for a in annotation.__args__ if a is not types.NoneType]
  assert typing.get_origin(annotation) is typing.Literal, annotation
  return list(annotation.__args__)


def test_required_flags_match_master():
  assert set(REQUIRED_FLAGS) == {d for d, a in _PARSER.items() if a["required"]}


@pytest.mark.parametrize("knob", sorted(_CONSTANTS))
def test_constant_default_equals_master(knob):
  dotted = next(k for k in CONSTANT_KEYS if k.split(".")[1] == knob)
  assert cp.jsonable(value_of(DEFAULT_CONFIG, dotted)) == _CONSTANTS[knob]


def test_yaml_native_values_encode_like_argparse():
  cfg = GeneratorConfig.model_validate({
      "io": {
          "create_if_not_exists": True
      },
      "rag": {
          "build_rag_layer": False
      },
      "relational": {
          "generate_fk_relationships": False,
          "pk_cols": ["a", "b"]
      },
      "serving": {
          "vllm_max_model_len": 4096,
          "initial_workers": None
      },
      "generation": {
          "seed": 42
      },
  })
  assert cfg.io.create_if_not_exists == "true"
  assert cfg.rag.build_rag_layer == ""
  assert cfg.relational.generate_fk_relationships == "false"
  assert cfg.relational.pk_cols == "a,b"
  assert cfg.serving.vllm_max_model_len == "4096"
  assert cfg.serving.initial_workers == ""
  assert cfg.generation.seed == "42"


def test_unknown_key_and_frozen():
  with pytest.raises(ValidationError):
    GeneratorConfig.model_validate({"generation": {"similarty": 0.1}})
  with pytest.raises(ValidationError):
    DEFAULT_CONFIG.generation.similarity = 0.9  # type: ignore[misc]


def test_constant_constraints():
  with pytest.raises(ValidationError):
    GeneratorConfig.model_validate(
        {"sampling": {
            "length_percentiles": [0.5, 0.2, 0.9]
        }})
  with pytest.raises(ValidationError):
    GeneratorConfig.model_validate(
        {"relational": {
            "fk_key_sample_floor": 10,
            "fk_key_sample_ceiling": 5
        }})
  with pytest.raises(ValidationError):
    GeneratorConfig.model_validate({"free_text": {"pool_values_per_call": 0}})


def test_min_novel_follows_per_call_unless_set():
  assert value_of(DEFAULT_CONFIG, "free_text.pool_stagnation_min_novel") == 4
  cfg = GeneratorConfig.model_validate(
      {"free_text": {
          "pool_values_per_call": 64
      }})
  assert value_of(cfg, "free_text.pool_stagnation_min_novel") == 8
  cfg = GeneratorConfig.model_validate(
      {"free_text": {
          "pool_stagnation_min_novel": 2
      }})
  assert value_of(cfg, "free_text.pool_stagnation_min_novel") == 2


# YAML 1.1 reads an unquoted on/off as a boolean.
_OFF_CHOICES = (
    ("sampling", "source_stats"),
    ("free_text", "prompt_debug"),
    ("free_text", "freetext_expansion"),
    ("free_text", "prompt_constraints"),
)


@pytest.mark.parametrize("section, key", _OFF_CHOICES)
def test_yaml_off_is_the_off_choice(section, key):
  cfg = GeneratorConfig.model_validate({section: {key: False}})
  assert value_of(cfg, f"{section}.{key}") == "off"


def test_yaml_on_is_a_choice_only_for_prompt_constraints():
  cfg = GeneratorConfig.model_validate(
      {"free_text": {
          "prompt_constraints": True
      }})
  assert cfg.free_text.prompt_constraints == "on"
  for section, key in _OFF_CHOICES[:3]:
    with pytest.raises(ValidationError):
      GeneratorConfig.model_validate({section: {key: True}})


def test_landing_table_takes_a_yaml_list():
  cfg = GeneratorConfig.model_validate(
      {"io": {
          "landing_table": ["p.land.a", "p.land.b"]
      }})
  assert cfg.io.landing_table == "p.land.a,p.land.b"
  assert DEFAULT_CONFIG.io.landing_table is None


def test_literal_top_values_stay_within_the_enum_cap():
  with pytest.raises(
      ValidationError,
      match=r"sampling\.top_values_max_distinct.*"
      r"free_text\.free_text_max_categories"):
    GeneratorConfig.model_validate(
        {"sampling": {
            "top_values_max_distinct": 51
        }})
  lower = GeneratorConfig.model_validate(
      {"sampling": {
          "top_values_max_distinct": 20
      }})
  assert lower.sampling.top_values_max_distinct == 20
  both = GeneratorConfig.model_validate({
      "sampling": {
          "top_values_max_distinct": 80
      },
      "free_text": {
          "free_text_max_categories": 80
      }
  })
  assert both.sampling.top_values_max_distinct == 80
