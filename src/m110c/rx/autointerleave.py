"""Telling zero interleave from short interleave at the receiver (S4, INT-012).

Zero and short interleave send the same preamble (Table XI gives them the same D1/D2), the
same probes and the same frames: up to the deinterleaver, the receiver cannot tell them
apart. The standard leaves it to the two ends to agree, and lists telling them apart
automatically as a design objective (§5.3.2.3.7.2.1 note).

The data can tell. :class:`InterleaveDetectingDecoder` feeds the same symbols to two
decoders, one deinterleaving as for short interleave and one not, and compares how well
each one's soft bits look like the convolutional code: the Viterbi decoder's best path
metric over the most any path could score (:attr:`m110c.fec.ViterbiDecoder.fit`). Under the
right hypothesis the soft bits are a code sequence plus noise, and fit close to 1; under the
wrong one they are a code sequence shuffled (by the deinterleaver, or by its absence), which
no code path follows, and fit like noise. Both hypotheses see the very same soft bits, only
in another order, so the two fits can be compared directly.

Decision rule, measured with ``tools/interleave_check.py`` (docs/PERFORMANCE.md, S4): after
each 0.6-s block, the hypothesis ahead by ``DECIDE_MARGIN`` wins. Of 1097 receptions (every
rate, both truths, noise-only and Table XVI fading channels, -9 to 18 dB), every message that
could be decoded at all was ahead by 0.036 or more after one block, in the right direction;
wrong-way margins, up to 0.039, came only from messages that neither hypothesis could
decode. Without a decision after ``MAX_BLOCKS`` blocks, the larger fit wins. An EOM found
under one hypothesis decides at once. A signal that ends inside its first block (no EOM
found) was zero interleave, since short interleave always sends whole blocks; one that ends
later, undecided, goes to the larger fit over its whole blocks.

Cost: none when short interleave is sent (its decoder waits for a whole block anyway); up to
one block (0.6 s) of extra delay when zero interleave is sent, while its bits are held.
"""

import numpy as np

from ..spec.modes import ModeSpec, mode
from .decode import DataDecoder
from .eom import EomDetector

DECIDE_MARGIN = 0.02
MAX_BLOCKS = 3


def applies_to(m: ModeSpec) -> bool:
    """Whether a detected mode could be zero interleave instead: short interleave below
    4800 bps (4800 has its own D1/D2), and not digital voice (short only, INT-019)."""
    return m.interleave == "short" and not m.voice


class InterleaveDetectingDecoder:
    """A :class:`~m110c.rx.decode.DataDecoder` for a short-interleave detection that
    decides, from the data, whether the transmission is really zero interleave.

    Same interface as :class:`DataDecoder`. Until it decides, bits are held back; then the
    winner's bits so far are released and the winner decodes the rest alone."""

    def __init__(self, detected: ModeSpec):
        if not applies_to(detected):
            raise ValueError(f"{detected} cannot be zero interleave")
        self._candidates = {
            "short": DataDecoder(detected),
            "zero": DataDecoder(mode(detected.rate, "zero")),
        }
        self._eoms = {name: EomDetector() for name in self._candidates}
        self._held = {name: [] for name in self._candidates}
        self._block = detected.block_symbols  # the same for both
        self._pending = np.zeros(0, dtype=complex)
        self._pending_var = np.zeros(0)
        self.decided: str | None = None  # "short" or "zero"
        self.blocks_judged = 0
        self.margin: float | None = None  # fit(short) - fit(zero) when last judged
        self.reason: str | None = None  # what decided: "margin", "EOM", "end of signal", ...

    # -- the DataDecoder interface ------------------------------------------------------

    @property
    def mode(self) -> ModeSpec:
        return self._candidates[self.decided or "short"].mode

    @property
    def snr_db(self):
        return self._candidates[self.decided or "short"].snr_db

    def snr_db_before(self, end_symbol: int):
        return self._candidates[self.decided or "short"].snr_db_before(end_symbol)

    def process(self, y: np.ndarray, noise_var: np.ndarray | None = None) -> np.ndarray:
        y = np.asarray(y, dtype=complex)
        var = np.ones(y.size) if noise_var is None else np.asarray(noise_var, dtype=float)
        if self.decided:
            return self._candidates[self.decided].process(y, var)
        self._pending = np.concatenate([self._pending, y])
        self._pending_var = np.concatenate([self._pending_var, var])
        # Whole blocks only, so that both decoders have consumed the same soft bits when
        # their fits are compared (the short one decodes nothing before a block is full).
        while self._pending.size >= self._block and not self.decided:
            block, self._pending = self._pending[: self._block], self._pending[self._block :]
            block_var = self._pending_var[: self._block]
            self._pending_var = self._pending_var[self._block :]
            self._feed(block, block_var)
            self.blocks_judged += 1
            self._judge()
        if not self.decided:
            return np.zeros(0, dtype=np.uint8)
        rest, rest_var = self._pending, self._pending_var
        self._pending, self._pending_var = np.zeros(0, dtype=complex), np.zeros(0)
        winner = self._candidates[self.decided]
        return np.concatenate([*self._held[self.decided], winner.process(rest, rest_var)])

    def finish(self) -> np.ndarray:
        if self.decided:
            return self._candidates[self.decided].finish()
        whole_blocks = self.blocks_judged
        self._feed(self._pending, self._pending_var)
        for name, decoder in self._candidates.items():
            bits = decoder.finish()
            self._held[name].append(bits)
            self._eoms[name].process(bits)
            self._eoms[name].finish()  # the signal has ended: a short flush may do
        if not self._decide_by_eom():
            if whole_blocks == 0:
                # Short interleave always sends whole blocks; a signal that ended inside
                # its first one was zero interleave. (Fits are no help here: the short
                # decoder fills the rest of its block with erasures, and a few soft bits
                # scattered among erasures fit any code.)
                self._decide("zero", "ended inside the first block")
            else:  # judged on whole blocks only, for the same reason
                self._decide("zero" if self.margin < 0 else "short", "larger fit")
        return np.concatenate(self._held[self.decided])

    # -- deciding -------------------------------------------------------------------------

    def _feed(self, y: np.ndarray, var: np.ndarray) -> None:
        for name, decoder in self._candidates.items():
            bits = decoder.process(y, var)
            self._held[name].append(bits)
            self._eoms[name].process(bits)

    def _fit(self, name: str) -> float | None:
        viterbi = self._candidates[name]._viterbi
        return None if viterbi is None else viterbi.fit

    def _judge(self) -> None:
        if self._decide_by_eom():
            return
        short, zero = self._fit("short"), self._fit("zero")
        if short is None or zero is None:
            return
        self.margin = short - zero
        if abs(self.margin) >= DECIDE_MARGIN:
            self._decide("short" if self.margin > 0 else "zero", "margin")
        elif self.blocks_judged >= MAX_BLOCKS:
            self._decide("short" if self.margin >= 0 else "zero", "larger fit")

    def _decide_by_eom(self) -> bool:
        found = [name for name, eom in self._eoms.items() if eom.found]
        if len(found) == 1:
            self._decide(found[0], "EOM")
            return True
        return False

    def _decide(self, name: str, reason: str) -> None:
        self.decided, self.reason = name, reason
        self._eoms = None  # no longer needed
