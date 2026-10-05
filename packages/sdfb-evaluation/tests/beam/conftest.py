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
"""The runner of every Beam test of this package: in process, never Prism.

A test that builds a pipeline without naming a runner (`TestPipeline()`,
`beam.Pipeline()`) gets Beam's default, `DirectRunner`, which hands a
batch pipeline to Prism wherever the Prism binary is cached or can be
downloaded. Prism starts a step before its batch side input is complete
(`sdfb_evaluation.beam.pipeline`, Runners), and every transform here
reads side inputs: the label key, the panel sets, the parent key sets,
the failure map. So the default runner of the session is `FnApiRunner`,
Beam's in-process Python runner, on which a stage runs to completion
before the stages reading it — the batch contract Dataflow holds too.

Design: docs/designs/2026-07-07-evaluation-framework-design.md
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from apache_beam.options.pipeline_options import StandardOptions

IN_PROCESS_RUNNER = "FnApiRunner"


@pytest.fixture(autouse=True, scope="session")
def in_process_default_runner() -> Iterator[None]:
  """`StandardOptions.DEFAULT_RUNNER` is `FnApiRunner` for the session
  (restored after it)."""
  default = StandardOptions.DEFAULT_RUNNER
  StandardOptions.DEFAULT_RUNNER = IN_PROCESS_RUNNER
  yield
  StandardOptions.DEFAULT_RUNNER = default
