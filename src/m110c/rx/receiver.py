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
from ..framing import AsyncDecoder, AsyncFormat
from ..spec import tables as T
from ..spec.modes import ModeSpec
from .acquire import Detection, PreambleSearch
from .decode import DataDecoder
from .eom import EomDetector
from .equalize import EqualizingDemodulator
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


class Receiver:
    def __init__(
        self,
        sample_rate: int = 9600,
        carrier_hz: float = T.CARRIER_HZ,
        max_offset_hz: float = 400.0,
        msb_first: bool = False,
        async_format: AsyncFormat | None = None,
        zero_interleave: bool = False,
        invert_spectrum: bool = False,
        threshold: float | None = None,
        equalize: bool = True,
    ):
        self.sample_rate = sample_rate
        self.carrier_hz = carrier_hz
        self.max_offset_hz = max_offset_hz
        self.msb_first = msb_first
        self.async_format = async_format
        self.zero_interleave = zero_interleave
        self.invert_spectrum = invert_spectrum
        self.threshold = threshold
        self.equalize = equalize  # False: the M8 single-gain tracker (for comparison)
        self.messages: list[Message] = []
        self._raw = np.zeros(0, dtype=complex)  # recent input
        self._raw_start = 0  # global index of _raw[0]
        self._samples_in = 0
        self._start_search(0)

    # -- public ------------------------------------------------------------------------

    def process(self, samples: np.ndarray) -> bytes:
        x = np.asarray(samples)
        if self.invert_spectrum:
            x = np.conj(x)  # INT-021: a lower-sideband signal is the mirror image
        self._raw = np.concatenate([self._raw, x])
        self._samples_in += x.size
        out = bytearray()
        new = x
        while True:
            if self._demod is None:
                detection = self._search.process(new)
                if detection is None:
                    break
                self._start_message(detection)
                out += self._feed_message(self._raw_from(self._demod_start))
            else:
                out += self._feed_message(new)
            if self._demod is not None:
                break
            # The message ended inside this batch: search the rest of it.
            new = self._raw_from(self._search_from)
        self._trim()
        return bytes(out)

    def finish(self) -> bytes:
        """End of input: search what is left, then close any message in progress."""
        out = b""
        if self._demod is None:
            detection = self._search.flush()
            if detection is None:
                return b""
            self._start_message(detection)
            out += self._feed_message(self._raw_from(self._demod_start))
            if self._demod is None:  # it ended inside the remaining samples
                return out
        return out + self._end_message(final=True)

    # -- message lifecycle --------------------------------------------------------------

    def _start_search(self, position: int) -> None:
        options = {} if self.threshold is None else {"threshold": self.threshold}
        self._search = PreambleSearch(
            self.sample_rate,
            self.carrier_hz,
            self.max_offset_hz,
            zero_interleave=self.zero_interleave,
            **options,
        )
        # The search counts samples from the position it starts at.
        self._search._samples_in = position
        self._search._z_start = position
        self._search._next = position
        self._search_from = position
        self._demod = None

    def _start_message(self, detection: Detection) -> None:
        m = detection.mode
        self._message = Message(mode=m, detection=detection)
        # Start a few samples early (interpolation needs neighbours), but never before the
        # stream itself began: the demodulator treats earlier samples as zero anyway.
        self._demod_start = max(int(np.floor(detection.segment_start)) - 8, self._raw_start)
        demodulator = EqualizingDemodulator if self.equalize else TrackingDemodulator
        self._demod = demodulator(m, detection, self.sample_rate, start_index=self._demod_start)
        self._decoder = DataDecoder(m)
        self._eom = EomDetector()
        self._async = AsyncDecoder(self.async_format) if self.async_format else None
        self._bit_pending = np.zeros(0, dtype=np.uint8)

    def _feed_message(self, samples) -> bytes:
        symbols, noise_var = self._demod.process(samples)
        payload = self._eom.process(self._decoder.process(symbols, noise_var))
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
        msg = self._message
        msg.eom_found = eom.found
        msg.lost = demod.lost and not eom.found  # silence after a complete message is not a loss
        msg.snr_db = decoder.snr_db
        msg.clock_ppm = demod.clock_ppm
        msg.end_sample = self._message_end(demod)
        self.messages.append(msg)
        self._start_search(max(msg.end_sample, self._raw_start))
        return out

    def _message_end(self, demod) -> int:
        """Where the transmission ended, so the search for the next one can start there.

        The demodulator runs about a second ahead of the decoder (it holds output back while
        it judges loss of signal), so its position is past the end. But the decoder only
        gets the last block, and so the EOM, once the demodulator has seen a second of
        whatever followed: by then it has declared the signal lost where its final run of
        bad units began. (Computing the end from the EOM's position as well was tried; it
        never changed a result, so it was removed.)"""
        if demod.lost and demod.end_symbol is not None:
            end = demod.symbol_time(demod.end_symbol)
        else:
            end = demod.position
        return int(min(max(end, 0), demod.position))

    def _deframe(self, payload_bits: np.ndarray, final: bool = False) -> bytes:
        if self._async is not None:
            out = self._async.process(payload_bits)
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
