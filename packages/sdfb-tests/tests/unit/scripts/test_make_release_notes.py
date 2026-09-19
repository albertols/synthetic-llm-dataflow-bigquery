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
"""Unit tests for `scripts/release/make_release_notes.py` — the CHANGELOG
`[Unreleased]` promotion and the GitHub Release body it feeds.

Loaded via importlib the same way `test_make_release_report.py` loads
`scripts/release/make_release_report.py` (see that file's docstring/idiom).

Contract under test (spec: the release-notes convention documented in
`CHANGELOG.md` and wired in `.github/workflows/release_tag_report.yaml`):

  * `CHANGELOG.md` carries one permanent `## [Unreleased]` block holding the
    six emoji category headings. At tag time the release Action promotes it to
    `## [<tag>] — <date>` and re-opens a fresh empty `[Unreleased]` above it.
  * The promoted body — and nothing else — becomes the GitHub Release body,
    so the file on disk and the notes on the releases page can never drift.
  * An `[Unreleased]` block nobody filled in yields an empty body; the caller
    then falls back to conventional-commit titles, so a release is never
    published noteless.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(
    __file__).parents[5] / "scripts" / "release" / "make_release_notes.py"
_spec = importlib.util.spec_from_file_location("make_release_notes", _SCRIPT)
assert _spec and _spec.loader
notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notes)

REPO = "albertols/synthetic-llm-dataflow-bigquery"

_CHANGELOG = """# Changelog

Preamble prose that must survive promotion.

## [Unreleased]

### 🚀 Added

### 🔧 Changed

### 🐛 Fixed

## [v0.3.0] — 2026-09-13

