"""Symbol formation (§5.3.2.3.7): channel symbols -> unscrambled tribit numbers (0..7).

Three kinds of transmitted content are formed here; scrambling is applied afterwards
(:mod:`m110c.scrambler`), and assembling them into a transmission is the framer's job.

Unknown data (§5.3.2.3.7.1.1)
    Above 75 bps, each MGD output value becomes one tribit, placed on the 8-PSK circle by
    Figure 5: one-bit symbols at 0/4, two-bit at 0/2/4/6, three-bit at all eight (INT-020).
    At 75 bps, each two-bit MGD value becomes a *set* of 32 tribits (Table X): one of four
    orthogonal patterns. Every 45th (short) or 360th (long) set uses the alternative
    "exceptional" patterns of Table X b, marking interleaver block boundaries (INT-010).

Known data / probes (§5.3.2.3.7.1.2)
    All zeros, except that the probes that carry D1 and D2 begin with a 16-tribit pattern:
    the Table XIII pattern for that 3-bit value, repeated twice instead of four times
    (INT-006).

Sync preamble (§5.3.2.3.7.2)
    Segments of 15 three-bit channel symbols, 0 1 3 0 1 3 1 2 0 D1 D2 C1 C2 C3 0, each
    expanded to 32 tribits by Table XIII. C1 C2 C3 encode the segment count, which runs
    down to zero (INT-001 for the count encoding).
"""

import numpy as np

from . import mgd
from .spec import tables as T
from .spec.modes import ModeSpec

# --- Unknown data ----------------------------------------------------------------

#: Figure 5 placement of an n-bit MGD value on the 8-PSK circle (INT-020).
CONSTELLATION = {
    1: np.array([T.FIGURE_5_ONE_BIT[v] for v in range(2)], dtype=np.uint8),
    2: np.array([T.FIGURE_5_TWO_BIT[v] for v in range(4)], dtype=np.uint8),
    3: np.arange(8, dtype=np.uint8),
}


def data_tribits(channel_bits: np.ndarray, mode: ModeSpec) -> np.ndarray:
    """Interleaver output bits -> one tribit per channel symbol (rates above 75 bps)."""
    if mode.is_75bps:
        raise ValueError("75 bps uses data_sets_75bps()")
    n = mode.bits_per_channel_symbol
    return CONSTELLATION[n][mgd.mgd_encode(channel_bits, n)]


def tribit_labels(bits_per_symbol: int) -> dict[int, tuple[int, ...]]:
    """For each tribit a data symbol can take, the channel bits that produce it.

    This is the whole MGD + Figure 5 chain run backwards; the receiver's soft demapper
    uses it to turn symbol likelihoods into bit likelihoods.
    """
    labels = {}
    for v in range(1 << bits_per_symbol):
        bits = mgd.mgd_decode(np.array([v]), bits_per_symbol)
        labels[int(CONSTELLATION[bits_per_symbol][v])] = tuple(int(b) for b in bits)
    return labels


_X_NORMAL = np.array([T.TABLE_X_NORMAL[v] for v in range(4)], dtype=np.uint8)
_X_EXCEPTIONAL = np.array([T.TABLE_X_EXCEPTIONAL[v] for v in range(4)], dtype=np.uint8)


def is_exceptional_set(set_index: int, mode: ModeSpec) -> bool:
    """75 bps: is the ``set_index``-th set (0-based, from the end of sync) exceptional?

    "Every 45th set" is sets 44, 89, 134, ... in 0-based counting: the last set of each
    interleaver block (INT-010).
    """
    return (set_index + 1) % mode.exceptional_set_period == 0


def data_sets_75bps(channel_bits: np.ndarray, mode: ModeSpec, first_set_index: int) -> np.ndarray:
    """75 bps: interleaver output bits -> 32 tribits per dibit (Table X).

    ``first_set_index`` is the position of the first set counted from the end of the sync
    preamble, so a streaming framer can pass where it has got to.
    """
    if not mode.is_75bps:
        raise ValueError("only 75 bps uses 32-tribit sets")
    values = mgd.mgd_encode(channel_bits, 2)  # MGD first, then Table X (INT-024)
    out = np.empty((values.size, T.TRIBITS_PER_75BPS_SET), dtype=np.uint8)
    for i, v in enumerate(values):
        exceptional = is_exceptional_set(first_set_index + i, mode)
        out[i] = (_X_EXCEPTIONAL if exceptional else _X_NORMAL)[v]
    return out.reshape(-1)


# --- Known data (probes) ------------------------------------------------------------


def d_pattern(value: int) -> np.ndarray:
    """The 16-tribit known pattern for D1 or D2 (INT-006)."""
    return np.array(T.TABLE_XIII[value][: T.D_PATTERN_TRIBITS], dtype=np.uint8)


def probe_tribits(mode: ModeSpec, d_value: int | None = None) -> np.ndarray:
    """One probe (known-data slot) of ``mode``: zeros, or a D pattern padded with zeros."""
    probe = np.zeros(mode.known_per_frame, dtype=np.uint8)
    if d_value is not None:
        probe[: T.D_PATTERN_TRIBITS] = d_pattern(d_value)
    return probe


# --- Sync preamble ----------------------------------------------------------------


def count_symbols(count: int) -> tuple[int, int, int]:
    """C1, C2, C3 for a segment count: six bits split into three two-bit values, each with
    a 1 prefixed as MSB (§5.3.2.3.7.2.1 text and note; *not* Table XII, see INT-001)."""
    if not 0 <= count < 64:
        raise ValueError("segment count must fit in six bits")
    return tuple(T.COUNT_SYMBOL_PREFIX | ((count >> shift) & 0b11) for shift in (4, 2, 0))


def preamble_channel_symbols(mode: ModeSpec) -> np.ndarray:
    """All three-bit channel symbols of the preamble, segment after segment."""
    d1, d2 = mode.d1_d2
    segments = []
    for count in range(mode.preamble_count_start, -1, -1):
        segments.append(
            [*T.PREAMBLE_FIXED_PREFIX, d1, d2, *count_symbols(count), *T.PREAMBLE_FIXED_SUFFIX]
        )
    return np.array(segments, dtype=np.uint8).reshape(-1)


_XIII = np.array([T.TABLE_XIII[v] for v in range(8)], dtype=np.uint8)


def preamble_tribits(mode: ModeSpec) -> np.ndarray:
    """The whole sync preamble as unscrambled tribits (32 per channel symbol)."""
    return _XIII[preamble_channel_symbols(mode)].reshape(-1)
