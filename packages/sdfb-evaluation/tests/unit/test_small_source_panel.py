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
"""A source smaller than the generator's `reference_rows_limit`.

The generator samples such a source whole (R = every source row). The
panel then holds R = the whole source and H = nothing, so the privacy
metrics read `not_evaluated` with the `|R| ... |H| differ` reason, never
an exception.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from beam.membership_data import PEOPLE_FIELDS, PEOPLE_IDENTITY, people_rows
from beam.membership_data import planned
from beam.privacy_data import DETECTION_ROWS, LABEL_KEY, SALT, SAMPLE_ROWS
from beam.privacy_data import encode_all
from sdfb_evaluation.beam.privacy import PRIVACY_METRIC_IDS, privacy_outputs
from sdfb_evaluation.catalogue import load_catalogue
from sdfb_evaluation.context.reference import fetch_panel
from sdfb_evaluation.scoring import status_for
from sdfb_evaluation.types import Status

_CATALOGUE = load_catalogue()
N_SOURCE = 37
REFERENCE_ROWS_LIMIT = 10_000


class _WholeSourceBq:
  """BigQuery reduced to the panel query over a 37-row source: the first
  2n ranked rows are every row, ranked 1..37."""

  def __init__(self, rows: list[dict[str, Any]]):
    self.rows = rows

  def query(self,
            sql: str,
            params: Mapping[str, Any] | None = None,
            *,
            max_bytes: int | None = None) -> list[dict]:
    del sql, params, max_bytes
    return [{**row, "__sdfb_rk": i + 1} for i, row in enumerate(self.rows)]


def test_a_small_source_panel_is_not_evaluated_with_its_reason():
  source = people_rows(N_SOURCE, 31, 1_000_000)
  synthetic = people_rows(300, 32, 9_000_000)
  panel = fetch_panel(
      _WholeSourceBq(source),
      source_read_table="demo-project.dataset.x_table",
      n=REFERENCE_ROWS_LIMIT,
      expected_digest=None)
  assert (len(panel.r_rows), len(panel.h_rows)) == (N_SOURCE, 0)
  # The digest is not what this test is about: mark the panel verified so
  # the size reason is the one reported.
  panel = dataclasses.replace(panel, verified=True, reason=None)
  table = planned(
      "people",
      PEOPLE_FIELDS,
      source,
      synthetic,
      pk=("person_id",),
      identity=PEOPLE_IDENTITY,
      panel=panel)
  result = privacy_outputs(
      table,
      encode_all(table, {
          "source": source,
          "synthetic": synthetic
      }),
      salt=SALT,
      label_key=LABEL_KEY,
      privacy_sample_rows=SAMPLE_ROWS,
      detection_sample_rows=DETECTION_ROWS,
      row_flags_top_k=25)
  by = {mv.metric_id: mv for mv in result.metrics}
  for metric_id in PRIVACY_METRIC_IDS:
    mv = by[metric_id]
    assert mv.value is None, metric_id
    assert status_for(_CATALOGUE.get(metric_id), mv) is Status.NOT_EVALUATED
    reason = mv.detail["reason"]
    assert f"|R| = {N_SOURCE} and |H| = 0 differ" in reason, reason
