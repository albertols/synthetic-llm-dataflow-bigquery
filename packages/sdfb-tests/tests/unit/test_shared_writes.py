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
"""One load job per shared table per relational job (ADR 0045 D9).

The 2026-11-03 whole-model launch lost four of its fourteen batch jobs to
BigQuery's `rateLimitExceeded` on `synthetic_rag.rag_chunks`: every table of
a batch applied its own `WriteRagChunks`, so a batch of ten fired ten load
jobs into one table within seconds (about five table updates per ten seconds
are allowed). Tables handed the same sink now write once, over a Flatten:
the RAG chunks, the DLQ and the validation_runs rows alike. Each table's
pool trigger still waits on its OWN chunks (ADR 0034 D8), and each table's
BLOCKER gate on the shared write's load jobs.

Design: docs/DESIGN.md §4 Relational generation; §6 Throughput
(ADR 0034, 0045).
"""

# Test module: pytest fixtures and white-box access are intentional.
# pylint: disable=arguments-renamed,protected-access

from __future__ import annotations

import argparse
import json
from pathlib import Path

import apache_beam as beam
import pytest
from apache_beam.io.gcp.bigquery import WriteResult
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.pipeline import PipelineVisitor
from sdfb_beam.cli import run_pipeline
from sdfb_beam.io.local_sinks import WriteToJsonLines
from sdfb_beam.pipeline import (
    PipelineConfig,
    SkippedTable,
    TableSpec,
    build_relational_pipeline,
)
from sdfb_core.contracts import TableSchema
from sdfb_core.pools.store import InMemoryFreeTextPoolStore
from sdfb_core.validation import Thresholds
from sdfb_tests.fakes import FakeModelClient  # also registers MinimalEngine as "minimal"

_TABLES = tuple(f"T{i:02d}" for i in range(10))
_SCHEMA = TableSchema.model_validate({
    "table_info": {
        "table_id": "p.src.t"
    },
    "schema": [{
        "name": "v",
        "type": "STRING",
        "mode": "REQUIRED"
    }],
})


class _Nodes(PipelineVisitor):
  """Every applied transform of a built graph, by full label."""

  def __init__(self):
    self.by_label: dict[str, object] = {}

  def visit_transform(self, node):
    self.by_label[node.full_label] = node

  def enter_composite_transform(self, node):
    self.by_label[node.full_label] = node


def _nodes(pipeline) -> dict[str, object]:
  visitor = _Nodes()
  pipeline.visit(visitor)
  return visitor.by_label


class _LoadsSink(beam.PTransform):
  """A sink shaped like WriteToBigQuery's FILE_LOADS: it returns a
  WriteResult whose load-job pairs the BLOCKER gate waits on."""

  def expand(self, pcoll):
    jobs = pcoll | "Jobs" >> beam.Map(lambda row: ("dest", "job"))
    return WriteResult(method="FILE_LOADS", destination_load_jobid_pairs=jobs)


def _config(table: str) -> PipelineConfig:
  return PipelineConfig(
      table_schema=_SCHEMA,
      engine_name="fake",
      model_client=None,
      num_rows=4,
      batch_size=4,
      run_id=f"r1-b00-{table}",
      landing_table=f"p.land.{table}",
      thresholds=Thresholds(env="dev", blocker_failure_ratio=0.05),
  )


def _specs(*, shared: bool) -> list[TableSpec]:
  sinks = {
      "rag": _LoadsSink(),
      "dlq": _LoadsSink(),
      "vr": _LoadsSink(),
  }

  def sink(kind: str):
    return sinks[kind] if shared else _LoadsSink()

  return [
      TableSpec(
          config=_config(table),
          reference_rows=[{
              "v": f"{table}-a"
          }],
          landing_sink=beam.Map(lambda x: x),
          dlq_sink=sink("dlq"),
          validation_runs_sink=sink("vr"),
          rag_chunks_sink=sink("rag"),
          freetext_pools_store=InMemoryFreeTextPoolStore(),
      ) for table in _TABLES
  ]


def _writes(labels, name: str) -> list[str]:
  """The labels whose last part is `name` (a write, not its inner steps)."""
  return sorted(label for label in labels if label.rsplit("/", 1)[-1] == name)


def test_a_ten_table_job_writes_each_shared_table_once():
  p = beam.Pipeline()
  build_relational_pipeline(p, _specs(shared=True))
  nodes = _nodes(p)
  assert _writes(nodes, "WriteRagChunks") == ["WriteRagChunks"]
  assert _writes(nodes, "WriteDLQ") == ["WriteDLQ"]
  assert _writes(nodes, "WriteValidationRun") == ["WriteValidationRun"]
  for flatten in ("RagChunks/Flatten", "DLQ/Flatten", "ValidationRuns/Flatten"):
    assert len(nodes[flatten].inputs) == len(_TABLES), flatten
  for table in _TABLES:
    # ADR 0034 D8: the pool trigger waits on THIS table's embedded chunks.
    await_population = nodes[f"{table}/AwaitRagPopulation"]
    (side,) = await_population.side_inputs
    assert side.pvalue.producer.full_label.startswith(
        f"{table}/RagEmbedChunks"), side.pvalue.producer.full_label
    # Each gate waits on the shared write's load jobs: a tripped gate fails
    # the job only once every table's row has committed.
    (gate_side,) = nodes[f"{table}/BlockerGate"].side_inputs
    assert gate_side.pvalue.producer.full_label == "WriteValidationRun/Jobs"


