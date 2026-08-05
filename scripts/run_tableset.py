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

from sdfb_core.contracts.relational import RelationalContract

_RUN_PIPELINE = "packages/sdfb-beam/src/sdfb_beam/cli/run_pipeline.py"


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


def build_argv(
    table: str,
    config: dict,
    contract: RelationalContract | None,
    index: int,
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
    ]
    if contract is not None and contract.fk:
        argv.append(f"--fk_parent_landing={landing_dataset}")
    for key, value in sorted(config.get("common_args", {}).items()):
        argv.append(f"--{key}={value}")
    return argv


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
    args = p.parse_args(argv)

    config = json.loads(Path(args.config).read_text())
    contracts = _load_contracts(list(config["tables"]), args.contracts_json)
    plans = plan_tableset(config, contracts)

    for cmd in plans:
        print(" \\\n    ".join(cmd))
        print()
        if args.dry_run:
            continue
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(
                f"ABORT: {cmd[2]} failed (rc={result.returncode}); "
                f"children must not run without parents.",
                file=sys.stderr,
            )
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
