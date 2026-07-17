"""Shape detection for STRING columns that must never reach the LLM pool.

The 2026-07-17 E2E runs showed the free-text LLM pool failing on two whole
classes of *shaped* strings, one per engine:

  - date-shaped strings (B.1 COL_044/COL_034/COL_061): a plausible generated
    date has a high chance of existing somewhere in the dense source
    keyspace, so copy_ratio flags fire without any exemplar memorization —
    and the pool's distinct yield can never cover the marginal anyway;
  - fixed-alphabet identifiers (B.2 COL_001, 24-char upper-hex): the model
    echoes the shown exemplars verbatim at every escalation level
    (prompt_echoes == parsed, novel=0) — an identifier keyspace is exactly
    what an LLM cannot "vary" its way through.

Both are detectable from the reference values alone. Temporal-shaped
columns re-route to the engines' range samplers; identifier-shaped columns
generate format-preserving values from a per-position character template.

Shared between B.1 and B.2 (like `engines/identity.py`). Pure stdlib.
"""

from __future__ import annotations

import string
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable, Iterable

# Every distinct value must parse with ONE of these for a column to be
# temporal-shaped. Formats are mutually exclusive (a value parses under at
# most one), so first-match is the match.
_TEMPORAL_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
)

# One value proves nothing about a shared shape.
_MIN_VALUES = 2
# Below this length "identifier" vs enum-code is ambiguous — short codes
# stay on their existing (categorical / LLM) routes.
_MIN_IDENTIFIER_LENGTH = 8

# Ordered narrowest-first: a position's observed characters bind to the
# first class that covers them, keeping generation as tight as the evidence.
_CHAR_CLASSES: tuple[str, ...] = (
    string.digits,
    string.digits + "abcdef",
    string.digits + "ABCDEF",
    string.ascii_lowercase,
    string.ascii_uppercase,
    string.digits + string.ascii_lowercase,
    string.digits + string.ascii_uppercase,
    string.digits + string.ascii_letters,
)


def detect_temporal_format(values: Iterable[str]) -> str | None:
    """The strftime format every value renders in, or None.

    Returns a format only when EVERY non-empty value parses with the same
    one — a single mixed-format value means the column is not uniformly
    date-shaped and stays on its existing route.
    """
    vals = [v for v in values if v]
    if len(vals) < _MIN_VALUES:
        return None
    for fmt in _TEMPORAL_FORMATS:
        try:
            datetime.strptime(vals[0], fmt)
        except ValueError:
            continue
        try:
            for v in vals[1:]:
                datetime.strptime(v, fmt)
        except ValueError:
            return None  # formats are mutually exclusive — no other fits
        return fmt
    return None


def detect_identifier_shape(values: Iterable[str]) -> tuple[str, ...] | None:
    """Per-position character template of a fixed-length identifier, or None.

    Each entry is either a literal character (every value agrees at that
    position) or the narrowest character class covering the position's
    observed characters. Any position with unclassifiable variation (spaces,
    mixed punctuation) disqualifies the whole column — prose never matches.
    """
    vals = [v for v in values if v]
    if len(vals) < _MIN_VALUES:
        return None
    length = len(vals[0])
    if length < _MIN_IDENTIFIER_LENGTH:
        return None
    if any(len(v) != length for v in vals):
        return None
    # Whitespace anywhere means prose, however uniform the template looks
    # ("Ticket about issue 0042" is not an identifier).
    if any(" " in v or "\t" in v for v in vals):
        return None
    shape: list[str] = []
    for i in range(length):
        chars = {v[i] for v in vals}
        if len(chars) == 1:
            shape.append(next(iter(chars)))
            continue
        for cls in _CHAR_CLASSES:
            if chars <= set(cls):
                shape.append(cls)
                break
        else:
            return None
    return tuple(shape)


def sample_identifier(
    shape: tuple[str, ...], pick: Callable[[int], int]
) -> str:
    """One format-preserving value from a template.

    ``pick(k)`` must return an int in ``[0, k)`` — pass the caller's seeded
    RNG (``random.Random.randrange``, or a NumPy-backed lambda) so draws
    stay reproducible in either backend.
    """
    return "".join(a if len(a) == 1 else a[pick(len(a))] for a in shape)


__all__ = [
    "detect_identifier_shape",
    "detect_temporal_format",
    "sample_identifier",
]
