"""The SDR device (PLAN.md P1), against a fake SoapySDR module: the frequency conversions,
keying by enabling the transmitter, and the bench cap. With real SDRs: docs/ONAIR.md,
bench D."""

import sys
import threading
import time
import types

import numpy as np
import pytest

from m110c.modulate import modulate
from m110c.resample import Resampler
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.station import Received, Station, TxEnded
from m110c.tx import transmit_bytes

RATE = 240000
IF = RATE / 4
TEXT = b"Over the SDR, as an upper sideband."


class FakeSDR:
    """Stands for SoapySDR.Device: plays ``source`` (at the SDR's rate) into its receive
    stream, a few times faster than real time, and records what is written."""

    source = np.zeros(0, np.complex64)
    last = None

    def __init__(self, args):
        self.args, self.calls, self.written, self.events = args, [], [], []
        self._at = 0
        FakeSDR.last = self

    def __getattr__(self, name):  # setSampleRate, setFrequency, setGain, ...: recorded
        return lambda *a: self.calls.append((name, *a))

    def hasGainMode(self, direction, channel):
        return True

    def getGainRange(self, direction, channel):
        return types.SimpleNamespace(minimum=lambda: -12.0, maximum=lambda: 64.0)

    def setupStream(self, direction, fmt, channels):
        return direction

    def activateStream(self, stream):
        self.events.append(("on", stream, len(self.written)))

    def deactivateStream(self, stream):
        self.events.append(("off", stream, len(self.written)))

    def closeStream(self, stream):
        pass

    def readStream(self, stream, buffers, n, timeoutUs=0):
        time.sleep(0.002)
        piece = FakeSDR.source[self._at : self._at + n]
        if not piece.size:
            piece = np.zeros(n, np.complex64)  # the band goes quiet
        buffers[0][: piece.size] = piece
        self._at += piece.size
        return types.SimpleNamespace(ret=piece.size)

    def writeStream(self, stream, buffers, n, timeoutUs=0):
        self.written.append(np.array(buffers[0][:n]))
        time.sleep(0.001)
        return types.SimpleNamespace(ret=n)


@pytest.fixture
def soapy(monkeypatch):
    module = types.ModuleType("SoapySDR")
    module.Device = FakeSDR
    module.Device.enumerate = staticmethod(lambda: [{"driver": "fake", "label": "Fake SDR"}])
    module.SOAPY_SDR_RX, module.SOAPY_SDR_TX, module.SOAPY_SDR_CF32 = 1, 0, "CF32"
    monkeypatch.setitem(sys.modules, "SoapySDR", module)
    FakeSDR.source = np.zeros(0, np.complex64)
    return module


def on_the_air(audio: np.ndarray) -> np.ndarray:
    """The station's signal as the SDR sees it: at its rate, the dial IF above its centre."""
    y = Resampler(9600, RATE).process(audio)
    y = np.concatenate([y, np.zeros(RATE // 10, complex)])
    return (y * np.exp(2j * np.pi * IF * np.arange(y.size) / RATE)).astype(np.complex64)


def heard(sdr_samples: np.ndarray) -> np.ndarray:
    y = sdr_samples * np.exp(-2j * np.pi * IF * np.arange(sdr_samples.size) / RATE)
    return Resampler(RATE, 9600).process(y)


def sdr(**kwargs):
    from m110c.audio.sdr import SdrDevice

    return SdrDevice("driver=fake", dial_hz=14.1e6, sdr_rate=RATE, **kwargs)


def run_for(station, seconds):
    stopper = threading.Timer(seconds, station.stop)
    stopper.start()
    station.run()
    stopper.cancel()


def test_tuning_and_gains(soapy):
    device = sdr()
    calls = FakeSDR.last.calls
    centre = 14.1e6 - IF  # the dial IF above the SDR's centre, both directions
    assert ("setFrequency", 1, 0, centre) in calls and ("setFrequency", 0, 0, centre) in calls
    assert ("setSampleRate", 1, 0, RATE) in calls
    assert ("setGainMode", 1, 0, True) in calls  # automatic receive gain
    assert ("setGain", 0, 0, -12.0) in calls  # bench: the lowest transmit gain
    device.close()
    corrected = sdr(ppm=10.0)
    assert ("setFrequency", 1, 0, (14.1e6 - IF) / (1 + 10e-6)) in FakeSDR.last.calls
    corrected.close()


def test_receiving_through_the_sdr(soapy):
    audio = modulate(transmit_bytes(TEXT, mode(2400, "short")))
    FakeSDR.source = on_the_air(np.concatenate([np.zeros(2000, complex), audio]))
    device = sdr(tx_mode="off")
    station = Station(device)
    log = []
    station.on(log.append)
    run_for(station, 2.0)
    device.close()
    assert b"".join(e.data for e in log if isinstance(e, Received)) == TEXT


def test_transmitting_through_the_sdr(soapy):
    device = sdr()
    station = Station(device, callsign="N0CALL")
    log = []
    station.on(log.append)
    station.send(TEXT, rate=2400)
    run_for(station, 2.5)
    device.close()
    fake = FakeSDR.last
    assert any(isinstance(e, TxEnded) and e.complete for e in log)
    # Keyed for the transmission only: one "on" and one "off" of the transmit stream.
    keying = [(kind, n) for kind, stream, n in fake.events if stream == 0]
    assert [kind for kind, _ in keying] == ["on", "off"]
    out = np.concatenate(fake.written)
    rx = Receiver()
    assert rx.process(heard(out)) + rx.finish() == TEXT
    # The bench level: 20 dB below the station's own.
    expected = Resampler(9600, RATE).process(modulate(transmit_bytes(TEXT, mode(2400, "short"))))
    ratio = np.sqrt(np.mean(np.abs(out) ** 2) / np.mean(np.abs(expected) ** 2))
    assert 20 * np.log10(ratio) == pytest.approx(-20, abs=0.5)


def test_the_bench_cap(soapy):
    with pytest.raises(ValueError, match="filtered"):
        sdr(tx_gain=30.0)  # a gain without a filter
    device = sdr(tx_mode="filtered", tx_gain=30.0)
    assert ("setGain", 0, 0, 30.0) in FakeSDR.last.calls and device._level == 1.0
    device.close()
    with pytest.raises(ValueError, match="one of off, bench, filtered"):
        sdr(tx_mode="loud")


def test_receive_only(soapy):
    device = sdr(tx_mode="off")
    assert not device.on_air and not device.can_transmit
    with pytest.raises(ValueError, match="receive only"):
        Station(device, callsign="N0CALL").send(b"x")
    device.close()


def test_rates_and_installation(soapy, monkeypatch):
    from m110c.audio.sdr import SdrDevice, list_sdrs

    with pytest.raises(ValueError, match="multiple of 9600"):
        SdrDevice(sdr_rate=250000)
    assert list_sdrs() == [{"driver": "fake", "label": "Fake SDR"}]
    monkeypatch.setitem(sys.modules, "SoapySDR", None)
    with pytest.raises(RuntimeError, match="SoapySDR"):
        SdrDevice()
