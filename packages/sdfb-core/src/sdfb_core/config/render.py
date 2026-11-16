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
"""Render the comments of generator.yaml around the values it holds.

The file's value lines are the generator's defaults (`defaults.py`); this
module writes every key's explanation around the values it is given and never
chooses a value itself (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import re
import textwrap
from collections.abc import Mapping

import yaml

from sdfb_core.config.catalogue import load_catalogue, render_text
from sdfb_core.config.model import (CONFIG_VERSION, FLAG_KEYS, IDENTITY_KEYS,
                                    REQUIRED_FLAGS, SECTION_TYPES)

_HEADER = """\
# Generator configuration: the DEFAULTS. Every value line is the default the
# generator loads. Precedence: this file < composer/sdfb_env.yaml < DAG
# trigger values < your --config_uri file (keys it omits fall back).
# Copy only the keys you want to pin; a file of your own starts with
# `config_version: 1`. Comments are rendered from sdfb_core/config/
# catalogue.yaml by scripts/doc/render_generator_config.py --fix (never
# touches a value).
#
#   [--x · ADR n · required]  the CLI / Flex / Composer flag, its ADR, and
#                             whether a launch needs a value
#   ↑ / ↓                     what raising / lowering the value does
#   null = ...                what a null means (otherwise `required`)
#   'on', 'false', '8192'     quoted: string-encoded CLI values, keep them
"""

_RULE = "# " + "=" * 73

_SECTION_TITLES = {
    "io": "IO — where a launch reads from and writes to",
    "launch": "LAUNCH — which tables, how many jobs, and the launch identity",
    "sampling": "SAMPLING — the reference sample and the source-stats profiler",
    "generation": "GENERATION — what is generated, and how close to the source",
    "free_text": "FREE TEXT — pools, routing and expansion",
    "rag": "RAG — the embedder, the chunk store and the exemplar pick",
    "guardrails": "GUARDRAILS — the gate and the landing write mode",
    "relational": "RELATIONAL — keys, edges and FK key samples",
    "serving": "SERVING — the model, vLLM and the worker fleet",
}


def _comment(text: str, indent: str) -> list[str]:
  return [
      f"{indent}# {line}".replace("\u00a0", " ")
      for line in textwrap.wrap(text, 75 - len(indent), break_on_hyphens=False)
  ]


def _value(value: object) -> str:
  """One YAML flow value; strings are quoted so YAML 1.1 never reinterprets."""
  if isinstance(value, tuple):
    value = list(value)
  text = yaml.safe_dump(value, default_flow_style=True, width=10**6)
  return str(text.strip().removesuffix("...").strip())


def render_reference(values: Mapping[str, Mapping[str, object]]) -> str:
  """The full text of generator.yaml (without the licence header): the
  catalogue's comments around the given `{section: {key: value}}` values.
  Launch identity has no value line; it is shown commented out as null."""
  cat = load_catalogue()
  out = [_HEADER, f"config_version: {CONFIG_VERSION}", ""]
  for section, cls in SECTION_TYPES.items():
    out += [_RULE, f"# {_SECTION_TITLES[section]}", _RULE, f"{section}:"]
    for key in cls.model_fields:
      dotted = f"{section}.{key}"
      entry = cat.entries[dotted]
      where = []
      if key in FLAG_KEYS or key in IDENTITY_KEYS:
        where.append(f"--{key}")
      if entry.adrs:
        where.append("ADR\u00a0" + ", ".join(entry.adrs))
      if key in REQUIRED_FLAGS:
        where.append("required")
      meta = " [" + " · ".join(where) + "]" if where else ""
      # A non-breaking space keeps "ADR n" on one line while wrapping.
      head = f"{entry.label} — {render_text(entry.purpose)}{meta}"
      out += _comment(head, "  ")
      out += _comment(
          f"↑ {render_text(entry.up)} · ↓ {render_text(entry.down)}", "  ")
      if entry.null_means:
        out += _comment(f"null = {entry.null_means}", "  ")
      if key in IDENTITY_KEYS:
        out.append(f"  # {key}: null")
      else:
        out.append(f"  {key}: {_value(values[section][key])}")
      out.append("")
  return "\n".join(out).rstrip() + "\n"


_VALUE_LINE = re.compile(r"^  (\w+):")
_IDENTITY_LINE = re.compile(r"^  # (\w+): null$")
_SECTION_LINE = re.compile(r"^(\w+):")


def _key_of(line: str) -> str | None:
  """The key a rendered value line (or commented identity line) carries."""
  if match := _VALUE_LINE.match(line):
    return match.group(1)
  if (match := _IDENTITY_LINE.match(line)) and match.group(1) in IDENTITY_KEYS:
    return match.group(1)
  return None


def first_stale_key(actual: str, expected: str) -> str | None:
  """The dotted key whose rendered entry is the first to differ, `header`
  when the difference comes before the first section, `end of file` after
  the last key, None when the texts are equal."""
  if actual == expected:
    return None
  got, want = actual.splitlines(), expected.splitlines()
  first = next(
      (i for i, (a, b) in enumerate(zip(got, want, strict=False)) if a != b),
      min(len(got), len(want)))
  section = None
  for index, line in enumerate(want):
    if match := _SECTION_LINE.match(line):
      section = match.group(1) if match.group(1) in SECTION_TYPES else None
    if index < first:
      continue
    if section is None:
      return "header"
    if key := _key_of(line):
      return f"{section}.{key}"
  return "end of file"
