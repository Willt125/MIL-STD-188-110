"""Tests for the Watterson HF channel simulator against MIL-STD-188-110C Appendix E (M9).

Appendix E.7 asks for very long averages (20 minutes to 3 hours). The fading process is
checked at its own low rate (32 samples/s for 1 Hz), where 3 hours is cheap; the full
sample-rate chain is checked over shorter runs with tolerances set accordingly.
"""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c.channel.watterson import (
    TABLE_XVI_CHANNELS,
    Analytic,
    ChannelSpec,
    FadingGain,
    WattersonChannel,
    gaussian_fading_filter,
)

FS = 9600


def gain_samples(doppler_hz, hours, power=1.0, seed=1):
    g = FadingGain(doppler_hz, FS, power, np.random.default_rng(seed))
    n = int(hours * 3600 * g.gain_rate)
    g._ensure(n)
    return g._gains[:n], g.gain_rate


# --- E.5.4 fading filter ---------------------------------------------------------------------


@pytest.mark.parametrize("doppler_hz", [0.5, 1.0, 5.0, 10.0])
def test_fading_filter_truncated_at_one_percent(doppler_hz):
    """f(t) = sqrt(2) exp(-pi^2 t^2 d^2), truncated where it is 1% of its peak."""
    rate = 32 * doppler_hz
    f = gaussian_fading_filter(doppler_hz, rate)
    assert np.isclose(np.sum(f**2), 1.0)
    assert f[0] / f.max() >= 0.01  # the end taps are still inside |t| < tau
    t_next = ((f.size - 1) // 2 + 1) / rate  # one gain sample further would fall below 1%
    assert np.exp(-((np.pi * t_next * doppler_hz) ** 2)) < 0.01


@pytest.mark.parametrize("doppler_hz", [0.5, 1.0, 5.0, 10.0])
@pytest.mark.parametrize("sample_rate", [9600, 48000])
def test_gains_computed_at_least_32_times_the_fading_bandwidth(doppler_hz, sample_rate):
    g = FadingGain(doppler_hz, sample_rate, 1.0, np.random.default_rng(0))
    assert g.gain_rate >= 32 * doppler_hz


# --- E.7.4 Doppler spectrum ------------------------------------------------------------------


def test_doppler_spectrum_meets_table_e_i():
    """Table E-I (single path, 1 Hz): within +/-1.5 dB of the ideal Gaussian where it is
    20 dB down, +/-2.0 dB where it is 30 dB down. Kaiser-windowed average over 3 hours, as
    E.7.4 recommends. Measured: deviations of 0.4 dB or less."""
    h, rate = gain_samples(1.0, 3)
    nfft = 1024
    window = np.kaiser(nfft, 8)
    acc = np.zeros(nfft)
    for k in range(0, h.size - nfft, nfft // 2):
        acc += np.abs(np.fft.fft(h[k : k + nfft] * window)) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / rate))
    spectrum = np.fft.fftshift(acc / acc.max())
    for attenuation, allowed in ((20, 1.5), (30, 2.0)):
        f_ideal = np.sqrt(
            np.log(10 ** (attenuation / 10)) / 2
        )  # where exp(-2 f^2 / d^2) is that low
        for sign in (1, -1):
            measured = 10 * np.log10(np.interp(sign * f_ideal, f, spectrum))
            assert abs(measured + attenuation) <= allowed, (attenuation, sign, measured)


def test_fading_is_rayleigh_with_the_set_power():
    """A complex Gaussian gain: |h|^2 is exponential (P(|h|^2 < 0.1 x mean) = 1 - e^-0.1)."""
    h, _ = gain_samples(1.0, 3, power=0.5)
    p = np.abs(h) ** 2
    assert abs(10 * np.log10(p.mean() / 0.5)) < 0.25
    assert abs(np.mean(p < 0.05) - (1 - np.exp(-0.1))) < 0.01


def test_gains_are_interpolated_between_gain_samples():
    """E.5.4: gains are computed at >= 32 d and interpolated, "to avoid introducing large
    discontinuities". Stepped (held) gains would leave almost every consecutive pair equal."""
    g = FadingGain(1.0, FS, 1.0, np.random.default_rng(2)).next(FS * 20)
    assert np.mean(g[1:] == g[:-1]) < 0.01
    steps = np.abs(np.diff(g))
    assert steps.max() < 5 * np.median(steps)  # no jumps at gain-sample boundaries


