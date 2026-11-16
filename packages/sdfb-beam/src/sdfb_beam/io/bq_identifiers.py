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
"""Allow-list for BigQuery identifiers that reach SQL text (CWE-89).

BigQuery binds *values* as query parameters (`@name`), never
*identifiers*: a table name in `FROM` has to be part of the SQL text. Any
identifier that comes from configuration (a launch flag, a DAG param)
passes these checks before it is interpolated.

The patterns are strict subsets of BigQuery's own naming rules, so no
legitimate name is rejected, and none of them admits a backtick, quote,
semicolon, comment marker, whitespace, newline, parenthesis or dot:

  project  [A-Za-z0-9][A-Za-z0-9_-]{0,62}      (ASCII)
  dataset  [A-Za-z0-9_]{1,1024}                (ASCII)
  table    [\\w-]{1,1024}                       (Unicode letters/digits, _, -)
  FQN      [project.]dataset.table
"""

from __future__ import annotations

import re

_PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", re.ASCII)
_DATASET_RE = re.compile(r"[A-Za-z0-9_]{1,1024}", re.ASCII)
_TABLE_RE = re.compile(r"[\w-]{1,1024}")
_FQN_WITH_PROJECT = 3  # project.dataset.table
_FQN_NO_PROJECT = 2  # dataset.table (the client's default project)


def _check(kind: str, value: str, pattern: re.Pattern[str]) -> str:
  if not isinstance(value, str) or not pattern.fullmatch(value):
    raise ValueError(f"invalid BigQuery {kind} identifier {value!r} "
                     f"(allowed: {pattern.pattern})")
  return value


def validate_project(project: str) -> str:
  """`project`, unchanged, or `ValueError`."""
  return _check("project", project, _PROJECT_RE)


def validate_dataset(dataset: str) -> str:
  """`dataset`, unchanged, or `ValueError`."""
  return _check("dataset", dataset, _DATASET_RE)


def validate_table(table: str) -> str:
  """`table`, unchanged, or `ValueError`."""
  return _check("table", table, _TABLE_RE)


def validate_table_fqn(fqn: str) -> str:
  """`[project.]dataset.table`, unchanged, or `ValueError`."""
  if not isinstance(fqn, str):
    raise ValueError(f"table FQN must be a string, got {fqn!r}")
  parts = fqn.split(".")
  if len(parts) == _FQN_WITH_PROJECT:
    validate_project(parts[0])
  elif len(parts) != _FQN_NO_PROJECT:
    raise ValueError(
        f"table FQN must be '[project.]dataset.table', got {fqn!r}")
  validate_dataset(parts[-2])
  validate_table(parts[-1])
  return fqn


def quoted_table_fqn(fqn: str) -> str:
  """The validated FQN as a backtick-quoted SQL table reference."""
  return f"`{validate_table_fqn(fqn)}`"


__all__ = [
    "quoted_table_fqn",
    "validate_dataset",
    "validate_project",
    "validate_table",
    "validate_table_fqn",
]
