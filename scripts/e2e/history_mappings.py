#!/usr/bin/env python
"""Persistent table/column alias registry for E2E report redaction
(ADR 0029).

One LOCAL-ONLY file — ``runs/history_mappings_replacement
.json`` — replaces the per-job ``mapping.json``: a real table keeps the
same letter prefix and column aliases across every future run, so
reports, diagrams and cross-run comparisons stay consistent. It is the
DECODE KEY (real names inside): ``runs/`` is gitignored and
this file must never be committed or bundled, exactly like ``real/``.

Naming: prefixes in first-arrival order A..Z, AA, AB, … (dozens of
unrelated tables are expected); columns numbered in DDL order at first
sight (``A_COL_001``), new columns appended after the existing block —
an alias, once assigned, never changes. Non-DDL fields (the 6-table
example's JOIN_KEY) are recorded in ``retained`` and pass through
redaction as-is.

CLI:
    python scripts/e2e/history_mappings.py seed-example
    python scripts/e2e/history_mappings.py adopt --alias C_TABLE \\
        --real-fqn proj.ds.accounts_master
    python scripts/e2e/history_mappings.py assign \\
        --real-fqn proj.ds.new_table --ddl-json path/to/_ddl.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path

DEFAULT_PATH = Path("runs/history_mappings_replacement.json")
_EXAMPLE_ALIASES = 6  # A..F, docs/assets/fk_relationship_example.tf


def letter_prefix(index: int) -> str:
    """Spreadsheet-style prefix: 0→A … 25→Z, 26→AA, 702→AAA."""
    out = ""
    i = index
    while True:
        out = chr(ord("A") + i % 26) + out
        i = i // 26 - 1
        if i < 0:
            return out


class HistoryMappings:
    """The registry: an ordered list of table entries."""

    def __init__(self, tables: list[dict] | None = None) -> None:
        self.tables: list[dict] = tables or []

    # -- persistence -----------------------------------------------------
    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> HistoryMappings:
        if not Path(path).exists():
            return cls()
        data = json.loads(Path(path).read_text())
        return cls(tables=list(data.get("tables", [])))

    def save(self, path: Path = DEFAULT_PATH) -> Path:
        payload = {"version": 1, "tables": self.tables}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n"
        )
        return Path(path)

    # -- lookups ---------------------------------------------------------
    def _by_fqn(self, real_fqn: str) -> dict | None:
        return next(
            (t for t in self.tables if t.get("real_fqn") == real_fqn), None
        )

    def _by_alias(self, alias: str) -> dict | None:
        return next(
            (t for t in self.tables if t.get("alias") == alias), None
        )

    # -- mutation --------------------------------------------------------
    def assign_table(
        self, real_fqn: str, ddl_columns: list[str]
    ) -> dict:
        """The (stable) entry for ``real_fqn``; creates or extends it.

        Existing aliases NEVER change; unseen columns append after the
        current highest number, in the given DDL order."""
        entry = self._by_fqn(real_fqn)
        if entry is None:
            entry = {
                "real_fqn": real_fqn,
                "prefix": letter_prefix(len(self.tables)),
                "alias": f"{letter_prefix(len(self.tables))}_TABLE",
                "first_seen": _dt.date.today().isoformat(),
                "columns": {},
                "retained": [],
            }
            self.tables.append(entry)
        prefix = entry["prefix"]
        columns: dict = entry["columns"]
        next_n = len(columns) + 1
        for col in ddl_columns:
            if col in columns or col in entry.get("retained", []):
                continue
            columns[col] = f"{prefix}_COL_{next_n:03d}"
            next_n += 1
        return entry

    def adopt(self, alias: str, real_fqn: str) -> dict:
        """Bind a seeded (unbound) alias to its real table."""
        entry = self._by_alias(alias)
        if entry is None:
            raise KeyError(f"alias {alias!r} not in the registry")
        if entry.get("real_fqn") not in (None, real_fqn):
            raise ValueError(
                f"alias {alias!r} already bound to {entry['real_fqn']!r}"
            )
        entry["real_fqn"] = real_fqn
        return entry

    def retain_field(self, real_fqn: str, field: str) -> None:
        """Record a non-DDL field that passes through redaction as-is."""
        entry = self._by_fqn(real_fqn)
        if entry is None:
            raise KeyError(f"{real_fqn!r} not in the registry")
        if field not in entry["retained"]:
            entry["retained"].append(field)
        entry["columns"].pop(field, None)

    def alias_columns(self, real_fqn: str) -> dict[str, str]:
        entry = self._by_fqn(real_fqn)
        return dict(entry["columns"]) if entry else {}


def seed_example() -> HistoryMappings:
    """The 6-table example skeleton (A..F, unbound): run once, then
    ``adopt`` each alias onto its real corp FQN."""
    h = HistoryMappings()
    for i in range(_EXAMPLE_ALIASES):
        prefix = letter_prefix(i)
        h.tables.append(
            {
                "real_fqn": None,
                "prefix": prefix,
                "alias": f"{prefix}_TABLE",
                "first_seen": _dt.date.today().isoformat(),
                "columns": {},
                "retained": [],
            }
        )
    return h


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", default=str(DEFAULT_PATH))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed-example")
    adopt = sub.add_parser("adopt")
    adopt.add_argument("--alias", required=True)
    adopt.add_argument("--real-fqn", required=True)
    assign = sub.add_parser("assign")
    assign.add_argument("--real-fqn", required=True)
    assign.add_argument("--ddl-json", required=True,
                        help="_ddl.json whose schema[].name order numbers "
                             "the columns")
    args = p.parse_args(argv)

    path = Path(args.registry)
    if args.cmd == "seed-example":
        if path.exists():
            print(f"refusing to overwrite existing registry {path}")
            return 1
        seed_example().save(path)
        print(f"seeded A..F skeleton → {path}")
        return 0
    h = HistoryMappings.load(path)
    if args.cmd == "adopt":
        entry = h.adopt(args.alias, args.real_fqn)
        h.save(path)
        print(f"{args.alias} ← {entry['real_fqn']}")
        return 0
    ddl = json.loads(Path(args.ddl_json).read_text())
    cols = [c["name"] for c in ddl.get("schema", [])]
    entry = h.assign_table(args.real_fqn, cols)
    h.save(path)
    print(f"{entry['alias']}: {len(entry['columns'])} columns mapped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
