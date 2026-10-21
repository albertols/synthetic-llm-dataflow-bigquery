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
"""FK sample sizing, conditional request bound and repeat tolerance come from
the config (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""

# Test module: white-box access and local imports are intentional.
# pylint: disable=import-outside-toplevel,protected-access
import ast
import types
from pathlib import Path

import pytest
from sdfb_beam import pipeline as beam_pipeline
from sdfb_beam.cli import preflight as pf
from sdfb_beam.cli import run_pipeline as rp
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.contracts import TableSchema
from sdfb_core.contracts import model_adjustment as ma
from sdfb_core.contracts.relationships import parse_relationship_model
from sdfb_core.engines import pk_capacity as pk
from sdfb_core.validation import Thresholds

_SRC = Path(__file__).parents[5] / "packages/sdfb-beam/src/sdfb_beam"


def _cfg(section, **kw) -> GeneratorConfig:
  return GeneratorConfig.model_validate({section: kw})


def test_module_constants_are_gone():
  for name in ("FK_KEY_SAMPLE_FLOOR", "FK_KEY_SAMPLE_CEILING",
               "FK_KEY_SAMPLE_MARGIN"):
    assert not hasattr(pk, name), name
  assert not hasattr(ma, "REPEAT_SHARE_TOLERANCE")
  assert not hasattr(rp, "_MAX_CONDITIONAL_VALUES_PER_REQUEST")
  assert not hasattr(beam_pipeline, "_FK_SIDE_SAMPLE_CAP")


def test_sample_cap_defaults_match_master():
  # 1k rows: 10 x 1_000 / 12 < the 100k floor; 10M rows hit the 1M ceiling.
  assert pk.fk_key_sample_cap(1_000, 12) == 100_000
  assert pk.fk_key_sample_cap(1_000_000, 12) == 833_334
  assert pk.fk_key_sample_cap(10_000_000, 12) == 1_000_000
  assert pk.fk_key_sample_cap(10_000_000, None) == 100_000


def test_sample_cap_follows_the_config():
  relational = _cfg(
      "relational",
      fk_key_sample_floor=5,
      fk_key_sample_ceiling=50,
      fk_key_sample_margin=2).relational
  assert pk.fk_key_sample_cap(10, 1, relational=relational) == 20
  assert pk.fk_key_sample_cap(10, None, relational=relational) == 5
  assert pk.fk_key_sample_cap(1_000, 1, relational=relational) == 50


def test_repeat_tolerance_follows_the_config():
  assert ma.repeat_share_verdict(0.10, 0.12)[1] is True
  assert ma.repeat_share_verdict(0.10, 0.12, tolerance=0.01)[1] is False


def test_banner_states_the_tolerance_it_is_given():
  adjustment = ma.ModelAdjustment(
      table="t",
      change="pk_dropped",
      declared="d",
      measured="m",
      consequence="c",
      declared_pk=("A",),
      source_repeat_share=0.5,
  )
  assert "within 5%" in ma.adjustment_banner([adjustment])
  assert "within 12%" in ma.adjustment_banner([adjustment], tolerance=0.12)


# --- wiring: the config reaches every consumer -------------------------


def _child_inputs():
  relations = parse_relationship_model(
      "model: m\ntables:\n"
      "  parent:\n    pk: [PID]\n"
      "  child:\n    pk: [D1, C2, D18]\n    fk:\n"
      "      - cols: [D1]\n        ref: parent\n        ref_cols: [PID]\n",
      source="test.yaml").tables["child"]
  schema = TableSchema.model_validate({
      "table_info": {
          "table_id": "p.d.child"
      },
      "schema": [{
          "name": n,
          "type": t,
          "mode": "REQUIRED"
      } for n, t in (("D1", "STRING"), ("C2", "STRING"), ("D18", "STRING"),
                     ("AMT", "INT64"))],
  })
  rows = [{
      "D1": f"C2E3{i:020X}",
      "C2": f"C{i % 4}",
      "D18": f"K{i % 3}",
      "AMT": i * 7,
  } for i in range(480)]
  return relations, schema, rows


def test_preflight_sizes_the_fk_sample_from_the_config():
  relations, schema, rows = _child_inputs()
  config = _cfg("relational", fk_key_sample_floor=7, fk_key_sample_margin=2)
  result = pf.preflight(
      schema,
      (),
      (),
      rows,
      relations=relations,
      num_rows=1_000,
      fk_parent_rows={"parent": 10_000_000},
      config=config,
  )
  # ceil(2 x 1000 / 12) = 167, not the default 100_000 floor.
  assert result.fk_key_sample_caps == {("D1",): 167}


def test_preflight_stop_reads_ceiling_and_max_rows_from_the_config(monkeypatch):
  relations, schema, rows = _child_inputs()
  config = _cfg(
      "relational",
      fk_key_sample_floor=7,
      fk_key_sample_ceiling=170,
      fk_key_sample_margin=2)
  seen = []
  real = pf.max_rows_under_share

  def spy(*args, **kwargs):
    seen.append(kwargs.get("relational"))
    return real(*args, **kwargs)

  monkeypatch.setattr(pf, "max_rows_under_share", spy)
  # cap = min(170, ceil(2 x 2000 / 12)) = 170 -> 2040 tuples for 2000 rows:
  # ~37% expected duplicates, over the 20% gate -> the random-draw stop.
  with pytest.raises(SystemExit, match="170-tuple side-input ceiling"):
    pf.preflight(
        schema,
        (),
        (),
        rows,
        relations=relations,
        num_rows=2_000,
        fk_parent_rows={"parent": 10_000_000},
        blocker_failure_ratio=0.2,
        config=config,
    )
  assert seen == [config.relational]


def test_independent_pool_caps_read_the_config():
  supply = types.SimpleNamespace(
      independent=[types.SimpleNamespace(cols=("K",), ref="parent")])
  config = _cfg("relational", fk_key_sample_floor=5, fk_key_sample_margin=2)
  assert pf._independent_pool_caps(
      supply, 100, {}, config=config) == {
          ("K",): 200
      }
  assert pf._independent_pool_caps(
      supply, 100, {}, config=DEFAULT_CONFIG) == {
          ("K",): 100_000
      }


def test_run_summary_uses_the_given_tolerance():

  def row(tolerance):
    return beam_pipeline._build_validation_run_row(
        None,
        valid_count=10_000,
        dlq_by_rule={"pk.duplicate": 2_908},
        thresholds=Thresholds(env="dev", blocker_failure_ratio=0.5),
        run_id="r",
        reference_digest="d",
        num_rows=10_000,
        reference_table="p.src.t",
        landing_table="p.land.t",
        engine="b1_rag",
        model_uri="gs://b/m/v/",
        excluded_blocker_rules=("pk.duplicate",),
        source_repeat_share=0.2808,  # landing 0.2908 -> delta 0.01
        repeat_share_tolerance=tolerance,
    )

  assert row(0.05)["repeat_share_within_tolerance"] is True
  assert row(0.005)["repeat_share_within_tolerance"] is False


def _calls(path: Path, name: str) -> list[ast.Call]:
  tree = ast.parse(path.read_text(encoding="utf-8"))
  return [
      n for n in ast.walk(tree)
      if isinstance(n, ast.Call) and (getattr(n.func, "id", None) == name or
                                      getattr(n.func, "attr", None) == name)
  ]


def _kw(call: ast.Call, key: str) -> str:
  (value,) = [k.value for k in call.keywords if k.arg == key]
  return ast.unparse(value)


def test_the_launcher_threads_the_config_into_every_consumer():
  """The call sites a unit test cannot drive (they read BigQuery)."""
  run_pipeline = _SRC / "cli/run_pipeline.py"
  (edges,) = _calls(run_pipeline, "in_set_parent_edges")
  assert _kw(edges, "config") == "config_of(args)"
  (pools,) = _calls(run_pipeline, "load_fk_key_pools")
  assert _kw(pools, "limit") == "config_of(args).relational.fk_key_sample_floor"
  reports = _calls(run_pipeline, "report_model_adjustments")
  assert len(reports) == 2
  assert {_kw(c, "config") for c in reports} == {"config_of(args)"}
  (row,) = _calls(_SRC / "pipeline.py", "_BuildValidationRunDoFn")
  (kwargs,) = [k.value for k in row.keywords if k.arg == "row_kwargs"]
  assert isinstance(kwargs, ast.Dict)
  tolerance = {
      ast.unparse(k): ast.unparse(v)
      for k, v in zip(kwargs.keys, kwargs.values, strict=True)
      if k is not None
  }["'repeat_share_tolerance'"]
  assert tolerance == (
      "config.generator_config.guardrails.repeat_share_tolerance")
