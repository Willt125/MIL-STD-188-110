"""A device on files and pipes, in the formats of :mod:`m110c.io`.

Input comes from a file or pipe (or is silence, with no input); output goes to a file or
pipe as a timeline: one block of output, silence included, for each block of input, so
the two files line up sample for sample. With ``idle="skip"`` only the transmissions are
written, back to back, as ``m110c-tx`` would. Nothing here runs in real time: a file is
read as fast as the station processes it.
"""

import numpy as np

from .. import io
from .device import Device


class FileDevice(Device):
    def __init__(
        self,
        infile=None,
        outfile=None,
        fmt: str = "cf32",
        out_fmt: str | None = None,
        sample_rate: int = 9600,
        block: int | None = None,
        channels: int = 1,
        idle: str = "silence",
    ):
        """``infile``/``outfile``: binary streams (None: no input, silence; no output).
        ``channels``: interleaved input channels, averaged. ``idle``: "silence" writes the
        output timeline, "skip" only the transmissions."""
        super().__init__()
        if idle not in ("silence", "skip"):
            raise ValueError(f"idle must be silence or skip, not {idle!r}")
        self.sample_rate = sample_rate
        self.block = block or sample_rate // 10
        self.fmt, self.channels, self.idle = fmt, channels, idle
        self.complex_output = (out_fmt or fmt) in io.COMPLEX_FORMATS
        self._in = infile
        self._frame = io.bytes_per_sample(fmt) * channels
        self._leftover = b""
        self._writer = None
        if outfile is not None:
            self._writer = io.SampleWriter(outfile, out_fmt or fmt, sample_rate)

    def _read(self) -> np.ndarray | None:
        n = self.block
        if self._in is None:
            x = np.zeros(n, complex)
        else:
            want = n * self._frame - len(self._leftover)
            data = self._leftover + (self._in.read(want) if want > 0 else b"")
            usable = len(data) - len(data) % self._frame
            self._leftover = data[usable:]
            if not usable:
                return None
            x = io.decode_samples(data[:usable], self.fmt)
            if self.channels > 1:
                x = x.reshape(-1, self.channels).mean(axis=1)
        played = self._take_output(x.size)
        if self._writer is not None:
            if self.idle == "silence":
                self._writer.write(played)
            else:  # what was written, without the silence that pads a block
                self._writer.write(played[: x.size - self.last_missing])
        return x

    def close(self) -> None:
        if self._writer is not None:
            if self.idle == "skip" and self.queued:  # what remains of a transmission
                self._writer.write(self._out.take(self.queued)[0])
            self._writer.close()
            self._writer = None

    @property
    def clipped(self) -> int:
        return self._writer.clipped if self._writer is not None else 0
