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
"""The Dataflow job id, read on a worker from the GCE metadata server.

The generation record's `job_id` (ADR 0044). Each attribute is tried once
with a one-second timeout; nothing here raises — a worker that cannot read
its job id records NULL, and readers match the job's name instead.

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import urllib.request
from collections.abc import Callable
from typing import Any

METADATA_URL = ("http://metadata.google.internal/computeMetadata/v1/"
                "instance/attributes/{}")
# `job_id` per the design; `dataflow_job_id` in case the worker VM names it
# so (the attribute is to be confirmed on the first Dataflow run).
JOB_ID_ATTRIBUTES = ("job_id", "dataflow_job_id")
_NONE = "none"


def read_job_id(
    timeout: float = 1.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[str | None, str]:
  """(job id, where it came from): `metadata:<attribute>`, or `none`."""
  for name in JOB_ID_ATTRIBUTES:
    request = urllib.request.Request(
        METADATA_URL.format(name), headers={"Metadata-Flavor": "Google"})
    try:
      with opener(request, timeout=timeout) as response:
        value = response.read().decode("utf-8").strip()
    except (OSError, ValueError):
      continue
    if value:
      return value, f"metadata:{name}"
  return None, _NONE
