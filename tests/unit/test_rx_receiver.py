"""Tests for the complete receiver: acquisition + tracking + decoding (M8)."""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c.channel.awgn import add_awgn
from m110c.channel.impairments import clock_offset, frequency_offset, frequency_sweep
from m110c.framing import AsyncFormat
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.rx.track import interpolate
from m110c.spec import all_modes, mode
from m110c.tx import transmit_bytes

MSG = b"The quick brown fox jumps over the lazy dog 0123456789"


def burst(m, msg=MSG, lead=2777, tail=4000, offset_hz=0.0, ppm=0.0, sample_rate=9600, **tx):
    x = modulate(transmit_bytes(msg, m, **tx), sample_rate=sample_rate)
    if ppm:
        x = clock_offset(x, ppm)
    x = frequency_offset(x, offset_hz, sample_rate)
    return np.concatenate([np.zeros(lead, complex), x, np.zeros(tail, complex)])


def receive(x, chunks=None, **kwargs):
    rx = Receiver(**kwargs)
    if chunks is None:
        out = rx.process(x)
    else:
        out, i = b"", 0
        for size in itertools.cycle(chunks):
            if i >= x.size:
                break
            out += rx.process(x[i : i + size])
            i += size
    return out + rx.finish(), rx


# --- Clean signals -----------------------------------------------------------------------------


@pytest.mark.parametrize("m", all_modes(), ids=str)
def test_every_mode_is_acquired_and_decoded(m):
    zero = m.interleave == "zero" and m.rate != 4800
    out, rx = receive(burst(m), zero_interleave=zero)
    assert out == MSG
    assert len(rx.messages) == 1 and rx.messages[0].mode == m and rx.messages[0].eom_found


def test_signal_starting_at_sample_zero():
    m = mode(1200, "short")
    out, _ = receive(burst(m, lead=0))
    assert out == MSG


@pytest.mark.parametrize("offset_hz", [-400, -151.7, 89, 400])
def test_carrier_offsets_across_the_search_range(offset_hz):
    out, rx = receive(burst(mode(2400, "long"), offset_hz=offset_hz))
    assert out == MSG
    assert abs(rx.messages[0].detection.carrier_hz - 1800 - offset_hz) < 0.5


@pytest.mark.parametrize("ppm", [-2000, -900, 900, 2000])
@pytest.mark.parametrize("m", [mode(2400, "short"), mode(75, "short")], ids=str)
def test_clock_offsets_up_to_0_2_percent(m, ppm):
    """Transmitter clock off by up to 0.2% (PLAN §4.8; real captures reached 0.09%)."""
    out, rx = receive(burst(m, ppm=ppm))
    assert out == MSG
    assert abs(rx.messages[0].clock_ppm - ppm) < 150


@pytest.mark.parametrize(
    "m, n_bytes", [(mode(75, "short"), 60), (mode(2400, "short"), 1500)], ids=str
)
def test_doppler_sweep_of_3_5_hz_per_second(m, n_bytes):
    """Appendix C's Doppler-sweep rate (C.6.4: 3.5 Hz/s). Measured: with frequency
    tracking every mode decodes even at 10 Hz/s; without it, every mode fails at 3.5 Hz/s."""
    payload = (bytes(range(256)) * 8)[:n_bytes]
    x = modulate(transmit_bytes(payload, m))
    x = np.concatenate(
        [np.zeros(1000, complex), frequency_sweep(x, 3.5, 9600), np.zeros(3000, complex)]
    )
    out, _ = receive(x)
    assert out == payload


def test_carrier_and_clock_offset_together():
    out, _ = receive(burst(mode(600, "long"), offset_hz=-317, ppm=1500))
    assert out == MSG


# --- Noise ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "m, snr_db",
    [
        (mode(2400, "short"), 10),
        (mode(1200, "long"), 6),
        (mode(300, "short"), 0),
        (mode(75, "short"), -3),
    ],
    ids=["2400S@10dB", "1200L@6dB", "300S@0dB", "75S@-3dB"],
)
def test_decodes_in_noise(m, snr_db):
    x = add_awgn(burst(m), snr_db, 9600, np.random.default_rng(1), signal_power=0.16)
    out, rx = receive(x)
    assert out == MSG and rx.messages[0].eom_found


