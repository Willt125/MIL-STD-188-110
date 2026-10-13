"""Tests for end-of-message detection (INT-018, INT-026)."""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c.rx.eom import EOM_BITS, EomDetector

FLUSH = np.zeros(144 + 300, np.uint8)  # coder flush plus block padding


def run(bits, chunks=None, **kwargs):
    det = EomDetector(**kwargs)
    if chunks is None:
        out = [det.process(bits)]
    else:
        out, i = [], 0
        for size in itertools.cycle(chunks):
            if i >= len(bits):
                break
            out.append(det.process(bits[i : i + size]))
            i += size
    out.append(det.finish())
    return np.concatenate(out), det.found


def message(seed, n):
    return np.random.default_rng(seed).integers(0, 2, n).astype(np.uint8)


def test_payload_ends_exactly_at_the_eom():
    msg = message(1, 500)
    out, found = run(np.concatenate([msg, EOM_BITS, FLUSH]))
    assert found and np.array_equal(out, msg)


def test_empty_message():
    out, found = run(np.concatenate([EOM_BITS, FLUSH]))
    assert found and out.size == 0


@settings(max_examples=40, deadline=None)
@given(st.integers(0, 2**32 - 1), st.lists(st.integers(1, 97), min_size=1, max_size=6))
def test_streaming_gives_the_same_payload_however_chunked(seed, chunks):
    msg = message(seed, 700)
    out, found = run(np.concatenate([msg, EOM_BITS, FLUSH]), chunks)
    assert found and np.array_equal(out, msg)


def test_int_018_eom_inside_user_data_is_not_accepted():
    """The 32-bit pattern alone is not enough: it must be followed by flush zeros."""
    msg = np.concatenate([message(2, 300), EOM_BITS, message(3, 300)])
    out, found = run(np.concatenate([msg, EOM_BITS, FLUSH]))
    assert found and np.array_equal(out, msg)


def test_int_018_documented_risk_eom_plus_zeros_inside_data():
    """The residual risk, pinned so it is not forgotten: user data containing the EOM
    followed by 144+ zero bits *is* taken as the end of the message."""
    msg = np.concatenate([message(4, 100), EOM_BITS, np.zeros(200, np.uint8), message(5, 100)])
    out, found = run(np.concatenate([msg, EOM_BITS, FLUSH]))
    assert found and out.size == 100


def test_int_026_message_ending_with_the_eom_prefix_is_not_cut_short():
    """0x4B65A5B2 = X 11 X. A message ending in the EOM's first 17 bits creates an exact
    EOM match 17 bits early; the detector must still find the real one."""
    msg = np.concatenate([message(6, 200), EOM_BITS[:17]])
    early = np.concatenate([msg, EOM_BITS])[200 : 200 + 32]
    assert np.array_equal(early, EOM_BITS)  # the trap is real
    out, found = run(np.concatenate([msg, EOM_BITS, FLUSH]))
    assert found and np.array_equal(out, msg)


@pytest.mark.parametrize("errors, accepted", [(0, True), (1, True), (2, True), (3, False)])
def test_eom_tolerates_up_to_two_bit_errors(errors, accepted):
    eom = EOM_BITS.copy()
    eom[[3, 11, 29][:errors]] ^= 1
    msg = message(7, 200)
    out, found = run(np.concatenate([msg, eom, FLUSH]))
    assert found == accepted
    if accepted:
        assert np.array_equal(out, msg)


@pytest.mark.parametrize("ones, accepted", [(14, True), (15, False)])
def test_flush_tolerates_about_ten_percent_errors(ones, accepted):
    flush = FLUSH.copy()
    flush[np.linspace(0, 143, ones).astype(int)] = 1
    msg = message(8, 200)
    _, found = run(np.concatenate([msg, EOM_BITS, flush]))
    assert found == accepted


@pytest.mark.parametrize("flush_bits, accepted", [(144, True), (40, True), (31, False)])
def test_signal_cut_off_during_the_flush(flush_bits, accepted):
    msg = message(9, 200)
    out, found = run(np.concatenate([msg, EOM_BITS, np.zeros(flush_bits, np.uint8)]))
    assert found == accepted
    if accepted:
        assert np.array_equal(out, msg)


def test_no_eom_returns_everything_and_reports_it():
    bits = message(10, 1000)
    out, found = run(bits)
    assert not found and np.array_equal(out, bits)


def test_holdback_is_bounded():
    det = EomDetector()
    released = det.process(np.ones(5000, np.uint8))
    assert released.size == 5000 - (32 + 144 + 31 - 1)


def test_input_after_the_eom_is_ignored():
    det = EomDetector()
    det.process(np.concatenate([message(11, 50), EOM_BITS, FLUSH]))
    assert det.found
    assert det.process(np.ones(100, np.uint8)).size == 0 and det.finish().size == 0


@settings(max_examples=30, deadline=None)
@given(
    n=st.integers(0, 600),
    seed=st.integers(0, 1000),
    chunks=st.lists(st.integers(1, 200), min_size=1, max_size=5),
)
def test_payload_bits_counts_the_bits_before_the_eom(n, seed, chunks):
    """The receiver places a message's end from this count (M12), so it must be exact
    however the bits arrive."""
    msg = message(seed, n)
    det = EomDetector()
    bits = np.concatenate([msg, EOM_BITS, FLUSH])
    i = 0
    for size in itertools.cycle(chunks):
        if i >= bits.size:
            break
        det.process(bits[i : i + size])
        i += size
    det.finish()
    assert det.found and det.payload_bits == n
