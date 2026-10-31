"""What the station needs from a sound card, a file or an SDR (PLAN.md P1).

A :class:`Device` is a source of input samples and a sink for output samples on one clock:
the device's own. Reading one block of input advances the device's time by one block, and
in that time it plays one block of output, from what has been queued with :meth:`write`,
or silence when nothing is queued. So everything the station does is counted in the
device's samples, never in wall-clock time:

- a sound card delivers input blocks as its converter produces them (in real time) and
  plays output from a buffer its own thread drains;
- a file or a virtual device produces each input block when asked, as fast as the program
  runs, and the result is exactly repeatable.

Samples are complex at the device's rate. A sound card's input is real (a zero imaginary
part); its output is the real part of what is written, the audio an upper-sideband
transmitter is fed (the modulator's analytic signal, carrier 1800 Hz).
"""

import threading
from dataclasses import dataclass

import numpy as np


@dataclass
class DeviceStats:
    """Counters a device keeps; the station reports them (and any change) as faults."""

    blocks_read: int = 0
    overruns: int = 0  # input samples lost because the program did not read them in time
    underruns: int = 0  # output samples played as silence in the middle of a transmission
    output_clipped: int = 0  # output samples beyond full scale (written, then clipped)


class Device:
    """Base class. Subclasses implement :meth:`_read` and :meth:`_play`."""

    sample_rate: int = 9600
    block: int = 960  # samples per read (0.1 s at 9600 S/s)
    # Does writing to this device put a signal on the air (a sound card driving a radio, an
    # SDR)? The amateur profile then refuses to transmit without a callsign (D1).
    on_air: bool = False
    can_transmit: bool = True  # False: a receive-only device (an SDR with transmit off)
    # Does the output keep both parts (an SDR, a cf32 file)? A sound card plays the real
    # part only, so only it can clip.
    complex_output: bool = True

    def __init__(self):
        self.stats = DeviceStats()
        self._out = OutputQueue()
        self.last_missing = 0

    # -- input --

    def read(self) -> np.ndarray | None:
        """The next block of input (complex), or None once the input has ended. In the same
        span of time the device plays one block of the output queue."""
        x = self._read()
        if x is None:
            return None
        self.stats.blocks_read += 1
        return x

    def _read(self) -> np.ndarray | None:
        raise NotImplementedError

    # -- output --

    def write(self, x: np.ndarray) -> None:
        """Queue samples to be played after those already queued."""
        x = np.asarray(x, dtype=complex)
        self.stats.output_clipped += int(np.count_nonzero(np.abs(x.real) > 1.0))
        self._out.put(x)

    def _take_output(self, n: int) -> np.ndarray:
        """The next ``n`` output samples to play (for subclasses), counting underruns."""
        x, missing = self._out.take(n)
        self.last_missing = missing  # (played as silence at the end of x)
        if missing and self._out.expecting:
            self.stats.underruns += missing
        return x

    def expect_output(self, expecting: bool) -> None:
        """The station says whether a transmission is under way (silence is then an
        underrun, not an idle channel)."""
        self._out.expecting = expecting

    def discard_output(self) -> None:
        """Drop the output not yet played (a sound card then plays silence)."""
        self._out.clear()

    @property
    def queued(self) -> int:
        """Output samples written and not yet played."""
        return self._out.size

    def interrupt(self) -> None:
        """End the input now (any thread): a read waiting for it returns None. The station
        calls this when asked to stop."""

    def close(self) -> None:
        """Stop the device; queued output that has not been played is discarded."""


class OutputQueue:
    """Samples waiting to be played, taken in blocks of any size (thread-safe: a sound
    card's thread takes while the station's puts)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pieces: list[np.ndarray] = []
        self._size = 0
        self.expecting = False  # a transmission is under way: running dry is an underrun

    @property
    def size(self) -> int:
        return self._size

    def put(self, x: np.ndarray) -> None:
        if x.size:
            with self._lock:
                self._pieces.append(x)
                self._size += x.size

    def take(self, n: int) -> tuple[np.ndarray, int]:
        """``n`` samples to play, padded with silence; also how many were missing."""
        out = np.zeros(n, dtype=complex)
        got = 0
        with self._lock:
            while got < n and self._pieces:
                piece = self._pieces[0]
                k = min(n - got, piece.size)
                out[got : got + k] = piece[:k]
                got += k
                if k == piece.size:
                    self._pieces.pop(0)
                else:
                    self._pieces[0] = piece[k:]
            self._size -= got
        return out, n - got

    def clear(self) -> None:
        with self._lock:
            self._pieces, self._size = [], 0


class InputRing:
    """Input samples between a sound card's thread (which puts them) and the station (which
    reads them in blocks).

    When the station falls behind by more than ``capacity`` samples, the oldest are dropped
    and counted (an overrun) and *replaced by zeros*: the reader still gets every sample
    position, so the receiver's time base survives and it sees a short gap, like a fade,
    instead of a jump in time. A jump of 0.1 s lost the rest of a message at every rate but
    600L; the same gap as zeros cost at most the bytes under it (docs/PERFORMANCE.md, P1).
    A run of zeros is kept as a count, so memory stays bounded. The station keeps up on
    average (the receiver takes 0.1-0.4 of real time, PERFORMANCE.md M12), so a few seconds
    of capacity absorb its slow moments."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._cond = threading.Condition()
        self._pieces: list = []  # arrays of samples, or ints: runs of zeros
        self._size = 0  # sample positions held (zeros included)
        self._real = 0  # actual samples held
        self.dropped = 0
        self.closed = False

    def put(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=complex)
        with self._cond:
            self._pieces.append(x)
            self._size += x.size
            self._real += x.size
            i = 0
            while self._real > self.capacity:  # drop the oldest real samples
                piece = self._pieces[i]
                if isinstance(piece, int):
                    i += 1
                    continue
                k = min(self._real - self.capacity, piece.size)
                self._pieces[i : i + 1] = [k] + ([piece[k:]] if k < piece.size else [])
                self._real -= k
                self.dropped += k
            self._merge_zeros()
            self._cond.notify_all()

    def _merge_zeros(self) -> None:
        merged = []
        for piece in self._pieces:
            if isinstance(piece, int) and merged and isinstance(merged[-1], int):
                merged[-1] += piece
            else:
                merged.append(piece)
        self._pieces = merged

    def get(self, n: int, timeout: float | None = None) -> np.ndarray | None:
        """``n`` sample positions (zeros where samples were dropped), waiting for them;
        None if closed (or timed out) first."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._size >= n or self.closed, timeout):
                return None
            if self._size < n:
                return None
            out = np.zeros(n, dtype=complex)
            got = 0
            while got < n:
                piece = self._pieces[0]
                size = piece if isinstance(piece, int) else piece.size
                k = min(n - got, size)
                if not isinstance(piece, int):
                    out[got : got + k] = piece[:k]
                    self._real -= k
                got += k
                if k == size:
                    self._pieces.pop(0)
                else:
                    self._pieces[0] = piece - k if isinstance(piece, int) else piece[k:]
            self._size -= n
            return out

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()
