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
import importlib.util
from pathlib import Path

from sdfb_core.config.catalogue import load_catalogue
from sdfb_core.config.model import (ALL_KEYS, DEFAULT_CONFIG, IDENTITY_KEYS,
                                    REQUIRED_FLAGS, SECTION_TYPES)
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
      assert text.count("\n  # " + dotted.split(".")[1] + ":") == 1, dotted
    else:
      assert text.count("\n  " + dotted.split(".")[1] + ":") == 1, dotted


def test_reference_resolves_to_the_defaults():
  values = parse_config_text(render_reference(), source="config/generator.yaml")
  assert resolve({}, values).config == DEFAULT_CONFIG


def test_personal_example_resolves():
  text = (_REPO / "config/generator.personal.example.yaml").read_text()
  values = parse_config_text(text, source="example")
  assert resolve({"run_id": "r"}, values).config.guardrails.env == "dev"


def test_every_null_default_is_required_or_derived():
  entries = load_catalogue().entries
  for section, cls in SECTION_TYPES.items():
    for key, info in cls.model_fields.items():
      if info.default is None:
        assert (key in REQUIRED_FLAGS or key in IDENTITY_KEYS or
                entries[f"{section}.{key}"].null_means), f"{section}.{key}"


def test_a_null_max_model_len_says_it_is_no_cap():
  # A null there is a value (no cap), not "unset": the file must say so.
  assert ("  # null = no cap — the checkpoint's native context (the T4 needs "
          "a cap)\n  vllm_max_model_len: '8192'\n") in render_reference()


def test_no_word_is_split_at_a_hyphen():
  text = render_reference()
  assert "re-\n" not in text and "co-\n" not in text


def test_check_exits_1_on_a_drifted_file(tmp_path, monkeypatch):
  spec = importlib.util.spec_from_file_location(
      "render_generator_config",
      _REPO / "scripts/doc/render_generator_config.py")
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  target = tmp_path / "generator.yaml"
  monkeypatch.setattr(module, "_TARGET", target)
  monkeypatch.setattr("sys.argv", ["x", "--check"])
  assert module.main() == 1  # missing file
  target.write_text(module._LICENCE + render_reference(), encoding="utf-8")  # pylint: disable=protected-access
  assert module.main() == 0
  target.write_text(
      target.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
  assert module.main() == 1
