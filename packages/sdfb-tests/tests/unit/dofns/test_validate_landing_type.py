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
"""A value the LANDING type cannot hold diverts to the DLQ as
`schema.landing_type` instead of reaching the load (corp run, TABLE_C).

TABLE_C.COL_DATE_1 is STRING in the source and DATE in the landing table.
Records are validated against the source contract, so a b2 engine bug's
`'2847-46-81'` passed every line of defense and failed the FILE_LOADS job
(`Invalid date: '2847-46-81' Field: COL_DATE_1`), which failed the whole
Dataflow job instead of one row."""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import json
from pathlib import Path

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam.dofns.validate_record import ValidateRecordDoFn
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import PipelineConfig, build_pipeline
from sdfb_core.contracts import TableSchema
from sdfb_core.validation import BLOCKER_RULE_IDS, normalize_dlq_record
from sdfb_tests.fakes import FakeModelClient  # registers the "minimal" engine


def _schema(**types: str) -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "p.d.TABLE_C"
      },
      "schema": [{
          "name": "COL_ID",
          "type": "INT64",
          "mode": "REQUIRED"
      }] + [{
          "name": n,
          "type": t
      } for n, t in types.items()],
  })


_SOURCE = _schema(COL_DATE_1="STRING", COL_D="DATE", COL_S="STRING")
_LANDING = _schema(COL_DATE_1="DATE", COL_D="DATE", COL_S="STRING")


def _run(record, landing=_LANDING, source=_SOURCE):
  dofn = ValidateRecordDoFn(table_schema=source, landing_schema=landing)
  dofn.setup()
  return list(dofn.process(record))


def _dlq(outputs):
  return [o.value for o in outputs if isinstance(o, beam.pvalue.TaggedOutput)]


def _row(key, date_1, col_d="2024-05-01", col_s="x"):
  return {"COL_ID": key, "COL_DATE_1": date_1, "COL_D": col_d, "COL_S": col_s}


# -- the DoFn -----------------------------------------------------------------
def test_an_invalid_landing_date_diverts_with_full_context():
  record = _row(1, "2847-46-81")
  [envelope] = _dlq(_run(record))
  assert envelope["rule_id"] == "schema.landing_type"
  assert envelope["error_type"] == "load_safety"
  assert envelope["stage"] == "pre_write"
  assert envelope["raw_record"] == record
  [failure] = envelope["error_detail"]
  assert failure["column"] == "COL_DATE_1"
  assert failure["source_type"] == "STRING"
  assert failure["landing_type"] == "DATE"
  assert failure["value"] == "2847-46-81"
  assert "month" in failure["reason"]
  normalized = normalize_dlq_record(envelope, run_id="r")
  assert normalized["pipeline_step"] == "ValidateRecordDoFn"
  json.loads(normalized["error_detail"])


def test_every_failing_column_is_named():
  landing = _schema(COL_DATE_1="DATE", COL_D="DATE", COL_S="INT64")
  [envelope] = _dlq(_run(_row(1, "2024-02-30", col_s="1.5"), landing=landing))
  assert [f["column"] for f in envelope["error_detail"]
         ] == ["COL_DATE_1", "COL_S"]


def test_a_valid_landing_date_passes_unchanged():
  record = _row(1, "2024-05-01")
  assert _run(record) == [record]


def test_a_null_passes():
  record = _row(1, None)
  assert _run(record) == [record]


def test_a_source_date_column_is_unaffected():
  """COL_D is DATE in the source: the record model owns it (as today),
  and the landing check never looks at it."""
  source = _schema(COL_DATE_1="STRING", COL_D="DATE", COL_S="STRING")
  landing = _schema(COL_DATE_1="STRING", COL_D="DATE", COL_S="STRING")
  dofn = ValidateRecordDoFn(table_schema=source, landing_schema=landing)
  dofn.setup()
  assert not dofn._landing_types  # pylint: disable=protected-access
  record = _row(1, "2847-46-81")  # a STRING landing column takes any text
  assert list(dofn.process(record)) == [record]


def test_without_a_landing_schema_nothing_changes():
  record = _row(1, "2847-46-81")
  assert _run(record, landing=None) == [record]


def test_schema_landing_type_is_a_blocker():
  assert "schema.landing_type" in BLOCKER_RULE_IDS


# -- through the pipeline (DirectRunner) --------------------------------------
def _read(directory: Path) -> list[dict]:
  return [
      json.loads(line)
      for path in sorted(directory.parent.glob(directory.name + "*"))
      for line in path.read_text(encoding="utf-8").splitlines()
      if line.strip()
  ]


def _pipeline(tmp_path, rows, landing_schema):
  config = PipelineConfig(
      table_schema=_SOURCE,
      landing_schema=landing_schema,
      engine_name="minimal",
      model_client=FakeModelClient(responses=rows),
      num_rows=len(rows),
      batch_size=len(rows),
      run_id="landing-type",
      fail_on_blocker=False,
  )
  options = PipelineOptions(["--runner=DirectRunner"])
  with beam.Pipeline(options=options) as p:
    build_pipeline(
        p,
        reference_rows=rows,
        config=config,
        landing_sink=WriteToJsonLines(str(tmp_path / "landing")),
        dlq_sink=WriteToJsonLines(str(tmp_path / "dlq")),
    )
  return _read(tmp_path / "landing"), _read(tmp_path / "dlq")


def test_one_bad_date_in_a_batch_routes_only_that_row(tmp_path):
  rows = [
      _row(1, "2024-05-01"),
      _row(2, "2847-46-81"),
      _row(3, "9999-12-31"),
      _row(4, None),
  ]
  landed, dlq = _pipeline(tmp_path, rows, _LANDING)
  # The valid rows land exactly as generated.
  assert sorted(
      landed, key=lambda r: r["COL_ID"]) == [rows[0], rows[2], rows[3]]
  [dead] = dlq
  assert dead["rule_id"] == "schema.landing_type"
  assert dead["error_type"] == "load_safety"
  assert dead["stage"] == "pre_write"
  assert json.loads(dead["raw_record"]) == rows[1]
  [failure] = json.loads(dead["error_detail"])
  assert (failure["column"], failure["landing_type"],
          failure["value"]) == ("COL_DATE_1", "DATE", "2847-46-81")


def test_without_the_landing_schema_the_bad_date_would_reach_the_load(tmp_path):
  """The gap itself: the source contract (STRING) accepts the value."""
  rows = [_row(1, "2024-05-01"), _row(2, "2847-46-81")]
  landed, dlq = _pipeline(tmp_path, rows, None)
  assert dlq == []
  assert sorted(landed, key=lambda r: r["COL_ID"]) == rows
