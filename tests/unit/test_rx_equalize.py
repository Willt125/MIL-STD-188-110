"""Tests for the equalising demodulator and the decoder's channel-state input (M10).

Building blocks are checked against exact expectations; the demodulator against channels
whose effect is known (fixed multipath, clock offsets, truncation) and against the
Appendix E simulator (Table XVI).
"""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c import scrambler
from m110c.channel.awgn import add_awgn
from m110c.channel.impairments import clock_offset, fractional_delay, frequency_offset
from m110c.channel.watterson import ChannelSpec, WattersonChannel
from m110c.modulate import modulate, rrc_taps
from m110c.rx import equalize as E
from m110c.rx.decode import DataDecoder
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.spec import tables as T
from m110c.tx import transmit_bytes

FS = 9600
RNG = np.random.default_rng(7)


def psk(n, rng=RNG):
    return np.exp(1j * np.pi / 4 * rng.integers(0, 8, n))


def message(n_bytes, seed=0):
    return bytes(np.random.default_rng(100 + seed).integers(0, 256, n_bytes, dtype=np.uint8))


def signal(m, msg, lead=4000, tail=8000):
    x = modulate(transmit_bytes(msg, m))
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


def two_paths(x, delay_ms, gains=(1.0, 1.0), fractional=True):
    """A fixed two-path channel with chosen complex gains (unit total power by default)."""
    gains = np.asarray(gains, dtype=complex) / np.sqrt(np.sum(np.abs(gains) ** 2))
    delay = delay_ms * 1e-3 * FS
    late = fractional_delay(x, delay)[: x.size] if fractional else np.roll(x, int(delay))
    return gains[0] * x + gains[1] * late


def bit_errors(a, b):
    n = min(len(a), len(b))
    diff = np.frombuffer(a[:n], np.uint8) ^ np.frombuffer(b[:n], np.uint8)
    return int(np.count_nonzero(np.unpackbits(diff))) + 8 * abs(len(a) - len(b))


# --- Channel estimation -------------------------------------------------------------------------


def test_convolution_matrix_entries():
    s = np.arange(20) * 1.0
    S = E.convolution_matrix(s, 5, np.array([10, 11]), np.array([-1, 0, 2]))
    assert np.array_equal(S, [[6, 5, 3], [7, 6, 4]])  # s_{k-j} with s[i] = s_{i+5}


def test_least_squares_recovers_the_channel_exactly_without_noise():
    h = psk(9) * np.linspace(1, 0.1, 9)
    S = np.array([psk(9) for _ in range(40)])
    estimate, noise = E.estimate_channel(S @ h, S)
    assert np.allclose(estimate, h) and noise < 1e-9  # the ridge leaves ~1e-6 of the taps


def test_least_squares_with_periodic_known_symbols():
    """The end of a long preamble repeats every 32 symbols, so over a 41-tap span taps j and
    j+32 cannot be told apart and S^H S is singular. Taps the data cannot see must come out
    zero, not as whatever the BLAS rounding makes of a singular solve (in CI, a phantom
    path at -15 that wrecked every long-preamble mode on one CPU type)."""
    offsets = np.arange(-20, 21)
    period = psk(32)
    s = np.tile(period, 10)
    ks = np.arange(60, 60 + 123)
    S = E.convolution_matrix(s, 0, ks, offsets)
    assert np.linalg.matrix_rank(S.conj().T @ S) < offsets.size  # really singular
    h = np.zeros(offsets.size, complex)
    h[[20, 21, 24]] = [1.0, 0.3j, -0.5]  # taps 0, 1, 4: not aliased with each other
    estimate, _ = E.estimate_channel(S @ h, S)
    assert np.allclose(estimate, h, atol=1e-5)


def test_noise_variance_estimate():
    h = psk(9)
    S = np.array([psk(9) for _ in range(4000)])
    n = np.sqrt(0.05) * (RNG.standard_normal(4000) + 1j * RNG.standard_normal(4000))
    _, noise = E.estimate_channel(S @ h + n, S)
    assert noise == pytest.approx(0.1, rel=0.05)


