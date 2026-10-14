"""Tests for preamble acquisition (M8)."""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c import scrambler, symbols
from m110c.channel.awgn import add_awgn
from m110c.channel.impairments import fractional_delay, frequency_offset
from m110c.channel.watterson import ChannelSpec, WattersonChannel
from m110c.modulate import modulate
from m110c.rx.acquire import PreambleSearch, read_segment, slot_waveforms
from m110c.spec import mode
from m110c.spec import tables as T
from m110c.tx import transmit_bytes


def signal(m, lead=2345, offset_hz=0.0, snr_db=None, seed=0, sample_rate=9600, tail=3000):
    x = modulate(transmit_bytes(b"acquisition", m), sample_rate=sample_rate)
    x = frequency_offset(x, offset_hz, sample_rate)
    x = np.concatenate([np.zeros(lead, complex), x, np.zeros(tail, complex)])
    if snr_db is not None:
        x = add_awgn(x, snr_db, sample_rate, np.random.default_rng(seed), signal_power=0.16)
    return x


def first_detection(x, **kwargs):
    search = PreambleSearch(**kwargs)
    return search.process(x) or search.flush()


# --- Building blocks ----------------------------------------------------------------------


def test_slot_waveforms_are_the_transmitted_slots():
    """Slot v of any segment, as the transmitter sends it, starts the waveform library."""
    w = slot_waveforms(4)
    tri = scrambler.scramble(np.array(T.TABLE_XIII[5], np.uint8), scrambler.sync_sequence(0, 32))
    x = modulate(tri, carrier_hz=0) / 0.8  # modulator symbol amplitude at 4 sps
    assert np.allclose(x[: w.shape[1]], w[5], atol=1e-9)


def test_prefilter_passes_signal_band_and_offset():
    h = PreambleSearch().prefilter
    response = lambda f: abs(np.sum(h * np.exp(-2j * np.pi * f * np.arange(h.size) / 9600)))  # noqa: E731
    assert response(0) == pytest.approx(1, abs=1e-6)
    assert response(1900) > 0.95  # signal edge at the largest offset: 1500 + 400 Hz
    assert response(2500) < 0.01  # where a real input's mirror image can begin


# --- Detection --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "m", [mode(2400, "short"), mode(75, "long"), mode(4800), mode(600, "zero")], ids=str
)
def test_detects_mode_timing_and_count(m):
    d = first_detection(signal(m))
    assert d is not None
    expected = m if m.interleave != "zero" else mode(m.rate, "short")  # INT-012
    assert d.mode == expected
    assert abs(d.segment_start - 2345) < 0.1  # samples
    assert d.count == m.preamble_count_start  # found on the first segment
    assert d.prefix_errors == 0


@pytest.mark.parametrize("offset_hz", [-400, -237.5, -12, 0, 61, 333.3, 400])
def test_carrier_offset_is_estimated_within_half_a_hertz(offset_hz):
    d = first_detection(signal(mode(1200, "short"), offset_hz=offset_hz))
    assert d is not None
    assert abs(d.carrier_hz - (1800 + offset_hz)) < 0.5


def test_offset_beyond_search_range_is_not_found():
    assert first_detection(signal(mode(1200, "short"), offset_hz=700)) is None


def test_late_entry_reads_the_remaining_count():
    """Starting inside a long preamble (as in a real recording): the first full segment
    found must report how many segments remain."""
    m = mode(2400, "long")
    x = modulate(transmit_bytes(b"late", m))
    cut = 10 * 480 * 4 + 777  # 10 segments and a bit into the preamble
    d = first_detection(np.concatenate([x[cut:], np.zeros(2000, complex)]))
    assert d is not None
    assert d.count == 23 - 11  # segment 11 is the first complete one
    assert abs(d.segment_start - (11 * 480 * 4 - cut)) < 0.1


