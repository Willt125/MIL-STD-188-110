"""Tests for the modified-Gray decoder and symbol formation (M4)."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from m110c import mgd, symbols
from m110c.spec import all_modes, mode
from m110c.spec import tables as T

FRAMED = [m for m in all_modes() if not m.is_75bps]


def bits_strategy(n):
    return st.lists(st.integers(0, 1), max_size=60).map(
        lambda b: np.array(b[: len(b) - len(b) % n], dtype=np.uint8)
    )


# --- MGD ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "bits, n, table",
    [((b0, b1, b2), 3, T.TABLE_VIII) for b0 in (0, 1) for b1 in (0, 1) for b2 in (0, 1)]
    + [((b0, b1), 2, T.TABLE_IX) for b0 in (0, 1) for b1 in (0, 1)],
)
def test_mgd_follows_tables_viii_and_ix(bits, n, table):
    """INT-009: the first fetched bit is the table's "first bit"."""
    assert mgd.mgd_encode(np.array(bits), n).tolist() == [table[bits]]


def test_mgd_is_identity_for_one_bit_symbols():
    bits = np.array([0, 1, 1, 0], dtype=np.uint8)
    assert mgd.mgd_encode(bits, 1).tolist() == bits.tolist()


def test_group_bits_reads_first_bit_as_msb():
    assert mgd.group_bits(np.array([1, 0, 0, 0, 1, 1]), 3).tolist() == [4, 3]


@pytest.mark.parametrize("n", [1, 2, 3])
@given(data=st.data())
def test_mgd_round_trip(n, data):
    bits = data.draw(bits_strategy(n))
    assert mgd.mgd_decode(mgd.mgd_encode(bits, n), n).tolist() == bits.tolist()


def test_mgd_rejects_partial_symbol():
    with pytest.raises(ValueError):
        mgd.mgd_encode(np.array([1, 0]), 3)


# --- Unknown-data symbol formation --------------------------------------------------


@pytest.mark.parametrize(
    "rate, allowed",
    [(4800, set(range(8))), (2400, set(range(8))), (1200, {0, 2, 4, 6}), (600, {0, 4})],
)
def test_data_tribits_use_the_specified_phases(rate, allowed):
    """§5.3.2.3.7.1.1: 0/4 for one-bit symbols, 0/2/4/6 at 1200, all eight at 2400/4800."""
    m = mode(rate)
    bits = np.random.default_rng(1).integers(0, 2, 600 * m.bits_per_channel_symbol)
    tribits = symbols.data_tribits(bits.astype(np.uint8), m)
    assert tribits.size == 600
    assert set(tribits.tolist()) == allowed


@pytest.mark.parametrize("n", [2, 3])
def test_adjacent_phases_differ_in_one_channel_bit(n):
    """End-to-end Gray property of the whole MGD + Figure 5 chain (the reason the MGD
    exists, §5.3.2.3.6 note)."""
    labels = symbols.tribit_labels(n)
    phases = sorted(labels)
    for i, p in enumerate(phases):
        a, b = labels[p], labels[phases[(i + 1) % len(phases)]]
        assert sum(x != y for x, y in zip(a, b, strict=True)) == 1


@pytest.mark.parametrize("n", [1, 2, 3])
def test_tribit_labels_invert_data_tribits(n):
    m = {1: mode(600), 2: mode(1200), 3: mode(2400)}[n]
    for tribit, bits in symbols.tribit_labels(n).items():
        assert symbols.data_tribits(np.array(bits, dtype=np.uint8), m).tolist() == [tribit]


def test_data_tribits_refuses_75bps():
    with pytest.raises(ValueError):
        symbols.data_tribits(np.zeros(2, np.uint8), mode(75))


# --- 75 bps -------------------------------------------------------------------------


@pytest.mark.parametrize("interleave, period", [("short", 45), ("long", 360), ("zero", 45)])
def test_int_010_exceptional_sets_are_last_of_each_block(interleave, period):
    m = mode(75, interleave)
    flags = [symbols.is_exceptional_set(i, m) for i in range(3 * period)]
    assert [i for i, f in enumerate(flags) if f] == [period - 1, 2 * period - 1, 3 * period - 1]


