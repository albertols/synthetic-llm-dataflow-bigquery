#!/usr/bin/env python
"""Parent-first multi-table orchestrator (ADR 0021, Option 1).

Runs today's proven single-table pipeline once per table, ordered by the FK
edges declared in each table's relational contract. A child run receives
``--fk_parent_landing`` so its FK columns sample from the parents' landed
synthetic keys. Zero DAG-shape change; per-table failure isolation; a
mid-set failure stops the set (children must never run without parents).

Set config (JSON):

    {
      "tables": ["proj.src_ds.customers", "proj.src_ds.orders"],
      "landing_dataset": "proj.synthetic_data",
      "run_id": "set-2026-08-05",
      "common_args": {"num_rows": "1000000", "model_uri": "gs://...",
                       "dlq_table": "proj.synthetic_data_quality.dlq"}
    }

Contracts are fetched live via the DDL extractor; ``--contracts-json``
injects them offline (tests / air-gapped dry runs):

    {"proj.src_ds.orders": {"sdfb": 1, "fk": [{"cols": ["CUST_ID"],
      "ref": "src_ds.customers", "ref_cols": ["ID"]}]}}

Usage:
    python scripts/run_tableset.py --config tableset.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sdfb_core.contracts.fk_model import (
    FkModel,
    build_fk_model,
    fk_model_mermaid,
    model_sha12,
)
from sdfb_core.contracts.relational import RelationalContract

_RUN_PIPELINE = "packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py"
_FK_MODELS_DIR = "integration_tests/fk_models"


class TableSetError(ValueError):
    """Bad set config: unknown parent, FK cycle, duplicate table."""


def _suffix(fqn: str, parts: int) -> str:
    return ".".join(fqn.split(".")[-parts:])


def resolve_edges(
    tables: list[str], contracts: dict[str, RelationalContract | None]
) -> dict[str, set[str]]:
    """table → set of parent tables (only parents inside the set count).

    A contract's ``fk.ref`` is ``dataset.table``; it matches a set member
    whose FQN ends with it. An FK to a table OUTSIDE the set is allowed —
    the parent must already be landed (run_pipeline's preflight P3 and the
    FK-pool read enforce that at run time).
    """
    by_suffix = {_suffix(t, 2): t for t in tables}
    edges: dict[str, set[str]] = {t: set() for t in tables}
    for table in tables:
        contract = contracts.get(table)
        if contract is None:
            continue
        for fk in contract.fk:
            parent = by_suffix.get(_suffix(fk.ref, 2))
            if parent is not None and parent != table:
                edges[table].add(parent)
    return edges


def topo_sort(tables: list[str], edges: dict[str, set[str]]) -> list[str]:
    """Parents-first order, stable for independent tables; cycles raise."""
    if len(set(tables)) != len(tables):
        raise TableSetError(f"duplicate tables in set: {tables}")
    ordered: list[str] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(t: str) -> None:
        if t in done:
            return
        if t in visiting:
            raise TableSetError(f"FK cycle involving {t}")
        visiting.add(t)
        for parent in sorted(edges.get(t, ())):
            visit(parent)
        visiting.discard(t)
        done.add(t)
        ordered.append(t)

    for t in tables:
        visit(t)
    return ordered


def _enforced_fk(contract: RelationalContract | None) -> bool:
    return contract is not None and any(
        not fk.informational for fk in contract.fk
    )


def build_argv(
    table: str,
    config: dict,
    contract: RelationalContract | None,
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
    ]
    if generate_fk_relationships and _enforced_fk(contract):
        argv.append(f"--fk_parent_landing={landing_dataset}")
    for key, value in sorted(config.get("common_args", {}).items()):
        argv.append(f"--{key}={value}")
    return argv


def plan_tableset_waves(
    config: dict,
    contracts: dict[str, RelationalContract | None],
    generate_fk_relationships: bool = True,
) -> list[list[list[str]]]:
    """Parents-first WAVES of run_pipeline argvs (ADR 0029).

    Each wave's tables are FK-independent of one another, so a wave may
    run in parallel (`run_waves`); waves themselves stay sequential —
    children never launch before their parents landed. With the flag off
    every table is one wave (isolated generation) and any declared
    enforced edge is reported ignored."""
    tables = list(config["tables"])
    model = build_fk_model(tables, contracts)
    if not generate_fk_relationships:
        ignored = [
            f"{child}->{fk.ref}"
            for child, fk in model.edges
            if not fk.informational
        ]
        if ignored:
            print(
                "WARNING: --generate-fk-relationships=false ignores "
                f"declared FK edges: {ignored} — referential integrity "
                "UNVERIFIED for this set.",
                file=sys.stderr,
            )
        levels: tuple[tuple[str, ...], ...] = (tuple(tables),)
    else:
        levels = model.levels
    waves: list[list[list[str]]] = []
    index = 0
    for level in levels:
        wave = []
        for t in level:
            wave.append(
                build_argv(
                    t, config, contracts.get(t), index,
                    generate_fk_relationships=generate_fk_relationships,
                )
            )
            index += 1
        waves.append(wave)
    return waves


def emit_trigger_configs(
    config: dict,
    contracts: dict[str, RelationalContract | None],
    out_dir: Path,
    generate_fk_relationships: bool = True,
) -> list[Path]:
    """Ordered Airflow trigger confs, one JSON per table (corp path).

    The Composer DAG (`composer/synthetic_beam_bigquery.py`) stays
    single-table; multi-table on corp = triggering it once per file, in
    filename order (waves flattened — the NN_ prefix IS the order)."""
    tables = list(config["tables"])
    model = build_fk_model(tables, contracts)
    levels = (
        model.levels if generate_fk_relationships else (tuple(tables),)
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    index = 0
    for level in levels:
        for t in level:
            conf: dict = {
                "table_fqn": t,
                "generate_fk_relationships":
                    "true" if generate_fk_relationships else "false",
                **config.get("common_args", {}),
            }
            if generate_fk_relationships and _enforced_fk(contracts.get(t)):
                conf["fk_parent_landing"] = config["landing_dataset"]
            name = f"{index:02d}_{t.rsplit('.', 1)[-1]}.json"
            path = out_dir / name
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


def write_model_artifact(model: FkModel, out_dir: Path) -> Path:
    """Persist the set's FK model as `<sha12>.mmd` for diagram recycling
    (ADR 0029): the E2E report embeds this file instead of re-deriving
    the drawing when the model is unchanged."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{model_sha12(model)}.mmd"
    path.write_text(fk_model_mermaid(model))
    return path