def test_zero_interleave_option():
    d = first_detection(signal(mode(300, "zero")), zero_interleave=True)
    assert d.mode == mode(300, "zero")


@pytest.mark.parametrize("sample_rate", [9600, 48000])
@pytest.mark.parametrize("snr_db", [10, 0, -6])
def test_detects_in_noise(sample_rate, snr_db):
    """The in-band prefilter makes the metric independent of sample rate (measured: rho
    0.597 at +10 dB at both 9600 and 48000 S/s)."""
    m = mode(150, "short")
    d = first_detection(signal(m, snr_db=snr_db, sample_rate=sample_rate), sample_rate=sample_rate)
    assert d is not None and d.mode == m
    lead = 2345  # samples of silence before the signal (at any rate)
    assert abs(d.segment_start - lead) < 0.25 * sample_rate / 2400  # a quarter symbol


def test_no_false_alarms_on_noise():
    """60 s of noise alone: nothing may be detected (measured noise-only rho <= 0.046
    against a threshold of 0.08, and the prefix/mode/count checks behind it)."""
    rng = np.random.default_rng(3)
    noise = 0.3 * (rng.standard_normal(9600 * 60) + 1j * rng.standard_normal(9600 * 60))
    assert first_detection(noise) is None


def test_silence_produces_no_candidates():
    """Digital silence once made rho a ratio of rounding errors (0/1e-30)."""
    s = PreambleSearch()
    assert s.process(np.zeros(9600 * 3, complex)) is None
    assert s._next > 9600 * 2  # it moved through the silence instead of crawling


def test_unimplemented_designator_is_ignored():
    """INT-019: a preamble announcing 2400 bps digital voice (D1, D2 = 7, 7) is not a mode
    we implement; the search must keep looking rather than lock onto it."""
    m = mode(2400, "short")
    tribits = []
    for count in (2, 1, 0):
        cs = [*T.PREAMBLE_FIXED_PREFIX, 7, 7, *symbols.count_symbols(count), 0]
        tribits += [t for c in cs for t in T.TABLE_XIII[c]]
    tri = scrambler.scramble(np.array(tribits, np.uint8), scrambler.sync_sequence(0, len(tribits)))
    voice = np.concatenate([np.zeros(1000, complex), modulate(tri), np.zeros(3000, complex)])
    assert first_detection(voice) is None
    # ... and a real preamble right after it is still found.
    both = np.concatenate([voice, signal(m, lead=500)])
    d = first_detection(both)
    assert d is not None and d.mode == m


def test_int_019_voice_preamble_is_found_when_asked_for():
    """With voice enabled, the same preamble is a 2400V detection, and --zero-interleave
    does not turn it into a zero-interleave mode (voice is short interleave only)."""
    v = mode(2400, "short", voice=True)
    for zero in (False, True):
        s = PreambleSearch(voice=True, zero_interleave=zero)
        d = s.process(signal(v)) or s.flush()
        assert d is not None and d.mode == v and (d.d1, d.d2) == (7, 7) and d.count == 2


def test_int_019_a_voice_count_is_checked_against_the_next_segment():
    """A misread count (1 where the next segment says 1 too) is rejected for voice as for
    data, and the preamble is taken from the segment after it."""
    tribits = []
    for count in (1, 1, 0):
        cs = [*T.PREAMBLE_FIXED_PREFIX, 7, 7, *symbols.count_symbols(count), 0]
        tribits += [t for c in cs for t in T.TABLE_XIII[c]]
    tri = scrambler.scramble(np.array(tribits, np.uint8), scrambler.sync_sequence(0, len(tribits)))
    lead, segment = 1000, 480 * 4
    x = np.concatenate([np.zeros(lead, complex), modulate(tri), np.zeros(3000, complex)])
    d = first_detection(x, voice=True)
    assert d is not None and d.count == 1
    assert abs(d.segment_start - (lead + segment)) < 4


