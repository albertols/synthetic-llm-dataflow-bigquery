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
from typing import TYPE_CHECKING

from sdfb_core.engines.temporal_parse import parse_temporal_string

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
# A shape must carry at least this many varying (class) positions,
# mass-weighted, before expansion can diversify it — an all-literal
# template can only regenerate its own observed values.
_EXPAND_MIN_CLASS_POSITIONS = 2.0
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
            parse_temporal_string(vals[0], fmt)
        except ValueError:
            continue
        try:
            for v in vals[1:]:
                parse_temporal_string(v, fmt)
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


def build_shape_mix(
    values: Iterable[str], top_k: int = 8
) -> RelaxedShapes | None:
    """Per-EXACT-SHAPE templates with observed weights, or None.

    The 2026-08-04 crosscheck's core recall finding: the primary free-text
    routes collapse a column's observed *mix* of formats to one or two.
    This builder groups values by their exact character-class mask
    (digit→``9``, upper→``A``, lower→``a``, everything else literal —
    the crosscheck's own masking), keeps the ``top_k`` heaviest masks with
    their observed counts, and emits per-position templates compatible with
    :func:`sample_relaxed_identifier` — a draw reproduces the observed
    shape mass by construction.

    Differences vs :func:`build_relaxed_shapes` (which stays the LLM-pool
    gate): buckets are per-mask, not per-length; single-value buckets are
    KEPT (a mask generalizes, an all-literal length bucket does not);
    spaces are allowed and stay literal (this feeds free-text columns).
    """
    vals = [v for v in values if v]
    if not vals:
        return None
    buckets: dict[str, list[str]] = {}
    for v in vals:
        mask = "".join(
            "9" if ch.isdigit() else "A" if ch.isupper() else "a" if ch.islower() else ch
            for ch in v
        )
        buckets.setdefault(mask, []).append(v)
    heaviest = sorted(buckets.values(), key=len, reverse=True)[:top_k]
    shapes: list[tuple[int, tuple[str, ...]]] = []
    for bucket in heaviest:
        length = len(bucket[0])
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


def shape_mix_is_identifier_like(shapes: RelaxedShapes | None) -> bool:
    """True when the mix is code-like and expandable: no CLASS position can
    emit whitespace (variable padding is prose), and the mass-weighted
    average shape carries at least two class positions (an all-literal
    template can only regenerate its own observed values — nothing to
    expand). Literal positions do NOT disqualify — not even literal
    whitespace: fixed padding is part of a code's format, and refusing it
    pinned space-padded reference columns at the pool-cap diversity
    ceiling (2026-08-09 B_TABLE R1: 7 columns at distinct ≈ 513 vs source
    45k-146k, COL_038-class). Same whitespace rule as
    :func:`shape_mix_can_template`, to which this now delegates.
    """
    return shape_mix_can_template(shapes)


def shape_mix_can_template(shapes: RelaxedShapes | None) -> bool:
    """True when the mix can drive the *fallback pool* template.

    Looser than :func:`shape_mix_is_identifier_like` in exactly one way:
    whitespace is allowed as a LITERAL position (fixed padding is part of a
    code's format — the 2026-08-05 B_TABLE crosscheck's COL_026 carries 17
    literal leading spaces and the length-bucket relaxation rejected the
    whole column, leaving 0% shape recall). A CLASS position containing
    whitespace still disqualifies — variable padding is prose, not a code —
    and the mass-weighted two-class-position minimum stays, because an
    all-literal template can only regenerate its observed values.
    """
    if not shapes:
        return False
    total_weight = 0.0
    class_weight = 0.0
    for weight, shape in shapes:
        class_count = 0
        for entry in shape:
            if len(entry) > 1:
                if " " in entry or "\t" in entry:
                    return False
                class_count += 1
        total_weight += weight
        class_weight += weight * class_count
    return (
        total_weight > 0
        and class_weight / total_weight >= _EXPAND_MIN_CLASS_POSITIONS
    )


