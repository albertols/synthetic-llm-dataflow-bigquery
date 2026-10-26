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
"""`generator.yaml` holds the defaults; the model keeps the types only.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from sdfb_core.config.defaults import (GENERATOR_YAML, defaults_digest,
                                       load_defaults)
from sdfb_core.config.catalogue import load_catalogue
from sdfb_core.config.model import (DEFAULT_CONFIG, IDENTITY_KEYS,
                                    SECTION_TYPES, GeneratorConfig)

_REPO = Path(__file__).resolve().parents[5]
_MODEL_PY = _REPO / "packages/sdfb-core/src/sdfb_core/config/model.py"
_SCRIPT = _REPO / "scripts/doc/render_generator_config.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run([sys.executable, str(_SCRIPT), *args],
                        capture_output=True,
                        text=True,
                        check=False,
                        cwd=_REPO)


def _copy(tmp_path: Path) -> Path:
  target = tmp_path / "generator.yaml"
  target.write_bytes(GENERATOR_YAML.read_bytes())
  return target


def _set_similarity(path: Path, value: str) -> None:
  text = path.read_text(encoding="utf-8")
  assert text.count("\n  similarity: 0.5\n") == 1
  path.write_text(
      text.replace("\n  similarity: 0.5\n", f"\n  similarity: {value}\n"),
      encoding="utf-8")


def test_default_config_is_the_yaml():
  from_yaml = GeneratorConfig.model_validate(load_defaults())
  assert (DEFAULT_CONFIG.model_dump(mode="python") == from_yaml.model_dump(
      mode="python"))


def test_model_py_holds_no_default_literal():
  calls = [
      node for node in ast.walk(ast.parse(_MODEL_PY.read_text()))
      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
      node.func.id in {"_flag", "_const"}
  ]
  assert calls  # the walk found the fields
  for call in calls:
    assert not call.args, f"line {call.lineno}: a positional default"
    assert all(k.arg != "default" for k in call.keywords), (
        f"line {call.lineno}: a default= keyword")


def test_a_changed_yaml_value_changes_the_default(tmp_path):
  target = _copy(tmp_path)
  _set_similarity(target, "0.7")
  assert load_defaults(target)["generation"]["similarity"] == 0.7
  assert load_defaults()["generation"]["similarity"] == 0.5


def test_the_root_path_is_the_packaged_file():
  root = _REPO / "config/generator.yaml"
  assert root.is_symlink()
  assert root.resolve() == GENERATOR_YAML.resolve()
  assert root.read_bytes() == GENERATOR_YAML.read_bytes()


def test_the_digest_is_the_file_bytes(tmp_path):
  target = _copy(tmp_path)
  digest = defaults_digest(target)
  assert digest == defaults_digest() and len(digest) == 12
  _set_similarity(target, "0.7")
  assert defaults_digest(target) != digest


def test_a_wrong_config_version_is_refused(tmp_path):
  target = _copy(tmp_path)
  text = target.read_text(encoding="utf-8")
  target.write_text(
      text.replace("\nconfig_version: 1\n", "\nconfig_version: 2\n"),
      encoding="utf-8")
  with pytest.raises(ValueError, match="config_version"):
    load_defaults(target)


def _scramble_comments(path: Path) -> None:
  lines = path.read_text(encoding="utf-8").splitlines()
  out = []
  for line in lines:
    stripped = line.lstrip()
    if stripped.startswith("# ") and ":" not in stripped:
      out.append(line[:len(line) - len(stripped)] + "# scrambled")
    else:
      out.append(line)
  path.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_fix_never_changes_a_value(tmp_path):
  target = _copy(tmp_path)
  _set_similarity(target, "0.7")
  before = load_defaults(target)
  _scramble_comments(target)
  assert _run("--check", str(target)).returncode == 1
  fixed = _run("--fix", str(target))
  assert fixed.returncode == 0, fixed.stderr
  assert load_defaults(target) == before
  assert load_defaults(target)["generation"]["similarity"] == 0.7
  assert "scrambled" not in target.read_text(encoding="utf-8")
  assert _run("--check", str(target)).returncode == 0


def test_check_fails_on_stale_comments(tmp_path):
  target = _copy(tmp_path)
  lines = target.read_text(encoding="utf-8").splitlines()
  at = lines.index("  similarity: 0.5")
  lines[at - 1] = "  # a stale comment"
  target.write_text("\n".join(lines) + "\n", encoding="utf-8")
  checked = _run("--check", str(target))
  assert checked.returncode == 1
  assert "generation.similarity" in checked.stderr
  assert _run("--check").returncode == 0


def test_every_yaml_key_is_a_model_field_and_back():
  values = load_defaults()
  assert set(values) == set(SECTION_TYPES)
  identity = {d.split(".", 1)[1] for d in IDENTITY_KEYS.values()}
  for section, cls in SECTION_TYPES.items():
    assert set(values[section]) == set(cls.model_fields) - identity, section


def test_the_image_resolves_the_root_link():
  # The relative link resolves only where config/ and packages/sdfb-core/src
  # land side by side under the one WORKDIR.
  final = (_REPO / "docker/Dockerfile").read_text().rsplit("\nFROM ", 1)[1]
  assert "\nWORKDIR /workspace\n" in final
  assert "\nCOPY packages/sdfb-core/src packages/sdfb-core/src\n" in final
  assert "\nCOPY config config\n" in final


# 75 % of the 749 lines the file had before it was made compact.
_LINE_BUDGET = 561


def test_reference_is_compact():
  lines = GENERATOR_YAML.read_text(encoding="utf-8").splitlines()
  assert len(lines) <= _LINE_BUDGET, len(lines)


def _comment_runs() -> dict[str, list[str]]:
  """Per `section.key`, the comment lines directly above its value line."""
  runs: dict[str, list[str]] = {}
  section = ""
  above: list[str] = []
  for line in GENERATOR_YAML.read_text(encoding="utf-8").splitlines():
    if line in {f"{name}:" for name in SECTION_TYPES}:
      section, above = line[:-1], []
    elif line.startswith("  #") and section:
      key = line[4:].split(":", 1)[0]
      if line.endswith(": null") and key in IDENTITY_KEYS:
        runs[f"{section}.{key}"], above = above, []
      else:
        above.append(line)
    elif line.startswith("  ") and section:
      name = line.split(":", 1)[0].strip()
      runs[f"{section}.{name}"] = above
      above = []
    else:
      above = []
  return runs


def test_every_entry_has_at_most_three_comment_lines_plus_null():
  entries = load_catalogue().entries
  runs = _comment_runs()
  assert len(runs) == sum(
      len(cls.model_fields) for cls in SECTION_TYPES.values())
  for dotted, comments in runs.items():
    limit = 4 if entries[dotted].null_means else 3
    assert 1 <= len(comments) <= limit, (dotted, comments)


def test_compact_entry_layout():
  comments = _comment_runs()["generation.similarity"]
  assert comments[0].startswith("  # Similarity — ")
  assert "--similarity" in comments[0]
  arrows = [c for c in comments if c.startswith("  # ↑ ")]
  assert len(arrows) == 1 and " · ↓ " in arrows[0]
