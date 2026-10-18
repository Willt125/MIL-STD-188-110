"""Data-phase decoder: received data-phase symbols -> the bits that entered the FEC encoder.

The input is the stream of symbol estimates starting at the first data-phase symbol (right
after the preamble), already scaled so a noiseless symbol sits on the unit circle. Everything
the transmitter did after the FEC encoder is undone here, in reverse order:

    split frames into unknown (data) and known (probe) symbols    §5.3.2.2.2
    descramble                                                     §5.3.2.3.8
    soft-demap data symbols (or correlate 75-bps sets)             §5.3.2.3.7, §5.3.2.3.6
    deinterleave                                                   §5.3.2.3.4/5
    combine repeated pairs (150/300 bps)                           §5.3.2.3.3
    Viterbi decode                                                 §5.3.2.3.3

The probes are not needed to decode when the channel is known (M7), but they are known
symbols, so they give a free measurement of signal quality: :attr:`snr_db`. At 75 bps there
are no probes, so the same measurement is made against the pattern each set was decided to
be (decision-directed). A receiver that has lost track of the exceptional-set position
(INT-010) shows up there as a quality near 0 dB, even when the code still corrects the damage.

Streaming: :meth:`process` accepts any number of symbols and returns the bits that are
settled; :meth:`finish` returns the rest. Memory is bounded by one interleaver block.
"""

from collections import deque

import numpy as np

from .. import fec, scrambler, symbols
from ..interleave import Deinterleaver
from ..spec import tables as T
from ..spec.modes import ModeSpec
from . import demap

# Frames (sets) remembered for snr_db_before: ten seconds of symbols, far more than the
# demodulator runs ahead of the decoder (its one-second loss window, plus one block).
RECENT_SYMBOLS = 10 * T.SYMBOL_RATE


