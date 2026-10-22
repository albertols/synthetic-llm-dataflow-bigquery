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
"""`job_id` → `LaunchContext` (`sdfb_evaluation.context.launch`): the
resolution order, `params_source`, fallbacks and the registry filters.

Design: docs/DESIGN.md §4 Relational generation;
§7 Validation and the dead-letter queue; §8 Configuration
(ADR 0043, 0044, 0045).
"""

from __future__ import annotations

import json

import pytest

from sdfb_evaluation import schemas
from sdfb_evaluation.context.bq import BqApiError
from sdfb_evaluation.context.gcp import DataflowJobs, JobNotFoundError
from sdfb_evaluation.context.launch import (
    LaunchContext,
    RunRecord,
    job_record,
    resolve_launch,
)
from sdfb_evaluation.context.plan import Knobs, build_plan

from .plan_fakes import NOW, thelook_bq, thelook_models

PROJECT = "demo-project"
REGION = "europe-west1"
JOB_ID = "2026-09-13_06_10_16-9000000000000000017"
BASE = "thelook-0913-a1b2c3"
DS = "demo-project.thelook_synthetic"
QDS = "demo-project.synthetic_data_quality"
TABLES = (f"{DS}.users", f"{DS}.orders", f"{DS}.order_items")
RUN_IDS = (f"{BASE}-00-users", f"{BASE}-01-orders", f"{BASE}-02-order_items")
ADJUSTED_URI = ("gs://demo-bucket/staging/model_adjustments/"
                "sdfb-thelook-0913-a1b2c3.thelook_ecommerce.yaml")


def _message(entry) -> str:
  if "textPayload" in entry:
    return entry["textPayload"]
  return entry["jsonPayload"]["message"]


def _without_launch_config(entries):
  """The log as it looks once launch_config is gone (retention, or a launch
  that predates the milestone): its header and its body lines removed."""
  out, skipping = [], False
  for entry in entries:
    text = _message(entry)
    if "name=launch_config" in text:
      skipping = True
      continue
    if skipping and text.startswith("2026-"):
      skipping = False
    if not skipping:
      out.append(entry)
  return out


def _truncated_launch_config(entries):
  """launch_config whose closing brace never reached Cloud Logging."""
  out, inside = [], False
  for entry in entries:
    text = _message(entry)
    if "name=launch_config" in text:
      inside = True
    elif inside and text == "}":
      inside = False
      continue
    out.append(entry)
  return out


def _resolve(session, bq, **kwargs):
  return resolve_launch(
      bq=bq,
      session_factory=lambda project: session,
      project=PROJECT,
      region=REGION,
      **kwargs)


# --------------------------------------------------------------------------
# typed filters
# --------------------------------------------------------------------------
def test_typed_filters_mapping(fixture_data):
  ctx = LaunchContext.from_sources(
      job=None,
      launch_config=fixture_data("launch_config"),
      writes=[],
      manual=None)
  assert ctx.typed_filters() == {
      "engine":
          "b1_rag",
      "llm_model_uri":
          "gs://demo-bucket/synthetic/models/qwen/qwen2.5-3b-instruct/v1/",
      "embedder_id":
          "bge-small-en-v1.5",
      "seed":
          "derived",
      "similarity":
          0.5,
      "retrieval_method":
          "kcenter",
      "reference_rows_limit":
          10000,
      "num_rows_requested":
          5000,
      "uniqueness_mode":
          "exact",
      "freetext_expansion":
          "identifiers",
      "source_stats_tier":
          "exact",
      "profiler_version":
          None,
      "write_disposition":
          "append",
      "env":
          "dev",
      "client_type":
          "vllm",
      "vllm_dtype":
          "float16",
  }


def test_typed_filters_coerce_dataflow_display_strings(fixture_data):
  job = fixture_data("dataflow_job")
  ctx = LaunchContext.from_sources(
      job=job,
      launch_config=None,
      writes=[],
      manual={"tables_in_order": list(TABLES)})
  filters = ctx.typed_filters()
  assert filters["num_rows_requested"] == 5000  # int64Value "5000"
  assert filters["reference_rows_limit"] == 10000
  assert filters["similarity"] == 0.5
  assert filters["seed"] == "7"  # an explicit seed is kept verbatim
  # "" → the generator's HashingEmbedder identity (embedder_identity).
  assert filters["embedder_id"] == "hashing-384"
  assert filters["retrieval_method"] == "kcenter"


def test_typed_filters_uncoercible_value_is_null_and_warned(fixture_data):
  config = dict(fixture_data("launch_config"), similarity="high")
  ctx = LaunchContext.from_sources(
      job=None, launch_config=config, writes=[], manual=None)
  assert ctx.typed_filters()["similarity"] is None
  assert any("similarity" in w and "high" in w for w in ctx.warnings)


def test_typed_filters_keys_equal_registry_generation_filter_columns(
    fixture_data):
  fields = schemas.load_schema("evaluation_data_history")
  names = [f["name"] for f in fields]
  block = fields[names.index("engine"):names.index("vllm_dtype") + 1]
  ctx = LaunchContext.from_sources(
      job=None,
      launch_config=fixture_data("launch_config"),
      writes=[],
      manual={"params": {
          "profiler_version": "2"
      }})
  filters = ctx.typed_filters()
  assert list(filters) == [f["name"] for f in block]
  python_type = {"STRING": str, "INT64": int, "FLOAT64": float}
  for f in block:
    value = filters[f["name"]]
    assert value is not None, f["name"]
    assert isinstance(value, python_type[f["type"]]), f["name"]
    assert not isinstance(value, bool), f["name"]


# --------------------------------------------------------------------------
# resolution order
# --------------------------------------------------------------------------
def _scenario(name, fake_session, fake_bq, fixture_data):
  """(session, bq, resolve kwargs) with one source removed or broken."""
  entries = fixture_data("log_entries")["entries"]
  session, bq = fake_session(), fake_bq()
  kwargs: dict = {"job_id": JOB_ID}
  if name == "jobs_denied":
    bq.failures["JOBS_BY_PROJECT"] = PermissionError("Access Denied")
  elif name == "no_launch_config":
    session = fake_session(entries=_without_launch_config(entries))
  elif name == "launch_config_truncated":
    session = fake_session(entries=_truncated_launch_config(entries))
  elif name == "logs_denied":
    session = fake_session(status_script=[200, 403])
  elif name == "manual_only":
    kwargs = {
        "manual": {
            "tables_in_order": list(TABLES),
            "base_run_id": BASE,
            "params": {
                "engine": "b1_rag",
                "seed": ""
            },
        }
    }
  return session, bq, kwargs


def _assert_from_launch_config(ctx, fixture_data):
  # launch_config wins for params, run ids and the table order.
  assert ctx.params == fixture_data("launch_config")
  assert ctx.run_ids == RUN_IDS
  assert ctx.typed_filters()["seed"] == "derived"
  assert ctx.model_sha == "5e1f0c2a9b3d"
  assert ctx.model_name == "thelook_ecommerce"
  assert ctx.adjusted_model_uri == ADJUSTED_URI
  assert ctx.model_adjusted is True


def _assert_from_dataflow_params(ctx, fixture_data):
  # Dataflow display data supplies the params; validation_runs supplies the
  # closure's order ({base}-NN-{table}) and its run ids.
  assert ctx.params == DataflowJobs.params(fixture_data("dataflow_job"))
  assert "resolved" not in ctx.params
  assert ctx.typed_filters()["seed"] == "7"
  assert ctx.run_ids == RUN_IDS
  assert any("launch_config" in w for w in ctx.warnings)


def _assert_job_and_runs(ctx):
  assert ctx.generation_job_id == JOB_ID
  assert ctx.job_name == "sdfb-thelook-0913-a1b2c3"
  assert ctx.region == REGION
  assert ctx.started_at == "2026-09-13T13:10:16.512345Z"
  assert ctx.finished_at == "2026-09-13T14:02:41.118204Z"
  assert ctx.base_run_id == BASE
  # validation_runs: digests and valid counts per table, own launch only.
  assert [r.run_id for r in ctx.runs] == list(RUN_IDS)
  assert [r.valid_count for r in ctx.runs] == [5000, 18250, 44710]
  assert all(len(r.reference_digest) == 64 for r in ctx.runs)
  assert ctx.run_for(f"{DS}.orders").run_id == f"{BASE}-01-orders"
  assert any("thelook-0913-ffee00-01-orders" in w for w in ctx.warnings)


