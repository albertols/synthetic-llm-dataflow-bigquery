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
"""Pure decision logic for the billing killswitch. No cloud imports."""
from __future__ import annotations


def should_kill(payload: dict, threshold: float) -> bool:
  """True when spend has reached `threshold` fraction of the budget."""
  budget = payload.get("budgetAmount")
  cost = payload.get("costAmount")
  if not budget or cost is None:
    return False
  return (cost / budget) >= threshold
