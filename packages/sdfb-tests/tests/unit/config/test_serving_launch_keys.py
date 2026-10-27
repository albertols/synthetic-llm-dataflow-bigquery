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
"""The serving launch keys and master's DAG defaults (ADR 0043).

`serving.gpu`, `serving.sdk_containers` and `serving.launcher_machine_type`
were Composer-only parameters; they are settings now, with master's DAG
defaults. `serving.vllm_dtype` defaults to null, which derives the dtype
from the GPU (t4 -> float16, l4 -> bfloat16); an explicit value wins.
`generation.num_rows` defaults to 1000 and is no longer required; both
build layers default to true, a no-op without their table.

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
from pathlib import Path

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions, SetupOptions
from sdfb_beam.cli import run_pipeline as rp
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import build_pipeline
from sdfb_core.config.model import DEFAULT_CONFIG, value_of
from sdfb_core.config.resolve import (ConfigError, missing_required,
                                      parse_config_text, resolve)
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.engines.generation_plan import clear_generation_plan_log
from sdfb_tests import config_parity as cp
from sdfb_tests.fixtures import load_ddl, load_reference

_REPO = Path(__file__).parents[5]
_METADATA = _REPO / "docker" / "flex_template_metadata.json"


def _milestones(caplog, name: str) -> list[str]:
  return [
      r.getMessage()
      for r in caplog.records
      if f"SDFB_MILESTONE name={name} " in r.getMessage()
  ]


def test_t4_derives_float16():
  config = resolve({"gpu": "t4"}).config
  assert config.serving.vllm_dtype is None
  assert value_of(config, "serving.vllm_dtype") == "float16"


def test_l4_derives_bfloat16():
  config = resolve({"gpu": "l4"}).config
  assert value_of(config, "serving.vllm_dtype") == "bfloat16"
  # master's L4 default, and so the default of every launch that names none
  assert DEFAULT_CONFIG.serving.gpu == "l4"
  assert value_of(DEFAULT_CONFIG, "serving.vllm_dtype") == "bfloat16"


def test_explicit_vllm_dtype_wins_over_the_gpu_derivation(tmp_path, caplog):
  path = tmp_path / "launch.yaml"
  path.write_text("serving: {gpu: t4, vllm_dtype: auto}\n")
  args, _ = rp.parse_args([*cp.ARGV_BASE, f"--config_uri={path}"])
  assert args.vllm_dtype == "auto" and args.gpu == "t4"
  with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
    rp.log_vllm_dtype(args.config_resolution)
  assert _milestones(caplog, "vllm_dtype_resolved") == [
      "SDFB_MILESTONE name=vllm_dtype_resolved dtype=auto gpu=t4 source=file"
  ]


@pytest.mark.parametrize("argv, line", [
    ([], "dtype=bfloat16 gpu=l4 source=derived"),
    (["--gpu=t4"], "dtype=float16 gpu=t4 source=derived"),
    (["--gpu=t4", "--vllm_dtype=bfloat16"], "dtype=bfloat16 gpu=t4 source=flag")
])
def test_the_milestone_says_which_dtype_and_why(argv, line, caplog):
  args, _ = rp.parse_args([*cp.ARGV_BASE, *argv])
  with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
    rp.log_vllm_dtype(args.config_resolution)
  assert _milestones(caplog, "vllm_dtype_resolved") == [
      f"SDFB_MILESTONE name=vllm_dtype_resolved {line}"
  ]


def test_a_file_null_dtype_leaves_the_flag_or_the_derivation():
  values = parse_config_text("serving: {vllm_dtype: null}\n", source="f")
  assert resolve({"vllm_dtype": "float16"}, values).config.serving.vllm_dtype \
      == "float16"
  assert value_of(resolve({
      "gpu": "t4"
  }, values).config, "serving.vllm_dtype") == "float16"


def test_the_launch_keys_have_masters_dag_defaults_and_choices():
  serving = DEFAULT_CONFIG.serving
  assert (serving.gpu, serving.sdk_containers,
          serving.launcher_machine_type) == ("l4", "multi", "e2-standard-8")
  for key, bad in (("gpu", "a100"), ("sdk_containers", "many")):
    with pytest.raises(ConfigError, match=f"serving.{key}"):
      resolve({}, {"serving": {key: bad}})
  args, _ = rp.parse_args([
      *cp.ARGV_BASE, "--gpu=t4", "--sdk_containers=single",
      "--launcher_machine_type=n1-standard-4"
  ])
  assert (args.gpu, args.sdk_containers,
          args.launcher_machine_type) == ("t4", "single", "n1-standard-4")


def test_num_rows_defaults_to_1000_and_is_not_required():
  config = resolve({}).config
  assert config.generation.num_rows == 1000
  assert "num_rows" not in missing_required(config)
  argv = [a for a in cp.ARGV_BASE if not a.startswith("--num_rows")]
  args, _ = rp.parse_args(argv)
  assert args.num_rows == 1000


def test_build_layers_default_true():
  assert DEFAULT_CONFIG.rag.build_rag_layer == "true"
  assert DEFAULT_CONFIG.free_text.build_pool_layer == "true"
  args, _ = rp.parse_args(list(cp.ARGV_BASE))
  assert (args.build_rag_layer, args.build_pool_layer) == ("true", "true")


@pytest.mark.parametrize("flag, table",
                         [("build_rag_layer", "rag_chunks_table"),
                          ("build_pool_layer", "freetext_pools_table")])
def test_a_typed_true_without_its_table_is_still_refused(flag, table, capsys):
  """Master's error stands for a value someone set; only the default is
    the no-op."""
  with pytest.raises(SystemExit):
    rp.parse_args([*cp.ARGV_BASE, f"--{flag}=true"])
  assert f"--{flag} requires --{table}" in capsys.readouterr().err


@pytest.mark.parametrize("section, flag", [("rag", "build_rag_layer"),
                                           ("free_text", "build_pool_layer")])
def test_a_file_true_without_its_table_is_the_noop(section, flag, tmp_path):
  """A config file may be a replayed record, whose values hold every key."""
  path = tmp_path / "launch.yaml"
  path.write_text(f"{section}: {{{flag}: true}}\n")
  args, _ = rp.parse_args([*cp.ARGV_BASE, f"--config_uri={path}"])
  assert getattr(args, flag) == "true"


@pytest.fixture(name="fake_reads")
def fixture_fake_reads(monkeypatch):
  """The launcher's BigQuery-facing reads, answered from the fixtures."""
  schema, rows = load_ddl("customers"), load_reference("customers")
  configure = rp.configure_pipeline_options

  def configure_without_main_session(options, *a, **k):
    configure(options, *a, **k)
    # pytest's __main__ is not picklable; the test needs no main session.
    options.view_as(SetupOptions).save_main_session = False

  monkeypatch.setattr(rp, "resolve_schemas", lambda *a, **k: (schema, None))
  monkeypatch.setattr(rp, "load_reference_rows", lambda *a, **k: list(rows))
  monkeypatch.setattr(rp, "_resolve_table_fanout", lambda *a, **k: (None, {}))
  monkeypatch.setattr(rp, "report_model_adjustments", lambda *a, **k: ())
  monkeypatch.setattr(rp, "report_row_projection", lambda *a, **k: ())
  monkeypatch.setattr(rp, "configure_pipeline_options",
                      configure_without_main_session)
  return rows


