"""Tests for telling zero interleave from short interleave at the receiver (S4, INT-012).

The decision rules are checked on the transmitted symbols themselves (plus noise where it
matters), without a channel; the receiver and the CLI are tested end to end elsewhere
(test_rx_receiver.py, test_cli_rx.py)."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c import fec
from m110c.rx import autointerleave as A
from m110c.rx.decode import DataDecoder
from m110c.spec import mode
from m110c.tx import transmit_bytes

RATES = (2400, 1200, 600, 300, 150, 75)


def data_symbols(m, message, snr_db=None, seed=0):
    """The data phase of a transmission (probes included), as unit-circle symbols."""
    tribits = transmit_bytes(message, m)[m.preamble_symbols :]
    z = np.exp(1j * np.pi / 4 * tribits.astype(float))
    if snr_db is not None:
        rng = np.random.default_rng(seed)
        sigma = np.sqrt(10 ** (-snr_db / 10) / 2)
        z = z + sigma * (rng.standard_normal(z.size) + 1j * rng.standard_normal(z.size))
    return z


def message(n, seed=0):
    return bytes(np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8))


def decode_all(decoder, z, chunks=None):
    if chunks is None:
        return np.concatenate([decoder.process(z), decoder.finish()])
    out, i = [], 0
    for size in chunks:
        out.append(decoder.process(z[i : i + size]))
        i += size
    out.append(decoder.process(z[i:]))
    return np.concatenate([*out, decoder.finish()])


# --- The Viterbi fit ----------------------------------------------------------------------


def test_fit_is_one_for_a_code_sequence_and_lower_shuffled():
    rng = np.random.default_rng(1)
    coded = fec.encode(rng.integers(0, 2, 2000).astype(np.uint8))
    clean, shuffled = fec.ViterbiDecoder(), fec.ViterbiDecoder()
    assert clean.fit is None
    clean.decode(1.0 - 2.0 * coded)
    shuffled.decode(1.0 - 2.0 * coded[rng.permutation(coded.size)])
    assert clean.fit == pytest.approx(1.0)
    assert shuffled.fit < 0.8
    clean.flush()
    assert clean.fit is None  # a new message starts afresh


def test_fit_is_the_best_path_metric_over_the_soft_magnitude():
    """path_metric accumulates what the normalisation took away: the best path's metric."""
    rng = np.random.default_rng(2)
    soft = rng.standard_normal(400)
    d = fec.ViterbiDecoder()
    d.decode(soft)
    assert d.soft_magnitude == pytest.approx(np.abs(soft).sum())
    # The best final metric is at most the sum of |soft| (every bit agreeing) and at least
    # the metric of the all-zero path, which emits +1 everywhere.
    assert soft.sum() <= d.path_metric <= d.soft_magnitude


# --- Which modes it applies to -----------------------------------------------------------


@pytest.mark.parametrize(
    "m, applies",
    [
        (mode(2400, "short"), True),
        (mode(75, "short"), True),
        (mode(2400, "long"), False),
        (mode(4800), False),
        (mode(2400, "short", voice=True), False),
        (mode(600, "zero"), False),
    ],
    ids=str,
)
def test_applies_only_to_short_interleave_data_modes(m, applies):
    assert A.applies_to(m) is applies
    if not applies:
        with pytest.raises(ValueError):
            A.InterleaveDetectingDecoder(m)


