"""Rational resampling, so that the modem works at any audio sample rate (S3).

The modulator and the receiver count whole samples per symbol, so they run natively only at
multiples of 2400 S/s (9600, 48000, ...). Sound cards and recordings also use 8000, 11025,
16000, 22050 and 44100 S/s. For those, the signal is converted exactly between the device
rate and 9600 S/s by :class:`Resampler`: rate_out / rate_in = up / down, a fraction in
lowest terms (44100 -> 9600 is 32/147, 8000 -> 9600 is 6/5).

How (a polyphase FIR filter)
    Conceptually: insert ``up - 1`` zeros between input samples, low-pass filter at the
    high rate ``up * rate_in``, keep every ``down``-th sample. Output sample ``m`` is then

        y[m] = sum_j x[j] * h[m*down + D - j*up]

    where ``D`` is the filter's centre, so the output is aligned with the input in time
    (no delay). Only every ``up``-th tap meets a nonzero input, so each output sample is a
    short dot product with one *phase* of the filter, ``h[p], h[p+up], ...``.

The filter
    The signal occupies 300-3300 Hz (with the default carrier). It must pass up to
    ``PASSBAND_HZ`` unchanged and stop whatever would alias into it: a component at f
    reaches the band after resampling at rate r if it lies within PASSBAND_HZ of a
    multiple of r, so the stopband starts at min(rate_in, rate_out) - PASSBAND_HZ. A
    Kaiser-windowed sinc with its cutoff halfway, ``ATTENUATION_DB`` down in the stopband.
    That needs rates above 2 * PASSBAND_HZ (6800 S/s).

Streaming: :meth:`Resampler.process` takes any number of samples and returns the outputs
whose inputs have all arrived; :meth:`Resampler.flush` returns the rest, as if the input
went on with zeros, up to ceil(n_in * up / down) outputs in all. Each output is the same
fixed dot product however the input was chunked, so the output does not depend on chunking.
"""

from math import ceil, gcd

import numpy as np

PASSBAND_HZ = 3400.0  # the 300-3300 Hz voice channel, with a margin
ATTENUATION_DB = 80.0
INTERNAL_RATE = 9600  # what the modem runs at when the device rate is not a multiple of 2400


def lowpass(up: int, rate_in: int, rate_out: int) -> np.ndarray:
    """The interpolation filter at the high rate ``up * rate_in``, with a gain of ``up``
    (which makes up for the zeros inserted), an odd number of taps, symmetric."""
    if min(rate_in, rate_out) <= 2 * PASSBAND_HZ:
        raise ValueError(f"sample rates must be above {2 * PASSBAND_HZ:g} S/s")
    high = up * rate_in
    stop = min(rate_in, rate_out) - PASSBAND_HZ
    cutoff = (PASSBAND_HZ + stop) / 2 / high  # cycles per high-rate sample
    width = (stop - PASSBAND_HZ) / high
    # Kaiser's formulas for the length and the window's shape parameter.
    n = int(np.ceil((ATTENUATION_DB - 8) / (2.285 * 2 * np.pi * width))) | 1
    beta = 0.1102 * (ATTENUATION_DB - 8.7)
    t = np.arange(n) - (n - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * t) * np.kaiser(n, beta)
    return h * up / h.sum()


