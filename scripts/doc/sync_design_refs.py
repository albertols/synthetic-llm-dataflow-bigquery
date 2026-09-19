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
"""Keep each module's `Design:` docstring line in step with docs/DESIGN.md.

Code cites decisions by number (`ADR 0036`). The decision records stay in
this repository and do not ship to the Dataflow Solution Guides, so every
shipped module that cites one names the section of `docs/DESIGN.md` to read:

    Design: docs/DESIGN.md §4 Relational generation; §6 Throughput
    (ADR 0030, 0034, 0036).

The pairing is typed once, in the ADR reference map of that document. This
script derives the lines from it; inline comments are never touched.

    uv run python scripts/doc/sync_design_refs.py          # check
    uv run python scripts/doc/sync_design_refs.py --fix    # rewrite
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_DESIGN_DOC = "docs/DESIGN.md"
_WIDTH = 79
# `ADR 0036`, `ADR 0036/0037`, `ADR-0009`. A comma never continues a citation:
# `ADR 0033, 2026-08-25` is a decision and a date.
_CITATION = re.compile(r"ADR[ -](\d{4}(?:/\d{4})*)")
_MAP_BLOCK = re.compile(r"<!-- adr-map:start -->(.*?)<!-- adr-map:end -->",
                        re.DOTALL)
_MAP_ROW = re.compile(
    r"^\| (\d{4}) \| \[(§\d+ [^\]]+)\]\(#([^)]+)\) \| \[[^\]]*\]\(([^)]+)\) \|$",
    re.MULTILINE)
_DESIGN_PARAGRAPH = re.compile(r"\n\nDesign: docs/DESIGN\.md.*?\)\.\n\Z",
                               re.DOTALL)
_DOCSTRING_OPEN = re.compile(r"[rRuU]?(\"\"\"|''')")
# An ADR mention that is not already the text of a link.
_BARE_ADR = re.compile(r"(?<!\[)ADR (\d{4})(?!\]\()")


class DesignRefError(Exception):
  """A module or the design document cannot be reconciled."""


def _load_sync():
  spec = importlib.util.spec_from_file_location(
      "dsg_sync_for_design_refs", _HERE.parent / "dsg" / "sync.py")
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module  # dataclasses resolve their module by name
  spec.loader.exec_module(module)
  return module


def parse_adr_map(design: str) -> dict[str, str]:
  """ADR number -> section label, from the map in `docs/DESIGN.md`."""
  block = _MAP_BLOCK.search(design)
  if not block:
    raise DesignRefError(f"{_DESIGN_DOC} has no adr-map block")
  return {row[0]: row[1] for row in _MAP_ROW.findall(block.group(1))}


def cited_adrs(text: str) -> list[str]:
  """ADR numbers cited in `text`, outside a generated `Design:` paragraph."""
  text = re.sub(
      r"^Design: docs/DESIGN\.md.*?\)\.$",
      "",
      text,
      flags=re.DOTALL | re.MULTILINE)
  return sorted({
      number for match in _CITATION.findall(text) for number in match.split("/")
  })


def design_line(adrs: Sequence[str], adr_map: Mapping[str, str]) -> str:
  """The docstring paragraph for a module citing `adrs`."""
  missing = [adr for adr in adrs if adr not in adr_map]
  if missing:
    unmapped = ", ".join(missing)
    raise DesignRefError(f"ADR {unmapped} has no row in the {_DESIGN_DOC} map")
  sections = sorted({adr_map[adr] for adr in adrs},
                    key=lambda label: int(label[1:].split()[0]))
  numbers = sorted(adrs)
  return "\n".join([
      _fill(f"Design: {_DESIGN_DOC}",
            [f"{label};" for label in sections[:-1]] + sections[-1:]),
      _fill("(ADR", [f"{n}," for n in numbers[:-1]] + [f"{numbers[-1]})."]),
  ])


def _fill(first: str, pieces: Sequence[str]) -> str:
  """Pack whole `pieces` onto lines of `_WIDTH`; a piece is never split."""
  lines, current = [], first
  for piece in pieces:
    if len(current) + 1 + len(piece) > _WIDTH:
      lines.append(current)
      current = piece
    else:
      current = f"{current} {piece}"
  return "\n".join([*lines, current])


def _offset(lines: Sequence[str], lineno: int, byte_col: int) -> int:
  """Character offset of an `ast` position (its columns count UTF-8 bytes)."""
  line = lines[lineno - 1]
  chars = len(line.encode("utf-8")[:byte_col].decode("utf-8"))
  return sum(len(earlier) for earlier in lines[:lineno - 1]) + chars


def apply(source: str, adr_map: Mapping[str, str]) -> str:
  """`source` with its module docstring's `Design:` paragraph made current."""
  adrs = cited_adrs(source)
  body = ast.parse(source).body
  node = body[0] if body else None
  has_docstring = (
      isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and
      isinstance(node.value.value, str))
  if not has_docstring:
    if adrs:
      raise DesignRefError(
          "cites an ADR but has no module docstring to carry the Design line")
    return source
  lines = source.splitlines(keepends=True)
  start = _offset(lines, node.lineno, node.col_offset)
  end = _offset(lines, node.end_lineno, node.end_col_offset)
  opening = _DOCSTRING_OPEN.match(source, start)
  if not opening:
    raise DesignRefError("module docstring must be triple-quoted")
  quote = opening.group(1)
  text = source[opening.end():end - len(quote)]
  text = _DESIGN_PARAGRAPH.sub("\n", text)
  if adrs:
    text = text.rstrip("\n") + "\n\n" + design_line(adrs, adr_map) + "\n"
  return source[:opening.end()] + text + source[end - len(quote):]


