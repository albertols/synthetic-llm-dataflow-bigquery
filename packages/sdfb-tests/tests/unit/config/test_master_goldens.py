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
"""The generator behaves exactly as on master (spec AC-A1-1), but for the
seven deliberate changes `config_parity.DELIBERATE_CHANGES` lists.

The goldens are master's and never edited: each comparison applies the
table to the golden first.
"""
from __future__ import annotations

import pytest
from sdfb_core.config.model import (CONSTANT_KEYS, DEFAULT_CONFIG,
                                    DEFAULT_VALUES, value_of)
from sdfb_tests import config_parity as cp

_SEVEN = {
    "generation.num_rows", "rag.build_rag_layer", "free_text.build_pool_layer",
    "serving.vllm_dtype", "serving.gpu", "serving.sdk_containers",
    "serving.launcher_machine_type"
}


def test_master_goldens_differ_only_by_deliberate_changes():
  assert set(cp.DELIBERATE_CHANGES) == _SEVEN
  assert cp.observe_namespaces() == cp.master_golden("namespaces")
  constants = cp.master_golden("constants")
  assert {
      knob:
          cp.jsonable(
              value_of(DEFAULT_CONFIG,
                       next(k
                            for k in CONSTANT_KEYS
                            if k.endswith(f".{knob}")))) for knob in constants
  } == constants


def test_every_deliberate_change_is_the_yaml_default_and_changes_a_golden():
  for dotted, (master, now) in cp.DELIBERATE_CHANGES.items():
    section, key = dotted.split(".", 1)
    assert DEFAULT_VALUES[section][key] == now, dotted
    assert master != now, dotted
  assert cp.master_golden("namespaces") != cp.raw_golden("namespaces")
  assert cp.master_golden("parser") != cp.raw_golden("parser")


def test_the_changes_touch_no_constant_or_pipeline_golden():
  for name in ("constants", "pipelines"):
    assert cp.master_golden(name) == cp.raw_golden(name)


def test_constants_golden_is_complete():
  assert set(cp.raw_golden("constants")) == set(cp.CONSTANT_SOURCES)


@pytest.mark.slow
def test_pipelines_match_master(tmp_path):
  assert cp.observe_pipelines(tmp_path) == cp.master_golden("pipelines")
