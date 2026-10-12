"""Regression pin: the transmitter must reproduce tests/vectors/tx_golden.json exactly.

The vectors are this implementation's own output (the spec gives no test vectors), so a
failure here means the transmitted waveform changed. If that change is intended,
regenerate with ``python tools/make_tx_vectors.py`` and explain why in the commit message.
"""

import hashlib
import json
import pathlib

import pytest

from m110c.spec import all_modes
from m110c.tx import transmit_bytes

GOLDEN = json.loads(
    (pathlib.Path(__file__).resolve().parent.parent / "vectors" / "tx_golden.json").read_text()
)
MESSAGE = bytes.fromhex(GOLDEN["message_hex"])


def test_golden_file_covers_every_mode():
    assert sorted(GOLDEN["modes"]) == sorted(m.name for m in all_modes())


@pytest.mark.parametrize("m", all_modes(), ids=lambda m: m.name)
def test_transmitter_reproduces_golden_vector(m):
    expected = GOLDEN["modes"][m.name]
    digits = "".join(map(str, transmit_bytes(MESSAGE, m).tolist()))
    # Head and tail first: on failure they show *where* the stream diverged.
    assert digits[: len(expected["head"])] == expected["head"]
    assert digits[-len(expected["tail"]) :] == expected["tail"]
    assert len(digits) == expected["length"]
    assert hashlib.sha256(digits.encode()).hexdigest() == expected["sha256"]
