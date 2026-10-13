"""Garbage tolerance (M12, PLAN 4.4): the receiver must not crash, hang or warn on any input,
and a real transmission must still be found next to garbage.

Warnings are errors in this test suite (pyproject.toml), so "no warning" is checked too.
"""

import numpy as np
from hypothesis import example, given, settings
from hypothesis import strategies as st

from m110c import io
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit_bytes

MSG = b"robustness"
SIGNAL = np.concatenate(
    [
        np.zeros(2000, complex),
        modulate(transmit_bytes(MSG, mode(2400, "short"))),
        np.zeros(9600, complex),
    ]
)


def run(x, chunks=(4096,)):
    rx = Receiver()
    out, i, k = b"", 0, 0
    while i < x.size:
        n = chunks[k % len(chunks)]
        out += rx.process(x[i : i + n])
        i, k = i + n, k + 1
    out += rx.finish()
    assert isinstance(out, bytes)
    return out, rx


@settings(max_examples=25, deadline=None)
@given(
    data=st.binary(min_size=0, max_size=120_000),
    chunks=st.lists(st.integers(1, 20_000), min_size=1, max_size=4),
)
def test_arbitrary_bytes_read_as_cf32(data, chunks):
    """Random bytes as cf32 include NaN, infinities and values up to 3.4e38."""
    x = io.decode_samples(data[: len(data) - len(data) % 8], "cf32")
    out, rx = run(x, chunks)
    assert out == b"" or rx.messages  # bytes only ever come from a detected message


@settings(max_examples=20, deadline=None)
@example(hits=[(0, 3.4e38)], cut=5908)  # found by hypothesis: overflow in the search metric
@given(
    hits=st.lists(
        st.tuples(
            st.integers(0, SIGNAL.size - 1),
            st.sampled_from([np.nan, np.inf, -np.inf, 3.4e38, -3.4e38, 1e-38]),
        ),
        max_size=5,
    ),
    cut=st.integers(0, SIGNAL.size),
)
def test_a_transmission_corrupted_or_cut_anywhere(hits, cut):
    """A few corrupt samples and truncation at any point: never an exception or a warning.
    Without either, the message is decoded exactly."""
    x = SIGNAL.copy()
    for position, value in hits:
        x[position] = value
    out, _ = run(x[:cut])
    if not hits and cut == SIGNAL.size:
        assert out == MSG


def test_non_finite_samples_inside_a_message_are_treated_as_silence():
    x = SIGNAL.copy()
    x[20000:20005] = [np.nan, np.inf, -np.inf, np.nan + 1j * np.inf, complex(np.inf, -np.inf)]
    out, rx = run(x)
    assert out == MSG and rx.messages[0].eom_found


def test_a_transmission_between_blocks_of_garbage_is_found():
    """Random cf32 garbage (NaN, infinities, 1e38) right before and after the message."""
    rng = np.random.default_rng(9)
    garbage = io.decode_samples(rng.bytes(8 * 30_000), "cf32")
    out, rx = run(np.concatenate([garbage, SIGNAL, garbage]))
    assert out == MSG and len(rx.messages) == 1


def test_one_huge_sample_just_before_a_preamble():
    """One float32-maximum sample (3.4e38) swamps the search block it falls in (its metric
    used to overflow: a warning, an error here). At most the preamble segment sharing that
    block is missed; a later one is found."""
    long_message = np.concatenate(
        [
            np.zeros(3000, complex),
            modulate(transmit_bytes(MSG, mode(600, "long"))),
            np.zeros(9600, complex),
        ]
    )
    for position in (1000, 2990):
        x = long_message.copy()
        x[position] = 3.4e38
        out, rx = run(x)
        assert out == MSG and rx.messages[0].detection.count >= 20
