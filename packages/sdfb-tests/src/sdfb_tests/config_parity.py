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
"""Behaviour goldens for the generator-config refactor.

Observations of what the launcher and the pipeline DO, captured once from
master and compared on every later commit: the parsed namespace for a matrix
of argv shapes, the launcher's error for a set of bad argv, every promoted
constant's value, and DirectRunner output for b1_rag, b2_library and a
two-table relational launch with fixed seeds.

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
from pathlib import Path

GOLDENS_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" /
    "config_parity")

# Attributes the refactor adds to the namespace on purpose: the config file
# (ADR 0043) and the whole-model launch keys (ADR 0045). The namespace golden
# does not compare them; test_model_launch_keys.py pins the latter's
# defaults on every master argv instead.
MODEL_LAUNCH_ATTRS = frozenset({
    "source_dataset", "landing_dataset", "tables", "max_tables_per_job",
    "max_parallel_jobs", "prep_workers", "batch_index", "batch_count"
})
EXCLUDED_ATTRS = frozenset({"config_uri", "config_resolution"
                           }) | MODEL_LAUNCH_ATTRS

ARGV_BASE = [
    "--reference_table=p.src.t",
    "--landing_table=p.land.t",
    "--dlq_table=p.q.dlq",
    "--num_rows=10",
    "--run_id=r1",
    "--model_uri=gs://b/synthetic/models/m/v1/",
]

ARGV_CASES: dict[str, list[str]] = {
    "minimal":
        ARGV_BASE,
    "every_flag_non_default": [
        *ARGV_BASE,
        "--ddl_uri=gs://b/ddl.json",
        "--reference_rows_limit=500",
        "--rag_row_docs_limit=64",
        "--write_disposition=overwrite",
        "--create_if_not_exists=true",
        "--autoscaling=fixed",
        "--initial_workers=3",
        "--batch_size=40",
        "--similarity=0.8",
        "--seed=42",
        "--identity_cols=a,b",
        "--pk_cols=a",
        "--engine=b2_library",
        "--embedder_uri=gs://b/synthetic/models/embedders/bge/v1/",
        "--build_rag_layer=true",
        "--rag_chunks_table=p.r.chunks",
        "--build_pool_layer=true",
        "--freetext_pools_table=p.r.pools",
        "--uniqueness_mode=streaming",
        "--fk_fanout_stats_table=p.q.fanout",
        "--fk_candidate_cap=8",
        "--driven_uniqueness_mode=exact",
        "--on_model_conflict=stop",
        "--pool_seed_strategy=kcenter",
        "--pool_pattern_guidance=true",
        "--freetext_expansion=all",
        "--prompt_constraints=off",
        "--prompt_debug=redacted",
        "--source_stats=exact",
        "--source_stats_table=p.q.stats",
        "--source_stats_json=gs://b/stats.json",
        "--generate_fk_relationships=false",
        "--fk_parent_landing=p.other",
        "--multi_table_mode=sequential_jobs",
        "--relationships_uri=config/relationships/example_retail.yaml",
        "--validation_runs_table=p.q.vr",
        "--env=prd",
        "--thresholds_uri=gs://b/thresholds.yml",
        "--client_type=fake",
        "--vllm_dtype=float16",
        "--vllm_max_model_len=4096",
    ],
    "string_bools_and_empties": [
        *ARGV_BASE,
        "--create_if_not_exists=yes",
        "--build_rag_layer",
        "--rag_chunks_table=p.r.chunks",
        "--build_pool_layer=false",
        "--generate_fk_relationships=0",
        "--initial_workers=",
        "--vllm_max_model_len=",
        "--seed=",
        "--rag_row_docs_limit=",
    ],
    "beam_passthrough": [
        *ARGV_BASE,
        "--runner=DirectRunner",
        "--temp_location=gs://b/tmp",
        "--disk_size_gb=50",
    ],
    "abbreviated_flag": [
        "--reference_table=p.src.t",
        "--landing_table=p.land.t",
        "--dlq_table=p.q.dlq",
        "--num_row=10",
        "--run_id=r1",
        "--model_uri=gs://b/synthetic/models/m/v1/",
    ],
    "multi_target": [
        "--reference_table=p.src.a",
        "--landing_table=p.land.a,p.land.b",
        "--dlq_table=p.q.dlq",
        "--num_rows=10",
        "--run_id=r1",
        "--model_uri=gs://b/synthetic/models/m/v1/",
    ],
}

ERROR_CASES: dict[str, list[str]] = {
    "missing_required": ["--reference_table=p.src.t"],
    "rag_layer_without_table": [*ARGV_BASE, "--build_rag_layer=true"],
    "pool_layer_without_table": [*ARGV_BASE, "--build_pool_layer=true"],
    "bad_choice": [*ARGV_BASE, "--uniqueness_mode=nope"],
    "bad_candidate_cap": [*ARGV_BASE, "--fk_candidate_cap=0"],
    "bad_row_docs": [*ARGV_BASE, "--rag_row_docs_limit=0"],
    "bad_int": [*ARGV_BASE, "--batch_size=many"],
}

# Every constant this phase promotes into the config model (knob id →
# master module + attribute). `pool_stagnation_min_novel` is recorded as its
# master value (4 = max(1, 32 // 8)).
CONSTANT_SOURCES: dict[str, tuple[str, str]] = {
    "top_values_max_distinct":
        ("sdfb_core.stats.source_stats", "_TOP_VALUES_MAX_DISTINCT"),
    "top_values_top_k": ("sdfb_core.stats.source_stats", "_TOP_VALUES_TOP_K"),
    "shape_mix_top_k": ("sdfb_core.stats.source_stats", "_SHAPE_MIX_TOP_K"),
    "length_percentiles": ("sdfb_core.stats.source_stats", "_LEN_PCTS"),
    "null_pattern_max_cols":
        ("sdfb_core.stats.source_stats", "_NULL_PATTERN_MAX_COLS"),
    "null_pattern_top_k":
        ("sdfb_core.stats.source_stats", "_NULL_PATTERN_TOP_K"),
    "temporal_max_age_years_b1":
        ("sdfb_core.engines.b1_rag.profile", "_MAX_TEMPORAL_AGE_YEARS"),
    "temporal_max_age_years_b2":
        ("sdfb_core.engines.b2_library.fidelity", "_MAX_TEMPORAL_AGE_YEARS"),
    "free_text_pool_max":
        ("sdfb_core.engines.generation_plan", "FREE_TEXT_POOL_MAX"),
    "pool_values_per_call":
        ("sdfb_core.engines.b1_rag.engine", "_POOL_VALUES_PER_CALL"),
    "pool_parallel_choices":
        ("sdfb_core.engines.b1_rag.engine", "_POOL_PARALLEL_CHOICES"),
    "pool_stagnation_window":
        ("sdfb_core.engines.b1_rag.engine", "_POOL_STAGNATION_WINDOW"),
    "pool_stagnation_min_novel":
        ("sdfb_core.engines.b1_rag.engine", "_POOL_STAGNATION_MIN_NOVEL"),
    "pool_format_collapse_rounds":
        ("sdfb_core.engines.b1_rag.engine", "_POOL_FORMAT_COLLAPSE_ROUNDS"),
    "pool_build_max_workers":
        ("sdfb_core.engines.b1_rag.engine", "_POOL_BUILD_MAX_WORKERS"),
    "source_domain_cap": ("sdfb_beam.io.source_values", "_DEFAULT_CAP"),
    "free_text_unique_ratio":
        ("sdfb_core.engines.b1_rag.profile", "_FREE_TEXT_UNIQUE_RATIO"),
    "free_text_min_mean_len":
        ("sdfb_core.engines.b1_rag.profile", "_FREE_TEXT_MIN_MEAN_LEN"),
    "free_text_max_categories":
        ("sdfb_core.engines.b1_rag.profile", "_FREE_TEXT_MAX_CATEGORIES"),
    "max_free_text_values_per_column":
        ("sdfb_core.rag.chunking", "MAX_FREE_TEXT_VALUES_PER_COLUMN"),
    "rag_top_k": ("sdfb_core.engines.b1_rag.engine", "_DEFAULT_TOP_K"),
    "embedder_min_free_vram":
        ("sdfb_core.rag.embedding", "_MIN_FREE_VRAM_BYTES"),
    "repeat_share_tolerance":
        ("sdfb_core.contracts.model_adjustment", "REPEAT_SHARE_TOLERANCE"),
    "fk_key_sample_floor":
        ("sdfb_core.engines.pk_capacity", "FK_KEY_SAMPLE_FLOOR"),
    "fk_key_sample_ceiling":
        ("sdfb_core.engines.pk_capacity", "FK_KEY_SAMPLE_CEILING"),
    "fk_key_sample_margin":
        ("sdfb_core.engines.pk_capacity", "FK_KEY_SAMPLE_MARGIN"),
    "max_conditional_values_per_request":
        ("sdfb_beam.cli.run_pipeline", "_MAX_CONDITIONAL_VALUES_PER_REQUEST"),
}


def jsonable(value):
  """Plain JSON for any namespace value: tuples → lists, sets sorted."""
  if isinstance(value, (list, tuple)):
    return [jsonable(v) for v in value]
  if isinstance(value, (set, frozenset)):
    return sorted(jsonable(v) for v in value)
  if isinstance(value, dict):
    return {str(k): jsonable(v) for k, v in sorted(value.items())}
  if value is None or isinstance(value, (bool, int, float, str)):
    return value
  return repr(value)


def _error_text(stderr: str) -> str:
  """The argparse message without the program name (pytest vs python)."""
  lines = [line for line in stderr.strip().splitlines() if line]
  return lines[-1].split("error: ", 1)[-1] if lines else ""


def observe_namespaces() -> dict:
  """`parse_args` over the argv matrix, and the error for each bad argv."""
  from sdfb_beam.cli import run_pipeline as rp  # pylint: disable=import-outside-toplevel  # heavy import, test-time only
  out: dict = {}
  for name, argv in ARGV_CASES.items():
    args, beam_argv = rp.parse_args(list(argv))
    out[name] = {
        "args": {
            k: jsonable(v)
            for k, v in sorted(vars(args).items())
            if k not in EXCLUDED_ATTRS
        },
        "beam_argv": list(beam_argv),
    }
  for name, argv in ERROR_CASES.items():
    err = io.StringIO()
    code: object = None
    with contextlib.redirect_stderr(err):
      try:
        rp.parse_args(list(argv))
      except SystemExit as e:
        code = e.code
    out[name] = {"exit": jsonable(code), "error": _error_text(err.getvalue())}
  return out


def observe_parser() -> dict:
  """Master only: every argparse action's default, choices and requiredness."""
  from sdfb_tests.launch_surface import generator_parser  # pylint: disable=import-outside-toplevel  # test-time only
  parser = generator_parser()
  out = {}
  for action in parser._actions:  # pylint: disable=protected-access  # argparse exposes no public action list
    if action.dest == "help":
      continue
    out[action.dest] = {
        "default": jsonable(action.default),
        "choices": jsonable(list(action.choices)) if action.choices else None,
        "required": bool(action.required),
        "nargs": action.nargs,
        "const": jsonable(action.const),
    }
  return out


