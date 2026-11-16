#!/usr/bin/env python3
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
"""Render a tiers.yaml (tier, table) preset into flex-template-run inputs.

Prints PARAMS / MACHINE_TYPE / ACCELERATOR / MAX_WORKERS / NUM_WORKERS /
SDK_CONTAINERS / EXPECT, one per line. `--format lines` (what run_e2e.sh
reads, as data, never eval'd) prints plain KEY=VALUE; `--format shell`
(the default) prints POSIX single-quoted assignments. Pure python + PyYAML;
unit-tested laptop-side.

CWE-78 (November 2026 scan): every `--var` name and value is allow-listed
before substitution, and no emitted value may hold a line break or NUL.
"""

# f-string fields keep single quotes: yapf 0.43 cannot parse PEP 701
# quote reuse, and pylint reads those quotes as inconsistent.
# pylint: disable=inconsistent-quotes

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

# `num_workers` = the INITIAL worker count (ADR 0034): empty leaves
# Dataflow's default (the 2026-08-29 R6 pair started on 2 and autoscaled to
# 4 only ~4 min into the first generate stage); a scale tier starts at its
# ceiling so the first stage never runs at a fraction of the fleet.
# `sdk_containers`: "single" keeps `no_use_multiple_sdk_containers` (one
# Python process per worker — GIL-bound generation, RUN_PLAYBOOK §3);
# "multi" lifts it (one process per vCPU; the cross-process vLLM spawn
# mutex keeps one server per worker, ADR 0034).
JOB_KEYS = (
    "machine_type",
    "accelerator",
    "max_workers",
    "num_workers",
    "sdk_containers",
    "expect",
)

# `--var` values are GCP project ids and bucket names (bucket names may
# hold dots, up to 222 characters): no quote, space, `$`, backtick or `;`.
_VAR_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_VAR_VALUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,221}")
_LINE_BREAKS = ("\n", "\r", "\0")


def validate_variables(variables: dict[str, str]) -> None:
  for name, value in variables.items():
    if not _VAR_NAME_RE.fullmatch(name):
      raise SystemExit(f"invalid --var name {name!r} "
                       f"(allowed: {_VAR_NAME_RE.pattern})")
    if not _VAR_VALUE_RE.fullmatch(value):
      raise SystemExit(f"invalid --var value for {name}: {value!r} "
                       f"(allowed: {_VAR_VALUE_RE.pattern})")


def _expand(value: str, variables: dict[str, str]) -> str:
  for k, v in variables.items():
    value = value.replace(f"{{{k}}}", v)
  return value


def render(tiers_path: Path, tier: str, table: str, variables: dict[str, str]):
  validate_variables(variables)
  cfg = yaml.safe_load(Path(tiers_path).read_text(encoding="utf-8"))
  if tier not in cfg["tiers"]:
    raise SystemExit(f"unknown tier {tier!r}; known: {sorted(cfg['tiers'])}")
  if table not in cfg["tables"]:
    raise SystemExit(f"unknown table {table!r}; known: {sorted(cfg['tables'])}")

  tier_cfg = cfg["tiers"][tier] or {}
  params: dict[str, str] = {}
  params.update(cfg["defaults"].get("params", {}))
  params.update(cfg["tables"][table].get("params", {}))
  params.update(tier_cfg.get("params", {}))

  job: dict[str, str] = dict(cfg["defaults"].get("job", {}))
  job.update(tier_cfg.get("job", {}))

  params = {
      k: _expand(str(v), variables) for k, v in params.items() if str(v) != ""
  }
  job = {k: _expand(str(v), variables) for k, v in job.items()}
  for key in JOB_KEYS:
    job.setdefault(key, "")
  return params, job


def _assignments(params: dict[str, str],
                 job: dict[str, str]) -> list[tuple[str, str]]:
  joined = ",".join(f"{k}={params[k]}" for k in sorted(params))
  pairs = [("PARAMS", joined)]
  pairs += [(key.upper(), job[key]) for key in JOB_KEYS]
  for key, value in pairs:
    if any(c in value for c in _LINE_BREAKS):
      raise SystemExit(f"{key} holds a line break or NUL: {value!r}")
  return pairs


def _single_quote(value: str) -> str:
  """POSIX single quotes: nothing inside is special except `'` itself."""
  return "'" + value.replace("'", "'\\''") + "'"


def to_shell(params: dict[str, str], job: dict[str, str]) -> str:
  return "\n".join(
      f"{k}={_single_quote(v)}" for k, v in _assignments(params, job))


def to_lines(params: dict[str, str], job: dict[str, str]) -> str:
  """Plain KEY=VALUE lines, read as data by run_e2e.sh."""
  return "\n".join(f"{k}={v}" for k, v in _assignments(params, job))


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--tiers", required=True, type=Path)
  ap.add_argument("--tier", required=True)
  ap.add_argument("--table", required=True)
  ap.add_argument("--var", action="append", default=[], metavar="K=V")
  ap.add_argument("--format", choices=("shell", "lines"), default="shell")
  args = ap.parse_args()
  if any("=" not in kv for kv in args.var):
    raise SystemExit("--var must be K=V")
  variables = dict(kv.split("=", 1) for kv in args.var)
  emit = to_lines if args.format == "lines" else to_shell
  print(emit(*render(args.tiers, args.tier, args.table, variables)))


if __name__ == "__main__":
  main()
