#!/usr/bin/env python3
"""Render a tiers.yaml (tier, table) preset into flex-template-run inputs.

Prints eval-able shell lines (PARAMS / MACHINE_TYPE / ACCELERATOR /
MAX_WORKERS / NUM_WORKERS / SDK_CONTAINERS / EXPECT). Pure python + PyYAML; unit-tested laptop-side.
"""
from __future__ import annotations

import argparse
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
    "machine_type", "accelerator", "max_workers", "num_workers",
    "sdk_containers", "expect",
)


def _expand(value: str, variables: dict[str, str]) -> str:
    for k, v in variables.items():
        value = value.replace(f"{{{k}}}", v)
    return value


def render(tiers_path: Path, tier: str, table: str, variables: dict[str, str]):
    cfg = yaml.safe_load(Path(tiers_path).read_text())
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

    params = {k: _expand(str(v), variables) for k, v in params.items() if str(v) != ""}
    job = {k: _expand(str(v), variables) for k, v in job.items()}
    for key in JOB_KEYS:
        job.setdefault(key, "")
    return params, job


def to_shell(params: dict[str, str], job: dict[str, str]) -> str:
    joined = ",".join(f"{k}={params[k]}" for k in sorted(params))
    lines = [
        f"PARAMS='{joined}'",
        f"MACHINE_TYPE='{job['machine_type']}'",
        f"ACCELERATOR='{job['accelerator']}'",
        f"MAX_WORKERS='{job['max_workers']}'",
        f"NUM_WORKERS='{job['num_workers']}'",
        f"SDK_CONTAINERS='{job['sdk_containers']}'",
        f"EXPECT='{job['expect']}'",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", required=True, type=Path)
    ap.add_argument("--tier", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--var", action="append", default=[], metavar="K=V")
    args = ap.parse_args()
    variables = dict(kv.split("=", 1) for kv in args.var)
    print(to_shell(*render(args.tiers, args.tier, args.table, variables)))


if __name__ == "__main__":
    main()
