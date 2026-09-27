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
"""Pytest config + shared fixtures.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
  """A loader for JSON fixtures kept under `tests/fixtures/`.

  Returns a function taking a path relative to `tests/fixtures/` (e.g.
  `"parity/goldens.json"`) and returning its parsed JSON content. Fixtures
  are `.json` only (repo-wide data-hygiene convention).
  """

  def _load(relative_path: str) -> Any:
    return json.loads((_FIXTURES / relative_path).read_text(encoding="utf-8"))

  return _load