def _load_contracts(
    tables: list[str], contracts_json: str | None
) -> dict[str, RelationalContract | None]:
    if contracts_json:
        raw = json.loads(Path(contracts_json).read_text())
        return {
            t: (
                RelationalContract.model_validate(raw[t])
                if t in raw
                else None
            )
            for t in tables
        }
    # Live mode: the extractor already knows how to parse the contract.
    from sdfb_beam.ddl.extractor import extract_ddl_metadata

    out: dict[str, RelationalContract | None] = {}
    for t in tables:
        project, dataset, name = t.split(".")
        meta = extract_ddl_metadata(project=project, dataset=dataset, table=name)
        rel = meta["table_info"].get("relational")
        out[t] = RelationalContract.model_validate(rel) if rel else None
    return out


def plan_tableset(
    config: dict, contracts: dict[str, RelationalContract | None]
) -> list[list[str]]:
    """The ordered list of run_pipeline argvs for the whole set."""
    tables = list(config["tables"])
    order = topo_sort(tables, resolve_edges(tables, contracts))
    return [
        build_argv(t, config, contracts.get(t), i)
        for i, t in enumerate(order)
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, help="set config JSON path")
    p.add_argument("--contracts-json", default=None,
                   help="offline contracts map (tests / dry runs)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the ordered commands, run nothing")
    p.add_argument("--generate-fk-relationships", default="true",
                   choices=["true", "false"],
                   help="false = isolated generation for every table (one "
                        "parallel wave, declared FK edges loudly ignored). "
                        "ADR 0029.")
    p.add_argument("--max-parallel", type=int, default=1,
                   help="jobs launched concurrently WITHIN a wave — bound "
                        "it to your Dataflow/GPU quota, waves stay "
                        "sequential (parents land first).")
    p.add_argument("--emit-trigger-configs", default=None, metavar="DIR",
                   help="write ordered Airflow trigger conf JSONs for the "
                        "Composer DAG instead of launching locally")
    p.add_argument("--fk-models-dir", default=_FK_MODELS_DIR,
                   help="where the set's FK-model .mmd artifact lands "
                        "(diagram recycling for E2E reports)")
    args = p.parse_args(argv)
    generate_fk = args.generate_fk_relationships == "true"

    config = json.loads(Path(args.config).read_text())
    tables = list(config["tables"])
    contracts = _load_contracts(tables, args.contracts_json)

    model = build_fk_model(tables, contracts)
    artifact = write_model_artifact(model, Path(args.fk_models_dir))
    print(f"FK model {model_sha12(model)} → {artifact}")
    print(fk_model_mermaid(model))
    print()

    if args.emit_trigger_configs:
        for path in emit_trigger_configs(
            config, contracts, Path(args.emit_trigger_configs),
            generate_fk_relationships=generate_fk,
        ):
            print(f"wrote {path}")
        return 0

    waves = plan_tableset_waves(
        config, contracts, generate_fk_relationships=generate_fk
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
