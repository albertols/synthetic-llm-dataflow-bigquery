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
import json
import logging

import pytest
from sdfb_beam.cli import run_pipeline as rp
from sdfb_tests.config_parity import ARGV_BASE
from sdfb_tests.launcher_logging import restoring_root_handlers


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


_EVALUATION_INPUTS = ("io.reference_table", "io.landing_table",
                      "relational.relationships_uri",
                      "relational.generate_fk_relationships",
                      "sampling.reference_rows_limit")


def _evaluation_input_warnings(caplog) -> list[str]:
  return [
      r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and
      r.getMessage().startswith("config_sets_evaluation_input")
  ]


def test_a_file_set_evaluation_input_warns_when_no_record_is_written(
    tmp_path, caplog):
  uri = _write(
      tmp_path, "io: {reference_table: p.src.u, landing_table: p.land.u}\n"
      "relational: {relationships_uri: gs://b/rel/, "
      "generate_fk_relationships: false}\n"
      "sampling: {reference_rows_limit: 5000}\n"
      "generation: {similarity: 0.7}\n")
  args, _ = rp.parse_args([*ARGV_BASE, f"--config_uri={uri}"])
  with caplog.at_level(logging.WARNING):
    rp.log_config_resolution(args.config_resolution)
  assert not _evaluation_input_warnings(caplog)  # the record carries them
  with caplog.at_level(logging.WARNING):
    rp.warn_unrecorded_evaluation_inputs(args.config_resolution,
                                         "validation_runs lacks the columns")
  assert _evaluation_input_warnings(caplog) == [
      f"config_sets_evaluation_input key={key} (no generation record is "
      "written — validation_runs lacks the columns — so a chained evaluation "
      "reads the DAG param, not the file)" for key in _EVALUATION_INPUTS
  ]


def test_evaluation_inputs_from_flags_do_not_warn(tmp_path, caplog):
  uri = _write(tmp_path, "generation: {similarity: 0.7}\n")
  args, _ = rp.parse_args([
      *ARGV_BASE, "--reference_rows_limit=5000", "--relationships_uri=",
      f"--config_uri={uri}"
  ])
  with caplog.at_level(logging.WARNING):
    rp.warn_unrecorded_evaluation_inputs(args.config_resolution, "no table")
  assert not _evaluation_input_warnings(caplog)


def test_a_reference_null_lets_the_typed_flag_fill_the_key(tmp_path):
  # The rendered reference shows the required keys as null; a file copied
  # from it must not make the launch blame the operator's typed flag.
  uri = _write(
      tmp_path, "io:\n  reference_table: null\n  landing_table: null\n"
      "  dlq_table: null\n")
  args, _ = rp.parse_args([*ARGV_BASE, f"--config_uri={uri}"])
  assert args.reference_table == "p.src.t"
  assert args.config_resolution.provenance["io.reference_table"] == "flag"


def test_unquoted_yaml_off_and_on_are_accepted(tmp_path):
  uri = _write(
      tmp_path, "sampling:\n  source_stats: off\n"
      "free_text:\n  prompt_constraints: on\n  prompt_debug: off\n"
      "  freetext_expansion: off\n")
  args, _ = rp.parse_args([*ARGV_BASE, f"--config_uri={uri}"])
  assert (args.source_stats, args.prompt_constraints, args.prompt_debug,
          args.freetext_expansion) == ("off", "on", "off", "off")


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


def test_main_reads_the_file(tmp_path, monkeypatch, capsys):
  uri = _write(
      tmp_path, "config_version: 1\n"
      "generation: {similarity: 0.7}\n"
      "free_text: {pool_values_per_call: 16}\n")
  seen = []

  def fake_run_one_table(a, *_unused, **_kwargs):
    seen.append(a)
    return 0

  monkeypatch.setattr(rp, "_run_one_table", fake_run_one_table)
  with restoring_root_handlers():
    rc = rp.main([
        *ARGV_BASE, "--similarity=0.3", "--relationships_uri=",
        "--client_type=fake", f"--config_uri={uri}"
    ])
  assert rc == 0 and len(seen) == 1
  args = seen[0]
  assert args.similarity == 0.7
  assert rp.config_of(args).free_text.pool_values_per_call == 16
  # main() reconfigures logging onto stderr, so read the launcher's log there.
  err = capsys.readouterr().err
  assert err.count("config_resolved uri=") == 1
  assert "config_override key=generation.similarity" in err
  assert f"config_resolved uri={uri} file=2" in err
  block = err.split("name=launch_config", 1)[1]
  body = block[block.index("{"):]
  launch_config = json.JSONDecoder().raw_decode(body)[0]
  assert launch_config["config_uri"] == uri
  assert "config_resolution" not in launch_config
