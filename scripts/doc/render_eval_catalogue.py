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
"""Render the evaluator's metric catalogue into its design document.

The catalogue (`packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/
metrics.yaml`) is the one place a metric is defined. The design document
shows it as tables, between two markers:

    <!-- eval-catalogue:start -->
    … generated: never edited by hand …
    <!-- eval-catalogue:end -->

    uv run python scripts/doc/render_eval_catalogue.py          # rewrite
    uv run python scripts/doc/render_eval_catalogue.py --check  # CI: 1 on drift

The YAML is read directly with PyYAML. The evaluator is a standalone project
with its own lock (ADR 0041); nothing here imports it, so the root
environment does not need it installed.

    exit   meaning
    ────   ──────────────────────────────────────────────────────────────
    0      the block is in step with the catalogue (after a rewrite, or
           found so by --check)
    1      --check: the document differs from what the catalogue renders
    2      nothing could be compared: the catalogue or the document is
           missing or unreadable, or the document does not hold exactly
           one marker pair
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = (
    _REPO_ROOT / "packages" / "sdfb-evaluation" / "src" / "sdfb_evaluation" /
    "catalogue" / "metrics.yaml")
DESIGN_DOC = (
    _REPO_ROOT / "docs" / "designs" /
    "2026-07-07-evaluation-framework-design.md")
START = "<!-- eval-catalogue:start -->"
END = "<!-- eval-catalogue:end -->"
_DASH = "—"
_COLUMNS = ("metric", "family", "kinds", "formula", "estimator", "direction",
            "target", "warn", "fail", "score", "noise floor", "flags")
# A float the catalogue writes as 1.0e-5 reads back as 1e-05.
_PADDED_EXPONENT = re.compile(r"e([+-])0(\d)")


class CatalogueError(ValueError):
  """The catalogue or the document cannot be rendered or compared."""


def _number(value: Any) -> str:
  """A catalogue number as the reader would write it: `1`, `0.99`, `1e-5`."""
  if value is None:
    return _DASH
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise CatalogueError(f"expected a number or null, got {value!r}")
  if float(value).is_integer():
    return str(int(value))
  return _PADDED_EXPONENT.sub(r"e\1\2", f"{value:g}").replace("e+", "e")


def _cell(text: str) -> str:
  """Text that stays inside one table cell."""
  return " ".join(str(text).split()).replace("|", r"\|")


def _flags(metric: Mapping[str, Any]) -> str:
  names = (("n_dependent", "matched n"), ("baseline", "baseline"),
           ("uses_ci_bound", "CI bound"))
  return ", ".join(label for key, label in names if metric[key]) or _DASH


def _target(metric: Mapping[str, Any]) -> str:
  if metric["target"] is None and metric["direction"] == "target":
    return "source value"
  return _number(metric["target"])


def _row(metric: Mapping[str, Any]) -> str:
  thresholds = metric["thresholds"]
  noise = metric["noise_floor"]
  metric_id, title = metric["id"], _cell(metric["title"])
  formula = _cell(metric["formula"])
  cells = (
      f"`{metric_id}`<br/>{title}",
      _cell(metric["family"]),
      ", ".join(metric["kinds"]) or _DASH,
      # $`…`$ is inline maths whose body markdown leaves alone: an
      # underscore or an asterisk in a formula is never read as emphasis.
      f"$`{formula}`$",
      _cell(metric["estimator"]),
      _cell(metric["direction"]),
      _target(metric),
      _number(thresholds["warn"]),
      _number(thresholds["fail"]),
      _cell(metric["score"]),
      _DASH if noise in (None, "none") else _cell(noise),
      _flags(metric),
  )
  return "| " + " | ".join(cells) + " |"


def _table(header: Sequence[str], rows: Sequence[str]) -> list[str]:
  return ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header), *rows]


def _count_table(levels: Sequence[str], families: Sequence[str],
                 metrics: Sequence[Mapping[str, Any]]) -> list[str]:

  def count(level: str | None, family: str | None) -> str:
    total = sum(
        1 for m in metrics
        if level in (None, m["level"]) and family in (None, m["family"]))
    return str(total) if total else _DASH

  rows = [
      "| " + " | ".join(
          [level, *(count(level, f) for f in families),
           count(level, None)]) + " |" for level in levels
  ]
  rows.append("| " + " | ".join(
      ["**total**", *(count(None, f) for f in families),
       count(None, None)]) + " |")
  return _table(("level", *families, "total"), rows)


def render_block(catalogue: Mapping[str, Any]) -> str:
  """The generated text that goes between the markers (markers excluded).

  Raises:
    CatalogueError: a metric names a level or a family the catalogue does
      not declare, so it would be left out of every table.
  """
  levels, families = list(catalogue["levels"]), list(catalogue["families"])
  metrics = list(catalogue["metrics"])
  stray = sorted(m["id"]
                 for m in metrics
                 if m["level"] not in levels or m["family"] not in families)
  if stray:
    raise CatalogueError(
        f"metric(s) {stray} name a level or family the catalogue does not "
        f"declare (levels {levels}, families {families})")
  version = catalogue["catalogue_version"]
  lines = [
      "<!-- Generated by scripts/doc/render_eval_catalogue.py from "
      "packages/sdfb-evaluation/src/sdfb_evaluation/catalogue/metrics.yaml."
      " Do not edit: change the YAML and run the script. -->", "",
      f"Metric catalogue `{version}`: "
      f"{len(metrics)} metrics over {len(levels)} levels and "
      f"{len(families)} families.", "", *_count_table(levels, families, metrics)
  ]
  for level in levels:
    own = [m for m in metrics if m["level"] == level]
    lines += ["", f"#### {level} ({len(own)})", ""]
    lines += _table(_COLUMNS, [_row(m) for m in own])
  return "\n".join(lines)


def replace_block(text: str, block: str) -> str:
  """`text` with `block` between its one marker pair.

  Raises:
    CatalogueError: the markers are missing, repeated or out of order.
  """
  if text.count(START) != 1 or text.count(END) != 1:
    raise CatalogueError(
        f"expected exactly one {START} and one {END} (the eval-catalogue "
        f"markers), found {text.count(START)} and {text.count(END)}")
  head, rest = text.split(START)
  if END not in rest:
    raise CatalogueError(
        f"{END} comes before {START} (the eval-catalogue markers)")
  tail = rest.split(END)[1]
  return f"{head}{START}\n{block}\n{END}{tail}"


def _read(path: Path, what: str) -> str:
  try:
    return path.read_text(encoding="utf-8")
  except OSError as exc:
    raise CatalogueError(f"cannot read {what} {path}: {exc}") from exc


def _load(path: Path) -> Mapping[str, Any]:
  try:
    catalogue = yaml.safe_load(_read(path, "the catalogue"))
  except yaml.YAMLError as exc:
    raise CatalogueError(f"{path} is not valid YAML: {exc}") from exc
  if not isinstance(catalogue, Mapping):
    raise CatalogueError(f"{path}: expected a mapping at the top level")
  return catalogue


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
  parser.add_argument(
      "--check",
      action="store_true",
      help="write nothing; exit 1 when the document differs")
  parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
  parser.add_argument("--doc", type=Path, default=DESIGN_DOC)
  args = parser.parse_args(argv)
  try:
    text = _read(args.doc, "the design document")
    try:
      block = render_block(_load(args.catalogue))
    except (KeyError, TypeError) as exc:
      raise CatalogueError(
          f"{args.catalogue}: a metric lacks a key or holds an unexpected "
          f"type ({exc!r})") from exc
    rendered = replace_block(text, block)
  except CatalogueError as exc:
    print(f"render_eval_catalogue.py: {exc}", file=sys.stderr)
    return 2
  if rendered == text:
    return 0
  if args.check:
    print(
        f"render_eval_catalogue.py: {args.doc} is out of step with "
        f"{args.catalogue}; run `python scripts/doc/render_eval_catalogue.py`"
        " and commit the result",
        file=sys.stderr)
    return 1
  args.doc.write_text(rendered, encoding="utf-8")
  print(f"rewrote the catalogue block of {args.doc}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
