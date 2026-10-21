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
"""The rendered reference file documents every key and resolves to the defaults.
"""
from pathlib import Path

from sdfb_core.config.model import ALL_KEYS, DEFAULT_CONFIG, IDENTITY_KEYS
from sdfb_core.config.render import render_reference
from sdfb_core.config.resolve import parse_config_text, resolve

_REPO = Path(__file__).resolve().parents[5]


def test_committed_file_is_current():
  assert (_REPO / "config/generator.yaml").read_text().endswith(
      render_reference())


def test_every_settable_key_appears_once():
  text = render_reference()
  for dotted in ALL_KEYS:
    if dotted in IDENTITY_KEYS.values():
      assert "# " + dotted.split(".")[1] + ":" in text
    else:
      assert "\n  " + dotted.split(".")[1] + ":" in text, dotted


def test_reference_resolves_to_the_defaults():
  values = parse_config_text(render_reference(), source="config/generator.yaml")
  assert resolve({}, values).config == DEFAULT_CONFIG


def test_personal_example_resolves():
  text = (_REPO / "config/generator.personal.example.yaml").read_text()
  values = parse_config_text(text, source="example")
  assert resolve({"run_id": "r"}, values).config.guardrails.env == "dev"