@pytest.mark.parametrize("scenario,expected_source", [
    ("all_sources", "jobs_labels+logs"),
    ("jobs_denied", "logs"),
    ("no_launch_config", "dataflow_params"),
    ("launch_config_truncated", "dataflow_params"),
    ("logs_denied", "dataflow_params"),
    ("manual_only", "manual"),
])
def test_resolution_order_and_params_source(fake_session, fake_bq, fixture_data,
                                            scenario, expected_source):
  session, bq, kwargs = _scenario(scenario, fake_session, fake_bq, fixture_data)
  ctx = _resolve(session, bq, **kwargs)
  assert ctx.params_source == expected_source
  assert ctx.tables_in_order == TABLES
  if scenario in ("all_sources", "jobs_denied"):
    _assert_from_launch_config(ctx, fixture_data)
  if scenario in ("no_launch_config", "launch_config_truncated", "logs_denied"):
    _assert_from_dataflow_params(ctx, fixture_data)
  if scenario == "logs_denied":
    assert ctx.model_sha is None and ctx.model_adjusted is None
    assert any("roles/logging.viewer" in w for w in ctx.warnings)
  if scenario == "manual_only":
    assert ctx.generation_job_id is None
    assert not ctx.writes and not ctx.runs
    assert ctx.base_run_id == BASE
    assert ctx.typed_filters()["seed"] == "derived"
  else:
    _assert_job_and_runs(ctx)


def test_all_sources_record_writes_and_query_region(fake_session, fake_bq):
  bq = fake_bq()
  ctx = _resolve(fake_session(), bq, job_id=JOB_ID)
  assert [w.table for w in ctx.writes] == [
      f"{DS}.users", f"{DS}.orders", f"{DS}.order_items",
      "demo-project.synthetic_data_quality.validation_runs"
  ]
  jobs_sql = next(sql for sql, _ in bq.queries if "JOBS_BY_PROJECT" in sql)
  # Location from the first landing table (EU) when Bq has none.
  assert "`region-eu`" in jobs_sql
  runs_sql, runs_params = next(
      (sql, p) for sql, p in bq.queries if "validation_runs" in sql)
  assert "`demo-project.synthetic_data_quality.validation_runs`" in runs_sql
  assert runs_params["landing_tables"] == sorted(TABLES)


def test_jobs_permission_denied_falls_back_with_warning(fake_session, fake_bq):
  bq = fake_bq()
  bq.failures["JOBS_BY_PROJECT"] = PermissionError("Access Denied: listAll")
  ctx = _resolve(fake_session(), bq, job_id=JOB_ID)
  assert not ctx.writes
  assert ctx.params_source == "logs"
  assert ctx.tables_in_order == TABLES
  warning = next(w for w in ctx.warnings if "resourceViewer" in w)
  assert "roles/bigquery.resourceViewer" in warning
  assert "Access Denied: listAll" in warning


def test_job_not_found_raises_with_region_hint(fake_session, fake_bq):
  with pytest.raises(JobNotFoundError) as info:
    _resolve(fake_session(jobs={}), fake_bq(), job_id=JOB_ID)
  message = str(info.value)
  assert JOB_ID in message
  assert REGION in message
  assert "--region" in message
  assert "retention" in message


def test_table_without_a_labelled_write_is_warned(fake_session, fake_bq,
                                                  fixture_data):
  rows = [
      r for r in fixture_data("jobs_by_project")
      if r["table_id"] != "order_items"
  ]
  bq = fake_bq(
      responses=[("JOBS_BY_PROJECT",
                  rows), ("validation_runs", fixture_data("validation_runs"))])
  ctx = _resolve(fake_session(), bq, job_id=JOB_ID)
  assert any(
      f"{DS}.order_items" in w and "no labelled" in w for w in ctx.warnings)


def test_manual_fills_gaps_never_overrides(fake_session, fake_bq):
  ctx = _resolve(
      fake_session(),
      fake_bq(),
      job_id=JOB_ID,
      manual={
          "model_sha": "0123456789ab",
          "params": {
              "profiler_version": "2",
              "engine": "b2_library"
          },
      })
  assert ctx.params_source == "jobs_labels+logs"
  assert ctx.model_sha == "5e1f0c2a9b3d"
  assert ctx.params["engine"] == "b1_rag"
  assert ctx.typed_filters()["profiler_version"] == "2"
  assert any("model_sha" in w for w in ctx.warnings)
  assert any("engine" in w for w in ctx.warnings)


@pytest.mark.parametrize("manual,match", [
    ({
        "tables": ["x"]
    }, "tables"),
    ({
        "tables_in_order": "demo-project.thelook_synthetic.users"
    }, "list"),
    ({
        "params": ["engine"]
    }, "params"),
])
def test_manual_is_strict(manual, match):
  with pytest.raises(ValueError, match=match):
    LaunchContext.from_sources(
        job=None, launch_config=None, writes=[], manual=manual)


def test_nothing_resolves_raises_actionable():
  with pytest.raises(ValueError, match="job_id"):
    resolve_launch(
        bq=None, session_factory=None, project=PROJECT, region=REGION)


def test_job_with_no_resolvable_table_raises_actionable(fake_session, fake_bq,
                                                        fixture_data):
  job = fixture_data("dataflow_job")
  job["environment"]["sdkPipelineOptions"]["display_data"] = []
  job["pipelineDescription"]["displayData"] = []
  session = fake_session(
      jobs={(REGION, JOB_ID): job},
      entries=_without_launch_config(fixture_data("log_entries")["entries"]))
  bq = fake_bq()
  bq.failures["JOBS_BY_PROJECT"] = PermissionError("Access Denied")
  with pytest.raises(ValueError) as info:
    _resolve(session, bq, job_id=JOB_ID)
  message = str(info.value)
  assert JOB_ID in message and REGION in message
  assert "tables_in_order" in message


# --------------------------------------------------------------------------
# R49 — a relational launch is never silently narrowed to --landing_table
# --------------------------------------------------------------------------
def _job_with(fixture_data, **display):
  """The recorded job with some display-data values replaced."""
  job = fixture_data("dataflow_job")
  for entry in job["environment"]["sdkPipelineOptions"]["display_data"]:
    if entry["key"] in display:
      entry["value"] = display[entry["key"]]
  return job


def test_logs_expired_without_validation_runs_uses_labelled_writes(
    fake_session, fake_bq, fixture_data):
  job = _job_with(fixture_data, validation_runs_table="")
  rows = [
      r for r in fixture_data("jobs_by_project")
      if r["table_id"] != "validation_runs"
  ]
  bq = fake_bq(responses=[("JOBS_BY_PROJECT", rows)])
  ctx = _resolve(
      fake_session(jobs={(REGION, JOB_ID): job}, entries=[]), bq, job_id=JOB_ID)
  assert ctx.params_source == "dataflow_params"
  # --landing_table names only order_items; the labelled writes show the
  # whole closure, in write-end order (users 13:41, orders 13:49, …).
  assert ctx.tables_in_order == TABLES
  added = next(w for w in ctx.warnings if "write-end order" in w)
  assert f"{DS}.users" in added and f"{DS}.orders" in added
  assert f"{DS}.order_items" not in added.split("added")[0]
  assert ctx.base_run_id == BASE
  assert not ctx.run_ids and not ctx.runs
  assert any("wrote no validation_runs" in w for w in ctx.warnings)
  assert any("flex template" in w for w in ctx.warnings)
  assert not any(
      "validation_runs" in sql and "FROM `demo-project.synthetic" in sql
      for sql, _ in bq.queries)


def test_jobs_denied_logs_gone_finds_the_launch_by_run_id_base(
    fake_session, fake_bq):
  bq = fake_bq()
  bq.failures["JOBS_BY_PROJECT"] = PermissionError("Access Denied")
  ctx = _resolve(fake_session(entries=[]), bq, job_id=JOB_ID)
  assert ctx.params_source == "dataflow_params"
  assert not ctx.writes
  assert ctx.tables_in_order == TABLES
  assert ctx.run_ids == RUN_IDS
  assert [r.valid_count for r in ctx.runs] == [5000, 18250, 44710]
  runs_sql, runs_params = next(
      (sql, p) for sql, p in bq.queries if "validation_runs" in sql)
  assert "STARTS_WITH(run_id, CONCAT(@base, '-'))" in runs_sql
  assert runs_params["base"] == BASE
  assert runs_params["landing_tables"] == [f"{DS}.order_items"]
  # The concurrent launch's orders row is not this launch's: never matched.
  assert not any("ffee00" in w for w in ctx.warnings)
  assert not any("write-end order" in w for w in ctx.warnings)


