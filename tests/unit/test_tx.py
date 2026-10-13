"""Tests for the transmit framer (M5).

The central tool is a *genie* inverse written here, independently of ``tx.py``. It
computes the expected layout (preamble length, frame and probe positions, D1/D2 slots,
total length) from the mode parameters alone, then undoes scrambling, symbol formation,
interleaving and coding. A framer bug in ordering, counting or padding shows up as a
mismatch with this independent bookkeeping.
"""

import itertools
import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c import bits as bitops
from m110c import fec, mgd, scrambler, symbols
from m110c.interleave import Deinterleaver
from m110c.spec import all_modes, mode
from m110c.spec import tables as T
from m110c.tx import Transmitter, data_phase_symbols, transmit, transmit_bytes

MODES = all_modes()
FRAMED = [m for m in MODES if not m.is_75bps]
VOICE = mode(2400, "short", voice=True)  # INT-019
EOM = bitops.int_to_bits(T.EOM_SEQUENCE, 32)


def ids(modes):
    return [m.name for m in modes]


# --- Independent bookkeeping ---------------------------------------------------------


def coded_bits_per_input(m) -> int:
    return 2 * m.repetitions if m.coded else 1


def expected_data_symbols(m, n_user_bits: int) -> int:
    """Data-phase length worked out from §5.3.2.3.2 and INT-013, not from tx.py."""
    n_input = n_user_bits + 32 + 144  # user + EOM + coder flush
    channel_bits = n_input * coded_bits_per_input(m)
    if m.has_interleaver:
        blocks = math.ceil(channel_bits / m.interleaver_bits)
        return blocks * m.block_symbols
    if m.is_75bps:
        return channel_bits // 2 * 32  # one 32-tribit set per dibit
    frames = math.ceil(channel_bits / m.channel_bits_per_frame)
    return frames * m.frame_symbols


def split(m, tribits):
    pre = tribits[: m.preamble_symbols]
    data = scrambler.descramble(
        tribits[m.preamble_symbols :], scrambler.data_sequence(0, tribits.size - m.preamble_symbols)
    )
    return scrambler.descramble(pre, scrambler.sync_sequence(0, pre.size)), data


def genie_decode(m, tribits) -> np.ndarray:
    """Invert the whole transmit chain; return the bits that entered the FEC encoder."""
    _, data = split(m, tribits)
    if m.is_75bps:
        sets = data.reshape(-1, 32)
        values = []
        for i, s in enumerate(sets):
            table = (
                T.TABLE_X_EXCEPTIONAL
                if (i + 1) % m.exceptional_set_period == 0
                else T.TABLE_X_NORMAL
            )
            matches = [v for v, pattern in table.items() if list(pattern) == s.tolist()]
            assert len(matches) == 1, f"set {i} matches no pattern of the expected table"
            values.append(matches[0])
        channel = mgd.mgd_decode(np.array(values), 2)
    else:
        frames = data.reshape(-1, m.frame_symbols)
        unknown = frames[:, : m.unknown_per_frame].reshape(-1)
        labels = symbols.tribit_labels(m.bits_per_channel_symbol)
        channel = np.array([b for t in unknown for b in labels[int(t)]], dtype=np.uint8)
    channel = Deinterleaver(m).process(channel)
    if not m.coded:
        return channel
    soft = fec.combine_pairs(
        fec.hard_to_soft(channel[: channel.size - channel.size % (2 * m.repetitions)]),
        m.repetitions,
    )
    return fec.viterbi_decode(soft)


def sample_message(seed=0, n_bytes=50) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 2, 8 * n_bytes).astype(np.uint8)


# --- Lengths and phases ------------------------------------------------------------------


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
@pytest.mark.parametrize("n_bytes", [0, 1, 37, 400])
def test_total_length(m, n_bytes):
    """INT-014: preamble, then the data blocks, with nothing in between."""
    tribits = transmit(sample_message(n_bytes=n_bytes), m)
    assert tribits.size == m.preamble_symbols + expected_data_symbols(m, 8 * n_bytes)


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_output_is_tribits(m):
    tribits = transmit(sample_message(), m)
    assert tribits.dtype == np.uint8 and tribits.max() <= 7


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_phase_a_preamble_is_the_scrambled_sync_pattern(m):
    pre, _ = split(m, transmit(sample_message(), m))
    assert np.array_equal(pre, symbols.preamble_tribits(m))


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_interleaved_modes_end_on_a_block_boundary(m):
    _, data = split(m, transmit(sample_message(n_bytes=123), m))
    assert data.size % m.block_symbols == 0 or not m.has_interleaver


