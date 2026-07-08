"""Structured worker-log milestones — the log-mining contract.

One stable, greppable line per milestone:

    SDFB_MILESTONE name=<milestone> key=value key='quoted value' ...

``scripts/e2e_gcp_probe.py`` mines Dataflow worker logs for this prefix to
derive engine execution timings. The format is therefore an API: fields are
emitted in sorted order, values containing whitespace are single-quoted via
``shlex.quote``, and the line never contains a newline. Change nothing here
without updating the probe and the contract test together.

This module is pure stdlib (``logging``/``shlex``/``re``) — sdfb-core must
stay Beam-free. Beam metric counterparts live in ``sdfb_beam``.
"""

from __future__ import annotations

import logging
import re
import shlex

MILESTONE_PREFIX = "SDFB_MILESTONE"

# name= then sorted key=value tokens; values may be shlex-quoted.
_MILESTONE_RE = re.compile(
    rf"{MILESTONE_PREFIX} name=(?P<name>[a-z0-9_]+)(?P<fields>.*)$"
)

_logger = logging.getLogger("sdfb.milestone")


def format_milestone(name: str, **fields) -> str:
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError(
            f"milestone name {name!r} must fully match [a-z0-9_]+ "
            "(lowercase_underscore only) — the parser anchors on this "
            "pattern, so a non-conforming name would produce a line "
            "`parse_milestone` can't mine back out"
        )
    parts = [f"{MILESTONE_PREFIX} name={name}"]
    for key in sorted(fields):
        value = str(fields[key]).replace("\n", " ")
        parts.append(f"{key}={shlex.quote(value)}")
    return " ".join(parts)


def log_milestone(name: str, *, level: int = logging.INFO, **fields) -> str:
    line = format_milestone(name, **fields)
    _logger.log(level, line)
    return line


def parse_milestone(line: str) -> dict | None:
    m = _MILESTONE_RE.search(line)
    if not m:
        return None
    out = {"name": m.group("name")}
    for token in shlex.split(m.group("fields")):
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out
