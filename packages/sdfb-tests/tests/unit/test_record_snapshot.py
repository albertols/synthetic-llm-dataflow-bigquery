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
"""The config snapshot: `values` replays through --config_uri to the same
config and the same digest; the record's launcher columns derive from it.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import json

import yaml
from sdfb_core.config.defaults import defaults_digest
from sdfb_core.config.model import SECTION_TYPES
from sdfb_core.config.resolve import parse_config_text, resolve
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_core.record import (LAUNCHER_COLUMNS, LaunchRecord, build_snapshot,
                              config_digest, config_values, launch_record,
                              relationships_block)

_TYPED = {
    "reference_table": "p.src.A_TABLE",
    "landing_table": "p.land.A_TABLE",
    "dlq_table": "p.q.dlq",
    "num_rows": 10,
    "run_id": "r1",
    "model_uri": "gs://b/synthetic/models/m/v1/",
    "similarity": 0.3,
}
_FILE = """
generation: {similarity: 0.7, seed: 42}
free_text: {pool_values_per_call: 16, prompt_constraints: off}
sampling: {source_stats: off, length_percentiles: [0.1, 0.5, 0.9]}
relational: {generate_fk_relationships: false}
"""
_MODEL = """
model: example_pair
tables:
  A_TABLE:
    pk: [COL_001]
  B_TABLE:
    pk: [COL_010]
    fk:
      - cols: [COL_001]
        ref: A_TABLE
        ref_cols: [COL_001]
  C_TABLE:
    enabled: false
"""


def _resolution():
  return resolve(
      _TYPED, parse_config_text(_FILE, source="f.yaml"), config_uri="f.yaml")


def _snapshot(**overrides):
  registry = RelationshipRegistry.from_sources([("example_pair.yaml", _MODEL)])
  kwargs = dict(
      derived={"temperature_ladder": [0.7, 1.0, 1.3]},
      thresholds={
          "uri": "config/thresholds.yml",
          "env": "dev"
      },
      relationships=relationships_block(registry, "config/relationships",
                                        ["p.land.A_TABLE"]),
      beam={"runner": "DirectRunner"},
      launcher_version="0.6.1-master-edcfdcd",
  )
  kwargs.update(overrides)
  return build_snapshot(_resolution(), **kwargs)


def test_values_hold_every_settable_key_and_no_launch_identity():
  values = config_values(_resolution().config)
  assert set(values) == set(SECTION_TYPES)
  # The four settable launch keys (ADR 0045, F18) are in the values and the
  # digest; the identity keys (run_id, batch_index, batch_count) are not.
  assert values["launch"] == {
      "tables": "explicit",
      "max_tables_per_job": 10,
      "max_parallel_jobs": 1,
      "prep_workers": 8
  }
  for section, cls in SECTION_TYPES.items():
    if section != "launch":
      assert set(values[section]) == set(cls.model_fields), section


def test_values_replay_to_the_same_config_and_digest():
  original = _resolution().config
  text = yaml.safe_dump(_snapshot()["values"], sort_keys=False)
  replayed = resolve({"run_id": "r1"}, parse_config_text(text, source="replay"))
  assert replayed.config == original
  assert config_digest(replayed.config) == config_digest(original)


def test_the_digest_ignores_launch_identity_and_follows_values():
  base = _resolution().config
  other_run = resolve({
      **_TYPED, "run_id": "r2"
  }, parse_config_text(_FILE, source="f.yaml")).config
  assert config_digest(other_run) == config_digest(base)
  changed = resolve({
      **_TYPED
  }, parse_config_text(_FILE.replace("0.7", "0.8"), source="f.yaml")).config
  assert config_digest(changed) != config_digest(base)
  assert len(config_digest(base)) == 12


def test_the_snapshot_carries_provenance_overrides_and_every_block():
  snap = _snapshot()
  assert list(snap) == [
      "config_version", "values", "provenance", "overridden", "derived",
      "thresholds", "relationships", "beam", "launcher", "batch"
  ]
  assert snap["config_version"] == 1
  assert snap["provenance"]["generation.similarity"] == "file"
  assert snap["provenance"]["generation.batch_size"] == "default"
  assert snap["overridden"] == [{
      "key": "generation.similarity",
      "typed": 0.3,
      "file": 0.7
  }]
  assert snap["launcher"] == {
      "version": "0.6.1-master-edcfdcd",
      "defaults_digest": defaults_digest()
  }
  assert snap["batch"] is None
  json.dumps(snap)  # JSON-serialisable as is


def test_a_batch_block_is_carried_as_given():
  batch = {"index": 0, "count": 2, "tables": ["A_TABLE"]}
  assert _snapshot(batch=batch)["batch"] == batch


def test_the_relationships_block_embeds_this_launchs_tables_as_declared():
  registry = RelationshipRegistry.from_sources([("example_pair.yaml", _MODEL)])
  block = relationships_block(registry, "gs://b/rel/",
                              ["p.land.A_TABLE", "p.land.B_TABLE"])
  assert block["uri"] == "gs://b/rel/"
  assert block["model"] == "example_pair"
  assert block["sha12"] == registry.sha12()
  assert set(block["tables"]) == {"A_TABLE", "B_TABLE"}
  assert block["tables"]["B_TABLE"]["fk"][0]["ref"] == "A_TABLE"
  assert block["tables"]["A_TABLE"]["pk"] == ["COL_001"]


def test_no_model_means_no_name_and_no_digest():
  block = relationships_block(RelationshipRegistry(), "", ["p.land.X"])
  assert block == {"uri": "", "model": None, "sha12": None, "tables": {}}


def test_launch_record_columns():
  snap = _snapshot()
  record = launch_record(
      snap,
      job_name="sdfb-r1",
      launch_run_id="r1",
      launched_at="2026-10-21T10:00:00+00:00",
      relationships_digest="abcdefabcdef",
      read_job_id=True)
  assert isinstance(record, LaunchRecord)
  assert tuple(record.columns) == LAUNCHER_COLUMNS
  c = record.columns
  assert c["job_name"] == "sdfb-r1" and c["launch_run_id"] == "r1"
  assert c["similarity"] == 0.7 and c["seed"] == "42"
  assert c["retrieval_method"] == "centroid"
  assert c["source_stats"] == "off"
  assert c["embedder_uri"] == ""
  assert c["relationships_uri"] == "config/relationships"
  assert c["config_digest"] == config_digest(_resolution().config)
  assert json.loads(c["config"]) == json.loads(json.dumps(snap))
  assert record.launcher_version == "0.6.1-master-edcfdcd"
  assert record.read_job_id is True


def test_a_derived_seed_and_a_b2_engine():
  typed = {**_TYPED, "engine": "b2_library"}
  resolution = resolve(typed, None)
  snap = build_snapshot(
      resolution,
      derived={},
      thresholds={},
      relationships={},
      beam={},
      launcher_version="v")
  c = launch_record(
      snap,
      job_name=None,
      launch_run_id="r1",
      launched_at="t",
      relationships_digest=None,
      read_job_id=False).columns
  assert c["seed"] == "derived"
  assert c["retrieval_method"] is None