def test_fading_is_stationary_from_the_first_sample():
    """The filter starts full of noise, not zeros, so the first 0.2 s already has the full
    average power (averaged over 200 independent channels)."""
    first = [
        np.mean(np.abs(FadingGain(1.0, FS, 1.0, np.random.default_rng(seed)).next(FS // 5)) ** 2)
        for seed in range(200)
    ]
    assert abs(10 * np.log10(np.mean(first))) < 1.0


def test_paths_fade_independently():
    ch = WattersonChannel(ChannelSpec(2, 2.0, 1.0))
    h0, h1 = ch._fading[0], ch._fading[1]
    a, b = h0.next(FS * 600), h1.next(FS * 600)
    corr = abs(np.vdot(a, b)) / np.sqrt(np.vdot(a, a).real * np.vdot(b, b).real)
    assert corr < 0.15  # about 600 independent fades: residual correlation ~ 1/sqrt(600)


# --- E.4 / E.5 structure -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "delay_ms, sample_rate, taps",
    [(2.0, 9600, 19), (5.0, 9600, 48), (2.0, 48000, 96), (0.5, 9600, 5)],  # 4.8 rounds to 5
)
def test_two_paths_are_delay_spread_apart(delay_ms, sample_rate, taps):
    """E.5.2: L = delay spread / sampling period, rounded (2 ms at 9600 S/s: 19.2 -> 19)."""
    ch = WattersonChannel(ChannelSpec(2, delay_ms, 0.0), sample_rate)
    impulse = np.zeros(200, complex)
    impulse[0] = 1
    y = ch.process(impulse)
    assert np.flatnonzero(np.abs(y) > 1e-12).tolist() == [0, taps]
    assert np.allclose(np.abs(y[[0, taps]]) ** 2, 0.5)  # equal power, total 1


# --- E.7.1 bandwidth ---------------------------------------------------------------------------


@pytest.mark.parametrize("tone_hz", [1050, 1800, 2550])
def test_flat_response_at_band_centre_and_plus_minus_25_percent(tone_hz):
    """E.7.1: within +/-0.5 dB at the band centre and +/-25% of the 3-kHz bandwidth, for a real
    input made analytic by the Hilbert transform (E.4.2). Measured: within 0.01 dB."""
    t = np.arange(FS * 3) / FS
    z = Analytic().process(np.cos(2 * np.pi * tone_hz * t))[1000:]
    y = WattersonChannel(ChannelSpec(1, 0.0, 0.0)).process(z)
    assert abs(10 * np.log10(np.mean(np.abs(y) ** 2))) < 0.5


def test_analytic_conversion_removes_the_negative_frequency():
    t = np.arange(FS * 2) / FS
    z = Analytic().process(np.cos(2 * np.pi * 1800 * t))[500:]
    spectrum = np.abs(np.fft.fft(z * np.hanning(z.size))) ** 2
    f = np.fft.fftfreq(z.size, 1 / FS)
    image = spectrum[np.abs(f + 1800) < 20].sum() / spectrum[np.abs(f - 1800) < 20].sum()
    assert 10 * np.log10(image) < -40


# --- E.7.2 / E.7.3 SNR -------------------------------------------------------------------------


def in_band_noise_power(noise, lo=300, hi=3300, nfft=8192):
    acc = np.zeros(nfft)
    for k in range(0, noise.size - nfft, nfft):
        acc += np.abs(np.fft.fft(noise[k : k + nfft])) ** 2
    f = np.fft.fftfreq(nfft, 1 / FS)
    return np.mean(np.abs(noise) ** 2) * acc[(f >= lo) & (f < hi)].sum() / acc.sum()


@pytest.mark.parametrize("snr_db", [0.0, 10.0, 20.0])
def test_snr_on_a_fixed_path(snr_db):
    """E.7.2: within 0.25 dB, SNR measured in a 3-kHz bandwidth (E.5.5). 60 s average."""
    tone = np.sqrt(0.16) * np.exp(2j * np.pi * 1800 * np.arange(FS * 60) / FS)
    y = WattersonChannel(ChannelSpec(1, 0.0, 0.0, snr_db), seed=3).process(tone)
    measured = 10 * np.log10(0.16 / in_band_noise_power(y - tone))
    assert abs(measured - snr_db) < 0.25


@pytest.mark.parametrize("paths", [1, 2])
def test_average_power_through_fading_paths(paths):
    """E.7.3: average SNR within 0.5 dB through fading paths, i.e. the paths' average total
    power gain is 1. 10 Hz Doppler over 10 minutes. Measured: within 0.06 dB."""
    ch = WattersonChannel(ChannelSpec(paths, 2.0, 10.0), seed=4)
    tone = np.exp(2j * np.pi * 1800 * np.arange(FS * 60) / FS)
    power = np.mean([np.mean(np.abs(ch.process(tone)) ** 2) for _ in range(10)])
    assert abs(10 * np.log10(power)) < 0.5


def test_no_noise_without_snr():
    x = np.exp(2j * np.pi * 0.1 * np.arange(1000))
    assert np.allclose(WattersonChannel(ChannelSpec(1, 0.0, 0.0)).process(x), x)


# --- Streaming and reproducibility ------------------------------------------------------------


@settings(max_examples=15, deadline=None)
@given(st.lists(st.integers(1, 3000), min_size=1, max_size=6))
def test_output_is_bit_identical_however_chunked(chunks):
    x = np.exp(2j * np.pi * 0.19 * np.arange(20000))
    spec = ChannelSpec(2, 5.0, 5.0, 10.0)
    reference = WattersonChannel(spec, seed=9).process(x)
    ch, out, i = WattersonChannel(spec, seed=9), [], 0
    for size in itertools.cycle(chunks):
        if i >= x.size:
            break
        out.append(ch.process(x[i : i + size]))
        out.append(ch.process(x[:0]))
        i += size
    assert np.array_equal(np.concatenate(out), reference)


def test_seed_reproducibility():
    x = np.ones(5000, complex)
    spec = ChannelSpec(2, 2.0, 1.0, 5.0)
    assert np.array_equal(
        WattersonChannel(spec, seed=1).process(x), WattersonChannel(spec, seed=1).process(x)
    )
    assert not np.array_equal(
        WattersonChannel(spec, seed=1).process(x), WattersonChannel(spec, seed=2).process(x)
    )


def test_table_xvi_presets():
    assert TABLE_XVI_CHANNELS["2 fading 5 ms 5 Hz"] == ChannelSpec(2, 5.0, 5.0)
    assert not TABLE_XVI_CHANNELS["1 fixed"].fading