def observe_constants() -> dict:
  """Master only: the value of every constant this phase promotes."""
  out = {}
  for knob, (module, attr) in sorted(CONSTANT_SOURCES.items()):
    out[knob] = jsonable(getattr(importlib.import_module(module), attr))
  return out


def _normalise(rows: list[dict], drop: tuple[str, ...]) -> list[dict]:
  cleaned = [{k: v for k, v in r.items() if k not in drop} for r in rows]
  return sorted(cleaned, key=lambda r: json.dumps(r, sort_keys=True))


def _read_jsonl(prefix: Path) -> list[dict]:
  rows: list[dict] = []
  for f in sorted(prefix.parent.glob(prefix.name + "*")):
    rows.extend(json.loads(line) for line in f.read_text().splitlines() if line)
  return rows


def _run_single(tmp: Path, engine: str, seed: int) -> dict:
  import apache_beam as beam  # pylint: disable=import-outside-toplevel  # test-time only
  from apache_beam.options.pipeline_options import PipelineOptions  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_beam.io.local_sinks import WriteToJsonLines  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_beam.pipeline import PipelineConfig, build_pipeline  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_core.validation import Thresholds  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_tests.fakes import FakeModelClient  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_tests.fixtures import load_ddl, load_reference  # pylint: disable=import-outside-toplevel  # test-time only

  schema, reference = load_ddl("customers"), load_reference("customers")
  config = PipelineConfig(
      table_schema=schema,
      engine_name=engine,
      model_client=FakeModelClient(reference_pool=reference),
      num_rows=30,
      batch_size=10,
      seed=seed,
      run_id=f"parity-{engine}",
      reference_table="proj.ds.customers",
      landing_table="proj.ds.customers_landing",
      identity_columns=("customer_id",),
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
      fail_on_blocker=False,
  )
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_pipeline(
        p,
        reference_rows=reference,
        config=config,
        landing_sink=WriteToJsonLines(str(tmp / "landing"), num_shards=1),
        dlq_sink=WriteToJsonLines(str(tmp / "dlq"), num_shards=1),
        validation_runs_sink=WriteToJsonLines(str(tmp / "vr"), num_shards=1),
    )
  return {
      "landing": _normalise(_read_jsonl(tmp / "landing"), ()),
      "dlq": _normalise(_read_jsonl(tmp / "dlq"), ("dlq_inserted_at",)),
      "validation_runs": _normalise(_read_jsonl(tmp / "vr"), ("created_at",)),
  }


