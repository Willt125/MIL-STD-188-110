"""The complete receiver: samples in, user bytes out, any number of messages.

State machine::

    SEARCH --preamble found--> DATA --EOM, or signal lost, or end of input--> SEARCH ...

* SEARCH runs :class:`~m110c.rx.acquire.PreambleSearch` on the incoming samples.
* On a detection, a :class:`~m110c.rx.track.TrackingDemodulator` is started at the detected
  segment, and a :class:`~m110c.rx.decode.DataDecoder`, an
  :class:`~m110c.rx.eom.EomDetector` and a deframer follow it.
* When the message ends, searching resumes from where the demodulator stopped, so
  back-to-back transmissions are each received.

A detection is only reported once the whole preamble segment has arrived (the search
must read its D1, D2 and count). So raw samples are buffered, and the demodulator is
first fed from the segment start. The buffer is trimmed to what may still be needed.

Bytes are returned as soon as they are certain. Each finished message is recorded in
:attr:`Receiver.messages` with its diagnostics.
"""

from dataclasses import dataclass, field

import numpy as np

from .. import bits as bitops
from .._blas import single_threaded_blas
from ..framing import AsyncDecoder, AsyncFormat
from ..modulate import DEFAULT_ROLLOFF
from ..resample import INTERNAL_RATE, Resampler
from ..spec import tables as T
from ..spec.modes import ModeSpec
from ..tx import data_phase_symbols
from .acquire import Detection, PreambleSearch
from .autointerleave import InterleaveDetectingDecoder, applies_to
from .decode import DataDecoder
from .eom import EomDetector
from .equalize import EqualizingDemodulator
from .lateentry import LateEntry, LateEntrySearch
from .track import TrackingDemodulator

# The end of a message can lie this far behind the newest sample: the demodulator holds
# output back for a second, and judges loss over a second (equalize.LOSS_SECONDS).
RAW_HISTORY_SECONDS = 2.5


@dataclass
class Message:
    """Diagnostics for one received message."""

    mode: ModeSpec
    detection: Detection
    n_bytes: int = 0
    eom_found: bool = False
    lost: bool = False
    snr_db: float | None = None
    clock_ppm: float = 0.0
    end_sample: int = 0
    notes: list[str] = field(default_factory=list)
    # Interleave told from the data (S4): None if not attempted (forced, long, 4800...),
    # else "zero" or "short" once decided, "deciding" until then.
    interleave_detection: str | None = None
    late_entry: bool = False  # joined without its preamble (S5); detection is a LateLock
    # With Receiver(trace=True): the demodulator's state after each unit (TraceUnit).
    trace: list = field(default_factory=list)


