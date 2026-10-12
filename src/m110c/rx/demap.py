"""Soft demapping: received symbol estimates -> log-likelihood ratios of channel bits.

Conventions (shared with :mod:`m110c.fec`): a soft bit is ``L = log P(b=0) / P(b=1)``, so
positive means "probably 0". The received symbols passed in here are already scaled so that
a noiseless symbol lands exactly on the unit circle at 45k degrees.

Descrambling
    The transmitter added a scrambling value s (mod 8) to every tribit, i.e. rotated the
    symbol by s * 45 degrees (§5.3.2.3.8). The receiver rotates it back by -s * 45 degrees.

Data symbols above 75 bps (max-log approximation)
    For each channel bit b of a symbol y, with sigma^2 the noise variance per complex sample:

        L_b = ( min_{s: b=1} |y - s|^2  -  min_{s: b=0} |y - s|^2 ) / sigma^2

    where s runs over the constellation points the mode uses (Figure 5) and their channel-bit
    labels come from the MGD (``symbols.tribit_labels``). The exact LLR would use a log-sum
    over all points instead of the nearest one; max-log loses a fraction of a dB and keeps the
    formula readable.

75 bps sets (§5.3.2.3.7.1.1)
    Each dibit is sent as one of four orthogonal 32-tribit patterns. With known phase, the
    log-likelihood of pattern v is proportional to Re{ sum_i y_i * conj(p_v,i) }, so

        L_b = ( max_{v: b=0} c_v  -  max_{v: b=1} c_v ) * 2 / sigma^2.
"""

import numpy as np

from .. import mgd, symbols
from ..spec import tables as T
from ..spec.modes import ModeSpec


def descramble(y: np.ndarray, scrambling: np.ndarray) -> np.ndarray:
    """Undo §5.3.2.3.8: rotate each symbol back by its scrambling value * 45 degrees."""
    return np.asarray(y) * np.exp(-1j * np.pi / 4 * np.asarray(scrambling, dtype=float))


def _label_table(bits_per_symbol: int):
    """Constellation points and a (points x bits) label matrix for one data alphabet."""
    labels = symbols.tribit_labels(bits_per_symbol)
    tribits = sorted(labels)
    points = np.exp(1j * np.pi / 4 * np.array(tribits, dtype=float))
    bits = np.array([labels[t] for t in tribits], dtype=np.uint8)
    return points, bits


_TABLES = {n: _label_table(n) for n in (1, 2, 3)}


def soft_bits(y: np.ndarray, bits_per_symbol: int, noise_var=1.0) -> np.ndarray:
    """Max-log LLRs for descrambled data symbols; ``bits_per_symbol`` LLRs per symbol, in
    the order the bits were fetched from the interleaver (first = MSB, INT-009).
    ``noise_var`` is one number, or one per symbol."""
    points, labels = _TABLES[bits_per_symbol]
    y = np.asarray(y, dtype=complex)
    dist = np.abs(y[:, None] - points[None, :]) ** 2  # (symbols, points)
    out = np.empty((y.size, bits_per_symbol))
    for b in range(bits_per_symbol):
        d0 = dist[:, labels[:, b] == 0].min(axis=1)
        d1 = dist[:, labels[:, b] == 1].min(axis=1)
        out[:, b] = (d1 - d0) / noise_var
    return out.reshape(-1)


def _patterns(table) -> np.ndarray:
    return np.exp(1j * np.pi / 4 * np.array([table[v] for v in range(4)], dtype=float))


_NORMAL = _patterns(T.TABLE_X_NORMAL)
_EXCEPTIONAL = _patterns(T.TABLE_X_EXCEPTIONAL)
# Channel bits for each MGD value v: Table X is indexed by the MGD output (INT-024).
_SET_LABELS = np.array([mgd.mgd_decode(np.array([v]), 2) for v in range(4)], dtype=np.uint8)


def set_correlations(sets: np.ndarray, mode: ModeSpec, first_set_index: int) -> np.ndarray:
    """75 bps: Re{correlation} of each descrambled 32-symbol set with the four patterns it
    could be (normal or exceptional, by position, INT-010). Shape (sets, 4)."""
    sets = np.asarray(sets, dtype=complex).reshape(-1, T.TRIBITS_PER_75BPS_SET)
    out = np.empty((len(sets), 4))
    for i, s in enumerate(sets):
        exceptional = symbols.is_exceptional_set(first_set_index + i, mode)
        patterns = _EXCEPTIONAL if exceptional else _NORMAL
        out[i] = np.real(patterns.conj() @ s)
    return out


def set_patterns(mode: ModeSpec, first_set_index: int, values: np.ndarray) -> np.ndarray:
    """75 bps: the transmitted phasor patterns for given MGD values, one row per set."""
    rows = []
    for i, v in enumerate(values):
        exceptional = symbols.is_exceptional_set(first_set_index + i, mode)
        rows.append((_EXCEPTIONAL if exceptional else _NORMAL)[v])
    return np.array(rows).reshape(len(rows), T.TRIBITS_PER_75BPS_SET)


def soft_bits_75(correlations: np.ndarray, noise_var: float = 1.0) -> np.ndarray:
    """75 bps: two LLRs per set from the four pattern correlations (max-log)."""
    out = np.empty((len(correlations), 2))
    for b in range(2):
        c0 = correlations[:, _SET_LABELS[:, b] == 0].max(axis=1)
        c1 = correlations[:, _SET_LABELS[:, b] == 1].max(axis=1)
        out[:, b] = (c0 - c1) * 2 / noise_var
    return out.reshape(-1)