def test_run_position_gaps_are_warned(fake_session, fake_bq, fixture_data):
  rows = [
      r for r in fixture_data("validation_runs")
      if not r["run_id"].endswith("-01-orders")
  ]
  bq = fake_bq(responses=[("JOBS_BY_PROJECT", []), ("validation_runs", rows)])
  ctx = _resolve(fake_session(entries=[]), bq, job_id=JOB_ID)
  assert ctx.tables_in_order == (f"{DS}.users", f"{DS}.order_items")
  assert any("position(s) [1]" in w for w in ctx.warnings)
  assert any(f"{BASE}-02-order_items" in w and "beyond the 2" in w
             for w in ctx.warnings)


def test_labelled_write_outside_launch_config_list_is_reported(
    fake_session, fake_bq, fixture_data):
  extra = dict(
      fixture_data("jobs_by_project")[0],
      job_id="beam_bq_job_LOAD_extra_inventory",
      table_id="inventory_items")
  stats = dict(
      fixture_data("job_stats"),
      beam_bq_job_LOAD_extra_inventory={"load": {
          "outputRows": "7"
      }})
  bq = fake_bq(
      responses=[
          ("JOBS_BY_PROJECT", [*fixture_data("jobs_by_project"), extra]),
          ("validation_runs", fixture_data("validation_runs")),
      ],
      job_stats=stats)
  ctx = _resolve(fake_session(), bq, job_id=JOB_ID)
  assert ctx.tables_in_order == TABLES
  assert any(f"{DS}.inventory_items" in w and "not evaluated" in w
             for w in ctx.warnings)


def test_line_tie_reorder_discards_launch_config(fake_session, fake_bq,
                                                 fixture_data):
  """Two list lines logged in the same instant came back swapped: the JSON
  still parses, but run_ids[i] no longer names tables_in_order[i]."""
  entries = fixture_data("log_entries")["entries"]
  lines = {
      e["jsonPayload"]["message"].strip(): e
      for e in entries
      if "jsonPayload" in e
  }
  users = lines[f'"{DS}.users",']
  orders = lines[f'"{DS}.orders",']
  users["jsonPayload"]["message"], orders["jsonPayload"]["message"] = (
      orders["jsonPayload"]["message"], users["jsonPayload"]["message"])
  orders["timestamp"] = users["timestamp"]
  ctx = _resolve(fake_session(entries=entries), fake_bq(), job_id=JOB_ID)
  assert ctx.params_source == "dataflow_params"
  assert "resolved" not in ctx.params
  assert ctx.tables_in_order == TABLES  # from validation_runs positions
  assert ctx.run_ids == RUN_IDS
  assert any(
      "launch_config discarded" in w and "reordered" in w for w in ctx.warnings)


@pytest.mark.parametrize("failure", [
    LookupError("Not found: Dataset demo-project:region-xx"),
    BqApiError("400 Invalid value for location: xx"),
    BqApiError("503 Backend error (after the client's retries)"),
])
def test_jobs_source_errors_degrade_to_warnings(fake_session, fake_bq, failure):
  bq = fake_bq()
  bq.failures["JOBS_BY_PROJECT"] = failure
  ctx = _resolve(fake_session(), bq, job_id=JOB_ID)
  assert not ctx.writes
  assert ctx.params_source == "logs"
  assert ctx.tables_in_order == TABLES
  assert any(
      str(failure) in w and "job labels unavailable" in w for w in ctx.warnings)


def test_validation_runs_errors_degrade_to_warnings(fake_session, fake_bq):
  bq = fake_bq()
  bq.failures["FROM `demo-project.synthetic_data_quality.validation_runs`"] = (
      BqApiError("500 internal error"))
  ctx = _resolve(fake_session(), bq, job_id=JOB_ID)
  assert not ctx.runs
  assert ctx.tables_in_order == TABLES
  assert any("500 internal error" in w for w in ctx.warnings)


def test_job_not_found_falls_back_to_labels_and_runs(fake_session, fake_bq):
  """Past Dataflow's retention: the labelled writes and validation_runs
  still resolve the launch; the missing job is a warning, not an error."""
  ctx = _resolve(
      fake_session(jobs={}),
      fake_bq(location="EU"),
      job_id=JOB_ID,
      manual={
          "params": {
              "validation_runs_table": f"{QDS}.validation_runs",
              "run_id": BASE,
          }
      })
  assert ctx.generation_job_id == JOB_ID
  assert ctx.started_at is None and ctx.region is None
  assert ctx.params_source == "manual"
  assert ctx.tables_in_order == TABLES
  assert ctx.run_ids == RUN_IDS
  assert len(ctx.writes) == 4
  assert any("retention" in w and "Falling back" in w for w in ctx.warnings)


# --------------------------------------------------------------------------
# R50 — side tables recognised when the launch parameters do not name them
# --------------------------------------------------------------------------
def _with_side_writes(fixture_data):
  """The recorded JOBS rows plus a DLQ written INTO the landing dataset
  (short name `dlq`) and a RAG layer table in `synthetic_rag`."""
  base = fixture_data("jobs_by_project")[0]
  extra = [
      dict(base, job_id="beam_bq_job_LOAD_side_dlq", table_id="dlq"),
      dict(
          base,
          job_id="beam_bq_job_LOAD_side_rag",
          dataset_id="synthetic_rag",
          table_id="chunks_thelook"),
  ]
  stats = dict(
      fixture_data("job_stats"),
      beam_bq_job_LOAD_side_dlq={"load": {
          "outputRows": "3"
      }},
      beam_bq_job_LOAD_side_rag={"load": {
          "outputRows": "40"
      }})
  return [*fixture_data("jobs_by_project"), *extra], stats


def test_side_tables_recognised_without_params(fake_session, fake_bq,
                                               fixture_data):
  """Past retention with no manual params: nothing names the side tables,
  so a write into a side dataset or under a side-table name is a side
  table (warned by name), never a landing table to evaluate."""
  rows, stats = _with_side_writes(fixture_data)
  bq = fake_bq(
      location="EU",
      responses=[("JOBS_BY_PROJECT", rows),
                 ("validation_runs", fixture_data("validation_runs"))],
      job_stats=stats)
  ctx = _resolve(fake_session(jobs={}), bq, job_id=JOB_ID)
  assert ctx.params_source == "manual"
  assert ctx.tables_in_order == TABLES
  note = next(w for w in ctx.warnings if "side tables" in w)
  for side in (f"{QDS}.validation_runs", f"{DS}.dlq",
               "demo-project.synthetic_rag.chunks_thelook"):
    assert side in note
  assert not any(t in note for t in TABLES)


def test_side_table_heuristic_fills_params_that_name_only_some(
    fake_session, fake_bq, fixture_data):
  """Dataflow display data names validation_runs_table and dlq_table only;
  a write into synthetic_rag is still recognised as a side table."""
  rows, stats = _with_side_writes(fixture_data)
  bq = fake_bq(
      responses=[("JOBS_BY_PROJECT", rows),
                 ("validation_runs", fixture_data("validation_runs"))],
      job_stats=stats)
  ctx = _resolve(fake_session(entries=[]), bq, job_id=JOB_ID)
  assert ctx.params_source == "dataflow_params"
  assert ctx.tables_in_order == TABLES
  note = next(w for w in ctx.warnings if "side tables" in w)
  assert "demo-project.synthetic_rag.chunks_thelook" in note
  assert f"{DS}.dlq" in note


def test_side_table_heuristic_off_when_params_name_every_side_table(
    fake_session, fake_bq, fixture_data):
  """A launch_config names all six side tables: nothing is guessed, and a
  labelled write outside its table list is reported, not reclassified."""
  rows, stats = _with_side_writes(fixture_data)
  bq = fake_bq(
      responses=[("JOBS_BY_PROJECT", rows),
                 ("validation_runs", fixture_data("validation_runs"))],
      job_stats=stats)
  ctx = _resolve(fake_session(), bq, job_id=JOB_ID)
  assert ctx.params_source == "jobs_labels+logs"
  assert ctx.tables_in_order == TABLES
  assert not any("side tables" in w for w in ctx.warnings)
  assert any(f"{DS}.dlq" in w and "not evaluated" in w for w in ctx.warnings)


