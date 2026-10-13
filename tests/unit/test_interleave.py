"""Tests for the block interleaver (M3)."""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from oracle import interleaver as oracle

from m110c import fec
from m110c.interleave import (
    Deinterleaver,
    Interleaver,
    fetch_positions,
    load_positions,
    permutation,
)
from m110c.spec import all_modes, mode

MODES = all_modes()
INTERLEAVED = [m for m in MODES if m.has_interleaver]
BYPASSED = [m for m in MODES if not m.has_interleaver]


def ids(modes):
    return [m.name for m in modes]


def row_col(flat, cols):
    return (int(flat) // cols, int(flat) % cols)


# --- Spec examples -------------------------------------------------------------------


def test_load_matches_spec_example():
    """§5.3.2.3.4: "the first bit is loaded into row 0, the next bit is loaded into row 9,
    the third bit is loaded into row 18, and the fourth bit into row 27"."""
    pos = load_positions(40, 576, 9)
    assert [row_col(p, 576) for p in pos[:4]] == [(0, 0), (9, 0), (18, 0), (27, 0)]
    # "This process continues until all 40 rows are loaded. The load then advances to column 1"
    assert row_col(pos[40], 576) == (0, 1)


def test_fetch_matches_spec_example():
    """§5.3.2.3.5: "for 2400 bps with a long interleave setting, the second bit comes from
    row 1, column 559, and the third bit from row 2, column 542"."""
    pos = fetch_positions(40, 576, 17)
    assert [row_col(p, 576) for p in pos[:3]] == [(0, 0), (1, 559), (2, 542)]


def test_fetch_restarts_one_column_right_of_last_diagonal():
    pos = fetch_positions(40, 576, 17)
    assert row_col(pos[40], 576) == (0, 1)
    assert row_col(pos[80], 576) == (0, 2)


@pytest.mark.parametrize("m", [mode(75, "long"), mode(75, "short")], ids=["75L", "75S"])
def test_int_007_75bps_uses_step_7(m):
    """INT-007: 75 bps loads rows in steps of 7 and fetches columns in steps of -7."""
    rows, cols = m.interleaver_rows, m.interleaver_cols
    load = load_positions(rows, cols, m.interleaver_load_row_step)
    assert [row_col(p, cols)[0] for p in load[:3]] == [0, 7, 14 % rows]
    fetch = fetch_positions(rows, cols, m.interleaver_fetch_col_decrement)
    assert row_col(fetch[1], cols) == (1, (0 - 7) % cols)


# --- Against the literal oracle -------------------------------------------------------


@pytest.mark.parametrize("m", INTERLEAVED, ids=ids(INTERLEAVED))
def test_permutation_matches_literal_oracle(m):
    """Feed the oracle the input *indices*; what comes out is the permutation itself.
    Covers the 75-bps variants and the fetch-then-reset reading (INT-007)."""
    n = m.interleaver_bits
    expected = oracle.interleave(
        list(range(n)),
        m.interleaver_rows,
        m.interleaver_cols,
        m.interleaver_load_row_step,
        m.interleaver_fetch_col_decrement,
    )
    assert permutation(m).tolist() == expected


@pytest.mark.parametrize("m", INTERLEAVED, ids=ids(INTERLEAVED))
def test_permutation_is_a_bijection(m):
    # INT-007: includes the 75-bps 20x36 and 10x9 matrices.
    perm = permutation(m)
    assert perm.size == m.interleaver_bits
    assert np.array_equal(np.sort(perm), np.arange(m.interleaver_bits))


# --- Round trips and streaming --------------------------------------------------------


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_deinterleave_inverts_interleave(m):
    rng = np.random.default_rng(1)
    n = 3 * (m.interleaver_bits or 1000)
    bits = rng.integers(0, 2, n).astype(np.uint8)
    tx = Interleaver(m).process(bits)
    assert tx.size == n
    assert np.array_equal(Deinterleaver(m).process(tx), bits)


@pytest.mark.parametrize("m", INTERLEAVED, ids=ids(INTERLEAVED))
def test_interleaver_actually_reorders(m):
    bits = np.arange(m.interleaver_bits)
    assert not np.array_equal(Interleaver(m).process(bits), bits)


def test_deinterleaver_handles_soft_values():
    m = mode(1200, "short")
    soft = np.random.default_rng(2).standard_normal(m.interleaver_bits)
    out = Deinterleaver(m).process(Interleaver(m).process(soft))
    assert out.dtype == soft.dtype
    assert np.array_equal(out, soft)


@pytest.mark.parametrize("m", BYPASSED, ids=ids(BYPASSED))
def test_zero_interleave_and_4800_are_bypassed(m):
    """§5.3.2.3.4: 0.0 s setting and 4800 bps bypass the interleaver."""
    bits = np.random.default_rng(3).integers(0, 2, 123).astype(np.uint8)
    il = Interleaver(m)
    assert il.block_size is None
    assert np.array_equal(il.process(bits), bits)
    il.flush()  # no block structure, nothing can be left over


@settings(max_examples=40, deadline=None)
@given(
    st.sampled_from(INTERLEAVED),
    st.lists(st.integers(1, 3000), min_size=1, max_size=12),
)
def test_streaming_is_chunk_invariant(m, chunk_sizes):
    """Splitting the input anywhere must not change the output. An empty write (which a
    pipe can produce) is interleaved after every real one."""
    n = 2 * m.interleaver_bits
    bits = np.random.default_rng(4).integers(0, 2, n).astype(np.uint8)
    reference = Interleaver(m).process(bits)
    il = Interleaver(m)
    pieces, i = [], 0
    for size in itertools.cycle(chunk_sizes):
        if i >= n:
            break
        pieces.append(il.process(bits[i : i + size]))
        pieces.append(il.process(bits[:0]))
        assert il.pending < m.interleaver_bits  # never more than one partial block
        i += size
    il.flush()
    assert np.array_equal(np.concatenate(pieces), reference)


def test_output_is_released_block_by_block():
    m = mode(600, "short")
    il = Interleaver(m)
    assert il.process(np.zeros(m.interleaver_bits - 1, np.uint8)).size == 0
    assert il.process(np.zeros(1, np.uint8)).size == m.interleaver_bits


def test_one_bit_at_a_time_is_not_quadratic():
    """A pipe may deliver one bit per call; a full long block must still be quick."""
    import time

    m = mode(2400, "long")
    il = Interleaver(m)
    bit = np.zeros(1, np.uint8)
    start = time.perf_counter()
    for _ in range(m.interleaver_bits):
        il.process(bit)
    assert il.pending == 0
    assert time.perf_counter() - start < 2.0


def test_flush_rejects_partial_block():
    il = Interleaver(mode(300, "short"))
    il.process(np.zeros(5, np.uint8))
    with pytest.raises(ValueError):
        il.flush()


# --- Does it do its job? -------------------------------------------------------------


def channel_neighbour_spread(m) -> int:
    """Smallest load-order distance between two bits sent back to back on the channel."""
    return int(np.min(np.abs(np.diff(permutation(m)))))


#: Measured when M3 was written; pinned so a later change cannot silently weaken them.
#: The 18-column short settings are inherently weak (decrement 17 == +1 mod 18).
EXPECTED_SPREAD = {
    "2400S": 609, "2400L": 671, "1200S": 609, "1200L": 671,
    "600S": 9, "600L": 671, "300S": 9, "300L": 671, "150S": 9, "150L": 671,
    "75S": 3, "75L": 137,
}  # fmt: skip


@pytest.mark.parametrize("m", INTERLEAVED, ids=ids(INTERLEAVED))
def test_adjacent_channel_bits_are_spread_apart(m):
    assert channel_neighbour_spread(m) == EXPECTED_SPREAD[m.name]


def decode_after_burst(m, interleaved: bool, burst_start: int, burst_len: int) -> bool:
    """Encode one block, wipe out a burst of channel bits, decode; True if error-free."""
    n_info = m.interleaver_bits // 2 - 6
    message = np.random.default_rng(5).integers(0, 2, n_info).astype(np.uint8)
    coded = fec.encode(np.concatenate([message, np.zeros(6, np.uint8)]))
    channel = Interleaver(m).process(coded) if interleaved else coded.copy()
    soft = fec.hard_to_soft(channel)
    soft[burst_start : burst_start + burst_len] *= -1  # a fade that inverts every bit
    if interleaved:
        soft = Deinterleaver(m).process(soft)
    return np.array_equal(fec.viterbi_decode(soft)[:n_info], message)


@pytest.mark.parametrize("start", [0, 5000, 12345, 20000])
def test_interleaving_lets_fec_survive_a_burst(start):
    """The point of the exercise: a 120-bit burst (25 ms at 4800 channel bits/s) is
    fatal to the bare code but harmless once interleaved (2400 bps, long)."""
    m = mode(2400, "long")
    assert not decode_after_burst(m, interleaved=False, burst_start=start, burst_len=120)
    assert decode_after_burst(m, interleaved=True, burst_start=start, burst_len=120)
