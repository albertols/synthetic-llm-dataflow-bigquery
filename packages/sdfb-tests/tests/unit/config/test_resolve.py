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
"""File > flag > default, with provenance and named errors (ADR 0043).

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import re

import pytest
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.config.resolve import (ConfigError, missing_required,
                                      namespace_values, parse_config_text,
                                      resolve)


def test_defaults_only():
  r = resolve({})
  assert r.config == DEFAULT_CONFIG
  assert set(r.provenance.values()) == {"default"}
  assert not r.overridden


def test_flag_beats_default():
  r = resolve({"similarity": 0.8})
  assert r.config.generation.similarity == 0.8
  assert r.provenance["generation.similarity"] == "flag"


def test_file_beats_flag_and_records_the_override():
  file = parse_config_text("generation:\n  similarity: 0.7\n", source="t.yaml")
  r = resolve({"similarity": 0.3}, file, config_uri="t.yaml")
  assert r.config.generation.similarity == 0.7
  assert r.provenance["generation.similarity"] == "file"
  assert [(o.key, o.typed, o.file) for o in r.overridden
         ] == [("generation.similarity", 0.3, 0.7)]


def test_same_value_typed_and_in_file_is_not_an_override():
  file = parse_config_text("generation:\n  similarity: 0.7\n", source="t.yaml")
  assert not resolve({"similarity": 0.7}, file).overridden


def test_file_sets_a_constant():
  file = parse_config_text(
      "free_text:\n  pool_values_per_call: 64\n", source="t")
  r = resolve({}, file)
  assert r.config.free_text.pool_values_per_call == 64
  assert r.provenance["free_text.pool_values_per_call"] == "file"


def test_empty_file_and_empty_section():
  assert not parse_config_text("", source="t")
  assert parse_config_text("rag:\n", source="t") == {"rag": {}}


@pytest.mark.parametrize("text, message", [
    ("generation:\n  similarty: 0.1\n",
     "unknown key 'generation.similarty' — did you mean 'generation.similarity'"
    ),
    ("generaton:\n  similarity: 0.1\n",
     "unknown section 'generaton' — did you mean 'generation'"),
    ("launch:\n  run_id: x\n",
     "launch.run_id is launch identity — pass it as a flag"),
    ("generation:\n  temperature_ladder: [1]\n",
     "generation.temperature_ladder is not settable"),
    ("config_version: 2\n", "understands config_version <= 1"),
    ("- a\n- b\n", "top level must be a mapping"),
    ("generation: [1]\n", "section 'generation' must be a mapping"),
    ("generation:\n  similarity: : :\n", "not valid YAML"),
])
def test_parse_errors(text, message):
  with pytest.raises(ConfigError, match=message):
    parse_config_text(text, source="t.yaml")


def test_type_errors_name_the_key():
  file = parse_config_text("generation:\n  similarity: high\n", source="t")
  with pytest.raises(ConfigError, match=r"generation\.similarity"):
    resolve({}, file, config_uri="t.yaml")


def test_missing_required_in_argparse_order():
  assert missing_required(resolve({}).config) == [
      "reference_table", "landing_table", "dlq_table", "run_id", "model_uri"
  ]
  assert missing_required(resolve({
      "run_id": "r"
  }).config) == ["reference_table", "landing_table", "dlq_table", "model_uri"]


def test_namespace_values_cover_every_flag():
  values = namespace_values(resolve({"run_id": "r1"}))
  assert values["run_id"] == "r1"
  assert values["build_rag_layer"] == "true"
  assert values["vllm_max_model_len"] == "8192"
  # master's 47 + the eight ADR 0045 keys + the three serving launch keys
  assert len(values) == 58


def test_a_file_null_on_a_null_default_leaves_the_key_to_the_flag():
  # The reference shows required keys as null: copying its io block and
  # typing the flag must not leave the key unset.
  file = parse_config_text("io:\n  reference_table: null\n", source="t")
  r = resolve({"reference_table": "p.s.t"}, file)
  assert r.config.io.reference_table == "p.s.t"
  assert r.provenance["io.reference_table"] == "flag"
  assert not r.overridden


def test_a_file_null_on_a_derived_default_is_the_default():
  file = parse_config_text(
      "free_text:\n  pool_stagnation_min_novel: null\n", source="t")
  r = resolve({}, file)
  assert r.provenance["free_text.pool_stagnation_min_novel"] == "default"
  assert r.config == DEFAULT_CONFIG


def test_a_file_null_on_a_set_default_still_wins():
  file = parse_config_text("serving:\n  vllm_max_model_len: null\n", source="t")
  r = resolve({"vllm_max_model_len": "4096"}, file)
  assert r.config.serving.vllm_max_model_len == ""
  assert r.provenance["serving.vllm_max_model_len"] == "file"


def test_a_flat_key_names_its_section():
  with pytest.raises(
      ConfigError,
      match=re.escape("unknown section 'similarity' — did you mean "
                      "'generation.similarity' (keys live under their "
                      "section)?")):
    parse_config_text("similarity: 0.7\n", source="t.yaml")


def test_bge_max_length_above_the_checkpoint_is_refused():
  file = parse_config_text("rag:\n  bge_max_length: 513\n", source="t")
  with pytest.raises(ConfigError, match=r"rag\.bge_max_length"):
    resolve({}, file, config_uri="t.yaml")
  file = parse_config_text("rag:\n  bge_max_length: 512\n", source="t")
  assert resolve({}, file).config.rag.bge_max_length == 512


def test_literal_top_values_above_the_enum_cap_are_refused():
  file = parse_config_text(
      "sampling:\n  top_values_max_distinct: 60\n", source="t")
  with pytest.raises(ConfigError) as e:
    resolve({}, file, config_uri="t.yaml")
  message = str(e.value)
  assert message.startswith("t.yaml: ")
  assert ": : " not in message
  assert "sampling.top_values_max_distinct (60)" in message
  assert "free_text.free_text_max_categories (50)" in message


def test_a_model_launch_needs_no_reference_or_landing_table():
  """ADR 0045: with launch.tables all the model names every table."""
  config = GeneratorConfig.model_validate({"launch": {"tables": "all"}})
  assert missing_required(config) == ["dlq_table", "run_id", "model_uri"]
  assert missing_required(
      GeneratorConfig())[:2] == ["reference_table", "landing_table"]
