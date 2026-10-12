"""Literal soft demapper: enumerate every constellation point for every bit.

Builds the constellation and labels from the spec tables directly (Tables VIII/IX via the
MGD definition, Figure 5 placement), loops over symbols, points and bits one at a time, and
returns both the max-log LLR and the exact log-sum-exp LLR.
"""

import math

from m110c.spec import tables as T

PLACEMENT = {1: T.FIGURE_5_ONE_BIT, 2: T.FIGURE_5_TWO_BIT, 3: {v: v for v in range(8)}}
MGD_TABLE = {2: T.TABLE_IX, 3: T.TABLE_VIII}


def alphabet(n):
    """[(complex point, channel bits tuple)] for an n-bit channel symbol."""
    out = []
    for value in range(2**n):
        bits = tuple((value >> (n - 1 - i)) & 1 for i in range(n))  # first bit = MSB
        mgd_value = MGD_TABLE[n][bits] if n in MGD_TABLE else value
        tribit = PLACEMENT[n][mgd_value]
        point = complex(math.cos(math.pi / 4 * tribit), math.sin(math.pi / 4 * tribit))
        out.append((point, bits))
    return out


def llrs(y_list, n, noise_var=1.0):
    """Returns (max_log, exact) lists, n LLRs per symbol, positive = bit 0."""
    max_log, exact = [], []
    for y in y_list:
        for b in range(n):
            d0 = [abs(y - p) ** 2 for p, bits in alphabet(n) if bits[b] == 0]
            d1 = [abs(y - p) ** 2 for p, bits in alphabet(n) if bits[b] == 1]
            max_log.append((min(d1) - min(d0)) / noise_var)
            lse0 = math.log(sum(math.exp(-d / noise_var) for d in d0))
            lse1 = math.log(sum(math.exp(-d / noise_var) for d in d1))
            exact.append(lse0 - lse1)
    return max_log, exact
