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
"""Merge a config file, the typed flags and the defaults into one config.

A key set in the file wins over the same key typed as a flag, which wins over
the model default (ADR 0043: file wins). Every key's provenance is recorded,
and every flag the file overrode is listed so the launcher can say so.

Design: docs/DESIGN.md §4 Relational generation; §8 Configuration
(ADR 0043, 0045).
"""
from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import ValidationError

from sdfb_core.config.catalogue import load_catalogue
from sdfb_core.config.model import (ALL_KEYS, CONFIG_VERSION, DEFAULT_VALUES,
                                    FLAG_KEYS, IDENTITY_KEYS, REQUIRED_FLAGS,
                                    SECTION_TYPES, SECTIONS, GeneratorConfig,
                                    value_of)

PROVENANCE_FILE = "file"
PROVENANCE_FLAG = "flag"
PROVENANCE_DEFAULT = "default"
_DEST_KEYS: dict[str, str] = {**FLAG_KEYS, **IDENTITY_KEYS}


class ConfigError(ValueError):
  """A config the generator refuses, with the offending key named."""


@dataclass(frozen=True)
class Override:
  """A flag typed on the command line that the config file overrode."""
  key: str
  typed: object
  file: object


@dataclass(frozen=True)
class ConfigResolution:
  """The merged config, where each key came from, and what the file overrode."""
  config: GeneratorConfig
  provenance: dict[str, str]
  overridden: tuple[Override, ...]
  config_uri: str = ""


def _closest(word: str, candidates: tuple[str, ...]) -> str:
  close = difflib.get_close_matches(word, list(candidates), n=1)
  return f" — did you mean '{close[0]}'?" if close else ""


def parse_config_text(text: str, *,
                      source: str) -> dict[str, dict[str, object]]:
  """A config file's text → `{section: {key: value}}`, keys validated."""
  try:
    data = yaml.safe_load(text)
  except yaml.YAMLError as e:
    raise ConfigError(f"{source}: not valid YAML — {e}") from e
  if data is None:
    return {}
  if not isinstance(data, dict):
    raise ConfigError(f"{source}: the top level must be a mapping of sections")
  version = data.pop("config_version", CONFIG_VERSION)
  if isinstance(version, bool) or not isinstance(version, int) or version < 1:
    raise ConfigError(
        f"{source}: config_version must be a positive integer, got {version!r}")
  if version > CONFIG_VERSION:
    raise ConfigError(f"{source}: this generator understands config_version "
                      f"<= {CONFIG_VERSION}, got {version}")
  return {
      str(section): _checked_section(source, section, values)
      for section, values in data.items()
  }


def _section_hint(name: str) -> str:
  """For a key written at the top level: the one section that holds it."""
  homes = [s for s, cls in SECTION_TYPES.items() if name in cls.model_fields]
  if len(homes) == 1:
    return (f" — did you mean '{homes[0]}.{name}' (keys live under their "
            "section)?")
  return _closest(name, SECTIONS)


def _checked_section(source: str, section: object,
                     raw: object) -> dict[str, object]:
  """One section's mapping, every key checked against the model."""
  if section not in SECTION_TYPES:
    raise ConfigError(f"{source}: unknown section '{section}'"
                      f"{_section_hint(str(section))}")
  if raw is None:
    return {}
  if not isinstance(raw, dict):
    raise ConfigError(f"{source}: section '{section}' must be a mapping")
  not_settable = load_catalogue().not_settable
  for key in raw:
    dotted = f"{section}.{key}"
    if dotted in IDENTITY_KEYS.values():
      raise ConfigError(
          f"{source}: {dotted} is launch identity — pass it as a flag")
    if dotted in not_settable:
      raise ConfigError(
          f"{source}: {dotted} is not settable — {not_settable[dotted]}")
    if key not in SECTION_TYPES[str(section)].model_fields:
      raise ConfigError(f"{source}: unknown key '{dotted}'"
                        f"{_closest(dotted, ALL_KEYS)}")
  return dict(raw)


def _describe(error: ValidationError, source: str) -> str:
  parts = []
  for item in error.errors():
    loc = ".".join(str(p) for p in item["loc"])
    message = item["msg"]
    # A whole-config rule has no location; its message names its keys.
    parts.append(f"{loc}: {message}" if loc else message)
  where = f"{source}: " if source else ""
  return where + "; ".join(parts)


def resolve(typed: Mapping[str, object],
            file_values: Mapping[str, Mapping[str, object]] | None = None,
            *,
            config_uri: str = "") -> ConfigResolution:
  """Merge: file > typed flag > model default.

  A file's `null` on a key whose generator.yaml default is null (the
  required keys, a derived default) means "not pinned": the reference file
  shows those keys as null, so a block copied from it leaves them to the
  flags.
  """
  file_values = file_values or {}
  merged: dict[str, dict[str, Any]] = {s: {} for s in SECTIONS}
  provenance: dict[str, str] = {}
  for section, values in file_values.items():
    fields = SECTION_TYPES[section].model_fields
    defaults = DEFAULT_VALUES[section]
    for key, value in values.items():
      if value is None and key in fields and defaults.get(key) is None:
        continue
      merged[section][key] = value
      provenance[f"{section}.{key}"] = PROVENANCE_FILE
  for dest, value in typed.items():
    dotted = _DEST_KEYS[dest]
    section, key = dotted.split(".", 1)
    if key in merged[section]:
      continue
    merged[section][key] = value
    provenance[dotted] = PROVENANCE_FLAG
  try:
    config = GeneratorConfig.model_validate({
        s: v for s, v in merged.items() if v
    })
  except ValidationError as e:
    raise ConfigError(_describe(e, config_uri)) from e
  overridden = tuple(
      Override(_DEST_KEYS[dest], typed[dest], value_of(config,
                                                       _DEST_KEYS[dest]))
      for dest in typed
      if provenance[_DEST_KEYS[dest]] == PROVENANCE_FILE and
      value_of(config, _DEST_KEYS[dest]) != typed[dest])
  for dotted in ALL_KEYS:
    provenance.setdefault(dotted, PROVENANCE_DEFAULT)
  return ConfigResolution(
      config=config,
      provenance=dict(sorted(provenance.items())),
      overridden=overridden,
      config_uri=config_uri)


# ADR 0045 — with launch.tables all the model names every table: no
# reference or landing table is read.
_UNUSED_UNDER_ALL = frozenset({"reference_table", "landing_table"})


def missing_required(config: GeneratorConfig) -> list[str]:
  """Required flag dests still unset, in argparse order."""
  skip = _UNUSED_UNDER_ALL if config.launch.tables == "all" else frozenset()
  return [
      dest for dest in REQUIRED_FLAGS
      if dest not in skip and value_of(config, _DEST_KEYS[dest]) is None
  ]


def namespace_values(resolution: ConfigResolution) -> dict[str, object]:
  """Flag dest → resolved value (plus `run_id`), for the argparse namespace."""
  cfg = resolution.config
  return {dest: value_of(cfg, dotted) for dest, dotted in _DEST_KEYS.items()}
