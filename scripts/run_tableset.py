#!/usr/bin/env python
"""Parent-first multi-table orchestrator (ADR 0021, Option 1).

Runs today's proven single-table pipeline once per table, ordered by the
FK edges declared in `config/relationships/` (ADR 0032). A child run
receives ``--fk_parent_landing`` so its FK columns sample from the
parents' landed synthetic keys. Zero DAG-shape change; per-table failure
isolation; a mid-set failure stops the set (children must never run
without parents).

`run_pipeline.py --multi_table_mode=single_job` (the default) already
puts a whole model in ONE Dataflow job (ADR 0030); this script stays for
the cases that want job-per-table isolation or Airflow trigger configs.

Set config (JSON):

    {
      "tables": ["proj.src_ds.customers", "proj.src_ds.orders"],
      "landing_dataset": "proj.synthetic_data",
      "run_id": "set-2026-08-05",
      "common_args": {"num_rows": "1000000", "model_uri": "gs://...",
                       "dlq_table": "proj.synthetic_data_quality.dlq"}
    }

Relationships come from ``--relationships-uri`` (default
`config/relationships`, a folder or file, local or gs://) — the same
single source of truth the pipeline reads.

Usage:
    python scripts/run_tableset.py --config tableset.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sdfb_beam.io.relationships import load_relationship_registry
from sdfb_core.contracts.relationships import RelationshipRegistry

_RUN_PIPELINE = "packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py"
_FK_MODELS_DIR = "integration_tests/fk_models"
_REL_DIR = "config/relationships"


class TableSetError(ValueError):
    """Bad set config: unknown parent, FK cycle, duplicate table."""


def _names(tables: list[str]) -> tuple[str, ...]:
    return tuple(t.rsplit(".", 1)[-1] for t in tables)


def build_argv(
    table: str,
    config: dict,
    registry: RelationshipRegistry,
    index: int,
    generate_fk_relationships: bool = True,
) -> list[str]:
    """The run_pipeline argv for one table of the set."""
    landing_dataset = config["landing_dataset"]
    run_id = config.get("run_id", "tableset")
    argv = [
        sys.executable,
        _RUN_PIPELINE,
        f"--reference_table={table}",
        f"--landing_table={landing_dataset}.{table.rsplit('.', 1)[-1]}",
        f"--run_id={run_id}-{index:02d}-{table.rsplit('.', 1)[-1]}",
        "--generate_fk_relationships="
        + ("true" if generate_fk_relationships else "false"),
        f"--relationships_uri={config.get('relationships_uri', _REL_DIR)}",
    ]
    if generate_fk_relationships and registry.enforced_edges(table):
        argv.append(f"--fk_parent_landing={landing_dataset}")
    for key, value in sorted(config.get("common_args", {}).items()):
        argv.append(f"--{key}={value}")
    return argv


def plan_tableset_waves(
    config: dict,
    registry: RelationshipRegistry,
    generate_fk_relationships: bool = True,
) -> list[list[list[str]]]:
    """Parents-first WAVES of run_pipeline argvs (ADR 0029/0032).

    Each wave's tables are FK-independent of one another, so a wave may
    run in parallel (`run_waves`); waves themselves stay sequential —
    children never launch before their parents landed. With the flag off
    every table is one wave (isolated generation) and any enforced edge
    the model declares is reported ignored."""
    tables = list(config["tables"])
    if not generate_fk_relationships:
        ignored = [
            f"{t}->{edge.ref}"
            for t in tables
            for edge in registry.enforced_edges(t)
        ]
        if ignored:
            print(
                "WARNING: --generate-fk-relationships=false ignores the "
                f"enforced FK edges {ignored} declared in "
                "config/relationships — referential integrity UNVERIFIED "
                "for this set.",
                file=sys.stderr,
            )
        waves_of_names: tuple[tuple[str, ...], ...] = (_names(tables),)
    else:
        waves_of_names = registry.generation_waves(_names(tables))
    by_name = {t.rsplit(".", 1)[-1]: t for t in tables}
    waves: list[list[list[str]]] = []
    index = 0
    for wave_names in waves_of_names:
        wave = []
        for name in wave_names:
            table = by_name.get(name, name)
            wave.append(
                build_argv(
                    table, config, registry, index,
                    generate_fk_relationships=generate_fk_relationships,
                )
            )
            index += 1
        waves.append(wave)
    return waves


def emit_trigger_configs(
    config: dict,
    registry: RelationshipRegistry,
    out_dir: Path,
    generate_fk_relationships: bool = True,
) -> list[Path]:
    """Ordered Airflow trigger confs, one JSON per table (corp path).

    The Composer DAG (`composer/synthetic_beam_bigquery.py`) stays
    single-table; multi-table on corp = triggering it once per file, in
    filename order (waves flattened — the NN_ prefix IS the order)."""
    tables = list(config["tables"])
    waves_of_names = (
        registry.generation_waves(_names(tables))
        if generate_fk_relationships
        else (_names(tables),)
    )
    by_name = {t.rsplit(".", 1)[-1]: t for t in tables}
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    index = 0
    for wave_names in waves_of_names:
        for name in wave_names:
            table = by_name.get(name, name)
            conf: dict = {
                "table_fqn": table,
                "generate_fk_relationships":
                    "true" if generate_fk_relationships else "false",
                **config.get("common_args", {}),
            }
            if generate_fk_relationships and registry.enforced_edges(table):
                conf["fk_parent_landing"] = config["landing_dataset"]
            path = out_dir / f"{index:02d}_{name}.json"
            path.write_text(json.dumps(conf, indent=2, sort_keys=True))
            paths.append(path)
            index += 1
    return paths


def run_waves(
    waves: list[list[list[str]]], max_parallel: int = 1
) -> int:
    """Execute waves; parallel WITHIN a wave, sequential across waves.

    First non-zero return code aborts everything after the current
    chunk — children must never run without parents. Bound
    ``max_parallel`` to the GPU/Dataflow quota, not the table count."""
    for wave in waves:
        for i in range(0, len(wave), max(1, max_parallel)):
            chunk = wave[i:i + max(1, max_parallel)]
            procs = [subprocess.Popen(cmd) for cmd in chunk]
            rcs = [p.wait() for p in procs]
            bad = next((rc for rc in rcs if rc != 0), 0)
            if bad:
                print(
                    f"ABORT: a set member failed (rc={bad}); later waves "
                    f"must not run without their parents.",
                    file=sys.stderr,
                )
                return bad
    return 0


def write_model_artifact(
    registry: RelationshipRegistry, table: str, out_dir: Path
) -> Path:
    """Persist the set's model diagram as `<sha12>.mmd` for recycling
    (ADR 0029): the E2E report embeds this file instead of re-deriving
    the drawing when the model is unchanged."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{registry.sha12()}.mmd"
    path.write_text(registry.mermaid(table))
    return path


