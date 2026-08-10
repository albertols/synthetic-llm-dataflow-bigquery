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

from sdfb_core.engines.text_shapes import (
    build_mask_table,
    build_shape_mix,
    collapsed_mask,
    mask_alphabets,
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
