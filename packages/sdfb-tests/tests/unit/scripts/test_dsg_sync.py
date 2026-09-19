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
"""Unit tests for `scripts/dsg/sync.py` — golden source -> DSG replica.

Everything the sync decides without a network or a subprocess is covered
here: which files ship, where overlays land, how links to unshipped docs
are pinned, that index rows are idempotent, that owned paths are replaced
wholesale while the Terraform-generated env script survives, and that the
PR body has no unrendered placeholder (ADR 0040).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[5] / "scripts" / "dsg" / "sync.py"
_spec = importlib.util.spec_from_file_location("dsg_sync", _SCRIPT)
sync = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync
_spec.loader.exec_module(sync)

_PIPE = "pipelines/demo"


def _write(root: Path, rel: str, text: str = "x\n") -> Path:
  path = root / rel
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text, encoding="utf-8")
  return path


def _manifest(**overrides):
  raw = {
      "source_repo": "https://github.com/acme/demo",
      "target_repo": "Org/guides",
      "target_base": "main",
      "branch_prefix": "sync/demo-",
      "pipeline_dir": _PIPE,
      "owned_paths": [_PIPE, "terraform/demo", "use_cases/Demo.md"],
      "preserve": [f"{_PIPE}/scripts/00_set_variables.sh"],
      "include": ["README.md", "pkg/**", "docs/adr/**", "scripts/tool.py"],
      "exclude": ["**/__pycache__/**", "pkg/tests/test_unshipped_tool.py"],
      "script_tests": {
          "pkg/tests/test_tool.py": "scripts/tool.py",
          "pkg/tests/test_unshipped_tool.py": "scripts/unshipped.py",
      },
      "overlays": {
          "dsg/pipeline": _PIPE,
          "dsg/terraform": "terraform/demo",
          "dsg/use_cases": "use_cases",
      },
      "readme_header": "dsg/pipeline/README.header.md",
      "pylintrc": {
          "vendored": "dsg/pylintrc",
          "target": "pipelines/pylintrc"
      },
      "precheck_config": "dsg/precheck.yaml",
      "pr_template": "dsg/PR_TEMPLATE.md",
      "index_rows": [],
  }
  raw.update(overrides)
  return sync.Manifest.from_dict(raw)


def _source(root: Path) -> Path:
  _write(
      root, "README.md", "# Demo\nSee [ADR](docs/adr/0001.md) and "
      "[playbook](docs/PLAYBOOK.md#run) and [web](https://x.org).\n")
  _write(root, "pkg/mod.py")
  _write(root, "pkg/__pycache__/mod.cpython-312.pyc")
  _write(root, "pkg/tests/test_tool.py")
  _write(root, "pkg/tests/test_unshipped_tool.py")
  _write(root, "docs/adr/0001.md", "Back to [readme](../../README.md).\n")
  _write(root, "docs/PLAYBOOK.md")
  _write(root, "scripts/tool.py")
  _write(root, "scripts/unshipped.py")
  _write(
      root, "dsg/pipeline/README.header.md", "> DSG quick start\n"
      "> release {ref} ([{short_sha}]({source_repo}/tree/{sha}))\n"
      "flow{decision} --> run\n")
  _write(root, "dsg/pipeline/setup.py")
  _write(root, "dsg/pipeline/scripts/01_build.sh", "echo build\n")
  _write(root, "dsg/terraform/main.tf", "# tf\n")
  _write(root, "dsg/use_cases/Demo.md",
         "[pipeline](../pipelines/demo/README.md)\n")
  return root


def test_select_files_applies_include_and_exclude(tmp_path):
  src = _source(tmp_path / "src")
  selected = sync.select_files(src, _manifest())
  assert selected == [
      "README.md", "docs/adr/0001.md", "pkg/mod.py", "pkg/tests/test_tool.py",
      "scripts/tool.py"
  ]


def test_select_files_rejects_a_shipped_test_whose_script_is_not_shipped(
    tmp_path):
  src = _source(tmp_path / "src")
  manifest = _manifest(exclude=["**/__pycache__/**"])
  with pytest.raises(sync.SyncError, match=r"test_unshipped_tool\.py"):
    sync.select_files(src, manifest)


def test_stage_tree_maps_sources_overlays_and_renders_the_header(tmp_path):
  src = _source(tmp_path / "src")
  staging = tmp_path / "staging"
  sync.stage_tree(src, _manifest(), staging, sha="a" * 40, ref="v1.2.3")
  pipe = staging / _PIPE
  assert (pipe / "pkg/mod.py").exists()
  assert (pipe / "setup.py").exists()
  assert (pipe / "scripts/01_build.sh").exists()
  assert not (pipe / "README.header.md").exists()
  assert (staging / "terraform/demo/main.tf").exists()
  assert (staging / "use_cases/Demo.md").exists()
  readme = (pipe / "README.md").read_text(encoding="utf-8")
  assert readme.startswith("> DSG quick start\n")
  assert "# Demo" in readme
  # Provenance is a line people read, not a file the guide has to carry.
  assert ("> release v1.2.3 ([" + "a" * 12 +
          "](https://github.com/acme/demo/tree/" + "a" * 40 + "))\n") in readme
  assert "flow{decision} --> run\n" in readme
  assert not (pipe / ".sync-source.json").exists()


def _git_repo(repo: Path) -> list[str]:
  repo.mkdir(parents=True)
  subprocess.run(["git", "init", "-q", str(repo)], check=True)
  return [
      "git", "-C",
      str(repo), "-c", "user.email=me@example.com", "-c", "user.name=me"
  ]


def test_prev_sha_is_read_from_the_shipped_readme(tmp_path):
  _write(tmp_path, f"{_PIPE}/README.md",
         "release v1 (https://github.com/acme/demo/tree/" + "d" * 40 + ")\n")
  assert sync.read_prev_sha(tmp_path, _manifest()) == "d" * 40


def test_prev_sha_falls_back_to_the_last_sync_commit(tmp_path):
  repo = tmp_path / "dsg"
  git = _git_repo(repo)
  _write(repo, f"{_PIPE}/README.md", "# a guide synced before v1\n")
  subprocess.run([*git, "add", "."], check=True)
  subprocess.run([
      *git, "commit", "-qm", "feat(demo): sync from source v0.9\n\n"
      "Source: https://github.com/acme/demo/tree/" + "e" * 40
  ],
                 check=True)
  _write(repo, "README.md", "# guides\n")
  subprocess.run([*git, "add", "."], check=True)
  subprocess.run([*git, "commit", "-qm", "docs: unrelated"], check=True)
  assert sync.read_prev_sha(repo, _manifest()) == "e" * 40


def test_prev_sha_is_none_on_a_first_sync(tmp_path):
  repo = tmp_path / "dsg"
  git = _git_repo(repo)
  _write(repo, "README.md", "# guides\n")
  subprocess.run([*git, "add", "."], check=True)
  subprocess.run([*git, "commit", "-qm", "init"], check=True)
  assert sync.read_prev_sha(repo, _manifest()) is None
  assert sync.read_prev_sha(tmp_path / "not-a-checkout", _manifest()) is None


def test_rewrite_links_pins_unshipped_targets_and_keeps_shipped_ones(tmp_path):
  src = _source(tmp_path / "src")
  staging = tmp_path / "staging"
  dsg = tmp_path / "dsg"
  _write(dsg, "pipelines/pylintrc")
  manifest = _manifest()
  sync.stage_tree(src, manifest, staging, sha="b" * 40, ref="v1")
  broken = sync.rewrite_links(staging, dsg, manifest, sha="b" * 40)
  assert not broken
  readme = (staging / _PIPE / "README.md").read_text(encoding="utf-8")
  assert "[ADR](docs/adr/0001.md)" in readme
  assert ("[playbook](https://github.com/acme/demo/blob/" + "b" * 40 +
          "/docs/PLAYBOOK.md#run)") in readme
  assert "[web](https://x.org)" in readme
  adr = (staging / _PIPE / "docs/adr/0001.md").read_text(encoding="utf-8")
  assert "[readme](../../README.md)" in adr


def test_rewrite_links_sees_the_target_behind_a_badge_image(tmp_path):
  src = tmp_path / "src"
  _write(src, "LICENSE")
  _write(src, "pyproject.toml")
  staging = tmp_path / "staging"
  dsg = tmp_path / "dsg"
  dsg.mkdir()
  _write(staging, f"{_PIPE}/pyproject.toml")
  _write(
      staging, f"{_PIPE}/README.md",
      "[![License](https://img.shields.io/l.svg)](LICENSE)\n"
      "[![Python](https://img.shields.io/p.svg)](pyproject.toml)\n"
      "[![Gone](https://img.shields.io/g.svg)](NOTICE)\n")
  broken = sync.rewrite_links(
      staging, dsg, _manifest(), sha="f" * 40, export_root=src)
  readme = (staging / _PIPE / "README.md").read_text(encoding="utf-8")
  assert ("[![License](https://img.shields.io/l.svg)]"
          "(https://github.com/acme/demo/blob/" + "f" * 40 +
          "/LICENSE)") in readme
  assert "[![Python](https://img.shields.io/p.svg)](pyproject.toml)" in readme
  assert broken == [f"{_PIPE}/README.md -> NOTICE"]


def test_rewrite_links_reports_links_broken_everywhere(tmp_path):
  staging = tmp_path / "staging"
  dsg = tmp_path / "dsg"
  dsg.mkdir()
  _write(staging, "use_cases/Demo.md", "[gone](../terraform/nope/README.md)\n")
  broken = sync.rewrite_links(staging, dsg, _manifest(), sha="c" * 40)
  assert broken == ["use_cases/Demo.md -> ../terraform/nope/README.md"]


def test_index_rows_are_idempotent_and_update_in_place(tmp_path):
  _write(tmp_path, "README.md", "| Guide |\n| :-: |\n| [A](./a.md) |\n")
  rows = [
      sync.IndexRow(
          file="README.md",
          anchor="[A](./a.md)",
          marker="Demo.md",
          text="| [Demo](./use_cases/Demo.md) v1 |")
  ]
  assert sync.apply_index_rows(tmp_path, rows) == ["README.md"]
  assert sync.apply_index_rows(tmp_path, rows) == []
  rows[0] = sync.IndexRow(
      file="README.md",
      anchor="[A](./a.md)",
      marker="Demo.md",
      text="| [Demo](./use_cases/Demo.md) v2 |")
  assert sync.apply_index_rows(tmp_path, rows) == ["README.md"]
  text = (tmp_path / "README.md").read_text(encoding="utf-8")
  assert text.count("Demo.md") == 1
  assert text.endswith("| [A](./a.md) |\n| [Demo](./use_cases/Demo.md) v2 |\n")


def test_index_block_is_replaced_between_markers(tmp_path):
  _write(tmp_path, "AGENTS.md", "# Agents\n")
  block = [
      sync.IndexRow(
          file="AGENTS.md",
          anchor=None,
          marker="demo",
          text="### Demo deployment\nv1")
  ]
  sync.apply_index_rows(tmp_path, block)
  block[0] = sync.IndexRow(
      file="AGENTS.md",
      anchor=None,
      marker="demo",
      text="### Demo deployment\nv2")
  sync.apply_index_rows(tmp_path, block)
  text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
  assert text.count("### Demo deployment") == 1
  assert "v2" in text and "v1" not in text


def test_index_row_with_missing_anchor_fails_loudly(tmp_path):
  _write(tmp_path, "README.md", "| other |\n")
  rows = [
      sync.IndexRow(
          file="README.md",
          anchor="[A](./a.md)",
          marker="Demo.md",
          text="| Demo |")
  ]
  with pytest.raises(sync.SyncError, match="anchor"):
    sync.apply_index_rows(tmp_path, rows)


def test_install_replaces_owned_paths_and_preserves_generated_env(tmp_path):
  src = _source(tmp_path / "src")
  staging = tmp_path / "staging"
  manifest = _manifest()
  sync.stage_tree(src, manifest, staging, sha="d" * 40, ref="v1")
  dsg = tmp_path / "dsg"
  _write(dsg, f"{_PIPE}/stale_file.py")
  _write(dsg, f"{_PIPE}/scripts/00_set_variables.sh", "export PROJECT=p\n")
  _write(dsg, "pipelines/other/keep.py")
  sync.install_into(dsg, staging, manifest)
  assert not (dsg / _PIPE / "stale_file.py").exists()
  assert (dsg / _PIPE / "pkg/mod.py").exists()
  assert (dsg / _PIPE / "scripts/00_set_variables.sh").read_text(
      encoding="utf-8") == "export PROJECT=p\n"
  assert (dsg / "pipelines/other/keep.py").exists()
  assert (dsg / "use_cases/Demo.md").exists()


def test_render_pr_body_fills_every_placeholder():
  template = ("{ref} {short_sha} {compare_url} {cloud_status}\n"
              "{changelog}\n{gates_table}\n")
  gates = [
      sync.GateResult("pylint", True, 1.5, ""),
      sync.GateResult("terraform", False, 2.0, "boom")
  ]
  body = sync.render_pr_body(
      template,
      manifest=_manifest(),
      ref="v1.2.3",
      sha="e" * 40,
      prev_sha="f" * 40,
      gates=gates,
      changelog="- added x",
      cloud_run=None)
  assert "{" not in body
  assert "compare/" + "f" * 40 + "..." + "e" * 40 in body
  assert "| pylint | ✅ |" in body and "| terraform | ❌ |" in body
  assert "Not yet run in Google Cloud" in body


def test_changelog_section_for_tag_and_unreleased():
  text = ("# Changelog\n## [Unreleased]\n- next\n"
          "## [1.2.3] - 2026-09-14\n- shipped\n## [1.2.2]\n- old\n")
  assert sync.changelog_section(text, "v1.2.3") == "- shipped"
  assert sync.changelog_section(text, "feature-branch") == "- next"


def test_changelog_section_matches_v_prefixed_release_headings():
  text = ("# Changelog\n## [Unreleased]\n\n## [v0.4.1] — 2026-09-15\n\n"
          "### Fixed\n- a fix\n\n## [v0.4.0] — 2026-09-14\n- older\n")
  assert sync.changelog_section(text, "v0.4.1") == "### Fixed\n- a fix"


def test_missing_tools_depends_on_gates_and_publishing(monkeypatch):
  monkeypatch.delenv("TERRAFORM_BIN", raising=False)
  on_path = {"git", "uv", "bash", "terraform"}

  def which(tool):
    return tool if tool in on_path else None

  assert not sync.missing_tools(gates="fast", publish=False, which=which)
  assert sync.missing_tools(
      gates="full", publish=True, which=which) == ["pipenv", "gh"]
  assert not sync.missing_tools(gates="none", publish=False, which=which)


def test_pylintrc_drift_is_detected(tmp_path):
  src = tmp_path / "src"
  dsg = tmp_path / "dsg"
  _write(src, "dsg/pylintrc", "[MAIN]\n")
  _write(dsg, "pipelines/pylintrc", "[MAIN]\njobs=4\n")
  result = sync.check_pylintrc(src, dsg, _manifest())
  assert not result.ok
  _write(dsg, "pipelines/pylintrc", "[MAIN]\n")
  assert sync.check_pylintrc(src, dsg, _manifest()).ok


def test_export_ref_uses_git_archive_so_untracked_files_never_ship(tmp_path):
  repo = tmp_path / "repo"
  repo.mkdir()
  subprocess.run(["git", "init", "-q", str(repo)], check=True)
  _write(repo, "tracked.txt")
  _write(repo, ".gitignore", "ignored.txt\n")
  subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
  subprocess.run([
      "git", "-C",
      str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t",
      "commit", "-qm", "init"
  ],
                 check=True)
  _write(repo, "ignored.txt")
  _write(repo, "untracked.txt")
  dest = tmp_path / "export"
  sha, committed_at = sync.export_ref(repo, "HEAD", dest)
  assert len(sha) == 40 and committed_at.endswith("Z")
  assert sorted(p.name for p in dest.iterdir()) == [".gitignore", "tracked.txt"]


def test_commit_uses_the_checkout_identity_and_no_trailers(tmp_path):
  repo = tmp_path / "dsg"
  repo.mkdir()
  git = [
      "git", "-C",
      str(repo), "-c", "user.email=me@example.com", "-c", "user.name=me"
  ]
  subprocess.run(["git", "init", "-q", str(repo)], check=True)
  _write(repo, "README.md", "# guides\n")
  subprocess.run([*git, "add", "."], check=True)
  subprocess.run([*git, "commit", "-qm", "init"], check=True)
  subprocess.run(
      ["git", "-C",
       str(repo), "config", "user.email", "me@example.com"],
      check=True)
  subprocess.run(
      ["git", "-C", str(repo), "config", "user.name", "me"], check=True)
  _write(repo, f"{_PIPE}/main.py")
  assert sync.commit(
      repo, _manifest(), ref="v1.2.3", sha="a" * 40, extra_files=["README.md"])
  log = subprocess.run(
      ["git", "-C", str(repo), "log", "-1", "--format=%an|%B"],
      capture_output=True,
      text=True,
      check=True).stdout
  author, body = log.split("|", 1)
  assert author == "me"
  assert body.strip() == ("feat(demo): sync from source v1.2.3 (" + "a" * 12 +
                          ")\n\nSource: https://github.com/acme/demo/tree/" +
                          "a" * 40)


def test_patches_apply_once_then_no_op_once_upstream_has_the_fix(tmp_path):
  wf = _write(tmp_path, ".github/workflows/ci.yml",
              "run: |\n  X=$(find d | head -n 1)\n")
  patches = [
      sync.Patch(
          file=".github/workflows/ci.yml",
          old="X=$(find d | head -n 1)",
          new="X=$(find d -print -quit)",
          reason="CI fix under review upstream")
  ]
  assert sync.apply_patches(tmp_path,
                            patches) == ([".github/workflows/ci.yml"],
                                         ["CI fix under review upstream"])
  assert "X=$(find d -print -quit)" in wf.read_text(encoding="utf-8")
  assert sync.apply_patches(tmp_path, patches) == ([], [])


def test_patch_with_neither_target_nor_replacement_stops_the_sync(tmp_path):
  _write(tmp_path, "ci.yml", "something else\n")
  patches = [sync.Patch(file="ci.yml", old="a", new="b", reason="r")]
  with pytest.raises(sync.SyncError, match="neither"):
    sync.apply_patches(tmp_path, patches)


def test_pr_body_lists_patches_in_effect():
  body = sync.render_pr_body(
      "{patches}",
      manifest=_manifest(),
      ref="v1",
      sha="a" * 40,
      prev_sha=None,
      gates=[],
      changelog="",
      cloud_run=None,
      patches=["CI fix under review upstream"])
  assert body == "- CI fix under review upstream"
  assert sync.render_pr_body(
      "{patches}",
      manifest=_manifest(),
      ref="v1",
      sha="a" * 40,
      prev_sha=None,
      gates=[],
      changelog="",
      cloud_run=None) == "_None._"


def test_each_ref_syncs_to_its_own_branch():
  manifest = _manifest()
  assert manifest.branch_for("v0.5.1") == "sync/demo-v0.5.1"
  assert manifest.branch_for("feat/x y") == "sync/demo-feat-x-y"
  assert manifest.is_sync_branch("sync/demo-v0.5.0")
  assert manifest.is_sync_branch("sync/demo")
  assert not manifest.is_sync_branch("sync/demolition")
  assert not manifest.is_sync_branch("fix/demo-ci")


def test_publishing_a_ref_supersedes_only_older_sync_prs_from_the_fork():

  def pr(number, head, owner="me"):
    return {
        "number": number,
        "url": f"https://github.com/Org/guides/pull/{number}",
        "headRefName": head,
        "headRepositoryOwner": {
            "login": owner
        },
    }

  prs = [
      pr(1, "sync/demo"),
      pr(2, "sync/demo-v0.5.0"),
      pr(3, "sync/demo-v0.5.1"),
      pr(4, "fix/demo-ci"),
      pr(5, "sync/demo-v0.4.0", owner="someone-else"),
  ]
  old = sync.superseded_prs(
      prs, _manifest(), owner="me", branch="sync/demo-v0.5.1")
  assert [p["number"] for p in old] == [1, 2]