@pytest.mark.parametrize("manual,field", [
    ({
        "model_adjusted": "yes"
    }, "model_adjusted"),
    ({
        "started_at": "yesterday"
    }, "started_at"),
    ({
        "model_sha": 123
    }, "model_sha"),
    ({
        "base_run_id": "  "
    }, "base_run_id"),
    ({
        "run_ids": ["a", 3]
    }, "run_ids"),
    ({
        "params": {
            1: "x"
        }
    }, "params"),
])
def test_manual_scalars_are_type_checked(manual, field):
  with pytest.raises(ValueError, match=field):
    LaunchContext.from_sources(
        job=None,
        launch_config=None,
        writes=[],
        manual={
            "tables_in_order": list(TABLES),
            **manual
        })


def test_manual_timestamps_and_flags_accepted():
  ctx = LaunchContext.from_sources(
      job=None,
      launch_config=None,
      writes=[],
      manual={
          "tables_in_order": list(TABLES),
          "started_at": "2026-09-13T13:10:16Z",
          "model_adjusted": False,
      })
  assert ctx.started_at == "2026-09-13T13:10:16Z"
  assert ctx.model_adjusted is False


# --------------------------------------------------------------------------
# the job as the launch's identity (R134): the Dataflow job and nothing else
# --------------------------------------------------------------------------
_HAND_NAMED = {
    "tables_in_order": list(TABLES),
    "params": {
        "reference_rows_limit": 10_000
    },
}


def _no_log_no_bigquery(session, bq) -> None:
  """The sources a deployment without `logging.viewer` cannot read: not
  called at all, rather than called and tolerated."""
  assert all(method == "GET" and "/jobs/" in url
             for method, url, _ in session.calls), session.calls
  assert bq.queries == [] and bq.stats_calls == []


def test_job_only_reads_the_dataflow_job_and_nothing_else(
    fake_session, fake_bq, fixture_data):
  session, bq = fake_session(), fake_bq()
  ctx = _resolve(session, bq, job_id=JOB_ID, manual=_HAND_NAMED, job_only=True)
  job = fixture_data("dataflow_job")
  assert len(session.calls) == 1
  _no_log_no_bigquery(session, bq)
  assert (ctx.generation_job_id, ctx.region) == (JOB_ID, REGION)
  assert (ctx.started_at, ctx.finished_at) == DataflowJobs.window(job)
  assert ctx.job_name == job["name"]
  # the tables and parameters are the command line's, not the job's
  # display data (which names the generation's own landing table)
  assert ctx.tables_in_order == TABLES
  assert ctx.params == _HAND_NAMED["params"]
  assert ctx.params_source == "manual" and not ctx.writes and not ctx.runs
  assert not ctx.warnings or all("window" not in w for w in ctx.warnings)


@pytest.mark.parametrize("failure", ["not found", "permission"])
def test_job_only_survives_an_unreadable_job_with_one_warning(
    fake_session, fake_bq, failure):
  session = fake_session(jobs={}) if failure == "not found" else fake_session(
      status_script=[403])
  bq = fake_bq()
  ctx = _resolve(session, bq, job_id=JOB_ID, manual=_HAND_NAMED, job_only=True)
  _no_log_no_bigquery(session, bq)
  assert ctx.generation_job_id == JOB_ID
  assert (ctx.started_at, ctx.finished_at) == (None, None)
  assert ctx.tables_in_order == TABLES
  (warning,) = [w for w in ctx.warnings if JOB_ID in w]
  assert "window" in warning and "unknown" in warning
  if failure == "permission":
    assert "roles/dataflow.viewer" in warning


def test_a_job_id_alone_still_reads_the_log_and_bigquery(fake_session, fake_bq):
  session, bq = fake_session(), fake_bq()
  ctx = _resolve(session, bq, job_id=JOB_ID)
  assert any(method == "POST" for method, _, _ in session.calls)  # the log
  assert any("JOBS_BY_PROJECT" in sql for sql, _ in bq.queries)
  assert any("validation_runs" in sql for sql, _ in bq.queries)
  assert ctx.params_source == "jobs_labels+logs"


# --------------------------------------------------------------------------
# the job as identity, plus the generator's validation_runs rows (R136)
# --------------------------------------------------------------------------
_RUNS_TABLE = f"{QDS}.validation_runs"


def _with_runs_table(**extra):
  return {
      "tables_in_order": list(TABLES),
      "params": {
          "reference_rows_limit": 10_000,
          "validation_runs_table": _RUNS_TABLE,
          **extra
      },
  }


def _runs_queries(bq):
  return [(sql, p) for sql, p in bq.queries if "validation_runs" in sql]


def test_job_only_reads_this_launchs_validation_runs_in_the_jobs_window(
    fake_session, fake_bq, fixture_data):
  session, bq = fake_session(), fake_bq()
  ctx = _resolve(
      session, bq, job_id=JOB_ID, manual=_with_runs_table(), job_only=True)
  # one query on the generator's table; no log, no JOBS labels
  assert len(session.calls) == 1 and len(bq.queries) == 1
  ((sql, bound),) = _runs_queries(bq)
  assert f"`{_RUNS_TABLE}`" in sql and "JOBS_BY_PROJECT" not in sql
  assert bound["landing_tables"] == sorted(TABLES)
  assert "base" not in bound  # the run id is not known here
  assert "start" in bound and "end" in bound  # the job's window, padded
  assert ctx.base_run_id == BASE and ctx.run_ids == RUN_IDS
  # the fixture also holds a second launch's orders row: it is ignored
  expected = {
      r["landing_table"]: r
      for r in fixture_data("validation_runs")
      if r["run_id"].startswith(BASE)
  }
  assert {
      r.landing_table: r.reference_digest for r in ctx.runs
  } == {
      t: expected[t]["reference_digest"] for t in TABLES
  }
  # the tables stay the caller's; the parameters too
  assert ctx.tables_in_order == TABLES and ctx.params_source == "manual"
  assert ctx.params["validation_runs_table"] == _RUNS_TABLE
  assert not ctx.writes


def test_job_only_keeps_the_callers_tables_when_a_table_has_no_run_row(
    fake_session, fake_bq):
  extra = f"{DS}.not_generated"
  manual = _with_runs_table()
  manual["tables_in_order"] = [*TABLES, extra]
  ctx = _resolve(
      fake_session(), fake_bq(), job_id=JOB_ID, manual=manual, job_only=True)
  assert ctx.tables_in_order == (*TABLES, extra)
  assert ctx.run_for(extra) is None and ctx.run_for(TABLES[0]) is not None
  assert not [w for w in ctx.warnings if w.startswith("manual")]


def test_job_only_without_a_window_does_not_read_validation_runs(
    fake_session, fake_bq):
  session, bq = fake_session(jobs={}), fake_bq()
  ctx = _resolve(
      session, bq, job_id=JOB_ID, manual=_with_runs_table(), job_only=True)
  assert bq.queries == [] and not ctx.runs
  (warning,) = [w for w in ctx.warnings if JOB_ID in w]
  assert "window" in warning and "not evaluated" in warning
  assert "reference sample" in warning


def test_job_only_without_a_runs_table_reads_no_validation_runs(
    fake_session, fake_bq):
  bq = fake_bq()
  ctx = _resolve(
      fake_session(),
      bq,
      job_id=JOB_ID,
      manual={"tables_in_order": list(TABLES)},
      job_only=True)
  assert bq.queries == [] and not ctx.runs


@pytest.mark.parametrize(
    "failure",
    [PermissionError("Access Denied"),
     BqApiError("404 Not found", status=404)])
def test_job_only_survives_an_unreadable_validation_runs(
    fake_session, fake_bq, failure):
  bq = fake_bq()
  bq.failures["validation_runs"] = failure
  ctx = _resolve(
      fake_session(),
      bq,
      job_id=JOB_ID,
      manual=_with_runs_table(),
      job_only=True)
  assert not ctx.runs and ctx.tables_in_order == TABLES
  (warning,) = [w for w in ctx.warnings if "validation_runs" in w]
  assert _RUNS_TABLE in warning and "could not be read" in warning


def _table_name(row) -> str:
  return row["landing_table"].rsplit(".", 1)[1]


def test_job_only_takes_the_latest_launch_when_the_window_holds_two(
    fake_session, fake_bq, fixture_data):
  rows = list(fixture_data("validation_runs"))
  later = [
      dict(
          r,
          run_id=f"other-0913-ffffff-{i:02d}-{_table_name(r)}",
          reference_digest=f"{i:064x}",
          created_at="2026-09-13T14:01:00.000000Z") for i, r in enumerate(rows)
  ]
  bq = fake_bq(responses=[("validation_runs", rows + later)])
  ctx = _resolve(
      fake_session(),
      bq,
      job_id=JOB_ID,
      manual=_with_runs_table(),
      job_only=True)
  assert ctx.base_run_id == "other-0913-ffffff"
  assert all(r.run_id.startswith("other-0913-ffffff") for r in ctx.runs)
  assert [w for w in ctx.warnings if "launches" in w and "latest" in w]


