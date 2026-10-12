"""Deterministic impairments for testing the receiver: carrier offset and clock offset."""

import numpy as np


def frequency_offset(x: np.ndarray, offset_hz: float, sample_rate: int) -> np.ndarray:
    """Shift a complex signal by ``offset_hz`` (a mistuned receiver or Doppler shift)."""
    n = np.arange(len(x))
    return np.asarray(x, dtype=complex) * np.exp(2j * np.pi * offset_hz * n / sample_rate)


def clock_offset(x: np.ndarray, ppm: float, half_width: int = 16) -> np.ndarray:
    """Resample as if the transmitter's clock ran ``ppm`` parts per million fast.

    A fast transmitter clock packs the waveform into fewer receiver samples: output sample
    m is the input evaluated at time m * (1 + ppm*1e-6). Values between input samples come
    from band-limited (windowed-sinc) interpolation, accurate for signals well below Nyquist.
    """
    x = np.asarray(x, dtype=complex)
    ratio = 1 + ppm * 1e-6
    n_out = int(np.floor((len(x) - 1) / ratio))
    t = np.arange(n_out) * ratio
    base = np.floor(t).astype(int)
    frac = t - base
    k = np.arange(-half_width + 1, half_width + 1)
    window = np.hanning(2 * half_width + 2)[1:-1]
    out = np.zeros(n_out, dtype=complex)
    for j, offset in enumerate(k):
        idx = base + offset
        valid = (idx >= 0) & (idx < len(x))
        weight = np.sinc(frac - offset) * window[j]
        out[valid] += weight[valid] * x[idx[valid]]
    return out


def fractional_delay(x: np.ndarray, delay: float, half_width: int = 16) -> np.ndarray:
    """Delay a band-limited signal by ``delay`` samples (any real number >= 0).

    Output sample m is the input at time m - delay, by windowed-sinc interpolation.
    """
    x = np.asarray(x, dtype=complex)
    n_out = len(x) + int(np.ceil(delay))
    t = np.arange(n_out) - delay
    base = np.floor(t).astype(int)
    frac = t - base
    window = np.hanning(2 * half_width + 2)[1:-1]
    out = np.zeros(n_out, dtype=complex)
    for j, offset in enumerate(range(-half_width + 1, half_width + 1)):
        idx = base + offset
        valid = (idx >= 0) & (idx < len(x))
        out[valid] += (np.sinc(frac - offset) * window[j])[valid] * x[idx[valid]]
    return out


def frequency_sweep(
    x: np.ndarray, rate_hz_per_s: float, sample_rate: int, start_hz: float = 0.0
) -> np.ndarray:
    """Carrier offset changing linearly with time (Doppler sweep): starts at ``start_hz``
    and moves by ``rate_hz_per_s`` every second. Appendix C's sweep test uses 3.5 Hz/s."""
    t = np.arange(len(x)) / sample_rate
    phase = 2 * np.pi * (start_hz * t + 0.5 * rate_hz_per_s * t**2)
    return np.asarray(x, dtype=complex) * np.exp(1j * phase)