def test_tables_alone_on_their_sinks_keep_their_own_writes():
  p = beam.Pipeline()
  build_relational_pipeline(p, _specs(shared=False))
  nodes = _nodes(p)
  for name in ("WriteRagChunks", "WriteDLQ", "WriteValidationRun"):
    assert _writes(nodes, name) == sorted(f"{t}/{name}" for t in _TABLES)
  assert not [
      label for label in nodes if label.endswith("/Flatten") and
      label.split("/")[0] in ("RagChunks", "DLQ", "ValidationRuns")
  ]
  for table in _TABLES:
    (gate_side,) = nodes[f"{table}/BlockerGate"].side_inputs
    assert gate_side.pvalue.producer.full_label == (
        f"{table}/WriteValidationRun/Jobs")


def test_a_skipped_tables_row_joins_the_shared_write():
  specs = _specs(shared=True)[:2]
  skipped = SkippedTable(
      landing_table="p.land.T99",
      row={
          "run_id": "r1-b00-T99",
          "status": "FAILED_PREFLIGHT"
      },
      validation_runs_sink=specs[0].validation_runs_sink)
  p = beam.Pipeline()
  build_relational_pipeline(p, specs, skipped=[skipped])
  nodes = _nodes(p)
  assert _writes(nodes, "WriteValidationRun") == ["WriteValidationRun"]
  assert not _writes(nodes, "WriteSkippedValidationRun")
  assert len(nodes["ValidationRuns/Flatten"].inputs) == 3


def _read_jsonl(prefix: Path) -> list[dict]:
  rows: list[dict] = []
  for f in sorted(prefix.parent.glob(prefix.name + "*")):
    rows.extend(json.loads(line) for line in f.read_text().splitlines() if line)
  return rows


@pytest.mark.integration
def test_every_tables_row_lands_through_the_one_shared_sink(
    tmp_path, customers_schema, customers_reference):
  vr_sink = WriteToJsonLines(str(tmp_path / "vr"), num_shards=1)
  dlq_sink = WriteToJsonLines(str(tmp_path / "dlq"), num_shards=1)
  specs = [
      TableSpec(
          config=PipelineConfig(
              table_schema=customers_schema,
              engine_name="minimal",
              model_client=FakeModelClient(reference_pool=customers_reference),
              num_rows=10,
              batch_size=5,
              seed=7,
              run_id=f"r1-b00-0{i}-{t}",
              reference_table=f"p.src.{t}",
              landing_table=f"p.land.{t}",
              thresholds=Thresholds(env="dev", blocker_failure_ratio=0.20),
          ),
          reference_rows=customers_reference,
          landing_sink=WriteToJsonLines(str(tmp_path / f"land_{t}")),
          dlq_sink=dlq_sink,
          validation_runs_sink=vr_sink,
      ) for i, t in enumerate(("T_A", "T_B", "T_C"))
  ]
  with beam.Pipeline(options=PipelineOptions(["--runner=DirectRunner"])) as p:
    build_relational_pipeline(p, specs)
  rows = _read_jsonl(tmp_path / "vr")
  assert sorted((r["landing_table"], r["run_id"]) for r in rows) == [
      ("p.land.T_A", "r1-b00-00-T_A"),
      ("p.land.T_B", "r1-b00-01-T_B"),
      ("p.land.T_C", "r1-b00-02-T_C"),
  ]


def test_the_launcher_shares_one_sink_per_destination_table():
  """`_share_sinks`: tables naming the same table share its first sink; a
  table naming another keeps its own; no sink stays no sink."""

  def args(rag: str, dlq: str, vr: str) -> argparse.Namespace:
    return argparse.Namespace(
        rag_chunks_table=rag, dlq_table=dlq, validation_runs_table=vr)

  specs = _specs(shared=False)[:3]
  specs[2] = TableSpec(
      config=specs[2].config,
      reference_rows=specs[2].reference_rows,
      landing_sink=specs[2].landing_sink,
      dlq_sink=specs[2].dlq_sink,
      validation_runs_sink=specs[2].validation_runs_sink,
      rag_chunks_sink=None)  # its digest is in the store already
  skipped = SkippedTable(
      landing_table="p.land.T99", row={}, validation_runs_sink=_LoadsSink())
  by_landing = {
      "p.land.T00": args("p.rag.chunks", "p.q.dlq", "p.q.runs"),
      "p.land.T01": args("p.rag.chunks", "p.q.dlq_other", "p.q.runs"),
      "p.land.T02": args("p.rag.chunks", "p.q.dlq", "p.q.runs"),
      "p.land.T99": args("p.rag.chunks", "p.q.dlq", "p.q.runs"),
  }
  shared, (skipped_out,) = run_pipeline._share_sinks(specs, [skipped],
                                                     by_landing)
  first = shared[0]
  assert shared[1].rag_chunks_sink is first.rag_chunks_sink
  assert shared[2].rag_chunks_sink is None
  assert shared[1].dlq_sink is specs[1].dlq_sink  # another table
  assert shared[2].dlq_sink is first.dlq_sink
  for out in (*shared, skipped_out):
    assert out.validation_runs_sink is first.validation_runs_sink