def test_a_run_record_reads_the_record_columns():
  snapshot = {"config_version": 1, "values": {"generation": {"seed": "7"}}}
  record = RunRecord.from_row({
      "run_id": "r-00-users",
      "landing_table": f"{DS}.users",
      "engine": "b1_rag",
      "job_id": "job-1",
      "job_name": "n",
      "generator_version": "0.6.1-master-edcfdcd",
      "embedder_uri": "",
      "relationships_uri": "",
      "similarity": "0.5",
      "config_digest": "0123456789ab",
      "config": json.dumps(snapshot),
  })
  assert record.engine == "b1_rag" and record.job_id == "job-1"
  assert record.embedder_uri == "" and record.relationships_uri == ""
  assert record.similarity == 0.5
  assert record.config == snapshot
  assert record.written_by("job-1", None)
  assert not record.written_by("job-2", "n")


def test_a_legacy_row_reads_as_no_record():
  record = RunRecord.from_row({"run_id": "r", "landing_table": f"{DS}.users"})
  assert record.config is None and record.generator_version is None
  assert record.embedder_uri is None and record.similarity is None


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", "42", "", "null"])
def test_a_malformed_config_is_no_snapshot(text):
  record = RunRecord.from_row({
      "run_id": "r",
      "landing_table": f"{DS}.users",
      "config": text,
      "similarity": "not a number",
  })
  assert record.config is None and record.similarity is None


def test_a_snapshot_with_unknown_blocks_is_kept_whole():
  snapshot = {"config_version": 9, "from_the_future": {"x": 1}}
  record = RunRecord.from_row({
      "run_id": "r",
      "landing_table": f"{DS}.users",
      "config": json.dumps(snapshot),
  })
  assert record.config == snapshot


def test_the_snapshot_does_not_decide_equality():
  row = {"run_id": "r", "landing_table": f"{DS}.users"}
  with_snapshot = RunRecord.from_row({**row, "config": '{"values": {"a": 1}}'})
  without = RunRecord.from_row(row)
  assert with_snapshot.config == {"values": {"a": 1}}
  assert with_snapshot == without
  assert hash(with_snapshot) == hash(without)


# --------------------------------------------------------------------------
# the generation record fills the context first (ADR 0044)
# --------------------------------------------------------------------------
_VERSION = "0.6.1-master-edcfdcd"
_POOL_KEY = "gs://b/synthetic/models/m/v1/+cfg.abc123abc123"


def _snapshot(**relational) -> dict:
  return {
      "config_version": 1,
      "values": {
          "io": {
              # the launch's own target: never allowed to name the tables
              "landing_table":
                  f"{DS}.order_items",
              "reference_table":
                  "bigquery-public-data.thelook_ecommerce.order_items",
              "validation_runs_table":
                  _RUNS_TABLE,
              "freetext_pools_table":
                  f"{QDS}.freetext_pools",
          },
          "launch": {},
          "sampling": {
              "reference_rows_limit": 5000,
              "source_stats": "exact"
          },
          "generation": {
              "engine": "b1_rag",
              "num_rows": 5000,
              "similarity": 0.7,
              "seed": ""
          },
          "free_text": {
              "pool_seed_strategy": "kcenter",
              "freetext_expansion": "identifiers"
          },
          "rag": {
              "embedder_uri": "gs://b/synthetic/models/embedders/bge-small/v1/"
          },
          "guardrails": {
              "env": "uat",
              "uniqueness_mode": "exact",
              "write_disposition": "append"
          },
          "relational": {
              "generate_fk_relationships": "true",
              "relationships_uri": "config/relationships",
              **relational
          },
          "serving": {
              "model_uri": "gs://b/synthetic/models/m/v1/",
              "client_type": "vllm",
              "vllm_dtype": "auto"
          },
      },
      "derived": {
          "profiler_version": "2",
          "cache_keys": {
              "freetext_pools": _POOL_KEY
          }
      },
      "launcher": {
          "version": _VERSION
      },
      "batch": None,
  }


def _record_rows(fixture_data, snapshot=None, **extra) -> list[dict]:
  """The fixture's rows; this launch's (BASE) carry the record."""
  text = json.dumps(snapshot or _snapshot())
  return [
      dict(
          r,
          job_id=JOB_ID,
          generator_version=_VERSION,
          config_digest="0123456789ab",
          config=text,
          **extra) if r["run_id"].startswith(BASE) else r
      for r in fixture_data("validation_runs")
  ]


def test_job_only_fills_the_generation_cells_from_the_record(
    fake_session, fake_bq, fixture_data):
  """AC-A2-4: the seed path's registry cells come from validation_runs."""
  bq = fake_bq(responses=[("validation_runs", _record_rows(fixture_data))])
  ctx = _resolve(
      fake_session(),
      bq,
      job_id=JOB_ID,
      manual=_with_runs_table(),
      job_only=True)
  assert ctx.params_source == "validation_runs"
  assert ctx.tables_in_order == TABLES  # the caller's tables stay the caller's
  assert not [w for w in ctx.warnings if "tables_in_order" in w]
  assert "landing_table" not in ctx.params  # the record's target list is not a table list
  typed = ctx.typed_filters()
  assert typed["engine"] == "b1_rag"
  assert typed["llm_model_uri"] == "gs://b/synthetic/models/m/v1/"
  assert typed["embedder_id"] == "bge-small"
  assert typed["retrieval_method"] == "kcenter"
  assert typed["similarity"] == 0.7
  assert typed["seed"] == "derived"
  assert typed["num_rows_requested"] == 5000
  assert typed["source_stats_tier"] == "exact"
  assert typed["env"] == "uat"
  assert typed["profiler_version"] == "2"
  assert typed["reference_rows_limit"] == 5000  # the record beats the DAG's
  assert any("manual params.reference_rows_limit=10000 ignored: the "
             "validation_runs value 5000 is kept" in w for w in ctx.warnings)
  assert ctx.generator_version == _VERSION
  assert ctx.config_digest == "0123456789ab"
  assert ctx.cache_key("freetext_pools") == _POOL_KEY
  assert ctx.relationships_uri == "config/relationships"


def test_an_isolated_record_turns_relationships_off(fake_session, fake_bq,
                                                    fixture_data):
  rows = _record_rows(
      fixture_data, snapshot=_snapshot(generate_fk_relationships="false"))
  ctx = _resolve(
      fake_session(),
      fake_bq(responses=[("validation_runs", rows)]),
      job_id=JOB_ID,
      manual=_with_runs_table(),
      job_only=True)
  assert ctx.relationships_uri is None
  assert ctx.params["relationships_uri"] == ""


def test_a_null_in_the_snapshot_leaves_the_key_to_the_next_source(
    fake_session, fake_bq, fixture_data):
  """A null in `values` is a derived default the snapshot does not pin
  (ADR 0043): it states nothing, so manual input still fills the key."""
  snapshot = _snapshot()
  snapshot["values"]["free_text"]["pool_stagnation_min_novel"] = None
  rows = _record_rows(fixture_data, snapshot=snapshot)
  ctx = _resolve(
      fake_session(),
      fake_bq(responses=[("validation_runs", rows)]),
      job_id=JOB_ID,
      manual=_with_runs_table(pool_stagnation_min_novel=4),
      job_only=True)
  assert ctx.params_source == "validation_runs"
  assert ctx.params["pool_stagnation_min_novel"] == 4
  assert not [w for w in ctx.warnings if "pool_stagnation_min_novel" in w]


def test_job_only_prefers_the_jobs_own_rows(fake_session, fake_bq,
                                            fixture_data):
  """Another launch wrote the same tables later in the window: without the
    record the latest launch would win (`_pick_runs`); with it, the job's."""
  rows = _record_rows(fixture_data)
  later = [
      dict(
          r,
          run_id=f"other-0913-ffffff-{i:02d}-{_table_name(r)}",
          created_at="2026-09-13T14:01:00.000000Z",
          job_id="another-job",
          config=None)
      for i, r in enumerate(rows)
      if r["run_id"].startswith(BASE)
  ]
  ctx = _resolve(
      fake_session(),
      fake_bq(responses=[("validation_runs", rows + later)]),
      job_id=JOB_ID,
      manual=_with_runs_table(),
      job_only=True)
  assert ctx.base_run_id == BASE
  assert all(r.run_id.startswith(BASE) for r in ctx.runs)


