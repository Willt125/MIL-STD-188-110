"""Modified-Gray decoder (MGD) of §5.3.2.3.6, Tables VIII and IX.

Despite its name, the MGD is a *transmit-side* mapping. Bits fetched from the interleaver
are grouped into channel symbols of 1, 2 or 3 bits (Table VII). Each 2- or 3-bit group is
renamed through Table IX or VIII before symbol formation places it on the 8-PSK circle.
The renaming makes neighbouring phases differ in exactly one bit, so the most likely
demodulation error (an adjacent phase) costs one bit error, not two or three.

One-bit channel symbols pass through unchanged (§5.3.2.3.6).

The first bit fetched is the "first bit" of the table, i.e. the MSB (INT-009).
"""

import numpy as np

from .spec import tables as T

_TABLES = {
    2: T.TABLE_IX,  # 1200 bps and 75 bps (fixed frequency)
    3: T.TABLE_VIII,  # 2400 and 4800 bps
}


def _lookup(bits_per_symbol: int) -> np.ndarray:
    """Table as an array indexed by the input bits read as a number (first bit = MSB)."""
    if bits_per_symbol == 1:
        return np.array([0, 1], dtype=np.uint8)
    table = _TABLES[bits_per_symbol]
    lut = np.zeros(1 << bits_per_symbol, dtype=np.uint8)
    for bits, value in table.items():
        lut[int("".join(map(str, bits)), 2)] = value
    return lut


#: MGD_LUT[n][v] = decoded value for an n-bit channel symbol whose bits read as v.
MGD_LUT = {n: _lookup(n) for n in (1, 2, 3)}
#: Inverse tables: decoded value -> channel-symbol bits read as a number.
MGD_INVERSE = {n: np.argsort(lut).astype(np.uint8) for n, lut in MGD_LUT.items()}


def group_bits(bits: np.ndarray, bits_per_symbol: int) -> np.ndarray:
    """Read consecutive groups of ``bits_per_symbol`` bits as numbers, first bit = MSB."""
    bits = np.asarray(bits, dtype=np.uint8)
    if bits.size % bits_per_symbol:
        raise ValueError(f"{bits.size} bits is not a whole number of {bits_per_symbol}-bit symbols")
    weights = 1 << np.arange(bits_per_symbol - 1, -1, -1)
    return (bits.reshape(-1, bits_per_symbol) * weights).sum(axis=1).astype(np.uint8)


def ungroup_bits(values: np.ndarray, bits_per_symbol: int) -> np.ndarray:
    """Inverse of :func:`group_bits`."""
    values = np.asarray(values, dtype=np.uint8)
    shifts = np.arange(bits_per_symbol - 1, -1, -1)
    return ((values[:, None] >> shifts) & 1).astype(np.uint8).reshape(-1)


def mgd_encode(bits: np.ndarray, bits_per_symbol: int) -> np.ndarray:
    """Channel bits -> modified-Gray-decoded channel-symbol values (0 .. 2**n - 1)."""
    return MGD_LUT[bits_per_symbol][group_bits(bits, bits_per_symbol)]


def mgd_decode(values: np.ndarray, bits_per_symbol: int) -> np.ndarray:
    """Inverse of :func:`mgd_encode` (hard decisions): values -> channel bits."""
    return ungroup_bits(MGD_INVERSE[bits_per_symbol][np.asarray(values)], bits_per_symbol)
