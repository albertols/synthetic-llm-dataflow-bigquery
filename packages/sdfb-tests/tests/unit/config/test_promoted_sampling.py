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
"""Source-stats constants come from the config (ADR 0043)."""
from sdfb_beam.io.exact_stats import build_exact_stats_sql
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.stats import source_stats as ss
from sdfb_tests.fixtures import load_ddl, load_reference


def _profile(config=None):
  kwargs = {} if config is None else {"config": config}
  return ss.profile_source_table(
      load_ddl("customers"), load_reference("customers"), **kwargs)


def _with(**sampling) -> GeneratorConfig:
  return GeneratorConfig.model_validate({"sampling": sampling})


def test_module_constants_are_gone():
  for name in ("_TOP_VALUES_MAX_DISTINCT", "_TOP_VALUES_TOP_K",
               "_SHAPE_MIX_TOP_K", "_LEN_PCTS", "_NULL_PATTERN_MAX_COLS",
               "_NULL_PATTERN_TOP_K"):
    assert not hasattr(ss, name), name


def test_default_profile_is_unchanged_and_unsalted():
  assert ss.stats_profiler_version(GeneratorConfig()) == ss.PROFILER_VERSION
  assert _profile() == _profile(GeneratorConfig())


def test_top_k_follows_the_config():
  default = _profile()
  assert any(len(e.get("top_values", [])) > 1 for e in default.values())
  narrow = _profile(_with(top_values_top_k=1))
  for entry in narrow.values():
    assert len(entry.get("top_values", [])) <= 1


def test_null_pattern_cap_follows_the_config():
  assert ss.TABLE_PSEUDO_COLUMN in _profile()
  assert ss.TABLE_PSEUDO_COLUMN not in _profile(_with(null_pattern_max_cols=1))


def test_changed_value_salts_the_profiler_version():
  version = ss.stats_profiler_version(_with(top_values_top_k=3))
  assert version.startswith(ss.PROFILER_VERSION + "+cfg.")


def test_exact_stats_sql_reads_the_sampling_section():
  schema, tier1 = load_ddl("customers"), _profile()
  default_k = DEFAULT_CONFIG.sampling.top_values_top_k
  assert f", {default_k}))" in build_exact_stats_sql("p.d.t", schema, tier1)
  narrow = _with(top_values_top_k=2).sampling
  assert ", 2))" in build_exact_stats_sql(
      "p.d.t", schema, tier1, sampling=narrow)
