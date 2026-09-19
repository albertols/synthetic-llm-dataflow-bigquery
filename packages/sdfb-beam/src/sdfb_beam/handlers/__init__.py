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
"""`ModelClient` / `ModelHandler` implementations.

Production (Linux + L4 + CUDA):
  - `vllm_client.py` — vLLM-backed (stub today; real impl in M1 §9).

M4 local smoke (Apple Silicon):
  - `mlx_client.py` — `mlx-lm`-backed (gated by `[mlx]` extra).

Test / development:
  - `fake_client.py` — deterministic `ModelClient` for CI and DirectRunner.

All three satisfy the `ModelClient` Protocol; engines never know which one
is in use. See ADR 0006 and ADR 0010.
"""

from sdfb_beam.handlers.fake_client import FakeModelClient

# vllm_client and mlx_client are intentionally NOT imported at package level —
# they have heavy / platform-specific runtime deps. Import them only at the
# call site (or via the factory in `sdfb_beam.cli.run_pipeline`).

__all__ = ["FakeModelClient"]
