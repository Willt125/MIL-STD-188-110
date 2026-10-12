"""Tests for preamble acquisition (M8)."""

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c import scrambler, symbols
from m110c.channel.awgn import add_awgn
from m110c.channel.impairments import fractional_delay, frequency_offset
from m110c.modulate import modulate
from m110c.rx.acquire import PreambleSearch, slot_waveforms
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
