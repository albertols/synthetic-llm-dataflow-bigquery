"""Mask-table sampling + collapsed masks (wave-2 design doc §4a/§4c).

COL_001 evidence (2026-08-09 A_TABLE R1): below top-8 mask coverage the
collapsed template scrambles digit/letter arrangement — 0% mask recall.
Drawing a whole observed mask and filling class positions from the column's
observed per-class alphabets reproduces the mask marginal by construction.

COL_038 evidence (2026-08-09 B_TABLE R1): the LLM normalized a literal
three-space run to one space and the (inactive) format gate accepted all of
it. The collapsed mask keeps whitespace runs literal while letting
digit/letter run lengths vary.
"""

import random
import re

from sdfb_core.engines.text_shapes import (
    build_mask_table,
    build_shape_mix,
    collapsed_mask,
    identifier_sampler,
    mask_alphabets,
    positional_alphabets,
    sample_from_mask,
    shape_mix_is_identifier_like,
)


def _mask(v: str) -> str:
    return "".join(
        "9" if c.isdigit() else "A" if c.isupper() else "a" if c.islower() else c
        for c in v
    )


class TestCollapsedMask:
    def test_alnum_runs_collapse_whitespace_stays_literal(self) -> None:
        assert collapsed_mask("EXSPF1   CS0951947980B") == "A+9   A+9+A"

    def test_single_symbol_runs_do_not_gain_plus(self) -> None:
        assert collapsed_mask("A1 B2") == "A9 A9"

    def test_punctuation_is_literal(self) -> None:
        assert collapsed_mask("TRF.EX-095019853") == "A+.A+-9+"

    def test_distinguishes_space_run_lengths(self) -> None:
        # The COL_038 failure: single-space output must NOT look like the
        # triple-space source.
        assert collapsed_mask("AB   CD") != collapsed_mask("AB CD")


class TestMaskTable:
    def test_weights_are_distinct_value_counts_heaviest_first(self) -> None:
        table = build_mask_table(["AB12", "CD34", "EF56", "12XY"])
        assert table is not None
        assert table[0] == (3, "AA99")
        assert table[1] == (1, "99AA")

    def test_weights_are_row_mass_not_distinct_counts(self) -> None:
        # 2026-08-11 R1 (COL_054/COL_015/COL_024-class): distinct-value
        # weighting inverted row-mass marginals — a heavily repeated value's
        # mask must outweigh a diverse-but-rare mask family.
        table = build_mask_table(
            ["BATCH"] * 8 + ["101A", "202B", "303C"]
        )
        assert table is not None
        assert table[0] == (8, "AAAAA")
        assert table[1] == (3, "999A")

    def test_cap_keeps_heaviest(self) -> None:
        values = [f"A{i:03d}" for i in range(50)] + ["9999", "8888"]
        table = build_mask_table(values, cap=1)
        assert table is not None
        assert len(table) == 1
        assert table[0] == (50, "A999")

    def test_too_few_values_is_none(self) -> None:
        assert build_mask_table(["X1"]) is None
        assert build_mask_table([]) is None


class TestMaskAlphabets:
    def test_alphabets_are_observed_chars_per_class(self) -> None:
        # Hex identifiers: uppercase alphabet is A-F only — a mask fill
        # must not emit G-Z (COL_001 stays hexadecimal).
        alphabets = mask_alphabets(["E2F3B715", "D0C4A9F1"])
        assert alphabets["A"] == "ABCDEF"
        assert alphabets["9"] == "01234579"

    def test_sample_fills_classes_and_keeps_literals(self) -> None:
        alphabets = {"9": "0123456789", "A": "ABCDEF"}
        rng = random.Random(5)
        for _ in range(30):
            v = sample_from_mask("A9-9A x", alphabets, rng.randrange)
            assert len(v) == 7
            assert v[0] in "ABCDEF" and v[1].isdigit()
            assert v[2] == "-" and v[3].isdigit() and v[4] in "ABCDEF"
            assert v[5:] == " x"


