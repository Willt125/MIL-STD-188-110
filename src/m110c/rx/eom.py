"""End-of-message detection (§5.3.2.3.1, INT-018).

The transmitter ends every message with the 32-bit EOM sequence 0x4B65A5B2 followed by at
least 144 zero flush bits (§5.3.2.2.3-4). The EOM is not escaped, so the same 32 bits can
appear inside user data. A detector that stopped at the first exact match would truncate
about one message in 2^32 / N (N = message length in bits).

Rule used here (INT-018): an EOM is accepted at bit position p when

* bits p .. p+31 differ from the EOM sequence in at most ``max_eom_errors`` places
  (default 2, so one or two decoding errors do not make the message run on forever), and
* the next ``confirm_bits`` bits (the coder flush, default 144) hold at most
  ``max_confirm_ones`` ones (default 14, i.e. about 10% errors), or the signal ends first
  with the bits seen so far being mostly zero.

Random data passes both tests with probability around 1e-7 * 1e-25 per position. The
remaining realistic risk is user data that contains a near-EOM pattern followed by a long
run of zeros; that is documented in SPEC_NOTES.

The EOM overlaps itself (INT-026). 0x4B65A5B2 is ``X 11 X`` with X = 010010110110010, so if
a message *ends* with the EOM's first 17 bits, the window starting 17 bits before the real
EOM is also an exact match, and its "flush" passes too. Taking the first match would cut the
message 17 bits short. So when a candidate is found, every candidate overlapping it is
checked, and the *last* one that passes wins. Nothing can match after the real EOM: the
flush zeros keep every later window at least 7 bits away.

Streaming: :meth:`EomDetector.process` releases payload bits once no EOM can start at or
before them. Checking overlapping candidates needs 31 extra bits, so the last
32 + 144 + 31 - 1 bits are held back.
"""

import numpy as np

from .. import bits as bitops
from ..spec import tables as T

EOM_BITS = bitops.int_to_bits(T.EOM_SEQUENCE, T.EOM_BITS)
#: At the end of a signal, an EOM is accepted with a truncated flush only if at least this
#: many flush bits were received, so a near-EOM pattern in the last bits alone is not enough.
MIN_SHORT_FLUSH = 32


class EomDetector:
    def __init__(
        self,
        max_eom_errors: int = 2,
        confirm_bits: int = T.CODER_FLUSH_BITS,
        max_confirm_ones: int = 14,
    ):
        self.max_eom_errors = max_eom_errors
        self.confirm_bits = confirm_bits
        self.max_confirm_ones = max_confirm_ones
        self.found = False  # True once the EOM has been accepted
        self.payload_bits = None  # once found: how many payload bits came before the EOM
        self._released = 0  # payload bits already returned
        self._buffer = np.zeros(0, dtype=np.uint8)

    @property
    def _window(self) -> int:
        """Bits needed to judge one candidate: the EOM itself plus its flush."""
        return T.EOM_BITS + self.confirm_bits

    @property
    def _holdback(self) -> int:
        """Bits needed to judge a candidate and every candidate overlapping it."""
        return self._window + T.EOM_BITS - 1

    def process(self, bits: np.ndarray) -> np.ndarray:
        """Accept decoded bits; return payload bits that are now certain. After the EOM is
        found, everything else is discarded (flush bits and anything after them)."""
        if self.found:
            return np.zeros(0, dtype=np.uint8)
        self._buffer = np.concatenate([self._buffer, np.asarray(bits, dtype=np.uint8)])
        checkable = self._buffer.size - self._holdback + 1  # starts that can be fully judged
        if checkable <= 0:
            return np.zeros(0, dtype=np.uint8)
        p = self._find_eom(checkable, allow_short=False)
        if p is not None:
            return self._accept(p)
        out, self._buffer = self._buffer[:checkable], self._buffer[checkable:]
        self._released += out.size
        return out

    def finish(self) -> np.ndarray:
        """End of signal: look for an EOM whose flush was cut short, then release the rest.

        If no EOM is found, all remaining bits are returned and :attr:`found` stays False:
        the caller cannot know where the message ended.
        """
        if self.found:
            return np.zeros(0, dtype=np.uint8)
        p = self._find_eom(self._buffer.size - T.EOM_BITS + 1, allow_short=True)
        if p is not None:
            return self._accept(p)
        out, self._buffer = self._buffer, np.zeros(0, dtype=np.uint8)
        self._released += out.size
        return out

    def _find_eom(self, n_starts: int, allow_short: bool) -> int | None:
        """Position of the accepted EOM among the first ``n_starts`` starts, or None.

        The first passing candidate is found, then the last passing candidate that overlaps
        it is returned (INT-026)."""
        if n_starts <= 0:
            return None
        first = None
        for p in self._candidates(0, n_starts):
            if self._confirmed(p, allow_short):
                first = p
                break
        if first is None:
            return None
        last = first
        for q in self._candidates(
            first + 1, min(first + T.EOM_BITS, self._buffer.size - T.EOM_BITS + 1)
        ):
            if self._confirmed(q, allow_short):
                last = q
        return last

    def _candidates(self, start: int, stop: int):
        """Starts in [start, stop) whose 32 bits are within max_eom_errors of the EOM."""
        if stop <= start:
            return []
        windows = np.lib.stride_tricks.sliding_window_view(self._buffer, T.EOM_BITS)[start:stop]
        distance = np.count_nonzero(windows != EOM_BITS, axis=1)
        return (start + np.flatnonzero(distance <= self.max_eom_errors)).tolist()

    def _confirmed(self, p: int, allow_short: bool) -> bool:
        """Is the candidate at p followed by (mostly) zero flush bits?"""
        flush = self._buffer[p + T.EOM_BITS : p + self._window]
        if flush.size < self.confirm_bits:
            # Only at the end of the signal, and only with enough flush to mean something.
            if not allow_short or flush.size < MIN_SHORT_FLUSH:
                return False
        allowed = self.max_confirm_ones * flush.size / self.confirm_bits
        return np.count_nonzero(flush) <= allowed

    def _accept(self, p: int) -> np.ndarray:
        self.found = True
        self.payload_bits = self._released + p
        out = self._buffer[:p]
        self._buffer = np.zeros(0, dtype=np.uint8)
        return out
