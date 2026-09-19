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
"""Unit tests for `scripts/dsg/headers.py` — the licence-header tool.

Every source file carries one Apache-2.0 header. The Dataflow Solution Guides
copy must carry the same block under another holder, so the sync rewrites the
copyright line and nothing else: a header that shifts a line would make the
two trees disagree on every traceback.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parents[5] / "scripts" / "dsg" / "headers.py"
_spec = importlib.util.spec_from_file_location("dsg_headers", _SCRIPT)
headers = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = headers
_spec.loader.exec_module(headers)

_FIRST = f"#  Copyright 2026 {headers.HOLDER}"


def _write(root: Path, rel: str, text: str) -> None:
  path = root / rel
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text, encoding="utf-8")


def test_header_is_the_thirteen_line_apache_block():
  block = headers.header_lines(headers.HOLDER, 2026)
  assert len(block) == 13
  assert block[0] == _FIRST
  assert block[6] == "#      https://www.apache.org/licenses/LICENSE-2.0"
  assert block[-1] == "#  limitations under the License."


def test_python_docstring_follows_the_header_with_no_blank_line():
  out = headers.apply('"""Doc."""\n\nimport os\n', "pkg/mod.py", year=2026)
  lines = out.splitlines()
  assert lines[0] == _FIRST
  assert lines[13] == '"""Doc."""'
  assert out.endswith('"""Doc."""\n\nimport os\n')


def test_a_file_that_opens_with_code_gets_one_blank_line():
  out = headers.apply("import os\n", "pkg/mod.py", year=2026)
  assert out.splitlines()[13:] == ["", "import os"]
  out = headers.apply('provider "google" {}\n', "infra/main.tf", year=2026)
  assert out.splitlines()[13:] == ["", 'provider "google" {}']


def test_shebang_stays_on_the_first_line():
  out = headers.apply("#!/usr/bin/env bash\nset -e\n", "run.sh", year=2026)
  lines = out.splitlines()
  assert lines[0] == "#!/usr/bin/env bash"
  assert lines[1] == _FIRST
  assert lines[14:] == ["", "set -e"]


def test_shebang_then_docstring_keeps_the_docstring_adjacent():
  out = headers.apply(
      '#!/usr/bin/env python\n"""Doc."""\n', "tool.py", year=2026)
  lines = out.splitlines()
  assert lines[0] == "#!/usr/bin/env python"
  assert lines[14] == '"""Doc."""'


def test_dockerfile_parser_directive_stays_on_the_first_line():
  out = headers.apply(
      "# syntax=docker/dockerfile:1\nFROM scratch\n",
      "docker/Dockerfile",
      year=2026)
  lines = out.splitlines()
  assert lines[0] == "# syntax=docker/dockerfile:1"
  assert lines[1] == _FIRST


def test_empty_file_becomes_the_header_alone():
  out = headers.apply("", "pkg/__init__.py", year=2026)
  assert out == "\n".join(headers.header_lines(headers.HOLDER, 2026)) + "\n"


def test_apply_is_idempotent():
  once = headers.apply('"""Doc."""\n', "pkg/mod.py", year=2026)
  assert headers.apply(once, "pkg/mod.py", year=2027) == once


def test_an_earlier_year_is_a_valid_header():
  text = "\n".join(headers.header_lines(headers.HOLDER, 2025)) + "\nx = 1\n"
  assert headers.problem(text) is None
  assert headers.apply(text, "pkg/mod.py", year=2026) == text


def test_retitle_rewrites_the_copyright_line_and_nothing_else():
  source = headers.apply(
      '#!/usr/bin/env python\n"""Doc."""\n', "tool.py", year=2026)
  shipped = headers.retitle(source, headers.DSG_HOLDER)
  before, after = source.splitlines(), shipped.splitlines()
  assert len(before) == len(after)
  changed = [
      i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b
  ]
  assert changed == [1]
  assert after[1] == "#  Copyright 2026 Google LLC"
  assert headers.problem(shipped, holder=headers.DSG_HOLDER) is None


def test_problem_names_what_is_wrong():
  good = headers.apply("x = 1\n", "pkg/mod.py", year=2026)
  assert headers.problem(good) is None
  assert headers.problem("x = 1\n") == "missing header"
  assert headers.problem(headers.retitle(
      good, "Someone Else")) == ("unexpected holder: Someone Else")
  truncated = "\n".join(good.splitlines()[:5]) + "\nx = 1\n"
  assert headers.problem(truncated) == "malformed header"
  # A header below the top is never auto-fixed: `--fix` would add a second.
  assert headers.problem("\n" + good) == "malformed header"
  assert headers.apply("\n" + good, "pkg/mod.py", year=2026) == "\n" + good


def test_scope_is_source_files_with_hash_comments():
  for rel in ("a/b.py", "run.sh", "infra/main.tf", "tests/x.tftest.hcl",
              "cloudbuild.yaml", "config/models.yml", "pyproject.toml",
              "docker/Dockerfile"):
    assert headers.in_scope(rel), rel
  # Byte-vendored, generated, or a format with no comment syntax.
  for rel in ("dsg/pylintrc", "uv.lock", ".python-version", "schema.json",
              "requirements.txt", "MANIFEST.in", ".gitignore", "README.md",
              "docs/assets/figure.png"):
    assert not headers.in_scope(rel), rel


def test_check_reports_and_fix_repairs_only_a_missing_header(tmp_path):
  _write(tmp_path, "pkg/ok.py",
         headers.apply("x = 1\n", "pkg/ok.py", year=2026))
  _write(tmp_path, "pkg/bare.py", '"""Doc."""\n')
  foreign = headers.retitle(
      headers.apply("y = 2\n", "pkg/foreign.py", year=2026), "Someone Else")
  _write(tmp_path, "pkg/foreign.py", foreign)
  _write(tmp_path, "notes.md", "# no header wanted\n")

  assert headers.check(tmp_path) == [
      "pkg/bare.py: missing header",
      "pkg/foreign.py: unexpected holder: Someone Else",
  ]
  assert headers.fix(tmp_path, year=2026) == ["pkg/bare.py"]
  assert headers.check(tmp_path) == [
      "pkg/foreign.py: unexpected holder: Someone Else"
  ]
  assert (tmp_path / "pkg/foreign.py").read_text(encoding="utf-8") == foreign


def test_every_source_file_in_this_repository_carries_the_header():
  assert headers.check(_SCRIPT.parents[2]) == []


def test_main_exit_code(tmp_path, capsys):
  _write(tmp_path, "pkg/bare.py", "x = 1\n")
  assert headers.main(["--root", str(tmp_path)]) == 1
  assert "pkg/bare.py: missing header" in capsys.readouterr().out
  assert headers.main(["--root", str(tmp_path), "--fix"]) == 0
  assert headers.main(["--root", str(tmp_path)]) == 0
