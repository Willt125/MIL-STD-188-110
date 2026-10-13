"""Consistency tests for the transcribed spec tables (M1).

These tests do not test code; they test our *transcription* and our *reading* of the
spec. Each one checks a table against a property the spec states or implies somewhere
else, so that a typo or misreading shows up as a contradiction.
"""

from fractions import Fraction
from math import gcd

import numpy as np
import pytest

from m110c.spec import tables as T


def bits_msb_first(value: int, width: int) -> list[int]:
    return [(value >> (width - 1 - i)) & 1 for i in range(width)]


def as_phasors(tribits) -> np.ndarray:
    """Tribit numbers -> unit 8-PSK phasors (§5.3.2.3.9: 45 degrees per tribit)."""
    return np.exp(1j * np.pi / 4 * np.asarray(tribits))


# --- §5.3.2.3.1 EOM -----------------------------------------------------------


def test_int_011_eom_first_eight_bits_match_spec_example():
    """INT-011: most significant digit first."""
    # "Thus the first eight bits are, left to right, 0100 1011."
    assert bits_msb_first(T.EOM_SEQUENCE, T.EOM_BITS)[:8] == [0, 1, 0, 0, 1, 0, 1, 1]


def test_eom_is_32_bits():
    assert T.EOM_SEQUENCE.bit_length() <= T.EOM_BITS == 32


# --- Double entry -------------------------------------------------------------
# Some constants appear only once in the spec, so no other table can cross-check them.
# They are re-typed here, by hand and in a different form from tables.py, from the
# rendered PDF. A typo now has to be made twice, identically, to slip through.


def test_double_entry_eom_sequence():
    # §5.3.2.3.1 "4B65A5B2", re-typed as binary nibble by nibble.
    typed = "0100 1011 0110 0101 1010 0101 1011 0010"
    assert bits_msb_first(T.EOM_SEQUENCE, 32) == [int(b) for b in typed.replace(" ", "")]


def test_double_entry_sync_scrambler_sequence():
    # §5.3.2.3.8.2 as rendered on page 37 of the PDF, with spaces between digits.
    typed = "7 4 3 0 5 1 5 0 2 2 1 1 5 7 4 3 5 0 2 6 2 1 6 2 0 0 5 0 5 2 6 6"
    assert T.SYNC_SCRAMBLER_SEQUENCE == tuple(int(d) for d in typed.split())


def test_double_entry_data_scrambler_feedback_matches_polynomial():
    """Figure 6 read twice: once as feedback cells, once as a polynomial (INT-003).

    In a Galois register shifting toward the MSB, the bit leaving cell N-1 enters cell 0
    (the x^0 term) and is XORed into cell k for every other x^k term; x^N is the register
    length."""
    cells = set(T.DATA_SCRAMBLER_FEEDBACK_CELLS) | {0, T.DATA_SCRAMBLER_BITS}
    assert cells == set(T.DATA_SCRAMBLER_POLYNOMIAL_EXPONENTS)


def test_double_entry_scalars():
    # §5.3.2.3.2 "144 bits (for coder flush)"; §5.3.2.3.8.1 "shifted eight times",
    # "After 160 transmit symbols"; Figure 4 "CONSTRAINT LENGTH = 7".
    assert T.CODER_FLUSH_BITS == 144
    assert T.DATA_SCRAMBLER_SHIFTS_PER_SYMBOL == 8
    assert T.DATA_SCRAMBLER_PERIOD == 160
    assert T.CONSTRAINT_LENGTH == 7


def test_double_entry_generator_polynomials():
    # Figure 4: "FOR T1 X6+X4+X3+X+1", "FOR T2 X6+X5+X4+X3+1".
    assert T.GENERATOR_T1_EXPONENTS == (6, 4, 3, 1, 0)
    assert T.GENERATOR_T2_EXPONENTS == (6, 5, 4, 3, 0)


# --- §5.3.2.3.8 scramblers ----------------------------------------------------


def test_sync_scrambler_sequence_shape():
    seq = T.SYNC_SCRAMBLER_SEQUENCE
    assert len(seq) == 32  # "repeat every 32 transmitted symbols"
    assert seq[0] == 7 and seq[-1] == 6  # "7 shall always be used first and 6 ... last"
    assert all(0 <= s <= 7 for s in seq)


def test_data_scrambler_init_matches_binary_in_text():
    # "101110101101 (binary) or BAD (hexadecimal)"
    assert T.DATA_SCRAMBLER_INIT == 0b101110101101 == 0xBAD
    assert T.DATA_SCRAMBLER_INIT.bit_length() <= T.DATA_SCRAMBLER_BITS


def test_int_003_scrambler_cells_within_register():
    cells = T.DATA_SCRAMBLER_FEEDBACK_CELLS + T.DATA_SCRAMBLER_OUTPUT_CELLS
    assert all(0 <= c < T.DATA_SCRAMBLER_BITS for c in cells)


