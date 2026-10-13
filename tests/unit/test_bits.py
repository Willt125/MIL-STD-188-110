"""Tests for byte/bit conversion (M2)."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from m110c.bits import bits_to_bytes, bytes_to_bits, int_to_bits
from m110c.spec import tables as T


def test_int_016_lsb_first_by_default():
    assert bytes_to_bits(b"\x01").tolist() == [1, 0, 0, 0, 0, 0, 0, 0]
    assert bytes_to_bits(b"\x80").tolist() == [0, 0, 0, 0, 0, 0, 0, 1]


def test_int_016_msb_first_option():
    assert bytes_to_bits(b"\x01", msb_first=True).tolist() == [0, 0, 0, 0, 0, 0, 0, 1]


def test_byte_order_is_preserved():
    assert bytes_to_bits(b"\x01\x02").tolist()[:16] == [1, 0, 0, 0, 0, 0, 0, 0] + [
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
    ]


@given(st.binary(max_size=200), st.booleans())
def test_round_trip(data, msb_first):
    bits = bytes_to_bits(data, msb_first=msb_first)
    assert bits.dtype == np.uint8 and set(bits.tolist()) <= {0, 1}
    assert len(bits) == 8 * len(data)
    assert bits_to_bytes(bits, msb_first=msb_first) == data


def test_empty():
    assert bytes_to_bits(b"").size == 0
    assert bits_to_bytes(np.zeros(0, dtype=np.uint8)) == b""


def test_partial_byte_is_rejected():
    with pytest.raises(ValueError):
        bits_to_bytes(np.ones(7, dtype=np.uint8))


def test_int_011_eom_as_bits():
    """INT-011."""
    bits = int_to_bits(T.EOM_SEQUENCE, T.EOM_BITS)
    assert bits[:8].tolist() == [0, 1, 0, 0, 1, 0, 1, 1]
    assert len(bits) == 32


def test_int_to_bits_rejects_overflow():
    with pytest.raises(ValueError):
        int_to_bits(8, 3)
