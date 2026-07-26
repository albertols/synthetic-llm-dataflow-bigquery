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

import re
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


# A (weight, per-position template) pair per observed length bucket.
RelaxedShapes = tuple[tuple[int, tuple[str, ...]], ...]


def build_relaxed_shapes(values: Iterable[str]) -> RelaxedShapes | None:
    """Length-bucketed per-position templates for identifier-ish columns the
    strict detector rejects, or None.

    Last-resort route for LLM-echo-saturated free-text pools (2026-07-22 b2
    E2E: CHANGE_USERID — the model returned the shown exemplars verbatim on
    every escalation attempt, killing the whole run). Relaxations vs
    :func:`detect_identifier_shape`: mixed lengths become weighted buckets,
    there is no minimum length, and a position whose characters fit no
    known class falls back to the observed character set at that position
    instead of disqualifying the column. The whitespace (prose) guard and
    the two-value minimum stay — and apply per bucket, because a
    single-value bucket is all-literal and can only regenerate its own
    observed value, which any novelty filter must reject.
    """
    vals = [v for v in values if v]
    if len(vals) < _MIN_VALUES:
        return None
    if any(" " in v or "\t" in v for v in vals):
        return None
    buckets: dict[int, list[str]] = {}
    for v in vals:
        buckets.setdefault(len(v), []).append(v)
    shapes: list[tuple[int, tuple[str, ...]]] = []
    for length, bucket in sorted(buckets.items()):
        if len(bucket) < _MIN_VALUES:
            continue
        shape: list[str] = []
        for i in range(length):
            chars = {v[i] for v in bucket}
            if len(chars) == 1:
                shape.append(next(iter(chars)))
                continue
            for cls in _CHAR_CLASSES:
                if chars <= set(cls):
                    shape.append(cls)
                    break
            else:
                shape.append("".join(sorted(chars)))
        shapes.append((len(bucket), tuple(shape)))
    return tuple(shapes) or None


def relaxed_shape_lengths(shapes: RelaxedShapes) -> set[int]:
    """Observed length buckets of a relaxed template set."""
    return {len(shape) for _, shape in shapes}


def relaxed_shape_charset(shapes: RelaxedShapes) -> set[str]:
    """Union of every character any template position can emit.

    Literals contribute themselves; class positions contribute the class.
    A candidate value drawn from characters OUTSIDE this union cannot be
    in-format for the column (the templates were built from every observed
    value), which is what makes it a cheap plausibility gate for
    LLM-generated pool candidates."""
    chars: set[str] = set()
    for _, shape in shapes:
        for entry in shape:
            chars.update(entry)
    return chars


def relaxed_shapes_pattern(shapes: RelaxedShapes) -> str:
    """Conservative anchored regex accepting the shapes' length buckets over
    their union charset — for vLLM structured-output ``pattern`` guidance.

    Deliberately looser than the per-position templates (a per-position
    regex would force near-verbatim reproduction and reintroduce the
    memorization pressure the novelty filter exists to stop): any character
    from the union charset, at any observed bucket length. Junk like
    'UUID-…' or column-name echoes is unrepresentable; novel in-charset
    combinations remain free."""
    charset = sorted(relaxed_shape_charset(shapes))
    cls = "".join(re.escape(c) for c in charset)
    lengths = sorted(relaxed_shape_lengths(shapes))
    alternation = "|".join(f"[{cls}]{{{n}}}" for n in lengths)
    return f"^(?:{alternation})$"


def sample_relaxed_identifier(
    shapes: RelaxedShapes, pick: Callable[[int], int]
) -> str:
    """One value from a relaxed template: draw a length bucket proportionally
    to its observed weight, then fill it position-by-position."""
    total = sum(w for w, _ in shapes)
    r = pick(total)
    for w, shape in shapes:
        if r < w:
            return sample_identifier(shape, pick)
        r -= w
    return sample_identifier(shapes[-1][1], pick)


__all__ = [
    "build_relaxed_shapes",
    "detect_identifier_shape",
    "detect_temporal_format",
    "relaxed_shape_charset",
    "relaxed_shape_lengths",
    "relaxed_shapes_pattern",
    "sample_identifier",
    "sample_relaxed_identifier",
]
