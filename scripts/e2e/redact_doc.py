#!/usr/bin/env python
"""Redact one markdown artifact into its ``oss/`` twin, post-bundle.

`e2e_bundle_export.py` redacts everything it ingests at export time, but some
artifacts are created AFTER the bundle exists — e.g. the
`llm_prompt_constraint_recommender` prompt's recommendations report. This
tool rebuilds the deployment's `Mapping` from the persisted
``real/mapping.json`` and applies the standard replacements (IDENTIFIERS,
COLUMN NAMES → ``COL_NNN``, DATA VALUES → ``VAL_NNNN``), so the ``oss/``
twin stays consistent with every other file in the bundle and can be shared
agnostically.

Exits non-zero when a known identifier/column survives redaction (same token
classes as ``redaction.leak_scan``, scoped to the file just written) — the
twin is only shareable when the run prints ``leak scan: clean ✅``.

Usage:
    python scripts/e2e/redact_doc.py \
        --mapping runs/<JOB_ID>/real/mapping.json \
        --in  runs/<JOB_ID>/real/prompt_constraint_recommendations.md \
        --out runs/<JOB_ID>/oss/prompt_constraint_recommendations.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redaction  # sibling import; path must be set up first


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="the bundle's real/mapping.json (Mapping.to_dict output)",
    )
    ap.add_argument(
        "--in",
        dest="in_path",
        type=Path,
        required=True,
        help="markdown artifact to redact (left untouched)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        required=True,
        help="redacted twin destination (parents created)",
    )
    args = ap.parse_args(argv)

    mapping = redaction.mapping_from_dict(json.loads(args.mapping.read_text()))
    redacted = mapping.redact_text(args.in_path.read_text())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(redacted)
    print(f"redacted doc → {args.out}")

    reals = list(mapping.identifiers) + list(mapping.columns)
    hits = [real for real in reals if real and real in redacted]
    if hits:
        print("WARNING: possible residual tokens:")
        for tok in hits:
            print(f"  {tok!r}")
        return 1
    print("leak scan: clean ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
