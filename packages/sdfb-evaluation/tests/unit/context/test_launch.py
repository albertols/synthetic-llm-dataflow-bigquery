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
from sdfb_evaluation.context.gcp import DataflowJobs, JobNotFoundError
from sdfb_evaluation.context.launch import LaunchContext, resolve_launch

PROJECT = "demo-project"
REGION = "europe-west1"
JOB_ID = "2026-09-13_06_10_16-9000000000000000017"
BASE = "thelook-0913-a1b2c3"
DS = "demo-project.thelook_synthetic"
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