@settings(max_examples=10, deadline=None)
@given(st.lists(st.integers(1, 9000), min_size=1, max_size=5))
def test_detection_does_not_depend_on_chunking(chunks):
    x = signal(mode(600, "long"), offset_hz=123)
    reference = first_detection(x)
    s, found, i = PreambleSearch(), None, 0
    for size in itertools.cycle(chunks):
        if i >= x.size or found is not None:
            break
        found = s.process(x[i : i + size])
        i += size
    found = found or s.flush()
    assert found is not None
    assert found.segment_start == pytest.approx(reference.segment_start, abs=1e-6)
    assert found.carrier_hz == pytest.approx(reference.carrier_hz, abs=1e-6)


@pytest.mark.parametrize("delay", [0.25, 0.5, 0.71])
def test_timing_is_estimated_between_samples(delay):
    """A signal arriving between two samples: the parabolic peak fit must find the
    fraction (integer-only timing would be up to half a sample, an eighth of a symbol, off)."""
    m = mode(1200, "short")
    x = fractional_delay(modulate(transmit_bytes(b"fraction", m)), delay)
    x = np.concatenate([np.zeros(2000, complex), x, np.zeros(3000, complex)])
    d = first_detection(x)
    assert d is not None
    assert abs(d.segment_start - (2000 + delay)) < 0.1


def test_prefix_and_mode_checks_reject_noise_on_their_own():
    """Defence in depth: with the threshold lowered to 0.03, noise produces many candidates.
    The checks after the threshold (prefix read-back, valid D1/D2, valid count) must reject
    every one of them."""
    rng = np.random.default_rng(5)
    noise = 0.3 * (rng.standard_normal(9600 * 10) + 1j * rng.standard_normal(9600 * 10))
    search = PreambleSearch(threshold=0.03)
    candidates = []
    original = search._refine_and_read

    def spy(peak, i_bin):
        candidates.append(peak)
        return original(peak, i_bin)

    search._refine_and_read = spy
    assert search.process(noise) is None and search.flush() is None
    assert len(candidates) > 100  # the checks really were exercised


def test_corrupted_prefix_is_rejected():
    """The prefix read-back on its own: a preamble whose fixed prefix has two wrong slots
    (but valid D1, D2 and counts) must not be accepted."""
    m = mode(2400, "short")
    tribits = []
    for count in (2, 1, 0):
        prefix = list(T.PREAMBLE_FIXED_PREFIX)
        prefix[2], prefix[5] = 7, 6  # two of the nine fixed slots replaced
        cs = [*prefix, *m.d1_d2, *symbols.count_symbols(count), 0]
        tribits += [t for c in cs for t in T.TABLE_XIII[c]]
    tri = scrambler.scramble(np.array(tribits, np.uint8), scrambler.sync_sequence(0, len(tribits)))
    x = np.concatenate([np.zeros(1000, complex), modulate(tri), np.zeros(3000, complex)])
    search = PreambleSearch(threshold=0.03)  # low enough that the candidate reaches the checks
    assert search.process(x) is None and search.flush() is None


def preamble_75l(counts, bad_prefix=(), silent=(), interleave="long"):
    """A 75-bps preamble whose segments carry ``counts``, with two prefix slots wrong in the
    segments listed in ``bad_prefix`` and the segments in ``silent`` not sent at all."""
    m = mode(75, interleave)
    tribits = []
    for i, count in enumerate(counts):
        prefix = list(T.PREAMBLE_FIXED_PREFIX)
        if i in bad_prefix:
            prefix[2], prefix[5] = 7, 6
        cs = [*prefix, *m.d1_d2, *symbols.count_symbols(count), 0]
        tribits += [t for c in cs for t in T.TABLE_XIII[c]]
    tri = scrambler.scramble(np.array(tribits, np.uint8), scrambler.sync_sequence(0, len(tribits)))
    x = modulate(tri)
    for i in silent:
        x[i * 480 * 4 : (i + 1) * 480 * 4] = 0
    return np.concatenate([np.zeros(1000, complex), x, np.zeros(3000, complex)])