def linkify_design(design: str) -> str:
  """`design` with every prose mention of an ADR linked to its record.

  The decision records do not ship to the Dataflow Solution Guides, and the
  sync points a link whose target does not ship at this repository. A bare
  `ADR 0036` would leave a reader there with nowhere to go. Links come from
  the document's own map; code spans, fences and the map are left as typed.
  """
  block = _MAP_BLOCK.search(design)
  if not block:
    raise DesignRefError(f"{_DESIGN_DOC} has no adr-map block")
  links = {row[0]: row[3] for row in _MAP_ROW.findall(block.group(1))}

  def link(match: re.Match) -> str:
    number = match.group(1)
    if number not in links:
      raise DesignRefError(
          f"ADR {number} is mentioned but has no row in the {_DESIGN_DOC} map")
    return f"[ADR {number}]({links[number]})"

  out, in_fence, in_map = [], False, False
  for line in design.split("\n"):
    prose = not in_fence and not in_map
    if line.lstrip().startswith("```"):
      in_fence, prose = not in_fence, False
    elif "adr-map:start" in line:
      in_map, prose = True, False
    elif "adr-map:end" in line:
      in_map = False
    # Odd segments are code spans: `ADR 0036` there shows a code comment.
    out.append("".join(
        segment if i % 2 else _BARE_ADR.sub(link, segment)
        for i, segment in enumerate(re.split(r"(`[^`]*`)", line)
                                   )) if prose else line)
  return "\n".join(out)


def _slug(heading: str) -> str:
  return re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")


def _shipped(root: Path) -> list[str]:
  """Repo-relative paths of the files the DSG copy ships."""
  sync = _load_sync()
  manifest = sync.Manifest.load(root / "dsg" / "manifest.yaml")
  tracked = [
      rel for rel in subprocess.run(
          ["git", "-C", str(root), "ls-files", "-z"],
          capture_output=True,
          text=True,
          check=True).stdout.split("\0") if rel and (root / rel).is_file()
  ]
  overlays = tuple(prefix + "/" for prefix in manifest.overlays)
  return sorted({
      *sync.select_files(root, manifest, files=tracked),
      *(rel for rel in tracked if rel.startswith(overlays))
  })


def _is_text(path: Path) -> bool:
  return path.suffix not in {".png", ".drawio", ".lock", ".json"}


def _reconcile_design(root: Path, *, write: bool) -> tuple[str, list[str]]:
  """The design document's own consistency; returns its text and findings."""
  design_path = root / _DESIGN_DOC
  design = design_path.read_text(encoding="utf-8")
  findings = []
  try:
    linked = linkify_design(design)
    if linked != design and write:
      design_path.write_text(linked, encoding="utf-8")
      design = linked
    elif linked != design:
      findings.append(f"{_DESIGN_DOC}: an ADR is mentioned without a link "
                      "to its record (run --fix)")
  except DesignRefError as error:
    findings.append(f"{_DESIGN_DOC}: {error}")
  anchors = {_slug(h) for h in re.findall(r"^#{2,6} (.+)$", design, re.M)}
  block = _MAP_BLOCK.search(design)
  for number, _, anchor, link in _MAP_ROW.findall(block.group(1)):
    if anchor not in anchors:
      findings.append(f"{_DESIGN_DOC}: ADR {number} points at #{anchor}, "
                      "which is not a heading")
    if not (design_path.parent / link).is_file():
      findings.append(f"{_DESIGN_DOC}: ADR {number} links {link}, "
                      "which does not exist")
  return design, findings


def _reconcile(root: Path, *, write: bool) -> list[str]:
  design, findings = _reconcile_design(root, write=write)
  adr_map = parse_adr_map(design)
  for rel in _shipped(root):
    path = root / rel
    if rel == _DESIGN_DOC or not _is_text(path):
      continue
    source = path.read_text(encoding="utf-8")
    try:
      if rel.endswith(".py"):
        current = apply(source, adr_map)
        if current != source:
          if write:
            path.write_text(current, encoding="utf-8")
          else:
            findings.append(f"{rel}: Design line is out of date (run --fix)")
      elif cited_adrs(source):
        # No docstring to carry a line: the map must still cover the ADRs.
        design_line(cited_adrs(source), adr_map)
    except DesignRefError as error:
      findings.append(f"{rel}: {error}")
  return findings


def check(root: Path) -> list[str]:
  """What is out of step, one line per finding; nothing is written."""
  return _reconcile(root, write=False)


def fix(root: Path) -> list[str]:
  """Rewrite stale `Design:` lines and link bare ADR mentions in the design
  document; return what a person still has to fix."""
  return _reconcile(root, write=True)


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--root", type=Path, default=_REPO_ROOT)
  parser.add_argument("--fix", action="store_true")
  args = parser.parse_args(argv)
  findings = fix(args.root) if args.fix else check(args.root)
  for finding in findings:
    print(finding)
  return 1 if findings else 0


if __name__ == "__main__":
  sys.exit(main())
