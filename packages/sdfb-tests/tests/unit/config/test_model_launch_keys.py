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
"""The whole-model launch keys (ADR 0045): eight settings, their defaults,
launch identity refused in a file, and master's argv untouched by them.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import pytest
from sdfb_beam.cli import run_pipeline as rp
from sdfb_core.config.defaults import load_defaults
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig
from sdfb_core.config.render import render_reference
from sdfb_core.config.resolve import ConfigError, parse_config_text, resolve
from sdfb_tests import config_parity as cp

_DEFAULTS = {
    "source_dataset": "",
    "landing_dataset": "",
    "tables": "explicit",
    "max_tables_per_job": 10,
    "max_parallel_jobs": 1,
    "prep_workers": 8,
    "batch_index": None,
    "batch_count": None,
}


def test_the_excluded_namespace_attrs_are_exactly_the_new_keys():
  assert set(_DEFAULTS) == cp.MODEL_LAUNCH_ATTRS


def test_the_defaults():
  launch, io = DEFAULT_CONFIG.launch, DEFAULT_CONFIG.io
  assert (launch.tables, launch.max_tables_per_job, launch.max_parallel_jobs,
          launch.prep_workers) == ("explicit", 10, 1, 8)
  assert launch.batch_index is None and launch.batch_count is None
  assert io.source_dataset == "" and io.landing_dataset == ""


@pytest.mark.parametrize("case", sorted(cp.ARGV_CASES))
def test_master_argv_leaves_every_new_key_at_its_default(case):
  """What the namespace golden no longer compares (EXCLUDED_ATTRS), pinned
    here: master's argv produces the defaults, whatever else it sets."""
  args, _ = rp.parse_args(list(cp.ARGV_CASES[case]))
  assert {k: getattr(args, k) for k in _DEFAULTS} == _DEFAULTS


def test_the_flags_reach_the_namespace():
  args, _ = rp.parse_args([
      *cp.ARGV_BASE, "--max_tables_per_job=3", "--max_parallel_jobs=2",
      "--prep_workers=1", "--source_dataset=p.src", "--landing_dataset=p.land"
  ])
  assert (args.max_tables_per_job, args.max_parallel_jobs,
          args.prep_workers) == (3, 2, 1)
  assert (args.source_dataset, args.landing_dataset) == ("p.src", "p.land")


def test_a_file_sets_the_settable_launch_keys():
  values = parse_config_text(
      "launch: {max_tables_per_job: 4, prep_workers: 2}\n"
      "io: {source_dataset: p.src}\n",
      source="f.yaml")
  config = resolve({}, values).config
  assert config.launch.max_tables_per_job == 4
  assert config.launch.prep_workers == 2
  assert config.io.source_dataset == "p.src"


@pytest.mark.parametrize("key", ["batch_index", "batch_count", "run_id"])
def test_launch_identity_is_refused_in_a_file(key):
  with pytest.raises(ConfigError, match=f"launch.{key} is launch identity"):
    parse_config_text(f"launch: {{{key}: 1}}\n", source="f.yaml")


@pytest.mark.parametrize(
    "key", ["max_tables_per_job", "max_parallel_jobs", "prep_workers"])
def test_a_count_below_one_is_refused(key):
  with pytest.raises(ConfigError, match=f"launch.{key}"):
    resolve({}, {"launch": {key: 0}})


def test_tables_is_explicit_or_all():
  config = GeneratorConfig.model_validate({"launch": {"tables": "all"}})
  assert config.launch.tables == "all"
  with pytest.raises(ConfigError):
    resolve({}, {"launch": {"tables": "some"}})


def test_master_prefixes_still_resolve_or_stop_loudly(capsys):
  """Ruling 25: a prefix only one flag matched still resolves; one that a
    new flag now shares is argparse's ambiguous-option error."""
  args, _ = rp.parse_args(
      [*cp.ARGV_BASE, "--landing_t=p.land.u", "--batch_s=40"])
  assert args.landing_table == "p.land.u" and args.batch_size == 40
  with pytest.raises(SystemExit) as exc:
    rp.parse_args([*cp.ARGV_BASE, "--landing=p.land.u"])
  assert exc.value.code == 2 and "ambiguous option" in capsys.readouterr().err


def test_the_reference_file_renders_the_launch_section():
  text = render_reference(load_defaults())
  assert "launch:\n  {}" not in text
  assert "\n  tables: explicit\n" in text
  assert "\n  max_tables_per_job: 10\n" in text
  assert "\n  # batch_index: null\n" in text
  assert "\n  # batch_count: null\n" in text
