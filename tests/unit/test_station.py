"""The station (PLAN.md P1): the receiver and transmitter around one device, on the device's
clock. Run here on virtual and file devices, which make it exactly repeatable."""

import io as pyio
import os
import threading
import time

import numpy as np
import pytest

from m110c import profile
from m110c.audio import FileDevice, VirtualDevice
from m110c.cli import tx_main
from m110c.modulate import modulate
from m110c.resample import Resampler
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.station import (
    Fault,
    Level,
    MessageEnded,
    MessageStarted,
    Received,
    Station,
    TxEnded,
    TxStarted,
)
from m110c.tx import transmit_bytes

TEXT = b"The station hears what the receiver hears. " * 6


def noisy(x, rms=0.02, seed=1):
    rng = np.random.default_rng(seed)
    return x + rms / np.sqrt(2) * (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))


def two_messages():
    """1200S then 600S, back to back, with silence around: a signal and its two payloads."""
    a, b = TEXT, b"A second message, at another rate."
    x = np.concatenate(
        [
            np.zeros(5000, complex),
            modulate(transmit_bytes(a, mode(1200, "short"))),
            np.zeros(3000, complex),
            modulate(transmit_bytes(b, mode(600, "short"))),
            np.zeros(20000, complex),
        ]
    )
    return noisy(x), (a, b)


def run_station(device, **kwargs):
    station = Station(device, **kwargs)
    log = []
    station.on(log.append)
    return station, log


def by_message(log):
    """Received bytes grouped by message, in order of starting."""
    order, data = [], {}
    for e in log:
        if isinstance(e, MessageStarted):
            order.append(id(e.message))
            data[id(e.message)] = b""
        elif isinstance(e, Received):
            data[id(e.message)] += e.data
    return [data[k] for k in order]


@pytest.mark.parametrize("block", [480, 960, 4801])
def test_the_station_receives_what_the_receiver_receives(block):
    """Same bytes, same messages, same diagnostics as the receiver given the same samples
    in one go, whatever the device's block size."""
    x, _ = two_messages()
    x = np.concatenate([x, np.zeros(-x.size % block, complex)])  # whole blocks, as read
    rx = Receiver()
    expected = rx.process(x) + rx.finish()
    station, log = run_station(VirtualDevice(x, block=block))
    station.run()
    got = by_message(log)
    assert b"".join(got) == expected
    ended = [e.message for e in log if isinstance(e, MessageEnded)]
    assert len(ended) == len(rx.messages) == 2
    for mine, theirs in zip(ended, rx.messages, strict=True):
        assert (str(mine.mode), mine.n_bytes, mine.eom_found, mine.end_sample) == (
            str(theirs.mode),
            theirs.n_bytes,
            theirs.eom_found,
            theirs.end_sample,
        )
        assert mine.snr_db == pytest.approx(theirs.snr_db)


def test_received_bytes_are_reported_per_message():
    x, payloads = two_messages()
    station, log = run_station(VirtualDevice(x))
    station.run()
    assert by_message(log) == list(payloads)
    kinds = [type(e) for e in log if not isinstance(e, Level)]
    assert kinds[0] is MessageStarted and kinds.count(MessageEnded) == 2


def test_sending_over_a_loopback_cable():
    """Full duplex on a loopback: the station hears its own transmission."""
    device = VirtualDevice(loopback_delay=300, noise_rms=0.01, duration=9600 * 15)
    station, log = run_station(device, full_duplex=True)
    request = station.send(TEXT, rate=1200)
    station.run(until_idle=True)
    assert b"".join(by_message(log)) == TEXT
    started = next(e for e in log if isinstance(e, TxStarted))
    ended = next(e for e in log if isinstance(e, TxEnded))
    assert started.request is ended.request is request and ended.complete
    air = modulate(transmit_bytes(TEXT, mode(1200, "short"))).size / 9600
    assert ended.seconds == pytest.approx(air, abs=0.1)  # (to the block)
    played = np.flatnonzero(np.abs(device.output) > 1e-9)
    assert played[0] / 9600 == pytest.approx(started.time, abs=0.1)


def test_half_duplex_hears_nothing_of_itself():
    device = VirtualDevice(loopback_delay=300, duration=9600 * 12)
    station, log = run_station(device)
    station.send(TEXT, rate=1200)
    station.run(until_idle=True)
    assert not any(isinstance(e, MessageStarted) for e in log)


def test_queued_transmissions_follow_each_other_and_start_when_asked():
    device = VirtualDevice(loopback_delay=0, duration=9600 * 30)
    station, log = run_station(device, full_duplex=True)
    station.send(b"first", rate=2400)
    station.send(b"second", rate=2400, at=8.0)
    station.run(until_idle=True)
    starts = [e.time for e in log if isinstance(e, TxStarted)]
    assert starts[0] < 0.2 and starts[1] == pytest.approx(8.0, abs=0.1)
    assert by_message(log) == [b"first", b"second"]