# --- The decisions -------------------------------------------------------------------------


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize("sent", ["short", "zero"])
def test_the_interleave_sent_is_found_and_decoded(rate, sent):
    """A few blocks' worth at a modest SNR: decided after the first block, on the margin,
    and the bits are exactly those of a decoder told the truth."""
    m = mode(rate, sent)
    z = data_symbols(m, message(max(40, rate * 2 // 8), seed=rate), snr_db=8.0, seed=rate)
    auto = A.InterleaveDetectingDecoder(mode(rate, "short"))
    bits = decode_all(auto, z)
    assert auto.decided == sent and auto.reason == "margin" and auto.blocks_judged == 1
    assert abs(auto.margin) >= A.DECIDE_MARGIN and (auto.margin > 0) == (sent == "short")
    assert auto.mode == m
    assert np.array_equal(bits, decode_all(DataDecoder(m), z))


@pytest.mark.parametrize("rate", [2400, 1200, 600, 75])
def test_a_tiny_zero_interleave_message_is_found(rate):
    """A message so short that its EOM and flush fit in the first block. At 2400 and 1200
    bps the zero decoder finds the EOM there, which decides. At the low rates a block holds
    too few decoded bits for the EOM to be confirmed yet (the Viterbi traceback and the
    EOM's confirmation hold some back), and the margin decides instead."""
    m = mode(rate, "zero")
    z = data_symbols(m, b"hi")
    block = m.block_symbols
    padded = np.concatenate([z, np.zeros(max(0, block - z.size), complex)])[:block]
    auto = A.InterleaveDetectingDecoder(mode(rate, "short"))
    auto.process(padded)
    assert auto.decided == "zero"
    assert auto.reason == ("EOM" if rate >= 1200 else "margin")


def test_a_signal_ending_inside_its_first_block_without_an_eom_was_zero_interleave():
    """Short interleave always sends whole blocks; with no EOM to go on (cut before the
    flush), a signal that ends inside its first block was zero interleave."""
    m = mode(1200, "zero")
    z = data_symbols(m, message(60))[:400]  # a third of a block, EOM cut off
    auto = A.InterleaveDetectingDecoder(mode(1200, "short"))
    decode_all(auto, z)
    assert auto.decided == "zero" and auto.reason == "ended inside the first block"


def test_without_a_clear_margin_it_waits_then_takes_the_larger_fit(monkeypatch):
    monkeypatch.setattr(A, "DECIDE_MARGIN", 10.0)  # never clear
    m = mode(600, "short")
    z = data_symbols(m, message(400), snr_db=8.0)
    auto = A.InterleaveDetectingDecoder(m)
    block = m.block_symbols
    for b in range(1, A.MAX_BLOCKS + 1):
        out = auto.process(z[(b - 1) * block : b * block])
        if b < A.MAX_BLOCKS:
            assert auto.decided is None and out.size == 0  # bits held while undecided
    assert auto.decided == "short" and auto.reason == "larger fit" and auto.margin > 0
    assert auto.blocks_judged == A.MAX_BLOCKS and out.size > 0


def test_an_undecided_signal_that_ends_takes_the_larger_fit_of_its_whole_blocks(monkeypatch):
    monkeypatch.setattr(A, "DECIDE_MARGIN", 10.0)
    m = mode(300, "zero")
    z = data_symbols(m, message(150), snr_db=8.0)
    cut = int(1.5 * m.block_symbols)  # one whole block, then half of one, no EOM
    auto = A.InterleaveDetectingDecoder(mode(300, "short"))
    decode_all(auto, z[:cut])
    assert auto.decided == "zero" and auto.reason == "larger fit" and auto.blocks_judged == 1


def test_at_the_end_of_the_signal_an_eom_outweighs_the_fits(monkeypatch):
    """Undecided after a whole block (forced here), the signal then ends: the EOM under the
    zero hypothesis, accepted with the flush that ended the signal, decides."""
    monkeypatch.setattr(A, "DECIDE_MARGIN", 10.0)
    m = mode(1200, "zero")
    z = data_symbols(m, message(140))  # EOM and flush in the second block
    assert m.block_symbols < z.size < 2 * m.block_symbols
    auto = A.InterleaveDetectingDecoder(mode(1200, "short"))
    bits = decode_all(auto, z)
    assert auto.decided == "zero" and auto.reason == "EOM" and auto.blocks_judged == 1
    assert np.array_equal(bits, decode_all(DataDecoder(m), z))


def test_noise_alone_is_never_mistaken_for_a_clear_margin():
    """No signal at all: neither hypothesis fits, so no margin decides; after MAX_BLOCKS
    the larger fit wins (whichever: the message is lost either way)."""
    rng = np.random.default_rng(3)
    m = mode(2400, "short")
    z = rng.standard_normal(4 * m.block_symbols) + 1j * rng.standard_normal(4 * m.block_symbols)
    auto = A.InterleaveDetectingDecoder(m)
    auto.process(z)
    assert auto.reason == "larger fit" and abs(auto.margin) < A.DECIDE_MARGIN


@settings(max_examples=8, deadline=None)
@given(st.lists(st.integers(1, 3000), min_size=1, max_size=5))
def test_output_does_not_depend_on_chunking(chunks):
    m = mode(1200, "zero")
    z = data_symbols(m, message(500, seed=4), snr_db=10.0, seed=4)
    whole = decode_all(A.InterleaveDetectingDecoder(mode(1200, "short")), z)
    assert np.array_equal(
        decode_all(A.InterleaveDetectingDecoder(mode(1200, "short")), z, chunks), whole
    )


def test_snr_is_measured_on_the_probes_either_way():
    m = mode(2400, "zero")
    z = data_symbols(m, message(800), snr_db=12.0)
    auto, forced = A.InterleaveDetectingDecoder(mode(2400, "short")), DataDecoder(m)
    decode_all(auto, z)
    decode_all(forced, z)
    assert auto.snr_db == pytest.approx(forced.snr_db)
