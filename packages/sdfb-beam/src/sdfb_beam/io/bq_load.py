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
"""A blocking BigQuery load job that waits out a table's rate limit
(ADR 0045 D9).

BigQuery allows about five table-update operations per ten seconds per
table. The stores that append outside Beam's sinks — `freetext_pools` (a
blocking load inside `BuildFreeTextPoolsDoFn`), the launcher-side
`source_table_stats` and `fanout_stats` writes — load one small job each,
so several batch jobs writing the same table at once can trip it: the
2026-11-03 launch lost four batch jobs to `rateLimitExceeded` on a shared
table.

`load_json_rows` retries only what BigQuery says may pass later
(`RETRIED_REASONS`), with exponential back-off and jitter, and each retry
is a NEW load job (the client mints a fresh job id), never a re-poll of the
failed one. It retries only when no row can have landed: the job insert was
refused, or the job itself finished with a retried reason as its error (a
failed load job commits nothing). An error from polling a job whose
outcome is unknown is raised, never retried: a retry could append the rows
twice.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sdfb_core.observability import log_milestone

# The error reasons a later attempt may pass: the table's update rate and a
# BigQuery backend error.
RETRIED_REASONS = frozenset({"rateLimitExceeded", "backendError"})
LOAD_ATTEMPTS = 5
FIRST_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 32.0


def error_reason(error: Any) -> str | None:
  """The retried reason an API error or a job's `error_result` names, if
  any: a `google.api_core` exception's `errors`, an `errorResult`-shaped
  mapping, or (last) its text."""
  if isinstance(error, Mapping):
    entries: Sequence[Any] = [error]
  else:
    entries = getattr(error, "errors", None) or []
  for entry in entries:
    reason = entry.get("reason") if isinstance(entry, Mapping) else None
    if reason in RETRIED_REASONS:
      return str(reason)
  text = str(error)
  return next((r for r in sorted(RETRIED_REASONS) if r in text), None)


def backoff_seconds(attempt: int,
                    *,
                    jitter: Callable[[], float] = random.random) -> float:
  """The wait after failed attempt `attempt` (1-based): 2, 4, 8, 16 … s,
  capped at 32, each scaled into [50 %, 100 %] by `jitter` so the jobs
  that collided do not retry in step."""
  delay = min(MAX_DELAY_SECONDS, FIRST_DELAY_SECONDS * 2**(attempt - 1))
  return delay * (0.5 + 0.5 * jitter())


def load_json_rows(client: Any,
                   rows: list[dict[str, Any]],
                   table: str,
                   job_config: Any,
                   *,
                   attempts: int = LOAD_ATTEMPTS,
                   sleep: Callable[[float], None] = time.sleep,
                   jitter: Callable[[], float] = random.random) -> None:
  """`client.load_table_from_json(rows, table, job_config=…).result()`,
  attempted up to `attempts` times while it fails with a retried reason
  and no row can have landed (module docstring); any other error, or the
  last attempt's, is raised as it is."""
  for attempt in range(1, attempts + 1):
    try:
      job = client.load_table_from_json(rows, table, job_config=job_config)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # re-raised unless the insert was refused with a retried reason
      reason = error_reason(exc)
      if reason is None or attempt == attempts:
        raise
    else:
      try:
        job.result()
        return
      except Exception:  # pylint: disable=broad-exception-caught  # re-raised unless the job itself failed with a retried reason
        failed = getattr(job, "error_result", None)
        reason = error_reason(failed) if failed else None
        if reason is None or attempt == attempts:
          raise
    delay = backoff_seconds(attempt, jitter=jitter)
    log_milestone(
        "bq_load_backoff",
        level=logging.WARNING,
        table=table,
        reason=reason,
        attempt=attempt,
        attempts=attempts,
        wait_s=round(delay, 1))
    sleep(delay)


__all__ = [
    "FIRST_DELAY_SECONDS", "LOAD_ATTEMPTS", "MAX_DELAY_SECONDS",
    "RETRIED_REASONS", "backoff_seconds", "error_reason", "load_json_rows"
]
