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
"""A cache built under one value is never served under another (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from sdfb_core.config.catalogue import CACHES
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.config.salt import salted, settings_salt


def test_defaults_leave_every_cache_key_unchanged():
  for cache in CACHES:
    assert settings_salt(DEFAULT_CONFIG, cache) == ""
  assert salted("v1", "") == "v1"


def test_a_file_restating_a_default_is_still_unsalted():
  cfg = GeneratorConfig.model_validate({
      "rag": {
          "rag_top_k": 8
      },
      "free_text": {
          "pool_stagnation_min_novel": 4
      }
  })
  assert settings_salt(cfg, "freetext_pools") == ""


def test_a_changed_value_salts_only_the_caches_it_shapes():
  cfg = GeneratorConfig.model_validate({"rag": {"bge_max_length": 256}})
  assert len(settings_salt(cfg, "rag_chunks")) == 12
  assert settings_salt(cfg, "freetext_pools") == ""
  assert settings_salt(cfg, "source_stats") == ""
  assert salted("v1", settings_salt(cfg, "rag_chunks")).startswith("v1+cfg.")


def test_salt_is_deterministic_and_value_sensitive():
  a = GeneratorConfig.model_validate({"rag": {"rag_top_k": 4}})
  b = GeneratorConfig.model_validate({"rag": {"rag_top_k": 4}})
  c = GeneratorConfig.model_validate({"rag": {"rag_top_k": 5}})
  assert settings_salt(a,
                       "freetext_pools") == settings_salt(b, "freetext_pools")
  assert settings_salt(a,
                       "freetext_pools") != settings_salt(c, "freetext_pools")
