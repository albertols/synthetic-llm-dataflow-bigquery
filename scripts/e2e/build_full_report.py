#!/usr/bin/env python
"""Unify a bundle dir's reports + metrics into one ``_full_report.md``.

One deployment produces several markdown reports (`report.md`,
`stats_diff.md`, `freetext_crosscheck_report.md`,
`prompt_constraint_recommendations.md`, …) and several metrics JSONs.
Sharing or pasting them one by one loses the whole-picture view, so this
tool recompiles everything in a bundle dir (``real/`` or ``oss/``) into a
single ``_full_report.md``:

  * a table of contents at the very top, linking every section;
  * one section per ``*.md`` document, content **verbatim** (all the
    individual files are kept — this is a recap, not a replacement);
  * every metrics ``*.json`` embedded as a fenced ```json annex at the
    bottom, so the numbers travel with the prose.

Discovery-based: any future ``.md``/``.json`` artifact dropped into the dir
is included automatically (canonical files first, newcomers after,
alphabetically). Excluded: the output itself and ``mapping.json`` (the
decode key is not a report input and must never ride a shareable recap).
Idempotent — rerun after any document changes (e.g. after the
prompt-constraint recommender lands its report) and the recap is rebuilt
from scratch.

Usage:
    python scripts/e2e/build_full_report.py \
        --dir integration_test/<JOB_ID>/real \
        --dir integration_test/<JOB_ID>/oss
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_OUT_NAME = "_full_report.md"

# Preferred section order; files not listed here follow alphabetically.
_CANONICAL_DOCS = [
    "report.md",
    "stats_diff.md",
    "freetext_crosscheck_report.md",
    "prompt_constraint_recommendations.md",
]
_CANONICAL_METRICS = [
    "gcp_metrics.json",
    "offline_metrics.json",
    "stats_diff_metrics.json",
    "freetext_crosscheck_metrics.json",
]
# mapping.json is the real/ decode key, not a report input.
_EXCLUDED = {_OUT_NAME, "mapping.json"}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _ordered(names: list[str], canonical: list[str]) -> list[str]:
    known = [n for n in canonical if n in names]
    rest = sorted(n for n in names if n not in canonical)
    return known + rest


def build_full_report(bundle_dir: Path, annexes: str = "inline") -> Path:
    """``annexes="inline"`` embeds every metrics JSON as a fenced annex;
    ``"list"`` names the files (with sizes) instead — the shareable small
    variant. The 2026-08-11 R1 recaps were deliberately shared annex-free
    for size; with no supported small variant, the hand edit left a ToC
    promising anchors the file no longer carried."""
    docs = _ordered(
        [p.name for p in bundle_dir.glob("*.md") if p.name not in _EXCLUDED],
        _CANONICAL_DOCS,
    )
    metrics = _ordered(
        [p.name for p in bundle_dir.glob("*.json") if p.name not in _EXCLUDED],
        _CANONICAL_METRICS,
    )

    variant = bundle_dir.name if bundle_dir.name in ("real", "oss") else ""
    job_id = bundle_dir.parent.name if variant else bundle_dir.name

    inline = annexes == "inline"
    annex_note = (
        f"{len(metrics)} metrics annexes"
        if inline
        else f"{len(metrics)} metrics files (annexes: listed, not inlined)"
    )
    lines = [
        f"# {job_id} — full E2E report" + (f" ({variant})" if variant else ""),
        "",
        f"One-file recap of every report + metrics artifact in this bundle "
        f"({len(docs)} documents, {annex_note}). The "
        f"individual files stay canonical — regenerate this recap with "
        f"`python scripts/e2e/build_full_report.py --dir <this dir>` after "
        f"any of them changes.",
        "",
        "## Table of contents",
        "",
    ]
    for i, name in enumerate(docs, 1):
        lines.append(f"{i}. [{name}](#doc-{_slug(name)})")
    lines += ["", "Annexes:", ""]
    for name in metrics:
        if inline:
            lines.append(f"- [{name}](#annex-{_slug(name)})")
        else:
            size = (bundle_dir / name).stat().st_size
            lines.append(f"- {name} ({size:,} bytes — see bundle dir)")

    for name in docs:
        lines += [
            "",
            "---",
            "",
            f'<a id="doc-{_slug(name)}"></a>',
            "",
            f"# 📄 {name}",
            "",
            (bundle_dir / name).read_text().rstrip(),
        ]
    if inline:
        for name in metrics:
            lines += [
                "",
                "---",
                "",
                f'<a id="annex-{_slug(name)}"></a>',
                "",
                f"# 📎 Annex — {name}",
                "",
                "```json",
                (bundle_dir / name).read_text().rstrip(),
                "```",
            ]

    out = bundle_dir / _OUT_NAME
    out.write_text("\n".join(lines) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dir",
        dest="dirs",
        action="append",
        required=True,
        type=Path,
        help="bundle dir to recap (repeatable — typically real/ and oss/)",
    )
    ap.add_argument(
        "--annexes",
        choices=("inline", "list"),
        default="inline",
        help="inline = embed metrics JSONs as fenced annexes (default); "
        "list = name them with sizes only (small shareable variant)",
    )
    args = ap.parse_args(argv)
    for bundle_dir in args.dirs:
        out = build_full_report(bundle_dir, annexes=args.annexes)
        print(f"full report → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
