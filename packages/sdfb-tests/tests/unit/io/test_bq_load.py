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
"""`load_json_rows` (ADR 0045 D9): a blocking load waits out a table's rate
limit, each retry a new load job, and never retries a load whose outcome is
unknown.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import pytest
from google.api_core import exceptions
from sdfb_beam.io import bq_load
from sdfb_beam.io.fanout_stats import BigQueryFanoutStatsStore
from sdfb_beam.io.stats_store import BigQuerySourceStatsStore
from sdfb_beam.pools.store import BigQueryFreeTextPoolStore

_RATE = ("Exceeded rate limits: too many table update operations for this "
         "table.")


class _Job:
  """A load job: `result()` raises `error` when it failed."""

  def __init__(self, job_id: str, error: dict | None = None):
    self.job_id = job_id
    self.error_result = error

  def result(self):
    if self.error_result:
      raise exceptions.Forbidden(
          self.error_result["message"], errors=[self.error_result])
    return self


class _Client:
  """`load_table_from_json` answers with `outcomes`, one per call: a job's
  error result (the job fails), None (it succeeds) or an exception (the
  insert is refused). Each call is a new job id, as the real client's."""

  def __init__(self, outcomes):
    self.outcomes = list(outcomes)
    self.jobs: list[str] = []

  def load_table_from_json(self, rows, table, job_config=None):
    del rows, table, job_config
    outcome = self.outcomes.pop(0)
    if isinstance(outcome, Exception):
      raise outcome
    job = _Job(f"job-{len(self.jobs)}", outcome)
    self.jobs.append(job.job_id)
    return job


def _load(client, **kwargs):
  waits: list[float] = []
  bq_load.load_json_rows(
      client, [{
          "a": 1
      }],
      "p.d.t",
      None,
      sleep=waits.append,
      jitter=lambda: 1.0,
      **kwargs)
  return waits


def test_two_rate_limited_jobs_then_a_third_lands():
  rate = {"reason": "rateLimitExceeded", "message": _RATE}
  client = _Client([rate, rate, None])
  assert _load(client) == [2.0, 4.0]
  # Each attempt was a NEW job, never a re-poll of the failed one.
  assert client.jobs == ["job-0", "job-1", "job-2"]


def test_a_refused_insert_and_a_backend_error_are_retried_too():
  backend = {"reason": "backendError", "message": "Backend error"}
  refused = exceptions.Forbidden(
      _RATE, errors=[{
          "reason": "rateLimitExceeded"
      }])
  client = _Client([refused, backend, None])
  assert _load(client) == [2.0, 4.0]
  assert client.jobs == ["job-0", "job-1"]


def test_the_last_attempts_error_is_raised():
  rate = {"reason": "rateLimitExceeded", "message": _RATE}
  client = _Client([rate] * 5)
  with pytest.raises(exceptions.Forbidden, match="Exceeded rate limits"):
    _load(client)
  assert len(client.jobs) == bq_load.LOAD_ATTEMPTS


@pytest.mark.parametrize(
    "error",
    [
        {
            "reason": "invalid",
            "message": "JSON parsing error in row starting at position 0"
        },
        {
            "reason": "accessDenied",
            "message": "Access Denied: Table p:d.t"
        },
    ],
)
def test_any_other_failure_is_raised_at_once(error):
  client = _Client([error])
  with pytest.raises(exceptions.Forbidden):
    _load(client)
  assert client.jobs == ["job-0"]


def test_a_poll_error_on_a_job_that_did_not_fail_is_never_retried():
  """The job may have landed: a retry could append the rows twice."""

  class _LostPoll(_Job):

    def result(self):
      raise exceptions.InternalServerError(
          "Backend error", errors=[{
              "reason": "backendError"
          }])

  class _Client1:
    calls = 0

    def load_table_from_json(self, *_, **__):
      self.calls += 1
      return _LostPoll("job-0")

  client = _Client1()
  with pytest.raises(exceptions.InternalServerError):
    _load(client)
  assert client.calls == 1


def test_the_waits_double_up_to_the_cap_with_jitter():
  assert [bq_load.backoff_seconds(a, jitter=lambda: 1.0) for a in range(1, 7)
         ] == [2.0, 4.0, 8.0, 16.0, 32.0, 32.0]
  assert bq_load.backoff_seconds(3, jitter=lambda: 0.0) == 4.0


@pytest.mark.parametrize("store, write", [
    (BigQueryFreeTextPoolStore, lambda s: s.write_rows([{
        "a": 1
    }])),
    (BigQuerySourceStatsStore, lambda s: s.write_rows([{
        "a": 1
    }])),
    (BigQueryFanoutStatsStore, lambda s: s.put("p.d.src", ("c",), "sha", {})),
])
def test_the_blocking_stores_wait_out_the_rate_limit(store, write, monkeypatch):
  monkeypatch.setattr(bq_load.time, "sleep", lambda _: None)
  rate = {"reason": "rateLimitExceeded", "message": _RATE}
  client = _Client([rate, None])
  write(store("p.d.t", client=client))
  assert client.jobs == ["job-0", "job-1"]
