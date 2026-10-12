"""End-to-end tests of the genie-aided receiver (M7): front end, data decoder, EOM, bytes."""

import itertools
import json
import math
import pathlib

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m110c.channel.awgn import add_awgn
from m110c.framing import AsyncFormat
from m110c.modulate import modulate, tribits_to_phasors
from m110c.rx.decode import DataDecoder
from m110c.rx.eom import EomDetector
from m110c.rx.frontend import SymbolSampler
from m110c.rx.genie import GenieReceiver
from m110c.spec import all_modes, mode
from m110c.tx import transmit, transmit_bytes

MODES = all_modes()
ids = [m.name for m in MODES]


def receive(x, m, chunks=None, **kwargs):
    rx = GenieReceiver(m, **kwargs)
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


# --- Front end ----------------------------------------------------------------------------


@pytest.mark.parametrize("sample_rate", [9600, 48000])
def test_front_end_recovers_the_transmitted_symbols(sample_rate):
    tri = np.random.default_rng(1).integers(0, 8, 400).astype(np.uint8)
    x = modulate(tri, sample_rate=sample_rate)
    sampler = SymbolSampler(sample_rate)
    y = sampler.process(x)[:400] / (0.4 * np.sqrt(sampler.sps))
    assert np.max(np.abs(y - tribits_to_phasors(tri))) < 0.01


def test_front_end_accepts_real_audio_at_half_amplitude():
    """The mirror image of a real signal lands at -3600 Hz after mixing, where the matched
    filter removes it; only the amplitude halves."""
    tri = np.random.default_rng(2).integers(0, 8, 400).astype(np.uint8)
    y = SymbolSampler().process(modulate(tri).real)[20:380] / 0.8
    assert np.max(np.abs(2 * y - tribits_to_phasors(tri)[20:380])) < 0.02


@settings(max_examples=25, deadline=None)
@given(st.lists(st.integers(1, 500), min_size=1, max_size=6))
def test_front_end_is_chunk_invariant(chunks):
    x = modulate(np.random.default_rng(3).integers(0, 8, 300).astype(np.uint8))
    reference = SymbolSampler().process(x)
    s, out, i = SymbolSampler(), [], 0
    for size in itertools.cycle(chunks):
        if i >= x.size:
            break
        out.append(s.process(x[i : i + size]))
        i += size
    assert np.allclose(np.concatenate(out), reference, atol=1e-12)


def test_front_end_buffer_is_bounded():
    s = SymbolSampler()
    for _ in range(100):
        s.process(np.zeros(1000))
        assert s._buffer.size < s.taps.size + s.sps


# --- Data decoder on symbols ------------------------------------------------------------------


@pytest.mark.parametrize("m", MODES, ids=ids)
def test_decoder_inverts_the_transmitter_on_ideal_symbols(m):
    msg = np.random.default_rng(4).integers(0, 2, 333).astype(np.uint8)
    tri = transmit(msg, m)
    dec = DataDecoder(m)
    bits = np.concatenate(
        [dec.process(tribits_to_phasors(tri[m.preamble_symbols :])), dec.finish()]
    )
    assert bits[: msg.size].tolist() == msg.tolist()
    assert dec.incomplete_symbols == 0 and dec.incomplete_block_bits == 0


def test_decoder_reads_the_real_modems_transmission():
    """The real 188-110A fox recording's received symbols (tests/vectors) go through our
    receive back half: the decoded payload must equal the payload the real modem sent."""
    vector = json.loads(
        (
            pathlib.Path(__file__).resolve().parent.parent / "vectors" / "irl_fox_2400S.json"
        ).read_text()
    )
    rx_symbols = tribits_to_phasors(np.array([int(c) for c in vector["rx_data_tribits"]]))
    payload = np.array([int(c) for c in vector["payload_bits"]], dtype=np.uint8)
    dec, eom = DataDecoder(mode(2400, "short")), EomDetector()
    out = [eom.process(dec.process(rx_symbols)), eom.process(dec.finish()), eom.finish()]
    assert eom.found
    assert np.array_equal(np.concatenate(out), payload)


# --- Whole receiver, noiseless ------------------------------------------------------------------