class TestPositionalAlphabets:
    """Per-position observed charsets (2026-08-11 A_TABLE R1, COL_001).

    Column-wide class alphabets scrambled fixed positional literals: every
    source value started with the literal run `E2F3` yet synthetic values
    opened with any observed hex character (shape recall 0.38, prefix lost).
    A position whose observed charset is a singleton is a literal by the
    same evidence rule `detect_identifier_shape` uses.
    """

    def test_singleton_positions_become_literals(self) -> None:
        pos = positional_alphabets(["E2F3AB", "E2F3CD", "E2F39F"])
        assert pos[6][0] == "E"
        assert pos[6][1] == "2"
        assert pos[6][2] == "F"
        assert pos[6][3] == "3"
        assert set(pos[6][4]) == {"A", "C", "9"}

    def test_single_value_length_buckets_are_skipped(self) -> None:
        # One value proves nothing about a shared per-position alphabet —
        # pinning it would regenerate the observed value verbatim.
        pos = positional_alphabets(["AB12", "CD34", "ONLYONE"])
        assert 4 in pos
        assert 7 not in pos

    def test_sample_from_mask_pins_positional_literals(self) -> None:
        values = [f"E2F3{i:02X}" for i in range(32)]
        alphabets = mask_alphabets(values)
        pos = positional_alphabets(values)
        rng = random.Random(11)
        for _ in range(50):
            v = sample_from_mask(
                "A9A999", alphabets, rng.randrange, positional=pos
            )
            assert v.startswith("E2F3")

    def test_sample_from_mask_intersects_position_with_class(self) -> None:
        # A class position draws from the characters observed AT THAT
        # POSITION, not from the column-wide class alphabet.
        values = ["A1X9", "B2X8", "C3X7"]
        alphabets = mask_alphabets(values)
        pos = positional_alphabets(values)
        rng = random.Random(3)
        for _ in range(30):
            v = sample_from_mask("A9A9", alphabets, rng.randrange, positional=pos)
            assert v[0] in "ABC"
            assert v[1] in "123"
            assert v[2] == "X"
            assert v[3] in "789"

    def test_identifier_sampler_preserves_fixed_prefix(self) -> None:
        # End-to-end through the mask-table path (coverage below the mix
        # pivot): every draw keeps the literal E2F3 prefix.
        rng = random.Random(9)
        values = list(
            dict.fromkeys(
                "E2F3"
                + "".join(rng.choice("0123456789ABCDEF") for _ in range(20))
                for _ in range(120)
            )
        )
        shape = tuple(["E", "2", "F", "3"] + ["0123456789ABCDEF"] * 20)
        draw = identifier_sampler(shape, None, values, rng.randrange)
        drawn = [draw() for _ in range(200)]
        assert all(v.startswith("E2F3") for v in drawn)
        assert not set(drawn) & set(values)

    def test_identifier_sampler_preserves_uuid_v4_nibbles(self) -> None:
        # COL_064-class (2026-08-11 A_TABLE R1): RFC 4122 v4 pins position
        # 14 to '4' and position 19 to the variant class {8,9,a,b} — the
        # column-wide fill emitted arbitrary hex there (shape precision
        # 0.10). No uuid special-case: positional evidence carries it.
        import uuid

        rng = random.Random(21)
        values = [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(200)]
        draw = identifier_sampler(tuple("x" * 36), None, values, rng.randrange)
        for _ in range(100):
            v = draw()
            assert re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                v,
            ), v


class TestIdentifierLikeLiteralSpaces:
    def test_literal_space_padding_is_identifier_like(self) -> None:
        # COL_038-class: rigid space-padded codes must expand per-row
        # instead of pinning at the pool cap.
        vals = [f"EXSPF{i%10}   CS{i:010d}B" for i in range(40)]
        assert shape_mix_is_identifier_like(build_shape_mix(vals))

    def test_prose_with_varying_masks_stays_excluded(self) -> None:
        # Word-diverse prose: singleton masks carry no class positions.
        assert not shape_mix_is_identifier_like(
            build_shape_mix(["SEG.DE CAMBIO 12", "ABONO CANON A 34"])
        )
