"""Tests for late entry: joining a transmission without its preamble (S5, INT-029).

Clean or mildly impaired signals cut well into their data phase, so that the preamble and
the first blocks are gone; the measurements over channels are tools/late_entry_check.py's."""

import numpy as np
import pytest

from m110c.bits import bytes_to_bits
from m110c.framing import AsyncFormat, async_encode
from m110c.modulate import modulate
from m110c.rx import lateentry as L
from m110c.rx.decode import DataDecoder
from m110c.rx.receiver import Receiver, _whole_bytes_per_block
from m110c.spec import all_modes, mode
from m110c.tx import transmit, transmit_bytes

FS = 9600
TEXT = b"Late entry: joining a transmission already in progress. " * 60


def stream(m, message=TEXT, offset_hz=37.0, cut_symbols=900, async_format=None):
    """The transmission of ``message`` in ``m``, cut ``cut_symbols`` into its data phase
    (plus an odd number of samples), with a carrier offset and a little noise."""
    bits = async_encode(message, async_format) if async_format else bytes_to_bits(message)
    x = np.concatenate([modulate(transmit(bits, m)), np.zeros(FS, complex)])
    x *= np.exp(2j * np.pi * offset_hz * np.arange(x.size) / FS)
    rng = np.random.default_rng(1)
    x += 0.02 * (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
    return x[(m.preamble_symbols + cut_symbols) * 4 + 3 :]


def receive(x, **kwargs):
    rx = Receiver(**kwargs)
    out = b"".join(rx.process(x[i : i + 4800]) for i in range(0, x.size, 4800))
    return out + rx.finish(), rx


# --- The search ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "m, family",
    [(mode(2400, "short"), "48"), (mode(600, "long"), "40"), (mode(75, "short"), "75")],
    ids=str,
)
def test_the_search_finds_the_family_and_the_carrier(m, family):
    search = L.LateEntrySearch()
    x = stream(m, offset_hz=-123.0)
    lock = search.process(x[: 2 * FS])
    assert lock is not None and lock.family == family and lock.metric > 0.8
    assert lock.carrier_hz == pytest.approx(1800 - 123, abs=1.0)


def test_noise_alone_stays_below_the_threshold():
    rng = np.random.default_rng(2)
    search = L.LateEntrySearch()
    assert search.process(rng.standard_normal(3 * FS) + 1j * rng.standard_normal(3 * FS)) is None
    assert 0.05 < search.last < L.THRESHOLD


def test_the_lock_puts_symbol_0_on_a_whole_probe_period():
    """Symbol 0 must have a data-phase index that is a multiple of the probe pattern's
    period, so that the demodulator's scrambler phase is right from the start."""
    m = mode(2400, "short")
    x = stream(m, offset_hz=0.0, cut_symbols=1000)
    lock = L.LateEntrySearch().process(x[: 2 * FS])
    # Symbol k of the cut stream starts at sample 4k - 3 (the cut was 3 samples past a
    # symbol), and its data-phase index is 1000 + k.
    k = (lock.segment_start + 3) / 4
    assert k == pytest.approx(round(k), abs=0.3)
    assert (1000 + round(k)) % 480 == 0


def test_a_lock_before_the_stream_keeps_the_time_base():
    """The lock puts symbol 0 on a whole probe period, which can be before the first sample
    the search holds. The demodulator must still be fed from symbol 0 (zeros before the
    stream), or its time base slips by the missing samples and it starts with the path
    several taps from tap 0 (seen as a 10-symbol error, and a lost message at low SNR)."""
    m = mode(1200, "short")
    x = stream(m, offset_hz=0.0, cut_symbols=160 * 6 + 7)  # 7 symbols past a probe period
    search = L.LateEntrySearch()
    lock = search.process(x[: 2 * FS])
    assert lock.segment_start < 0
    samples = search.samples_from(int(np.floor(lock.segment_start)) - 8)
    assert samples.size == 2 * FS - int(np.floor(lock.segment_start)) + 8
    attempt = L.LateEntry(lock)
    attempt.process(search.samples_from(attempt.start_index))
    attempt.process(x[2 * FS : 4 * FS])
    assert attempt.demod.anchored and attempt.demod.align == 0
    assert attempt.mode == m


# --- The attempt: boundary, mode, verification ---------------------------------------------