### 🚀 Added
- Parent-driven fan-out (ADR 0036).
"""


def _filled(*,
            added: list[str] | None = None,
            removed: list[str] | None = None) -> str:
  """`_CHANGELOG` with bullets written under Added / Removed."""
  text = _CHANGELOG
  for bullet in added or []:
    text = text.replace("### 🚀 Added\n", f"### 🚀 Added\n- {bullet}\n", 1)
  for bullet in removed or []:
    text = text.replace("### 🐛 Fixed\n",
                        f"### 🗑️ Removed\n- {bullet}\n\n### 🐛 Fixed\n", 1)
  return text


# ---------------------------------------------------------------------------
# promote_unreleased
# ---------------------------------------------------------------------------
def test_promote_renames_unreleased_heading_to_tag_and_date():
  text, _ = notes.promote_unreleased(
      _filled(added=["Fan-out."]), "v0.3.1", "2026-09-20")

  assert "## [v0.3.1] — 2026-09-20" in text


def test_promote_returns_only_the_section_body_without_its_heading():
  _, body = notes.promote_unreleased(
      _filled(added=["Fan-out."]), "v0.3.1", "2026-09-20")

  assert body.startswith("### 🚀 Added")
  assert "- Fan-out." in body
  assert "[v0.3.1]" not in body


def test_promote_drops_category_headings_that_have_no_bullets():
  _, body = notes.promote_unreleased(
      _filled(added=["Fan-out."]), "v0.3.1", "2026-09-20")

  assert "### 🔧 Changed" not in body
  assert "### 🐛 Fixed" not in body


def test_promote_keeps_every_category_that_has_bullets():
  _, body = notes.promote_unreleased(
      _filled(added=["Fan-out."], removed=["FK cap."]), "v0.3.1", "2026-09-20")

  assert "### 🚀 Added" in body
  assert "### 🗑️ Removed" in body
  assert "- FK cap." in body


def test_promote_reopens_a_fresh_empty_unreleased_block_above_the_release():
  text, _ = notes.promote_unreleased(
      _filled(added=["Fan-out."]), "v0.3.1", "2026-09-20")

  assert text.index("## [Unreleased]") < text.index("## [v0.3.1]")
  fresh = text[text.index("## [Unreleased]"):text.index("## [v0.3.1]")]
  assert "- Fan-out." not in fresh
  for heading in notes.CATEGORY_ORDER:
    assert f"### {heading}" in fresh


def test_promote_preserves_the_preamble_and_previously_released_sections():
  text, _ = notes.promote_unreleased(
      _filled(added=["Fan-out."]), "v0.3.1", "2026-09-20")

  assert "Preamble prose that must survive promotion." in text
  assert "## [v0.3.0] — 2026-09-13" in text
  assert "- Parent-driven fan-out (ADR 0036)." in text


def test_promote_yields_an_empty_body_when_nobody_filled_the_block_in():
  text, body = notes.promote_unreleased(_CHANGELOG, "v0.3.1", "2026-09-20")

  assert body == ""
  assert "## [v0.3.1] — 2026-09-20" in text


def test_promote_rejects_a_changelog_with_no_unreleased_block():
  with pytest.raises(ValueError, match="Unreleased"):
    notes.promote_unreleased("# Changelog\n\n## [v0.3.0] — 2026-09-13\n",
                             "v0.3.1", "2026-09-20")


# ---------------------------------------------------------------------------
# fallback_notes
# ---------------------------------------------------------------------------
def test_fallback_groups_conventional_commit_types_into_emoji_categories():
  body = notes.fallback_notes([
      {
          "type": "feat",
          "title": "feat(relational): parent-driven fan-out"
      },
      {
          "type": "fix",
          "title": "fix: dtype guard"
      },
  ])

  assert "### 🚀 Added" in body
  assert "### 🐛 Fixed" in body
  assert body.index("### 🚀 Added") < body.index("### 🐛 Fixed")


def test_fallback_strips_the_conventional_commit_prefix_from_each_bullet():
  body = notes.fallback_notes([{
      "type": "feat",
      "title": "feat(relational): fan-out"
  }])

  assert "- fan-out" in body
  assert "feat(relational):" not in body


def test_fallback_skips_the_actions_own_release_report_commits():
  body = notes.fallback_notes([
      {
          "type": "docs",
          "title": "docs(releases): v0.3.0 report [release-report]"
      },
      {
          "type": "feat",
          "title": "feat: fan-out"
      },
  ])

  assert "v0.3.0 report" not in body
  assert "- fan-out" in body


def test_fallback_maps_unlabelled_commit_types_to_changed():
  body = notes.fallback_notes([{"type": "chore", "title": "chore: bump beam"}])

  assert "### 🔧 Changed" in body
  assert "- bump beam" in body


def test_fallback_states_plainly_when_there_are_no_commits():
  assert "No changes recorded" in notes.fallback_notes([])


# ---------------------------------------------------------------------------
# render_body
# ---------------------------------------------------------------------------
def test_render_body_appends_an_absolute_link_to_the_committed_release_report():
  body = notes.render_body("### 🚀 Added\n- Fan-out.", "v0.3.1", "v0.3.0", REPO)

  assert f"https://github.com/{REPO}/blob/v0.3.1/docs/releases/v0.3.1/report.md" in body


def test_render_body_appends_a_compare_link_against_the_previous_tag():
  body = notes.render_body("### 🚀 Added\n- Fan-out.", "v0.3.1", "v0.3.0", REPO)

  assert f"https://github.com/{REPO}/compare/v0.3.0...v0.3.1" in body


def test_render_body_links_the_commit_list_when_there_is_no_previous_tag():
  body = notes.render_body("### 🚀 Added\n- Fan-out.", "v0.1.0", None, REPO)

  assert f"https://github.com/{REPO}/commits/v0.1.0" in body
  assert "/compare/" not in body


def test_render_body_keeps_the_section_text_first_and_verbatim():
  body = notes.render_body("### 🚀 Added\n- Fan-out.", "v0.3.1", "v0.3.0", REPO)

  assert body.startswith("### 🚀 Added\n- Fan-out.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_rewrites_the_changelog_in_place_and_writes_the_notes_file(
    tmp_path):
  changelog = tmp_path / "CHANGELOG.md"
  changelog.write_text(_filled(added=["Fan-out."]), encoding="utf-8")
  out = tmp_path / "notes.md"

  rc = notes.main([
      "--version",
      "v0.3.1",
      "--date",
      "2026-09-20",
      "--prev",
      "v0.3.0",
      "--changelog",
      str(changelog),
      "--out",
      str(out),
      "--repo",
      REPO,
  ])

  assert rc == 0
  assert "## [v0.3.1] — 2026-09-20" in changelog.read_text(encoding="utf-8")
  written = out.read_text(encoding="utf-8")
  assert "- Fan-out." in written
  assert f"https://github.com/{REPO}/compare/v0.3.0...v0.3.1" in written


def test_cli_falls_back_to_commit_titles_when_the_block_was_left_empty(
    tmp_path):
  changelog = tmp_path / "CHANGELOG.md"
  changelog.write_text(_CHANGELOG, encoding="utf-8")
  out = tmp_path / "notes.md"

  rc = notes.main([
      "--version",
      "v0.3.1",
      "--date",
      "2026-09-20",
      "--changelog",
      str(changelog),
      "--out",
      str(out),
      "--repo",
      REPO,
      "--commit-title",
      "feat(relational): fan-out",
  ])

  assert rc == 0
  assert "- fan-out" in out.read_text(encoding="utf-8")


def test_cli_still_promotes_the_changelog_when_it_falls_back(tmp_path):
  changelog = tmp_path / "CHANGELOG.md"
  changelog.write_text(_CHANGELOG, encoding="utf-8")

  notes.main([
      "--version",
      "v0.3.1",
      "--date",
      "2026-09-20",
      "--changelog",
      str(changelog),
      "--out",
      str(tmp_path / "notes.md"),
      "--repo",
      REPO,
      "--commit-title",
      "feat: fan-out",
  ])

  text = changelog.read_text(encoding="utf-8")
  assert "## [v0.3.1] — 2026-09-20" in text
  assert text.index("## [Unreleased]") < text.index("## [v0.3.1]")


# ---------------------------------------------------------------------------
# absolutize_links — relative repo links must survive extraction into a
# GitHub Release body, which has no repo-file context to resolve them against
# ---------------------------------------------------------------------------
def test_absolutize_pins_a_relative_repo_link_at_the_released_tag():
  out = notes.absolutize_links("See [ADR 0036](docs/adr/0036-fanout.md).", REPO,
                               "v0.3.1")

  assert f"https://github.com/{REPO}/blob/v0.3.1/docs/adr/0036-fanout.md" in out


def test_absolutize_leaves_links_that_are_already_absolute_alone():
  text = "See [Beam](https://beam.apache.org) and [arXiv](http://arxiv.org/abs/2210.06280)."

  assert notes.absolutize_links(text, REPO, "v0.3.1") == text


def test_absolutize_leaves_bare_anchors_alone():
  text = "See [the engines](#generation-engines)."

  assert notes.absolutize_links(text, REPO, "v0.3.1") == text


def test_render_body_absolutizes_the_section_it_publishes():
  body = notes.render_body("- See [ADR 0036](docs/adr/0036-fanout.md).",
                           "v0.3.1", "v0.3.0", REPO)

  assert "](docs/adr/" not in body
  assert f"https://github.com/{REPO}/blob/v0.3.1/docs/adr/0036-fanout.md" in body
