"""8-PSK modulator (§5.3.2.3.9): tribit numbers -> complex samples.

Each tribit k becomes a unit phasor at 45k degrees (Figure 5; INT-021 for the rotation sense).
The phasors are pulse-shaped with a root-raised-cosine filter and placed on the carrier:

    x[n] = A * sum_k  e^{j*pi*k_sym/4} * h[n - k*sps]  *  e^{j*2*pi*f_c*n/f_s}

* ``sps`` = samples per symbol = sample rate / 2400, a whole number. Other output rates
  (8000, 44100 S/s, ...) are reached by modulating at 9600 S/s and resampling exactly
  (:mod:`m110c.resample`, S3).
* ``h``  = root-raised cosine, roll-off 0.15, spanning +/-16 symbols (INT-015). The spec
  only says the waveforms "must be filtered" (§5.3.2.3.9 note). Roll-off 0.15 puts the
  signal in 1800 +/- 1380 Hz, 2.75 kHz wide at -26 dB: inside the US amateur limit of 2.8 kHz
  for HF data, which the earlier default, 0.25 (300..3300 Hz, 3.0 kHz), exceeds. Wider
  roll-offs are for the ``authorized`` profile (:mod:`m110c.profile`).
* ``f_c`` = 1800 Hz by default, so the real part of ``x`` *is* the audio waveform an SSB
  (upper sideband) transmitter would be fed. With ``carrier_hz=0`` the output is complex
  baseband centred on the carrier.

Streaming and determinism
    :meth:`Modulator.process` takes any number of tribits and returns the samples that are
    final. Internally, tribits are filtered in fixed blocks of 240 symbols (0.1 s), so the
    floating-point arithmetic, and therefore every output bit, is identical however the
    input was chunked. :meth:`Modulator.flush` returns the rest, including the filter's
    decaying tail.
"""

import numpy as np

from .resample import INTERNAL_RATE, Resampler
from .spec import tables as T

BLOCK_SYMBOLS = 240  # 0.1 s; fixed so that output does not depend on input chunking
DEFAULT_ROLLOFF = 0.15  # INT-015; the receiver's filters default to the same
DEFAULT_SPAN = 16  # transmit filter, symbols each side: 51 Hz below 2.8 kHz at -26 dB, not 20
# The modulator's latency, symbol start to pulse peak, whatever the filter's span: a longer
# filter drops its earliest precursor samples (see Modulator.delay).
LATENCY_SYMBOLS = 8


def rrc_taps(sps: int, alpha: float = DEFAULT_ROLLOFF, span: int = 8) -> np.ndarray:
    """Root-raised-cosine impulse response, ``2*span*sps + 1`` taps, unit energy.

    Standard closed form, with the two removable singularities (t = 0 and
    |t| = 1/(4*alpha) symbol periods) filled in by their limits.
    """
    if not 0 < alpha <= 1:
        raise ValueError("roll-off must be in (0, 1]")
    t = np.arange(-span * sps, span * sps + 1) / sps  # in symbol periods
    h = np.empty_like(t)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-12:
            h[i] = 1 - alpha + 4 * alpha / np.pi
        elif abs(abs(4 * alpha * ti) - 1) < 1e-9:
            h[i] = (alpha / np.sqrt(2)) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha))
            )
        else:
            num = np.sin(np.pi * ti * (1 - alpha)) + 4 * alpha * ti * np.cos(
                np.pi * ti * (1 + alpha)
            )
            h[i] = num / (np.pi * ti * (1 - (4 * alpha * ti) ** 2))
    return h / np.sqrt(np.sum(h**2))


def tribits_to_phasors(tribits: np.ndarray) -> np.ndarray:
    """§5.3.2.3.9 a: tribit k -> phase k * 45 degrees."""
    return np.exp(1j * np.deg2rad(T.DEGREES_PER_TRIBIT) * np.asarray(tribits, dtype=float))