def plan_tableset(
    config: dict, registry: RelationshipRegistry
) -> list[list[str]]:
    """The ordered list of run_pipeline argvs for the whole set."""
    tables = list(config["tables"])
    by_name = {t.rsplit(".", 1)[-1]: t for t in tables}
    order = registry.generation_order(_names(tables))
    return [
        build_argv(by_name.get(name, name), config, registry, i)
        for i, name in enumerate(order)
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, help="set config JSON path")
    p.add_argument("--relationships-uri", default=_REL_DIR,
                   help="where the relational models live (ADR 0032): a "
                        "folder or a single YAML file, local or gs://. "
                        "The same single source of truth run_pipeline "
                        "reads.")
    p.add_argument("--dry-run", action="store_true",
                   help="print the ordered commands, run nothing")
    p.add_argument("--generate-fk-relationships", default="true",
                   choices=["true", "false"],
                   help="false = isolated generation for every table (one "
                        "parallel wave, the model's enforced FK edges "
                        "loudly ignored). ADR 0029/0032.")
    p.add_argument("--max-parallel", type=int, default=1,
                   help="jobs launched concurrently WITHIN a wave — bound "
                        "it to your Dataflow/GPU quota, waves stay "
                        "sequential (parents land first).")
    p.add_argument("--emit-trigger-configs", default=None, metavar="DIR",
                   help="write ordered Airflow trigger conf JSONs for the "
                        "Composer DAG instead of launching locally")
    p.add_argument("--fk-models-dir", default=_FK_MODELS_DIR,
                   help="where the set's model .mmd artifact lands "
                        "(diagram recycling for E2E reports)")
    args = p.parse_args(argv)
    generate_fk = args.generate_fk_relationships == "true"

    config = json.loads(Path(args.config).read_text())
    config.setdefault("relationships_uri", args.relationships_uri)
    tables = list(config["tables"])
    registry = load_relationship_registry(args.relationships_uri)

    artifact = write_model_artifact(
        registry, tables[0], Path(args.fk_models_dir)
    )
    print(f"relationship model {registry.sha12()} → {artifact}")
    print(registry.card(tables[0]))
    print()

    if args.emit_trigger_configs:
        for path in emit_trigger_configs(
            config, registry, Path(args.emit_trigger_configs),
            generate_fk_relationships=generate_fk,
        ):
            print(f"wrote {path}")
        return 0

    waves = plan_tableset_waves(
        config, registry, generate_fk_relationships=generate_fk
    )
    for level, wave in enumerate(waves):
        print(f"— wave {level} ({len(wave)} tables) —")
        for cmd in wave:
            print(" \\\n    ".join(cmd))
            print()
    if args.dry_run:
        return 0
    return run_waves(waves, max_parallel=args.max_parallel)


if __name__ == "__main__":
    raise SystemExit(main())
