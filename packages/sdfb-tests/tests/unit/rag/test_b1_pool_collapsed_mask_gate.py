"""Collapsed-mask candidate gate for shape-rigid whitespace columns (§4c).

2026-08-09 B_TABLE R1, COL_038: the LLM normalized the source's literal
three-space run to one space and the pool ladder accepted all 512 of them —
the relaxed-shapes gate is inactive for any column containing whitespace
(`build_relaxed_shapes` → None), so nothing checked format at all. The gate
now falls back to collapsed-mask membership when the shape mix can template:
digit/letter run lengths may vary (novelty stays possible), whitespace runs
and punctuation must match an observed mask exactly.
"""

from __future__ import annotations

from sdfb_core.engines.b1_rag.engine import _pool_llm_yield
from sdfb_core.engines.b1_rag.profile import ColumnKind, ColumnProfile
from sdfb_core.engines.text_shapes import build_shape_mix

# COL_038-class: fixed 5-letter head, digit, THREE literal spaces, 2-letter
# code, digit run, letter tail.
_PADDED = tuple(f"EXSPF{i % 10}   CS{i:010d}B" for i in range(60))


def _padded_profile() -> ColumnProfile:
    return ColumnProfile(
        name="REF_CODE",
        bq_type="STRING",
        kind=ColumnKind.FREE_TEXT,
        nullable=False,
        null_fraction=0.0,
        observed_values=_PADDED,
        text_examples=_PADDED[:2],
        shape_mix=build_shape_mix(list(_PADDED)),
    )


class _SpaceCollapsingClient:
    """Mimics the R1 failure: mostly whitespace-normalized values."""

    def generate_json(self, prompt, json_schema, **kw):
        return [{"values": [
            "EXSPF7 CS9111111111B",      # single space — the R1 failure mode
            "EXSPF8 DN9222222222B",      # single space
            "EXSPF9   DN9333333333B",    # correct triple space, novel
            "EXSPF1CS9444444444B",       # dropped spaces entirely
        ]}]


def test_gate_rejects_normalized_whitespace_keeps_true_shape():
    y = _pool_llm_yield(
        _SpaceCollapsingClient(), "p", {}, _padded_profile(),
        ["EXSPF0   CS0000000000B"], target=8,
    )
    assert y.pool == ["EXSPF9   DN9333333333B"]
    assert y.format_rejected >= 3


def test_gate_stays_off_for_word_diverse_prose():
    # Genuinely free prose: word counts AND word lengths vary, so masks are
    # near-singleton buckets, the mix cannot template, the gate stays off.
    import random as _r

    rng = _r.Random(4)
    vocab = [
        "outage", "glitch", "failure", "spike", "lag", "crash",
        "incident", "router", "db", "saturation", "flap", "link",
    ]
    vals = tuple(
        " ".join(rng.choice(vocab) for _ in range(rng.randrange(3, 9)))
        + f" ticket {i}"
        for i in range(60)
    )
    prof = ColumnProfile(
        name="notes",
        bq_type="STRING",
        kind=ColumnKind.FREE_TEXT,
        nullable=False,
        null_fraction=0.0,
        observed_values=vals,
        text_examples=vals[:2],
        shape_mix=build_shape_mix(list(vals)),
    )

    class _ProseClient:
        def generate_json(self, prompt, json_schema, **kw):
            return [{"values": ["a fresh synthetic outage note entirely new"]}]

    y = _pool_llm_yield(_ProseClient(), "p", {}, prof, [vals[0]], target=4)
    assert "a fresh synthetic outage note entirely new" in y.pool
