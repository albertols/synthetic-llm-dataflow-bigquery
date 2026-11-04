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
"""The resolved config reaches the worker context (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from sdfb_beam import pipeline as pl
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import PipelineConfig
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.engines.base import GenerationContext
from sdfb_tests.fakes import FakeModelClient
from sdfb_tests.fixtures import load_ddl, load_reference


def test_defaults_everywhere():
  ctx = GenerationContext(table_schema=load_ddl("customers"))
  assert ctx.generator_config is DEFAULT_CONFIG
  cfg = PipelineConfig(
      table_schema=load_ddl("customers"),
      engine_name="b1_rag",
      model_client=FakeModelClient(reference_pool=[]),
      num_rows=1)
  assert cfg.generator_config is DEFAULT_CONFIG


def test_build_pipeline_copies_it_into_the_context(monkeypatch, tmp_path):
  seen = {}
  real = pl.GenerationContext

  def spy(**kwargs):
    seen["cfg"] = kwargs.get("generator_config")
    return real(**kwargs)

  monkeypatch.setattr(pl, "GenerationContext", spy)
  custom = GeneratorConfig.model_validate({"rag": {"rag_top_k": 5}})
  config = PipelineConfig(
      table_schema=load_ddl("customers"),
      engine_name="minimal",
      model_client=FakeModelClient(reference_pool=[]),
      num_rows=1,
      generator_config=custom)
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    pl.build_pipeline(
        p,
        reference_rows=load_reference("customers"),
        config=config,
        landing_sink=WriteToJsonLines(str(tmp_path / "l")),
        dlq_sink=WriteToJsonLines(str(tmp_path / "d")))
  assert seen["cfg"] is custom
