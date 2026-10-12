"""Watterson HF channel simulator, MIL-STD-188-110C Appendix E.

The model (E.4.1): the received signal is two copies of the transmitted signal, a fixed
*delay spread* apart, each multiplied by its own randomly fading complex gain, plus white
Gaussian noise. It is fully specified by three numbers: delay spread, fading bandwidth (also
called Doppler spread) per path, and SNR.

    y[i] = h0[i] * x[i]  +  h1[i] * x[i - L]  +  n[i]                   (Figure E-1)

* x is the complex (analytic) input. A real audio input is first made analytic with a
  Hilbert transform (E.4.2 note); see :func:`analytic`.
* L = delay spread / sampling period, rounded to the nearest integer (E.5.2 says
  "divided by the sample rate", but that is dimensionally wrong; E.4.2 says "divided by the
  sampling period"; INT-028).
* Each fading gain h_j is complex white Gaussian noise passed through a Gaussian filter
  (Figure E-2, E.5.4)

      f(t) = k * sqrt(2) * exp(-pi^2 t^2 d^2),     |t| < tau

  whose power spectrum is a Gaussian with standard deviation sigma = d/2. So the "fading
  bandwidth" d is 2 sigma, which is what Table XVI's "two sigma" means (INT-027). tau is where
  f falls to 1% of its peak: tau = sqrt(ln 100) / (pi d). k makes the *total* average power
  gain of the paths 1.0, so each of two paths has average power 1/2.
* Gains are computed at a rate of at least 32 d (E.5.4), here fs / D for a whole number D,
  and linearly interpolated to every sample (the design objective in E.5.4).
* The noise power is set from the *stated* average input power and the SNR, measured in a
  3-kHz bandwidth (E.5.5; Table XVI's note). A streaming simulator cannot know the average
  input power in advance, so it is a parameter. The default, 0.16, is the level
  ``m110c-tx`` produces (RMS 0.4).

Streaming and reproducibility: :meth:`WattersonChannel.process` accepts any number of
samples. The output depends only on the input and the seed, not on how the input was
chunked. Noise and each path's fading come from independent random streams spawned from the
seed, and every draw is a fixed-size unit (one complex sample).
"""

from dataclasses import dataclass

import numpy as np

DEFAULT_SIGNAL_POWER = 0.16  # m110c-tx default: RMS 0.4
MIN_GAIN_RATE_PER_HZ = 32  # E.5.4: gains computed at >= 32 x the fading bandwidth


@dataclass(frozen=True)
class ChannelSpec:
    """One test condition. ``paths=1`` with ``doppler_hz=0`` is a fixed (non-fading) path."""

    paths: int = 2
    delay_ms: float = 2.0
    doppler_hz: float = 1.0  # fading bandwidth d (two sigma), per path
    snr_db: float | None = None  # None: no noise

    @property
    def fading(self) -> bool:
        return self.doppler_hz > 0


#: Table XVI conditions (§5.3.2.5), by (paths, multipath ms, fading Hz).
TABLE_XVI_CHANNELS = {
    "1 fixed": ChannelSpec(paths=1, delay_ms=0.0, doppler_hz=0.0),
    "2 fading 2 ms 0.5 Hz": ChannelSpec(2, 2.0, 0.5),
    "2 fading 2 ms 1 Hz": ChannelSpec(2, 2.0, 1.0),
    "2 fading 2 ms 5 Hz": ChannelSpec(2, 2.0, 5.0),
    "2 fading 5 ms 1 Hz": ChannelSpec(2, 5.0, 1.0),
    "2 fading 5 ms 5 Hz": ChannelSpec(2, 5.0, 5.0),
}


def gaussian_fading_filter(doppler_hz: float, gain_rate: float) -> np.ndarray:
    """Taps of f(t) = sqrt(2) exp(-pi^2 t^2 d^2), sampled at ``gain_rate``, truncated where it
    falls to 1% of its peak, and scaled so filtered unit-power white noise has unit power."""
    tau = np.sqrt(np.log(100)) / (np.pi * doppler_hz)
    half = int(np.floor(tau * gain_rate))
    t = np.arange(-half, half + 1) / gain_rate
    f = np.sqrt(2) * np.exp(-((np.pi * t * doppler_hz) ** 2))
    return f / np.sqrt(np.sum(f**2))


