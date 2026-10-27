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
"""A source smaller than `sampling.reference_rows_limit` is sampled whole.

37 source rows against the default limit of 10000: the reference read
returns all 37, and a launch on them (preflight P1-P5 with the PK
capacity check, the profiler and source stats, the clamped row-document
limit) completes without tripping the BLOCKER gate, whether the table is named (`explicit`) or
found by the model (`all`).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import apache_beam as beam
import pytest
from apache_beam.options.pipeline_options import PipelineOptions, SetupOptions
from sdfb_beam.cli import run_pipeline as rp
from sdfb_beam.io import bq_sources
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import build_pipeline
from sdfb_core.config.model import DEFAULT_CONFIG
from sdfb_core.engines.generation_plan import clear_generation_plan_log
from sdfb_core.record import SCHEMA_PATH
from sdfb_tests.fixtures import load_ddl, load_reference
from sdfb_tests.launcher_logging import restoring_root_handlers

_ROOT = Path(__file__).resolve().parents[5]
_N_SOURCE = 37
_LIMIT = DEFAULT_CONFIG.sampling.reference_rows_limit
_ALL_COLUMNS = frozenset(
    f["name"] for f in json.loads((_ROOT / SCHEMA_PATH).read_text()))
_MODEL = "model: small_source\ntables:\n  customers: {pk: [customer_id]}\n"


def _source() -> list[dict]:
  """37 customers: the fixture's ten, cycled, under unique keys."""
  base = load_reference("customers")
  return [{
      **base[i % len(base)], "customer_id": i + 1,
      "email": f"user{i}@example.com"
  } for i in range(_N_SOURCE)]


class _Client:
  """A BigQuery client over `rows`: honours the trailing `LIMIT n`."""

  def __init__(self, rows):
    self.rows = rows
    self.queries: list[str] = []

  def query(self, sql):
    self.queries.append(sql)
    limit = int(sql.rsplit("LIMIT ", 1)[1])
    rows = [SimpleNamespace(items=dict(r).items) for r in self.rows[:limit]]
    return SimpleNamespace(result=lambda: iter(rows))


def test_a_source_below_the_limit_is_sampled_whole():
  client = _Client(_source())
  out = bq_sources.load_reference_rows(
      table="p.d.customers", limit=_LIMIT, client=client)
  assert _LIMIT == 10000
  assert out == _source()
  assert len(out) == _N_SOURCE
  assert client.queries[0].endswith("LIMIT 10000")


@pytest.fixture(name="reads")
def fixture_reads(monkeypatch):
  """The launcher's BigQuery-facing reads, answered from a 37-row source
  through the real `load_reference_rows`; the built specs are captured."""
  schema = load_ddl("customers")
  client = _Client(_source())
  specs: list = []
  configure = rp.configure_pipeline_options

  def configure_without_main_session(options, *a, **k):
    configure(options, *a, **k)
    # pytest's __main__ is not picklable; the test needs no main session.
    options.view_as(SetupOptions).save_main_session = False

  def capture(p, **kwargs):
    specs.append(SimpleNamespace(**kwargs))
    _ = p | beam.Create([0])  # a pipeline needs one transform to run
    return {"run_id": kwargs["config"].run_id, "reference_digest": ""}

  monkeypatch.setattr(rp, "resolve_schemas", lambda *a, **k: (schema, None))
  monkeypatch.setattr(
      rp, "load_reference_rows",
      lambda **k: bq_sources.load_reference_rows(client=client, **k))
  monkeypatch.setattr(rp, "read_table_columns", lambda fqn: _ALL_COLUMNS)
  monkeypatch.setattr(rp, "_resolve_table_fanout", lambda *a, **k: (None, {}))
  monkeypatch.setattr(rp, "report_model_adjustments", lambda *a, **k: ())
  monkeypatch.setattr(rp, "report_row_projection", lambda *a, **k: ())
  monkeypatch.setattr(rp, "build_pipeline", capture)
  monkeypatch.setattr(rp, "configure_pipeline_options",
                      configure_without_main_session)
  monkeypatch.delenv("FLEX_TEMPLATE_PYTHON_PY_FILE", raising=False)
  return SimpleNamespace(client=client, specs=specs)


_COMMON = [
    "--dlq_table=proj.q.dlq", "--run_id=r1",
    "--model_uri=gs://b/synthetic/models/m/v1/", "--client_type=fake",
    "--seed=7", "--runner=DirectRunner"
]
_EXPLICIT = [
    "--reference_table=proj.src.customers",
    "--landing_table=proj.land.customers", "--relationships_uri=",
    "--pk_cols=customer_id"
]