class Modulator:
    """Streaming pulse-shaping 8-PSK modulator."""

    def __init__(
        self,
        sample_rate: int = 9600,
        carrier_hz: float = T.CARRIER_HZ,
        rolloff: float = DEFAULT_ROLLOFF,
        span: int = DEFAULT_SPAN,
        rms: float = 0.4,
    ):
        self.sample_rate = sample_rate  # of the output
        # Modulated at the output rate when it is a multiple of 2400, else at 9600 S/s and
        # resampled (S3).
        native = sample_rate % T.SYMBOL_RATE == 0
        self.modulation_rate = sample_rate if native else INTERNAL_RATE
        self._resampler = None if native else Resampler(INTERNAL_RATE, sample_rate)
        self.sps = self.modulation_rate // T.SYMBOL_RATE
        self.carrier_hz = carrier_hz
        self.taps = rrc_taps(self.sps, rolloff, span)
        # Unit-energy taps spread each symbol's energy over sps samples, so a symbol
        # amplitude of rms*sqrt(sps) gives the requested RMS level of the complex envelope.
        self._amplitude = rms * np.sqrt(self.sps)
        self._pending = np.zeros(0, dtype=np.uint8)  # tribits awaiting a full block
        self._tail = np.zeros(self.taps.size - 1, dtype=complex)  # filter overlap
        self._samples_out = 0  # global sample index, for a continuous carrier phase
        # Precursor samples to drop at the start of a transmission (spans beyond 8 symbols).
        self._lead = max(0, span - LATENCY_SYMBOLS) * self.sps
        self._skip = self._lead

    @property
    def delay(self) -> int:
        """Samples (at the modulation rate) from a symbol's start to the peak of its pulse.

        The filter's group delay, but at most LATENCY_SYMBOLS: a filter spanning more symbols
        (DEFAULT_SPAN, 16) would otherwise make the transmitter later, and every time the
        receiver reports (which assumes 8 symbols, as its own filters span) later with it.
        So its output starts that much into the filtered signal: what is dropped is the
        first pulses' precursor more than 8 symbols before their peaks, 36 dB below one
        symbol's energy at roll-off 0.15, once per transmission (key-up does more)."""
        return (self.taps.size - 1) // 2 - self._lead

    def process(self, tribits: np.ndarray) -> np.ndarray:
        """Accept tribits; return every sample that is now final."""
        self._pending = np.concatenate([self._pending, np.asarray(tribits, dtype=np.uint8)])
        n_blocks = self._pending.size // BLOCK_SYMBOLS
        out = [
            self._block(self._pending[i * BLOCK_SYMBOLS : (i + 1) * BLOCK_SYMBOLS])
            for i in range(n_blocks)
        ]
        self._pending = self._pending[n_blocks * BLOCK_SYMBOLS :]
        return self._resample(np.concatenate(out) if out else np.zeros(0, dtype=complex))

    def flush(self) -> np.ndarray:
        """Remaining samples: the last partial block plus the filter tail."""
        out = [self._block(self._pending)] if self._pending.size else []
        self._pending = np.zeros(0, dtype=np.uint8)
        out.append(self._mix(self._tail))
        self._tail = np.zeros(self.taps.size - 1, dtype=complex)
        self._skip = self._lead  # the next transmission starts afresh
        x = self._resample(np.concatenate(out))
        if self._resampler is not None:
            x = np.concatenate([x, self._resampler.flush()])
            self._resampler = Resampler(INTERNAL_RATE, self.sample_rate)  # ready for more
        return x

    def _resample(self, x: np.ndarray) -> np.ndarray:
        return x if self._resampler is None else self._resampler.process(x)

    def _block(self, tribits: np.ndarray) -> np.ndarray:
        """Filter one block by overlap-add and put it on the carrier."""
        impulses = np.zeros(tribits.size * self.sps, dtype=complex)
        impulses[:: self.sps] = self._amplitude * tribits_to_phasors(tribits)
        shaped = np.convolve(impulses, self.taps)
        shaped[: self._tail.size] += self._tail
        self._tail = shaped[impulses.size :].copy()
        return self._mix(shaped[: impulses.size])

    def _mix(self, baseband: np.ndarray) -> np.ndarray:
        if self._skip:  # the start of a transmission (see delay)
            cut = min(self._skip, baseband.size)
            baseband, self._skip = baseband[cut:], self._skip - cut
        n = self._samples_out + np.arange(baseband.size)
        self._samples_out += baseband.size
        if self.carrier_hz == 0:
            return baseband
        # Reduce the phase modulo one cycle *before* scaling by 2*pi, so the carrier stays
        # accurate however long the stream runs.
        cycles = np.mod(self.carrier_hz * n, self.modulation_rate) / self.modulation_rate
        return baseband * np.exp(2j * np.pi * cycles)


def modulate(tribits: np.ndarray, **kwargs) -> np.ndarray:
    """One-shot: all samples for a tribit stream, including the filter tail."""
    mod = Modulator(**kwargs)
    return np.concatenate([mod.process(tribits), mod.flush()])