def test_a_digest_without_a_parseable_config_is_noted(fake_session, fake_bq,
                                                      fixture_data):
  rows = [
      dict(
          r,
          job_id=JOB_ID,
          config_digest="0123456789ab",
          config="{not json",
          engine="b2_library",
          model_uri="gs://b/synthetic/models/m/v1/",
          env="dev") if r["run_id"].startswith(BASE) else r
      for r in fixture_data("validation_runs")
  ]
  ctx = _resolve(
      fake_session(),
      fake_bq(responses=[("validation_runs", rows)]),
      job_id=JOB_ID,
      manual=_with_runs_table(),
      job_only=True)
  assert ctx.typed_filters()["engine"] == "b2_library"
  assert ctx.params_source == "validation_runs"
  assert any("could not be parsed" in w for w in ctx.warnings)
  assert ctx.record is None


def test_the_record_comes_first_on_the_full_path(fake_session, fake_bq,
                                                 fixture_data):
  """record → launch_config → display data → manual, each filling only
    what is missing (ADR 0044)."""
  rows = _record_rows(fixture_data)
  bq = fake_bq(
      responses=[("JOBS_BY_PROJECT", fixture_data("jobs_by_project")),
                 ("validation_runs", rows)],
      job_stats=fixture_data("job_stats"))
  ctx = _resolve(fake_session(), bq, job_id=JOB_ID)
  assert ctx.params_source == "validation_runs"
  assert ctx.typed_filters()["similarity"] == 0.7
  # a key only the launch log has still fills in
  assert ctx.params["run_id"] == BASE


def test_legacy_rows_keep_todays_sources(fake_session, fake_bq):
  """The fixture's rows predate the record: nothing changes."""
  ctx = _resolve(fake_session(), fake_bq(), job_id=JOB_ID)
  assert ctx.params_source == "jobs_labels+logs"
  assert ctx.record is None and ctx.generator_version is None


def _full_path_bq(fake_bq, fixture_data, rows, **kwargs):
  return fake_bq(
      responses=[("JOBS_BY_PROJECT", fixture_data("jobs_by_project")),
                 ("validation_runs", rows)],
      **kwargs)


def test_the_full_path_keeps_the_jobs_own_rows_when_its_run_id_is_unknown(
    fake_session, fake_bq, fixture_data):
  """Past Dataflow's retention there is no log, no display data and no run
  id, and the latest launch in the window is another job's: its snapshot
  must not fill this job's params (ADR 0044)."""
  other = _snapshot()
  other["values"]["generation"]["similarity"] = 0.2
  rows = _record_rows(fixture_data)
  later = [
      dict(
          r,
          run_id=f"other-0913-ffffff-{i:02d}-{_table_name(r)}",
          created_at="2026-09-13T14:01:00.000000Z",
          job_id="another-job",
          generator_version="0.6.1-master-ffffff0",
          config=json.dumps(other))
      for i, r in enumerate(rows)
      if r["run_id"].startswith(BASE)
  ]
  ctx = _resolve(
      fake_session(jobs={}),
      _full_path_bq(fake_bq, fixture_data, rows + later, location="EU"),
      job_id=JOB_ID,
      manual={
          "params": {
              "validation_runs_table": _RUNS_TABLE,
              "similarity": 0.7
          }
      })
  assert ctx.base_run_id == BASE
  assert all(r.run_id.startswith(BASE) for r in ctx.runs)
  assert ctx.generator_version == _VERSION
  assert ctx.params["similarity"] == 0.7
  assert not [w for w in ctx.warnings if w.startswith("manual params.")]
  (dropped,) = [w for w in ctx.warnings if "another Dataflow job" in w]
  assert all(r["run_id"] in dropped for r in later)


def test_a_snapshot_silent_on_relationships_keeps_the_launchs_model(
    fake_session, fake_bq, fixture_data):
  """A snapshot that does not state `generate_fk_relationships` says
  nothing about relationships: the launch log's model stays."""
  snapshot = _snapshot()
  del snapshot["values"]["relational"]
  rows = _record_rows(fixture_data, snapshot=snapshot)
  ctx = _resolve(
      fake_session(), _full_path_bq(fake_bq, fixture_data, rows), job_id=JOB_ID)
  assert ctx.params_source == "validation_runs"
  uri = fixture_data("launch_config")["relationships_uri"]
  assert ctx.params["relationships_uri"] == uri
  assert ctx.relationships_uri == uri
  assert ctx.record is not None and not ctx.record.relationships_off


def test_a_snapshot_without_values_is_no_snapshot(fake_session, fake_bq,
                                                  fixture_data):
  """A `config` object with no `values` block (another format, or a newer
  generator's) is no snapshot: a note, then the row's typed columns."""
  rows = _record_rows(fixture_data, snapshot={"config_version": 2})
  ctx = _resolve(
      fake_session(), _full_path_bq(fake_bq, fixture_data, rows), job_id=JOB_ID)
  assert ctx.record is None
  assert ctx.params_source == "jobs_labels+logs"
  assert ctx.params == fixture_data("launch_config")
  assert ctx.relationships_uri == fixture_data(
      "launch_config")["relationships_uri"]
  assert any("no values block" in w for w in ctx.warnings)
  assert ctx.generator_version == _VERSION  # the typed columns still count


# A real 21-column validation_runs row: master's table has always carried
# engine, model_uri and env, which the generator writes from the launch's
# own arguments.
_LEGACY_COLUMNS = {
    "engine":
        "b1_rag",
    "model_uri":
        "gs://demo-bucket/synthetic/models/qwen/qwen2.5-3b-instruct/v1/",
    "env":
        "dev",
}


def _legacy_rows(fixture_data) -> list[dict]:
  return [dict(r, **_LEGACY_COLUMNS) for r in fixture_data("validation_runs")]


def _registry_seed(ctx: LaunchContext) -> dict:
  plan = build_plan(
      launch=ctx,
      models=thelook_models(),
      bq=thelook_bq(),
      knobs=Knobs(temp_dataset=QDS),
      mode="exact",
      trigger="cli",
      runner="DirectRunner",
      now=NOW)
  return plan.registry_seed()


def test_a_legacy_row_on_the_job_only_path_fills_only_its_three_columns(
    fake_session, fake_bq, fixture_data):
  """Controller ruling F12 / plan Ruling 11: a legacy row's typed columns
  are the only params besides manual input, so they fill the engine, model
  and env cells and name the source; nothing else moves."""

  def resolve(rows):
    return _resolve(
        fake_session(),
        fake_bq(responses=[("validation_runs", rows)]),
        job_id=JOB_ID,
        manual=_with_runs_table(),
        job_only=True)

  bare, legacy = resolve(fixture_data("validation_runs")), resolve(
      _legacy_rows(fixture_data))
  assert (bare.params_source, legacy.params_source) == ("manual",
                                                        "validation_runs")
  filled = {
      "engine": "b1_rag",
      "llm_model_uri": _LEGACY_COLUMNS["model_uri"],
      "env": "dev"
  }
  assert legacy.typed_filters() == {**bare.typed_filters(), **filled}
  for name in ("tables_in_order", "run_ids", "base_run_id", "relationships_uri",
               "write_disposition", "warnings"):
    assert getattr(legacy, name) == getattr(bare, name), name
  before, after = _registry_seed(bare), _registry_seed(legacy)
  changed = {"params_source", "generation_params", *filled}
  assert {
      k: v for k, v in after.items() if k not in changed
  } == {
      k: v for k, v in before.items() if k not in changed
  }
  assert after["params_source"] == "validation_runs"
  assert after["generation_params"] == {
      **before["generation_params"],
      **_LEGACY_COLUMNS
  }


def test_a_legacy_row_on_the_full_path_changes_nothing(fake_session, fake_bq,
                                                       fixture_data):
  bare = _resolve(fake_session(), fake_bq(), job_id=JOB_ID)
  legacy = _resolve(
      fake_session(),
      _full_path_bq(fake_bq, fixture_data, _legacy_rows(fixture_data)),
      job_id=JOB_ID)
  assert legacy.params_source == bare.params_source == "jobs_labels+logs"
  assert legacy.typed_filters() == bare.typed_filters()
  assert legacy.params == bare.params
  assert legacy.warnings == bare.warnings


