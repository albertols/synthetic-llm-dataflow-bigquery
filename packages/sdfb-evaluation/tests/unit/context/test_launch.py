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
resolution order, `params_source`, fallbacks and the registry filters."""

from __future__ import annotations

import pytest

from sdfb_evaluation import schemas
from sdfb_evaluation.context.bq import BqApiError
from sdfb_evaluation.context.gcp import DataflowJobs, JobNotFoundError
from sdfb_evaluation.context.launch import LaunchContext, resolve_launch

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
