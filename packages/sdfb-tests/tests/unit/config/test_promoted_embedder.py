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
"""Embedder constants come from the config; a changed value salts the chunk key
(ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""

# Test module: white-box access and local imports are intentional.
# pylint: disable=import-outside-toplevel,protected-access,unused-argument

from __future__ import annotations

import argparse
import types

from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.config.salt import salted, settings_salt
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag import engine as b1e
from sdfb_core.engines.base import GenerationContext
from sdfb_core.rag import embedding
from sdfb_core.rag.embedding import HashingEmbedder, embedder_identity

_URI = "gs://b/models/embedders/bge/v1/"


def _cfg(**rag) -> GeneratorConfig:
  return GeneratorConfig.model_validate({"rag": rag})


def _schema() -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "demo.t"
      },
      "schema": [{
          "name": "v",
          "type": "STRING",
          "mode": "REQUIRED"
      }],
  })


def test_vram_constant_is_gone():
  assert not hasattr(embedding, "_MIN_FREE_VRAM_BYTES")


def test_bge_defaults_match_master():
  bge = embedding.BgeEmbedder("/unused")
  assert bge._max_length == DEFAULT_CONFIG.rag.bge_max_length == 512
  assert bge._min_free_vram == DEFAULT_CONFIG.rag.embedder_min_free_vram


def test_bge_takes_the_config_value():
  bge = embedding.BgeEmbedder("/unused", max_length=256, min_free_vram=7)
  assert bge._max_length == 256
  assert bge._min_free_vram == 7


def test_auto_device_threshold_is_a_parameter():
  torch = types.SimpleNamespace(
      cuda=types.SimpleNamespace(
          is_available=lambda: True, mem_get_info=lambda: (100, 200)))
  assert embedding._resolve_auto_device(torch, 100) == "cuda"
  assert embedding._resolve_auto_device(torch, 101) == "cpu"


def test_chunk_key_salt():
  cfg = _cfg(bge_max_length=256)
  assert salted("v1", settings_salt(DEFAULT_CONFIG, "rag_chunks")) == "v1"
  assert salted("v1", settings_salt(cfg, "rag_chunks")).startswith("v1+cfg.")


# --- wiring ------------------------------------------------------------------


def test_engine_builds_its_embedder_from_the_config(monkeypatch):
  seen: dict = {}

  def spy(uri, **kw):
    seen.update(uri=uri, **kw)
    return HashingEmbedder(dim=8)

  from sdfb_tests.fakes import FakeModelClient

  monkeypatch.setattr(b1e, "BgeEmbedder", spy)
  ctx = GenerationContext(
      table_schema=_schema(),
      reference_rows=[],
      reference_digest="d",
      model_uri=_URI,
      embedder_uri=_URI,
      pipeline_run_id="r",
      num_rows=1,
      generator_config=_cfg(bge_max_length=128, embedder_min_free_vram=1234),
  )
  b1e.B1RagEngine().setup(FakeModelClient(reference_pool=[{}]), ctx)
  assert seen["max_length"] == 128
  assert seen["min_free_vram"] == 1234
  assert seen["device"] == "auto"


def test_population_dofn_gets_the_config_values():
  import apache_beam as beam
  from sdfb_beam import pipeline as pl
  from sdfb_beam.rag.population import EmbedChunksDoFn

  config = pl.PipelineConfig(
      table_schema=_schema(),
      engine_name="fake",
      model_client=None,
      num_rows=1,
      generator_config=_cfg(bge_max_length=128, embedder_min_free_vram=1234),
  )
  p = beam.Pipeline()
  pl._population_branch(p, config, [{"v": "a"}], "dig", label_prefix="")

  def find(node):
    for part in getattr(node, "parts", []):
      fn = getattr(part.transform, "fn", None)
      if isinstance(fn, EmbedChunksDoFn):
        yield fn
      yield from find(part)

  (dofn,) = list(find(p.transforms_stack[0]))
  assert dofn.max_length == 128
  assert dofn.min_free_vram == 1234


def test_launcher_version_is_unsalted_at_defaults():
  from sdfb_beam.cli.run_pipeline import resolve_embedder_identity

  args = argparse.Namespace(embedder_uri=_URI)
  assert resolve_embedder_identity(args) == embedder_identity(_URI)


def test_launcher_version_is_salted_for_a_chunk_shaping_value():
  from sdfb_beam.cli.run_pipeline import resolve_embedder_identity

  args = argparse.Namespace(
      embedder_uri=_URI,
      config_resolution=types.SimpleNamespace(config=_cfg(bge_max_length=256)))
  embedder_id, version = resolve_embedder_identity(args)
  assert embedder_id == embedder_identity(_URI)[0]
  assert version.startswith(embedder_identity(_URI)[1] + "+cfg.")
