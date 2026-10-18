"""Tests for the 8-PSK modulator (M6).

Tolerances come from measurements taken when the modulator was written (noted per test),
with margin. They are not tuned to make the tests pass.
"""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c.modulate import BLOCK_SYMBOLS, Modulator, modulate, rrc_taps, tribits_to_phasors
from m110c.spec import all_modes
from m110c.tx import transmit_bytes


def matched_filter_symbols(x, n_symbols, sample_rate=9600, carrier_hz=1800.0):
    """Genie demodulator: remove the carrier, matched-filter, sample at the known times."""
    mod = Modulator(sample_rate=sample_rate, carrier_hz=carrier_hz)
    n = np.arange(x.size)
    baseband = x * np.exp(-2j * np.pi * carrier_hz * n / sample_rate)
    y = np.convolve(baseband, mod.taps[::-1].conj())
    peak = 2 * mod.delay  # transmit filter delay + matched filter delay
    return y[peak + mod.sps * np.arange(n_symbols)]


# --- Pulse shape (INT-015) -------------------------------------------------------------


@pytest.mark.parametrize("sps", [4, 20])
def test_rrc_is_unit_energy_and_symmetric(sps):
    h = rrc_taps(sps)
    assert h.size == 2 * 8 * sps + 1
    assert np.isclose(np.sum(h**2), 1.0)
    assert np.allclose(h, h[::-1])


