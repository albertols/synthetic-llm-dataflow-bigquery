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
"""The generator behaves exactly as on master (spec AC-A1-1).
"""
from __future__ import annotations

import json

import pytest
from sdfb_tests import config_parity as cp


def _golden(name: str) -> dict:
  return json.loads((cp.GOLDENS_DIR / f"{name}.json").read_text())


def test_namespaces_match_master():
  assert cp.observe_namespaces() == _golden("namespaces")


def test_constants_golden_is_complete():
  assert set(_golden("constants")) == set(cp.CONSTANT_SOURCES)


@pytest.mark.slow
def test_pipelines_match_master(tmp_path):
  assert cp.observe_pipelines(tmp_path) == _golden("pipelines")
