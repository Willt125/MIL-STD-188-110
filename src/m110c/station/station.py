"""The station: one device, a receiver that never stops, and a transmitter fed from a queue
(PLAN.md P1).

Two threads, so that receiving never delays transmitting:

- the *pump* (the thread that calls :meth:`Station.run`) reads the device block by block,
  which is what moves time on; after each block it keeps the device's output topped up
  from the transmission in progress (``lead_s`` ahead), starts the next queued one, and
  passes the block on to the receiver;
- the *receiver thread* demodulates and decodes, and reports messages and data.

Everything is counted in the device's samples, so on a file or a virtual device the whole
station is exactly repeatable: what is received depends only on the samples read, and when
a transmission starts depends only on which block it was queued before.

Half duplex, like a radio: while the station transmits, its receiver hears silence (the
blocks are replaced by zeros, keeping the receiver's time base). ``full_duplex=True`` lets
it hear its own signal, for a loopback cable.
"""

import itertools
import queue
import threading

import numpy as np

from .. import framing
from .. import profile as profiles
from ..modulate import Modulator
from ..resample import FractionalResampler
from ..rx.receiver import Receiver
from ..spec import mode as mode_of
from ..tx import Transmitter
from . import events as ev

RECEIVE_RATE = 9600  # the rate the station's receiver works at, whatever the device's
DEFAULT_RMS = 0.4  # the modulator's level at drive 0 dB: about 3 dB of headroom (io.py)