def _all(tmp_path) -> list[str]:
  model = tmp_path / "model.yaml"
  model.write_text(_MODEL, encoding="utf-8")
  return [
      "--tables=all", "--source_dataset=proj.src",
      "--landing_dataset=proj.land", f"--relationships_uri={model}"
  ]


def _launch(*argv: str, num_rows: int = 1000) -> int:
  with restoring_root_handlers():
    return rp.main([*_COMMON, f"--num_rows={num_rows}", *argv])


def _land(tmp: Path, spec) -> tuple[list[dict], dict]:
  """The spec on the DirectRunner: the landed rows and the validation row.
  `fail_on_blocker` is the launch's own, so a tripped gate raises."""
  clear_generation_plan_log()
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_pipeline(
        p,
        reference_rows=spec.reference_rows,
        config=spec.config,
        landing_sink=WriteToJsonLines(str(tmp / "landing"), num_shards=1),
        dlq_sink=WriteToJsonLines(str(tmp / "dlq"), num_shards=1),
        validation_runs_sink=WriteToJsonLines(str(tmp / "vr"), num_shards=1))

  def read(stem: str) -> list[dict]:
    return [
        json.loads(line)
        for f in sorted(tmp.glob(f"{stem}*"))
        for line in f.read_text().splitlines()
        if line
    ]

  (run,) = read("vr")
  return read("landing"), run


def test_a_launch_on_a_37_row_source_lands_rows(reads, monkeypatch, capsys,
                                                tmp_path):
  real_preflight = rp.preflight
  seen: list = []

  def spy(schema, pk, identity, rows, **kwargs):
    result = real_preflight(schema, pk, identity, rows, **kwargs)
    seen.append((pk, len(rows), kwargs["num_rows"], result))
    return result

  monkeypatch.setattr(rp, "preflight", spy)
  assert _launch(*_EXPLICIT) == 0
  (spec,) = reads.specs
  assert reads.client.queries[0].endswith(f"LIMIT {_LIMIT}")
  assert len(spec.reference_rows) == _N_SOURCE
  # Preflight (P1-P5, PK capacity included) ran on the 37 rows against the
  # 1000 requested and let the launch through unadjusted.
  assert len(seen) == 1
  pk, n_rows, num_rows, result = seen[0]
  assert (pk, n_rows, num_rows) == (("customer_id",), _N_SOURCE, 1000)
  assert not result.adjustments
  assert spec.config.pk_columns == ("customer_id",)
  # The profiler and source stats ran over the 37 rows (7 columns).
  stats = [
      line for line in capsys.readouterr().err.splitlines()
      if "name=source_table_stats " in line
  ]
  assert len(stats) == 1 and " columns=7 " in stats[0]
  # The launch's own row-document prefix is the whole 37-row sample.
  assert spec.config.rag_row_docs_limit == _N_SOURCE
  # No rag_chunks / free-text pool table is named, so those layers are the
  # no-op the build_* defaults promise: nothing is built.
  assert spec.freetext_pools_store is None and spec.rag_chunks_sink is None
  rows, run = _land(tmp_path, spec)
  # Every one of the 1000 requested rows is accounted for, none lost to a
  # crash, and the gate is not tripped. How many LAND is the fake model
  # client's: it draws the key from the 37-value sample, so most batches
  # repeat one and go to the DLQ as schema.batch (not a BLOCKER rule).
  assert run["status"] == "PASSED"
  assert run["valid_count"] == len(rows)
  assert run["valid_count"] + run["dlq_count"] == 1000


def test_a_small_source_with_a_small_launch_lands_rows(reads, tmp_path):
  assert _launch(*_EXPLICIT, num_rows=20) == 0
  (spec,) = reads.specs
  rows, run = _land(tmp_path, spec)
  assert rows and run["status"] == "PASSED"


def test_explicit_and_all_launches_agree_on_a_small_source(reads, tmp_path):
  assert _launch(*_EXPLICIT) == 0
  assert _launch(*_all(tmp_path)) == 0
  explicit, whole = reads.specs
  assert explicit.reference_rows == whole.reference_rows
  assert len(whole.reference_rows) == _N_SOURCE
  assert all(q.endswith(f"LIMIT {_LIMIT}") for q in reads.client.queries)
  first, run_a = _land(tmp_path / "a", explicit)
  second, run_b = _land(tmp_path / "b", whole)
  assert run_a["status"] == run_b["status"] == "PASSED"
  assert run_a["num_rows_requested"] == run_b["num_rows_requested"] == 1000
  assert (run_a["valid_count"], run_a["dlq_count"]) == (run_b["valid_count"],
                                                        run_b["dlq_count"])
  assert len(first) == len(second)