# --- §5.3.2.3.6 modified-Gray decoder ----------------------------------------


def test_table_viii_is_a_permutation():
    assert sorted(T.TABLE_VIII.values()) == list(range(8))
    assert len(T.TABLE_VIII) == 8


def test_table_viii_has_gray_property_on_the_8psk_circle():
    """§5.3.2.3.6 note: MGD guarantees "only one bit is in error when symbol errors
    involving adjacent phases are made". With tribit k at phase 45k (Figure 5), the
    input bits of adjacent tribits k and k+1 (mod 8) must differ in exactly one bit."""
    inverse = {v: bits for bits, v in T.TABLE_VIII.items()}
    for k in range(8):
        a, b = inverse[k], inverse[(k + 1) % 8]
        assert sum(x != y for x, y in zip(a, b, strict=True)) == 1, (k, a, b)


def test_int_020_table_ix_with_figure_5_has_gray_property():
    """INT-020. Same property for dibits: Table IX output goes to tribits 0,2,4,6 (Figure 5)."""
    inverse = {T.FIGURE_5_TWO_BIT[v]: bits for bits, v in T.TABLE_IX.items()}
    phases = sorted(inverse)
    assert phases == [0, 2, 4, 6]
    for i, p in enumerate(phases):
        a, b = inverse[p], inverse[phases[(i + 1) % 4]]
        assert sum(x != y for x, y in zip(a, b, strict=True)) == 1


def test_int_020_figure_5_one_bit_symbols_are_antipodal():
    """INT-020."""
    assert T.FIGURE_5_ONE_BIT == {0: 0, 1: 4}


# --- Tables X and XIII: orthogonal symbol sets -------------------------------


@pytest.mark.parametrize("table", [T.TABLE_X_NORMAL, T.TABLE_X_EXCEPTIONAL, T.TABLE_XIII])
def test_mapping_patterns_are_32_tribits_of_0_or_4(table):
    for pattern in table.values():
        assert len(pattern) == 32
        assert set(pattern) <= {0, 4}


@pytest.mark.parametrize(
    "table", [T.TABLE_X_NORMAL, T.TABLE_X_EXCEPTIONAL, T.TABLE_XIII], ids=["Xa", "Xb", "XIII"]
)
def test_mapping_patterns_are_mutually_orthogonal(table):
    """§5.3.2.3.7.1.1: "the resultant output is one of four orthogonal waveforms"."""
    waves = np.array([as_phasors(p) for p in table.values()])
    gram = waves @ waves.conj().T
    assert np.allclose(gram, 32 * np.eye(len(table)))


def test_table_x_normal_sets_equal_first_half_of_table_xiii():
    # A cross-check between two tables typed in separately.
    for dibit in range(4):
        assert T.TABLE_X_NORMAL[dibit] == T.TABLE_XIII[dibit]


def test_table_x_exceptional_sets_equal_second_half_of_table_xiii():
    for dibit in range(4):
        assert T.TABLE_X_EXCEPTIONAL[dibit] == T.TABLE_XIII[0b100 | dibit]


def test_table_xiii_is_period_8_so_16_tribit_d_patterns_are_well_defined():
    """§5.3.2.3.7.1.2: D patterns are Table XIII "repeated twice rather than four times"."""
    for pattern in T.TABLE_XIII.values():
        assert pattern == pattern[:8] * 4
    assert T.D_PATTERN_TRIBITS == 16


# --- Tables XI and XII: preamble designators and counts ----------------------


def test_table_xi_pairs_are_unique_and_distinct_from_non_data_codes():
    pairs = list(T.TABLE_XI.values())
    assert len(set(pairs)) == len(pairs)
    assert T.TABLE_XI_DIGITAL_VOICE not in pairs
    for reserved in T.TABLE_XI_APPLICATION_SPECIFIC:
        assert reserved not in pairs


def test_table_xi_designators_are_three_bit_symbols_with_msb_set():
    for d1, d2 in T.TABLE_XI.values():
        assert 4 <= d1 <= 7 and 4 <= d2 <= 7


def count_symbols_by_text_rule(count: int) -> tuple[int, int, int]:
    """§5.3.2.3.7.2.1 text: split the 6-bit count into 2-bit values, prefix a 1."""
    return tuple(T.COUNT_SYMBOL_PREFIX | ((count >> shift) & 0b11) for shift in (4, 2, 0))


def test_int_001_text_rule_reproduces_the_worked_note():
    # INT-001.
    # "The converted count of 23 (010111) would have values of 5, 5, and 7"
    assert count_symbols_by_text_rule(23) == (5, 5, 7)


