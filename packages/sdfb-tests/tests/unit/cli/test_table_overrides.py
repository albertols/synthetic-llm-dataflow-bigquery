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
"""A model's `source_table` and `num_rows` in an explicit launch
(ADR 0045, Ruling 6): the plan, each table's namespace, the fan-out's parent
source, the row projection and the launch log.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import pytest
from sdfb_beam.cli import run_pipeline as rp
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_tests.launcher_logging import restoring_root_handlers

_MODEL = """model: overrides
tables:
  T_P: {pk: [P1], source_table: other_ds.T_P_V2, num_rows: 40}
  T_C:
    fk:
      - {cols: [P1], ref: T_P, ref_cols: [P1]}
  T_X: {source_table: p2.ds.T_X_V2}
"""
_REG = RelationshipRegistry.from_sources([("m.yaml", _MODEL)])
_ARGV = [
    "--reference_table=p.src.T_C", "--dlq_table=p.q.dlq", "--num_rows=10",
    "--run_id=r1", "--model_uri=gs://b/synthetic/models/m/v1/"
]


@pytest.fixture(name="model_uri")
def fixture_model_uri(tmp_path):
  path = tmp_path / "overrides.yaml"
  path.write_text(_MODEL, encoding="utf-8")
  return str(path)


def _main(*extra: str) -> int:
  """`main()` reconfigures logging onto stderr (`basicConfig(force=True)`
    drops pytest's capture handler), so its log is read from `capsys`."""
  with restoring_root_handlers():
    return rp.main([*_ARGV, "--landing_table=p.land.T_C", *extra])


def test_overrides_reach_the_plan():
  plan = rp.plan_launch(["p.land.T_C"], "p.src.T_C", True, _REG, "r1")
  runs = {r.landing_table: r for r in plan.runs}
  assert list(runs) == ["p.land.T_P", "p.land.T_C"]
  assert runs["p.land.T_P"].source_table == "p.other_ds.T_P_V2"
  assert runs["p.land.T_P"].num_rows == 40
  assert runs["p.land.T_C"].source_table == "p.src.T_C"
  assert runs["p.land.T_C"].num_rows is None


def test_a_three_part_override_is_kept():
  plan = rp.plan_launch(["p.land.T_X"], "p.src.T_X", True, _REG, "r1")
  assert [r.source_table for r in plan.runs] == ["p2.ds.T_X_V2"]


def test_a_model_launch_suffixes_a_single_run_and_skips_the_advice():
  plan = rp.plan_launch(["p.land.T_X"],
                        "",
                        True,
                        _REG,
                        "r1-b00",
                        source_dataset="p.src",
                        model_launch=True)
  assert [r.run_id for r in plan.runs] == ["r1-b00-00-T_X"]
  # F10: the many-targets advice is emitted only with relationships on, so
  # only `generate_fk=True` can prove a model launch drops it.
  plan = rp.plan_launch(["p.land.T_X", "p.land.T_C"],
                        "",
                        True,
                        _REG,
                        "r1-b00",
                        source_dataset="p.src",
                        model_launch=True)
  assert not [w for w in plan.warnings if "targets WITH relationships" in w]


def test_each_table_gets_its_rows_and_source_one_job_per_table(
    model_uri, monkeypatch):
  seen = []
  monkeypatch.setattr(
      rp,
      "_run_one_table",
      lambda a, beam_argv, registry=None: (seen.append(
          (a.landing_table, a.reference_table, a.num_rows)) or 0))
  assert _main(f"--relationships_uri={model_uri}",
               "--multi_table_mode=sequential_jobs") == 0
  assert seen == [("p.land.T_P", "p.other_ds.T_P_V2", 40),
                  ("p.land.T_C", "p.src.T_C", 10)]


def test_each_table_gets_its_rows_and_source_in_one_job(model_uri, monkeypatch):
  seen = []

  def prepare(table_args, *args, **kwargs):
    del args, kwargs
    seen.append((table_args.landing_table, table_args.reference_table,
                 table_args.num_rows, table_args._source_dataset))  # pylint: disable=protected-access
    raise SystemExit("stop after the namespace")

  monkeypatch.setattr(rp, "_prepare_table_spec", prepare)
  with pytest.raises(SystemExit, match="2 of 2 planned tables"):
    _main(f"--relationships_uri={model_uri}", "--client_type=fake",
          "--runner=DirectRunner")
  assert seen == [("p.land.T_P", "p.other_ds.T_P_V2", 40, "p.src"),
                  ("p.land.T_C", "p.src.T_C", 10, "p.src")]


def test_every_override_is_announced_once(model_uri, monkeypatch, capsys):
  payloads = {}
  monkeypatch.setattr(
      rp, "log_milestone_pretty",
      lambda name, payload, **kw: payloads.setdefault(name, payload))
  monkeypatch.setattr(
      rp, "_run_relational_job", lambda plan, a, beam_argv, registry=None: 0)
  assert _main(f"--relationships_uri={model_uri}") == 0
  text = capsys.readouterr().err
  overrides = [ln for ln in text.splitlines() if "table_override" in ln]
  assert len(overrides) == 1
  assert "table=T_P" in overrides[0] and "num_rows=40" in overrides[0]
  assert payloads["launch_config"]["resolved"]["overrides"] == {
      "T_P": {
          "source_table": "p.other_ds.T_P_V2",
          "num_rows": 40
      }
  }


def test_a_model_without_overrides_logs_nothing_new(tmp_path, monkeypatch,
                                                    capsys):
  path = tmp_path / "plain.yaml"
  path.write_text(
      "model: plain\ntables:\n  T_C: {pk: [C1]}\n", encoding="utf-8")
  payloads = {}
  monkeypatch.setattr(
      rp, "log_milestone_pretty",
      lambda name, payload, **kw: payloads.setdefault(name, payload))
  monkeypatch.setattr(
      rp, "_run_one_table", lambda a, beam_argv, registry=None: 0)
  assert _main(f"--relationships_uri={path}") == 0
  text = capsys.readouterr().err
  assert "name=launch_scenario " in text  # the log was captured
  assert "table_override" not in text
  assert "launch_option_unused" not in text
  assert "overrides" not in payloads["launch_config"]["resolved"]


def test_an_unused_dataset_is_warned_about_in_an_explicit_launch(
    model_uri, monkeypatch, capsys):
  monkeypatch.setattr(
      rp, "_run_relational_job", lambda plan, a, beam_argv, registry=None: 0)
  assert _main(f"--relationships_uri={model_uri}",
               "--landing_dataset=p.land") == 0
  (line,) = [
      ln for ln in capsys.readouterr().err.splitlines()
      if "launch_option_unused" in ln
  ]
  assert "key=io.landing_dataset" in line


_CHILD_SCHEMA = TableSchema.model_validate({
    "table_info": {
        "table_id": "p.src.T_C"
    },
    "schema": [{
        "name": "P1",
        "type": "STRING",
        "mode": "REQUIRED"
    }],
})


@pytest.mark.parametrize(
    "source_dataset, parent_source",
    [
        (None, "p.src.T_P"),  # today's rule: the child's dataset + the name
        ("p.src", "p.other_ds.T_P_V2"),  # ADR 0045: the parent's own source
    ])
def test_the_driving_parent_is_measured_on_its_own_source(
    monkeypatch, source_dataset, parent_source):
  measured = []

  def measure(**kw):
    measured.append(kw["source_parent"])
    return {"histogram": {"1": 3}, "parents": 3, "children": 3}

  monkeypatch.setattr(rp, "measure_fanout", measure)
  monkeypatch.setattr(rp, "fanout_payload",
                      lambda m, cols, exact: {"histogram": m["histogram"]})
  monkeypatch.setattr(rp, "log_fanout_measured", lambda *a, **k: None)
  rp.resolve_fanout(
      _REG,
      "p.land.T_C",
      "p.src.T_C",
      in_set_names={"T_P", "T_C"},
      reference_rows=[],
      table_schema=_CHILD_SCHEMA,
      stats_store=None,
      bq_client=None,
      source_dataset=source_dataset)
  assert measured == [parent_source]


def test_without_an_override_the_parent_source_is_unchanged(monkeypatch):
  registry = RelationshipRegistry.from_sources([
      ("m.yaml", "model: m\ntables:\n  T_P: {pk: [P1]}\n  T_C:\n    fk:\n"
       "      - {cols: [P1], ref: T_P, ref_cols: [P1]}\n")
  ])
  sources = []
  monkeypatch.setattr(
      rp, "measure_fanout",
      lambda **kw: sources.append(kw["source_parent"]) or {
          "histogram": {
              "1": 1
          },
          "parents": 1,
          "children": 1
      })
  monkeypatch.setattr(rp, "fanout_payload",
                      lambda m, cols, exact: {"histogram": m["histogram"]})
  monkeypatch.setattr(rp, "log_fanout_measured", lambda *a, **k: None)
  for source_dataset in (None, "p.src"):
    rp.resolve_fanout(
        registry,
        "p.land.T_C",
        "p.src.T_C",
        in_set_names={"T_P", "T_C"},
        reference_rows=[],
        table_schema=_CHILD_SCHEMA,
        stats_store=None,
        bq_client=None,
        source_dataset=source_dataset)
  assert sources == ["p.src.T_P", "p.src.T_P"]