def test_lmmse_shrinks_taps_the_prior_says_are_empty():
    """Two taps carry the channel; the other seven are noise. With the delay profile as
    prior the noise taps nearly vanish and the total error falls well below least squares."""
    h = np.zeros(9, complex)
    h[[2, 6]] = [1, 0.5j]
    prior = np.where(h != 0, 1.0, 1e-6)
    ls_err = lmmse_err = 0.0
    for _ in range(200):
        S = np.array([psk(9) for _ in range(48)])
        r = S @ h + np.sqrt(0.05) * (RNG.standard_normal(48) + 1j * RNG.standard_normal(48))
        ls, noise = E.estimate_channel(r, S)
        lmmse, _ = E.estimate_channel(r, S, noise, prior)
        ls_err += np.sum(np.abs(ls - h) ** 2)
        lmmse_err += np.sum(np.abs(lmmse - h) ** 2)
    assert lmmse_err < 0.35 * ls_err  # 2 of 9 taps estimated: ideally 2/9 of the error


# --- MMSE decision-feedback equaliser -----------------------------------------------------------


def run_dfe(h, precursor, noise_var, n=3000, rng=None):
    """Equalise symbols through channel h with correct past decisions; return the
    estimates, the true symbols and the predicted error variance."""
    rng = rng or np.random.default_rng(1)
    s = psk(n + 100, rng)
    j = np.arange(h.size) - precursor
    k = np.arange(50, n + 50)
    r = np.zeros(n + 100, complex)
    for jj, hj in zip(j, h, strict=True):
        r += hj * np.roll(s, jj)
    r += np.sqrt(noise_var / 2) * (rng.standard_normal(r.size) + 1j * rng.standard_normal(r.size))
    f, g, gamma = E.mmse_dfe(h, noise_var, precursor, E.LOOKAHEAD)
    m = np.arange(-precursor, h.size - precursor + E.LOOKAHEAD)
    past = np.arange(1, h.size)
    z = np.array([(np.vdot(f, r[kk + m]) - np.vdot(g, s[kk - past])) / gamma for kk in k])
    return z, s[k], (1 - gamma) / gamma


@pytest.mark.parametrize("weak_first", [False, True])
def test_dfe_is_nearly_exact_without_noise(weak_first):
    """Also when the first path is the weak one (the precursor must be undone by the
    feedforward filter alone)."""
    h = np.zeros(15, complex)
    h[2], h[2 + 9] = (0.3, 1.0j) if weak_first else (1.0, 0.6j)
    z, s, _ = run_dfe(h, 2, 1e-8)
    assert np.max(np.abs(z - s)) < 1e-2


def test_dfe_reports_its_error_variance():
    h = np.zeros(12, complex)
    h[3], h[8] = 0.8, 0.6j
    z, s, predicted = run_dfe(h, 3, 0.05)
    assert np.mean(np.abs(z - s) ** 2) == pytest.approx(predicted, rel=0.15)
    assert abs(np.mean(z / s) - 1) < 0.02  # unbiased


# --- Timing detector, alignment, clock fit ----------------------------------------------------


def pulse_on_taps(mu, n=9, sps=40):
    """End-to-end pulse arriving mu symbols late, sampled at whole symbols around tap 4."""
    taps = rrc_taps(sps, 0.25)
    pulse = np.convolve(taps, taps)
    pulse /= pulse.max()
    centre = pulse.size // 2
    t = np.arange(n) - n // 2 - mu
    return np.interp(t * sps, np.arange(pulse.size) - centre, pulse)


def test_pulse_slope():
    assert E.pulse_slope(40, 0.25) == pytest.approx(-0.94, abs=0.01)
    assert E.pulse_slope(4, 0.25) == pytest.approx(-0.94, abs=0.04)  # coarse difference


@pytest.mark.parametrize("mu", [-0.3, -0.1, 0.0, 0.05, 0.2, 0.3])
def test_peak_offset_measures_a_late_or_early_path(mu):
    """Unit gain to within 15% over +-0.3 symbol (16% high at 0.35), any carrier phase."""
    h = pulse_on_taps(mu) * np.exp(1j * 2.1)
    est = E.peak_offset(h, E.pulse_slope(40, 0.25))
    assert est == pytest.approx(mu, abs=0.15 * abs(mu) + 1e-3)


