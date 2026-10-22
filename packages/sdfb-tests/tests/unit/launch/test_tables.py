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
"""Per-table resolution and the `launch.tables` rules (ADR 0045).

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import pytest
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.contracts.relationships import TableRelations
from sdfb_core.launch.tables import (model_launch_problems, source_fqn,
                                     table_rows)


def test_without_an_override_a_source_is_the_dataset_and_the_name():
  assert source_fqn("T_A", None, "p.src") == "p.src.T_A"
  assert source_fqn("p.land.T_A", TableRelations(), "p.src") == "p.src.T_A"


def test_a_two_part_override_takes_the_datasets_project():
  rel = TableRelations(source_table="other_ds.T_A_V2")
  assert source_fqn("p.land.T_A", rel, "p.src") == "p.other_ds.T_A_V2"


def test_a_three_part_override_is_kept():
  rel = TableRelations(source_table="p2.other_ds.T_A_V2")
  assert source_fqn("T_A", rel, "p.src") == "p2.other_ds.T_A_V2"


def test_without_a_project_a_two_part_override_is_kept_as_written():
  rel = TableRelations(source_table="other_ds.T_A_V2")
  assert source_fqn("T_A", rel, "src") == "other_ds.T_A_V2"


def test_table_rows():
  assert table_rows(None) is None
  assert table_rows(TableRelations()) is None
  assert table_rows(TableRelations(num_rows=7)) == 7


def _all(**sections) -> GeneratorConfig:
  base = {
      "launch": {
          "tables": "all"
      },
      "io": {
          "source_dataset": "p.src",
          "landing_dataset": "p.land"
      },
      "relational": {
          "relationships_uri": "gs://b/rel/forest.yaml"
      },
  }
  for section, values in sections.items():
    base[section] = {**base.get(section, {}), **values}
  return GeneratorConfig.model_validate(base)


def test_today_has_no_problem():
  assert model_launch_problems(DEFAULT_CONFIG) == []


def test_a_complete_model_launch_has_no_problem():
  assert model_launch_problems(_all()) == []
  assert model_launch_problems(
      _all(launch={
          "batch_index": 2,
          "batch_count": 3
      })) == []


def test_the_batch_flags_need_tables_all():
  config = GeneratorConfig.model_validate(
      {"launch": {
          "batch_index": 0,
          "batch_count": 1
      }})
  assert model_launch_problems(config) == [
      "--batch_index and --batch_count need --tables=all (launch.tables)"
  ]


@pytest.mark.parametrize("io, problem", [
    ({
        "source_dataset": ""
    }, "--tables=all needs io.source_dataset "
     "(project.dataset)"),
    ({
        "landing_dataset": ""
    }, "--tables=all needs io.landing_dataset "
     "(project.dataset)"),
    ({
        "source_dataset": "src"
    }, "io.source_dataset must be project.dataset, "
     "got 'src'"),
    ({
        "landing_dataset": "p.land.t"
    }, "io.landing_dataset must be "
     "project.dataset, got 'p.land.t'"),
])
def test_tables_all_needs_both_datasets(io, problem):
  assert model_launch_problems(_all(io=io)) == [problem]


@pytest.mark.parametrize("uri", ["config/relationships", "gs://b/rel/", ""])
def test_tables_all_needs_one_model_file(uri):
  (problem,) = model_launch_problems(
      _all(relational={"relationships_uri": uri}))
  assert problem.startswith("--tables=all needs relational.relationships_uri "
                            "to name ONE model file")


@pytest.mark.parametrize("key", ["pk_cols", "identity_cols"])
def test_tables_all_refuses_a_global_key(key):
  (problem,) = model_launch_problems(_all(relational={key: "COL_001"}))
  assert problem.startswith(f"relational.{key} is not allowed with "
                            "--tables=all")


@pytest.mark.parametrize("launch, problem", [
    ({
        "batch_index": 0
    }, "--batch_index and --batch_count go together"),
    ({
        "batch_count": 2
    }, "--batch_index and --batch_count go together"),
    ({
        "batch_index": 2,
        "batch_count": 2
    }, "--batch_index must be 0 to 1 "
     "(--batch_count=2), got 2"),
    ({
        "batch_index": 0,
        "batch_count": 0
    }, "--batch_count must be >= 1, got 0"),
])
def test_the_batch_flags_are_consistent(launch, problem):
  assert model_launch_problems(_all(launch=launch)) == [problem]
