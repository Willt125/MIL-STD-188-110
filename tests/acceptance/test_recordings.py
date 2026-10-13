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

import pytest

from m110c import io
from m110c.framing import AsyncFormat
from m110c.rx.receiver import Receiver

ROOT = os.environ.get("M110C_RECORDINGS")
pytestmark = pytest.mark.skipif(
    not ROOT or not pathlib.Path(ROOT).is_dir(), reason="M110C_RECORDINGS not set"
)

FOX = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789"


def receive(name):
    path = pathlib.Path(ROOT) / name
    if not path.is_file():
        pytest.skip(f"{name} not present")
    with path.open("rb") as f:
        rate, channels = io.read_wav_header(f)
        data = f.read()
    x = io.decode_samples(data[: len(data) - len(data) % (2 * channels)], "wav")
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    rx = Receiver(sample_rate=rate, async_format=AsyncFormat.parse("8N1"))
    out = rx.process(x) + rx.finish()
    return out, [(str(m.mode), m.n_bytes, m.eom_found) for m in rx.messages]


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


def test_digital_voice_is_not_taken_for_data():
    """D1/D2 = 7,7 (digital voice) is not implemented (INT-019)."""
    _, messages = receive("MIL188-110A_DIGI-VX.wav")
    assert messages == []