@pytest.mark.parametrize("sps", [4, 20])
def test_rrc_pair_is_nyquist(sps):
    """Transmit RRC followed by a matched RRC is a raised cosine: no inter-symbol
    interference at the symbol instants. Measured: worst 5.4e-4 (sps 4), 8.7e-4 (sps 20),
    the residue of truncating the filter at +/-8 symbols."""
    h = rrc_taps(sps)
    g = np.convolve(h, h)
    centre = (g.size - 1) // 2
    at_symbols = g[centre % sps :: sps]
    assert np.isclose(at_symbols[centre // sps], 1.0)
    assert np.max(np.abs(np.delete(at_symbols, centre // sps))) < 2e-3


def test_rrc_rejects_bad_rolloff():
    with pytest.raises(ValueError):
        rrc_taps(4, alpha=0)


def test_tribit_k_is_phase_45k_degrees():
    """§5.3.2.3.9 a: 0 -> 0 degrees, 1 -> 45 degrees, 2 -> 90 degrees, ..."""
    angles = np.rad2deg(np.angle(tribits_to_phasors(np.arange(8)))) % 360
    assert np.allclose(angles, 45 * np.arange(8))


# --- Constellation, carrier, spectrum ----------------------------------------------------


@pytest.mark.parametrize("sample_rate, carrier", [(9600, 1800.0), (48000, 1800.0), (9600, 0.0)])
def test_symbols_are_recovered_at_the_symbol_instants(sample_rate, carrier):
    tri = np.random.default_rng(1).integers(0, 8, 500)
    x = modulate(tri, sample_rate=sample_rate, carrier_hz=carrier)
    rx = matched_filter_symbols(x, 500, sample_rate, carrier)
    rx /= np.mean(np.abs(rx))
    error = np.angle(rx * np.conj(tribits_to_phasors(tri)))
    assert np.max(np.abs(error)) < 0.02  # radians; measured below 0.01


@pytest.mark.parametrize("sample_rate", [9600, 48000, 8000, 44100])
def test_carrier_frequency_is_within_1_hz(sample_rate):
    """§5.3.2.3.9 b: "Clock accuracy for generation of the 1800-Hz carrier shall be within
    +/-1 Hz". An all-zero tribit stream is a pure carrier; fit its phase slope over 10 s."""
    x = modulate(np.zeros(24000, np.uint8), sample_rate=sample_rate)
    core = x[sample_rate // 5 : -sample_rate // 5]  # skip the filter ramps
    slope = np.polyfit(np.arange(core.size) / sample_rate, np.unwrap(np.angle(core)), 1)[0]
    assert abs(slope / (2 * np.pi) - 1800) < 1e-3


def power_spectrum(x, sample_rate=9600, n=4096):
    acc = np.zeros(n)
    for k in range(0, x.size - n, n // 2):
        acc += np.abs(np.fft.fft(x[k : k + n] * np.hanning(n))) ** 2
    return np.fft.fftfreq(n, 1 / sample_rate), acc / acc.sum()


def test_signal_occupies_the_300_to_3300_hz_voice_channel():
    """INT-015: roll-off 0.25 -> 1800 +/- 1500 Hz. Measured: 99.997% of power in band."""
    f, p = power_spectrum(modulate(np.random.default_rng(2).integers(0, 8, 24000)))
    assert p[(f >= 300) & (f <= 3300)].sum() > 0.9995


def test_spectrum_stops_at_the_rolloff_band_edge():
    """Roll-off 0.25 puts the band edge at 1800 +/- 1500 Hz. Measured power beyond
    +/-1550 Hz of the carrier: 1.9e-5 for roll-off 0.25, but 3.5e-4 for 0.35 and 1.6e-2
    for 0.6. The threshold separates them, so the band edge is really tested."""
    f, p = power_spectrum(modulate(np.random.default_rng(2).integers(0, 8, 24000)))
    assert p[np.abs(f - 1800) > 1550].sum() < 1e-4


@pytest.mark.parametrize("carrier", [1750.0, 1333.3])
def test_carrier_phase_is_continuous_for_any_frequency(carrier):
    """At 1800 Hz a 240-symbol block is exactly 60 carrier cycles, which would hide a
    carrier that restarts its phase each block. Other carriers expose it: the phase of a
    pure carrier must then follow one straight line with no jumps."""
    x = modulate(np.zeros(4800, np.uint8), carrier_hz=carrier)[2000:-2000]
    phase = np.unwrap(np.angle(x))
    t = np.arange(x.size) / 9600
    fit = np.polyval(np.polyfit(t, phase, 1), t)
    assert np.max(np.abs(phase - fit)) < 1e-6


def test_complex_output_is_analytic():
    """With the carrier at 1800 Hz and 1500 Hz of half-bandwidth, nothing should sit at
    negative frequencies: the real part alone carries the whole signal (upper sideband).
    Measured: 3.9e-6 of the power."""
    f, p = power_spectrum(modulate(np.random.default_rng(3).integers(0, 8, 24000)))
    assert p[f < 0].sum() < 1e-4


# --- Levels ----------------------------------------------------------------------------


def test_rms_level():
    x = modulate(np.random.default_rng(4).integers(0, 8, 24000))
    assert abs(np.sqrt(np.mean(np.abs(x) ** 2)) - 0.4) < 0.01


@pytest.mark.parametrize("m", all_modes(), ids=lambda m: m.name)
def test_no_mode_comes_near_full_scale(m):
    """Integer formats saturate at 1.0. Measured worst-case peak over all modes: 0.685."""
    x = modulate(transmit_bytes(bytes(range(256)), m))
    assert np.abs(x).max() < 0.85


# --- Streaming ---------------------------------------------------------------------------


@settings(max_examples=30, deadline=None)
@given(st.lists(st.integers(1, 700), min_size=1, max_size=8))
def test_output_is_bit_identical_however_input_is_chunked(chunk_sizes):
    """Exact equality, not closeness: the fixed internal block size makes the floating-point
    arithmetic independent of how tribits arrive."""
    tri = np.random.default_rng(5).integers(0, 8, 3000).astype(np.uint8)
    reference = modulate(tri)
    mod = Modulator()
    pieces, i = [], 0
    for size in itertools.cycle(chunk_sizes):
        if i >= tri.size:
            break
        pieces.append(mod.process(tri[i : i + size]))
        pieces.append(mod.process(tri[:0]))
        i += size
    pieces.append(mod.flush())
    assert np.array_equal(np.concatenate(pieces), reference)


def test_output_length():
    mod = Modulator()
    n = 1000
    x = modulate(np.zeros(n, np.uint8))
    assert x.size == n * mod.sps + mod.taps.size - 1


def test_buffering_is_bounded_by_one_block():
    mod = Modulator()
    for _ in range(50):
        mod.process(np.zeros(97, np.uint8))
        assert mod._pending.size < BLOCK_SYMBOLS


def test_flush_resets_for_a_new_transmission():
    mod = Modulator()
    first = np.concatenate([mod.process(np.arange(8)), mod.flush()])
    mod2 = Modulator()
    second_fresh = np.concatenate([mod2.process(np.arange(8)), mod2.flush()])
    assert np.array_equal(first, second_fresh)


def test_sample_rates_too_low_for_the_voice_channel_are_refused():
    """Rates that are not multiples of 2400 are resampled (S3); below 6800 S/s the
    300-3300 Hz signal does not fit."""
    with pytest.raises(ValueError, match="above 6800"):
        Modulator(sample_rate=6000)
    assert Modulator(sample_rate=8000).modulation_rate == 9600


# --- End to end ----------------------------------------------------------------------------


@pytest.mark.parametrize("m", all_modes(), ids=lambda m: m.name)
def test_every_mode_survives_modulation(m):
    """Framer -> modulator -> genie demodulator gives back the framer's exact tribits."""
    tri = transmit_bytes(b"M6 end-to-end check", m)
    rx = matched_filter_symbols(modulate(tri), tri.size)
    decided = np.round(np.angle(rx) / (np.pi / 4)).astype(int) % 8
    assert np.array_equal(decided, tri)