@pytest.mark.parametrize(
    "case, expected_segment",
    [
        ({}, 1),
        # The next segment is unreadable: the one after decides.
        ({"silent": (1,)}, 2),
        # The next segment's prefix has two slots wrong (enough to reject it as a detection
        # of its own), but its count still contradicts.
        ({"bad_prefix": (1,)}, 2),
        ({"bad_prefix": (1,), "silent": (2,)}, 3),
    ],
    ids=["next", "after an unreadable one", "with a damaged prefix", "only the damaged one"],
)
def test_a_count_contradicted_by_the_next_segments_is_rejected(case, expected_segment):
    """A fade can turn the count slots into another valid count; the next segments must
    count down from it. Here the first segment of a 75L preamble claims 18 while the rest
    count down from 22, so it is rejected and a later, confirmed segment accepted."""
    d = first_detection(preamble_75l((18, *range(22, -1, -1)), **case))
    assert d is not None and d.count == 23 - expected_segment
    assert abs(d.segment_start - (1000 + expected_segment * 480 * 4)) < 0.1


@pytest.mark.parametrize(
    "counts, silent",
    [
        (range(23, -1, -1), (1,)),  # confirmed two segments on: 21 = 23 - 2
        ((23, 22, 5, *range(20, -1, -1)), ()),  # the first readable segment decides
    ],
    ids=["two on", "first decides"],
)
def test_a_confirmed_count_stands(counts, silent):
    """The 75L count 23, at the start of the preamble, with later segments as listed."""
    d = first_detection(preamble_75l(counts, silent=silent))
    assert d is not None and d.count == 23 and abs(d.segment_start - 1000) < 0.1


def test_an_unconfirmed_count_waits_for_a_confirmed_one():
    """Nothing readable after the first segment: its count is not taken on trust while
    later segments can still check one (M13: at 75 bps a count misread in a fade, with the
    next segments faded too, put the data phase a whole segment late). The first segment
    after the gap, confirmed by the next, is used instead."""
    d = first_detection(preamble_75l(range(23, -1, -1), silent=(1, 2)))
    assert d is not None and d.count == 20 and abs(d.segment_start - (1000 + 3 * 480 * 4)) < 0.1


def test_a_count_with_no_segments_left_to_check_it_stands():
    """A short preamble's first segment (count 2) with the other two unreadable: nothing
    could ever confirm it, so it stands."""
    d = first_detection(preamble_75l((2, 1, 0), silent=(1, 2), interleave="short"))
    assert d is not None and d.count == 2 and abs(d.segment_start - 1000) < 0.1


def preamble_of(segments, silent=()):
    """A preamble whose segments carry the (D1, D2, count) listed, with the segments in
    ``silent`` not sent."""
    tribits = []
    for d1, d2, count in segments:
        cs = [*T.PREAMBLE_FIXED_PREFIX, d1, d2, *symbols.count_symbols(count), 0]
        tribits += [t for c in cs for t in T.TABLE_XIII[c]]
    tri = scrambler.scramble(np.array(tribits, np.uint8), scrambler.sync_sequence(0, len(tribits)))
    x = modulate(tri)
    for i in silent:
        x[i * 480 * 4 : (i + 1) * 480 * 4] = 0
    return np.concatenate([np.zeros(1000, complex), x, np.zeros(3000, complex)])


S1200, S75 = mode(1200, "short"), mode(75, "short")
MISREAD = (*S75.d1_d2, 2)  # one D1/D2 symbol changed: 1200S (6, 5) read as 75S (7, 5)


