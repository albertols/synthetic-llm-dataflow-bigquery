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
"""Calling the launcher's `main()` from a test without leaking its log handler.

`run_pipeline.main()` starts with `logging.basicConfig(force=True)`, which
points the root logger at whatever `sys.stderr` is — under pytest, a capture
stream that is closed once the test ends. Beam's job server and SDK worker
threads log after that (at session exit), and every such record then prints
`--- Logging error --- ValueError: I/O operation on closed file`.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator

__all__ = ["restoring_root_handlers"]


@contextlib.contextmanager
def restoring_root_handlers() -> Iterator[None]:
  """Removes, on exit, every root handler the block added. What the block
    logged stays readable from `capsys`."""
  before = list(logging.root.handlers)
  try:
    yield
  finally:
    for handler in logging.root.handlers[:]:
      if handler not in before:
        logging.root.removeHandler(handler)