class DataDecoder:
    def __init__(
        self,
        mode: ModeSpec,
        noise_var: float = 1.0,
        traceback_depth: int = 96,
        start_state: int | None = 0,
    ):
        """``start_state``: the encoder's state at the first bit (0 at the start of a
        message, INT-023); None when decoding joins a message in progress (late entry)."""
        self.mode = mode
        self.noise_var = noise_var
        self._symbols_in = 0  # data-phase symbols consumed (scrambler position)
        self._frames = 0  # frames consumed (probe position within a block)
        self._sets = 0  # 75-bps sets consumed (exceptional-set position)
        self._pending = np.zeros(0, dtype=complex)  # symbols waiting for a whole frame/set
        self._pending_var = np.zeros(0)  # their noise variances
        self._deinterleaver = Deinterleaver(mode)
        self._repeat_pending = np.zeros(0)  # soft bits waiting for a whole repeated group
        self._viterbi = (
            fec.ViterbiDecoder(traceback_depth, start_state=start_state) if mode.coded else None
        )
        self._known_signal = 0.0  # accumulated |known (or decided) symbol|^2
        self._known_error = 0.0  # accumulated |received - known|^2
        # The same per frame (or set), for the last few seconds only: (first data-phase
        # symbol, signal, error). See snr_db_before.
        self._recent: deque = deque()
        self.incomplete_symbols = 0  # set by finish(): symbols lost in a partial frame
        self.incomplete_block_bits = 0  # set by finish(): bits lost in a partial block

    # -- public interface --------------------------------------------------------------

    @property
    def unit(self) -> int:
        """Symbols per frame (or per 75-bps set): the decoder works one unit at a time."""
        return T.TRIBITS_PER_75BPS_SET if self.mode.is_75bps else self.mode.frame_symbols

    @property
    def snr_db(self) -> float | None:
        """Signal-to-noise ratio per symbol, measured on the probes (or, at 75 bps, against
        the decided patterns). None before anything has been measured."""
        if self._known_error == 0.0:
            return None if self._known_signal == 0.0 else float("inf")
        return 10 * np.log10(self._known_signal / self._known_error)

    def snr_db_before(self, end_symbol: int) -> float | None:
        """:attr:`snr_db` counting only frames (sets) that start before data-phase symbol
        ``end_symbol``. The demodulator runs ahead of the decoder, so by the time the EOM
        says where a message ended, the decoder has also measured whatever followed it:
        another transmission, read as this one, measures as noise."""
        signal, error = self._known_signal, self._known_error
        for start, s, e in self._recent:
            if start >= end_symbol:
                signal, error = signal - s, error - e
        if error <= 0.0:
            return None if signal <= 0.0 else float("inf")
        return 10 * np.log10(signal / error)

    def _record(self, start: int, signal: float, error: float) -> None:
        self._known_signal += signal
        self._known_error += error
        self._recent.append((start, signal, error))
        while self._recent and self._recent[0][0] < start - RECENT_SYMBOLS:
            self._recent.popleft()

    def process(self, y: np.ndarray, noise_var: np.ndarray | None = None) -> np.ndarray:
        """Accept more data-phase symbols; return decoded bits that are now settled.

        ``noise_var`` is the error variance of each symbol, if the demodulator knows it
        (channel-state information). Soft bits are divided by it, so symbols received in a
        fade carry less weight. Without it, the decoder's fixed ``noise_var`` is used.
        """
        y = np.asarray(y, dtype=complex)
        var = np.full(y.size, self.noise_var) if noise_var is None else np.asarray(noise_var)
        if var.shape != y.shape:
            raise ValueError("one noise variance per symbol")
        self._pending = np.concatenate([self._pending, y])
        self._pending_var = np.concatenate([self._pending_var, var.astype(float)])
        usable = self._pending.size - self._pending.size % self.unit
        ready, self._pending = self._pending[:usable], self._pending[usable:]
        ready_var, self._pending_var = self._pending_var[:usable], self._pending_var[usable:]
        if ready.size == 0:
            return np.zeros(0, dtype=np.uint8)
        y_clean = demap.descramble(ready, scrambler.data_sequence(self._symbols_in, ready.size))
        self._symbols_in += ready.size
        if self.mode.is_75bps:
            soft = self._soft_bits_75(y_clean, ready_var)
        else:
            soft = self._soft_bits_framed(y_clean, ready_var)
        return self._decode(soft)

    def finish(self) -> np.ndarray:
        """End of signal: return the remaining bits.

        If the signal was cut off inside an interleaver block, the missing positions are
        filled with erasures (soft value 0, "no information") and the block is decoded anyway.
        The code then recovers whatever the received part supports, instead of discarding up to
        4.8 s of data. A partial frame is too short to use and is dropped.
        :attr:`incomplete_symbols` and :attr:`incomplete_block_bits` record what was missing.
        """
        self.incomplete_symbols = self._pending.size
        self.incomplete_block_bits = self._deinterleaver.pending
        out = []
        if self._deinterleaver.pending:
            missing = self.mode.interleaver_bits - self._deinterleaver.pending
            out.append(self._decode(np.zeros(missing)))
        if self._viterbi is not None:
            out.append(self._viterbi.flush(terminated=False))
        return np.concatenate(out) if out else np.zeros(0, dtype=np.uint8)

    # -- stages ------------------------------------------------------------------------

    def _soft_bits_framed(self, y: np.ndarray, var: np.ndarray) -> np.ndarray:
        unknown = self.mode.unknown_per_frame
        frames = y.reshape(-1, self.mode.frame_symbols)
        data = frames[:, :unknown].reshape(-1)
        data_var = var.reshape(-1, self.mode.frame_symbols)[:, :unknown].reshape(-1)
        for probe in frames[:, unknown:]:
            self._measure_probe(probe)
        return demap.soft_bits(data, self.mode.bits_per_channel_symbol, data_var)

    def _measure_probe(self, probe: np.ndarray) -> None:
        position = self._frames % self.mode.frames_per_block
        d1, d2 = self.mode.d1_d2
        d_value = {self.mode.frames_per_block - 2: d1, self.mode.frames_per_block - 1: d2}
        known = np.exp(1j * np.pi / 4 * symbols.probe_tribits(self.mode, d_value.get(position)))
        self._record(
            self._frames * self.mode.frame_symbols,
            float(np.sum(np.abs(known) ** 2)),
            float(np.sum(np.abs(probe - known) ** 2)),
        )
        self._frames += 1

    def _soft_bits_75(self, y: np.ndarray, var: np.ndarray) -> np.ndarray:
        corr = demap.set_correlations(y, self.mode, self._sets)
        decided = demap.set_patterns(self.mode, self._sets, np.argmax(corr, axis=1))
        sets = y.reshape(decided.shape)
        signal = np.sum(np.abs(decided) ** 2, axis=1)
        error = np.sum(np.abs(sets - decided) ** 2, axis=1)
        for i in range(len(corr)):
            start = (self._sets + i) * T.TRIBITS_PER_75BPS_SET
            self._record(start, float(signal[i]), float(error[i]))
        self._sets += len(corr)
        set_var = var.reshape(-1, T.TRIBITS_PER_75BPS_SET).mean(axis=1)
        return demap.soft_bits_75(corr, set_var)

    def _decode(self, soft: np.ndarray) -> np.ndarray:
        soft = self._deinterleaver.process(soft)
        if self._viterbi is None:  # 4800 bps: no FEC, hard decisions
            return (soft < 0).astype(np.uint8)
        group = 2 * self.mode.repetitions
        soft = np.concatenate([self._repeat_pending, soft])
        usable = soft.size - soft.size % group
        self._repeat_pending = soft[usable:]
        combined = fec.combine_pairs(soft[:usable], self.mode.repetitions)
        return self._viterbi.decode(combined)
