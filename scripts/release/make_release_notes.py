#!/usr/bin/env python
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
"""Release-notes generator: promotes the `CHANGELOG.md` `[Unreleased]` block
into a dated release section and renders the GitHub Release body from it.

Companion to `make_release_report.py` — that script owns the *measured* half
of a release (metric deltas, charts, `docs/releases/<version>/report.md`);
this one owns the *narrative* half (what was added, changed, removed), which
no artifact can derive because squash-merges leave one commit subject per
release.

Usage (see `main()` / `--help` for the full flag set):

    # invoked by .github/workflows/release_tag_report.yaml
    python scripts/release/make_release_notes.py \
        --version vX.Y.Z --date YYYY-MM-DD [--prev vW.Y.Z] \
        --changelog CHANGELOG.md --head-ref <sha> --out "$RUNNER_TEMP/notes.md"

The convention it enforces:

  * `CHANGELOG.md` carries exactly one permanent `## [Unreleased]` block
    holding the six `CATEGORY_ORDER` headings. Work in progress writes bullets
    under them; nobody has to know the next version number in advance (the
    Action computes it from the merge title only *after* the merge lands).
  * At tag time the block is renamed `## [<tag>] — <date>`, empty categories
    are dropped, and a fresh empty `[Unreleased]` is re-opened above it.
  * The promoted body — and nothing else — becomes the GitHub Release body, so
    the file on disk and the notes on the releases page cannot drift.
  * A block nobody filled in promotes to an empty body; the CLI then renders
    conventional-commit subjects instead, so a release is never published
    noteless (and the empty section on disk stays as the honest record).
"""

# f-string fields keep single quotes: yapf 0.43 cannot parse PEP 701
# quote reuse, and pylint reads those quotes as inconsistent.
# pylint: disable=inconsistent-quotes

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Keep a Changelog categories, scio-style emoji headings. Document order:
# what a reader of a release wants first (new capability) down to what they
# want last (docs). Referenced by name in `CHANGELOG.md`'s own preamble.
CATEGORY_ORDER = (
    "🚀 Added",
    "🔧 Changed",
    "⚡ Performance",
    "🐛 Fixed",
    "🗑️ Removed",
    "📗 Docs",
)

UNRELEASED_HEADING = "## [Unreleased]"

# The Action's own report commits (`[release-report]`) are bookkeeping, not
# release content — see `.github/workflows/release_tag_report.yaml`.
_REPORT_COMMIT_MARKER = "[release-report]"

_UNRELEASED_RE = re.compile(r"^## \[Unreleased\][^\n]*$", re.MULTILINE)
_ANY_H2_RE = re.compile(r"^## ", re.MULTILINE)
_CATEGORY_RE = re.compile(r"^### (.+)$", re.MULTILINE)
_CONVENTIONAL_PREFIX_RE = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s*")

_TYPE_TO_CATEGORY = {
    "feat": "🚀 Added",
    "fix": "🐛 Fixed",
    "perf": "⚡ Performance",
    "docs": "📗 Docs",
}
_DEFAULT_CATEGORY = "🔧 Changed"

_DEFAULT_REPO = "albertols/synthetic-llm-dataflow-bigquery"


# --------------------------------------------------------------------------
# promote_unreleased — the CHANGELOG rewrite, pure string in / string out
# --------------------------------------------------------------------------
def _split_categories(body: str) -> list[tuple[str | None, list[str]]]:
  """`body` as `(category heading or None, content lines)` chunks, in
    document order. Lines before the first `###` belong to a `None` chunk so
    free prose written straight under `[Unreleased]` is never silently lost."""
  chunks: list[tuple[str | None, list[str]]] = [(None, [])]
  for line in body.splitlines():
    m = _CATEGORY_RE.match(line)
    if m:
      chunks.append((m.group(1).strip(), []))
    else:
      chunks[-1][1].append(line)
  return chunks


def _drop_empty_categories(body: str) -> str:
  """`body` with every category heading that carries no content removed. A
    heading survives only if some line under it is non-blank."""
  rendered: list[str] = []
  for heading, lines in _split_categories(body):
    content = [line for line in lines if line.strip()]
    if not content:
      continue
    rendered.append(
        "\n".join([f"### {heading}", *content] if heading else content))
  return "\n\n".join(rendered).strip()


def _fresh_unreleased_block() -> str:
  headings = "\n\n".join(f"### {category}" for category in CATEGORY_ORDER)
  return f"{UNRELEASED_HEADING}\n\n{headings}\n\n"


def promote_unreleased(text: str, version: str, date: str) -> tuple[str, str]:
  """Promote the `[Unreleased]` block of `text` to a `version`/`date`
    section.

    Returns `(rewritten changelog, promoted section body)`. The body carries
    no `##` heading — it is exactly what belongs in a GitHub Release — and is
    `""` when the block held nothing, which is the caller's signal to fall
    back to `fallback_notes`.

    Raises `ValueError` when there is no `[Unreleased]` block: that is a
    corrupted changelog, not a quiet edge case, and the release Action must
    surface it rather than publish a section that silently went missing.
    """
  match = _UNRELEASED_RE.search(text)
  if not match:
    raise ValueError(
        f"{UNRELEASED_HEADING!r} block not found — cannot promote a release section."
    )

  head, rest = text[:match.start()], text[match.end():]
  next_h2 = _ANY_H2_RE.search(rest)
  body, tail = (rest[:next_h2.start()],
                rest[next_h2.start():]) if next_h2 else (rest, "")

  section = _drop_empty_categories(body)
  heading = f"## [{version}] — {date}"
  promoted = f"{heading}\n\n{section}\n\n" if section else f"{heading}\n\n"
  return f"{head}{_fresh_unreleased_block()}{promoted}{tail}", section


