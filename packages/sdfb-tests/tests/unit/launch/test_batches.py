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
"""The batch planner of a whole-model launch (ADR 0045): deterministic,
first-fit-decreasing, never splits a connected component.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.launch.batches import (ModelLaunchError, batch_label,
                                      batch_run_id, launch_banner, plan_batches,
                                      plan_model_launch, select_tables)
from sdfb_tests.model_fixtures import forest_model_text

_ROOT = Path(__file__).resolve().parents[5]


def _registry(text: str) -> RelationshipRegistry:
  return RelationshipRegistry.from_sources([("m.yaml", text)])


def test_the_spec_forest_packs_into_twelve_batches():
  launch = plan_model_launch(_registry(forest_model_text()), 10)
  assert len(launch.selected) == 112 and len(launch.skipped) == 8
  assert [len(b.tables) for b in launch.batches] == [10] * 11 + [2]
  assert [b.label for b in launch.batches] == [f"b{i:02d}" for i in range(12)]
  assert launch.batches[0].tables == tuple(f"T_{i:04d}" for i in range(10))
  assert launch.skipped == tuple(f"T_{i:04d}" for i in range(112, 120))


@pytest.mark.parametrize("k", [1, 3, 7, 10, 50])
def test_the_plan_depends_only_on_the_content_and_k(k):
  shape = {"enabled": 30, "disabled": 4, "chains": (4, 3, 12)}
  forward = plan_model_launch(_registry(forest_model_text(**shape)), k)
  backward = plan_model_launch(
      _registry(forest_model_text(**shape, reverse=True)), k)
  assert [b.tables for b in forward.batches
         ] == [b.tables for b in backward.batches]


@pytest.mark.parametrize("k", [1, 2, 3, 4, 5, 10])
def test_a_component_is_never_split_and_every_table_runs_once(k):
  registry = _registry(
      forest_model_text(enabled=7, disabled=2, chains=(4, 3, 2)))
  launch = plan_model_launch(registry, k)
  where: dict[str, int] = {}
  for batch in launch.batches:
    for table in batch.tables:
      assert table not in where, table
      where[table] = batch.index
  assert sorted(where) == sorted(launch.selected)
  for table, index in where.items():
    assert {where[m] for m in registry.component(table)} == {index}
  for batch in launch.batches:
    assert len(batch.tables) <= k or len(batch.components) == 1


def test_a_component_larger_than_k_gets_its_own_batch():
  launch = plan_model_launch(
      _registry(forest_model_text(enabled=3, disabled=0, chains=(5,))), 3)
  (big,) = [b for b in launch.batches if b.oversized]
  assert big.tables == ("C0_0", "C0_1", "C0_2", "C0_3", "C0_4")
  assert big.components == (big.tables,)
  assert all(len(b.tables) <= 3 for b in launch.batches if b is not big)


_FIGURE = """model: figure
tables:
  T_ORDERS: {pk: [O]}
  T_ITEMS:
    fk:
      - {cols: [O], ref: T_ORDERS, ref_cols: [O]}
  T_SHIPMENTS:
    fk:
      - {cols: [O], ref: T_ORDERS, ref_cols: [O]}
  T_ACCOUNTS: {pk: [A]}
  T_LOGINS:
    fk:
      - {cols: [A], ref: T_ACCOUNTS, ref_cols: [A]}
  T_EVENTS_LOG: {}
  T_RATES: {}
  T_LEGACY: {enabled: false}
"""


def test_first_fit_decreasing_on_the_adr_figure():
  """ADR 0045 Figure M3: components sized 3, 2, 1, 1 into K=4 — the 3 and a
    1 share a bin, the 2 and the other 1 the next; bins are named by their
    first table."""
  launch = plan_model_launch(_registry(_FIGURE), 4)
  assert [b.tables for b in launch.batches] == [
      ("T_ACCOUNTS", "T_LOGINS", "T_RATES"),
      ("T_EVENTS_LOG", "T_ITEMS", "T_ORDERS", "T_SHIPMENTS"),
  ]
  assert launch.skipped == ("T_LEGACY",)


def test_a_disabled_table_detaches_its_branch():
  text = ("model: m\ntables:\n  T_P: {pk: [P]}\n  T_M:\n    enabled: false\n"
          "    fk:\n      - {cols: [P], ref: T_P, ref_cols: [P]}\n  T_C:\n"
          "    fk:\n      - {cols: [M], ref: T_M, ref_cols: [M]}\n")
  launch = plan_model_launch(_registry(text), 1)
  assert [b.tables for b in launch.batches] == [("T_C",), ("T_P",)]
  assert launch.skipped == ("T_M",)


def test_the_isolated_forest_example_selects_exactly_the_enabled_tables():
  """AC-B-1."""
  path = _ROOT / "config" / "relationships" / "example_isolated_forest.yaml"
  registry = RelationshipRegistry.from_sources([(str(path), path.read_text())])
  selected, skipped = select_tables(registry)
  assert selected == ("T_ACCOUNTS", "T_EVENTS_LOG", "T_RATES")
  assert skipped == ("T_LEGACY",)
  launch = plan_model_launch(registry, 10)
  assert [b.tables for b in launch.batches] == [selected]


def test_a_launch_needs_exactly_one_model():
  two = RelationshipRegistry.from_sources([
      ("a.yaml", "model: a\ntables:\n  T_A: {}\n"),
      ("b.yaml", "model: b\ntables:\n  T_B: {}\n"),
  ])
  with pytest.raises(ModelLaunchError, match="exactly one model"):
    plan_model_launch(two, 10)
  with pytest.raises(ModelLaunchError, match="exactly one model"):
    plan_model_launch(RelationshipRegistry(), 10)


def test_a_model_that_enables_no_table_is_refused():
  text = "model: m\ntables:\n  T_A: {enabled: false}\n"
  with pytest.raises(ModelLaunchError, match="enables no table"):
    plan_model_launch(_registry(text), 10)


def test_k_below_one_is_refused():
  registry = _registry(forest_model_text(enabled=2, disabled=0))
  with pytest.raises(ModelLaunchError, match="max_tables_per_job"):
    plan_batches(registry, select_tables(registry)[0], 0)


def test_labels_and_run_ids():
  assert batch_label(0) == "b00" and batch_label(12) == "b12"
  assert batch_run_id("launch-1", 3) == "launch-1-b03"


def test_the_banner_states_the_whole_plan():
  """A 3-table chain and four singletons at K=2: the chain is its own
    (oversized) bin, the singletons pair up, and the bins are named by their
    first table — C0_0 < T_0000 < T_0002."""
  launch = plan_model_launch(
      _registry(forest_model_text(enabled=4, disabled=1, chains=(3,))), 2)
  assert launch_banner(launch).splitlines() == [
      "MODEL LAUNCH generated_forest | source m.yaml | 7 tables enabled · "
      "1 disabled · 3 batch job(s) of <= 2 tables",
      "  b00 |   3 tables | C0_0, C0_1, C0_2   [OVERSIZED — one component "
      "of 3 tables]",
      "  b01 |   2 tables | T_0000, T_0001",
      "  b02 |   2 tables | T_0002, T_0003",
      "  skipped (enabled: false): T_0004",
  ]