def _mask_char(ch: str) -> str:
    if ch.isdigit():
        return "9"
    if ch.isupper():
        return "A"
    if ch.islower():
        return "a"
    return ch


_CLASS_RUN = re.compile(r"9{2,}|A{2,}|a{2,}")


def collapsed_mask(value: str) -> str:
    """Run-collapsed character-class mask: digit/letter runs of ≥2 collapse
    to ``9+``/``A+``/``a+``; whitespace and punctuation stay literal, run
    lengths included.

    The candidate-gate key for shape-rigid whitespace columns (wave-2 §4c):
    letting digit/letter run LENGTHS vary keeps legitimate LLM diversity
    (lexical variation, shorter numbers), while a normalized whitespace run
    (`` ␣␣␣ `` → `` ␣ ``, the 2026-08-09 B_TABLE COL_038 failure) or a
    dropped delimiter changes the mask and is rejected.
    """
    return _CLASS_RUN.sub(
        lambda m: m.group(0)[0] + "+", "".join(_mask_char(c) for c in value)
    )


# A (weight, exact-mask) table over a column's distinct values.
MaskTable = tuple[tuple[int, str], ...]

_MASK_TABLE_CAP = 1024


def build_mask_table(
    values: Iterable[str], cap: int = _MASK_TABLE_CAP
) -> MaskTable | None:
    """Exact-mask frequency table over distinct values, heaviest first.

    The full mask DISTRIBUTION, not the top-8 templates: high-entropy
    identifier columns (2026-08-09 A_TABLE R1, COL_001: top-8 masks cover
    ~20% of 52k distinct) need whole-mask draws to reproduce the source
    mask marginal — the collapsed per-position template scrambles it (0%
    recall). ``cap`` bounds memory; ties break lexicographically for
    determinism.
    """
    distinct = list(dict.fromkeys(v for v in values if v))
    if len(distinct) < _MIN_VALUES:
        return None
    counts: dict[str, int] = {}
    for v in distinct:
        mask = "".join(_mask_char(c) for c in v)
        counts[mask] = counts.get(mask, 0) + 1
    heaviest = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]
    return tuple((n, mask) for mask, n in heaviest)


def mask_alphabets(values: Iterable[str]) -> dict[str, str]:
    """Observed characters per mask class, column-wide.

    A mask fill must stay inside the column's real alphabet — hex
    identifiers must not grow ``G-Z`` just because the mask says
    "uppercase" (COL_001 stays hexadecimal). Keys present only for classes
    the column actually exhibits.
    """
    digits: set[str] = set()
    uppers: set[str] = set()
    lowers: set[str] = set()
    for v in values:
        for ch in v:
            if ch.isdigit():
                digits.add(ch)
            elif ch.isupper():
                uppers.add(ch)
            elif ch.islower():
                lowers.add(ch)
    out: dict[str, str] = {}
    if digits:
        out["9"] = "".join(sorted(digits))
    if uppers:
        out["A"] = "".join(sorted(uppers))
    if lowers:
        out["a"] = "".join(sorted(lowers))
    return out


def sample_from_mask(
    mask: str, alphabets: dict[str, str], pick: Callable[[int], int]
) -> str:
    """One value from an exact mask: class symbols draw from the column's
    observed alphabets (full class sets as last resort), literals pass
    through."""
    out: list[str] = []
    for ch in mask:
        if ch == "9":
            alpha = alphabets.get("9", string.digits)
        elif ch == "A":
            alpha = alphabets.get("A", string.ascii_uppercase)
        elif ch == "a":
            alpha = alphabets.get("a", string.ascii_lowercase)
        else:
            out.append(ch)
            continue
        out.append(alpha[pick(len(alpha))])
    return "".join(out)


def sample_mask_table(
    table: MaskTable, alphabets: dict[str, str], pick: Callable[[int], int]
) -> str:
    """One value from a mask table: draw a mask proportionally to its
    distinct-value weight, then fill it via :func:`sample_from_mask`."""
    total = sum(w for w, _ in table)
    r = pick(total)
    for w, mask in table:
        if r < w:
            return sample_from_mask(mask, alphabets, pick)
        r -= w
    return sample_from_mask(table[-1][1], alphabets, pick)


