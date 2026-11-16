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
"""AC-B-4: an explicit launch over a model without overrides plans exactly
as before ADR 0045 — tables, order, sources, run ids, parents, warnings.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import json

from sdfb_beam.cli import run_pipeline as rp
from sdfb_tests import launch_plans as lp


def test_the_explicit_plans_match_the_golden():
  assert lp.observe_launch_plans() == json.loads(
      lp.GOLDEN.read_text(encoding="utf-8"))


def test_no_existing_model_plans_a_row_override():
  for model, targets, reference, fk in lp.PLAN_CASES.values():
    plan = rp.plan_launch(
        list(targets), reference, fk, lp._registry(model), "r1")  # pylint: disable=protected-access
    assert all(r.num_rows is None for r in plan.runs)
