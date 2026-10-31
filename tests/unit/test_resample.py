"""Tests for the rational resampler (S3) and for the modem at rates that are not multiples of
2400 S/s, where it is used."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c import resample as R
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit_bytes

PAIRS = [(8000, 9600), (9600, 8000), (44100, 9600), (9600, 44100), (11025, 9600), (22050, 9600)]


def tone(f, rate, seconds=1.0):
    return np.exp(2j * np.pi * f * np.arange(int(rate * seconds)) / rate)


def middle(x):
    return x[x.size // 4 : 3 * x.size // 4]


@pytest.mark.parametrize("rate_in, rate_out", PAIRS)
def test_the_ratio_is_exact_and_in_lowest_terms(rate_in, rate_out):
    r = R.Resampler(rate_in, rate_out)
    assert r.up * rate_in == r.down * rate_out
    assert np.gcd(r.up, r.down) == 1
    x = np.zeros(12345, complex)
    assert R.resample(x, rate_in, rate_out).size == -(-12345 * rate_out // rate_in)


@pytest.mark.parametrize("rate_in, rate_out", PAIRS)
@pytest.mark.parametrize("f", [300.0, 1800.0, 3300.0])
def test_tones_in_the_voice_channel_come_out_unchanged(rate_in, rate_out, f):
    """Amplitude and phase, at the right times: no delay, and errors near -70 dB at worst."""
    y = R.resample(tone(f, rate_in), rate_in, rate_out)
    assert np.max(np.abs(middle(y) - middle(tone(f, rate_out, y.size / rate_out)))) < 3e-4


def in_band_leakage_db(y, rate, n=4096):
    """Power in 300-3300 Hz (both signs) relative to the total, in dB, of the middle."""
    seg = middle(y)[:n] * np.hanning(n)
    spectrum = np.abs(np.fft.fft(seg)) ** 2
    f = np.abs(np.fft.fftfreq(n, 1 / rate))
    return 10 * np.log10(spectrum[(f >= 300) & (f <= 3300)].sum() / spectrum.sum())


@pytest.mark.parametrize("rate_in", [44100, 22050, 16000])
def test_what_would_alias_into_the_voice_channel_is_stopped(rate_in):
    """Down to 9600 S/s, a tone at 9600 - 3300 + 100 Hz would land at -3200 Hz, inside the
    band (the real signal's mirror image). The filter stops it by 70 dB or more. (Not at
    11025 S/s: nothing it can carry, below 5512 Hz, folds into the band at 9600.)"""
    f = 9600 - 3300 + 100
    y = R.resample(tone(f, rate_in), rate_in, 9600)
    assert np.max(np.abs(middle(y))) < 10 ** (-70 / 20)
    assert in_band_leakage_db(R.resample(tone(1800, rate_in), rate_in, 9600), 9600) > -1e-3


@pytest.mark.parametrize("rate_out", [44100, 11025, 9600])
def test_no_images_above_the_band_when_going_up(rate_out):
    """Up from 8000 S/s, the copies of the spectrum at multiples of 8000 Hz are removed:
    everything outside the band is 70 dB down."""
    y = R.resample(tone(1800, 8000, seconds=4.0), 8000, rate_out)
    n = 8192
    seg = middle(y)[:n] * np.kaiser(n, 14)
    spectrum = np.abs(np.fft.fft(seg)) ** 2
    f = np.fft.fftfreq(n, 1 / rate_out)
    outside = spectrum[np.abs(f - 1800) > 300].sum() / spectrum.sum()
    assert 10 * np.log10(outside) < -70


def test_there_and_back_again():
    x = np.random.default_rng(1).standard_normal(9600) + 0j
    # Keep only the voice channel, which is what the resampler promises to preserve.
    spectrum = np.fft.fft(x)
    f = np.abs(np.fft.fftfreq(x.size, 1 / 9600))
    x = np.fft.ifft(np.where(f <= 3300, spectrum, 0))
    back = R.resample(R.resample(x, 9600, 44100), 44100, 9600)
    assert back.size == x.size
    assert np.max(np.abs(middle(back) - middle(x))) < 1e-3 * np.max(np.abs(x))


@settings(max_examples=10, deadline=None)
@given(st.lists(st.integers(1, 5000), min_size=1, max_size=6))
def test_output_does_not_depend_on_chunking(chunks):
    x = np.random.default_rng(2).standard_normal(20000) + 1j
    whole = R.resample(x, 44100, 9600)
    r, out, i = R.Resampler(44100, 9600), [], 0
    for size in chunks:
        out.append(r.process(x[i : i + size]))
        i += size
    out += [r.process(x[i:]), r.flush()]
    assert np.array_equal(np.concatenate(out), whole)


@pytest.mark.parametrize("rate_in, rate_out", [(8000, 9600), (44100, 9600), (9600, 11025)])
def test_every_split_point_gives_the_same_output(rate_in, rate_out):
    """Exhaustively over two-piece inputs: an output is released only once every input it
    uses has arrived (one released a sample early would differ here)."""
    x = np.random.default_rng(5).standard_normal(300) + 1j
    whole = R.resample(x, rate_in, rate_out)
    for split in range(1, x.size):
        r = R.Resampler(rate_in, rate_out)
        parts = [r.process(x[:split]), r.process(x[split:]), r.flush()]
        assert np.array_equal(np.concatenate(parts), whole), split


@pytest.mark.parametrize("rate", [6800, 6000])
def test_rates_too_low_for_the_voice_channel_are_refused(rate):
    with pytest.raises(ValueError, match="above 6800"):
        R.Resampler(rate, 9600)


# --- The modem at these rates --------------------------------------------------------------


@pytest.mark.parametrize("rate", [8000, 44100])
def test_the_modulator_resamples_its_9600_signal(rate):
    tribits = np.random.default_rng(3).integers(0, 8, 3000).astype(np.uint8)
    x = modulate(tribits, sample_rate=rate)
    assert np.array_equal(x, R.resample(modulate(tribits), 9600, rate))


@pytest.mark.parametrize("rate", [8000, 11025, 16000, 22050, 44100])
def test_the_receiver_takes_any_audio_rate(rate):
    """Positions in its diagnostics count at 9600 S/s, the rate it works at."""
    msg = b"The quick brown fox jumps over the lazy dog"
    lead = rate // 2
    x = np.concatenate(
        [
            np.zeros(lead, complex),
            modulate(transmit_bytes(msg, mode(600, "short")), sample_rate=rate),
            np.zeros(rate, complex),
        ]
    )
    rx = Receiver(sample_rate=rate)
    out = b"".join(rx.process(x[i : i + 4000]) for i in range(0, x.size, 4000)) + rx.finish()
    assert out == msg and rx.input_rate == rate and rx.sample_rate == R.INTERNAL_RATE
    assert rx.messages[0].detection.segment_start == pytest.approx(4800, abs=2)


def test_the_receiver_takes_the_resamplers_last_samples_at_the_end():
    """finish() also consumes what the resampler held back: every input sample is accounted
    for, at the working rate."""
    rx = Receiver(sample_rate=8000)
    rx.process(np.zeros(8001, complex))
    rx.finish()
    assert rx._samples_in == -(-8001 * 6 // 5)


def test_the_receiver_runs_natively_at_multiples_of_2400():
    rx = Receiver(sample_rate=48000)
    assert rx.sample_rate == rx.input_rate == 48000 and rx._resampler is None


def test_spectrum_inversion_is_applied_once_when_resampling():
    """INT-021 at a resampled rate: the last samples, flushed out of the resampler at the
    end, must not be conjugated a second time."""
    msg = b"lower sideband"
    x = modulate(transmit_bytes(msg, mode(1200, "short")), sample_rate=8000)
    out = Receiver(sample_rate=8000, invert_spectrum=True)
    got = out.process(np.conj(np.concatenate([x, np.zeros(800, complex)]))) + out.finish()
    assert got == msg


# --- the fractional resampler (a sound card's clock error, --tx-ppm) --------------------


@pytest.mark.parametrize("ratio", [1.0, 1 + 50e-6, 1 - 200e-6, 1.01])
@pytest.mark.parametrize("f", [300.0, 1800.0, 3300.0])
def test_fractional_resampling_keeps_tones_exact(ratio, f):
    """A tone comes out as the same tone at the new rate: the samples played by a card whose
    clock runs ratio times fast are the tone at the intended frequency."""
    x = tone(f, 9600, 2.0)
    r = R.FractionalResampler(ratio, 9600)
    y = np.concatenate([r.process(x), r.flush()])
    assert y.size == int(np.ceil(x.size * ratio))
    expected = np.exp(2j * np.pi * f / ratio * np.arange(y.size) / 9600)
    assert np.max(np.abs(middle(y) - middle(expected))) < 3e-4


@settings(max_examples=25, deadline=None)
@given(st.lists(st.integers(1, 3000), min_size=1, max_size=8))
def test_fractional_resampling_does_not_depend_on_chunking(cuts):
    x = tone(1234.5, 9600, 1.0) * np.linspace(0.2, 1, 9600)
    whole = R.FractionalResampler(1 + 37e-6, 9600)
    reference = np.concatenate([whole.process(x), whole.flush()])
    r = R.FractionalResampler(1 + 37e-6, 9600)
    out, at = [], 0
    for n in cuts:
        out.append(r.process(x[at : at + n]))
        at += n
    out += [r.process(x[at:]), r.flush()]
    assert np.allclose(np.concatenate(out), reference, atol=1e-12)


def test_fractional_resampling_memory_stays_bounded():
    r = R.FractionalResampler(1 - 100e-6, 48000)
    for _ in range(50):
        r.process(np.ones(4800, complex))
    assert r._x.size < 100
