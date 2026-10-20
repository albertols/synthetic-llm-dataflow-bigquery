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

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sdfb_core.config.model import (CONSTANT_KEYS, DEFAULT_CONFIG, FLAG_KEYS,
                                    IDENTITY_KEYS, REQUIRED_FLAGS,
                                    SECTION_TYPES, GeneratorConfig, value_of)
from sdfb_tests import config_parity as cp

_PARSER = json.loads((cp.GOLDENS_DIR / "parser.json").read_text())
_CONSTANTS = json.loads((cp.GOLDENS_DIR / "constants.json").read_text())


def test_every_master_flag_has_one_field():
  master = set(_PARSER) - {"help"}
  assert set(FLAG_KEYS) | set(IDENTITY_KEYS) == master
  assert len(FLAG_KEYS) == 46 and set(IDENTITY_KEYS) == {"run_id"}


@pytest.mark.parametrize("dest", sorted(set(_PARSER) - {"help"}))
def test_flag_default_and_choices_match_master(dest):
  dotted = FLAG_KEYS.get(dest) or IDENTITY_KEYS[dest]
  section, key = dotted.split(".")
  field = SECTION_TYPES[section].model_fields[key]
  master = _PARSER[dest]
  expected_default = None if master["required"] else master["default"]
  assert cp.jsonable(value_of(DEFAULT_CONFIG, dotted)) == expected_default
  if master["choices"] is not None:
    assert list(field.annotation.__args__) == master["choices"]


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
