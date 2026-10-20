"""The roll-off as a setting: the receiver follows the transmitter's, and the bandwidths that
docs/PERFORMANCE.md (roll-off) and docs/NETWORK.md (D5) rely on (tools/rolloff_check.py)."""

import importlib.util
import pathlib

import numpy as np
import pytest

from m110c.cli import rx_main
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit_bytes

PATH = pathlib.Path(__file__).parents[2] / "tools" / "rolloff_check.py"
spec = importlib.util.spec_from_file_location("rolloff_check", PATH)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

TEXT = b"Roll-off as a setting. " * 20


def signal(rolloff):
    x = check.modulated(transmit_bytes(TEXT, mode(1200, "short")), rolloff)
    return np.concatenate([np.zeros(2000, complex), x, np.zeros(9600, complex)])


def receive(x, **kwargs):
    rx = Receiver(**kwargs)
    return b"".join(rx.process(x[i : i + 4800]) for i in range(0, x.size, 4800)) + rx.finish()


@pytest.mark.parametrize("rolloff", [0.15, 0.35])
def test_the_receiver_takes_the_transmitters_rolloff(rolloff):
    assert receive(signal(rolloff), rolloff=rolloff) == TEXT


def test_the_receivers_filters_follow_the_setting():
    """Acquisition, the demodulator and late entry all filter with the setting."""
    rx = Receiver(rolloff=0.15, late_entry=True)
    rx.process(signal(0.15)[:30000])
    assert rx._search.waveforms.shape == Receiver()._search.waveforms.shape
    assert not np.allclose(rx._search.waveforms, Receiver()._search.waveforms)
    assert np.allclose(rx._demod.taps, check.Modulator(rolloff=0.15).taps)
    assert np.allclose(rx._late_search.taps, check.Modulator(rolloff=0.15).taps)


def test_rx_cli_rolloff(tmp_path):
    path = tmp_path / "in.cf32"
    signal(0.15).astype(np.complex64).tofile(path)
    out = tmp_path / "out.bin"
    assert rx_main([str(path), "-o", str(out), "--rolloff", "0.15", "-q"]) == 0
    assert out.read_bytes() == TEXT
    assert rx_main([str(path), "-o", str(out), "--rolloff", "0", "-q"]) == 2


def test_amateur_bandwidth():
    """At -26 dB, roll-off 0.15 stays within 2.8 kHz (US amateur HF data) and 0.25 does not;
    every roll-off up to 0.35 keeps 110C's 3-kHz occupied bandwidth (99% of the power)."""
    tribits = transmit_bytes(bytes(np.random.default_rng(0).integers(0, 256, 3000)), mode(2400))
    occupied, width = check.bandwidths(check.modulated(tribits, 0.15))
    assert width < 2800 and occupied < 2600
    _, width = check.bandwidths(check.modulated(tribits, 0.25))
    assert width > 2900
    occupied, width = check.bandwidths(check.modulated(tribits, 0.35))
    assert occupied < 3000 < width
