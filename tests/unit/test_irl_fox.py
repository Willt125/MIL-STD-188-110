"""External validation: our transmitter against a real modem's transmission.

tests/vectors/irl_fox_2400S.json holds data derived from a real-world 188-110A recording
(see docs/VALIDATION.md): the payload bits the real modem sent (recovered by decoding the
recording) and the symbols it actually put on the air. Feeding that payload to *our*
transmitter must reproduce those symbols. This is the only test in the suite whose
reference values come from outside this implementation.

What a pass shows: every interpretation on the 2400-bps transmit path (encoder register
orientation, interleaver, MGD, symbol placement, data scrambler, probe and D1/D2 layout,
EOM and flush length) agrees with a fielded modem. It does not cover other modes; see
docs/VALIDATION.md for those.
"""

import json
import pathlib

import numpy as np

from m110c.spec import mode
from m110c.tx import transmit

VECTOR = json.loads(
    (pathlib.Path(__file__).resolve().parent.parent / "vectors" / "irl_fox_2400S.json").read_text()
)
PAYLOAD = np.array([int(c) for c in VECTOR["payload_bits"]], dtype=np.uint8)
RX = np.array([int(c) for c in VECTOR["rx_data_tribits"]], dtype=np.uint8)
FOX = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789\r\n"


def test_our_transmitter_reproduces_the_real_modem():
    """Symbol-by-symbol comparison over the whole data phase. The one disagreement
    observed is a channel-noise error in the recording (1 in 7200 symbols)."""
    m = mode(2400, "short")
    data_phase = transmit(PAYLOAD, m)[m.preamble_symbols :]
    assert data_phase.size == RX.size
    assert np.count_nonzero(data_phase != RX) <= 2


def test_int_013_transmission_length_matches_the_real_signal():
    """The real signal stops after exactly 150 frames (5 blocks): EOM, 144 flush bits,
    then zero padding to the end of the interleaver block, and nothing more."""
    m = mode(2400, "short")
    frames = (transmit(PAYLOAD, m).size - m.preamble_symbols) // m.frame_symbols
    assert frames == VECTOR["observed"]["data_phase_frames"] == 150


def uart_8n1(bits):
    """Parse asynchronous characters: idle 1s, start bit 0, 8 data bits LSB first, stop 1."""
    chars, i = [], 0
    while i + 10 <= len(bits):
        if bits[i]:
            i += 1
            continue
        assert bits[i + 9] == 1, f"framing error at bit {i}"
        chars.append(sum(int(b) << k for k, b in enumerate(bits[i + 1 : i + 9])))
        i += 10
    return bytes(chars)


def test_payload_is_async_8n1_characters_lsb_first():
    """The real modem carried a UART stream: start/stop framing over the air, data bits
    LSB first (consistent with INT-016's bit order, but framed, see VALIDATION.md)."""
    text = uart_8n1(PAYLOAD)
    assert len(text) == 560
    assert text == (FOX * 11)[:560]