def test_peak_offset_ignores_power_shape():
    """The reason for the signed detector: a power-only (|h|^2) estimate is blind to small
    offsets, because the neighbouring taps have equal power either way."""
    h = pulse_on_taps(0.1)
    p = np.abs(h) ** 2
    assert abs(p[3] - p[5]) < 0.01 * p[4]
    assert E.peak_offset(h, E.pulse_slope(40, 0.25)) > 0.08


def test_leading_edge_and_shift():
    power = np.array([0, 0.001, 0, 0.5, 1.0, 0, 0, 0.3, 0])
    offsets = np.arange(-4, 5)
    assert E.leading_edge(power, offsets) == -1  # 0.5 is within 10 dB of 1.0
    shifted = E._shift(power, -1)
    assert shifted[4] == 0.5 and shifted[0] == 0
    assert np.array_equal(E._shift(E._shift(power, 2), -2)[2:], power[2:])


def test_line_fit():
    fit = E.LineFit()
    assert fit.slope() is None and fit.at(3) is None
    for x in range(10**6, 10**6 + 50):
        fit.add(x, 4.0004 * x + 12.5)
    assert fit.slope() == pytest.approx(4.0004, rel=1e-12)
    assert fit.at(10**6 + 100) == pytest.approx(4.0004 * (10**6 + 100) + 12.5, abs=1e-6)


# --- Decoder channel-state information ---------------------------------------------------------


def test_decoder_weights_symbols_by_their_noise_variance():
    """Every fourth frame is wiped out (as by a deep fade). Told which ones, the decoder
    ignores them and the code fills the gap; not told, it trusts garbage and fails."""
    m = mode(1200, "short")
    msg = message(200)
    tribits = transmit_bytes(msg, m)[m.preamble_symbols :]
    y = np.exp(1j * np.pi / 4 * tribits.astype(float))
    var = np.full(y.size, 0.01)
    frames = np.arange(y.size) // m.frame_symbols
    faded = frames % 4 == 1
    y[faded] = psk(int(faded.sum()))  # random symbols: nothing of the signal left
    var[faded] = 100.0
    payload = 8 * len(msg)
    told = DataDecoder(m)
    with_csi = np.concatenate([told.process(y, var), told.finish()])[:payload]
    blind = DataDecoder(m)
    without = np.concatenate([blind.process(y), blind.finish()])[:payload]
    ref = np.unpackbits(np.frombuffer(msg, np.uint8), bitorder="little")
    assert np.count_nonzero(with_csi != ref) == 0
    assert np.count_nonzero(without != ref) > 50


def test_decoder_rejects_mismatched_noise_variances():
    with pytest.raises(ValueError):
        DataDecoder(mode(2400, "short")).process(np.ones(48, complex), np.ones(47))


# --- The demodulator on known channels ---------------------------------------------------------


@pytest.mark.parametrize("delay_ms", [0.5, 2.0, 3.3, 5.0, 7.0])
def test_fixed_two_path_channels_are_equalised(delay_ms):
    m = mode(2400, "short")
    msg = message(300)
    out, rx = receive(two_paths(signal(m, msg), delay_ms))
    assert out == msg and rx.messages[0].eom_found


def test_m8_receiver_fails_where_the_equaliser_succeeds():
    """Why M10 exists: 2 ms of equal-power multipath at 2400 bps."""
    m = mode(2400, "short")
    msg = message(300)
    x = two_paths(signal(m, msg), 2.0)
    assert receive(x)[0] == msg
    assert bit_errors(receive(x, equalize=False)[0], msg) > 0.1 * 8 * len(msg)


@pytest.mark.parametrize("first_db", [-10.0, -6.0])
def test_strong_late_path(first_db):
    """The acquisition locks on to the strong late path; the equaliser must still find the
    weak early one and move tap 0 to it."""
    m = mode(1200, "short")
    msg = message(200)
    x = two_paths(signal(m, msg), 4.0, gains=(10 ** (first_db / 20), 1j))
    out, rx = receive(x)
    assert out == msg and len(rx.messages) == 1


@pytest.mark.parametrize("ppm", [-1500, 1500])
def test_clock_offset_on_multipath(ppm):
    m = mode(1200, "long")
    msg = message(400)
    out, rx = receive(two_paths(clock_offset(signal(m, msg), ppm), 2.0))
    assert out == msg
    assert rx.messages[0].clock_ppm == pytest.approx(ppm, abs=40)


