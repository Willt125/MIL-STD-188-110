"""The sound card device, against a fake ``sounddevice`` module (no hardware in CI). With a
real card: docs/ONAIR.md, bench B."""

import sys
import threading
import time
import types

import numpy as np
import pytest

from m110c.modulate import modulate
from m110c.spec import mode
from m110c.station import Fault, Received, Station, TxEnded
from m110c.tx import transmit_bytes


class FakeStream:
    """Stands for sounddevice.Stream: the test (or a thread) calls the callback as a card
    would, one block at a time."""

    instances: list = []

    def __init__(self, samplerate, blocksize, device, channels, dtype, latency, callback):
        self.samplerate, self.channels, self.callback = samplerate, channels, callback
        self.started = self.closed = False
        self.played: list[np.ndarray] = []
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def block(self, indata, input_overflow=False, output_underflow=False):
        """One callback: ``indata`` (frames, input channels); returns what was played."""
        frames = indata.shape[0]
        outdata = np.full((frames, self.channels[1]), np.nan, dtype=np.float32)
        status = types.SimpleNamespace(
            input_overflow=input_overflow, output_underflow=output_underflow
        )
        self.callback(indata.astype(np.float32), outdata, frames, None, status)
        self.played.append(outdata.copy())
        return outdata


@pytest.fixture
def fake_sd(monkeypatch):
    module = types.ModuleType("sounddevice")
    module.Stream = FakeStream
    module.query_devices = lambda device=None, kind=None: (
        {"max_input_channels": 2, "max_output_channels": 2} if kind else "0 Fake card, 2 in, 2 out"
    )
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    FakeStream.instances.clear()
    return module


def card(**kwargs):
    from m110c.audio.soundcard import SoundCard

    return SoundCard(sample_rate=9600, **kwargs), FakeStream.instances[-1]


def test_a_callback_moves_one_block_each_way(fake_sd):
    device, stream = card(input_channel=1)
    assert stream.started and stream.channels == (2, 2)
    indata = np.zeros((960, 2))
    indata[:, 1] = 0.25  # the chosen input channel
    device.write(np.full(500, 2.0 + 1j))  # beyond full scale: clipped on the way out
    out = stream.block(indata)
    assert np.all(out[:500] == 1.0) and np.all(out[500:] == 0.0)  # real part, every channel
    x = device.read()
    assert x.size == 960 and np.allclose(x, 0.25) and not np.any(x.imag)


def test_a_slow_reader_loses_the_oldest_input_as_zeros(fake_sd):
    device, stream = card(ring_s=0.2)  # room for 1920 samples
    for k in range(3):
        stream.block(np.full((960, 2), k + 1.0))
    first = device.read()
    assert not np.any(first) and device.stats.overruns == 960  # block 1 dropped
    assert np.allclose(device.read(), 2.0) and np.allclose(device.read(), 3.0)


def test_driver_reports_are_counted(fake_sd):
    device, stream = card()
    stream.block(np.zeros((960, 2)), input_overflow=True, output_underflow=True)
    assert (device.driver_overflows, device.driver_underflows) == (1, 1)


def test_a_silent_card_is_an_error_not_a_hang(fake_sd, monkeypatch):
    device, _ = card(block_s=0.01)
    monkeypatch.setattr(device._ring, "get", lambda n, timeout=None: None)
    with pytest.raises(RuntimeError, match="no input"):
        device.read()
    device.close()
    assert FakeStream.instances[-1].closed


def test_without_sounddevice_the_error_says_what_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # import fails
    from m110c.audio.soundcard import SoundCard

    with pytest.raises(RuntimeError, match="pip install"):
        SoundCard()


def test_a_station_on_a_sound_card(fake_sd):
    """A message arrives through the card's callbacks (in real time, from another thread)
    while the station replies through its output."""
    text = b"Through the sound card."
    signal = modulate(transmit_bytes(text, mode(2400, "short")))
    signal = np.concatenate([np.zeros(2000, complex), signal, np.zeros(9600 * 2, complex)])
    device, stream = card()
    station = Station(device, callsign="N0CALL")
    log = []
    station.on(log.append)
    station.send(b"reply", rate=2400, at=(signal.size - 19000) / 9600)  # after the message

    def feed():
        for i in range(0, signal.size, 480):
            stream.block(np.repeat(signal.real[i : i + 480, None], 2, axis=1))
            time.sleep(0.001)
        time.sleep(0.5)
        station.stop()

    feeder = threading.Thread(target=feed)
    feeder.start()
    station.run()
    feeder.join()
    device.close()
    assert b"".join(e.data for e in log if isinstance(e, Received)) == text
    assert any(isinstance(e, TxEnded) and e.complete for e in log)
    assert [e for e in log if isinstance(e, Fault)] == []
    played = np.concatenate(stream.played)[:, 0]
    assert np.count_nonzero(played) > 9600  # the reply went out
