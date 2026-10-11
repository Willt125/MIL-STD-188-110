"""Conversions between bytes and bit arrays.

Bits are represented throughout the package as ``numpy`` arrays of ``uint8`` holding 0 or 1.

The spec's line-side interface carries a synchronous bit stream, not bytes, so it says
nothing about the order of bits within a byte. We send each byte least-significant bit
first, as a UART does (INT-016); ``msb_first=True`` selects the other order.
"""

import numpy as np


def bytes_to_bits(data: bytes, msb_first: bool = False) -> np.ndarray:
    """Unpack ``data`` into a bit array, LSB of each byte first by default (INT-016)."""
    octets = np.frombuffer(data, dtype=np.uint8)
    return np.unpackbits(octets, bitorder="big" if msb_first else "little")


def bits_to_bytes(bits: np.ndarray, msb_first: bool = False) -> bytes:
    """Pack a bit array into bytes. The length must be a multiple of 8.

    The receiver decides what to do with a trailing partial byte (INT-016) before
    calling this, so a partial byte here is treated as a caller error.
    """
    bits = np.asarray(bits, dtype=np.uint8)
    if bits.size % 8:
        raise ValueError(f"bit count {bits.size} is not a multiple of 8")
    return np.packbits(bits, bitorder="big" if msb_first else "little").tobytes()


def int_to_bits(value: int, width: int) -> np.ndarray:
    """``value`` as ``width`` bits, most significant first (e.g. the EOM, INT-011)."""
    if value < 0 or value >= 1 << width:
        raise ValueError(f"{value} does not fit in {width} bits")
    return np.array([(value >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.uint8)