def _fake_launch(monkeypatch, *extra: str):
  """One fake-client launch through the launcher: the spec it builds."""
  specs: list = []

  def capture(p, given):
    specs.extend(given)
    _ = p | beam.Create([0])  # a pipeline needs one transform to run
    return {}

  monkeypatch.setattr(rp, "build_relational_pipeline", capture)
  args, beam_argv = rp.parse_args([
      "--reference_table=proj.src.customers",
      "--landing_table=proj.land.customers", "--dlq_table=proj.q.dlq",
      "--num_rows=20", "--run_id=r1",
      "--model_uri=gs://b/synthetic/models/m/v1/", "--client_type=fake",
      "--seed=7", "--relationships_uri=", "--runner=DirectRunner", *extra
  ])
  registry = RelationshipRegistry()
  plan = rp.plan_launch(["proj.land.customers"], "proj.src.customers", True,
                        registry, "r1")
  assert rp._run_relational_job(plan, args, beam_argv, registry=registry) == 0  # pylint: disable=protected-access
  assert len(specs) == 1, specs  # one table, one spec
  return args, specs[0]


def _landed(tmp: Path, spec) -> list[dict]:
  clear_generation_plan_log()
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_pipeline(
        p,
        reference_rows=spec.reference_rows,
        config=spec.config,
        landing_sink=WriteToJsonLines(str(tmp / "landing"), num_shards=1),
        dlq_sink=WriteToJsonLines(str(tmp / "dlq"), num_shards=1),
    )
  rows = [
      json.loads(line)
      for f in sorted(tmp.glob("landing*"))
      for line in f.read_text().splitlines()
      if line
  ]
  return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))


