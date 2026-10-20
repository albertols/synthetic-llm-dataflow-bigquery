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
"""`--config_uri`: the file wins, missing files fail cleanly, blank means none.

The launcher's side of the generator config file (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import argparse
import logging

import pytest
from sdfb_beam.cli import run_pipeline as rp
from sdfb_tests.config_parity import ARGV_BASE


def _write(tmp_path, text):
  path = tmp_path / "launch.yaml"
  path.write_text(text)
  return str(path)


def test_file_sets_values_and_flags_fill_the_rest(tmp_path):
  uri = _write(
      tmp_path, "generation:\n  similarity: 0.7\n"
      "free_text:\n  pool_values_per_call: 64\n")
  args, _ = rp.parse_args(
      [*ARGV_BASE, "--similarity=0.3", f"--config_uri={uri}"])
  assert args.similarity == 0.7
  assert args.num_rows == 10
  assert rp.config_of(args).free_text.pool_values_per_call == 64
  assert args.config_resolution.provenance["generation.similarity"] == "file"


def test_file_can_supply_required_flags(tmp_path):
  uri = _write(
      tmp_path, """
io: {reference_table: p.src.t, landing_table: p.land.t, dlq_table: p.q.dlq}
generation: {num_rows: 5}
serving: {model_uri: gs://b/synthetic/models/m/v1/}
""")
  args, _ = rp.parse_args(["--run_id=r1", f"--config_uri={uri}"])
  assert args.reference_table == "p.src.t" and args.num_rows == 5


def test_override_warning_lists_the_conflict(tmp_path, caplog):
  uri = _write(tmp_path, "generation:\n  similarity: 0.7\n")
  args, _ = rp.parse_args(
      [*ARGV_BASE, "--similarity=0.3", f"--config_uri={uri}"])
  with caplog.at_level(logging.WARNING):
    rp.log_config_resolution(args.config_resolution)
  assert "generation.similarity" in caplog.text
  assert "0.3" in caplog.text and "0.7" in caplog.text


def test_blank_config_uri_is_no_file():
  args, _ = rp.parse_args([*ARGV_BASE, "--config_uri="])
  assert args.config_uri == ""
  assert set(args.config_resolution.provenance.values()) <= {"flag", "default"}


def test_missing_file_exits_2_with_the_uri(tmp_path, capsys):
  missing = str(tmp_path / "nope.yaml")
  with pytest.raises(SystemExit) as e:
    rp.parse_args([*ARGV_BASE, f"--config_uri={missing}"])
  assert e.value.code == 2
  assert f"could not read --config_uri {missing}" in capsys.readouterr().err


def test_bad_file_exits_2_naming_the_key(tmp_path, capsys):
  uri = _write(tmp_path, "generation:\n  similarty: 0.1\n")
  with pytest.raises(SystemExit) as e:
    rp.parse_args([*ARGV_BASE, f"--config_uri={uri}"])
  assert e.value.code == 2
  assert "did you mean 'generation.similarity'" in capsys.readouterr().err


def test_config_of_a_hand_built_namespace_is_the_default():
  assert rp.config_of(argparse.Namespace()) is rp.DEFAULT_CONFIG