# --- Probes ---------------------------------------------------------------------------


@pytest.mark.parametrize("m", [*FRAMED, VOICE], ids=ids([*FRAMED, VOICE]))
def test_int_005_probes_are_zero_except_d1_d2_at_block_end(m):
    """Every probe is zero except the last two of each 1440/11520-symbol block, which
    carry the D1 and D2 patterns, including in the final block."""
    two_blocks = 2 * m.block_symbols // m.frame_symbols * m.channel_bits_per_frame
    n_bytes = math.ceil(two_blocks / coded_bits_per_input(m) / 8)  # just over two blocks
    _, data = split(m, transmit(sample_message(n_bytes=n_bytes), m))
    frames = data.reshape(-1, m.frame_symbols)
    assert len(frames) >= 2 * m.frames_per_block  # at least two blocks exercised
    d1, d2 = m.d1_d2
    for i, frame in enumerate(frames):
        probe = frame[m.unknown_per_frame :]
        position = i % m.frames_per_block
        if position == m.frames_per_block - 2:
            expected = symbols.probe_tribits(m, d1)
        elif position == m.frames_per_block - 1:
            expected = symbols.probe_tribits(m, d2)
        else:
            expected = np.zeros(m.known_per_frame, np.uint8)
        assert probe.tolist() == expected.tolist(), f"frame {i}"


@pytest.mark.parametrize("m", FRAMED, ids=ids(FRAMED))
def test_data_phase_starts_with_unknown_symbols(m):
    """§5.3.2.2.2 note: "switch S2 is placed in the UNKNOWN DATA position first"."""
    message = np.ones(400, dtype=np.uint8)  # all ones, so data symbols are non-zero
    _, data = split(m, transmit(message, m))
    assert data[: m.unknown_per_frame].any()


def test_int_004_scrambler_runs_through_probes_from_data_start():
    """INT-004: if the scrambler restarted per frame or skipped probes, descrambling with
    one continuous sequence would leave probes non-zero."""
    m = mode(600, "short")
    raw = transmit(sample_message(), m)[m.preamble_symbols :]
    seq = scrambler.data_sequence(0, raw.size)
    frames = scrambler.descramble(raw, seq).reshape(-1, 40)
    assert not frames[: m.frames_per_block - 2, 20:].any()


# --- Round trip through the genie ---------------------------------------------------------


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
@pytest.mark.parametrize("n_bytes", [0, 1, 50])
def test_genie_recovers_message_eom_and_flush(m, n_bytes):
    message = sample_message(seed=n_bytes, n_bytes=n_bytes)
    decoded = genie_decode(m, transmit(message, m))
    n = message.size
    assert decoded[:n].tolist() == message.tolist()
    assert decoded[n : n + 32].tolist() == EOM.tolist()  # §5.3.2.2.3
    tail = decoded[n + 32 :]
    assert tail.size >= 144 and not tail.any()  # §5.3.2.3.2: flush bits are "0"


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_message_overflowing_a_block_by_one_bit(m):
    """Block-boundary edge case: a message that overflows a block by exactly one bit."""
    if m.has_interleaver:
        # Smallest message whose EOM + flush overflow a block boundary by exactly one bit.
        per_block = m.interleaver_bits // coded_bits_per_input(m)
        n = math.ceil((32 + 144 + 1) / per_block) * per_block + 1 - 32 - 144
    else:
        n = 97
    message = sample_message(seed=7, n_bytes=math.ceil(n / 8))[:n]
    decoded = genie_decode(m, transmit(message, m))
    assert decoded[:n].tolist() == message.tolist()


def test_int_016_transmit_bytes_is_lsb_first():
    m = mode(2400, "short")
    assert np.array_equal(
        transmit_bytes(b"\x01\xf0", m), transmit(bitops.bytes_to_bits(b"\x01\xf0"), m)
    )
    assert not np.array_equal(
        transmit_bytes(b"\x01", m), transmit_bytes(b"\x01", m, msb_first=True)
    )


# --- Streaming ------------------------------------------------------------------------


