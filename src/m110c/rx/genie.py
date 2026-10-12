"""Genie-aided receiver (M7): decodes a signal whose timing, carrier and mode are *given*.

"Genie" is the usual name for a receiver that is told what a real one must estimate. Here
the genie supplies:

* the mode (in reality read from D1/D2 in the preamble),
* where symbol 0 of the preamble is (in reality found by preamble correlation),
* the carrier frequency and phase and the channel gain (in reality estimated and tracked).

Everything after that is the real receive chain: front end, data decoder, EOM detection and
deframing into bytes. Its purpose is to prove that chain before acquisition exists (M8), so a
failing loopback can be blamed on the right half.

Usage::

    rx = GenieReceiver(mode(2400, "long"))
    data = b"".join(rx.process(chunk) for chunk in sample_chunks) + rx.finish()
    rx.eom_found, rx.snr_db
"""

import numpy as np

from .. import bits as bitops
from ..framing import AsyncDecoder, AsyncFormat
from ..spec import tables as T
from ..spec.modes import ModeSpec
from .decode import DataDecoder
from .eom import EomDetector
from .frontend import SymbolSampler

DEFAULT_RMS = 0.4  # Modulator's default complex-envelope RMS level


class GenieReceiver:
    def __init__(
        self,
        mode: ModeSpec,
        sample_rate: int = 9600,
        carrier_hz: float = T.CARRIER_HZ,
        first_symbol: int | None = None,
        gain: complex | None = None,
        msb_first: bool = False,
        async_format: AsyncFormat | None = None,
        noise_var: float = 1.0,
    ):
        """``gain`` is the complex amplitude of a received symbol (the genie's channel
        knowledge); received symbols are divided by it so they land on the unit circle.
        The default is what :class:`~m110c.modulate.Modulator` produces at its default
        level: symbol amplitude rms * sqrt(samples per symbol), preserved by the unit-energy
        matched filter. Real (audio) input carries half of that (see ``frontend``)."""
        self.mode = mode
        self.sampler = SymbolSampler(sample_rate, carrier_hz, first_symbol)
        if gain is None:
            gain = DEFAULT_RMS * np.sqrt(self.sampler.sps)
        self.decoder = DataDecoder(mode, noise_var)
        self.eom = EomDetector()
        self._gain = complex(gain)
        self._preamble_left = mode.preamble_symbols
        self._msb_first = msb_first
        self._async = AsyncDecoder(async_format) if async_format is not None else None
        self._bit_pending = np.zeros(0, dtype=np.uint8)  # sync mode: bits of a partial byte

    @property
    def eom_found(self) -> bool:
        return self.eom.found

    @property
    def snr_db(self) -> float | None:
        """Measured signal-to-noise ratio per symbol (see :class:`DataDecoder`)."""
        return self.decoder.snr_db

    def process(self, samples: np.ndarray) -> bytes:
        """Accept samples; return the user bytes that are now certain."""
        if self.eom.found:
            return b""
        y = self.sampler.process(samples) / self._gain
        skip = min(self._preamble_left, y.size)  # the genie knows where the preamble ends
        self._preamble_left -= skip
        bits = self.decoder.process(y[skip:])
        return self._deframe(self.eom.process(bits))

    def finish(self) -> bytes:
        """End of signal: decode what is left and return the remaining bytes."""
        if self.eom.found:
            return b""
        out = self._deframe(self.eom.process(self.decoder.finish()))
        return out + self._deframe(self.eom.finish(), final=True)

    def _deframe(self, payload_bits: np.ndarray, final: bool = False) -> bytes:
        if self._async is not None:
            return self._async.process(payload_bits)
        bits = np.concatenate([self._bit_pending, payload_bits])
        whole = bits.size - bits.size % 8
        self._bit_pending = bits[whole:] if not final else np.zeros(0, dtype=np.uint8)
        return bitops.bits_to_bytes(bits[:whole], msb_first=self._msb_first)  # INT-016
