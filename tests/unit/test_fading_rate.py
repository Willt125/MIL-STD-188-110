"""The receiver's fading-rate report (EqualizingDemodulator.fading_hz, Message.fading_hz):
how fast the channel changes, measured from the equaliser's own channel estimates, for a link
layer to tell slow fading from fast (docs/NETWORK.md §7)."""

import re

import numpy as np
import pytest

from m110c.bits import bytes_to_bits
from m110c.channel.watterson import ChannelSpec, WattersonChannel
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit


def received(rate, fading_hz, snr_db=15.0, seconds=10, seed=1, paths=2):
    m = mode(rate, "short")
    msg = bytes(np.random.default_rng(seed).integers(0, 256, rate * seconds // 8, dtype=np.uint8))
    x = modulate(transmit(bytes_to_bits(msg), m))
    x = np.concatenate([np.zeros(3000, complex), x, np.zeros(9600, complex)])
    spec = ChannelSpec(paths, 2.0 if paths == 2 else 0.0, fading_hz, snr_db)
    y = WattersonChannel(spec, seed=seed + 3).process(x)
    rx = Receiver(interleave="short")
    rx.process(y)
    rx.finish()
    return rx.messages[0]


@pytest.mark.parametrize("rate", [1200, 75])
@pytest.mark.parametrize("fading_hz", [1.0, 5.0])
def test_the_fading_rate_is_measured(rate, fading_hz):
    """Measured over 0.5-10 Hz, 2400-75 bps, 6-20 dB: within about 10% from 0.5 to 5 Hz
    (a little high for slow fading at low SNR), saturating near 6-7 Hz above."""
    msg = received(rate, fading_hz)
    assert msg.fading_hz == pytest.approx(fading_hz, rel=0.25)


def test_no_fading_reads_near_zero():
    msg = received(1200, 0.0, paths=1)
    assert msg.fading_hz is not None and msg.fading_hz < 0.3


def test_even_a_short_message_reads_close():
    """150 bytes at 1200S (about 2 s of data phase) already gives a fair reading."""
    msg = received(1200, 1.0, seconds=1)
    assert msg.fading_hz == pytest.approx(1.0, rel=0.4)


def test_no_reading_without_the_equaliser_or_before_the_data():
    from m110c.rx.equalize import EqualizingDemodulator

    m = mode(1200, "short")
    x = modulate(transmit(bytes_to_bits(bytes(200)), m))
    rx = Receiver(equalize=False)
    rx.process(np.concatenate([np.zeros(3000, complex), x, np.zeros(9600, complex)]))
    rx.finish()
    assert rx.messages[0].fading_hz is None  # the M8 tracker does not measure it
    assert EqualizingDemodulator.fading_hz.fget(type("D", (), {"_fading": np.zeros(5)})) is None


def test_rx_reports_the_fading_rate(tmp_path, capsys):
    from m110c.cli import rx_main

    m = mode(1200, "short")
    x = modulate(transmit(bytes_to_bits(bytes(600)), m))
    x = np.concatenate([np.zeros(3000, complex), x, np.zeros(9600, complex)])
    y = WattersonChannel(ChannelSpec(2, 2.0, 2.0, 20.0), seed=5).process(x)
    path = tmp_path / "in.cf32"
    y.astype(np.complex64).tofile(path)
    assert rx_main([str(path), "-o", str(tmp_path / "out.bin")]) == 0
    found = re.search(r"SNR [\d.]+ dB, fading ([\d.]+) Hz, 600 bytes", capsys.readouterr().err)
    assert found and float(found[1]) == pytest.approx(2.0, rel=0.3)
