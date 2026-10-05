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
"""Fixtures of the CLI tests.

The packaged catalogue is immutable, and parsing its YAML takes about a
quarter of a second; a CLI run asks for it several times (the planner,
the registry rows, the gate, the renderers). `cached_catalogue` parses
it once for the whole session, which keeps these tests inside their
60-second budget (Ruling R88i).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import functools

import pytest

from sdfb_evaluation.beam import assemble
from sdfb_evaluation.catalogue import Catalogue, load_catalogue
from sdfb_evaluation.cli import driver, fixture, gate, main
from sdfb_evaluation.context import plan
from sdfb_evaluation.report import render

_MODULES = (assemble, driver, fixture, gate, main, plan, render)


@functools.cache
def catalogue() -> Catalogue:
  """The packaged catalogue, parsed once."""
  return load_catalogue()


@pytest.fixture(autouse=True)
def cached_catalogue(monkeypatch) -> Catalogue:
  """Every module the CLI tests drive loads the session's one parse."""
  for module in _MODULES:
    monkeypatch.setattr(module, "load_catalogue", catalogue)
  return catalogue()
