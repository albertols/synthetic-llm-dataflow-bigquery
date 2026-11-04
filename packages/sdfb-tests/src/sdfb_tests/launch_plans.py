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
"""The explicit launch plan, observed (ADR 0045, AC-B-4).

`plan_launch` decides which tables an explicit launch generates, in which
order, from which sources and under which run ids. Whole-model launches and
the per-table `source_table` / `num_rows` keys extend it; a model that sets
neither must plan exactly as before. `observe_launch_plans()` runs a matrix
of explicit launches over the committed models; the golden it is compared
with was written from the code before ADR 0045, once:

    uv run --no-sync python3 -m sdfb_tests.launch_plans --write

Design: docs/DESIGN.md §4 Relational generation
(ADR 0045).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GOLDEN = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" /
    "launch_plans" / "explicit.json")
_MODELS = Path(__file__).resolve().parents[4] / "config" / "relationships"

# case → (model file or None, landing targets, reference table, generate_fk)
PLAN_CASES: dict[str, tuple[str | None, tuple[str, ...], str, bool]] = {
    "retail_closure_from_e":
        ("example_retail.yaml", ("p.land.E_TABLE",), "p.src.E_TABLE", True),
    "retail_root_a":
        ("example_retail.yaml", ("p.land.A_TABLE",), "p.src.A_TABLE", True),
    "retail_two_targets":
        ("example_retail.yaml", ("p.land.B_TABLE", "p.land.D_TABLE"),
         "p.src.B_TABLE", True),
    "retail_isolated":
        ("example_retail.yaml", ("p.land.C_TABLE",), "p.src.C_TABLE", False),
    "retail_multi_isolated":
        ("example_retail.yaml", ("p.land.A_TABLE", "p.land.B_TABLE"),
         "p.src.A_TABLE", False),
    "star_fact":
        ("example_star_diamond.yaml", ("p.land.fact",), "p.src.fact", True),
    "diamond_bottom":
        ("example_star_diamond.yaml", ("p.land.bottom",), "p.src.bottom", True),
    "thelook_items": ("gcp_public_fk_example.yaml", ("p.land.order_items",),
                      "p.src.order_items", True),
    "no_model": (None, ("p.land.t",), "p.src.t", True),
    "unknown_target":
        ("example_retail.yaml", ("p.land.Z_TABLE",), "p.src.Z_TABLE", True),
}


def _registry(name: str | None):
  from sdfb_core.contracts.relationships import RelationshipRegistry  # pylint: disable=import-outside-toplevel  # test-time only
  if name is None:
    return RelationshipRegistry()
  path = _MODELS / name
  return RelationshipRegistry.from_sources([(f"config/relationships/{name}",
                                             path.read_text(encoding="utf-8"))])


def observe_launch_plans() -> dict:
  """Every case's scenario, warnings and runs — the four fields a run had
  before ADR 0045 (its `num_rows` is checked separately)."""
  from sdfb_beam.cli import run_pipeline as rp  # pylint: disable=import-outside-toplevel  # heavy import, test-time only
  out = {}
  for case, (model, targets, reference, fk) in PLAN_CASES.items():
    plan = rp.plan_launch(list(targets), reference, fk, _registry(model), "r1")
    out[case] = {
        "scenario":
            plan.scenario,
        "warnings":
            list(plan.warnings),
        "runs": [{
            "landing_table": r.landing_table,
            "source_table": r.source_table,
            "run_id": r.run_id,
            "fk_parent_landing": r.fk_parent_landing,
        } for r in plan.runs],
    }
  return out


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
  parser.add_argument("--write", action="store_true")
  args = parser.parse_args(argv)
  text = json.dumps(observe_launch_plans(), indent=2, sort_keys=True) + "\n"
  if not args.write:
    sys.stdout.write(text)
    return 0
  GOLDEN.parent.mkdir(parents=True, exist_ok=True)
  GOLDEN.write_text(text, encoding="utf-8")
  return 0


if __name__ == "__main__":
  sys.exit(main())
