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
"""Unit tests for the billing killswitch decision logic (pure python)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
LOGIC = REPO_ROOT / "public_cloud" / "deploy" / "gcp" / "killswitch" / "logic.py"

spec = importlib.util.spec_from_file_location("killswitch_logic", LOGIC)
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)


def test_kills_at_or_above_threshold():
  assert logic.should_kill({
      "costAmount": 22.5,
      "budgetAmount": 25.0
  }, 0.9) is True
  assert logic.should_kill({
      "costAmount": 25.0,
      "budgetAmount": 25.0
  }, 0.9) is True


def test_does_not_kill_below_threshold():
  assert logic.should_kill({
      "costAmount": 12.0,
      "budgetAmount": 25.0
  }, 0.9) is False


def test_defensive_on_malformed_payload():
  assert logic.should_kill({}, 0.9) is False
  assert logic.should_kill({"budgetAmount": 0},
                           0.9) is False  # div-by-zero guard
  assert logic.should_kill({
      "costAmount": None,
      "budgetAmount": 25.0
  }, 0.9) is False