class FadingGain:
    """One path's fading gain h_j(n), streamed at the channel's sample rate."""

    def __init__(self, doppler_hz: float, sample_rate: int, power: float, rng: np.random.Generator):
        self.decimation = max(1, int(sample_rate // (MIN_GAIN_RATE_PER_HZ * doppler_hz)))
        self.gain_rate = sample_rate / self.decimation
        self.taps = gaussian_fading_filter(doppler_hz, self.gain_rate)
        self._scale = np.sqrt(power)
        self._rng = rng
        # Pre-fill the filter with noise so the process is stationary from the first sample.
        self._noise = self._white(self.taps.size - 1)
        self._gains = np.zeros(0, dtype=complex)  # gain samples at the gain rate
        self._gains_start = 0  # gain-rate index of _gains[0]
        self._samples_out = 0

    def _white(self, n: int) -> np.ndarray:
        """Complex white Gaussian noise of unit power, drawn as (re, im) pairs."""
        pairs = self._rng.standard_normal((n, 2))
        return (pairs[:, 0] + 1j * pairs[:, 1]) / np.sqrt(2)

    def _ensure(self, last_index: int) -> None:
        """Make sure gain samples up to gain-rate index ``last_index`` exist."""
        missing = last_index - (self._gains_start + self._gains.size) + 1
        if missing <= 0:
            return
        self._noise = np.concatenate([self._noise, self._white(missing)])
        new = np.convolve(self._noise, self.taps, mode="valid") * self._scale
        self._noise = self._noise[-(self.taps.size - 1) :]
        self._gains = np.concatenate([self._gains, new])

    def next(self, n: int) -> np.ndarray:
        """Gains for the next ``n`` channel samples (linear interpolation, E.5.4 DO)."""
        if n == 0:
            return np.zeros(0, dtype=complex)
        idx = self._samples_out + np.arange(n)
        self._samples_out += n
        position = idx / self.decimation  # in gain samples
        lower = np.floor(position).astype(np.int64)
        self._ensure(int(lower[-1]) + 1)
        frac = position - lower
        g = self._gains[lower - self._gains_start]
        g_next = self._gains[lower + 1 - self._gains_start]
        out = (1 - frac) * g + frac * g_next
        # Drop gain samples no longer needed.
        keep_from = int(np.floor(self._samples_out / self.decimation)) - self._gains_start
        self._gains = self._gains[max(0, keep_from) :]
        self._gains_start += max(0, keep_from)
        return out


class WattersonChannel:
    """Streaming two-path (or one-path) Watterson channel with additive noise."""

    def __init__(
        self,
        spec: ChannelSpec,
        sample_rate: int = 9600,
        seed: int = 0,
        signal_power: float = DEFAULT_SIGNAL_POWER,
        bandwidth_hz: float = 3000.0,
    ):
        self.spec = spec
        self.sample_rate = sample_rate
        self.delay = int(round(spec.delay_ms * 1e-3 * sample_rate)) if spec.paths == 2 else 0
        streams = np.random.SeedSequence(seed).spawn(3)
        path_power = 1.0 / spec.paths  # k: total average power gain 1.0 (E.5.4)
        if spec.fading:
            self._fading = [
                FadingGain(
                    spec.doppler_hz, sample_rate, path_power, np.random.default_rng(streams[j])
                )
                for j in range(spec.paths)
            ]
        else:
            self._fading = None
        self._noise_rng = np.random.default_rng(streams[2])
        if spec.snr_db is None:
            self.noise_power = 0.0
        else:
            in_band = signal_power / 10 ** (spec.snr_db / 10)  # E.5.5: SNR in 3 kHz
            self.noise_power = in_band * sample_rate / bandwidth_hz  # white over all fs
        self._history = np.zeros(self.delay, dtype=complex)  # last L inputs, for path 2

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=complex)
        n = x.size
        if self.spec.paths == 2:
            extended = np.concatenate([self._history, x])
            delayed = extended[:n]  # x[i - L]
            self._history = extended[n:]  # the last L inputs (empty when L = 0)
        if self._fading is None:
            y = x.copy() if self.spec.paths == 1 else np.sqrt(0.5) * (x + delayed)
        else:
            y = self._fading[0].next(n) * x
            if self.spec.paths == 2:
                y += self._fading[1].next(n) * delayed
        if self.noise_power:
            pairs = self._noise_rng.standard_normal((n, 2))
            y += np.sqrt(self.noise_power / 2) * (pairs[:, 0] + 1j * pairs[:, 1])
        return y


def analytic_fir(num_taps: int = 127) -> np.ndarray:
    """Taps of a Hilbert transformer (90-degree phase shift) for real-to-analytic conversion.

    ``analytic(x) = x_delayed + 1j * (hilbert_taps * x)``, with the real part delayed by the
    filter's group delay. Hamming-windowed ideal response; odd taps are zero.
    """
    if num_taps % 2 == 0:
        raise ValueError("use an odd number of taps")
    k = np.arange(num_taps) - (num_taps - 1) // 2
    h = np.zeros(num_taps)
    odd = k % 2 != 0
    h[odd] = 2 / (np.pi * k[odd])
    return h * np.hamming(num_taps)


class Analytic:
    """Streaming real -> analytic (complex) conversion, E.4.2 note. Delay: (taps-1)/2."""

    def __init__(self, num_taps: int = 127):
        self.taps = analytic_fir(num_taps)
        self.delay = (num_taps - 1) // 2
        self._history = np.zeros(num_taps - 1)

    def flush(self) -> np.ndarray:
        """End of input: the last ``delay`` samples, still inside the filter."""
        return self.process(np.zeros(self.delay))

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self._history, np.asarray(x, dtype=float)])
        self._history = buf[buf.size - self._history.size :]
        quadrature = np.convolve(buf, self.taps, mode="valid")
        in_phase = buf[self.delay : self.delay + quadrature.size]
        return in_phase + 1j * quadrature
