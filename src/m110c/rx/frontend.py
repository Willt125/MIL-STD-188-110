"""Receive front end: samples -> one complex estimate per symbol.

Three steps, the mirror image of :mod:`m110c.modulate`:

1. **Carrier removal.** Multiply by e^{-j 2 pi f_c n / f_s}, which moves the signal from
   f_c (1800 Hz) down to 0 Hz.
2. **Matched filter.** Convolve with the same root-raised-cosine pulse the transmitter
   used. Transmit and receive filters together form a raised cosine, which has no
   inter-symbol interference at the symbol instants, and the matched filter maximises the
   signal-to-noise ratio there.
3. **Sampling.** Keep one output sample per symbol, at the given instants.

Real-valued input (audio) needs no special handling. A real signal holds the wanted
spectrum at +f_c and a mirror image at -f_c. After step 1 the image sits at -2 f_c
(-3600 Hz), far outside the matched filter's +/-1500 Hz passband, so step 2 removes it. The
only effect is half the amplitude, which the caller's gain normalisation absorbs.

Timing is given, not estimated (that is M8): ``first_symbol`` is the sample index at which the
matched-filter output for symbol 0 peaks. For a signal from :class:`~m110c.modulate.Modulator`
that starts at sample 0, it is twice the filter delay.
"""

import numpy as np

from ..modulate import DEFAULT_ROLLOFF, rrc_taps
from ..spec import tables as T


class SymbolSampler:
    """Streaming carrier removal, matched filtering and symbol sampling."""

    def __init__(
        self,
        sample_rate: int = 9600,
        carrier_hz: float = T.CARRIER_HZ,
        first_symbol: int | None = None,
        rolloff: float = DEFAULT_ROLLOFF,
        span: int = 8,
    ):
        if sample_rate % T.SYMBOL_RATE:
            raise ValueError(f"sample rate must be a multiple of {T.SYMBOL_RATE} Hz")
        self.sample_rate = sample_rate
        self.sps = sample_rate // T.SYMBOL_RATE
        self.carrier_hz = carrier_hz
        self.taps = rrc_taps(self.sps, rolloff, span)
        delay = (self.taps.size - 1) // 2
        self.first_symbol = 2 * delay if first_symbol is None else first_symbol
        # Baseband history. Before sample 0 the signal is taken to be zero, so the buffer
        # starts with len(taps) - 1 zeros at global index -(len(taps) - 1).
        self._buffer = np.zeros(self.taps.size - 1, dtype=complex)
        self._buffer_start = -(self.taps.size - 1)  # global index of _buffer[0]
        self._samples_in = 0  # global index of the next input sample
        self._next_symbol = 0  # index of the next symbol to produce

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Accept samples (real or complex); return the symbols whose window is complete."""
        x = np.asarray(samples)
        n = self._samples_in + np.arange(x.size)
        self._samples_in += x.size
        cycles = np.mod(self.carrier_hz * n, self.sample_rate) / self.sample_rate
        baseband = x * np.exp(-2j * np.pi * cycles)
        self._buffer = np.concatenate([self._buffer, baseband])

        # Matched-filter output at global index t uses inputs t - L + 1 .. t, L = len(taps).
        # (The taps are symmetric, so convolution and correlation coincide.)
        L = self.taps.size
        last_available = self._buffer_start + self._buffer.size - 1
        if last_available < self.first_symbol:
            return np.zeros(0, dtype=complex)
        n_ready = (last_available - self.first_symbol) // self.sps + 1 - self._next_symbol
        if n_ready <= 0:
            return np.zeros(0, dtype=complex)
        k = self._next_symbol + np.arange(n_ready)
        t = self.first_symbol + k * self.sps
        windows = np.lib.stride_tricks.sliding_window_view(self._buffer, L)
        out = windows[t - L + 1 - self._buffer_start] @ self.taps[::-1]
        self._next_symbol += n_ready

        # Keep only what the next symbol's window still needs.
        keep_from = self.first_symbol + self._next_symbol * self.sps - L + 1
        drop = max(0, keep_from - self._buffer_start)
        self._buffer = self._buffer[drop:]
        self._buffer_start += drop
        return out
