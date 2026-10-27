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
"""The DAG's generator.yaml digest against the image's (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import logging

import pytest
from sdfb_beam.cli import run_pipeline as rp
from sdfb_core.config.defaults import defaults_digest
from sdfb_core.record import build_snapshot
from sdfb_tests.config_parity import ARGV_BASE

_MARK = "name=generator_defaults_mismatch"


def _mismatch_lines(caplog, argv):
  args, _ = rp.parse_args(argv)
  with caplog.at_level(logging.INFO):
    rp.log_defaults_mismatch(args.config_resolution)
  return [r for r in caplog.records if _MARK in r.getMessage()]


def test_matching_digest_logs_nothing(caplog):
  argv = [*ARGV_BASE, f"--generator_defaults_digest={defaults_digest()}"]
  assert not _mismatch_lines(caplog, argv)


def test_mismatched_digest_warns_with_both(caplog):
  argv = [*ARGV_BASE, "--generator_defaults_digest=abcdef012345"]
  (record,) = _mismatch_lines(caplog, argv)
  assert record.levelno == logging.WARNING
  assert record.getMessage().endswith(
      f"{_MARK} dag=abcdef012345 image={defaults_digest()}")


def test_absent_digest_logs_nothing(caplog):
  assert not _mismatch_lines(caplog, list(ARGV_BASE))


def test_snapshot_records_the_image_digest():
  args, _ = rp.parse_args(list(ARGV_BASE))
  snapshot = build_snapshot(
      args.config_resolution,
      derived={},
      thresholds={},
      relationships={},
      beam={},
      launcher_version="v")
  assert snapshot["launcher"]["defaults_digest"] == defaults_digest()


def test_a_file_cannot_set_the_digest(tmp_path):
  path = tmp_path / "launch.yaml"
  path.write_text("launch:\n  generator_defaults_digest: abcdef012345\n")
  with pytest.raises(SystemExit):
    rp.parse_args([*ARGV_BASE, f"--config_uri={path}"])


def test_the_resolver_names_the_key(tmp_path):
  from sdfb_core.config.resolve import ConfigError, parse_config_text
  with pytest.raises(
      ConfigError,
      match=r"launch\.generator_defaults_digest is launch identity"):
    parse_config_text(
        "launch:\n  generator_defaults_digest: abcdef012345\n", source="f.yaml")
