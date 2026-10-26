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
"""The generator's defaults: the value lines of `generator.yaml`.

The file ships inside the sdfb-core package (`config/generator.yaml` at the
repository root is a symlink to it), so the image, the wheel and the DSG copy
all carry the one file. `model.py` imports this module, so it imports nothing
from sdfb_core: only the standard library and PyYAML.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

CONFIG_VERSION = 1

GENERATOR_YAML = Path(__file__).resolve().parent / "generator.yaml"


def load_defaults(path: Path = GENERATOR_YAML) -> dict[str, dict[str, object]]:
  """The file's values as `{section: {key: value}}`, config_version checked.

  The launch-identity keys are commented out in the file, so they are not
  in the mapping.
  """
  data = yaml.safe_load(path.read_text(encoding="utf-8"))
  if not isinstance(data, dict):
    raise ValueError(f"{path}: the top level must be a mapping of sections")
  version = data.pop("config_version", None)
  if version != CONFIG_VERSION or isinstance(version, bool):
    raise ValueError(f"{path}: config_version must be {CONFIG_VERSION}, "
                     f"got {version!r}")
  out: dict[str, dict[str, object]] = {}
  for section, values in data.items():
    if not isinstance(values, dict):
      raise ValueError(f"{path}: section '{section}' must be a mapping")
    out[str(section)] = {str(k): v for k, v in values.items()}
  return out


def defaults_digest(path: Path = GENERATOR_YAML) -> str:
  """The first 12 hex characters of the sha256 of the file's bytes."""
  return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
