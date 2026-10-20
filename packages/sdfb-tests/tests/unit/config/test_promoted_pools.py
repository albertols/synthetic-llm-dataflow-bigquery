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
"""Free-text pool constants come from the config, and salt the pool key
(ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""

# Test module: white-box access and local imports are intentional.
# pylint: disable=import-outside-toplevel,protected-access,unused-argument

from __future__ import annotations

import argparse
import inspect
import types

import pytest
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.config.salt import pool_store_model_key
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag import engine as b1e
from sdfb_core.engines.base import GenerationContext
from sdfb_core.pools import FreeTextPool, InMemoryFreeTextPoolStore
from sdfb_core.rag.embedding import HashingEmbedder

_URI = "gs://b/m/v1/"
_DIGEST = "digest-promoted-pools"


def test_module_constants_are_gone():
  for name in ("_DEFAULT_TOP_K", "_FREE_TEXT_POOL_MAX", "_POOL_VALUES_PER_CALL",
               "_POOL_PARALLEL_CHOICES", "_POOL_STAGNATION_WINDOW",
               "_POOL_STAGNATION_MIN_NOVEL", "_POOL_FORMAT_COLLAPSE_ROUNDS",
               "_POOL_BUILD_MAX_WORKERS", "_DEFAULT_FREE_TEXT_POOL"):
    assert not hasattr(b1e, name), name


def test_pool_key_is_unchanged_at_defaults():
  assert pool_store_model_key("gs://b/m/v1/", DEFAULT_CONFIG) == "gs://b/m/v1/"


def test_pool_key_changes_when_the_ladder_does():
  cfg = GeneratorConfig.model_validate(
      {"free_text": {
          "pool_values_per_call": 16
      }})
  assert pool_store_model_key("gs://b/m/v1/",
                              cfg).startswith("gs://b/m/v1/+cfg.")


def test_ladder_helpers_take_the_section_not_module_defaults():
  for fn in (b1e._pool_llm_yield, b1e._collapse_rounds):
    params = inspect.signature(fn).parameters
    assert params["ladder"].kind is inspect.Parameter.KEYWORD_ONLY, fn.__name__
  params = inspect.signature(b1e._pool_llm_yield).parameters
  assert params["target"].default is inspect.Parameter.empty
  assert params["n_choices"].default is inspect.Parameter.empty


# --- the other promoted names, and their new seams ---------------------------


def test_the_shared_caps_are_gone_too():
  from sdfb_beam.io import source_values
  from sdfb_core.engines import generation_plan
  from sdfb_core.rag import chunking

  assert not hasattr(generation_plan, "FREE_TEXT_POOL_MAX")
  assert not hasattr(chunking, "MAX_FREE_TEXT_VALUES_PER_COLUMN")
  assert not hasattr(source_values, "_DEFAULT_CAP")


def test_the_source_value_store_has_no_cap_of_its_own():
  from sdfb_beam.io.source_values import BigQuerySourceValueStore

  cap = inspect.signature(BigQuerySourceValueStore).parameters["cap"]
  assert cap.kind is inspect.Parameter.KEYWORD_ONLY
  assert cap.default is inspect.Parameter.empty


def test_the_chunking_cap_defaults_to_the_config():
  from sdfb_core.rag.chunking import distinct_free_text_values

  cap = inspect.signature(distinct_free_text_values).parameters["cap"]
  assert cap.kind is inspect.Parameter.KEYWORD_ONLY
  assert cap.default == DEFAULT_CONFIG.rag.max_free_text_values_per_column


# --- fixtures ----------------------------------------------------------------


def _cfg(section: str, **kw) -> GeneratorConfig:
  return GeneratorConfig.model_validate({section: kw})


_LADDER = _cfg("free_text", pool_values_per_call=16)
# (config, the model_uri every pool-store call must carry)
_KEYS = [
    pytest.param(DEFAULT_CONFIG, _URI, id="defaults"),
    pytest.param(
        _LADDER, pool_store_model_key(_URI, _LADDER), id="changed-ladder"),
]


@pytest.fixture(autouse=True)
def _fresh_caches():
  from sdfb_beam.io.source_values import clear_source_value_cache
  from sdfb_core.engines.generation_plan import clear_generation_plan_log

  b1e.clear_free_text_pool_cache()
  clear_source_value_cache()
  clear_generation_plan_log()
  yield
  b1e.clear_free_text_pool_cache()
  clear_source_value_cache()
  clear_generation_plan_log()


def _schema(*columns: str) -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "p.d.notes"
      },
      "schema": [{
          "name": "id",
          "type": "INT64",
          "mode": "REQUIRED"
      }] + [{
          "name": c,
          "type": "STRING",
          "mode": "REQUIRED"
      } for c in columns],
      "primary_keys": ["id"],
  })


def _rows(*columns: str, n: int = 120) -> list[dict]:
  return [{
      "id": i,
      **{
          c: f"{c} reported issue number {i} with long details" for c in columns
      },
  } for i in range(n)]


def _ctx(config: GeneratorConfig = DEFAULT_CONFIG,
         columns: tuple[str, ...] = ("notes",),
         **kw) -> GenerationContext:
  fields = dict(
      table_schema=_schema(*columns),
      reference_rows=_rows(*columns),
      reference_digest=_DIGEST,
      model_uri=_URI,
      pipeline_run_id="promoted-pools",
      num_rows=100,
      # Ladder-mechanics tests: expansion off keeps every column on the
      # pool path (expandable columns skip the ladder).
      freetext_expansion="off",
      generator_config=config,
  )
  fields.update(kw)
  return GenerationContext(**fields)


class _ArrayClient:
  """Fresh in-format values per requested choice; records every call."""

  def __init__(self) -> None:
    self.calls: list[dict] = []
    self._issued = 0

  def generate_json(self, prompt, json_schema, *, n=1, **kw):
    self.calls.append({"prompt": prompt, "n": n, **kw})
    out = []
    for _ in range(max(1, n)):
      base = self._issued
      self._issued += 32
      out.append(
          {"values": [f"novel prose value {base + i} here" for i in range(32)]})
    return out


class _NoClient:

  def generate_json(self, *args, **kw):
    raise AssertionError("the ladder must not run on a store hit")


class _KeyedPoolStore(InMemoryFreeTextPoolStore):
  """An in-memory pool store that records the model_uri of every call."""

  def __init__(self, pools=()) -> None:
    super().__init__(pools)
    self.keys: list[tuple[str, str]] = []

  def fetch(self, reference_digest, model_uri):
    self.keys.append(("fetch", model_uri))
    return super().fetch(reference_digest, model_uri)

  def exists(self, reference_digest, model_uri):
    self.keys.append(("exists", model_uri))
    return super().exists(reference_digest, model_uri)

  def delete(self, reference_digest, model_uri):
    self.keys.append(("delete", model_uri))

  def write_rows(self, rows):
    self.keys.extend(("write", r["model_uri"]) for r in rows)
    super().write_rows(rows)


def _pool(model_uri: str, column: str = "notes", values=("nov-1",)):
  return FreeTextPool(
      reference_digest=_DIGEST,
      model_uri=model_uri,
      column=column,
      target=len(values),
      values=tuple(values),
      stagnated=False,
      attempts=1,
  )


def _engine() -> b1e.B1RagEngine:
  return b1e.B1RagEngine(embedder=HashingEmbedder(dim=32))


# --- (a) every pool-store call carries the same key --------------------------


class _RecordingValueStore:
  """Stands in for BigQuerySourceValueStore; every live value is 'real-'."""

  def __init__(self, table_fqn, *, cap) -> None:
    self.table_fqn = table_fqn
    self.cap = cap

  def count_overlap(self, column, values):
    return sum(1 for v in values if v.startswith("real-"))


def _launch_args(config: GeneratorConfig) -> argparse.Namespace:
  resolution = (None if config is DEFAULT_CONFIG else types.SimpleNamespace(
      config=config))
  return argparse.Namespace(
      build_pool_layer="true",
      freetext_pools_table="p.d.freetext_pools",
      reference_table="p.d.source",
      model_uri=_URI,
      config_resolution=resolution,
  )


@pytest.mark.parametrize("config,key", _KEYS)
def test_the_driver_keys_exists_fetch_and_delete_alike(monkeypatch, config,
                                                       key):
  from sdfb_beam.cli import run_pipeline as rp
  from sdfb_beam.io.digest import compute_reference_digest

  rows = _rows("notes")
  digest = compute_reference_digest(rows)
  # A warm, TAINTED store under the expected key: exists -> taint fetch
  # -> delete, the three driver calls.
  store = _KeyedPoolStore([
      FreeTextPool(
          reference_digest=digest,
          model_uri=key,
          column="notes",
          target=2,
          values=("real-1", "nov-2"),
      )
  ])
  monkeypatch.setattr(rp, "BigQueryFreeTextPoolStore", lambda fqn: store)
  monkeypatch.setattr(rp, "BigQuerySourceValueStore", _RecordingValueStore)

  pool_store, _ = rp.resolve_pool_layer(_launch_args(config), rows)

  assert pool_store is store  # tainted -> the branch rebuilds
  assert [op for op, _ in store.keys] == ["exists", "fetch", "delete"]
  assert {uri for _, uri in store.keys} == {key}


@pytest.mark.parametrize("config,key", _KEYS)
def test_the_worker_reads_its_pools_under_the_key(config, key):
  store = _KeyedPoolStore([_pool(key)])
  engine = _engine()
  engine.setup(_NoClient(), _ctx(config, pool_store=store))
  assert engine._pool_sources["notes"] == "store"
  assert store.keys == [("fetch", key)]
  engine.teardown()


def test_a_pool_stored_under_another_config_is_not_served():
  store = _KeyedPoolStore([_pool(_URI)])  # written at the defaults
  client = _ArrayClient()
  engine = _engine()
  engine.setup(client, _ctx(_LADDER, pool_store=store))
  assert engine._pool_sources["notes"] == "llm_ladder"
  assert client.calls
  engine.teardown()


@pytest.mark.parametrize("config,key", _KEYS)
def test_the_pool_branch_writes_under_the_key(config, key):
  from sdfb_beam.dofns.pools import BuildFreeTextPoolsDoFn

  store = _KeyedPoolStore()
  dofn = BuildFreeTextPoolsDoFn(
      "b1_rag", _ArrayClient(), _ctx(config), store=store)
  dofn.setup()
  rows = list(dofn.process(None))
  assert rows and {r["model_uri"] for r in rows} == {key}
  assert store.keys and {uri for _, uri in store.keys} == {key}
  dofn.teardown()

  # What the branch wrote is exactly what a worker under the same
  # config reads back.
  reader = _engine()
  reader.setup(_NoClient(), _ctx(config, pool_store=store))
  assert reader._pool_sources["notes"] == "store"
  reader.teardown()


# --- (b) the source-value store's cap is the config's ------------------------

_CAPPED = _cfg("free_text", source_domain_cap=7)


@pytest.mark.parametrize(
    "config", [DEFAULT_CONFIG, _CAPPED], ids=["defaults", "changed-cap"])
def test_the_driver_builds_the_value_store_with_the_config_cap(
    monkeypatch, config):
  from sdfb_beam.cli import run_pipeline as rp

  monkeypatch.setattr(rp, "BigQueryFreeTextPoolStore",
                      lambda fqn: _KeyedPoolStore())
  monkeypatch.setattr(rp, "BigQuerySourceValueStore", _RecordingValueStore)

  _, value_store = rp.resolve_pool_layer(_launch_args(config), _rows("notes"))

  assert isinstance(value_store, _RecordingValueStore)
  assert value_store.table_fqn == "p.d.source"
  assert value_store.cap == config.free_text.source_domain_cap


@pytest.mark.parametrize(
    "config", [DEFAULT_CONFIG, _CAPPED], ids=["defaults", "changed-cap"])
def test_the_worker_builds_the_value_store_with_the_config_cap(config):
  from sdfb_beam.dofns.generate import GenerateRecordsDoFn
  from sdfb_beam.io.source_values import BigQuerySourceValueStore
  from sdfb_tests.fakes import FakeModelClient

  dofn = GenerateRecordsDoFn(
      engine_name="minimal",
      model_client=FakeModelClient(reference_pool=[{}]),
      ctx=_ctx(config, source_values_table="p.d.src"),
  )
  dofn.setup()
  store = dofn.ctx.source_value_store
  assert isinstance(store, BigQuerySourceValueStore)
  assert store.cap == config.free_text.source_domain_cap


# --- (c) the in-process pool cache is salted too -----------------------------


def test_two_configs_in_one_process_never_share_a_cached_pool():
  first = _engine()
  first.setup(_ArrayClient(), _ctx())
  assert first._pool_sources["notes"] == "llm_ladder"

  client = _ArrayClient()
  other = _engine()
  other.setup(client, _ctx(_LADDER))
  assert other._pool_sources["notes"] == "llm_ladder"
  assert client.calls

  again = _engine()
  again.setup(_NoClient(), _ctx())
  assert again._pool_sources["notes"] == "process_cache"
  for engine in (first, other, again):
    engine.teardown()


# --- the values reach the ladder ---------------------------------------------


def test_the_engine_hands_its_ladder_to_the_yield_loop(monkeypatch):
  cfg = _cfg("free_text", pool_parallel_choices=2, pool_values_per_call=16)
  seen = []
  real = b1e._pool_llm_yield

  def spy(*args, **kw):
    seen.append(kw)
    return real(*args, **kw)

  monkeypatch.setattr(b1e, "_pool_llm_yield", spy)
  client = _ArrayClient()
  engine = _engine()
  engine.setup(client, _ctx(cfg))
  assert seen and all(kw["ladder"] is cfg.free_text for kw in seen)
  assert all(kw["n_choices"] == 2 for kw in seen)
  assert client.calls and all(call["n"] == 2 for call in client.calls)
  # The per-call batch the prompt asks for is the config's.
  assert "generate 16 NEW" in client.calls[0]["prompt"]
  engine.teardown()


def test_the_pool_target_is_capped_by_the_config():
  from sdfb_core.engines.b1_rag.profile import profile_columns

  engine = _engine()
  ctx = _ctx(num_rows=1_000_000)
  prof = profile_columns(ctx.table_schema, ctx.reference_rows)["notes"]
  assert engine._pool_target(prof, ctx, source_cardinality=4022) == 512
  capped = ctx.model_copy(
      update={"generator_config": _cfg("free_text", free_text_pool_max=50)})
  assert engine._pool_target(prof, capped, source_cardinality=4022) == 50


def test_the_exemplar_count_is_the_config_top_k(monkeypatch):
  ks = []
  real = b1e.select_seed_examples

  def spy(vectors, texts, k, **kw):
    ks.append(k)
    return real(vectors, texts, k, **kw)

  monkeypatch.setattr(b1e, "select_seed_examples", spy)
  engine = _engine()
  engine.setup(_ArrayClient(), _ctx(_cfg("rag", rag_top_k=3)))
  assert ks and set(ks) == {3}
  engine.teardown()


def test_the_ladder_thread_pool_is_sized_by_the_config(monkeypatch):
  import concurrent.futures

  sizes = []
  real = concurrent.futures.ThreadPoolExecutor

  def spy(*args, **kw):
    sizes.append(kw.get("max_workers"))
    return real(*args, **kw)

  monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", spy)
  engine = _engine()
  engine.setup(
      _ArrayClient(),
      _ctx(
          _cfg("free_text", pool_build_max_workers=1),
          columns=("notes", "remarks")))
  assert sizes == [1]  # two ladders, one thread
  engine.teardown()


def test_the_collapse_threshold_is_one_call_of_the_config():
  sixteen = [f"v{i}" for i in range(16)]
  assert b1e._collapse_rounds(
      0, sixteen, [], ladder=DEFAULT_CONFIG.free_text) == 0
  assert b1e._collapse_rounds(0, sixteen, [], ladder=_LADDER.free_text) == 1


def test_the_stagnation_window_is_the_config():
  from sdfb_core.engines.b1_rag.profile import ColumnKind, ColumnProfile
  from sdfb_core.engines.base import escalating_sampling

  class _Empty:

    def __init__(self) -> None:
      self.calls = 0

    def generate_json(self, prompt, json_schema, **kw):
      self.calls += 1
      return [{"values": []}]

  prof = ColumnProfile(
      name="notes",
      bq_type="STRING",
      kind=ColumnKind.FREE_TEXT,
      nullable=False,
      null_fraction=0.0,
  )
  levels = len(escalating_sampling())
  for window in (1, levels + 2):
    client = _Empty()
    ladder = _cfg("free_text", pool_stagnation_window=window).free_text
    b1e._pool_llm_yield(
        client, "p", {}, prof, [], target=512, n_choices=4, ladder=ladder)
    assert client.calls == max(levels, window), window


# --- the launcher's P4 pool cap and the chunking cap -------------------------

_PROSE_PK = '{"llm_prompt_constraint": {"route": "llm", "format": "opaque key"}}'


def _pk_schema() -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "p.d.t"
      },
      "schema": [{
          "name": "ID",
          "type": "STRING",
          "mode": "REQUIRED",
          "description": _PROSE_PK,
      }],
  })


@pytest.mark.parametrize("with_relations", [True, False])
def test_preflight_caps_a_pool_routed_pk_at_the_config_pool_max(with_relations):
  from sdfb_beam.cli.preflight import preflight
  from sdfb_core.contracts.relationships import parse_relationship_model

  relations = (
      parse_relationship_model(
          "model: m\ntables:\n  t:\n    pk: [ID]\n",
          source="test.yaml").tables["t"] if with_relations else None)
  pk_cols = () if with_relations else ("ID",)
  rows = [{"ID": f"key-{i}"} for i in range(10)]

  def run(config):
    preflight(
        _pk_schema(),
        pk_cols, (),
        rows,
        relations=relations,
        num_rows=500,
        config=config)

  run(DEFAULT_CONFIG)  # 500 rows fit the default 512-value pool
  with pytest.raises(SystemExit, match="preflight P4"):
    run(_cfg("free_text", free_text_pool_max=100))


def test_the_population_branch_caps_values_at_the_config(monkeypatch):
  import apache_beam as beam
  from sdfb_beam import pipeline as pl
  from sdfb_tests.fakes import FakeModelClient

  caps = []
  real = pl.distinct_free_text_values

  def spy(rows, columns, **kw):
    caps.append(kw.get("cap"))
    return real(rows, columns, **kw)

  monkeypatch.setattr(pl, "distinct_free_text_values", spy)
  cfg = _cfg("rag", max_free_text_values_per_column=5)
  config = pl.PipelineConfig(
      table_schema=_schema("notes"),
      engine_name="b1_rag",
      model_client=FakeModelClient(reference_pool=[{}]),
      num_rows=1,
      generator_config=cfg,
  )
  pl._population_branch(
      beam.Pipeline(), config, _rows("notes"), "dig", label_prefix="")
  assert caps == [5]