class Resampler:
    """Streaming rational resampler from ``rate_in`` to ``rate_out`` samples per second."""

    def __init__(self, rate_in: int, rate_out: int):
        if rate_in <= 0 or rate_out <= 0:
            raise ValueError("sample rates must be positive")
        g = gcd(rate_in, rate_out)
        self.rate_in, self.rate_out = rate_in, rate_out
        self.up, self.down = rate_out // g, rate_in // g
        h = lowpass(self.up, rate_in, rate_out)
        self.centre = (h.size - 1) // 2
        # Phase p holds taps p, p + up, p + 2 up, ... (zero-padded to equal length).
        width = -(-h.size // self.up)
        padded = np.zeros(width * self.up)
        padded[: h.size] = h
        self._phases = padded.reshape(width, self.up).T.copy()  # (up, width)
        self._width = width
        self._x = np.zeros(0, dtype=complex)  # input kept for the outputs still to come
        self._x_start = 0  # global index of _x[0]
        self._n_in = 0
        self._m = 0  # next output sample

    def _window(self, m: np.ndarray):
        """For outputs m: the filter phase, and the newest input index each one uses."""
        position = m * self.down + self.centre  # in high-rate samples
        return position % self.up, position // self.up

    def _outputs(self, stop: int) -> np.ndarray:
        """Outputs self._m .. stop-1 from the inputs held (missing ones count as zero)."""
        m = np.arange(self._m, stop)
        if m.size == 0:
            return np.zeros(0, dtype=complex)
        phase, newest = self._window(m)
        j = newest[:, None] - np.arange(self._width)[None, :] - self._x_start
        inside = (j >= 0) & (j < self._x.size)
        held = self._x if self._x.size else np.zeros(1, dtype=complex)
        x = np.where(inside, held[np.clip(j, 0, held.size - 1)], 0)
        y = np.sum(x * self._phases[phase], axis=1)
        self._m = stop
        # Keep only the inputs that later outputs can still reach.
        oldest = self._window(np.array([stop]))[1][0] - self._width + 1
        drop = max(0, min(oldest - self._x_start, self._x.size))
        self._x, self._x_start = self._x[drop:], self._x_start + drop
        return y

    def process(self, x: np.ndarray) -> np.ndarray:
        """Accept input samples; return every output whose inputs have all arrived."""
        x = np.asarray(x, dtype=complex)
        self._x = np.concatenate([self._x, x])
        self._n_in += x.size
        # Output m is complete once its newest input, (m*down + centre) // up, has arrived.
        stop = max(self._m, (self._n_in * self.up - self.centre - 1) // self.down + 1)
        stop = min(stop, self.total_outputs())
        return self._outputs(stop)

    def flush(self) -> np.ndarray:
        """The remaining outputs, as if the input continued with zeros."""
        return self._outputs(self.total_outputs())

    def total_outputs(self) -> int:
        """Outputs for the input so far: the same duration at the new rate."""
        return ceil(self._n_in * self.up / self.down)


def resample(x: np.ndarray, rate_in: int, rate_out: int) -> np.ndarray:
    """One-shot: all of ``x`` at the new rate."""
    r = Resampler(rate_in, rate_out)
    return np.concatenate([r.process(x), r.flush()])


PHASES = 256  # the fractional resampler's filter table: positions per input sample


class FractionalResampler:
    """Streaming resampler by any ratio (output samples per input sample), for small
    corrections such as a sound card's clock error (``--tx-ppm``: a ratio of 1.00005 is
    no fraction a rational resampler could use).

    The same low-pass filter as :class:`Resampler`, tabulated at PHASES positions per input
    sample. Output sample m lies at input position t = m / ratio; its value is

        y[m] = sum_j x[j] h(t - j),

    with h read from the table by linear interpolation between the two nearest positions.
    Output 0 lies on input 0 (no delay), so the output does not depend on chunking. The
    error of the interpolated table is below the filter's own (about -80 dB) in the voice
    channel."""

    def __init__(self, ratio: float, sample_rate: int):
        if ratio <= 0:
            raise ValueError("ratio must be positive")
        self.ratio = ratio
        L = PHASES
        h = lowpass(L, sample_rate, sample_rate)  # at L times the rate; each row sums to 1
        self.centre = (h.size - 1) // 2
        width = -(-(h.size + 1) // L) + 1
        padded = np.zeros(width * L + 1)
        padded[: h.size] = h
        # Row p: taps p, p + L, ... and the same one step further on (for interpolating).
        self._table = padded[: width * L].reshape(width, L).T.copy()
        self._next = padded[1 : width * L + 1].reshape(width, L).T.copy()
        self._width = width
        self._x = np.zeros(0, dtype=complex)
        self._x_start = 0
        self._n_in = 0
        self._m = 0

    def _window(self, m: np.ndarray):
        """For outputs m: the table row, the interpolation fraction, the newest input."""
        position = m / self.ratio * PHASES + self.centre  # in table positions
        base = np.floor(position).astype(np.int64)
        return base % PHASES, position - base, base // PHASES

    def _outputs(self, stop: int) -> np.ndarray:
        m = np.arange(self._m, stop)
        if m.size == 0:
            return np.zeros(0, dtype=complex)
        row, frac, newest = self._window(m)
        j = newest[:, None] - np.arange(self._width)[None, :] - self._x_start
        inside = (j >= 0) & (j < self._x.size)
        held = self._x if self._x.size else np.zeros(1, dtype=complex)
        x = np.where(inside, held[np.clip(j, 0, held.size - 1)], 0)
        taps = (1 - frac)[:, None] * self._table[row] + frac[:, None] * self._next[row]
        y = np.sum(x * taps, axis=1)
        self._m = stop
        oldest = int(self._window(np.array([stop]))[2][0]) - self._width + 1
        drop = max(0, min(oldest - self._x_start, self._x.size))
        self._x, self._x_start = self._x[drop:], self._x_start + drop
        return y

    def process(self, x: np.ndarray) -> np.ndarray:
        """Accept input samples; return every output whose inputs have all arrived."""
        x = np.asarray(x, dtype=complex)
        self._x = np.concatenate([self._x, x])
        self._n_in += x.size

        # Output m is complete once its newest input, (m / ratio * L + centre) // L, has
        # arrived: m < (n_in * L - centre) * ratio / L, give or take rounding.
        def newest(m):
            return int(self._window(np.array([m]))[2][0])

        stop = int((self._n_in * PHASES - self.centre) * self.ratio / PHASES)
        while newest(stop) < self._n_in:
            stop += 1
        while stop > 0 and newest(stop - 1) >= self._n_in:
            stop -= 1
        stop = min(max(stop, self._m), self.total_outputs())
        return self._outputs(stop)

    def flush(self) -> np.ndarray:
        """The remaining outputs, as if the input continued with zeros."""
        return self._outputs(self.total_outputs())

    def total_outputs(self) -> int:
        """Outputs for the input so far: the same duration at the new rate."""
        return ceil(self._n_in * self.ratio)
