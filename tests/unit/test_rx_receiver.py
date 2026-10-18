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
from m110c.rx.receiver import RAW_HISTORY_SECONDS, Receiver
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


@pytest.mark.parametrize("m", all_modes(), ids=str)
def test_every_mode_is_received_with_the_interleave_told_from_the_data(m):
    """The default, interleave="auto" (S4): zero and short interleave share a preamble, and
    the receiver tells them apart from the data; the message reports the mode found."""
    out, rx = receive(burst(m))
    msg = rx.messages[0]
    assert out == MSG and msg.mode == m and msg.eom_found
    told = m.rate != 4800 and m.interleave in ("zero", "short")
    assert msg.interleave_detection == (m.interleave if told else None)
    assert any(n.startswith(f"interleave: {m.interleave} (") for n in msg.notes) == told


@pytest.mark.parametrize("interleave, expected", [("short", "600S"), ("zero", "600Z")])
def test_a_forced_interleave_is_taken_as_given(interleave, expected):
    """interleave="short" or "zero" assumes one, as before S4: forcing the wrong one loses
    the message, which is what auto prevents."""
    out, rx = receive(burst(mode(600, "zero")), interleave=interleave)
    assert str(rx.messages[0].mode) == expected and rx.messages[0].interleave_detection is None
    assert (out == MSG) == (expected == "600Z")


def test_an_unknown_interleave_option_is_refused():
    with pytest.raises(ValueError, match="auto, short or zero"):
        Receiver(interleave="long")


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
    """Raw samples are kept for RAW_HISTORY_SECONDS (to resume the search where a message
    ended), plus up to one input block; a 19-s transmission must not accumulate."""
    m = mode(2400, "long")
    x = burst(m, bytes(range(256)) * 16)
    rx = Receiver()
    largest = 0
    for i in range(0, x.size, 4096):
        rx.process(x[i : i + 4096])
        largest = max(largest, rx._raw.size)
    assert RAW_HISTORY_SECONDS * 9600 <= largest <= RAW_HISTORY_SECONDS * 9600 + 4096


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


# --- Resources (M12) -----------------------------------------------------------------------