def test_int_024_75bps_sets_follow_mgd_then_table_x():
    """Figure 2 puts the MGD before symbol formation at every rate, and Table IX lists
    75 bps: channel bits 10 -> MGD 11 -> Table X a row 11."""
    m = mode(75, "short")
    out = symbols.data_sets_75bps(np.array([1, 0], dtype=np.uint8), m, first_set_index=0)
    assert out.tolist() == list(T.TABLE_X_NORMAL[0b11])


def test_75bps_exceptional_set_uses_table_x_b():
    m = mode(75, "short")
    bits = np.zeros(2 * 46, dtype=np.uint8)
    out = symbols.data_sets_75bps(bits, m, first_set_index=0).reshape(46, 32)
    assert out[43].tolist() == list(T.TABLE_X_NORMAL[0])
    assert out[44].tolist() == list(T.TABLE_X_EXCEPTIONAL[0])
    assert out[45].tolist() == list(T.TABLE_X_NORMAL[0])


def test_75bps_streaming_by_set_index():
    """Forming sets in pieces with the right starting index equals forming them at once."""
    m = mode(75, "long")
    bits = np.random.default_rng(2).integers(0, 2, 2 * 800).astype(np.uint8)
    whole = symbols.data_sets_75bps(bits, m, 0)
    parts = [
        symbols.data_sets_75bps(bits[:700], m, 0),
        symbols.data_sets_75bps(bits[700:], m, 350),
    ]
    assert np.array_equal(np.concatenate(parts), whole)


# --- Probes -------------------------------------------------------------------------


@pytest.mark.parametrize("m", FRAMED, ids=lambda m: m.name)
def test_plain_probe_is_all_zero(m):
    probe = symbols.probe_tribits(m)
    assert probe.size == m.known_per_frame and not probe.any()


@pytest.mark.parametrize("m", FRAMED, ids=lambda m: m.name)
def test_int_006_d_probe_is_table_xiii_twice_then_zero_padding(m):
    d1, d2 = m.d1_d2
    for d in (d1, d2):
        probe = symbols.probe_tribits(m, d)
        assert probe[:16].tolist() == list(T.TABLE_XIII[d][:8]) * 2
        assert not probe[16:].any()  # "the unused last four tribit symbols shall be set to 0"
        assert probe.size == m.known_per_frame


# --- Preamble ---------------------------------------------------------------------


def test_int_001_count_symbols():
    assert symbols.count_symbols(23) == (5, 5, 7)
    assert symbols.count_symbols(2) == (4, 4, 6)
    assert symbols.count_symbols(0) == (4, 4, 4)
    with pytest.raises(ValueError):
        symbols.count_symbols(64)


@pytest.mark.parametrize("m", all_modes(), ids=lambda m: m.name)
def test_preamble_structure(m):
    segs = symbols.preamble_channel_symbols(m).reshape(-1, 15)
    assert len(segs) == m.preamble_segments
    for i, seg in enumerate(segs):
        count = m.preamble_count_start - i
        expected = [0, 1, 3, 0, 1, 3, 1, 2, 0, *m.d1_d2, *symbols.count_symbols(count), 0]
        assert seg.tolist() == expected
    assert symbols.preamble_tribits(m).size == m.preamble_symbols


def test_preamble_counts_run_down_to_zero():
    segs = symbols.preamble_channel_symbols(mode(1200, "long")).reshape(-1, 15)
    counts = [((s[11] & 3) << 4) | ((s[12] & 3) << 2) | (s[13] & 3) for s in segs]
    assert counts == list(range(23, -1, -1))


def test_preamble_tribits_expand_through_table_xiii():
    m = mode(600, "short")
    tribits = symbols.preamble_tribits(m).reshape(-1, 32)
    for cs, row in zip(symbols.preamble_channel_symbols(m), tribits, strict=True):
        assert row.tolist() == list(T.TABLE_XIII[int(cs)])