@pytest.mark.parametrize("m", MODES, ids=ids)
@pytest.mark.parametrize("n_bytes", [0, 1, 300])
def test_noiseless_loopback(m, n_bytes):
    msg = bytes(np.random.default_rng(n_bytes).integers(0, 256, n_bytes, dtype=np.uint8))
    out, rx = receive(modulate(transmit_bytes(msg, m)), m)
    assert out == msg and rx.eom_found


@pytest.mark.parametrize("fmt", ["8N1", "7E2"])
def test_async_loopback(fmt):
    f = AsyncFormat.parse(fmt)
    msg = b"ASYNC LOOPBACK 0123456789\r\n"
    m = mode(1200, "short")
    out, _ = receive(modulate(transmit_bytes(msg, m, async_format=f)), m, async_format=f)
    # With 7 data bits only the low 7 bits of each byte travel (Table A-VIII).
    expected = bytes(b & 0x7F for b in msg) if f.data_bits == 7 else msg
    assert out == expected


def test_msb_first_loopback():
    m = mode(300, "long")
    msg = b"\x01\x80msb"
    out, _ = receive(modulate(transmit_bytes(msg, m, msb_first=True)), m, msb_first=True)
    assert out == msg


def test_real_audio_input():
    m = mode(2400, "long")
    msg = b"real-valued input, as from a sound card"
    out, rx = receive(modulate(transmit_bytes(msg, m)).real, m, gain=0.4)
    assert out == msg and rx.eom_found


def test_48k_sample_rate():
    m = mode(600, "short")
    msg = b"forty-eight thousand"
    out, _ = receive(modulate(transmit_bytes(msg, m), sample_rate=48000), m, sample_rate=48000)
    assert out == msg


def test_truncated_signal_reports_no_eom_and_keeps_what_it_decoded():
    m = mode(600, "zero")
    msg = bytes(range(200))
    x = modulate(transmit_bytes(msg, m))
    out, rx = receive(x[: int(x.size * 0.6)], m)
    assert not rx.eom_found
    assert 0 < len(out) < len(msg) and msg.startswith(out[: len(out) - 2])


# --- Streaming ---------------------------------------------------------------------------------


@settings(max_examples=15, deadline=None)
@given(st.sampled_from(MODES), st.lists(st.integers(1, 5000), min_size=1, max_size=5))
def test_receiver_output_does_not_depend_on_chunking(m, chunks):
    msg = bytes(range(64))
    x = add_awgn(
        modulate(transmit_bytes(msg, m)), 12, 9600, np.random.default_rng(5), signal_power=0.16
    )
    whole, _ = receive(x, m)
    pieces, _ = receive(x, m, chunks)
    assert pieces == whole


# --- Noise -------------------------------------------------------------------------------------


def q_function(x):
    return 0.5 * math.erfc(x / math.sqrt(2))


def test_uncoded_4800_bit_error_rate_matches_theory():
    """4800 bps is uncoded 8-PSK with Gray-like MGD labelling: BER ~ (2/3) Q(sqrt(2 Es/N0)
    sin(pi/8)). With SNR measured in 3 kHz and 2400 symbols/s, Es/N0 = SNR * 3000/2400."""
    m = mode(4800)
    rng = np.random.default_rng(6)
    msg_bits = rng.integers(0, 2, 60_000).astype(np.uint8)
    snr_db = 14.0
    x = add_awgn(modulate(transmit(msg_bits, m)), snr_db, 9600, rng, signal_power=0.16)
    rx = GenieReceiver(m)
    dec_bits = np.concatenate(
        [rx.decoder.process(rx.sampler.process(x)[m.preamble_symbols :] / 0.8), rx.decoder.finish()]
    )
    ber = np.mean(dec_bits[: msg_bits.size] != msg_bits)
    esn0 = 10 ** (snr_db / 10) * 3000 / 2400
    theory = 2 / 3 * q_function(math.sqrt(2 * esn0) * math.sin(math.pi / 8))
    assert 0.6 * theory < ber < 1.5 * theory, (ber, theory)