class Station:
    def __init__(
        self,
        device,
        *,
        profile: profiles.Profile = profiles.DEFAULT,
        callsign: str | None = None,
        rolloff: float | None = None,
        carrier_hz: float = 1800.0,
        drive_db: float = 0.0,
        tx_ppm: float = 0.0,
        full_duplex: bool = False,
        lead_s: float = 1.0,
        level_interval_s: float = 1.0,
        receiver_options: dict | None = None,
    ):
        """``drive_db``: the transmit level relative to the modulator's default (0.4 RMS;
        more than about +2 dB clips the peaks of a real output). ``tx_ppm``: the device's
        output clock error in parts per million, positive when it runs fast (it plays more
        samples per second than it says); the transmitter makes that many more samples per
        second, so what goes out has the right symbol rate and carrier (110C asks for
        +/-10 ppm; sound cards are often tens of ppm off). ``lead_s``: how far ahead
        of the device the transmitter keeps its output queued; it absorbs slow moments of
        this program (a sound card plays silence if the queue runs dry mid-transmission,
        an underrun). ``receiver_options``: passed to :class:`Receiver` (the sample rate
        and roll-off are set here)."""
        self.device = device
        self.profile = profile
        self.callsign = callsign
        self.rolloff = profiles.check_rolloff(profile, rolloff)
        self.carrier_hz = carrier_hz
        self.drive_db = drive_db
        self.tx_ppm = tx_ppm
        self.full_duplex = full_duplex
        self.rate = device.sample_rate
        # At least two blocks: each read plays a whole block from the queue.
        self.lead = max(int(lead_s * self.rate), 2 * device.block)
        self.level_interval = int(level_interval_s * self.rate)
        options = dict(receiver_options or {})
        options.setdefault("carrier_hz", carrier_hz)
        # A sound card's 48000 S/s is brought down to 9600 before the receiver: searching at
        # the card's rate cost 8 times as much while idle (docs/PERFORMANCE.md, P1).
        options.setdefault("working_rate", RECEIVE_RATE)
        self.receiver = Receiver(sample_rate=self.rate, rolloff=self.rolloff, **options)
        self._callbacks = []
        self._callback_lock = threading.Lock()
        self._requests: queue.SimpleQueue = queue.SimpleQueue()
        self._waiting: list[ev.TxRequest] = []
        self._numbers = itertools.count(1)  # (next() on it is safe from any thread)
        self._tx = None  # (request, sample generator, first sample's time) in progress
        self._out_peak, self._out_clipped = 0.0, 0  # of the transmission in progress
        self._samples = 0  # input samples read: the device's clock
        self._stop = threading.Event()
        self._level = [0.0, 0.0, 0, 0]  # sum of squares, peak, clipped, samples
        self._faults = {"overrun": 0, "underrun": 0}
        # Receiver thread's bookkeeping: messages reported, bytes reported per message.
        self._seen = 0
        self._reported: dict[int, int] = {}
        self._announced: set[int] = set()

    # -- public ------------------------------------------------------------------------

    @property
    def time(self) -> float:
        """Seconds of the device's clock so far."""
        return self._samples / self.rate

    @property
    def transmitting(self) -> bool:
        return self._tx is not None

    def on(self, callback) -> None:
        """Call ``callback(event)`` for every event (from the station's threads, one at a
        time; it should return quickly)."""
        self._callbacks.append(callback)

    def send(
        self,
        data: bytes,
        rate: int = 1200,
        interleave: str = "short",
        async_format=None,
        msb_first: bool = False,
        voice: bool = False,
        at: float | None = None,
    ) -> ev.TxRequest:
        """Queue ``data`` as one 110 message (any thread). Raises ValueError for a mode that
        does not exist or a transmission the profile does not allow."""
        if not self.device.can_transmit:
            raise ValueError("the device is receive only")
        profiles.check_transmit(self.profile, self.callsign, self.device.on_air)
        if voice and async_format is not None:
            raise ValueError("voice sends a bit stream; async framing does not apply")
        m = mode_of(rate, interleave, voice=voice)
        number = next(self._numbers)
        request = ev.TxRequest(bytes(data), m, async_format, msb_first, at, number)
        self._requests.put(request)
        return request

    def stop(self) -> None:
        """Ask :meth:`run` to return (any thread). The device's input ends with it."""
        self._stop.set()
        self.device.interrupt()

    def run(self, until: float | None = None, until_idle: bool = False, linger: float = 2.0):
        """Pump the device until its input ends, :meth:`stop` is called, the device's time
        reaches ``until`` seconds, or (``until_idle``) nothing is queued or being sent and
        ``linger`` seconds have passed since the last transmission ended (time for the
        receiver to finish what it heard). The receiver then finishes what it holds."""
        blocks: queue.Queue = queue.Queue(maxsize=max(4, int(30 * self.rate / self.device.block)))
        worker = threading.Thread(target=self._receive_loop, args=(blocks,), daemon=True)
        worker.start()
        idle_since = 0.0
        try:
            while not self._stop.is_set():
                if until is not None and self.time >= until:
                    break
                muted = self.transmitting and not self.full_duplex
                x = self.device.read()
                if x is None:
                    break
                self._samples += x.size
                self._check_faults()
                self._transmit_step()
                if muted:
                    x = np.zeros_like(x)
                else:
                    self._note_level(x)
                blocks.put(x)
                if until_idle:
                    busy = self.transmitting or self._waiting or not self._requests.empty()
                    if busy:
                        idle_since = self.time
                    elif self.time - idle_since >= linger:
                        break
        finally:
            self._abort()
            blocks.put(None)
            worker.join()

    def _abort(self) -> None:
        """Stopping: nothing more is played, so the transmitter unkeys at once; what was
        still queued is dropped and a transmission in progress is reported cut short."""
        self.device.expect_output(False)
        self.device.discard_output()
        if self._tx is not None:
            request, _, started = self._tx
            seconds = self.time - started if started is not None else 0.0
            self._tx = None
            peak = _dbfs(self._out_peak)
            self._emit(ev.TxEnded(self.time, request, seconds, False, peak, self._out_clipped))

    # -- transmitting (the pump's thread) ---------------------------------------------

    def _transmit_step(self) -> None:
        while not self._requests.empty():
            self._waiting.append(self._requests.get())
        if self._tx is None:
            ready = [r for r in self._waiting if r.at is None or r.at <= self.time]
            if not ready:
                return
            request = ready[0]
            self._waiting.remove(request)
            self._tx = [request, self._samples_of(request), None]
            self._out_peak, self._out_clipped = 0.0, 0
        request, samples, started = self._tx
        while samples is not None and self.device.queued < self.lead:
            chunk = next(samples, None)
            if chunk is None:
                samples = self._tx[1] = None
                self.device.expect_output(False)
                break
            if started is None:
                # It plays from the next block on (this block's output is already decided).
                started = self._tx[2] = self.time + self.device.queued / self.rate
                self._emit(ev.TxStarted(self.time, request))
            self._note_output(chunk)
            self.device.write(chunk)
            # Only now: a sound card's thread may play a block at any moment, and silence
            # before the first samples is not an underrun.
            self.device.expect_output(True)
        if samples is None and self.device.queued == 0:
            seconds = self.time - (started if started is not None else self.time)
            self._tx = None
            peak = _dbfs(self._out_peak)
            self._emit(ev.TxEnded(self.time, request, seconds, True, peak, self._out_clipped))

    def _note_output(self, x: np.ndarray) -> None:
        a = np.abs(x.real)
        if self.device.complex_output:
            a = np.maximum(a, np.abs(x.imag))
        self._out_peak = max(self._out_peak, float(a.max(initial=0.0)))
        self._out_clipped += int(np.count_nonzero(a > 1.0))

    def _samples_of(self, request: ev.TxRequest):
        """The transmission's samples, about a second at a time (memory stays bounded at
        every rate, as in m110c-tx)."""
        m = request.mode
        tx = Transmitter(m)
        modulator = Modulator(
            sample_rate=self.rate,
            carrier_hz=self.carrier_hz,
            rolloff=self.rolloff,
            rms=DEFAULT_RMS * 10 ** (self.drive_db / 20),
        )
        clock = FractionalResampler(1 + self.tx_ppm * 1e-6, self.rate) if self.tx_ppm else None
        if request.async_format is not None:
            bits = framing.async_encode(request.data, request.async_format)
        else:
            bits = framing.sync_encode(request.data, msb_first=request.msb_first)
        for i in range(0, bits.size, m.rate):
            x = modulator.process(tx.process(bits[i : i + m.rate]))
            x = clock.process(x) if clock is not None else x
            if x.size:
                yield x
        x = np.concatenate([modulator.process(tx.finish()), modulator.flush()])
        yield np.concatenate([clock.process(x), clock.flush()]) if clock is not None else x

    # -- receiving (the receiver thread) ------------------------------------------------

    def _receive_loop(self, blocks: queue.Queue) -> None:
        position = 0
        while True:
            x = blocks.get()
            if x is None:
                break
            position += x.size
            self._report(self.receiver.process(x), position / self.rate)
        self._report(self.receiver.finish(), position / self.rate)

    def _report(self, out: bytes, time: float) -> None:
        """Split the receiver's output among the messages it belongs to (each message's
        n_bytes grows as its bytes come out, in order) and report starts and ends."""
        r = self.receiver
        ended = r.messages[self._seen :]
        current = [r.receiving] if r.receiving is not None else []
        at = 0
        for msg in ended + current:
            key = id(msg)  # (messages stay referenced, so ids are not reused)
            if key not in self._announced:
                self._announced.add(key)
                self._emit(ev.MessageStarted(time, msg))
            new = msg.n_bytes - self._reported.get(key, 0)
            if new:
                self._emit(ev.Received(time, msg, out[at : at + new]))
                at += new
                self._reported[key] = msg.n_bytes
        for msg in ended:
            self._emit(ev.MessageEnded(time, msg))
        self._seen = len(r.messages)

    # -- reports ------------------------------------------------------------------------

    def _emit(self, event) -> None:
        with self._callback_lock:
            for callback in self._callbacks:
                callback(event)

    def _note_level(self, x: np.ndarray) -> None:
        a = np.abs(x)
        level = self._level
        level[0] += float(np.sum(a * a))
        level[1] = max(level[1], float(a.max(initial=0.0)))
        level[2] += int(np.count_nonzero((np.abs(x.real) >= 0.999) | (np.abs(x.imag) >= 0.999)))
        level[3] += x.size
        if level[3] >= self.level_interval:
            rms = np.sqrt(level[0] / level[3])
            self._emit(ev.Level(self.time, _dbfs(rms), _dbfs(level[1]), level[2]))
            self._level = [0.0, 0.0, 0, 0]

    def _check_faults(self) -> None:
        stats = self.device.stats
        for kind, count in (("overrun", stats.overruns), ("underrun", stats.underruns)):
            if count > self._faults[kind]:
                self._emit(ev.Fault(self.time, kind, count - self._faults[kind]))
                self._faults[kind] = count


def _dbfs(x: float) -> float:
    return 20 * np.log10(x) if x > 0 else -np.inf