def test_runs_are_exactly_repeatable():
    def once():
        device = VirtualDevice(loopback_delay=100, noise_rms=0.05, seed=4, duration=9600 * 10)
        station, log = run_station(device, full_duplex=True)
        station.send(b"repeatable", rate=600, at=0.5)
        station.run(until_idle=True)
        return device.output, by_message(log)

    (out1, rx1), (out2, rx2) = once(), once()
    assert np.array_equal(out1, out2) and rx1 == rx2


def test_the_amateur_profile_needs_a_callsign_on_the_air():
    on_air = VirtualDevice(duration=960, on_air=True)
    with pytest.raises(ValueError, match="callsign"):
        Station(on_air).send(b"x")
    with pytest.raises(ValueError, match="callsign"):
        Station(on_air, callsign="nonsense!").send(b"x")
    Station(on_air, callsign="N0CALL").send(b"x")
    Station(on_air, profile=profile.AUTHORIZED).send(b"x")
    Station(VirtualDevice(duration=960)).send(b"x")  # a file or virtual device: no air
    with pytest.raises(ValueError, match="authorized"):
        Station(on_air, callsign="N0CALL", rolloff=0.35)


@pytest.mark.parametrize("call", ["N0CALL", "w1aw", "VE3ABC-7", "G4XYZ/P"])
def test_callsigns(call):
    assert profile.valid_callsign(call)


@pytest.mark.parametrize("call", ["", "NOCALL", "12345", "AB", "N0CALL!", "TOOLONG12"])
def test_not_callsigns(call):
    assert not profile.valid_callsign(call)


def test_a_gap_in_the_input_is_reported_and_costs_only_what_it_covers():
    """A sound card's overrun reaches the receiver as zeros in place of the lost samples
    (InputRing): the message survives the gap. (A jump in time instead lost the rest of the
    message: docs/PERFORMANCE.md, P1.)"""
    data = bytes(np.random.default_rng(0).integers(0, 256, 2400, dtype=np.uint8))
    x = np.concatenate(
        [
            np.zeros(4800, complex),
            modulate(transmit_bytes(data, mode(2400, "short"))),
            np.zeros(9600 * 3, complex),
        ]
    )
    station, log = run_station(VirtualDevice(noisy(x), gap_blocks={45}))
    station.run()
    faults = [e for e in log if isinstance(e, Fault)]
    assert [(f.kind, f.samples) for f in faults] == [("overrun", 960)]
    assert faults[0].time == pytest.approx(4.6)
    assert by_message(log) == [data]


def test_levels_are_reported():
    t = np.arange(9600 * 3)
    x = 0.5 * np.exp(2j * np.pi * 1000 * t / 9600)  # constant envelope, -6 dBFS
    x[100:110] = 1.0  # ten samples at full scale
    station, log = run_station(VirtualDevice(x))
    station.run()
    levels = [e for e in log if isinstance(e, Level)]
    assert len(levels) == 3
    assert levels[1].rms_dbfs == pytest.approx(-6.02, abs=0.01)
    assert levels[0].peak_dbfs == pytest.approx(0.0, abs=0.01) and levels[0].clipped == 10


def test_stopping_mid_transmission_unkeys_and_says_so():
    device = VirtualDevice(duration=None)  # endless
    station, log = run_station(device)
    station.send(TEXT * 4, rate=150)  # far longer than the run
    stopper = threading.Timer(0.5, station.stop)
    stopper.start()
    station.run()
    ended = [e for e in log if isinstance(e, TxEnded)]
    assert len(ended) == 1 and not ended[0].complete and ended[0].seconds > 0
    assert device.queued == 0 and not station.transmitting


def test_an_underrun_is_reported():
    class Slow(VirtualDevice):
        """Once, plays everything queued and half a block more, as if the program had
        fallen further behind than its lead."""

        def _read(self):
            if self._index == 20:
                self._take_output(self.queued + 480)
            return super()._read()

    device = Slow(duration=9600 * 6)
    station, log = run_station(device)
    station.send(TEXT, rate=1200)
    station.run(until_idle=True)
    faults = [e for e in log if isinstance(e, Fault)]
    # The half block too many, then that read's own block, with nothing left to play.
    assert [(f.kind, f.samples) for f in faults] == [("underrun", 480 + 960)]


def test_file_device_writes_what_m110c_tx_writes(tmp_path):
    """idle="skip": the transmission alone, sample for sample what m110c-tx produces."""
    src = tmp_path / "in.txt"
    src.write_bytes(TEXT)
    reference = tmp_path / "tx.cf32"
    assert tx_main([str(src), "-o", str(reference), "-r", "1200", "-q"]) == 0
    out = pyio.BytesIO()
    device = FileDevice(outfile=out, idle="skip")
    station = Station(device)
    station.send(TEXT, rate=1200)
    station.run(until_idle=True)
    device.close()
    assert out.getvalue() == reference.read_bytes()


