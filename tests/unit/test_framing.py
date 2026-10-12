"""Tests for user-data framing: synchronous bytes and asynchronous characters (INT-016/025)."""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c.framing import AsyncDecoder, AsyncFormat, async_encode, sync_encode
from m110c.spec import mode
from m110c.tx import transmit, transmit_bytes

ALL_FORMATS = [AsyncFormat(d, p, s) for d in (5, 6, 7, 8) for p in "NEO" for s in (1, 2)]


def test_parse_and_print():
    assert AsyncFormat.parse("8N1") == AsyncFormat(8, "N", 1)
    assert AsyncFormat.parse(" 7e2 ") == AsyncFormat(7, "E", 2)
    assert str(AsyncFormat(5, "O", 2)) == "5O2"


@pytest.mark.parametrize("text", ["9N1", "8X1", "8N3", "8N", "N81", "", "88N1"])
def test_parse_rejects_formats_outside_table_a_viii(text):
    with pytest.raises(ValueError):
        AsyncFormat.parse(text)


def test_frame_bits():
    assert AsyncFormat(8, "N", 1).frame_bits == 10
    assert AsyncFormat(7, "E", 2).frame_bits == 11
    assert AsyncFormat(5, "N", 1).frame_bits == 7


def test_int_025_8n1_frame_layout():
    """'A' = 0x41 = 0100 0001: start 0, data LSB first 1000 0010, stop 1."""
    assert async_encode(b"A", AsyncFormat(8, "N", 1)).tolist() == [0, 1, 0, 0, 0, 0, 0, 1, 0, 1]


def test_parity_bits():
    # 'A' has two 1 bits in its low 7 bits: even parity bit 0, odd parity bit 1.
    assert async_encode(b"A", AsyncFormat(7, "E", 1)).tolist() == [0, 1, 0, 0, 0, 0, 0, 1, 0, 1]
    assert async_encode(b"A", AsyncFormat(7, "O", 1)).tolist() == [0, 1, 0, 0, 0, 0, 0, 1, 1, 1]


def test_fewer_than_8_data_bits_use_the_low_bits():
    """A.5.1.2.8.2.1: "the least significant N bits of each 8-bit data bytes"."""
    assert async_encode(b"\xff", AsyncFormat(5, "N", 2)).tolist() == [0, 1, 1, 1, 1, 1, 1, 1]


def test_sync_encode_is_plain_lsb_first_bytes():
    assert sync_encode(b"\x01").tolist() == [1, 0, 0, 0, 0, 0, 0, 0]


@settings(max_examples=60)
@given(st.binary(max_size=80), st.sampled_from(ALL_FORMATS))
def test_round_trip(data, fmt):
    dec = AsyncDecoder(fmt)
    out = dec.process(async_encode(data, fmt))
    mask = (1 << fmt.data_bits) - 1
    assert out == bytes(b & mask for b in data)
    assert dec.framing_errors == dec.parity_errors == 0


@settings(max_examples=40)
@given(st.binary(min_size=1, max_size=60), st.lists(st.integers(1, 23), min_size=1, max_size=6))
def test_decoder_is_chunk_invariant(data, chunk_sizes):
    fmt = AsyncFormat(8, "E", 2)
    bits = np.concatenate([np.ones(13, np.uint8), async_encode(data, fmt), np.ones(5, np.uint8)])
    dec = AsyncDecoder(fmt)
    out, i = b"", 0
    for size in itertools.cycle(chunk_sizes):
        if i >= bits.size:
            break
        out += dec.process(bits[i : i + size])
        i += size
    assert out == data


def test_idle_line_between_characters_is_ignored():
    fmt = AsyncFormat(8, "N", 1)
    bits = np.concatenate(
        [
            np.ones(7, np.uint8),
            async_encode(b"H", fmt),
            np.ones(30, np.uint8),
            async_encode(b"i", fmt),
        ]
    )
    assert AsyncDecoder(fmt).process(bits) == b"Hi"


def test_framing_and_parity_errors_are_counted():
    fmt = AsyncFormat(8, "E", 1)
    good = async_encode(b"OK", fmt)
    bad_stop = good.copy()
    bad_stop[fmt.frame_bits - 1] = 0  # first character's stop bit
    dec = AsyncDecoder(fmt)
    dec.process(np.concatenate([bad_stop, np.ones(20, np.uint8)]))
    assert dec.framing_errors >= 1
    bad_parity = good.copy()
    bad_parity[1 + 8] ^= 1  # first character's parity bit
    dec = AsyncDecoder(fmt)
    assert dec.process(bad_parity) == b"OK"
    assert dec.parity_errors == 1 and dec.framing_errors == 0


def test_transmit_bytes_async_option():
    m = mode(1200, "short")
    fmt = AsyncFormat(8, "N", 1)
    assert np.array_equal(
        transmit_bytes(b"hi", m, async_format=fmt), transmit(async_encode(b"hi", fmt), m)
    )
    assert not np.array_equal(transmit_bytes(b"hi", m, async_format=fmt), transmit_bytes(b"hi", m))