# --------------------------------------------------------------------------
# fallback_notes — conventional-commit subjects, when nobody wrote prose
# --------------------------------------------------------------------------
def fallback_notes(change_log: list[dict]) -> str:
  """Conventional-commit subjects grouped into `CATEGORY_ORDER` sections.

    Deliberately thin: with squash-merges this is one or two bullets. It
    exists so a release is never noteless, not as a substitute for the
    hand-written `[Unreleased]` block.
    """
  grouped: dict[str, list[str]] = {}
  for entry in change_log:
    title = (entry.get("title") or "").strip()
    if not title or _REPORT_COMMIT_MARKER in title:
      continue
    category = _TYPE_TO_CATEGORY.get(entry.get("type") or "", _DEFAULT_CATEGORY)
    grouped.setdefault(category,
                       []).append(_CONVENTIONAL_PREFIX_RE.sub("", title))

  if not grouped:
    return "_No changes recorded for this release._"

  sections = [
      "\n".join(
          [f"### {category}", *(f"- {title}"
                                for title in grouped[category])])
      for category in CATEGORY_ORDER
      if category in grouped
  ]
  return "\n\n".join(sections)


# --------------------------------------------------------------------------
# render_body — the published GitHub Release body
# --------------------------------------------------------------------------
# A markdown link whose target is neither absolute, an anchor, nor a mail
# address — i.e. a path into this repo.
_RELATIVE_LINK_RE = re.compile(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)")


def absolutize_links(text: str, repo: str, ref: str) -> str:
  """Repo-relative markdown links rewritten to absolute `blob/<ref>` URLs.

    `CHANGELOG.md` is read both as a repo file (where `docs/adr/…` resolves)
    and as a GitHub Release body (where it does not — a release has no file
    context). Pinning at `ref` rather than a branch also keeps a published
    release pointing at the docs as they stood when it shipped.
    """
  return _RELATIVE_LINK_RE.sub(
      lambda m:
      f"](https://github.com/{repo}/blob/{ref}/{m.group(1).lstrip('/')})", text)


def render_body(section: str, version: str, prev: str | None, repo: str) -> str:
  """`section` verbatim, then the evidence footer: the committed release
    report (pinned at this tag, so the link never rots) and the full commit
    range — `compare/` against `prev`, or the tag's commit list for the very
    first release."""
  section = absolutize_links(section, repo, version)
  report = f"https://github.com/{repo}/blob/{version}/docs/releases/{version}/report.md"
  if prev:
    commits = f"[**Full changelog**](https://github.com/{repo}/compare/{prev}...{version})"
  else:
    commits = f"[**All commits**](https://github.com/{repo}/commits/{version})"
  return f"{section.strip()}\n\n---\n\n📊 [**Release report**]({report}) · {commits}\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _change_log_entry(title: str) -> dict:
  m = re.match(r"^(?P<type>[a-z]+)(\([^)]*\))?!?:", title.strip())
  return {"type": m.group("type") if m else "other", "title": title.strip()}


def _git_subjects(prev: str | None, head_ref: str) -> list[str]:
  """Commit subjects in `prev..head_ref` (or just `head_ref` for the first
    release). Any git failure degrades to `[]` — a release must not die
    because the fallback path could not read history."""
  rng = f"{prev}..{head_ref}" if prev else head_ref
  try:
    out = subprocess.run(
        ["git", "log", "--format=%s", rng],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
  except (subprocess.CalledProcessError, OSError) as exc:
    print(f"warning: could not read git log for {rng}: {exc}", file=sys.stderr)
    return []
  return [line for line in out.splitlines() if line.strip()]


def _build_arg_parser() -> argparse.ArgumentParser:
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument(
      "--version", required=True, help="tag being released, e.g. v0.3.1")
  ap.add_argument("--date", required=True, help="release date, YYYY-MM-DD")
  ap.add_argument(
      "--prev", default=None, help="previous tag, for the compare link")
  ap.add_argument(
      "--changelog",
      default="CHANGELOG.md",
      help="changelog to promote in place")
  ap.add_argument(
      "--out",
      default=None,
      help="write the release body here (default: stdout)")
  ap.add_argument(
      "--repo", default=_DEFAULT_REPO, help="owner/name, for absolute links")
  ap.add_argument(
      "--head-ref", default=None, help="head sha/ref for the commit fallback")
  ap.add_argument(
      "--commit-title",
      action="append",
      default=None,
      help="explicit commit subject for the fallback (repeatable; skips git)",
  )
  return ap


def main(argv: list[str] | None = None) -> int:
  args = _build_arg_parser().parse_args(argv)

  changelog = Path(args.changelog)
  text, section = promote_unreleased(
      changelog.read_text(encoding="utf-8"), args.version, args.date)
  changelog.write_text(text, encoding="utf-8")

  if not section:
    print(
        f"note: {UNRELEASED_HEADING} was empty; rendering notes from commit subjects.",
        file=sys.stderr,
    )
    subjects = args.commit_title
    if subjects is None:
      subjects = _git_subjects(args.prev,
                               args.head_ref) if args.head_ref else []
    section = fallback_notes([_change_log_entry(s) for s in subjects])

  body = render_body(section, args.version, args.prev, args.repo)
  if args.out:
    Path(args.out).write_text(body, encoding="utf-8")
  else:
    print(body)
  return 0


if __name__ == "__main__":  # pragma: no cover
  raise SystemExit(main())