def identifier_sampler(
    shape: tuple[str, ...],
    shape_mix: RelaxedShapes | None,
    observed_values: Iterable[object],
    pick: Callable[[int], int],
    coverage_min: float = 0.5,
) -> Callable[[], str]:
    """Per-row identifier generator shared by both engines (wave-2 §4a).

    Mask mix when the top masks cover most distinct values (rigid mask
    families, the 2026-08-07 fix); otherwise a whole-mask draw from the
    FULL mask table filled from the column's observed alphabets — the
    collapsed per-position template scrambled long-tail mask families
    (2026-08-09 A_TABLE R1, COL_001: 0% mask recall). The collapsed
    template stays as the last resort. Draws retry x3 against the observed
    set so novelty pressure stays with the sampler.
    """
    observed = {str(v) for v in observed_values}
    non_empty = [v for v in observed if v]
    if shape_mix:
        distinct = len(non_empty)
        coverage = (
            sum(w for w, _ in shape_mix) / distinct if distinct else 0.0
        )
        if coverage >= coverage_min:

            def _from_mix() -> str:
                v = ""
                for _ in range(3):
                    v = sample_relaxed_identifier(shape_mix, pick)
                    if v not in observed:
                        break
                return v

            return _from_mix
    table = build_mask_table(non_empty)
    if table is not None:
        alphabets = mask_alphabets(non_empty)

        def _from_table() -> str:
            v = ""
            for _ in range(3):
                v = sample_mask_table(table, alphabets, pick)
                if v not in observed:
                    break
            return v

        return _from_table
    return lambda: sample_identifier(shape, pick)


_DIGIT_RUN = re.compile(r"\d{2,}")


def mutate_digit_runs(value: str, pick: Callable[[int], int]) -> str:
    """Replace every maximal run of ≥2 digits with fresh digits of the same
    length. The first digit keeps its zero/nonzero-ness so fixed prefixes
    like ``0001…`` survive; runs of one digit and non-digits are untouched.
    The cardinality lever of `--freetext_expansion=all` (2026-08-05 spec C3).
    """

    def _fresh(m: re.Match[str]) -> str:
        run = m.group(0)
        first = "0" if run[0] == "0" else "123456789"[pick(9)]
        rest = "".join("0123456789"[pick(10)] for _ in run[1:])
        return first + rest

    return _DIGIT_RUN.sub(_fresh, value)


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


def length_hint(values: Iterable[object], *, min_samples: int = 8) -> str:
    """Measured length band for free-text pool prompts.

    Steers the LLM's length marginal toward the source's observed p05-p95
    band (the 2026-08-04 crosscheck: synthetic prose ran systematically
    shorter than source). Returns "" below ``min_samples`` or when the band
    is degenerate — fixed-width values already carry their length in the
    shape template. Callers must APPEND this after the shared instruction
    prefix: a per-column constant suffix keeps vLLM automatic prefix caching
    serving the common prefix (ADR 0018).
    """
    lengths = sorted(len(str(v)) for v in values)
    if len(lengths) < min_samples:
        return ""
    last = len(lengths) - 1
    p05 = lengths[int(0.05 * last)]
    p50 = lengths[int(0.50 * last)]
    p95 = lengths[int(0.95 * last)]
    if p05 == p95:
        return ""
    return f"Most values are {p05}-{p95} characters long (median {p50})."


__all__ = [
    "build_mask_table",
    "build_relaxed_shapes",
    "build_shape_mix",
    "collapsed_mask",
    "detect_identifier_shape",
    "detect_temporal_format",
    "identifier_sampler",
    "length_hint",
    "mask_alphabets",
    "mutate_digit_runs",
    "relaxed_shape_charset",
    "relaxed_shape_lengths",
    "relaxed_shapes_pattern",
    "sample_from_mask",
    "sample_identifier",
    "sample_mask_table",
    "sample_relaxed_identifier",
    "shape_mix_can_template",
    "shape_mix_is_identifier_like",
]
