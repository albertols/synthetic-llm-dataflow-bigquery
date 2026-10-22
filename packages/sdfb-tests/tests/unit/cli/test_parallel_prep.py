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
"""Parallel launcher prep (ADR 0045 D8, Ruling 17): the reads run ahead
on prep_workers threads, the cross-table steps keep the plan's order, the
specs do not depend on the thread count, and prep_workers=1 is the exact
sequence of before.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import argparse
import json
import logging
import threading

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import SetupOptions
from sdfb_beam.cli import run_pipeline as rp
from sdfb_beam.cli.preflight import PreflightResult
from sdfb_core.contracts.relationships import RelationshipRegistry
from sdfb_tests.fixtures import load_ddl, load_reference

_REG = RelationshipRegistry.from_sources([
    ("m.yaml", "model: m\ntables:\n  T_A: {}\n  T_B: {}\n  T_C: {}\n")
])
_TABLES = ("T_A", "T_B", "T_C")


class _Reads:
  """The launcher's BigQuery-facing reads, faked, and the order things
    happened in (appended from the prep threads too)."""

  def __init__(self):
    self.events: list[tuple[str, str]] = []
    self.threads: dict[tuple[str, str], str] = {}
    self.fail_schema_of: str | None = None
    self.stop_preflight_of: str | None = None
    self._lock = threading.Lock()
    self._schema = load_ddl("customers")
    self._rows = load_reference("customers")

  def note(self, what: str, table: str) -> None:
    event = (what, table.rsplit(".", 1)[-1])
    with self._lock:
      self.events.append(event)
      self.threads[event] = threading.current_thread().name

  def schema_of(self, table: str):
    """`table`'s own schema: the fixture, under the table's own id."""
    name = table.rsplit(".", 1)[-1]
    info = self._schema.table_info.model_copy(
        update={"table_id": f"proj.src.{name}"})
    return self._schema.model_copy(update={"table_info": info})

  def rows_of(self, table: str) -> list[dict]:
    """`table`'s own sample: one row fewer per table, in plan order."""
    drop = _TABLES.index(table.rsplit(".", 1)[-1])
    return self._rows[:len(self._rows) - drop]

  def schemas(self, ddl_uri, reference_table, landing_table=""):
    del ddl_uri, reference_table
    self.note("schema", landing_table)
    if landing_table.endswith(self.fail_schema_of or "\0"):
      raise RuntimeError(f"schema read failed for {landing_table}")
    return self.schema_of(landing_table), None

  def reference(self, table, limit):
    del limit
    self.note("rows", table)
    return self.rows_of(table)

  def cross(self, args, *a, **kw):
    del a, kw
    self.note("cross", args.landing_table)
    if args.landing_table.endswith(self.stop_preflight_of or "\0"):
      raise SystemExit(f"[preflight P4] stop on {args.landing_table}")
    return None, {}


@pytest.fixture(name="reads")
def fixture_reads(monkeypatch):
  reads = _Reads()
  configure = rp.configure_pipeline_options

  def configure_without_main_session(options, *a, **k):
    configure(options, *a, **k)
    # pytest's __main__ is not picklable; the test needs no main session.
    options.view_as(SetupOptions).save_main_session = False

  monkeypatch.setattr(rp, "resolve_schemas", reads.schemas)
  monkeypatch.setattr(rp, "load_reference_rows", reads.reference)
  monkeypatch.setattr(rp, "_resolve_table_fanout", reads.cross)
  monkeypatch.setattr(rp, "report_model_adjustments", lambda *a, **k: ())
  monkeypatch.setattr(rp, "report_row_projection", lambda *a, **k: ())
  monkeypatch.setattr(rp, "configure_pipeline_options",
                      configure_without_main_session)
  return reads


