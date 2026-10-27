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
"""The four cache keys a launch reads and writes under, computed once on
the launcher (A1 ruling R14) and recorded in the snapshot (ADR 0044).

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

from sdfb_beam.cli import run_pipeline as rp
from sdfb_core.config.salt import salted, settings_salt
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.engines.b1_rag.engine import B1RagEngine
from sdfb_core.engines.base import GenerationContext
from sdfb_core.rag.embedding import embedder_identity
from sdfb_core.stats.source_stats import PROFILER_VERSION
from sdfb_tests.config_parity import ARGV_BASE
from sdfb_tests.fixtures import load_ddl

_MODEL = "gs://b/synthetic/models/m/v1/"


def _args(tmp_path, file_text: str | None = None):
  argv = list(ARGV_BASE)
  if file_text is not None:
    path = tmp_path / "c.yaml"
    path.write_text(file_text)
    argv.append(f"--config_uri={path}")
  args, _ = rp.parse_args(argv)
  return args


def test_defaults_are_the_unsalted_keys(tmp_path):
  keys = rp.launch_cache_keys(_args(tmp_path), RelationshipRegistry())
  assert keys == {
      "freetext_pools": _MODEL,
      "rag_chunks": embedder_identity("")[1],
      "source_stats": PROFILER_VERSION,
      "fk_fanout_stats": RelationshipRegistry().sha12(),
  }
  assert tuple(keys) == rp.CACHES


def test_a_pool_shaping_value_salts_the_pool_key_only(tmp_path):
  args = _args(tmp_path, "free_text: {pool_values_per_call: 16}\n")
  keys = rp.launch_cache_keys(args, RelationshipRegistry())
  salt = settings_salt(rp.config_of(args), "freetext_pools")
  assert salt and keys["freetext_pools"] == salted(_MODEL, salt)
  assert keys["rag_chunks"] == embedder_identity("")[1]
  assert keys["source_stats"] == PROFILER_VERSION


def test_cache_keys_of_reads_what_main_stored(tmp_path):
  args = _args(tmp_path)
  args._cache_keys = {"freetext_pools": "stored"}  # pylint: disable=protected-access
  assert rp.cache_keys_of(args) == {"freetext_pools": "stored"}
  del args._cache_keys  # pylint: disable=protected-access
  assert rp.cache_keys_of(args)["freetext_pools"] == _MODEL


class _Store:

  def __init__(self):
    self.keys: list[str] = []

  def fetch(self, digest, model_uri):
    del digest
    self.keys.append(model_uri)
    return []


def _ctx(**extra):
  return GenerationContext(
      table_schema=load_ddl("customers"),
      reference_digest="d",
      model_uri="gs://m/x",
      pool_store=_Store(),
      **extra)


def test_the_worker_fetches_under_the_key_the_launcher_carried():
  ctx = _ctx(pool_store_key="gs://m/x+cfg.carried00000")
  B1RagEngine()._stored_pools(ctx)  # pylint: disable=protected-access
  assert ctx.pool_store.keys == ["gs://m/x+cfg.carried00000"]


def test_a_hand_built_context_derives_the_key():
  ctx = _ctx()
  assert ctx.pool_key() == "gs://m/x"