@settings(max_examples=6, deadline=None)
@given(st.lists(st.integers(1, 9000), min_size=1, max_size=4))
def test_output_does_not_depend_on_chunking(chunks):
    """Identical bytes on a fading channel, and the same diagnostics. (The diagnostics only
    to within rounding: NumPy's vectorised maths can round the last bit differently
    depending on array length, and chunking changes how many observations are taken at
    once. Found by hypothesis on Python 3.11: SNR ...333 against ...334.)"""
    m = mode(600, "short")
    x = signal(m, message(60), tail=4000)
    y = WattersonChannel(ChannelSpec(2, 2.0, 1.0, 12.0), seed=3).process(x)
    whole, rx1 = receive(y)
    pieces, rx2 = receive(y, chunks)
    assert pieces == whole
    assert [(a.n_bytes, a.end_sample) for a in rx1.messages] == [
        (b.n_bytes, b.end_sample) for b in rx2.messages
    ]
    for a, b in zip(rx1.messages, rx2.messages, strict=True):
        assert a.snr_db == pytest.approx(b.snr_db, rel=1e-9)
        assert a.clock_ppm == pytest.approx(b.clock_ppm, rel=1e-9, abs=1e-6)


def test_message_end_is_located():
    """The next search starts where the transmission really ended, not where the
    demodulator (which runs a second ahead) happens to be."""
    m = mode(2400, "short")
    tri = transmit_bytes(message(100), m)
    x = modulate(tri)
    lead = 3000
    out, rx = receive(np.concatenate([np.zeros(lead, complex), x, np.zeros(20000, complex)]))
    assert rx.messages[0].eom_found
    true_end = lead + tri.size * 4  # (what follows is the transmit filter's ringing)
    assert abs(rx.messages[0].end_sample - true_end) <= 2 * 4


def test_noise_after_the_signal_is_not_decoded_and_loss_is_placed():
    """A transmission cut off mid-message: what was received is decoded, the loss is
    declared and placed near the cut.

    Cut at 60% of the transmitted symbols. The placement depends on where the cut falls:
    over 21 cuts between 55% and 65%, 18 were placed within 0.01 s and 3 between 0.29 and
    0.64 s late (a noise frame that scores as signal restarts the final run of bad frames
    the end is placed at; PLAN.md, known weaknesses)."""
    m = mode(1200, "long")
    msg = message(1500)
    x = signal(m, msg, tail=0)
    cut = 4000 + int(0.6 * transmit_bytes(msg, m).size) * 4
    rng = np.random.default_rng(2)
    noise = 0.02 * (rng.standard_normal(3 * FS) + 1j * rng.standard_normal(3 * FS))
    y = np.concatenate([x[:cut], noise])
    y[:cut] += 0.02 * (rng.standard_normal(cut) + 1j * rng.standard_normal(cut))
    out, rx = receive(y)
    assert len(rx.messages) == 1 and rx.messages[0].lost
    assert abs(rx.messages[0].end_sample - cut) < 0.3 * FS
    # Complete interleaver blocks are exact. The block cut short is decoded with erasures
    # (M9) and may hold errors; one block at 1200L is 720 bytes.
    block = 720
    assert len(out) < len(msg) and len(out) % block == 0
    assert out[: len(out) - block] == msg[: len(out) - block]


