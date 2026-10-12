"""Tests for the data and sync scramblers (M4)."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from oracle import data_scrambler as oracle

from m110c import scrambler as S
from m110c.spec import tables as T


def test_data_sequence_matches_literal_figure_6_oracle():
    assert S.data_sequence(0, 3 * 160).tolist() == oracle.sequence(3 * 160)


def test_first_symbol_trace_through_figure_6():
    """The eight shifts before the first output, for a reader to follow on paper.

    Register shown cell 11 .. cell 0 (= B1 .. B12 of the figure). Step 1 by hand:
    0xBAD = 1011 1010 1101; shift left, the leaving 1 is XORed into cells 0, 1, 4, 6:
    0111 0101 1010 ^ 0000 0101 0011 = 0111 0000 1001.
    """
    expected = [
        "011100001001",
        "111000010010",
        "110001110111",
        "100010111101",
        "000100101001",
        "001001010010",
        "010010100100",
        "100101001000",
    ]
    state = T.DATA_SCRAMBLER_INIT
    for step in expected:
        state = S.lfsr_shift(state)
        assert format(state, "012b") == step
    # Cells 2, 1, 0 of 1001 0100 1000 are 0, 0, 0: the first scrambling value is 0.
    assert S.lfsr_output(state) == 0 == S.DATA_SEQUENCE[0]


def test_first_sixteen_values_are_pinned():
    """Regression pin. These are *our* outputs, not values from the spec (which gives
    none); a real recording is the only external check of INT-003."""
    assert S.DATA_SEQUENCE[:16].tolist() == [0, 2, 4, 3, 3, 6, 4, 5, 7, 6, 7, 0, 5, 5, 4, 3]


def test_int_003_figure_6_register_is_maximal_length():
    """Supporting evidence for our reading of Figure 6. Without the 160-symbol reset the
    register visits all 4095 non-zero states, so x^12+x^6+x^4+x+1 is primitive, which is
    what a designer would choose. Most misreadings of the taps are not. (The mirrored
    reading is also primitive, so this cannot rule that one out.)"""
    state, period = T.DATA_SCRAMBLER_INIT, 0
    while True:
        state = S.lfsr_shift(state)
        period += 1
        if state == T.DATA_SCRAMBLER_INIT:
            break
    assert period == 2**12 - 1


def test_data_sequence_resets_every_160_symbols():
    seq = S.data_sequence(0, 800)
    for k in range(1, 5):
        assert np.array_equal(seq[160 * k : 160 * (k + 1)], seq[:160])
    # ... and does *not* happen to repeat sooner.
    assert not any(np.array_equal(seq[p : p + 160], seq[:160]) for p in range(1, 160))


def test_data_sequence_uses_all_eight_values_roughly_evenly():
    counts = np.bincount(S.DATA_SEQUENCE, minlength=8)
    assert counts.sum() == 160
    assert counts.min() >= 10 and counts.max() <= 30  # 20 each would be perfectly even


@given(st.integers(0, 10_000), st.integers(0, 500))
def test_int_004_data_sequence_is_positional(start, count):
    """Value i depends only on i, so a streaming framer just remembers its position."""
    whole = S.data_sequence(0, start + count)
    assert np.array_equal(S.data_sequence(start, count), whole[start:])


def test_sync_sequence():
    seq = S.sync_sequence(0, 64)
    assert seq[:32].tolist() == list(T.SYNC_SCRAMBLER_SEQUENCE)
    assert np.array_equal(seq[32:], seq[:32])
    # A 480-symbol preamble segment is exactly 15 periods, so every segment starts at 7.
    assert S.sync_sequence(480, 1)[0] == 7


@given(st.lists(st.integers(0, 7), max_size=200), st.integers(0, 1000))
def test_descramble_inverts_scramble(tribits, start):
    seq = S.data_sequence(start, len(tribits))
    scrambled = S.scramble(np.array(tribits, dtype=np.uint8), seq)
    assert scrambled.dtype == np.uint8 and (scrambled < 8).all()
    assert S.descramble(scrambled, seq).tolist() == tribits


def test_scrambling_is_addition_modulo_8():
    assert S.scramble(np.array([7, 4, 0]), np.array([1, 5, 0])).tolist() == [0, 1, 0]


@pytest.mark.parametrize("value", range(8))
def test_scrambling_spreads_a_constant_over_all_phases(value):
    """Why scrambling exists: a run of identical tribits (e.g. all-zero probes) is
    transmitted using every phase."""
    out = S.scramble(np.full(160, value, dtype=np.uint8), S.data_sequence(0, 160))
    assert set(out.tolist()) == set(range(8))
