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
"""Generated relationships models for the whole-model launch tests
(ADR 0045): forests of isolated tables, with optional chains (components).

`forest_model_text()` is the spec's 120-table forest — 112 enabled
isolated tables `T_0000…T_0111` and 8 disabled ones — and `chains=(4, 3)`
adds one parents-first chain per entry (`C0_0 ← C0_1 ← …`). `reverse=True`
declares the same content in the opposite order: the planner must not care.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

from collections.abc import Sequence


def _blocks(enabled: int, disabled: int,
            chains: Sequence[int]) -> list[list[str]]:
  blocks = []
  for i in range(enabled + disabled):
    state = "{enabled: false}" if i >= enabled else "{}"
    blocks.append([f"  T_{i:04d}: {state}"])
  for c, size in enumerate(chains):
    for j in range(size):
      lines = [f"  C{c}_{j}:", f"    pk: [K{j}]"]
      if j:
        lines += [
            "    fk:",
            f"      - {{cols: [K{j - 1}], ref: C{c}_{j - 1}, "
            f"ref_cols: [K{j - 1}]}}",
        ]
      blocks.append(lines)
  return blocks


def forest_model_text(*,
                      enabled: int = 112,
                      disabled: int = 8,
                      chains: Sequence[int] = (),
                      reverse: bool = False) -> str:
  """A model file's text: `enabled` + `disabled` isolated tables and one
  chain per entry of `chains`, declared in order or `reverse`d."""
  blocks = _blocks(enabled, disabled, chains)
  if reverse:
    blocks.reverse()
  lines = ["model: generated_forest", "tables:"]
  for block in blocks:
    lines.extend(block)
  return "\n".join(lines) + "\n"
