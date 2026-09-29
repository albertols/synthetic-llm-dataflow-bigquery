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
"""Test tables for the nearest-neighbour privacy and detection pass: the
invented `people` table of the membership tests (planned by the real
planner), sized for a 2,000-row R/H panel, with planted defects —
verbatim copies of R records, a shifted amount column, whole-row
duplicates.

Nothing here is real data: invented names, `example.com` e-mails, ids in
invented ranges (source 7_000_000+, synthetic 5_000_000+).

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from sdfb_evaluation.beam.encode import BatchEncoder, EncodedBatch
from sdfb_evaluation.context.plan import TablePlan

from .membership_data import (
    PEOPLE_FIELDS,
    PEOPLE_IDENTITY,
    SOURCE_ID_BASE,
    SYNTHETIC_ID_BASE,
    copy_content,
    panel_of,
    people_rows,
    planned,
)

SALT = "pr1v" * 8
LABEL_KEY = b"privacy-test-label-key-0123456789"
PANEL_ROWS = 2000  # R and H: the pass's minimum
SAMPLE_ROWS = 2500  # the privacy sample: every synthetic row by default
# keeps every in-process gradient-boosting fit to a few seconds
DETECTION_ROWS = 1200


def people(n_source: int = 5000,
           n_synthetic: int = 2500,
           n_reference: int = PANEL_ROWS,
           *,
           copies: int = 0,
           shift: float = 0.0,
           duplicates: int = 0,
           verified: bool = True,
           seed: int = 32) -> tuple[TablePlan, dict[str, list[dict]]]:
  """The people table, R = the first `n_reference` source rows and H the
  next as many. The synthetic side is drawn afresh (no copies), then:
  its first `copies` rows carry an R record's content under their own
  keys; `shift` is added to every synthetic `balance` (a detectable
  distortion); and the last `duplicates` rows are whole-row copies of
  the first synthetic row (one key held many times: its multiplicity).
  """
  source = people_rows(n_source, 31, SOURCE_ID_BASE)
  synthetic = people_rows(n_synthetic, seed, SYNTHETIC_ID_BASE)
  for k in range(copies):
    donor = source[(7 * k) % n_reference]
    synthetic[k] = copy_content(donor, synthetic[k])
  if shift:
    for row in synthetic:
      row["balance"] = round(row["balance"] + shift, 2)
  for k in range(duplicates):
    synthetic[n_synthetic - 1 - k] = dict(synthetic[0])
  table = planned(
      "people",
      PEOPLE_FIELDS,
      source,
      synthetic,
      pk=("person_id",),
      identity=PEOPLE_IDENTITY,
      panel=panel_of(source, n_reference, verified=verified))
  return table, {"source": source, "synthetic": synthetic}


def with_rates(table: TablePlan, *, synthetic: float) -> TablePlan:
  """`table` read in sampled mode at `synthetic` on the synthetic side."""
  return dataclasses.replace(table, sample_rate_synthetic=synthetic)


def encode(table: TablePlan,
           side: str,
           rows: Sequence[Mapping[str, Any]],
           *,
           chunk: int = 997) -> list[EncodedBatch]:
  """`rows` of one side as encoded batches, as `EncodeSide` makes them."""
  encoder = BatchEncoder.from_table(table, side, salt=SALT)
  return [
      encoder.encode(rows[start:start + chunk])
      for start in range(0, len(rows), chunk)
  ]


def encode_all(table: TablePlan,
               rows_by: Mapping[str, Sequence[Mapping[str, Any]]],
               *,
               chunk: int = 997) -> list[EncodedBatch]:
  return [
      batch for side, rows in rows_by.items()
      for batch in encode(table, side, rows, chunk=chunk)
  ]
