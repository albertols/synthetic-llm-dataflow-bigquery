"""Structured worker-log milestones — the log-mining contract.

One stable, greppable line per milestone:

    SDFB_MILESTONE name=<milestone> key=value key='quoted value' ...

``scripts/e2e/e2e_gcp_probe.py`` mines Dataflow worker logs for this prefix to
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


def log_prompt_debug(
    mode: str, column: str, prompt: str, redacted_prompt: str
) -> None:
    """Log one built pool prompt per the `--prompt_debug` contract
    (ADR 0024 §3c).

    "off" logs nothing (production default — reference values are banned
    from logs). "redacted" logs the instruction + rendered constraint with
    seed exemplars elided, at INFO. "full" logs the verbatim prompt —
    reference exemplars INCLUDED — at WARNING, so a debug run's leak
    surface is loud in Dataflow logs. The sha12 of the FULL prompt is
    logged in both modes, so prompt drift across runs is comparable even
    when only redacted text was captured.
    """
    if mode not in ("redacted", "full"):
        return
    log_milestone(
        "freetext_pool_prompt",
        level=logging.WARNING if mode == "full" else logging.INFO,
        column=column,
        mode=mode,
        prompt_chars=len(prompt),
        sha12=sha12(prompt),
        text=prompt if mode == "full" else redacted_prompt,
    )


def sha12(text: str) -> str:
    """First 12 hex chars of the text's sha256 — the drift-comparison key
    used for pool prompts and rendered constraint clauses alike."""
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:12]


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