def test_the_warnings_keep_their_order_without_a_record(fake_session, fake_bq):
  """Controller ruling: a launch without a record warns in master's order,
  the params notes before validation_runs' (the registry's `warnings`)."""
  ctx = _resolve(
      fake_session(),
      fake_bq(),
      job_id=JOB_ID,
      manual={"params": {
          "engine": "b2_library"
      }})
  notes = list(ctx.warnings)
  manual = next(
      i for i, w in enumerate(notes) if w.startswith("manual params.engine"))
  other = next(
      i for i, w in enumerate(notes) if "another launch in the window" in w)
  assert manual < other


def test_a_manual_disagreement_names_the_source_that_supplied_the_value(
    fake_session, fake_bq, fixture_data):
  ctx = _resolve(
      fake_session(),
      _full_path_bq(fake_bq, fixture_data, _record_rows(fixture_data)),
      job_id=JOB_ID,
      manual={"params": {
          "batch_size": 32,
          "similarity": 0.9
      }})
  assert ctx.params_source == "validation_runs"
  assert ("manual params.batch_size=32 ignored: the jobs_labels+logs value "
          "16 is kept") in ctx.warnings
  assert ("manual params.similarity=0.9 ignored: the validation_runs value "
          "0.7 is kept") in ctx.warnings


@pytest.mark.parametrize(
    "manual", [
        _with_runs_table(run_id=BASE),
        {
            **_with_runs_table(), "base_run_id": BASE
        },
    ],
    ids=["params.run_id", "base_run_id"])
def test_job_only_takes_the_callers_run_id_over_the_latest_launch(
    fake_session, fake_bq, fixture_data, manual):
  rows = list(fixture_data("validation_runs"))
  later = [
      dict(
          r,
          run_id=f"other-0913-ffffff-{i:02d}-{_table_name(r)}",
          created_at="2026-09-13T14:01:00.000000Z") for i, r in enumerate(rows)
  ]
  ctx = _resolve(
      fake_session(),
      fake_bq(responses=[("validation_runs", rows + later)]),
      job_id=JOB_ID,
      manual=manual,
      job_only=True)
  assert ctx.base_run_id == BASE
  assert ctx.runs and all(r.run_id.startswith(BASE) for r in ctx.runs)


# --------------------------------------------------------------------------
# the record of a job whose tables are not known yet (F1, ADR 0044)
# --------------------------------------------------------------------------
def _job_record(session_factory, bq, notes=None):
  return job_record(
      bq=bq,
      session_factory=session_factory,
      project=PROJECT,
      region=REGION,
      job_id=JOB_ID,
      validation_runs_table=_RUNS_TABLE,
      notes=notes)


def test_job_record_reads_the_jobs_own_rows(fake_session, fake_bq,
                                            fixture_data):
  rows = _record_rows(fixture_data)
  bq = fake_bq(responses=[("validation_runs", rows)])
  record = _job_record(lambda project: fake_session(), bq)
  assert record is not None
  assert record.landing_tables == TABLES
  assert record.reference_rows_limit == 5000
  ((sql, params),) = [(s, p) for s, p in bq.queries if "validation_runs" in s]
  assert "landing_tables" not in params  # the tables are what it finds out
  assert f"`{_RUNS_TABLE}`" in sql and "start" in params and "end" in params


def _no_credentials(project):
  raise RuntimeError(f"no usable Application Default Credentials ({project})")


@pytest.mark.parametrize(
    "case",
    ["no job", "denied", "no credentials", "legacy rows", "unreadable table"])
def test_job_record_is_none_without_a_record(fake_session, fake_bq,
                                             fixture_data, case):
  """Never raises (controller ruling F19): the job-only resolution reads
  the job and the table again and reports what failed."""
  session = {
      "no job": lambda: fake_session(jobs={}),
      "denied": lambda: fake_session(status_script=[403]),
  }.get(case, fake_session)()
  factory = _no_credentials if case == "no credentials" else (
      lambda project: session)
  bq = fake_bq(responses=[("validation_runs", _legacy_rows(fixture_data))])
  if case == "unreadable table":
    bq.failures["validation_runs"] = PermissionError("Access Denied")
  notes: list[str] = []
  assert _job_record(factory, bq, notes) is None
  if case in ("no job", "denied", "no credentials"):
    assert bq.queries == []
  # final review M4: a record that could not be READ is said, one that does
  # not exist is not
  if case == "legacy rows":
    assert not notes
  else:
    assert len(notes) == 1
    note = notes[0]
    assert note.startswith(
        f"the generation record of Dataflow job {JOB_ID} could not be read")
    assert note.endswith(
        "the seed's tables and the given inputs are evaluated (ADR 0044)")
    what = (f"({_RUNS_TABLE} could not be read: Access Denied)"
            if case == "unreadable table" else "(the job could not be read: ")
    assert what in note


# The fixture job's name: what a worker that could not read the job id
# records, and what every launch of the same Composer model is named.
_JOB_NAME = "sdfb-thelook-0913-a1b2c3"
_PREVIOUS = "prev-0913-aaaaaa"


def _named_rows(fixture_data,
                base: str,
                launched_at: str | None,
                limit: int,
                created_at: str | None = None,
                job_id: str | None = None) -> list[dict]:
  """One launch's rows of the fixture's three tables, written by workers
  that could not read the job id (`source=none`) unless `job_id` is given:
  matched by the job's name alone."""
  snapshot = _snapshot()
  snapshot["values"]["sampling"]["reference_rows_limit"] = limit
  return [
      dict(
          r,
          run_id=r["run_id"].replace(BASE, base),
          job_id=job_id,
          job_name=_JOB_NAME,
          launch_run_id=base,
          launched_at=launched_at,
          **({
              "created_at": created_at
          } if created_at else {}))
      for r in _record_rows(fixture_data, snapshot)
      if r["run_id"].startswith(BASE)
  ]


@pytest.mark.parametrize(
    "launched_at",
    ["2026-09-13T13:14:30Z", "2026-09-13T13:09:50Z"],
    # a Flex launch stamps launched_at after the job was created; a direct
    # submission, just before
    ids=["flex launch", "direct submission"])
def test_job_record_by_name_takes_the_jobs_own_launch_not_the_previous(
    fake_session, fake_bq, fixture_data, launched_at):
  """Final review I2: a Composer job name is the same for every launch of
  a model, so the job's padded window holds the previous launch's rows
  under the same name (back-to-back triggers). The record is the job's own
  launch's — 5,000 rows, not the previous launch's 10,000 — and the
  previous launch's rows are named in a note."""
  previous = _named_rows(
      fixture_data,
      _PREVIOUS,
      "2026-09-13T12:20:00Z",
      10_000,
      created_at="2026-09-13T13:03:00Z")
  own = _named_rows(fixture_data, BASE, launched_at, 5000)
  bq = fake_bq(responses=[("validation_runs", previous + own)])
  notes: list[str] = []
  record = _job_record(lambda project: fake_session(), bq, notes)
  assert record is not None and record.reference_rows_limit == 5000
  assert {r.launch_run_id for r in record.runs} == {BASE}
  assert record.landing_tables == TABLES
  assert len(notes) == 1
  note = notes[0]
  assert _JOB_NAME in note and BASE in note and _PREVIOUS in note


def test_job_record_by_name_ignores_a_later_launch_in_the_end_pad(
    fake_session, fake_bq, fixture_data):
  """Final review I2: the job's own launch, not the latest one — a launch
  stamped after the job ended (its rows inside the window's end pad) is
  another launch's."""
  own = _named_rows(fixture_data, BASE, "2026-09-13T13:14:30Z", 5000)
  later = _named_rows(
      fixture_data,
      "next-0913-bbbbbb",
      "2026-09-13T14:05:00Z",
      10_000,
      created_at="2026-09-13T14:10:00Z")
  bq = fake_bq(responses=[("validation_runs", own + later)])
  record = _job_record(lambda project: fake_session(), bq, [])
  assert record is not None and record.reference_rows_limit == 5000
  assert {r.launch_run_id for r in record.runs} == {BASE}


