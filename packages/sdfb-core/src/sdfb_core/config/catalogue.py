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
"""The setting catalogue: one strict YAML file of explanations.

`catalogue.yaml` holds the prose for every generator setting — what it is
for, what raising and lowering it does, the tip, the formula, the sources —
and nothing else: values and types live in `model.py`. The loader is strict
in the way the evaluator's metric catalogue is: a missing or unknown key is
a load error, so the file cannot drift silently.

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import functools
import importlib.resources
import math
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from sdfb_core.config.model import (ALL_KEYS, DEFAULT_CONFIG, GeneratorConfig,
                                    value_of)

CACHES = frozenset(
    {"source_stats", "freetext_pools", "rag_chunks", "fk_fanout_stats"})
_STATUS_OF_BLOCK = {
    "settings": "settable",
    "fixed": "fixed",
    "derived": "derived"
}
_TOP_KEYS = frozenset({"catalogue_version", *_STATUS_OF_BLOCK})
_REQUIRED = frozenset({"label", "group", "purpose", "up", "down"})
_OPTIONAL = frozenset(
    {"unit", "tip", "formula", "refs", "adrs", "docs", "shapes_cache"})
_REF_KINDS = frozenset({"paper", "doc", "code"})
_PLACEHOLDER = re.compile(
    r"\{\{(value|tex|dkw_eps|dup_share|dup_share_tex):([a-z_]+\.[a-z0-9_]+)\}\}"
)


class CatalogueError(ValueError):
  """The catalogue file breaks its own contract."""


@dataclass(frozen=True)
class Ref:
  """A source for a setting's explanation: a paper, a doc or a code line."""

  kind: str
  label: str
  url: str = ""
  path: str = ""
  line: int | None = None


@dataclass(frozen=True)
class Entry:
  """One documented setting: its prose, and what it shapes."""

  key: str
  status: str
  label: str
  group: str
  purpose: str
  up: str
  down: str
  unit: str | None = None
  tip: str = ""
  formula: str = ""
  refs: tuple[Ref, ...] = ()
  adrs: tuple[str, ...] = ()
  docs: tuple[str, ...] = ()
  shapes_cache: tuple[str, ...] = ()
  why_fixed: str = ""


@dataclass(frozen=True)
class Catalogue:
  """Every documented setting, keyed by dotted name."""

  version: str
  entries: dict[str, Entry] = field(default_factory=dict)

  @property
  def not_settable(self) -> dict[str, str]:
    """Dotted key → why a config file may not set it."""
    return {
        k: e.why_fixed or "derived from other settings"
        for k, e in self.entries.items()
        if e.status != "settable"
    }

  @property
  def caches_shaped_by(self) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {c: [] for c in sorted(CACHES)}
    for key, entry in self.entries.items():
      for cache in entry.shapes_cache:
        out[cache].append(key)
    return {c: tuple(keys) for c, keys in out.items()}


def _ref(raw: Any, where: str) -> Ref:
  if not isinstance(raw, dict) or raw.get("kind") not in _REF_KINDS:
    raise CatalogueError(f"{where}: a ref needs kind in {sorted(_REF_KINDS)}")
  unknown = set(raw) - {"kind", "label", "url", "path", "line"}
  if unknown:
    raise CatalogueError(f"{where}: unknown ref keys {sorted(unknown)}")
  return Ref(
      kind=raw["kind"],
      label=str(raw["label"]),
      url=raw.get("url", ""),
      path=raw.get("path", ""),
      line=raw.get("line"))


def _entry(key: str, status: str, raw: Any) -> Entry:
  if not isinstance(raw, dict):
    raise CatalogueError(f"{key}: an entry must be a mapping")
  required = _REQUIRED | ({"why_fixed"} if status == "fixed" else set())
  missing = required - set(raw)
  if missing:
    raise CatalogueError(f"{key}: missing {sorted(missing)}")
  unknown = set(raw) - required - _OPTIONAL
  if unknown:
    raise CatalogueError(f"{key}: unknown keys {sorted(unknown)}")
  caches = tuple(raw.get("shapes_cache", ()))
  if set(caches) - CACHES:
    raise CatalogueError(
        f"{key}: shapes_cache names {sorted(set(caches) - CACHES)}; known: "
        f"{sorted(CACHES)}")
  return Entry(
      key=key,
      status=status,
      label=str(raw["label"]),
      group=str(raw["group"]),
      purpose=str(raw["purpose"]),
      up=str(raw["up"]),
      down=str(raw["down"]),
      unit=raw.get("unit"),
      tip=str(raw.get("tip", "")),
      formula=str(raw.get("formula", "")),
      refs=tuple(_ref(r, key) for r in raw.get("refs", ())),
      adrs=tuple(str(a) for a in raw.get("adrs", ())),
      docs=tuple(str(d) for d in raw.get("docs", ())),
      shapes_cache=caches,
      why_fixed=str(raw.get("why_fixed", "")))


def parse_catalogue(text: str) -> Catalogue:
  data = yaml.safe_load(text)
  if not isinstance(data, dict):
    raise CatalogueError("the catalogue must be a mapping")
  unknown = set(data) - _TOP_KEYS
  if unknown:
    raise CatalogueError(f"unknown top-level keys {sorted(unknown)}")
  entries: dict[str, Entry] = {}
  for block, status in _STATUS_OF_BLOCK.items():
    for key, raw in (data.get(block) or {}).items():
      if key in entries:
        raise CatalogueError(f"{key}: documented twice")
      entries[key] = _entry(key, status, raw)
  return Catalogue(
      version=str(data.get("catalogue_version", "")), entries=entries)


@functools.cache
def load_catalogue() -> Catalogue:
  text = (importlib.resources.files("sdfb_core.config") /
          "catalogue.yaml").read_text(encoding="utf-8")
  return parse_catalogue(text)


def _fmt(value: Any) -> str:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return str(value)
  return f"{value:,}" if isinstance(value, int) else f"{value:g}"


def _dup_share(margin: float) -> float:
  return (1 - margin * -math.expm1(-1 / margin)) * 100


def render_text(text: str, config: GeneratorConfig = DEFAULT_CONFIG) -> str:
  """Expand `{{kind:section.key}}` placeholders with this config's values."""

  def expand(match: re.Match[str]) -> str:
    kind, dotted = match.group(1), match.group(2)
    if dotted not in ALL_KEYS:
      raise CatalogueError(f"placeholder names unknown key {dotted!r}")
    value = value_of(config, dotted)
    if kind == "value":
      return _fmt(value)
    if kind == "tex":
      return _fmt(value).replace(",", "{,}")
    if kind == "dkw_eps":
      return f"{math.sqrt(math.log(40) / (2 * value)):.4f}"
    share = _dup_share(float(value))
    return f"{share:.1f} %" if kind == "dup_share" else f"{share:.1f}\\%"

  return _PLACEHOLDER.sub(expand, text)