def _run(monkeypatch, workers: int) -> list:
  """`_run_relational_job` over three isolated tables; returns the specs it
    would hand the pipeline."""
  specs: list = []

  def capture(p, given):
    specs.extend(given)
    _ = p | beam.Create([0])  # a pipeline needs one transform to run
    return {}

  monkeypatch.setattr(rp, "build_relational_pipeline", capture)
  args, beam_argv = rp.parse_args([
      "--reference_table=proj.src.T_A", "--landing_table=proj.land.T_A",
      "--dlq_table=proj.q.dlq", "--num_rows=20", "--run_id=r1",
      "--model_uri=gs://b/synthetic/models/m/v1/", "--client_type=fake",
      "--source_stats=sample", "--relationships_uri=",
      f"--prep_workers={workers}", "--runner=DirectRunner"
  ])
  plan = rp.plan_launch([f"proj.land.{t}" for t in _TABLES], "proj.src.T_A",
                        True, _REG, "r1")
  assert rp._run_relational_job(plan, args, beam_argv, registry=_REG) == 0  # pylint: disable=protected-access
  return specs


def _shape(specs) -> list:
  return [(s.config.landing_table, s.config.reference_table, s.config.num_rows,
           s.config.run_id, s.config.pk_columns, s.reference_rows,
           s.config.table_schema, s.config.source_distinct) for s in specs]


def _prep_threads_alive() -> list[str]:
  return [
      t.name for t in threading.enumerate() if t.name.startswith("sdfb-prep")
  ]


def test_one_prep_worker_reads_in_todays_order(reads, monkeypatch):
  _run(monkeypatch, 1)
  assert reads.events == [
      (what, t) for t in _TABLES for what in ("schema", "rows", "cross")
  ]
  assert set(reads.threads.values()) == {"MainThread"}


def test_prep_workers_read_ahead_and_keep_the_plans_order(reads, monkeypatch):
  _run(monkeypatch, 4)
  assert [e for e in reads.events if e[0] == "cross"
         ] == [("cross", t) for t in _TABLES]
  for table in _TABLES:
    assert (reads.events.index(("rows", table)) < reads.events.index(
        ("cross", table)))
    # the reads ran on prep threads, the cross-table step on the launcher's
    assert reads.threads[("schema", table)].startswith("sdfb-prep")
    assert reads.threads[("rows", table)].startswith("sdfb-prep")
    assert reads.threads[("cross", table)] == "MainThread"
  assert not _prep_threads_alive()


@pytest.mark.usefixtures("reads")
def test_the_specs_do_not_depend_on_the_thread_count(monkeypatch):
  one = _shape(_run(monkeypatch, 1))
  four = _shape(_run(monkeypatch, 4))
  assert one == four


@pytest.mark.parametrize("workers", [1, 4])
def test_each_spec_is_built_from_its_own_tables_reads(reads, monkeypatch,
                                                      workers):
  # the fake reads differ per table, so a spec handed another table's reads
  # cannot compare equal
  assert len({len(reads.rows_of(t)) for t in _TABLES}) == len(_TABLES)
  assert len({reads.schema_of(t).fqn for t in _TABLES}) == len(_TABLES)
  specs = _run(monkeypatch, workers)
  assert [s.config.landing_table for s in specs
         ] == [f"proj.land.{t}" for t in _TABLES]
  for spec, table in zip(specs, _TABLES, strict=True):
    assert spec.reference_rows == reads.rows_of(table)
    assert spec.config.table_schema == reads.schema_of(table)


@pytest.mark.parametrize("workers", [1, 4])
def test_a_failed_read_surfaces_at_its_tables_turn(reads, monkeypatch, workers):
  reads.fail_schema_of = "T_B"
  with pytest.raises(
      RuntimeError, match=r"schema read failed for proj\.land\.T_B"):
    _run(monkeypatch, workers)
  assert [e for e in reads.events if e[0] == "cross"] == [("cross", "T_A")]
  # the reads still in flight were waited for: no prep thread outlives it
  assert not _prep_threads_alive()


def test_a_preflight_stop_aborts_the_same_way_at_any_thread_count(
    reads, monkeypatch):
  reads.stop_preflight_of = "T_B"
  messages = []
  for workers in (1, 4):
    with pytest.raises(SystemExit) as stop:
      _run(monkeypatch, workers)
    messages.append(str(stop.value))
  assert messages[0] == messages[1]
  assert messages[0].startswith(
      "1 of 3 planned tables failed driver-side preflight")
  assert "proj.land.T_B: [preflight P4] stop on proj.land.T_B" in messages[0]
  # collect-then-fail: every table's preflight still ran, in plan order
  crosses = [e for e in reads.events if e[0] == "cross"]
  assert crosses == [("cross", t) for t in _TABLES] * 2