def _run_relational(tmp: Path, seed: int) -> dict:
  import apache_beam as beam  # pylint: disable=import-outside-toplevel  # test-time only
  from apache_beam.options.pipeline_options import PipelineOptions  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_beam.io.local_sinks import WriteToJsonLines  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_beam.pipeline import (  # pylint: disable=import-outside-toplevel  # test-time only
      FkEdgeSpec, PipelineConfig, TableSpec, build_relational_pipeline)
  from sdfb_core.contracts import TableSchema  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_tests.fakes import FakeModelClient  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_tests.fixtures import load_ddl, load_reference  # pylint: disable=import-outside-toplevel  # test-time only

  child_schema = TableSchema.model_validate({
      "table_info": {
          "table_id": "p.src.orders_flat"
      },
      "schema": [
          {
              "name": "ORDER_ID",
              "type": "STRING",
              "mode": "REQUIRED"
          },
          {
              "name": "CUST_ID",
              "type": "INT64",
              "mode": "REQUIRED"
          },
          {
              "name": "AMOUNT",
              "type": "INT64",
              "mode": "REQUIRED"
          },
      ],
  })
  child_reference = [{
      "ORDER_ID": f"ORD{i:05d}",
      "CUST_ID": 900000 + i,
      "AMOUNT": i * 3
  } for i in range(40)]
  parent_schema, parent_reference = load_ddl("customers"), load_reference(
      "customers")
  parent = PipelineConfig(
      table_schema=parent_schema,
      engine_name="b1_rag",
      model_client=FakeModelClient(reference_pool=parent_reference),
      num_rows=50,
      batch_size=25,
      seed=seed,
      run_id="parity-rel-parent",
      landing_table="p.land.customers",
      log_table_prefix="customers",
      identity_columns=("customer_id",))
  child = PipelineConfig(
      table_schema=child_schema,
      engine_name="b1_rag",
      model_client=FakeModelClient(reference_pool=child_reference),
      num_rows=80,
      batch_size=40,
      seed=seed + 1,
      run_id="parity-rel-child",
      landing_table="p.land.orders_flat",
      log_table_prefix="orders_flat")
  specs = [
      TableSpec(
          config=parent,
          reference_rows=parent_reference,
          landing_sink=WriteToJsonLines(str(tmp / "parent"), num_shards=1),
          dlq_sink=WriteToJsonLines(str(tmp / "dlq_parent"), num_shards=1)),
      TableSpec(
          config=child,
          reference_rows=child_reference,
          landing_sink=WriteToJsonLines(str(tmp / "child"), num_shards=1),
          dlq_sink=WriteToJsonLines(str(tmp / "dlq_child"), num_shards=1),
          parent_edges=(FkEdgeSpec(
              child_cols=("CUST_ID",),
              ref_cols=("customer_id",),
              parent_landing="p.land.customers",
              parent_pk=("customer_id",)),)),
  ]
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_relational_pipeline(p, specs)
  return {
      "parent":
          _normalise(_read_jsonl(tmp / "parent"), ()),
      "child":
          _normalise(_read_jsonl(tmp / "child"), ()),
      "dlq_parent":
          _normalise(_read_jsonl(tmp / "dlq_parent"), ("dlq_inserted_at",)),
      "dlq_child":
          _normalise(_read_jsonl(tmp / "dlq_child"), ("dlq_inserted_at",)),
  }


def observe_pipelines(tmp: Path) -> dict:
  """DirectRunner output of the three parity scenarios (fixed seeds)."""
  from sdfb_beam.dofns.generate import _reset_engine_registry  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_core.engines.b1_rag.engine import clear_free_text_pool_cache  # pylint: disable=import-outside-toplevel  # test-time only
  from sdfb_core.engines.generation_plan import clear_generation_plan_log  # pylint: disable=import-outside-toplevel  # test-time only
  out = {}
  try:
    for name, run in (
        ("b1_rag", lambda d: _run_single(d, "b1_rag", 7)),
        ("b2_library", lambda d: _run_single(d, "b2_library", 11)),
        ("relational", lambda d: _run_relational(d, 13)),
    ):
      _reset_engine_registry()
      clear_free_text_pool_cache()
      (tmp / name).mkdir(parents=True, exist_ok=True)
      out[name] = run(tmp / name)
  finally:
    # The worker logs each plan (and its relationship card) once per
    # process; leaving these runs' keys behind silences the same plan in
    # the next test that builds the same engine/digest/table.
    clear_generation_plan_log()
  return out