class Receiver:
    def __init__(
        self,
        sample_rate: int = 9600,
        carrier_hz: float = T.CARRIER_HZ,
        max_offset_hz: float = 400.0,
        msb_first: bool = False,
        async_format: AsyncFormat | None = None,
        zero_interleave: bool = False,
        interleave: str = "auto",
        invert_spectrum: bool = False,
        threshold: float | None = None,
        equalize: bool = True,
        voice: bool = False,
        trace: bool = False,
        late_entry: bool = False,
        rolloff: float = DEFAULT_ROLLOFF,
    ):
        # The receiver runs at the input rate when it is a multiple of 2400 S/s; other rates
        # (8000, 44100, ...) are resampled to 9600 S/s first (S3). Sample positions in the
        # diagnostics (Detection.segment_start, Message.end_sample) count at sample_rate.
        self.input_rate = sample_rate
        native = sample_rate % T.SYMBOL_RATE == 0
        self._resampler = None if native else Resampler(sample_rate, INTERNAL_RATE)
        self.sample_rate = sample_rate if native else INTERNAL_RATE
        self.carrier_hz = carrier_hz
        self.max_offset_hz = max_offset_hz
        self.msb_first = msb_first
        self.async_format = async_format
        # Short and zero interleave share their preambles (INT-012): "auto" tells them apart
        # from the data (rx/autointerleave.py), "short" or "zero" assumes one.
        # zero_interleave=True is the older spelling of interleave="zero".
        if interleave not in ("auto", "short", "zero"):
            raise ValueError(f"interleave must be auto, short or zero, not {interleave!r}")
        self.interleave = "zero" if zero_interleave else interleave
        self.zero_interleave = self.interleave == "zero"
        self.invert_spectrum = invert_spectrum
        self.threshold = threshold
        self.equalize = equalize  # False: the M8 single-gain tracker (for comparison)
        # Also receive 2400 bps digital voice (INT-019). Its payload is a vocoder's bit
        # stream, so it is always delivered as raw synchronous bits, never async-deframed.
        self.voice = voice
        # Record each message's demodulator state per unit in Message.trace (for figures and
        # diagnosis; memory grows with the message). Equalising demodulator only.
        self.trace = trace
        # Also join transmissions already in progress, without their preamble (S5,
        # rx/lateentry.py): costs about a quarter of real time in CPU while no message is
        # being received. Needs the equaliser.
        self.late_entry = late_entry and equalize
        # The receive filters' root-raised-cosine roll-off. Best matched to the
        # transmitter's, but from 0.15 to 0.35 a mismatch cost nothing measurable
        # (docs/PERFORMANCE.md, roll-off).
        self.rolloff = rolloff
        self._late = None  # the late-entry attempt in progress, if any
        self.messages: list[Message] = []
        self._message: Message | None = None
        self._raw = np.zeros(0, dtype=complex)  # recent input
        self._raw_start = 0  # global index of _raw[0]
        self._samples_in = 0
        self._start_search(0)

    # -- public ------------------------------------------------------------------------

    @property
    def receiving(self) -> Message | None:
        """The message being received now (its preamble found, its end not yet reached),
        or None. It moves to :attr:`messages` when it ends."""
        return self._message if self._demod is not None else None

    @single_threaded_blas
    def process(self, samples: np.ndarray) -> bytes:
        x = np.asarray(samples)
        if not np.all(np.isfinite(x)):
            # A NaN or infinite sample (a corrupt file, random bytes read as cf32) carries no
            # information: treat it as silence rather than let it poison every filter.
            x = np.where(np.isfinite(x), x, 0)
        if self.invert_spectrum:
            x = np.conj(x)  # INT-021: a lower-sideband signal is the mirror image
        if self._resampler is not None:
            x = self._resampler.process(x)
        return self._consume(x)

    def _consume(self, x: np.ndarray) -> bytes:
        """Samples at sample_rate, ready for the search and the demodulator."""
        self._raw = np.concatenate([self._raw, x])
        self._samples_in += x.size
        out = bytearray()
        new = x
        while True:
            if self._demod is None:
                detection = self._search.process(new)
                if detection is not None:  # (a late-entry attempt is dropped at its end)
                    self._start_message(detection)
                    out += self._feed_message(self._raw_from(self._demod_start))
                else:
                    # A late message, if one starts, has been fed every sample so far.
                    started = self._late_step(new) if self.late_entry else None
                    if started is None:
                        break
                    out += started
            else:
                out += self._feed_message(new)
            if self._demod is not None:
                break
            # The message ended inside this batch: search the rest of it.
            new = self._raw_from(self._search_from)
        self._trim()
        return bytes(out)

    @single_threaded_blas
    def finish(self) -> bytes:
        """End of input: close any message in progress, and receive any further
        transmissions in the samples still buffered (the input may end soon after one)."""
        out = b""
        if self._resampler is not None:  # the resampler's last outputs
            out += self._consume(self._resampler.flush())
        self._late = None  # a late entry not yet verified when the input ends is dropped
        unsearched = self._demod is not None  # samples after the message's end, if so
        if unsearched:
            out += self._end_message(final=True)
        last_start = None
        while True:
            detection = None
            if unsearched:  # a new search starts at the message's end: give it the rest
                detection = self._search.process(self._raw_from(self._search_from))
            detection = detection or self._search.flush()
            if detection is None or (
                last_start is not None and detection.segment_start <= last_start
            ):
                return out  # (the second test only guards against looping on one preamble)
            last_start = detection.segment_start
            self._start_message(detection)
            out += self._feed_message(self._raw_from(self._demod_start))
            if self._demod is not None:  # still in progress where the input ends
                out += self._end_message(final=True)
            unsearched = True

    # -- message lifecycle --------------------------------------------------------------

    def _start_search(self, position: int) -> None:
        options = {} if self.threshold is None else {"threshold": self.threshold}
        # Let the old search go first: each holds ~17 MB of precomputed FFTs, and two at
        # once at every message end set the receiver's peak memory.
        self._search = None
        self._search = PreambleSearch(
            self.sample_rate,
            self.carrier_hz,
            self.max_offset_hz,
            zero_interleave=self.zero_interleave,
            voice=self.voice,
            rolloff=self.rolloff,
            **options,
        )
        # The search counts samples from the position it starts at.
        self._search._samples_in = position
        self._search._z_start = position
        self._search._next = position
        self._search_from = position
        self._demod = None
        self._late = None
        if self.late_entry:
            self._late_search = LateEntrySearch(
                self.sample_rate,
                self.carrier_hz,
                self.max_offset_hz,
                start=position,
                rolloff=self.rolloff,
            )

    def _late_step(self, new: np.ndarray) -> bytes | None:
        """Late entry, while no message is being received: search for a transmission in
        progress, and follow an attempt to join one. Returns the first output (possibly
        empty) once a message starts this way, else None."""
        if self._late is None:
            lock = self._late_search.process(new)
            if lock is None:
                return None
            self._late = LateEntry(lock, self.sample_rate, rolloff=self.rolloff)
            confirmed = self._late.process(self._late_search.samples_from(self._late.start_index))
        else:
            confirmed = self._late.process(new)
        if self._late.failed:  # no verified block boundary: search on from here
            self._late = None
            self._late_search = LateEntrySearch(
                self.sample_rate,
                self.carrier_hz,
                self.max_offset_hz,
                start=self._samples_in,
                rolloff=self.rolloff,
            )
            return None
        return self._start_late_message() if confirmed else None

    def _start_late_message(self) -> bytes:
        late, self._late = self._late, None
        m, lock = late.mode, late.lock
        self._message = Message(mode=m, detection=lock, late_entry=True)
        margin = (
            "" if late.margin is None else f", verified by decoding (margin {late.margin:+.3f})"
        )
        self._message.notes.append(f"late entry: joined at an interleaver block{margin}")
        if self.async_format is None and not _whole_bytes_per_block(m):
            self._message.notes.append(
                "late entry: a block is not a whole number of bytes at this mode, so the "
                "synchronous byte boundaries are unknown (asynchronous framing finds its own)"
            )
        self._demod, self._demod_start = late.demod, late.start_index
        self._decoder = DataDecoder(m, start_state=None)  # joined mid-message
        self._eom = EomDetector()
        self._async = (
            AsyncDecoder(self.async_format, find_phase=True) if self.async_format else None
        )
        self._bit_pending = np.zeros(0, dtype=np.uint8)
        z, v = late.symbols_from_block()
        payload = self._eom.process(self._decoder.process(z, v))
        out = self._deframe(payload)
        if self._eom.found or self._demod.lost:
            out += self._end_message(final=False)
        return out

    def _start_message(self, detection: Detection) -> None:
        m = detection.mode
        self._message = Message(mode=m, detection=detection)
        # Start a few samples early (interpolation needs neighbours), but never before the
        # stream itself began: the demodulator treats earlier samples as zero anyway.
        self._demod_start = max(int(np.floor(detection.segment_start)) - 8, self._raw_start)
        demodulator = EqualizingDemodulator if self.equalize else TrackingDemodulator
        options = {"trace": self._message.trace} if self.trace and self.equalize else {}
        self._demod = demodulator(
            m,
            detection,
            self.sample_rate,
            rolloff=self.rolloff,
            start_index=self._demod_start,
            **options,
        )
        if self.interleave == "auto" and applies_to(m):
            self._decoder = InterleaveDetectingDecoder(m)
            self._message.interleave_detection = "deciding"
        else:
            self._decoder = DataDecoder(m)
        self._eom = EomDetector()
        self._async = AsyncDecoder(self.async_format) if self.async_format and not m.voice else None
        self._bit_pending = np.zeros(0, dtype=np.uint8)

    def _feed_message(self, samples) -> bytes:
        symbols, noise_var = self._demod.process(samples)
        payload = self._eom.process(self._decoder.process(symbols, noise_var))
        self._note_interleave()
        out = self._deframe(payload)
        if self._eom.found or self._demod.lost:
            out += self._end_message(final=False)
        return out

    def _end_message(self, final: bool) -> bytes:
        demod, decoder, eom = self._demod, self._decoder, self._eom
        out = b""
        if not demod.lost and not eom.found:
            out += self._deframe(eom.process(decoder.process(*demod.finish())))
        if not eom.found:
            out += self._deframe(eom.process(decoder.finish()))
            out += self._deframe(eom.finish(), final=True)
        self._note_interleave()  # a decision made while finishing
        msg = self._message
        msg.eom_found = eom.found
        msg.lost = demod.lost and not eom.found  # silence after a complete message is not a loss
        msg.clock_ppm = demod.clock_ppm
        msg.end_sample = self._message_end(demod, eom)
        if eom.found:  # measure the message only, not whatever followed it
            msg.snr_db = decoder.snr_db_before(data_phase_symbols(msg.mode, eom.payload_bits))
        else:
            msg.snr_db = decoder.snr_db
        self.messages.append(msg)
        self._start_search(max(msg.end_sample, self._raw_start))
        return out

    def _note_interleave(self) -> None:
        """Once the interleave has been told from the data, the message takes that mode."""
        decoder, msg = self._decoder, self._message
        if msg.interleave_detection == "deciding" and decoder.decided:
            msg.mode = decoder.mode
            msg.interleave_detection = decoder.decided
            margin = "" if decoder.margin is None else f", margin {decoder.margin:+.3f}"
            msg.notes.append(
                f"interleave: {decoder.decided} ({decoder.reason}, after "
                f"{decoder.blocks_judged} block(s){margin})"
            )

    def _message_end(self, demod, eom) -> int:
        """Where the transmission ended, so the search for the next one can start there.

        With an EOM, the end is known exactly: the framer's arithmetic gives the length of
        the data phase from the number of payload bits before it (:func:`data_phase_symbols`).
        Without one, the demodulator's position is all there is, and it runs about a second
        ahead of the decoder (it holds output back while it judges loss of signal). When the
        signal stops, it declares loss where its final run of bad units began, which is
        close. When another transmission follows at once, it may not declare loss at all:
        a 75-bps demodulator finds a 2400-bps preamble a plausible signal. Placing the end
        from the EOM is what lets back-to-back messages of different rates both be received
        (M12)."""
        if eom.found:
            msg = self._message
            length = data_phase_symbols(msg.mode, eom.payload_bits)
            end = demod.symbol_time(msg.detection.data_start_symbol + length)
        elif demod.lost and demod.end_symbol is not None:
            end = demod.symbol_time(demod.end_symbol)
        else:
            end = demod.position
        return int(min(max(end, 0), demod.position))

    def _deframe(self, payload_bits: np.ndarray, final: bool = False) -> bytes:
        if self._async is not None:
            out = self._async.process(payload_bits, final=final)
        else:
            bits = np.concatenate([self._bit_pending, payload_bits])
            whole = bits.size - bits.size % 8
            self._bit_pending = bits[whole:] if not final else np.zeros(0, dtype=np.uint8)
            out = bitops.bits_to_bytes(bits[:whole], msb_first=self.msb_first)  # INT-016
        self._message.n_bytes += len(out)
        return out

    def _raw_from(self, index: int) -> np.ndarray:
        """Buffered raw samples from global ``index`` on. Fails loudly if they are gone:
        a negative slice start would silently count from the end instead."""
        offset = index - self._raw_start
        if offset < 0:
            raise RuntimeError(
                f"raw sample {index} already discarded (buffer starts at {self._raw_start})"
            )
        return self._raw[offset:]

    def _trim(self) -> None:
        """Keep only raw samples a future step may still need."""
        if self._demod is None:
            # A detection reports a segment start up to the prefilter delay before the
            # search position, and the demodulator starts a few samples earlier still.
            margin = self._search.prefilter.size + 16
            keep_from = min(self._search._z_start, self._search._next) - margin
        else:
            # Enough to resume the search where the message ended (see _message_end).
            keep_from = self._samples_in - int(RAW_HISTORY_SECONDS * self.sample_rate)
        drop = max(0, min(keep_from, self._samples_in) - self._raw_start)
        self._raw = self._raw[drop:]
        self._raw_start += drop


def _whole_bytes_per_block(m: ModeSpec) -> bool:
    """Does an interleaver block (0.6 s without one) carry a whole number of bytes? Then a
    late entry at a block boundary keeps the synchronous byte boundaries."""
    if m.rate == 4800:
        return True
    if m.has_interleaver:
        coded = m.interleaver_bits
    elif m.is_75bps:
        coded = 2 * m.block_symbols // T.TRIBITS_PER_75BPS_SET
    else:
        coded = m.block_symbols // m.frame_symbols * m.unknown_per_frame * m.bits_per_channel_symbol
    return (coded // (2 * m.repetitions)) % 8 == 0
