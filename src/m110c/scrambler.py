"""Scramblers of §5.3.2.3.8.

Every transmitted symbol is a tribit number (0..7) from symbol formation, plus a
pseudo-random tribit, modulo 8 (§5.3.2.3.8). Adding k modulo 8 rotates the 8-PSK phase by
k * 45 degrees, so scrambling makes even the all-zero probes and the {0, 4}-only low-rate
symbols use all eight phases. The receiver, which knows the sequence, rotates back.

Data scrambler (§5.3.2.3.8.1, Figure 6, INT-003)
    A 12-bit Galois shift register. Data moves toward the MSB (leftward in the figure); the
    bit leaving cell 11 re-enters at cell 0 and is XORed into the inputs of cells 1, 4 and 6.
    That is the polynomial x^12 + x^6 + x^4 + x + 1. Each symbol: shift 8 times, then read
    cells (2, 1, 0) as (MSB, middle, LSB). Every 160 symbols the register is reloaded with
    0xBAD before its 8 shifts, so the output repeats with period 160.

Sync scrambler (§5.3.2.3.8.2)
    A fixed 32-symbol sequence, repeated through the preamble.

Both generators here are positional: symbol ``i`` of the data phase always gets the same
scrambling value, which makes streaming trivial (just remember the position).
"""

import numpy as np

from .spec import tables as T

_MASK = (1 << T.DATA_SCRAMBLER_BITS) - 1
#: XORed into the shifted register whenever the bit leaving cell 11 is 1: cell 0 (the
#: re-entering bit itself) plus the feedback cells of Figure 6.
_FEEDBACK = sum(1 << c for c in (0, *T.DATA_SCRAMBLER_FEEDBACK_CELLS))


def lfsr_shift(state: int) -> int:
    """One shift of the Figure 6 register."""
    leaving = (state >> (T.DATA_SCRAMBLER_BITS - 1)) & 1
    state = (state << 1) & _MASK
    return state ^ _FEEDBACK if leaving else state


def lfsr_output(state: int) -> int:
    """Tribit read from cells (2, 1, 0) as (MSB, middle, LSB)."""
    msb, mid, lsb = (((state >> c) & 1) for c in T.DATA_SCRAMBLER_OUTPUT_CELLS)
    return (msb << 2) | (mid << 1) | lsb


def _data_sequence() -> np.ndarray:
    out = np.empty(T.DATA_SCRAMBLER_PERIOD, dtype=np.uint8)
    state = T.DATA_SCRAMBLER_INIT  # reset to BAD at the start of each period ...
    for i in range(T.DATA_SCRAMBLER_PERIOD):
        for _ in range(T.DATA_SCRAMBLER_SHIFTS_PER_SYMBOL):  # ... "prior to the eight shifts"
            state = lfsr_shift(state)
        out[i] = lfsr_output(state)
    return out


#: One period (160 symbols) of the data scrambling sequence.
DATA_SEQUENCE = _data_sequence()
#: One period (32 symbols) of the sync preamble scrambling sequence.
SYNC_SEQUENCE = np.array(T.SYNC_SCRAMBLER_SEQUENCE, dtype=np.uint8)


def _periodic(sequence: np.ndarray, start: int, count: int) -> np.ndarray:
    return sequence[(start + np.arange(count)) % sequence.size]


def data_sequence(start: int, count: int) -> np.ndarray:
    """Data scrambling values for data-phase symbols ``start .. start+count-1`` (INT-004)."""
    return _periodic(DATA_SEQUENCE, start, count)


def sync_sequence(start: int, count: int) -> np.ndarray:
    """Sync scrambling values for preamble symbols ``start .. start+count-1``."""
    return _periodic(SYNC_SEQUENCE, start, count)


def scramble(tribits: np.ndarray, sequence: np.ndarray) -> np.ndarray:
    """Modulo-8 addition (§5.3.2.3.8)."""
    return ((np.asarray(tribits, dtype=np.int64) + sequence) % 8).astype(np.uint8)


def descramble(tribits: np.ndarray, sequence: np.ndarray) -> np.ndarray:
    """Modulo-8 subtraction: the hard-decision inverse of :func:`scramble`."""
    return ((np.asarray(tribits, dtype=np.int64) - sequence) % 8).astype(np.uint8)
