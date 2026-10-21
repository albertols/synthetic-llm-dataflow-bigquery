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
"""The Dataflow job id from the GCE metadata server (ADR 0044): the first
attribute that answers, never an error.

Design: docs/DESIGN.md §7 Validation and the dead-letter queue
(ADR 0044).
"""
from __future__ import annotations

import io
import urllib.error

from sdfb_beam.io import gce_metadata


def _opener(answers: dict[str, bytes | Exception], seen: list):

  def opener(request, timeout):
    seen.append(
        (request.full_url, request.get_header("Metadata-flavor"), timeout))
    name = request.full_url.rsplit("/", 1)[-1]
    answer = answers.get(name, urllib.error.URLError("no route"))
    if isinstance(answer, Exception):
      raise answer
    return io.BytesIO(answer)

  return opener


def test_the_job_id_attribute_answers():
  seen: list = []
  got = gce_metadata.read_job_id(
      opener=_opener({"job_id": b"2026-10-21_01_02_03-123\n"}, seen))
  assert got == ("2026-10-21_01_02_03-123", "metadata:job_id")
  ((url, flavor, timeout),) = seen
  assert url == ("http://metadata.google.internal/computeMetadata/v1/"
                 "instance/attributes/job_id")
  assert flavor == "Google" and timeout == 1.0


def test_the_second_attribute_answers_when_the_first_is_absent():
  seen: list = []
  missing = urllib.error.HTTPError("u", 404, "Not Found", None, None)
  got = gce_metadata.read_job_id(
      opener=_opener({
          "job_id": missing,
          "dataflow_job_id": b"j-2"
      }, seen))
  assert got == ("j-2", "metadata:dataflow_job_id")
  assert len(seen) == 2


def test_no_metadata_server_is_none_never_an_error():
  seen: list = []
  assert gce_metadata.read_job_id(opener=_opener({}, seen)) == (None, "none")
  assert len(seen) == len(gce_metadata.JOB_ID_ATTRIBUTES)


def test_an_empty_answer_is_no_answer():
  seen: list = []
  assert gce_metadata.read_job_id(
      opener=_opener({
          "job_id": b"  ",
          "dataflow_job_id": b""
      }, seen)) == (None, "none")
