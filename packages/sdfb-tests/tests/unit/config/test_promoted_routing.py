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
"""Column routing thresholds and temporal age floors come from the config
(ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""

# Test module: white-box access and local imports are intentional.
# pylint: disable=import-outside-toplevel,protected-access
import datetime as dt
import inspect

from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.contracts import TableSchema
from sdfb_core.engines.b1_rag import profile as b1p
from sdfb_core.engines.b2_library import fidelity as b2f
from sdfb_tests.fixtures import load_ddl, load_reference

_PROSE = [
    "The quick brown fox jumps over the lazy dog number one",
    "A second entirely different sentence about gardening tools",
    "Notes from the third call regarding the delayed shipment",
    "Customer asked about a refund for the broken kettle lid",
    "Follow up scheduled after the quarterly review meeting",
]


def _schema(col: str, bq_type: str) -> TableSchema:
  return TableSchema.model_validate({
      "table_info": {
          "table_id": "p.d.t"
      },
      "schema": [{
          "name": col,
          "type": bq_type,
          "mode": "REQUIRED"
      }],
  })


def _prose_case():
  return _schema("NOTES", "STRING"), [{"NOTES": t} for t in _PROSE]


def _cfg(section, **kw) -> GeneratorConfig:
  return GeneratorConfig.model_validate({section: kw})


def test_module_constants_are_gone():
  for name in ("_FREE_TEXT_UNIQUE_RATIO", "_FREE_TEXT_MIN_MEAN_LEN",
               "_FREE_TEXT_MAX_CATEGORIES", "_MAX_TEMPORAL_AGE_YEARS"):
    assert not hasattr(b1p, name), name
  assert not hasattr(b2f, "_MAX_TEMPORAL_AGE_YEARS")


def test_default_profile_unchanged():
  schema, rows = load_ddl("customers"), load_reference("customers")
  assert b1p.profile_columns(schema, rows) == b1p.profile_columns(
      schema, rows, config=GeneratorConfig())


def test_routing_threshold_follows_the_config():
  schema, rows = _prose_case()
  default = b1p.profile_columns(schema, rows)["NOTES"]
  strict = b1p.profile_columns(
      schema, rows, config=_cfg("free_text",
                                free_text_min_mean_len=10_000))["NOTES"]
  assert default.kind == b1p.ColumnKind.FREE_TEXT
  assert strict.kind != b1p.ColumnKind.FREE_TEXT


def test_b2_age_floor_default_is_the_config_default():
  param = inspect.signature(b2f.profile_table).parameters["max_age_years"]
  assert param.default == DEFAULT_CONFIG.generation.temporal_max_age_years_b2


def test_b2_age_floor_argument_reaches_the_clamp(monkeypatch):
  seen = []
  real = b2f.age_floor_epoch
  monkeypatch.setattr(
      b2f, "age_floor_epoch", lambda t, f, years:
      (seen.append(years), real(t, f, years))[1])
  today = dt.date.today()
  rows = [{"D": today - dt.timedelta(days=30 * i)} for i in range(1, 60)]
  b2f.profile_table(_schema("D", "DATE"), rows, max_age_years=1)
  assert seen and set(seen) == {1}


def test_b1_age_floor_follows_the_config(monkeypatch):
  seen = []
  real = b1p.log_milestone
  monkeypatch.setattr(b1p, "log_milestone", lambda *a, **k:
                      (seen.append(k), real(*a, **k))[1])
  today = dt.date.today()
  rows = [{"D": today - dt.timedelta(days=30 * i)} for i in range(1, 60)]
  b1p.profile_columns(
      _schema("D", "DATE"),
      rows,
      config=_cfg("generation", temporal_max_age_years_b1=1))
  assert any(k.get("max_age_years") == 1 for k in seen)


def test_preflight_passes_the_config_to_the_profiler(monkeypatch):
  import sdfb_beam.cli.preflight as pf

  non_default = _cfg("free_text", free_text_min_mean_len=7)
  configs = []
  real = pf.profile_columns

  def spy(*args, **kw):
    configs.append(kw.get("config"))
    return real(*args, **kw)

  monkeypatch.setattr(pf, "profile_columns", spy)
  schema, rows = _prose_case()
  pf.preflight(schema, (), (), rows, num_rows=5, config=non_default)
  assert configs and all(c is non_default for c in configs)
