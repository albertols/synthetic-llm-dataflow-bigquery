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
"""Per-table resolution and the rules of `launch.tables` (ADR 0045).

    source  = T.source_table ?? <source dataset>.T     (a two-part override
              takes the source dataset's project)
    rows    = T.num_rows     ?? generation.num_rows    (a driven child's
              rows follow its parents, ADR 0036)

`explicit` (the default) generates io.landing_table and its components;
`all` generates every enabled table of ONE model file, from
io.source_dataset into io.landing_dataset, and refuses a global pk_cols /
identity_cols (it would key every table the model leaves without a key).

Design: docs/DESIGN.md §4 Relational generation
(ADR 0036, 0045).
"""
from __future__ import annotations

import re

from sdfb_core.config.model import GeneratorConfig
from sdfb_core.contracts.relationships import TableRelations

__all__ = [
    "TABLES_ALL", "TABLES_EXPLICIT", "model_launch_problems", "source_fqn",
    "table_rows"
]

TABLES_EXPLICIT = "explicit"
TABLES_ALL = "all"
_DATASET = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_]+")
_MODEL_SUFFIXES = (".yaml", ".yml")
_FQN_PARTS = 3  # project.dataset.table


def source_fqn(table: str, relations: TableRelations | None,
               source_dataset: str) -> str:
  """Where a launch reads `table`'s source (D2 of ADR 0045).

  `table` may be a bare name or an FQN (its last part is the name). Without
  an override: `<source_dataset>.<name>` — the rule `derive_source_fqn`
  applied to every launch before. A two-part `source_table` takes the
  project of `source_dataset` when it has one; a three-part one is kept.
  """
  name = table.rsplit(".", 1)[-1]
  override = relations.source_table if relations is not None else None
  if override is None:
    return f"{source_dataset}.{name}"
  if override.count(".") + 1 == _FQN_PARTS or source_dataset.count(".") != 1:
    return override
  project = source_dataset.split(".", 1)[0]
  return f"{project}.{override}"


def table_rows(relations: TableRelations | None) -> int | None:
  """The model's own row count for a table; None = the launch's."""
  return relations.num_rows if relations is not None else None


def model_launch_problems(config: GeneratorConfig) -> list[str]:
  """Every reason the launch modes refuse `config`, as sentences (empty =
  none). Checked when the flags are parsed, before any BigQuery read."""
  launch, io, relational = config.launch, config.io, config.relational
  index, count = launch.batch_index, launch.batch_count
  problems: list[str] = []
  if launch.tables == TABLES_EXPLICIT:
    if index is not None or count is not None:
      problems.append(
          "--batch_index and --batch_count need --tables=all (launch.tables)")
    return problems
  for key, value in (("io.source_dataset", io.source_dataset),
                     ("io.landing_dataset", io.landing_dataset)):
    if not value:
      problems.append(f"--tables=all needs {key} (project.dataset)")
    elif not _DATASET.fullmatch(value):
      problems.append(f"{key} must be project.dataset, got {value!r}")
  uri = relational.relationships_uri.strip()
  if not uri.rstrip("/").endswith(_MODEL_SUFFIXES):
    problems.append(
        f"--tables=all needs relational.relationships_uri to name ONE model "
        f"file (.yaml or .yml), got {uri!r}: a directory may hold several "
        f"models")
  for key, value in (("relational.pk_cols", relational.pk_cols),
                     ("relational.identity_cols", relational.identity_cols)):
    if value.strip():
      problems.append(
          f"{key} is not allowed with --tables=all: it would apply to every "
          f"table the model leaves without one. Declare pk: / identity: per "
          f"table in the model instead")
  if (index is None) != (count is None):
    problems.append("--batch_index and --batch_count go together")
  elif count is not None and index is not None:
    if count < 1:
      problems.append(f"--batch_count must be >= 1, got {count}")
    elif not 0 <= index < count:
      problems.append(f"--batch_index must be 0 to {count - 1} "
                      f"(--batch_count={count}), got {index}")
  return problems