def test_file_device_timeline_lines_up_with_its_input(tmp_path):
    x, payloads = two_messages()
    data = np.empty(2 * x.size, "<f4")
    data[0::2], data[1::2] = x.real, x.imag
    out = pyio.BytesIO()
    device = FileDevice(pyio.BytesIO(data.tobytes()), out)
    station, log = run_station(device)
    station.send(b"reply", rate=2400, at=(x.size - 19000) / 9600)  # in the silence at the end
    station.run()
    device.close()
    played = np.frombuffer(out.getvalue(), "<f4")
    assert played.size == data.size  # one output sample per input sample
    assert by_message(log) == list(payloads)
    reply = np.abs(played[0::2] + 1j * played[1::2]) > 1e-6
    assert np.flatnonzero(reply)[0] == pytest.approx(x.size - 19000, abs=960)


def test_the_transmit_clock_correction():
    """tx_ppm=+100 says the device plays 100 ppm fast, so the station makes 100 ppm more
    samples: played at the nominal rate here, they look like a transmitter 100 ppm slow."""
    device = VirtualDevice(duration=9600 * 12)
    station, log = run_station(device, tx_ppm=100.0)
    station.send(TEXT, rate=2400)
    station.run(until_idle=True)
    rx = Receiver()
    out = rx.process(device.output) + rx.finish()
    assert out == TEXT
    assert rx.messages[0].clock_ppm == pytest.approx(-100, abs=10)


def test_drive_level_and_clipping_are_reported():
    class Card(VirtualDevice):
        complex_output = False  # plays the real part, like a sound card

    def peak(drive_db, device_class):
        station, log = run_station(device_class(duration=9600 * 8), drive_db=drive_db)
        station.send(TEXT, rate=1200)
        station.run(until_idle=True)
        return next(e for e in log if isinstance(e, TxEnded))

    quiet, loud = peak(0.0, Card), peak(6.0, Card)
    assert quiet.clipped == 0 and quiet.peak_dbfs < -1.0
    assert loud.peak_dbfs == pytest.approx(quiet.peak_dbfs + 6.0, abs=0.01) and loud.clipped > 0


@pytest.mark.skipif(bool(os.environ.get("M110C_SKIP_TIMING")), reason="M110C_SKIP_TIMING set")
@pytest.mark.parametrize(("rate", "interleave"), [(2400, "short"), (600, "short"), (75, "long")])
def test_the_station_keeps_up_with_real_time(rate, interleave):
    """A station must process a second of signal in well under a second, or a sound card
    overruns. Measured 0.08-0.25 s per second of signal (docs/PERFORMANCE.md, P1); the
    budget allows for slower machines (set M110C_SKIP_TIMING on very slow ones)."""
    data = bytes(np.random.default_rng(0).integers(0, 256, rate, dtype=np.uint8))
    x = noisy(modulate(transmit_bytes(data, mode(rate, interleave)))[: 9600 * 6])
    station = Station(VirtualDevice(x))
    start = time.perf_counter()
    station.run()
    assert (time.perf_counter() - start) / (x.size / 9600) < 0.75


def test_a_sound_card_rate_is_received_at_9600():
    """At 48000 S/s the station's receiver works at 9600 (Receiver(working_rate=9600)): the
    same bytes, for a fraction of the cost while idle (docs/PERFORMANCE.md, P1)."""
    x, payloads = two_messages()
    x48 = Resampler(9600, 48000).process(x)
    station, log = run_station(VirtualDevice(x48, sample_rate=48000, block=4800))
    station.run()
    assert station.receiver.sample_rate == 9600 and station.receiver.input_rate == 48000
    assert by_message(log) == list(payloads)
    with pytest.raises(ValueError, match="working_rate"):
        Receiver(sample_rate=48000, working_rate=24000)
    assert Receiver(sample_rate=48000).sample_rate == 48000  # m110c-rx: unchanged


@pytest.mark.skipif(bool(os.environ.get("M110C_SKIP_TIMING")), reason="M110C_SKIP_TIMING set")
@pytest.mark.parametrize("late_entry", [False, True])
def test_a_sound_card_rate_keeps_up_while_idle(late_entry):
    """Idle at 48000 S/s, the search once cost 0.43 of a core (2.1 with late entry): more
    than a 2-core laptop's processor can spare. At 9600 inside: 0.06 (0.31)."""
    x = noisy(np.zeros(48000 * 6, complex), rms=0.02)
    station = Station(
        VirtualDevice(x, sample_rate=48000, block=4800), receiver_options={"late_entry": late_entry}
    )
    start = time.process_time()
    station.run()
    assert (time.process_time() - start) / 6 < 0.75
