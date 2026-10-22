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
"""Read a generator config file from a local path or gs:// (ADR 0043).

Design: docs/DESIGN.md §8 Configuration
(ADR 0043).
"""
from __future__ import annotations

from apache_beam.io.filesystems import FileSystems


def read_config_text(uri: str) -> str:
  """The file's text; any failure surfaces as OSError for the caller."""
  try:
    with FileSystems.open(uri) as f:
      return f.read().decode("utf-8")
  except OSError:
    raise
  except Exception as e:  # pylint: disable=broad-exception-caught  # Beam's filesystems raise their own types; the launcher reports one message
    raise OSError(str(e)) from e
