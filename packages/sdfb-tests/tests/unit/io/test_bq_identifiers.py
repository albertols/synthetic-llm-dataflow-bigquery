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
"""The BigQuery identifier allow-list (CWE-89, November 2026 scan).

BigQuery binds values as query parameters but never identifiers, so a
table name that reaches SQL text must pass this allow-list first.
"""

from __future__ import annotations

import pytest
from sdfb_beam.io.bq_identifiers import (quoted_table_fqn, validate_dataset,
                                         validate_project, validate_table,
                                         validate_table_fqn)

INJECTION_PAYLOADS = [
    "p.d.t` WHERE 1=1; DROP TABLE `p.d.x",  # backtick breakout
    "p.d.t` UNION ALL SELECT * FROM `p.d.secret",
    "p.d.(SELECT 1)",  # subquery
    "p.d.t' OR '1'='1",  # quote
    # A line comment needs a breakout first: inside backticks `--` is plain
    # text, and BigQuery table names may legitimately contain hyphens.
    "p.d.t`--",
    "p.d.t/*x*/",  # block comment
    "p.d.t\n; DROP TABLE x",  # newline
    "p.d.t x",  # whitespace
    "",  # empty
    "solo",  # one part
    "a.b.c.d",  # four parts
]


@pytest.mark.parametrize("fqn", INJECTION_PAYLOADS)
def test_injection_payloads_are_rejected(fqn):
  with pytest.raises(ValueError):
    validate_table_fqn(fqn)
  with pytest.raises(ValueError):
    quoted_table_fqn(fqn)


@pytest.mark.parametrize(
    "fqn",
    [
        "proj.synthetic_rag.rag_chunks",
        "my-project-01.ds_1.freetext_pools",
        "ds.table_only",  # 2-part: the client's default project
        "p.d.table-with-hyphens",
        "p.d.tablé_ünïcode",
        "p.d.T123",
    ])
def test_valid_fqns_pass_unchanged(fqn):
  assert validate_table_fqn(fqn) == fqn
  assert quoted_table_fqn(fqn) == f"`{fqn}`"


@pytest.mark.parametrize("project", ["p", "my-project-01", "a" * 63])
def test_valid_projects(project):
  assert validate_project(project) == project


@pytest.mark.parametrize(
    "project", ["", "-lead", "a" * 64, "p;x", "p`", "p.q", "p q", "ünï"])
def test_invalid_projects(project):
  with pytest.raises(ValueError, match="project"):
    validate_project(project)


@pytest.mark.parametrize("dataset", ["", "d-x", "d.x", "d`", "dé", "d\n"])
def test_invalid_datasets(dataset):
  with pytest.raises(ValueError, match="dataset"):
    validate_dataset(dataset)


@pytest.mark.parametrize("table", ["", "t'", "t`", "t;", "t x", "t\n", "t.u"])
def test_invalid_tables(table):
  with pytest.raises(ValueError, match="table"):
    validate_table(table)


def test_non_string_is_rejected():
  with pytest.raises(ValueError):
    validate_table_fqn(None)  # type: ignore[arg-type]