def test_linear_algebra_runs_on_one_blas_thread_whatever_the_caller_set(monkeypatch):
    """The equaliser's thousands of small solves ran 10x slower on a multithreaded BLAS
    (docs/PERFORMANCE.md, M12). The limit must hold inside the receiver even when the
    caller's process allows more threads, and the caller's setting must come back after."""
    from threadpoolctl import ThreadpoolController, threadpool_limits

    import m110c.rx.equalize as E

    seen = []
    original = E.estimate_channel

    def spy(*args, **kwargs):
        seen.append(
            {i["num_threads"] for i in ThreadpoolController().select(user_api="blas").info()}
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(E, "estimate_channel", spy)
    blas = ThreadpoolController().select(user_api="blas")
    if not blas.info():
        pytest.skip("no BLAS library loaded")
    with threadpool_limits(limits=2, user_api="blas"):
        out, _ = receive(burst(mode(2400, "short")))
        after = {i["num_threads"] for i in ThreadpoolController().select(user_api="blas").info()}
    assert out == MSG
    assert seen and all(s == {1} for s in seen)
    assert after == {2}


def _traced_memory(x, at_seconds, chunk=9600):
    """Run a receiver over ``x`` in 1-s chunks; tracemalloc's current bytes at each mark."""
    import tracemalloc

    marks = {}
    tracemalloc.start()
    try:
        rx = Receiver()
        for i, k in enumerate(range(0, x.size, chunk)):
            rx.process(x[k : k + chunk])
            if i + 1 in at_seconds:
                marks[i + 1] = tracemalloc.get_traced_memory()
        rx.finish()
    finally:
        tracemalloc.stop()
    return rx, marks


def test_memory_does_not_grow_during_a_long_message():
    """Every unit's SNR used to be kept for the whole message: about 2.5 kB a second at
    75 bps, 200 kB over this message, 9 MB an hour. Only the most recent are kept now."""
    msg = bytes(np.random.default_rng(3).integers(0, 256, 1000, dtype=np.uint8))
    x = burst(mode(75, "short"), msg)
    rx, marks = _traced_memory(x, (20, 100))
    assert rx.messages and rx.messages[0].n_bytes == len(msg)
    assert marks[100][0] - marks[20][0] < 100e3


@pytest.mark.slow
def test_ten_minutes_of_mixed_input_in_bounded_memory():
    """Noise, a long 2400-bps message, noise, a long 75-bps message, noise: 10 minutes.
    After the first message has started, neither the current nor the peak memory grows."""
    rng = np.random.default_rng(4)

    def noise(seconds):
        return 0.1 * (
            rng.standard_normal(9600 * seconds) + 1j * rng.standard_normal(9600 * seconds)
        )

    a = bytes(rng.integers(0, 256, 60000, dtype=np.uint8))  # about 200 s at 2400 bps
    b = bytes(rng.integers(0, 256, 1800, dtype=np.uint8))  # about 200 s at 75 bps
    x = np.concatenate(
        [
            noise(60),
            burst(mode(2400, "long"), a),
            noise(30),
            burst(mode(75, "short"), b),
            noise(120),
        ]
    )
    assert x.size >= 600 * 9600
    rx, marks = _traced_memory(x, (120, 300, x.size // 9600 - 1))
    assert [m.n_bytes for m in rx.messages] == [len(a), len(b)]
    first, *later = sorted(marks)
    for t in later:
        assert marks[t][0] < marks[first][0] + 200e3  # current
        assert marks[t][1] < marks[first][1] + 2e6  # peak (a 2400L interleaver block in flight)


# --- Back to back at different rates (M12) -------------------------------------------------


@pytest.mark.parametrize(
    "first, second",
    [
        (mode(2400, "short"), mode(75, "short")),
        (mode(75, "short"), mode(2400, "short")),
        (mode(75, "short"), mode(4800)),
        (mode(4800), mode(75, "short")),
        (mode(75, "long"), mode(1200, "long")),
    ],
    ids=str,
)
@pytest.mark.parametrize("tail", [0, 9600], ids=["input ends", "silence after"])
def test_back_to_back_at_different_rates(first, second, tail):
    """With no gap, a 75-bps demodulator takes the next preamble for more signal (and the
    other way round), so loss of signal came up to a second late and the search for the
    next message started past most of its preamble. The end now comes from the EOM. When
    the input ended soon after the second message, it was never searched for at all."""
    a = burst(first, b"first", lead=500, tail=0)
    b = burst(second, b"second", lead=0, tail=tail)
    out, rx = receive(np.concatenate([a, b]))
    assert out == b"firstsecond"
    assert [m.mode for m in rx.messages] == [first, second]
    # The SNR covers each message only: what followed used to count as noise (-4 dB).
    assert all(m.snr_db > 40 for m in rx.messages)


def test_message_end_is_placed_from_the_eom():
    a = burst(mode(2400, "short"), b"first", lead=500, tail=0)
    b = burst(mode(75, "short"), b"second", lead=0)
    _, rx = receive(np.concatenate([a, b]))
    assert abs(rx.messages[0].end_sample - a.size) < 100  # within the pulse-shaping tail


# --- INT-019: digital voice ------------------------------------------------------------


VOICE = mode(2400, "short", voice=True)


def test_int_019_voice_is_received_as_a_raw_bit_stream_when_asked_for():
    bits = bytes(np.random.default_rng(5).integers(0, 256, 600, dtype=np.uint8))
    out, rx = receive(burst(VOICE, bits), voice=True)
    assert out == bits
    assert [(str(m.mode), m.eom_found) for m in rx.messages] == [("2400V", True)]


def test_int_019_voice_is_ignored_by_default():
    out, rx = receive(burst(VOICE))
    assert out == b"" and rx.messages == []


def test_int_019_voice_is_never_async_deframed():
    """A vocoder's bits are not characters: with --async, voice still comes out as raw
    synchronous bytes, and a data message after it is still deframed."""
    fmt = AsyncFormat.parse("8N1")
    x = np.concatenate([burst(VOICE), burst(mode(1200, "short"), async_format=fmt)])
    out, rx = receive(x, voice=True, async_format=fmt)
    assert out == MSG + MSG
    assert [str(m.mode) for m in rx.messages] == ["2400V", "1200S"]
