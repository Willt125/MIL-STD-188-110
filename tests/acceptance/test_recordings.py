"""Acceptance tests on real 188-110A recordings (PLAN 4.8, M12).

The recordings are third-party files and are not in this repository (docs/VALIDATION.md
says where they come from). These tests run only when ``M110C_RECORDINGS`` names a
directory holding them as 9600-S/s WAV files under their original names, e.g. after
``sox in.wav -r 9600 -c 1 out.wav``; otherwise they are skipped.

The expectations are what the receiver decodes today, in 8N1 framing (INT-025): exact text
where the payload is plain text, and otherwise the mode, the byte count and the EOM, which
together pin down every bit (a wrong bit almost always breaks the EOM or the count).
"""

import os
import pathlib

import numpy as np
import pytest

from m110c import io
from m110c.framing import AsyncFormat
from m110c.rx.receiver import Receiver

ROOT = os.environ.get("M110C_RECORDINGS")
pytestmark = pytest.mark.skipif(
    not ROOT or not pathlib.Path(ROOT).is_dir(), reason="M110C_RECORDINGS not set"
)

FOX = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789"


def load(name):
    path = pathlib.Path(ROOT) / name
    if not path.is_file():
        pytest.skip(f"{name} not present")
    with path.open("rb") as f:
        rate, channels = io.read_wav_header(f)
        data = f.read()
    x = io.decode_samples(data[: len(data) - len(data) % (2 * channels)], "wav")
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return rate, x


def receive(name, x=None, **options):
    rate, samples = load(name)
    rx = Receiver(sample_rate=rate, async_format=AsyncFormat.parse("8N1"), **options)
    out = rx.process(samples if x is None else x) + rx.finish()
    return out, [(str(m.mode), m.n_bytes, m.eom_found) for m in rx.messages]


def stretch(x, ratio, half=32):
    """Resample by ``ratio`` input samples per output sample (Hann-windowed sinc)."""
    t = np.arange(int((x.size - 1) / ratio)) * ratio
    k0 = np.floor(t).astype(int)
    out = np.zeros(t.size)
    for j in range(-half + 1, half + 1):
        k = k0 + j
        ok = (k >= 0) & (k < x.size)
        d = t[ok] - k[ok]
        out[ok] += x.real[k[ok]] * np.sinc(d) * (0.5 + 0.5 * np.cos(np.pi * d / half))
    return out


@pytest.mark.parametrize(
    "name, mode, text",
    [
        ("150L.wav", "150L", FOX + b"\x00"),
        ("150S.wav", "150S", FOX + b"\x00"),
        ("Mil-188-110a-2400bps-quick-brown-fox.wav", "2400S", (FOX + b"\r\n") * 10),
    ],
)
def test_known_plaintext(name, mode, text):
    out, messages = receive(name)
    assert messages == [(mode, len(text), True)]
    assert out == text


@pytest.mark.parametrize(
    "name, mode, n_bytes, start",
    [
        ("MIL188-110A_4800_No_Interleave.wav", "4800", 3135, FOX + b"\r\n"),
        ("MIL188-110A_1200L.wav", "1200L", 1697, b"\r\nV V V  DE BDA21 BDA21 BDA21 HR MSG RY"),
        ("MIL188-110A_600L.wav", "600L", 646, b"\r\nV V V  DE BXT78 BXT78 BXT78 HR ZNN RY"),
        (
            "Mil-std-188-110A_(serial)_1_ale_ch_op_de_bro45_600bps_5sym_code_words.wav",
            "600L",
            4039,
            b"\r\nV V V  DE BRO45 BRO45 BRO45 HR ZNN RY",
        ),
    ],
)
def test_plain_text(name, mode, n_bytes, start):
    out, messages = receive(name)
    assert messages == [(mode, n_bytes, True)]
    assert out.startswith(start)


@pytest.mark.parametrize(
    "name, mode, n_bytes",
    [
        ("MIL188-110A_2400L.wav", "2400L", 947),
        ("MIL188-110A_2400S.wav", "2400S", 6),
        ("MIL188-110A_1200S.wav", "1200S", 1094),
        ("MIL188-110A_600S.wav", "600S", 185),
        ("MIL188-110A_300L.wav", "300L", 24),
        ("MIL188-110A_300S_.wav", "300S", 131),
        ("MIL188-110A_75L.wav", "75L", 52),
        ("MIL188-110A_75S.wav", "75S", 57),
    ],
)
def test_binary_payloads(name, mode, n_bytes):
    _, messages = receive(name)
    assert messages == [(mode, n_bytes, True)]


def test_a_contact_that_moves_from_600_to_1200_bps():
    """ "DE NC97 OK 1200 K", then the station's next transmissions at 1200 bps."""
    out, messages = receive("Mil_188-110_Serial_(ryryry_de_rc97).wav")
    assert [(m, eom) for m, _, eom in messages] == [
        ("600S", True),
        ("600S", True),
        ("1200S", True),
        ("1200S", True),
    ]
    assert b"DE NC97 OK 1200 K" in out


VOICE = "MIL188-110A_DIGI-VX.wav"
# This recording runs 0.47% slow: its preamble segments are 1929 samples apart, not 1920
# (VALIDATION.md). Undone here; no working sound card is that far off.
VOICE_CLOCK = 1929 / 1920


def test_digital_voice_is_not_taken_for_data():
    """INT-019: by default a 7,7 preamble is ignored, even where it is perfectly readable."""
    _, x = load(VOICE)
    _, messages = receive(VOICE, stretch(x, VOICE_CLOCK))
    assert messages == []


def test_digital_voice_is_the_2400s_chain():
    """INT-019, measured: received as 2400S with 7,7 in the preamble and the D probes, the
    voice transmission decodes to 9056 bits (1132 bytes) and ends with the EOM. The output
    is raw bits, not 8N1 characters, though the receiver was asked for 8N1."""
    _, x = load(VOICE)
    out, messages = receive(VOICE, stretch(x, VOICE_CLOCK), voice=True)
    assert messages == [("2400V", 1132, True)]
    assert len(out) == 1132


def test_digital_voice_payload_is_not_clear_stanag_4198():
    """STANAG 4198 (LPC-10) frames are 54 bits; bit 54 alternates 1, 0, and the 7-bit
    pitch word (bits 4, 9, 15, 27, 31, 42, 47) has Hamming weight 0, 3, 4 or 7 (Annex C,
    Table 4). This payload shows neither at any frame offset: about 0.5 and 0.56, as
    random bits would. Consistent with encrypted voice; recorded so the finding can be
    rerun."""
    _, x = load(VOICE)
    out, _ = receive(VOICE, stretch(x, VOICE_CLOCK), voice=True)
    bits = np.unpackbits(np.frombuffer(out, np.uint8), bitorder="little")[600:9000]
    pitch = np.array([4, 9, 15, 27, 31, 42, 47]) - 1
    best_sync = best_pitch = 0.0
    for offset in range(54):
        frames = bits[offset:][: (bits.size - offset) // 54 * 54].reshape(-1, 54)
        best_sync = max(best_sync, float(np.mean(frames[1:, 53] != frames[:-1, 53])))
        weight = frames[:, pitch].sum(axis=1)
        best_pitch = max(best_pitch, float(np.isin(weight, (0, 3, 4, 7)).mean()))
    assert best_sync < 0.7 and best_pitch < 0.7  # clear LPC-10 would score about 1.0