@pytest.mark.parametrize(
    "m",
    [mode(2400, "short"), mode(1200, "zero"), mode(300, "short"), mode(4800), mode(75, "short")],
    ids=str,
)
def test_an_attempt_finds_the_block_boundary_and_the_mode(m):
    x = stream(m)
    search = L.LateEntrySearch()
    lock = None
    for i in range(0, x.size, 4800):
        lock = search.process(x[i : i + 4800])
        if lock:
            break
    attempt = L.LateEntry(lock)
    attempt.process(search.samples_from(attempt.start_index))
    fed = i + 4800
    while not attempt.mode and fed < x.size:
        attempt.process(x[fed : fed + 4800])
        fed += 4800
    assert attempt.mode == m and not attempt.failed
    # The block starts where a block starts: data-phase index a multiple of 1440.
    k = (lock.segment_start + 3) / 4 + attempt.block_start
    assert (900 + round(k)) % m.block_symbols == 0


def test_verification_rejects_a_false_boundary():
    """Decoding one block from a point that is not a block start fits no better than no
    interleaver at all."""
    m = mode(2400, "short")
    data = transmit_bytes(bytes(range(256)) * 4, m)[m.preamble_symbols :]
    z = np.exp(1j * np.pi / 4 * data.astype(float))
    accepted, margin = L.verify(m, z[: m.block_symbols], np.full(m.block_symbols, 0.01))
    assert accepted == m and margin > 0.05
    off = 15 * m.frame_symbols  # half a block late
    accepted, margin = L.verify(m, z[off : off + m.block_symbols], np.full(m.block_symbols, 0.01))
    assert accepted is None and abs(margin) < L.DECIDE_MARGIN


def test_decoding_can_start_in_an_unknown_encoder_state():
    """From the second block on, the encoder is in whatever state the first left it in."""
    m = mode(1200, "short")
    data = transmit_bytes(bytes(range(200)), m)[m.preamble_symbols :]
    z = np.exp(1j * np.pi / 4 * data.astype(float))[m.block_symbols :]  # from the 2nd block
    decoder = DataDecoder(m, start_state=None)
    bits = decoder.process(z)
    assert decoder._viterbi.fit > 0.99  # (finish() resets the decoder)
    bits = np.concatenate([bits, decoder.finish()])
    expected = bytes_to_bits(bytes(range(200)))[720:]  # a block holds 720 user bits
    assert np.array_equal(bits[: expected.size], expected)


# --- Through the receiver ------------------------------------------------------------------