def test_a_misread_mode_is_outvoted_by_the_next_segments():
    """A 1200S preamble whose first segment reads as 75S (a fade changed one symbol, as in
    the sweep's interleave|1200S|2ms|5Hz|14dB). Its count is right, and both later
    segments, with counts that fit it, say 1200S: the majority mode wins, from the first
    segment. (Before the vote, segments of another mode were skipped, and with nothing left
    to confirm a count of 2 the 75S reading stood.)"""
    d = first_detection(preamble_of([MISREAD, (*S1200.d1_d2, 1), (*S1200.d1_d2, 0)]))
    assert d is not None and d.mode == S1200 and d.count == 2
    assert abs(d.segment_start - 1000) < 0.1


def test_a_tied_vote_waits_for_the_next_segment():
    """One later segment for each mode reading (the other is silent): no majority, so the
    first segment is not used; the next one, 1200S with nothing left to contradict it,
    is."""
    d = first_detection(preamble_of([MISREAD, (*S1200.d1_d2, 1), (*S1200.d1_d2, 0)], silent=(2,)))
    assert d is not None and d.mode == S1200 and d.count == 1
    assert abs(d.segment_start - (1000 + 480 * 4)) < 0.1


def test_one_misread_follower_does_not_outvote_a_confirmed_mode():
    """The reverse: the first segment is right and the next misread. The segment after
    agrees with the first, which stands."""
    d = first_detection(preamble_of([(*S1200.d1_d2, 2), MISREAD[:2] + (1,), (*S1200.d1_d2, 0)]))
    assert d is not None and d.mode == S1200 and d.count == 2
    assert abs(d.segment_start - 1000) < 0.1


def test_another_mode_with_another_count_is_not_a_vote():
    """A following segment of another mode whose count does not fit belongs to another
    transmission: it is ignored, as before (here leaving the count of 2 unconfirmed, which
    stands, as nothing later could check it)."""
    d = first_detection(preamble_of([(*S1200.d1_d2, 2), (*S75.d1_d2, 2), (*S75.d1_d2, 2)]))
    assert d is not None and d.mode == S1200 and d.count == 2
    assert abs(d.segment_start - 1000) < 0.1


# --- Fading (M11) -------------------------------------------------------------------------


def test_slots_are_read_through_a_wandering_phase():
    """On a fading channel the phase does not follow a straight line. Here it is flat over
    the prefix and then turns by 0.6 rad per slot, so a ramp fitted to the prefix and
    extrapolated is 90 degrees wrong by the third count slot. Reading each slot against the
    one before it follows the turn."""
    sps = 4
    waveforms = slot_waveforms(sps)
    slot = 32 * sps
    m = mode(150, "long")
    channel = symbols.preamble_channel_symbols(m)[:15]  # first segment
    phase = np.where(np.arange(15) < 9, 0.0, 0.6 * (np.arange(15) - 8))
    z = np.zeros(15 * slot + waveforms.shape[1], dtype=complex)
    for j, v in enumerate(channel):
        z[j * slot : j * slot + waveforms.shape[1]] += waveforms[v] * np.exp(1j * phase[j])
    decided, residual_hz, prefix_errors = read_segment(z, 0, 0.0, waveforms, slot, 9600)
    assert decided == [int(v) for v in channel] and prefix_errors == 0


def test_short_preambles_are_acquired_on_a_5_hz_fading_channel():
    """Table XVI's hardest channel (2 paths, 5 ms, 5 Hz) at the 75-bps SNR, 2 dB, with only
    a 0.6-s preamble. Measured over 20 channels: 16 acquired before slot reading followed
    the phase, 19 after."""
    m = mode(75, "short")
    acquired = 0
    for seed in range(10):
        x = np.concatenate(
            [
                np.zeros(4000, complex),
                modulate(transmit_bytes(b"x" * 20, m)),
                np.zeros(9600, complex),
            ]
        )
        y = WattersonChannel(ChannelSpec(2, 5.0, 5.0, 2.0), seed=1000 + seed).process(x)
        search = PreambleSearch()
        found = search.process(y) or search.flush()
        acquired += found is not None and found.mode == m
    assert acquired >= 9
