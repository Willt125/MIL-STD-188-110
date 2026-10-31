"""A device made of arrays, for tests and experiments: exactly repeatable, as fast as the
program runs.

Its input is a given signal (then silence), optionally with the device's own output added
back after a delay (a loopback cable) and noise. Its output is recorded on the same
timeline as the input: block k of :attr:`VirtualDevice.output` was played while block k of
input was being received. Blocks can be dropped or repeated on purpose, to see what a sound
card's hiccups do to the station.
"""

import numpy as np

from .device import Device


class VirtualDevice(Device):
    def __init__(
        self,
        signal: np.ndarray | None = None,
        sample_rate: int = 9600,
        block: int = 960,
        duration: int | None = None,
        loopback_delay: int | None = None,
        noise_rms: float = 0.0,
        seed: int = 0,
        drop_blocks=(),
        repeat_blocks=(),
        gap_blocks=(),
        on_air: bool = False,
    ):
        """``duration``: input samples before the input ends (default: the signal's length,
        rounded up to whole blocks; endless without a signal). ``loopback_delay``: add the
        output back into the input this many samples later. ``drop_blocks`` and
        ``repeat_blocks``: indices of input blocks lost (the next one is delivered in their
        place: a jump in time) or delivered twice (the device's buffer replayed).
        ``gap_blocks``: blocks lost and counted as an overrun, delivered as zeros (what the
        station's input ring does when it drops samples: the time base is kept)."""
        super().__init__()
        self.sample_rate, self.block, self.on_air = sample_rate, block, on_air
        self.signal = np.zeros(0, complex) if signal is None else np.asarray(signal, complex)
        if duration is None and signal is not None:
            duration = -(-self.signal.size // block) * block
        self.duration = duration
        self.loopback_delay = loopback_delay
        self.noise_rms = noise_rms
        self._rng = np.random.default_rng(seed)
        self.drop_blocks, self.repeat_blocks = set(drop_blocks), set(repeat_blocks)
        self.gap_blocks = set(gap_blocks)
        self._played: list[np.ndarray] = []
        self._loop = np.zeros(0, complex)  # output still to come round the loopback
        self._position = 0  # input samples produced
        self._index = 0  # input blocks produced
        self._last = None

    @property
    def output(self) -> np.ndarray:
        """Everything played so far, silence included, on the input's timeline."""
        return np.concatenate(self._played) if self._played else np.zeros(0, complex)

    @property
    def time(self) -> float:
        return self._position / self.sample_rate

    def _next(self) -> np.ndarray | None:
        n = self.block
        if self.duration is not None and self._position >= self.duration:
            return None
        played = self._take_output(n)
        self._played.append(played)
        x = np.zeros(n, complex)
        piece = self.signal[self._position : self._position + n]
        x[: piece.size] = piece
        if self.loopback_delay is not None:
            if not self._loop.size:
                self._loop = np.zeros(self.loopback_delay, complex)
            self._loop = np.concatenate([self._loop, played])
            x += self._loop[:n]
            self._loop = self._loop[n:]
        if self.noise_rms:
            z = self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n)
            x += self.noise_rms / np.sqrt(2) * z
        self._position += n
        return x

    def _read(self) -> np.ndarray | None:
        index = self._index
        self._index += 1
        if index in self.repeat_blocks and self._last is not None:
            return self._last.copy()  # the same samples again; the device's time moves on
        x = self._next()
        if index in self.drop_blocks and x is not None:
            x = self._next()  # this block was lost; the next one arrives in its place
        if index in self.gap_blocks and x is not None:
            self.stats.overruns += x.size
            x = np.zeros_like(x)
        self._last = x
        return x