@pytest.mark.parametrize("m", [mode(2400, "short"), mode(600, "zero"), mode(1200, "long")], ids=str)
def test_the_receiver_joins_and_delivers_the_rest_of_the_message(m):
    """A few blocks of the mode: the late receiver needs the boundary block and one more to
    verify it (long interleave: 4.8 s each)."""
    text = TEXT[: m.rate * (13 if m.interleave == "long" else 4) // 8]
    out, rx = receive(stream(m, text), late_entry=True)
    msg = rx.messages[0]
    assert msg.late_entry and msg.mode == m and msg.eom_found
    assert len(out) > 40 and text.endswith(out)


def test_75_bps_with_asynchronous_framing():
    """At 75S a block holds 45 user bits, so the synchronous byte boundaries are lost on a
    late entry; asynchronous characters find their own."""
    fmt = AsyncFormat.parse("8N1")
    m = mode(75, "short")
    out, rx = receive(stream(m, TEXT[:200], async_format=fmt), late_entry=True, async_format=fmt)
    assert rx.messages[0].late_entry and rx.messages[0].mode == m
    assert len(out) > 40 and out[-40:] == TEXT[160:200]


def test_a_late_entry_without_async_framing_at_75s_says_the_bytes_may_be_misaligned():
    out, rx = receive(stream(mode(75, "short"), TEXT[:200]), late_entry=True)
    assert any("byte boundaries are unknown" in n for n in rx.messages[0].notes)


def test_a_preamble_wins_and_late_entry_is_off_by_default():
    m = mode(1200, "short")
    x = np.concatenate(
        [np.zeros(3000, complex), modulate(transmit_bytes(TEXT[:300], m)), np.zeros(FS, complex)]
    )
    on, rx_on = receive(x, late_entry=True)
    off, rx_off = receive(x)
    assert on == off == TEXT[:300]
    assert not rx_on.messages[0].late_entry and len(rx_on.messages) == 1
    late, _ = receive(stream(m))
    assert late == b""  # without late_entry, a stream with no preamble yields nothing


def test_noise_alone_delivers_nothing():
    rng = np.random.default_rng(4)
    y = 0.3 * (rng.standard_normal(20 * FS) + 1j * rng.standard_normal(20 * FS))
    out, rx = receive(y, late_entry=True)
    assert out == b"" and rx.messages == []


def test_which_modes_keep_byte_alignment_at_a_block_boundary():
    """User bits per block: 300, 150 and 75 bps with zero or short interleave carry 180, 90
    and 45, not whole bytes; every other mode does."""
    misaligned = {str(m) for m in all_modes() if not _whole_bytes_per_block(m)}
    assert misaligned == {"300Z", "300S", "150Z", "150S", "75Z", "75S"}


# --- The late demodulator ------------------------------------------------------------------


def test_the_late_demodulator_reads_the_d_probes_and_takes_the_mode_when_told():
    m = mode(1200, "short")
    x = stream(m, offset_hz=0.0)
    lock = L.LateEntrySearch().process(x[: 2 * FS])
    attempt = L.LateEntry(lock)
    demod = attempt.demod
    demod.process(x[max(attempt.start_index, 0) :][: 3 * FS])
    d_frames = [(k, d) for k, d, margin in demod.probe_readings if d is not None and margin > 3]
    # D1 = 6 then D2 = 5 (1200S), in consecutive frames, once per block (36 frames)
    pairs = [(a, b) for a, b in zip(d_frames, d_frames[1:], strict=False) if b[0] - a[0] == 40]
    assert pairs and all((a[1], b[1]) == m.d1_d2 for a, b in pairs)
    assert len({(a[0] // 40) % 36 for a, _ in pairs}) == 1
    demod.set_block_start(pairs[0][1][0] + 40, mode(600, "short"))
    assert demod.block_known and demod.mode == mode(600, "short")


def test_a_doubtful_probe_holds_the_channel_estimate_for_two_frames_at_most():
    """A probe decided by a small margin (a fade) keeps the previous channel estimate rather
    than fitting one to a possibly wrong D pattern; but when every probe is doubtful (the
    signal gone), the estimate must still follow, so a hold lasts two frames at most."""
    m = mode(1200, "short")
    x = stream(m, offset_hz=0.0)
    lock = L.LateEntrySearch().process(x[: 2 * FS])
    attempt = L.LateEntry(lock)
    demod = attempt.demod
    estimated, held = [], []
    estimate = demod._estimate

    def counting(k1):
        estimated.append(k1)
        return estimate(k1)

    demod._estimate = counting
    signal = x[max(attempt.start_index, 0) :][: 2 * FS]
    rng = np.random.default_rng(5)
    noise = rng.standard_normal(FS) + 1j * rng.standard_normal(FS)
    for chunk in (signal, noise):
        for i in range(0, chunk.size, 480):
            demod.process(chunk[i : i + 480])
            held.append(demod._held_frames)
    assert max(held) == 2  # noise: doubtful probes, held, then estimated again
    frames = [k for k in estimated if k > 480]
    gaps = np.diff(frames) // m.frame_symbols
    assert gaps.max() == 3  # never more than two frames held in a row


@pytest.mark.parametrize("m", [mode(2400, "short"), mode(1200, "short")], ids=str)
def test_the_first_late_estimate_keeps_no_floor_of_noise_taps(m):
    """Without a preamble the first channel estimate comes from the probes alone, so every
    tap carries estimation noise; taps that do not clear it must not enter the delay profile
    (they would make the channel estimates of the next seconds noisier). One path, 7 dB."""
    x = stream(m, offset_hz=0.0)
    rng = np.random.default_rng(3)
    x = x + 0.3 * (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
    attempt = L.LateEntry(L.LateEntrySearch().process(x[: 2 * FS]))
    demod, profiles = attempt.demod, []
    anchor = demod._anchor_late

    def recording():
        anchor()
        profiles.append(demod.profile.copy())

    demod._anchor_late = recording
    demod.process(x[max(attempt.start_index, 0) :][: FS // 2])
    far = np.abs(demod.wide) > 3  # more than 3 symbols from the one path
    assert profiles and np.count_nonzero(profiles[0][far]) <= 3  # of 34: chance only
    assert profiles[0][~far].sum() > 0.9 * profiles[0].sum()