def test_job_record_rows_with_the_job_id_decide_the_launch(
    fake_session, fake_bq, fixture_data):
  """Final review I2: rows that recorded this job's id are the job's
  (unaffected); a same-named launch's rows without an id are dropped,
  even when they come first in run order."""
  previous = _named_rows(
      fixture_data,
      _PREVIOUS,
      "2026-09-13T12:20:00Z",
      10_000,
      created_at="2026-09-13T13:03:00Z")
  own = _named_rows(
      fixture_data, BASE, "2026-09-13T13:14:30Z", 5000, job_id=JOB_ID)
  bq = fake_bq(responses=[("validation_runs", previous + own)])
  record = _job_record(lambda project: fake_session(), bq, [])
  assert record is not None and record.reference_rows_limit == 5000
  assert {r.run_id for r in record.runs} == set(RUN_IDS)


def test_job_record_by_name_without_a_launch_time_takes_the_latest_with_a_note(
    fake_session, fake_bq, fixture_data):
  """Final review I2: when no launch says it was launched before the job
  ended, the latest one in the window is taken — and a note says so."""
  previous = _named_rows(
      fixture_data, _PREVIOUS, None, 10_000, created_at="2026-09-13T13:03:00Z")
  own = _named_rows(fixture_data, BASE, None, 5000)
  bq = fake_bq(responses=[("validation_runs", previous + own)])
  notes: list[str] = []
  record = _job_record(lambda project: fake_session(), bq, notes)
  assert record is not None and record.reference_rows_limit == 5000
  assert len(notes) == 1
  note = notes[0]
  assert "the latest is taken" in note and BASE in note and _PREVIOUS in note


# --------------------------------------------------------------------------
# ADR 0045 — a whole-model launch's batches are one launch
# --------------------------------------------------------------------------
_LAUNCH = "manual__2026-10-21-abcd1234"
_LANDING = "demo-project.synthetic_data"


def _batch_row(batch: int, index: int, table: str, *, recorded: bool = True):
  return {
      "run_id": f"{_LAUNCH}-b{batch:02d}-{index:02d}-{table}",
      "landing_table": f"{_LANDING}.{table}",
      "reference_table": f"demo-project.src.{table}",
      "created_at": f"2026-10-21T10:{batch:02d}:{index:02d}.000000Z",
      "launch_run_id": _LAUNCH if recorded else None,
      "job_id": f"job-{batch}" if recorded else None,
  }


def _by_base(rows, base):
  return LaunchContext.from_sources(
      job=None,
      launch_config=None,
      writes=(),
      manual={
          "base_run_id": base,
          "params": {
              "run_id": base
          }
      },
      runs=rows)


_BATCH_ROWS = [
    _batch_row(0, 0, "T_A"),
    _batch_row(0, 1, "T_B"),
    _batch_row(1, 0, "T_C"),
]


@pytest.mark.parametrize("recorded", [True, False])
def test_a_model_launch_by_its_base_takes_every_batchs_rows(recorded):
  """With the record (launch_run_id) and without it (the `-bNN` rule)."""
  rows = [
      _batch_row(b, i, t, recorded=recorded)
      for b, i, t in ((0, 0, "T_A"), (0, 1, "T_B"), (1, 0, "T_C"))
  ]
  ctx = _by_base(rows, _LAUNCH)
  assert sorted(ctx.tables_in_order) == [
      f"{_LANDING}.{t}" for t in ("T_A", "T_B", "T_C")
  ]
  assert ctx.base_run_id == _LAUNCH
  assert not [w for w in ctx.warnings if "another launch" in w]


def test_one_batch_by_its_own_run_id():
  ctx = _by_base(_BATCH_ROWS, f"{_LAUNCH}-b01")
  assert ctx.tables_in_order == (f"{_LANDING}.T_C",)


def test_a_run_id_that_merely_ends_like_a_batch_is_its_own_launch():
  row = {
      "run_id": "nightly-b01",
      "landing_table": f"{_LANDING}.T_A",
      "created_at": "2026-10-21T10:00:00.000000Z",
  }
  ctx = _by_base([row], "nightly-b01")
  assert ctx.tables_in_order == (f"{_LANDING}.T_A",)


def test_without_a_base_the_batches_group_by_their_recorded_launch():
  ctx = LaunchContext.from_sources(
      job=None, launch_config=None, writes=(), manual=None, runs=_BATCH_ROWS)
  assert len(ctx.tables_in_order) == 3
  assert not [w for w in ctx.warnings if "launches" in w]


def _one_table_batch_config(run_id: str) -> dict:
  """The `launch_config` a one-table batch b03 logs (ADR 0045): the batch's
  run_id, and its one run id with the suffix a multi-table plan gets."""
  return {
      "run_id": f"{_LAUNCH}-b03",
      "resolved": {
          "tables_in_order": [f"{_LANDING}.T_A"],
          "run_ids": [run_id],
      },
  }


def test_a_one_table_batchs_launch_config_is_kept():
  """`plan_launch(model_launch=True)` names a one-table batch's run
  `<batch run id>-00-<table>`: no reorder, so the log is kept."""
  ctx = LaunchContext.from_sources(
      job=None,
      launch_config=_one_table_batch_config(f"{_LAUNCH}-b03-00-T_A"),
      writes=(),
      manual=None)
  assert ctx.tables_in_order == (f"{_LANDING}.T_A",)
  assert ctx.run_ids == (f"{_LAUNCH}-b03-00-T_A",)
  assert ctx.params_source == "logs"
  assert not [w for w in ctx.warnings if "launch_config discarded" in w]


@pytest.mark.parametrize(
    "run_id",
    [
        f"{_LAUNCH}-b03-01-T_A",  # another position
        f"{_LAUNCH}-b03-00-T_B",  # another table
        f"{_LAUNCH}-b04-00-T_A",  # another batch
    ])
def test_a_one_table_run_id_of_another_run_is_still_discarded(run_id):
  ctx = LaunchContext.from_sources(
      job=None,
      launch_config=_one_table_batch_config(run_id),
      writes=(),
      manual={"tables_in_order": [f"{_LANDING}.T_A"]})
  assert ctx.params_source == "manual"
  assert any("launch_config discarded" in w for w in ctx.warnings)


def _row_of(run_id: str, table: str, created: str, launch: str | None):
  return {
      "run_id": run_id,
      "landing_table": f"{_LANDING}.{table}",
      "created_at": created,
      "launch_run_id": launch,
  }


def test_a_recorded_launch_named_like_a_batch_is_not_claimed():
  """`runs_for(base_run_id="exp")` also returns a separate, later launch
  whose own run id is `exp-b12`: its record says so, so its rows are not
  batch 12 of `exp` but another launch's, dropped with the contamination
  note — as before ADR 0045."""
  rows = [
      _row_of("exp-00-T_A", "T_A", "2026-10-21T10:00:00.000000Z", "exp"),
      _row_of("exp-01-T_B", "T_B", "2026-10-21T10:00:01.000000Z", "exp"),
      _row_of("exp-b12-00-T_A", "T_A", "2026-10-21T11:00:00.000000Z",
              "exp-b12"),
  ]
  ctx = _by_base(rows, "exp")
  assert ctx.run_ids == ("exp-00-T_A", "exp-01-T_B")
  assert any(
      "another launch" in w and "'exp-b12-00-T_A'" in w for w in ctx.warnings)
  assert not [w for w in ctx.warnings if "several validation_runs rows" in w]


_GAP_NOTE = ("validation_runs has no row for position(s) [1] of {where} "
             "(run ids …-NN-<table>): those tables' runs failed, expired or "
             "were never recorded")


def test_a_gap_in_one_batch_is_not_masked_by_another_batch():
  """Every batch numbers its tables from 00: position 01 of b01 is missing
  although b00 has a row there."""
  rows = [
      _batch_row(0, 0, "T_A"),
      _batch_row(0, 1, "T_B"),
      _batch_row(0, 2, "T_C"),
      _batch_row(1, 0, "U_A"),
      _batch_row(1, 2, "U_C"),
  ]
  ctx = _by_base(rows, _LAUNCH)
  assert len(ctx.tables_in_order) == 5
  assert [w for w in ctx.warnings if "position" in w
         ] == [_GAP_NOTE.format(where=f"batch {_LAUNCH}-b01")]


def test_a_single_launchs_positions_are_checked_as_before():
  rows = [
      _row_of("nightly-00-T_A", "T_A", "2026-10-21T10:00:00.000000Z", None),
      _row_of("nightly-02-T_C", "T_C", "2026-10-21T10:00:02.000000Z", None),
  ]
  ctx = _by_base(rows, "nightly")
  assert [w for w in ctx.warnings if "position" in w] == [
      _GAP_NOTE.format(where="this launch"),
      "run ids ['nightly-02-T_C'] sit at positions beyond the 2 table(s) "
      "resolved: the launch covered more tables than are known",
  ]