def test_source_stats_are_written_after_preflight_as_before(
    reads, monkeypatch, caplog):
  real_preflight = rp.preflight
  real_write = rp.write_source_stats

  def preflight(table_schema, *a, **kw):
    reads.note("preflight", table_schema.fqn)  # each table's own fqn
    return real_preflight(table_schema, *a, **kw)

  def write(args, *a, **kw):
    reads.note("write", args.landing_table)
    return real_write(args, *a, **kw)

  monkeypatch.setattr(rp, "preflight", preflight)
  monkeypatch.setattr(rp, "write_source_stats", write)
  with caplog.at_level(logging.INFO, logger="sdfb.milestone"):
    _run(monkeypatch, 4)
  assert "name=launch_prep_parallel " in caplog.text
  assert caplog.text.count("name=source_table_stats ") == 3
  # each table's stats are written after ITS preflight, on the launcher's
  # thread, once, in plan order — though they were computed on a prep thread
  for table in _TABLES:
    assert (reads.events.index(("preflight", table)) < reads.events.index(
        ("write", table)))
    assert reads.threads[("write", table)] == "MainThread"
  assert [e for e in reads.events if e[0] == "write"
         ] == [("write", t) for t in _TABLES]


def test_read_ahead_exact_stats_write_what_the_inline_path_writes(
    reads, monkeypatch, tmp_path):
  """`--source_stats=exact` through a prep thread (`prepared`) writes the
    JSON artifact and the store rows, and returns the exact distinct counts,
    exactly as the inline path does for the same inputs."""
  written: dict[str, list] = {}

  def exact(unused_table, unused_schema, stats, **kwargs):
    del kwargs
    merged = {name: dict(entry) for name, entry in stats.items()}
    for i, name in enumerate(sorted(n for n in merged if n != "__table__")):
      merged[name].update(stats_tier="exact", distinct=1000 + i)
    return merged

  class _Store:

    def __init__(self, table_fqn):
      self.key = table_fqn.rsplit(".", 1)[-1]

    def exists(self, *a, **kw):
      del a
      written.setdefault(f"{self.key}.exists", []).append(kw)
      return False

    def write_rows(self, rows):
      written.setdefault(self.key, []).extend({
          k: v for k, v in row.items() if k != "computed_at"
      } for row in rows)

  monkeypatch.setattr("sdfb_beam.io.exact_stats.compute_exact_stats", exact)
  monkeypatch.setattr(rp, "BigQuerySourceStatsStore", _Store)
  base, _ = rp.parse_args([
      "--reference_table=proj.src.T_B", "--landing_table=proj.land.T_B",
      "--dlq_table=proj.q.dlq", "--num_rows=20", "--run_id=r1",
      "--model_uri=gs://b/synthetic/models/m/v1/", "--client_type=fake",
      "--source_stats=exact", "--relationships_uri="
  ])
  pf = PreflightResult((), (), _REG.relations("proj.land.T_B"))
  out: dict[str, dict] = {}
  for path in ("inline", "prepared"):
    args = argparse.Namespace(**vars(base))
    args.source_stats_json = str(tmp_path / f"{path}.json")
    args.source_stats_table = f"proj.ds.{path}"
    if path == "inline":
      out[path] = rp._emit_source_stats(  # pylint: disable=protected-access
          args, reads.schema_of("T_B"), reads.rows_of("T_B"), pf)
      continue
    read_ahead = rp.PrepReadAhead([args], _REG, 2)
    try:
      prepared = read_ahead.prep(0)
    finally:
      read_ahead.close()
    out[path] = rp._emit_source_stats(  # pylint: disable=protected-access
        args,
        prepared.schemas[0],
        prepared.reference_rows,
        pf,
        prepared=prepared)
  assert out["inline"] and out["prepared"] == out["inline"]
  assert (json.loads((tmp_path / "prepared.json").read_text()) == json.loads(
      (tmp_path / "inline.json").read_text()))
  assert written["prepared"] and written["prepared"] == written["inline"]
  assert written["prepared.exists"] == written["inline.exists"]
  assert written["inline.exists"][0]["stats_tier"] == "exact"