@pytest.mark.parametrize(
    "rate, interleave, snr_db, max_ber", [(4800, "zero", 17, 1e-3), (2400, "long", 10, 1e-5)]
)
def test_table_xvi_single_fixed_path_rows(rate, interleave, snr_db, max_ber):
    """Table XVI, "1 Fixed" rows: 4800 bps at 17 dB SNR needs BER <= 1e-3, and 2400 bps at
    10 dB needs BER <= 1e-5. With perfect timing these should be met with room to spare
    (the fading rows need M9/M10)."""
    m = mode(rate, interleave)
    rng = np.random.default_rng(7)
    msg = bytes(rng.integers(0, 256, 6000, dtype=np.uint8))
    x = add_awgn(modulate(transmit_bytes(msg, m)), snr_db, 9600, rng, signal_power=0.16)
    out, rx = receive(x, m)
    assert rx.eom_found
    n = min(len(out), len(msg))
    errors = np.count_nonzero(
        np.unpackbits(np.frombuffer(out[:n], np.uint8) ^ np.frombuffer(msg[:n], np.uint8))
    )
    assert len(out) == len(msg)
    assert errors / (8 * len(msg)) <= max_ber


def test_lower_rates_survive_lower_snr():
    """The point of the rate ladder: at -3 dB SNR, 75 bps still decodes perfectly while
    2400 bps does not."""
    rng = np.random.default_rng(8)
    msg = b"robustness ladder"
    results = {}
    for m in (mode(75, "short"), mode(2400, "short")):
        x = add_awgn(modulate(transmit_bytes(msg, m)), -3, 9600, rng, signal_power=0.16)
        out, _ = receive(x, m)
        results[m.name] = out == msg
    assert results == {"75S": True, "2400S": False}


@pytest.mark.parametrize("seed", [100, 101, 102])
def test_repetition_combining_earns_its_gain(seed):
    """150 bps sends every coded pair 4 times. Measured at -6 dB SNR: 10/10 messages
    perfect when the copies are combined, 0/10 when only one copy is used."""
    m = mode(150, "short")
    msg = bytes(range(120))
    x = add_awgn(
        modulate(transmit_bytes(msg, m)), -6, 9600, np.random.default_rng(seed), signal_power=0.16
    )
    out, rx = receive(x, m)
    assert out == msg and rx.eom_found


@pytest.mark.parametrize("interleave", ["short", "long", "zero"])
def test_75bps_streaming_in_small_pieces(interleave):
    """The exceptional-set position must carry across calls (INT-010)."""
    m = mode(75, interleave)
    msg = bytes(range(40))
    x = modulate(transmit_bytes(msg, m))
    out, rx = receive(x, m, chunks=[701, 1999, 64])
    assert out == msg and rx.eom_found
    # A wrong set position only causes erasures, which the code repairs; the quality
    # measurement is what reveals it.
    assert rx.snr_db > 40


@pytest.mark.parametrize(
    "m", [mode(2400, "short"), mode(75, "long")], ids=["probes", "decided sets"]
)
def test_snr_estimate_tracks_the_true_snr(m):
    """Per-symbol SNR = SNR(3 kHz) * 3000/2400; measured on probes, or on decided sets at
    75 bps (decision-directed, so slightly optimistic at low SNR)."""
    msg = bytes(range(200))
    x = add_awgn(
        modulate(transmit_bytes(msg, m)), 10, 9600, np.random.default_rng(9), signal_power=0.16
    )
    _, rx = receive(x, m)
    assert abs(rx.snr_db - (10 + 10 * math.log10(3000 / 2400))) < 1.0


@pytest.mark.parametrize("fraction", [0.7, 0.9])
def test_signal_cut_off_inside_a_block_still_decodes(fraction):
    """A partial final interleaver block is completed with erasures and decoded. Measured on
    2400L: zero errors from 60% of the block received; below 55% the code gives up. Before
    this, any shortfall discarded the whole block (up to 4.8 s of data)."""
    m = mode(2400, "long")
    msg = np.random.default_rng(10).integers(0, 2, 11000).astype(np.uint8)
    tri = transmit(msg, m)[m.preamble_symbols :]
    keep = int(fraction * tri.size) // m.frame_symbols * m.frame_symbols
    dec = DataDecoder(m)
    bits = np.concatenate([dec.process(tribits_to_phasors(tri[:keep])), dec.finish()])
    assert dec.incomplete_block_bits > 0  # the block really was cut short
    assert np.array_equal(bits[: msg.size], msg)
