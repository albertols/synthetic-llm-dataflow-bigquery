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
"""Settings salts: a cache built under one value is never served under another.

Four BigQuery caches persist work across runs. Their keys were built from
digests and model ids only, because the values that shape them were fixed
constants. Now that a config file can change those values, each cache key
gains a suffix — empty at the defaults, so every existing key is unchanged (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

import hashlib
import json

from sdfb_core.config.catalogue import load_catalogue
from sdfb_core.config.model import DEFAULT_CONFIG, GeneratorConfig, value_of


def settings_salt(config: GeneratorConfig, cache: str) -> str:
  changed = {}
  for dotted in load_catalogue().caches_shaped_by[cache]:
    value = value_of(config, dotted)
    if value != value_of(DEFAULT_CONFIG, dotted):
      changed[dotted] = list(value) if isinstance(value, tuple) else value
  if not changed:
    return ""
  blob = json.dumps(changed, sort_keys=True, separators=(",", ":"))
  return hashlib.sha256(blob.encode()).hexdigest()[:12]


def salted(key: str, salt: str) -> str:
  return f"{key}+cfg.{salt}" if salt else key
