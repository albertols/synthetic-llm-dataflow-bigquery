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
"""BigQuery statement rules the context fakes enforce, so a statement
BigQuery would reject fails the laptop suite too.

Time travel (query syntax, FOR SYSTEM_TIME AS OF): "A single query
statement can't reference a single table at more than one point in time,
including the current time." Every backticked `project.dataset.table` in
a statement is one reference — at the `TIMESTAMP` literal of a directly
following `FOR SYSTEM_TIME AS OF`, else at the current time.
"""

from __future__ import annotations

import re

from sdfb_evaluation.context.bq import BqApiError

_FQN = r"[a-z][a-z0-9-]{4,28}[a-z0-9][.:][A-Za-z0-9_]+\.[A-Za-z0-9_$-]+"
_REFERENCE_RE = re.compile(rf"`(?P<table>{_FQN})`"
                           r"(?: FOR SYSTEM_TIME AS OF TIMESTAMP "
                           r"'(?P<as_of>[^']+)')?")


def points_in_time(sql: str) -> dict[str, set[str]]:
  """Each table the statement references → the points it reads it at."""
  seen: dict[str, set[str]] = {}
  for match in _REFERENCE_RE.finditer(sql):
    seen.setdefault(match["table"], set()).add(match["as_of"] or "current")
  return seen


def check_one_point_in_time_per_table(sql: str) -> None:
  """Raise what BigQuery raises (a 400, here `BqApiError`) when one
  statement reads the same table at two points in time."""
  clash = {t: sorted(p) for t, p in points_in_time(sql).items() if len(p) > 1}
  if clash:
    raise BqApiError(
        "400 (fake) A single query statement can't reference a single table "
        f"at more than one point in time, including the current time: {clash}")