def test_noise_alone_produces_nothing():
    rng = np.random.default_rng(2)
    noise = 0.3 * (rng.standard_normal(9600 * 20) + 1j * rng.standard_normal(9600 * 20))
    out, rx = receive(noise)
    assert out == b"" and rx.messages == []


# --- Several messages, and endings ------------------------------------------------------------


def test_back_to_back_messages_with_a_gap():
    a, b = burst(mode(2400, "short"), b"first message"), burst(mode(150, "long"), b"second message")
    out, rx = receive(np.concatenate([a, b]))
    assert out == b"first messagesecond message"
    assert [m.mode for m in rx.messages] == [mode(2400, "short"), mode(150, "long")]


def test_back_to_back_messages_without_a_gap():
    a = burst(mode(1200, "short"), b"one", lead=500, tail=0)
    b = burst(mode(600, "short"), b"two", lead=0)
    out, rx = receive(np.concatenate([a, b]))
    assert out == b"onetwo" and len(rx.messages) == 2


def test_message_followed_by_long_noise():
    rng = np.random.default_rng(3)
    x = np.concatenate([burst(mode(2400, "short")), np.zeros(9600 * 10, complex)])
    x = add_awgn(x, 15, 9600, rng, signal_power=0.16)
    out, rx = receive(x)
    assert out == MSG and len(rx.messages) == 1


def test_truncated_transmission_returns_what_it_has():
    m = mode(600, "zero")
    x = burst(m, bytes(range(200)), tail=0)
    out, rx = receive(x[: int(0.7 * x.size)], zero_interleave=True)
    assert not rx.messages[0].eom_found
    assert len(out) > 50 and bytes(range(200)).startswith(out[:50])


# --- Options and inputs --------------------------------------------------------------------------


def test_invert_spectrum():
    """INT-021: a lower-sideband receiver delivers the mirror image (the conjugate)."""
    x = np.conj(burst(mode(1200, "short")))
    assert receive(x)[0] == b""
    assert receive(x, invert_spectrum=True)[0] == MSG


def test_real_audio_input():
    out, _ = receive(burst(mode(2400, "long"), offset_hz=0).real)
    assert out == MSG


def test_48k_sample_rate():
    out, _ = receive(burst(mode(300, "long"), sample_rate=48000, lead=12000), sample_rate=48000)
    assert out == MSG


def test_async_characters():
    fmt = AsyncFormat.parse("8N1")
    out, _ = receive(burst(mode(1200, "short"), async_format=fmt), async_format=fmt)
    assert out == MSG


# --- Streaming ---------------------------------------------------------------------------


@settings(max_examples=8, deadline=None)
@given(st.lists(st.integers(1, 6000), min_size=1, max_size=4))
def test_output_does_not_depend_on_chunking(chunks):
    x = add_awgn(
        burst(mode(1200, "short"), offset_hz=55),
        12,
        9600,
        np.random.default_rng(4),
        signal_power=0.16,
    )
    whole, _ = receive(x)
    pieces, _ = receive(x, chunks)
    assert pieces == whole == MSG


def test_raw_buffer_stays_bounded():
    m = mode(2400, "long")
    x = burst(m, bytes(range(256)) * 8)
    rx = Receiver()
    for i in range(0, x.size, 4096):
        rx.process(x[i : i + 4096])
        assert rx._raw.size < 3 * 9600  # about a second plus a search block


# --- Building blocks ---------------------------------------------------------------------


def test_cubic_interpolation_of_a_band_limited_signal():
    """The tracker reads symbols between samples by cubic interpolation. Worst case is a
    tone at the band edge (1500 Hz at 9600 S/s): measured error 2.0%, about -34 dB. That
    sets the ~35 dB ceiling on tracked SNR seen on clean signals; a longer interpolator is an
    M10 candidate."""
    n = np.arange(400)
    f = 1500 / 9600
    u = np.exp(2j * np.pi * f * n)
    t = np.linspace(5, 390, 1000)
    assert np.max(np.abs(interpolate(u, t) - np.exp(2j * np.pi * f * t))) < 0.025
