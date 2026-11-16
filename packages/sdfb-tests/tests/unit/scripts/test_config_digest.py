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
"""`scripts/config/config_digest.py`: a config file's digest, as the record
writes it (ADR 0044) — how a downloaded config.yaml is checked.

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from sdfb_core.config.resolve import parse_config_text, resolve
from sdfb_core.record import config_digest

_SCRIPT = Path(__file__).parents[5] / "scripts" / "config" / "config_digest.py"
_spec = importlib.util.spec_from_file_location("config_digest_script", _SCRIPT)
assert _spec and _spec.loader
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def test_it_prints_the_digest_of_the_files_config(tmp_path, capsys):
  path = tmp_path / "c.yaml"
  path.write_text("generation: {similarity: 0.7}\n")
  assert script.main([str(path)]) == 0
  want = config_digest(
      resolve({}, parse_config_text(path.read_text(), source="x")).config)
  assert capsys.readouterr().out.strip() == want


def test_a_bad_file_exits_2(tmp_path, capsys):
  path = tmp_path / "c.yaml"
  path.write_text("generation: {similarty: 0.7}\n")
  assert script.main([str(path)]) == 2
  assert "did you mean" in capsys.readouterr().err
