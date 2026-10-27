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
"""The batch planner of a whole-model launch (ADR 0045).

`launch.tables: all` generates every enabled table of one relationships
model. The planner groups them into Dataflow jobs of at most
`launch.max_tables_per_job` (K) tables and never splits a connected
component: a child is generated from its parent's landed keys inside one
job (ADR 0030, 0036), so a component spread over two jobs would lose its
referential integrity.

    1. the components of the selected tables (RelationshipRegistry.component)
    2. sorted by size descending, then by their first table name
    3. first-fit-decreasing into bins of <= K tables; a component larger
       than K gets a bin of its own (`Batch.oversized`)
    4. bins ordered by their first table name: b00, b01, ...

The result depends only on the model's content and K, not on the order the
file declares its tables. `composer/sdfb_batches.py` repeats this for the
Composer DAG's tables=all branch, which cannot import sdfb-core; a parity
test pins the two to the same output.

Design: docs/DESIGN.md §4 Relational generation
(ADR 0030, 0045).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sdfb_core.contracts.relationships import RelationshipRegistry

__all__ = [
    "Batch", "ModelLaunch", "ModelLaunchError", "batch_label", "batch_run_id",
    "launch_banner", "plan_batches", "plan_model_launch", "select_tables"
]


class ModelLaunchError(ValueError):
  """A whole-model launch this model or K cannot support."""


def batch_label(index: int) -> str:
  """`b00`, `b01`, …: a batch's name in run ids, job names and logs."""
  return f"b{index:02d}"


def batch_run_id(base: str, index: int) -> str:
  """A batch job's run id: the launch's base run id and the batch label."""
  return f"{base}-{batch_label(index)}"


@dataclass(frozen=True)
class Batch:
  """One Dataflow job of a whole-model launch."""
  index: int
  tables: tuple[str, ...]  # bare names, sorted
  components: tuple[tuple[str, ...], ...]  # each sorted; sorted by first name
  max_tables_per_job: int

  @property
  def label(self) -> str:
    return batch_label(self.index)

  @property
  def oversized(self) -> bool:
    """One component larger than K: it runs whole, as its own job."""
    return len(self.tables) > self.max_tables_per_job


@dataclass(frozen=True)
class ModelLaunch:
  """The whole plan: the model, what it selects and skips, the batches."""
  model: str
  source: str
  selected: tuple[str, ...]
  skipped: tuple[str, ...]
  batches: tuple[Batch, ...]
  max_tables_per_job: int


def select_tables(
    registry: RelationshipRegistry) -> tuple[tuple[str, ...], tuple[str, ...]]:
  """(selected, skipped) of the registry's ONE model, in model order: the
  `enabled: true` tables, and the `enabled: false` ones."""
  if len(registry.models) != 1:
    names = [m.model for m in registry.models]
    raise ModelLaunchError(
        f"a whole-model launch reads exactly one model, the relationships "
        f"URI loaded {len(names)}: {names}")
  (model,) = registry.models
  selected = tuple(t for t, r in model.tables.items() if r.enabled)
  skipped = tuple(t for t, r in model.tables.items() if not r.enabled)
  if not selected:
    raise ModelLaunchError(f"{model.source}: model {model.model!r} enables "
                           f"no table — nothing to generate")
  return selected, skipped


def _components(registry: RelationshipRegistry,
                selected: Sequence[str]) -> list[tuple[str, ...]]:
  chosen = set(selected)
  seen: set[str] = set()
  out: list[tuple[str, ...]] = []
  for table in selected:
    if table in seen:
      continue
    component = tuple(
        sorted(t for t in registry.component(table) if t in chosen))
    seen.update(component)
    out.append(component)
  return out


def plan_batches(registry: RelationshipRegistry, selected: Sequence[str],
                 max_tables_per_job: int) -> tuple[Batch, ...]:
  """`selected` packed into batches of at most `max_tables_per_job`
  tables, first-fit-decreasing over whole components (module docstring)."""
  if max_tables_per_job < 1:
    raise ModelLaunchError(
        f"max_tables_per_job must be >= 1, got {max_tables_per_job}")
  components = sorted(
      _components(registry, selected), key=lambda c: (-len(c), c[0]))
  bins: list[list[tuple[str, ...]]] = []
  for component in components:
    for members in bins:
      if sum(len(c) for c in members) + len(component) <= max_tables_per_job:
        members.append(component)
        break
    else:
      bins.append([component])
  bins.sort(key=lambda members: min(t for c in members for t in c))
  return tuple(
      Batch(
          index=index,
          tables=tuple(sorted(t
                              for c in members
                              for t in c)),
          components=tuple(sorted(members)),
          max_tables_per_job=max_tables_per_job)
      for index, members in enumerate(bins))


def plan_model_launch(registry: RelationshipRegistry,
                      max_tables_per_job: int) -> ModelLaunch:
  """Select the model's enabled tables and plan their batches."""
  selected, skipped = select_tables(registry)
  (model,) = registry.models
  return ModelLaunch(
      model=model.model,
      source=model.source,
      selected=selected,
      skipped=skipped,
      batches=plan_batches(registry, selected, max_tables_per_job),
      max_tables_per_job=max_tables_per_job)


def launch_banner(launch: ModelLaunch) -> str:
  """The plan as one text block — logged by the launcher, printed by
  `scripts/launch/run_model.py --dry_run`."""
  lines = [
      f"MODEL LAUNCH {launch.model} | source {launch.source} | "
      f"{len(launch.selected)} tables enabled · {len(launch.skipped)} "
      f"disabled · {len(launch.batches)} batch job(s) of <= "
      f"{launch.max_tables_per_job} tables"
  ]
  for batch in launch.batches:
    flag = (f"   [OVERSIZED — one component of {len(batch.tables)} tables]"
            if batch.oversized else "")
    tables = ", ".join(batch.tables)
    lines.append(f"  {batch.label} | {len(batch.tables):>3} tables | "
                 f"{tables}{flag}")
  if launch.skipped:
    skipped = ", ".join(launch.skipped)
    lines.append(f"  skipped (enabled: false): {skipped}")
  return "\n".join(lines)