def _without(config, *names: str) -> dict:
  return {
      f.name: getattr(config, f.name)
      for f in dataclasses.fields(config)
      if f.name not in names
  }


@pytest.mark.usefixtures("fake_reads")
def test_build_layers_true_without_tables_is_a_noop(tmp_path, monkeypatch):
  on_args, on = _fake_launch(monkeypatch)
  off_args, off = _fake_launch(monkeypatch, "--build_rag_layer=",
                               "--build_pool_layer=")
  assert (on_args.build_rag_layer, on_args.build_pool_layer) == ("true", "true")
  assert (off_args.build_rag_layer, off_args.build_pool_layer) == ("", "")
  assert on_args.rag_chunks_table == on_args.freetext_pools_table == ""
  # Identical configs, but for the two settings themselves (the record
  # states them) and the fake client, a fresh object per launch.
  assert _without(on.config, "generator_config",
                  "model_client") == _without(off.config, "generator_config",
                                              "model_client")
  on_values = on.config.generator_config.model_dump()
  off_values = off.config.generator_config.model_dump()
  assert on_values["rag"].pop("build_rag_layer") == "true"
  assert off_values["rag"].pop("build_rag_layer") == ""
  assert on_values["free_text"].pop("build_pool_layer") == "true"
  assert off_values["free_text"].pop("build_pool_layer") == ""
  assert on_values == off_values
  assert on.reference_rows == off.reference_rows
  rows = _landed(tmp_path / "on", on)
  assert rows and rows == _landed(tmp_path / "off", off)


def _param(name: str) -> dict:
  metadata = json.loads(_METADATA.read_text())
  (param,) = [p for p in metadata["parameters"] if p["name"] == name]
  return param


def test_flex_metadata_declares_the_serving_launch_keys():
  # Optional means omitted, never '': an empty gpu or sdk_containers is
  # refused by argparse's choices, and an empty launcher_machine_type would
  # record no VM, so the template refuses '' before a launcher VM starts.
  for name, good, bad in (("gpu", ("l4", "t4"), "a100"),
                          ("sdk_containers", ("multi", "single"), "many"),
                          ("launcher_machine_type", ("e2-standard-8",
                                                     "n1-highmem-4"), "e2 8")):
    param = _param(name)
    assert param["isOptional"] is True
    (regex,) = param["regexes"]
    for value in good:
      assert re.fullmatch(regex, value), (name, value)
    assert not re.fullmatch(regex, bad), (name, bad)
    assert not re.fullmatch(regex, ""), name
  # The template states no default the yaml would contradict.
  dtype = _param("vllm_dtype")["helpText"]
  assert "auto (default)" not in dtype
  assert "gpu" in dtype
  assert "Required" not in _param("num_rows")["helpText"]


@pytest.mark.parametrize("source", ["flag", "file"])
def test_an_empty_launcher_machine_type_is_refused(source):
  """'' would record no launcher VM; neither a flag nor a file may set it."""
  typed = {
      "reference_table": "p.src.A_TABLE",
      "landing_table": "p.land.A_TABLE",
      "dlq_table": "p.q.dlq",
      "run_id": "r1",
  }
  file = None
  if source == "flag":
    typed["launcher_machine_type"] = ""
  else:
    file = parse_config_text(
        "serving: {launcher_machine_type: ''}\n", source="f.yaml")
  with pytest.raises(ConfigError, match="launcher_machine_type"):
    resolve(typed, file, config_uri="f.yaml" if file else None)