def test_int_001_table_xii_as_printed_contradicts_the_note():
    """Pins the erratum: Table XII as printed would give 6, 6, 7 for count 23.

    If this test fails, someone has "corrected" the transcription of Table XII; it
    must stay as printed, and the encoder must use the text rule instead."""
    by_table = tuple(T.TABLE_XII_AS_PRINTED[(23 >> s) & 0b11] for s in (4, 2, 0))
    assert by_table == (6, 6, 7)
    assert by_table != count_symbols_by_text_rule(23)


def test_preamble_count_fits_three_two_bit_fields():
    assert max(T.PREAMBLE_COUNT_START.values()) < 2**6


def test_preamble_segment_layout():
    n = len(T.PREAMBLE_FIXED_PREFIX) + 2 + 3 + len(T.PREAMBLE_FIXED_SUFFIX)  # D1 D2 C1-C3
    assert n == T.PREAMBLE_CHANNEL_SYMBOLS_PER_SEGMENT == 15
    # 15 channel symbols x 32 tribits = 480 symbols = 200 ms at 2400 symbols/s
    assert n * 32 == T.PREAMBLE_SEGMENT_SECONDS * T.SYMBOL_RATE


# --- Tables V, VI, VII, XV: rates and sizes ----------------------------------


@pytest.mark.parametrize("rate", T.DATA_RATES)
def test_table_v_and_table_xv_agree_on_code_rate(rate):
    assert T.TABLE_V[rate].effective_code_rate == T.TABLE_XV[rate].coding_rate


@pytest.mark.parametrize("rate", T.DATA_RATES)
def test_table_xv_channel_rate_equals_user_rate_over_code_rate(rate):
    code_rate = T.TABLE_XV[rate].coding_rate or Fraction(1)
    assert T.TABLE_XV[rate].channel_rate == rate / code_rate


@pytest.mark.parametrize("rate", T.DATA_RATES)
def test_table_xv_channel_rate_equals_symbol_throughput(rate):
    """Channel rate = 2400 sym/s x (fraction of symbols that carry data) x bits/symbol."""
    row = T.TABLE_XV[rate]
    if row.unknown_per_frame is None:  # 75 bps: every symbol carries data, 32 per dibit
        data_fraction = Fraction(1, row.tribits_per_channel_symbol)
    else:
        data_fraction = Fraction(row.unknown_per_frame, row.unknown_per_frame + row.known_per_frame)
    assert T.SYMBOL_RATE * data_fraction * row.bits_per_channel_symbol == row.channel_rate


@pytest.mark.parametrize("rate", T.TABLE_VII)
def test_table_vii_and_table_xv_agree_on_bits_per_symbol(rate):
    assert T.TABLE_VII[rate] == T.TABLE_XV[rate].bits_per_channel_symbol


def test_table_v_repetition_matches_code_rate():
    for rate, row in T.TABLE_V.items():
        if row.effective_code_rate is not None:
            assert row.effective_code_rate * row.repetitions == Fraction(1, 2), rate


@pytest.mark.parametrize("rate", T.TABLE_VI)
@pytest.mark.parametrize("setting", ["short", "long"])
def test_interleaver_load_step_visits_every_row(rate, setting):
    """Row steps of 9 (mod 40) and 7 (mod 20 or 10) only fill every row if coprime."""
    rows = T.TABLE_VI[rate][setting].rows
    step = T.INTERLEAVE_LOAD_ROW_STEP_75 if rate == 75 else T.INTERLEAVE_LOAD_ROW_STEP
    assert gcd(step, rows) == 1


@pytest.mark.parametrize("rate", T.TABLE_VI)
@pytest.mark.parametrize("setting", ["short", "long"])
def test_table_vi_rows_match_moduli_stated_in_load_text(rate, setting):
    """§5.3.2.3.4 states the row modulus in prose ("modulo 40", "modulo 20", "modulo 10").
    This is the only independent check that Table VI's rows and columns are not swapped."""
    if rate == 75:
        expected = T.INTERLEAVE_LOAD_ROW_MODULUS_75[setting]
    else:
        expected = T.INTERLEAVE_LOAD_ROW_MODULUS
    assert T.TABLE_VI[rate][setting].rows == expected


def test_fetch_decrement_reproduces_spec_example():
    """§5.3.2.3.5: at 2400 bps long, "the second bit comes from row 1, column 559, and
    the third bit from row 2, column 542"."""
    cols = T.TABLE_VI[2400]["long"].cols
    d = T.INTERLEAVE_FETCH_COL_DECREMENT
    assert ((0 - d) % cols, (0 - 2 * d) % cols) == (559, 542)


def test_table_xvi_rows_refer_to_supported_rates():
    for row in T.TABLE_XVI:
        assert row.rate in T.DATA_RATES
        assert (row.multipath_ms is None) == (not row.fading)
