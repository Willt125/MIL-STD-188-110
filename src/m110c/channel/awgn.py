"""Additive white Gaussian noise at a given signal-to-noise ratio.

Table XVI of the spec states SNR with "both signal and noise powers ... measured in a 3-kHz
bandwidth" (§5.3.2.5 note). White noise spread over the whole sample bandwidth has only
3000 / f_s of its power inside 3 kHz, so it is scaled up accordingly.
"""

import numpy as np


def add_awgn(
    x: np.ndarray,
    snr_db: float,
    sample_rate: int,
    rng: np.random.Generator,
    bandwidth_hz: float = 3000.0,
    signal_power: float | None = None,
) -> np.ndarray:
    """Return ``x`` plus complex white Gaussian noise at ``snr_db`` (measured in
    ``bandwidth_hz``). ``signal_power`` defaults to the mean power of ``x``; pass the
    nominal value when ``x`` contains silence."""
    x = np.asarray(x, dtype=complex)
    if signal_power is None:
        signal_power = float(np.mean(np.abs(x) ** 2))
    noise_in_band = signal_power / 10 ** (snr_db / 10)
    noise_total = noise_in_band * sample_rate / bandwidth_hz
    noise = np.sqrt(noise_total / 2) * (
        rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size)
    )
    return x + noise
