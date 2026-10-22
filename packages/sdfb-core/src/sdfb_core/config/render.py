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
"""Render config/generator.yaml: every key, its default and its explanation.

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import textwrap

import yaml

from sdfb_core.config.catalogue import load_catalogue, render_text
from sdfb_core.config.model import (CONFIG_VERSION, DEFAULT_CONFIG, FLAG_KEYS,
                                    IDENTITY_KEYS, REQUIRED_FLAGS,
                                    SECTION_TYPES)

_HEADER = """\
# Generator configuration — REFERENCE. Rendered from sdfb_core/config by
# scripts/doc/render_generator_config.py; do not edit by hand.
#
# Copy ONLY the keys you want to pin into your own file and pass it with
# --config_uri (local path or gs://). Keys in that file override the same
# flags and DAG params; flags fill the keys it leaves out (ADR 0043).
# Passing this whole file pins every key and makes every flag a no-op.
# A file of your own starts with `config_version: 1`.
#
# How to read an entry:
#   flag --x   the key is also a CLI / Flex / Composer flag; keys without
#              it can be set only in a file.
#   raise/lower  what a higher / lower value does.
#   null       either `required` (the entry says so: a launch needs a
#              value from a flag or a file; a null left in your file pins
#              nothing, so a flag still fills it) or `derived` (the entry
#              says `null = ...`: leave it null and the value is computed).
#              On a key with a value, `null = ...` says what a null in
#              your file means instead (vllm_max_model_len: no cap).
#   Quoted values ('on', 'false', '8192', '') are string-encoded CLI
#   values: keep them quoted.
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
      f"{indent}# {line}"
      for line in textwrap.wrap(text, 75 - len(indent), break_on_hyphens=False)
  ]


def _value(value: object) -> str:
  """One YAML flow value; strings are quoted so YAML 1.1 never reinterprets."""
  if isinstance(value, tuple):
    value = list(value)
  text = yaml.safe_dump(value, default_flow_style=True, width=10**6)
  return str(text.strip().removesuffix("...").strip())


def render_reference() -> str:
  """The full text of the reference file (without the licence header)."""
  cat = load_catalogue()
  out = [_HEADER, f"config_version: {CONFIG_VERSION}", ""]
  for section, cls in SECTION_TYPES.items():
    out += [_RULE, f"# {_SECTION_TITLES[section]}", _RULE, f"{section}:"]
    for key in cls.model_fields:
      dotted = f"{section}.{key}"
      entry = cat.entries[dotted]
      out += _comment(f"{entry.label} — {render_text(entry.purpose)}", "  ")
      out += _comment(f"raise: {render_text(entry.up)}", "  ")
      out += _comment(f"lower: {render_text(entry.down)}", "  ")
      where = []
      if key in FLAG_KEYS or key in IDENTITY_KEYS:
        where.append(f"flag --{key}")
      if entry.adrs:
        where.append("ADR " + ", ".join(entry.adrs))
      if key in REQUIRED_FLAGS:
        where.append("required")
      if where:
        out += _comment(" · ".join(where), "  ")
      if entry.null_means:
        out += _comment(f"null = {entry.null_means}", "  ")
      line = f"{key}: {_value(getattr(getattr(DEFAULT_CONFIG, section), key))}"
      out.append(f"  # {line}" if key in IDENTITY_KEYS else f"  {line}")
      out.append("")
  return "\n".join(out).rstrip() + "\n"
