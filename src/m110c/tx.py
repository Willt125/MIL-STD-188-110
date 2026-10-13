"""Transmit framer: user bits -> the complete stream of scrambled tribits (§5.3.2.2).

This module wires the Figure 2 blocks together and runs the four time phases of §5.3.2.1:

a. **Sync preamble.** Table XIII patterns of the preamble channel symbols, scrambled with
   the 32-symbol sync sequence.
b. **Data.** User bits -> FEC encoder -> repetition -> interleaver -> MGD and symbol
   formation. Frames of unknown (data) symbols are followed by known (probe) symbols. The
   last two probes of every block carry D1 and D2 (INT-005). Everything is scrambled with
   the 160-symbol data sequence, counted from the first data symbol (INT-004).
c. **EOM.** The 32-bit EOM sequence enters the FEC encoder like data (§5.3.2.2.3).
d. **Flush.** 144 zero bits, then (with an interleaver) more zero bits until the
   interleaver block is complete (§5.3.2.3.2). Without an interleaver, the last frame is
   completed with zero channel bits (INT-013).

Usage::

    tx = Transmitter(mode(2400, "long"))
    out = [tx.process(bits) for bits in chunks]   # any chunking, any number of calls
    out.append(tx.finish())                        # EOM and flush

Output appears as soon as it is fully determined: the preamble on the first call, then
each complete frame (or 75-bps set), so the stream is preamble, block 1, block 2, ...
(INT-014). There is no idle fill: the framer waits for input, and :meth:`finish` (end of
input) starts the EOM phase (INT-017). With an interleaver, nothing can leave until a whole
block of input has arrived. At most one interleaver block and one frame of channel bits
are ever buffered.

The output is tribit numbers (0..7); :mod:`m110c.modulate` turns them into samples.
"""

import numpy as np

from . import bits as bitops
from . import fec, scrambler, symbols
from .framing import AsyncFormat, async_encode, sync_encode
from .interleave import Interleaver
from .spec import tables as T
from .spec.modes import ModeSpec