def test_a_fade_does_not_end_the_message():
    """Both paths silent for 0.4 s in the middle of the data: the message survives in one
    piece (the M8 receiver split transmissions at every fade)."""
    m = mode(300, "long")
    msg = message(200)
    x = two_paths(signal(m, msg), 2.0)
    middle = x.size // 2
    x[middle : middle + int(0.4 * FS)] = 0
    rng = np.random.default_rng(4)
    x += 0.01 * (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
    out, rx = receive(x)
    assert len(rx.messages) == 1 and rx.messages[0].eom_found
    assert bit_errors(out, msg) == 0


def test_75bps_rake_on_a_fixed_two_path_channel_at_low_snr():
    m = mode(75, "short")
    msg = message(30)
    x = two_paths(signal(m, msg), 5.0)
    rng = np.random.default_rng(5)
    noise_var = 0.16 * 10 ** (0 / 10) * FS / 3000  # 0 dB in 3 kHz
    x += np.sqrt(noise_var / 2) * (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
    out, rx = receive(x)
    assert out == msg and rx.messages[0].eom_found


# --- Table XVI (Appendix E simulator) ----------------------------------------------------------


def table_xvi_trial(row, seed, n_bytes=None):
    m = mode(row.rate, "zero" if row.rate == 4800 else "long")
    if row.fading:
        spec = ChannelSpec(2, row.multipath_ms, row.fading_bw_hz, row.snr_db)
    else:
        spec = ChannelSpec(1, 0.0, 0.0, row.snr_db)
    msg = message(n_bytes or max(60, min(3000, row.rate * 6 // 8)), seed)
    y = WattersonChannel(spec, seed=seed).process(signal(m, msg))
    out, rx = receive(y)
    return bit_errors(out, msg), 8 * len(msg), rx


FAST_ROWS = [T.TABLE_XVI[i] for i in (3, 5, 10)]  # 2400 2ms/1Hz, 2400 5ms/1Hz, 75 bps


def row_id(row):
    return f"{row.rate}-{row.multipath_ms}ms-{row.fading_bw_hz}Hz-{row.snr_db}dB"


@pytest.mark.parametrize("row", FAST_ROWS, ids=row_id)
def test_table_xvi_fading_rows_quick(row):
    errors, bits, rx = table_xvi_trial(row, seed=0)
    assert errors <= row.max_ber * bits and len(rx.messages) == 1 and rx.messages[0].eom_found


@pytest.mark.slow
@pytest.mark.parametrize("row", T.TABLE_XVI, ids=row_id)
def test_table_xvi(row):
    """Every row, three channel realisations, each message in one piece with its EOM.
    (A 6-s message measures BER only coarsely; docs/PERFORMANCE.md has longer runs.)"""
    errors = bits = 0
    for seed in range(3):
        e, b, rx = table_xvi_trial(row, seed)
        errors, bits = errors + e, bits + b
        assert len(rx.messages) == 1 and rx.messages[0].eom_found
    assert errors <= max(row.max_ber * bits, 0)


@pytest.mark.parametrize("chunk", [1024, 4096])
def test_back_to_back_short_preambles_in_small_chunks(chunk):
    """Fed in small pieces: the search must restart where the first message ended, or the
    0.6-s preamble of the next is already behind it."""
    a = modulate(transmit_bytes(b"first message", mode(2400, "short")))
    b = modulate(transmit_bytes(b"second", mode(600, "short")))
    x = np.concatenate([np.zeros(3000, complex), a, b, np.zeros(9600, complex)])
    out, rx = receive(x, [chunk])
    assert out == b"first messagesecond" and len(rx.messages) == 2


# --- The optional trace (S10) ------------------------------------------------------------


def test_the_trace_is_off_by_default_and_changes_nothing():
    m = mode(1200, "short")
    msg = message(300)
    x = two_paths(signal(m, msg), 2.0, gains=(1.0, 0.6j))
    plain, rx = receive(x)
    traced, rx_traced = receive(x, trace=True)
    assert plain == traced == msg
    assert rx.messages[0].trace == []
    assert len(rx_traced.messages[0].trace) > 10


def test_the_trace_records_what_the_loops_measured():
    """A transmitter 20 Hz high and 300 ppm fast, through two fixed paths: the trace's
    carrier and clock settle on those values, its units follow each other without gaps
    (two-slot units in the preamble, then frames), and the data symbols' estimates,
    descrambled, sit on the 8-PSK points."""
    m = mode(2400, "short")
    msg = message(1500)
    x = clock_offset(signal(m, msg), 300.0)
    x = frequency_offset(two_paths(x, 2.0, gains=(1.0, 0.5j)), 20.0, FS)
    out, rx = receive(x, trace=True)
    assert out == msg
    trace = rx.messages[0].trace
    starts = [u.symbol for u in trace]
    lengths = np.diff(starts)
    assert np.all(lengths > 0)
    data = [u for u in trace if not u.training]
    assert data and all(not u.training for u in trace[trace.index(data[0]) :])
    assert set(np.diff([u.symbol for u in data])) == {m.frame_symbols}
    # (The demodulator runs on into the silence after the message until it declares the
    # signal lost; the trace records those units too, with nothing in them.)
    signal_units = [u for u in data if u.snr_db > 10]
    assert len(signal_units) < len(data)
    late = signal_units[len(signal_units) // 2 :]
    carrier = T.CARRIER_HZ * (1 + 300e-6) + 20  # the fast clock raises the carrier too
    assert np.median([u.frequency_hz for u in late]) == pytest.approx(carrier, abs=0.1)
    assert np.median([u.clock_ppm for u in late]) == pytest.approx(300, abs=60)
    assert np.all(np.diff([u.time_s for u in trace]) > 0)
    z = np.concatenate([u.data for u in late])
    assert z.size == len(late) * m.unknown_per_frame
    nearest = np.exp(1j * np.pi / 4 * np.round(np.angle(z) / (np.pi / 4)))
    assert np.max(np.abs(z - nearest)) < 0.35
    # ... and they are the symbols sent, before scrambling.
    sent = transmit_bytes(msg, m)
    first = rx.messages[0].detection.data_start_symbol  # the trace's data phase starts here
    preamble = m.preamble_symbols  # the data phase follows it
    for u in late[:5]:
        k = np.arange(u.symbol - first, u.symbol - first + m.unknown_per_frame)
        expected = (sent[preamble + k].astype(int) - scrambler.data_sequence(k[0], k.size)) % 8
        assert np.array_equal(np.round(np.angle(u.data) / (np.pi / 4)).astype(int) % 8, expected)
    assert all(u.h.shape == (E.PRECURSOR_TAPS + E.POSTCURSOR_TAPS + 1,) for u in trace)


# --- Tracking through fades (M11) --------------------------------------------------------


def test_a_late_acquired_path_12_symbols_after_the_first():
    """5 ms is exactly 12 symbols, and the later path is the stronger, so the acquisition
    locks to it and the first path sits 12 symbols early. Through a long preamble, whose end
    repeats every 32 symbols (so that taps 32 apart cannot be told apart there), the
    receiver must move tap 0 to the first path and keep it there."""
    m = mode(1200, "long")
    msg = message(200)
    x = two_paths(signal(m, msg), 5.0, gains=(0.5, 1.0j), fractional=False)
    out, rx = receive(x)
    assert out == msg and len(rx.messages) == 1


@pytest.mark.parametrize("rate, delay_ms", [(2400, 2.0), (1200, 5.0)])
def test_paths_that_swap_in_half_a_second(rate, delay_ms):
    """The first path 15 dB below the second through the preamble, so the receiver aligns
    tap 0 on the second; then, half a second into the data, the two swap power in 0.5 s.
    The first path, now the strong one, is 4.8 or 12 symbols before tap 0, outside the
    equaliser's span (-A), so its decisions fail, and a delay profile fitted to them never
    shows the path: the receiver lost the message (the sweep's 1200 bps, 0.5 Hz point lost
    a whole message this way). The profile from the probes alone shows it, and tap 0 moves
    (and stays: the decision-fitted profile, slower, would move it back)."""
    m = mode(rate, "long")
    msg = message(rate * 10 // 8)
    x = signal(m, msg)
    t = np.arange(x.size) / FS
    t0 = 4000 / FS + m.preamble_symbols / T.SYMBOL_RATE + 0.5
    ramp = np.clip((t - t0) / 0.5, 0, 1)
    first_db, second_db = -15 + 15 * ramp, -15 * ramp
    late = np.roll(x, int(delay_ms * 1e-3 * FS))
    y = 10 ** (first_db / 20) * x + 10 ** (second_db / 20) * 1j * late
    y = add_awgn(y, 20.0, FS, np.random.default_rng(5), signal_power=0.16)
    out, rx = receive(y)
    assert out == msg and len(rx.messages) == 1


@pytest.mark.slow
def test_75bps_on_the_table_xvi_channel_at_its_snr():
    """75L on 2 paths, 5 ms, 5 Hz at 2 dB, the Table XVI point: all 16 channels decode
    without error. Before M11 checked counts against the next segments, 3 were lost to a
    count misread in a fade (docs/PERFORMANCE.md, M11)."""
    m = mode(75, "long")
    for seed in range(16):
        msg = message(60, seed)
        y = WattersonChannel(ChannelSpec(2, 5.0, 5.0, 2.0), seed=3000 + seed).process(
            signal(m, msg, tail=9600)
        )
        out, _ = receive(y)
        assert bit_errors(out, msg) == 0, seed


@pytest.mark.slow
def test_75bps_a_minute_long_message_at_the_table_xvi_point():
    """75L for a minute on 2 paths, 5 ms, 5 Hz at 2 dB (the conformance report's first long
    run). This channel let the timing loop's period run away 48 s in: 0.18% off within two
    seconds, lock lost, the last 112 bytes missing. At 75 bps the loops no longer learn from
    units that are mostly noise, and the period adapts slowly (docs/PERFORMANCE.md, M13)."""
    m = mode(75, "long")
    msg = bytes(np.random.default_rng(10_000 * 10 + 10).integers(0, 256, 562, dtype=np.uint8))
    x = signal(m, msg, tail=9600)
    y = WattersonChannel(ChannelSpec(2, 5.0, 5.0, 2.0), seed=10).process(x)
    out, rx = receive(y, [9600])
    assert out == msg and len(rx.messages) == 1 and rx.messages[0].eom_found


def _set_profiles(x, m):
    """Run the receiver on ``x``, recording the 75-bps set profile at every data unit."""
    seen = []
    original = E.EqualizingDemodulator._update_set_profile

    def spy(self, k0, k1):
        original(self, k0, k1)
        seen.append((self.align, self.set_profile.copy(), self.wide.copy()))

    E.EqualizingDemodulator._update_set_profile = spy
    try:
        out, rx = receive(x)
    finally:
        E.EqualizingDemodulator._update_set_profile = original
    return out, seen


def test_the_set_profile_sees_the_paths_without_the_equaliser():
    """75 bps (M13): correlating each set with the four patterns at every tap of the wide
    span is a delay profile that needs no decisions. Two fixed paths 12 symbols apart show
    as the two strongest taps, well above the floor of sets read at a wrong delay."""
    m = mode(75, "short")
    msg = message(30)
    x = two_paths(signal(m, msg), 5.0, gains=(1.0, 0.7j), fractional=False)
    out, seen = _set_profiles(x, m)
    assert out == msg and seen
    align, profile, wide = seen[-1]
    peaks = sorted(wide[np.argsort(profile)[-2:]].tolist())
    assert peaks[1] - peaks[0] == 12
    floor = np.median(profile)
    assert profile.max() > 10 * floor  # more than 10 dB above the floor


@pytest.mark.slow
@pytest.mark.parametrize(
    "seed, failure",
    [
        (103, "alignment jumped 15 symbols in a fade, to a path that was not there"),
        (170, "a count misread in a fade put the data phase a segment late"),
    ],
)
def test_75bps_minute_long_messages_that_used_to_lose_lock(seed, failure):
    """75L for a minute at the Table XVI point (2 dB, 5 ms, 5 Hz): two of the three messages
    the conformance run lost after the period fix (docs/PERFORMANCE.md, M13)."""
    m = mode(75, "long")
    msg = bytes(np.random.default_rng(100_000 + seed).integers(0, 256, 562, dtype=np.uint8))
    y = WattersonChannel(ChannelSpec(2, 5.0, 5.0, 2.0), seed=seed).process(
        signal(m, msg, tail=9600)
    )
    out, rx = receive(y, [9600])
    assert out == msg and len(rx.messages) == 1 and rx.messages[0].eom_found, failure


@pytest.mark.slow
def test_a_probe_sidelobe_does_not_move_tap_0():
    """300L for a minute on two 5-ms paths (5 Hz, 7 dB), the channel and message of a check
    on fresh seeds (tools/sweep.py's seeding). The probe repeats every four frames, so its
    correlation with the neighbouring frames' probes put a steady false path 16 symbols
    before tap 0, at about -10 dB, while the paths were strong; taking the probe profile's
    leading edge moved tap 0 onto it and lost half the message. Only power before the
    equaliser's span, in a large share, may move tap 0."""
    m = mode(300, "long")
    seed = 977_155_004
    msg = bytes(np.random.default_rng(seed).integers(0, 256, 2048, dtype=np.uint8))
    x = signal(m, msg, lead=4000, tail=9600)
    y = WattersonChannel(ChannelSpec(2, 5.0, 5.0, 7.0), seed=seed + 7).process(x)
    out, rx = receive(y, [9600])
    assert out == msg and len(rx.messages) == 1