@settings(max_examples=25, deadline=None)
@given(st.sampled_from(MODES), st.lists(st.integers(1, 2000), min_size=1, max_size=10))
def test_streaming_is_chunk_invariant(m, chunk_sizes):
    """Splitting the input anywhere must not change the output. An empty write (which a
    pipe can produce) follows every real one."""
    message = sample_message(seed=3, n_bytes=300)
    reference = transmit(message, m)
    tx = Transmitter(m)
    pieces, i = [], 0
    for size in itertools.cycle(chunk_sizes):
        if i >= message.size:
            break
        pieces.append(tx.process(message[i : i + size]))
        pieces.append(tx.process(message[:0]))
        i += size
    pieces.append(tx.finish())
    assert np.array_equal(np.concatenate(pieces), reference)


def test_preamble_is_released_immediately():
    """INT-014: the preamble does not wait for input; data blocks follow it."""
    m = mode(2400, "long")
    out = Transmitter(m).process(np.zeros(0, np.uint8))
    assert out.size == m.preamble_symbols


def test_frames_are_released_as_soon_as_complete():
    """INT-017: no idle fill; the transmitter waits for input."""
    m = mode(1200, "zero")  # no interleaver: 20 user bits fill one 40-symbol frame
    tx = Transmitter(m)
    tx.process(np.zeros(0, np.uint8))
    assert tx.process(np.zeros(19, np.uint8)).size == 0
    assert tx.process(np.zeros(1, np.uint8)).size == m.frame_symbols


def test_buffering_is_bounded():
    m = mode(2400, "long")
    tx = Transmitter(m)
    rng = np.random.default_rng(4)
    for _ in range(40):
        tx.process(rng.integers(0, 2, 997).astype(np.uint8))
        assert tx._interleaver.pending < m.interleaver_bits
        assert tx._pending.size < m.channel_bits_per_frame


def test_cannot_use_after_finish():
    tx = Transmitter(mode(600, "short"))
    tx.finish()
    with pytest.raises(RuntimeError):
        tx.process(np.zeros(8, np.uint8))
    with pytest.raises(RuntimeError):
        tx.finish()


def test_two_transmitters_are_independent():
    """State isolation: alternating calls between two framers changes neither output."""
    a, b = mode(300, "short"), mode(75, "long")
    msg_a, msg_b = sample_message(1), sample_message(2)
    ta, tb = Transmitter(a), Transmitter(b)
    out_a, out_b = [], []
    for lo, hi in [(0, 100), (100, 250), (250, 400)]:
        out_a.append(ta.process(msg_a[lo:hi]))
        out_b.append(tb.process(msg_b[lo:hi]))
    out_b.append(tb.finish())
    out_a.append(ta.finish())
    assert np.array_equal(np.concatenate(out_a), transmit(msg_a, a))
    assert np.array_equal(np.concatenate(out_b), transmit(msg_b, b))


@settings(max_examples=60, deadline=None)
@given(m=st.sampled_from(all_modes()), n_bits=st.integers(0, 3000))
def test_data_phase_length_matches_the_transmitter(m, n_bits):
    """The receiver places a message's end with data_phase_symbols (M12); it must agree
    exactly with what the framer sends, for every mode and length."""
    tribits = transmit(np.zeros(n_bits, dtype=np.uint8), m)
    preamble = symbols.preamble_tribits(m).size
    assert tribits.size - preamble == data_phase_symbols(m, n_bits)


def test_int_019_voice_differs_from_2400s_only_in_the_designators():
    """The same bits sent as 2400S and as 2400V: the transmissions differ only in the
    D1/D2 slots of each preamble segment and in the D probes, nowhere else."""
    data = mode(2400, "short")
    bits = sample_message(n_bytes=700)  # just over two interleaver blocks
    a, b = transmit(bits, data), transmit(bits, VOICE)
    assert a.size == b.size
    differ = np.flatnonzero(a != b)
    slot = len(T.TABLE_XIII[0])
    segment = T.PREAMBLE_CHANNEL_SYMBOLS_PER_SEGMENT * slot
    n_pre = data.preamble_symbols
    pre, post = differ[differ < n_pre], differ[differ >= n_pre] - n_pre
    assert pre.size and set((pre % segment) // slot) == {9, 10}  # D1 and D2 slots only
    position = post // data.frame_symbols % data.frames_per_block
    in_probe = post % data.frame_symbols >= data.unknown_per_frame
    assert post.size and in_probe.all()
    assert set(position) == {data.frames_per_block - 2, data.frames_per_block - 1}