class Transmitter:
    """Streaming transmit framer for one message in one mode."""

    def __init__(self, mode: ModeSpec):
        self.mode = mode
        self._encoder = fec.ConvolutionalEncoder() if mode.coded else None
        self._interleaver = Interleaver(mode)
        self._pending = np.zeros(0, dtype=np.uint8)  # channel bits not yet in a frame/set
        self._input_bits = 0  # user + EOM + flush bits accepted so far
        self._data_symbols = 0  # data-phase symbols sent so far (scrambler position)
        self._frames = 0  # frames sent so far (probe position within a block)
        self._sets = 0  # 75-bps sets sent so far (exceptional-set position)
        self._started = False
        self._finished = False

    # -- public interface ----------------------------------------------------------

    def process(self, user_bits: np.ndarray) -> np.ndarray:
        """Accept more user bits; return every tribit that is now determined."""
        if self._finished:
            raise RuntimeError("transmission already finished")
        out = [self._preamble()]
        out.append(self._accept(np.asarray(user_bits, dtype=np.uint8)))
        return np.concatenate(out)

    def finish(self) -> np.ndarray:
        """End of message (RTS dropped): send EOM and flush; return the remaining tribits."""
        if self._finished:
            raise RuntimeError("transmission already finished")
        out = [self._preamble()]
        # c. EOM phase.
        out.append(self._accept(bitops.int_to_bits(T.EOM_SEQUENCE, T.EOM_BITS)))
        # d. Flush phase: coder flush, then fill the interleaver block if there is one.
        out.append(self._accept(np.zeros(T.CODER_FLUSH_BITS, dtype=np.uint8)))
        if self.mode.has_interleaver:
            out.append(self._accept(np.zeros(self._bits_to_block_end(), dtype=np.uint8)))
            self._interleaver.flush()
        else:
            # INT-013: complete the last frame (or 75-bps dibit) with zero channel bits.
            unit = self._channel_bits_per_unit()
            pad = (-self._pending.size) % unit
            out.append(self._form(np.zeros(pad, dtype=np.uint8)))
        if self._pending.size:
            raise AssertionError("framer left channel bits unsent")  # cannot happen
        self._finished = True
        return np.concatenate(out)

    # -- phase a: preamble ---------------------------------------------------------

    def _preamble(self) -> np.ndarray:
        """The scrambled sync preamble, the first time it is asked for; then nothing."""
        if self._started:
            return np.zeros(0, dtype=np.uint8)
        self._started = True
        tribits = symbols.preamble_tribits(self.mode)
        return scrambler.scramble(tribits, scrambler.sync_sequence(0, tribits.size))

    # -- phases b-d: bits through the coding chain -----------------------------------

    def _accept(self, bits: np.ndarray) -> np.ndarray:
        """Bits entering at switch S1 (data, EOM or flush) -> any completed tribits."""
        self._input_bits += bits.size
        if self._encoder is None:  # 4800 bps: FEC bypassed (§5.3.2.3.3 c)
            channel = bits
        else:
            coded = self._encoder.encode(bits)
            channel = fec.repeat_pairs(coded, self.mode.repetitions)
        return self._form(self._interleaver.process(channel))

    def _bits_to_block_end(self) -> int:
        """Zero input bits still needed to fill the current interleaver block."""
        coded_per_input = 2 * self.mode.repetitions
        coded_so_far = self._input_bits * coded_per_input
        missing = (-coded_so_far) % self.mode.interleaver_bits
        # Always a whole number of input bits for the Table VI sizes (tested).
        assert missing % coded_per_input == 0
        return missing // coded_per_input

    def _channel_bits_per_unit(self) -> int:
        """Channel bits consumed by one frame, or by one 75-bps set."""
        if self.mode.is_75bps:
            return self.mode.bits_per_channel_symbol
        return self.mode.channel_bits_per_frame

    # -- symbol formation, framing and scrambling --------------------------------------

    def _form(self, channel_bits: np.ndarray) -> np.ndarray:
        """Turn every complete frame (or set) of buffered channel bits into tribits."""
        self._pending = np.concatenate([self._pending, channel_bits])
        unit = self._channel_bits_per_unit()
        usable = self._pending.size - self._pending.size % unit
        ready, self._pending = self._pending[:usable], self._pending[usable:]
        if self.mode.is_75bps:
            tribits = symbols.data_sets_75bps(ready, self.mode, self._sets)
            self._sets += usable // unit
        else:
            tribits = np.concatenate(
                [self._frame(f) for f in ready.reshape(-1, unit)] or [np.zeros(0, np.uint8)]
            )
        return self._scramble_data(tribits)

    def _frame(self, frame_bits: np.ndarray) -> np.ndarray:
        """One frame: unknown symbols then a probe (§5.3.2.2.2; S2 starts at UNKNOWN)."""
        position = self._frames % self.mode.frames_per_block
        d1, d2 = self.mode.d1_d2
        d_value = {self.mode.frames_per_block - 2: d1, self.mode.frames_per_block - 1: d2}
        probe = symbols.probe_tribits(self.mode, d_value.get(position))  # INT-005
        self._frames += 1
        return np.concatenate([symbols.data_tribits(frame_bits, self.mode), probe])

    def _scramble_data(self, tribits: np.ndarray) -> np.ndarray:
        seq = scrambler.data_sequence(self._data_symbols, tribits.size)
        self._data_symbols += tribits.size
        return scrambler.scramble(tribits, seq)


def data_phase_symbols(mode: ModeSpec, n_user_bits: int) -> int:
    """Channel symbols that the data, EOM and flush phases of a message take, after the
    preamble: the framer's arithmetic, without encoding anything. The receiver uses it to
    place the end of a message whose EOM it has found."""
    per_input_bit = 2 * mode.repetitions if mode.coded else 1  # rate-1/2 code, repeated
    channel_bits = (n_user_bits + T.EOM_BITS + T.CODER_FLUSH_BITS) * per_input_bit
    if mode.has_interleaver:  # flushed to the end of the interleaver block
        channel_bits = -(-channel_bits // mode.interleaver_bits) * mode.interleaver_bits
    if mode.is_75bps:  # one 32-tribit set per channel symbol
        sets = -(-channel_bits // mode.bits_per_channel_symbol)
        return sets * mode.tribits_per_channel_symbol
    frames = -(-channel_bits // mode.channel_bits_per_frame)  # the last frame completed
    return frames * mode.frame_symbols


def transmit(user_bits: np.ndarray, mode: ModeSpec) -> np.ndarray:
    """One-shot: the complete tribit stream for a message given as bits."""
    tx = Transmitter(mode)
    return np.concatenate([tx.process(user_bits), tx.finish()])


def transmit_bytes(
    data: bytes,
    mode: ModeSpec,
    msb_first: bool = False,
    async_format: AsyncFormat | None = None,
) -> np.ndarray:
    """One-shot: the complete tribit stream for a message given as bytes.

    By default bytes are sent synchronously, LSB first (INT-016). With ``async_format`` each
    byte is sent as an asynchronous character with start/stop bits (INT-025).
    """
    if async_format is not None:
        return transmit(async_encode(data, async_format), mode)
    return transmit(sync_encode(data, msb_first=msb_first), mode)
