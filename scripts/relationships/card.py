#!/usr/bin/env python
"""Print the relationship card a launch would log — without launching.

The model files decide what a run generates (ADR 0032), so the fastest
way to check an edit is to render exactly what `run_pipeline` will:

    uv run --no-sync python3 scripts/relationships/card.py --table A_TABLE
    uv run --no-sync python3 scripts/relationships/card.py --all
    uv run --no-sync python3 scripts/relationships/card.py \\
        --relationships-uri gs://bucket/relationships --table A_TABLE

Exit code is non-zero when the models do not load, so it doubles as a
pre-commit check on `config/relationships/`.
"""

from __future__ import annotations

import argparse
import sys

from sdfb_core.contracts.relationships import (
    RelationshipError,
    RelationshipRegistry,
)

_REL_DIR = "config/relationships"


def _load(uri: str) -> RelationshipRegistry:
    """Local paths go through the pure-Python loader; anything else (a
    gs:// URI) needs Beam's filesystems, imported only then."""
    if "://" not in uri:
        from pathlib import Path

        base = Path(uri)
        paths = (
            sorted(p for p in base.glob("*.y*ml"))
            if base.is_dir()
            else [base]
        )
        return RelationshipRegistry.from_sources(
            [(str(p), p.read_text(encoding="utf-8")) for p in paths]
        )
    from sdfb_beam.io.relationships import load_relationship_registry

    return load_relationship_registry(uri)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relationships-uri", default=_REL_DIR)
    parser.add_argument("--table", default="", help="table name or FQN")
    parser.add_argument("--all", action="store_true",
                        help="one card per model")
    parser.add_argument("--mermaid", action="store_true",
                        help="print the diagram source too")
    args = parser.parse_args(argv)

    try:
        registry = _load(args.relationships_uri)
    except RelationshipError as exc:
        print(f"model files do not load:\n{exc}", file=sys.stderr)
        return 2

    if not registry.models:
        print(f"no model files under {args.relationships_uri}")
        return 0

    targets: list[str] = []
    if args.table:
        targets = [args.table]
    elif args.all:
        targets = [next(iter(m.tables)) for m in registry.models if m.tables]
    else:
        print("pass --table <name> or --all", file=sys.stderr)
        return 2

    for target in targets:
        print(registry.log_body(target) if args.mermaid else registry.card(target))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
