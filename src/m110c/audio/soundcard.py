"""A sound card, through PortAudio (the ``sounddevice`` package: ``pip install m110c[audio]``).

The card runs its own clock and calls :meth:`SoundCard._callback` from its own thread every
block: that thread only moves samples, input into an :class:`InputRing` and output out of
the output queue, so it never waits on the program. The station reads the ring at its own
pace; if it falls more than ``ring_s`` behind, the oldest input is dropped and replaced by
zeros (an overrun), which keeps the receiver's time base (docs/PERFORMANCE.md, P1).

Input: one channel of the card (``input_channel``), real. Output: the real part of what is
written (the audio an upper-sideband transmitter is fed), on every output channel, clipped
at full scale.
"""

import numpy as np

from .device import Device, InputRing

INSTALL_HINT = "sound cards need the sounddevice package: pip install 'm110c[audio]'"


def _sounddevice():
    try:
        import sounddevice
    except ImportError as exc:  # also OSError when PortAudio itself is missing
        raise RuntimeError(INSTALL_HINT) from exc
    except OSError as exc:
        raise RuntimeError(f"{INSTALL_HINT} (PortAudio: {exc})") from exc
    return sounddevice


def list_devices() -> str:
    """The sound devices PortAudio sees, as ``sounddevice`` lists them."""
    return str(_sounddevice().query_devices())


class SoundCard(Device):
    on_air = True  # it drives a radio
    complex_output = False

    def __init__(
        self,
        device=None,
        sample_rate: int = 48000,
        block_s: float = 0.1,
        input_channel: int = 0,
        ring_s: float = 5.0,
        latency="high",
    ):
        """``device``: a name or index as ``list_devices`` shows (one for both directions,
        or a pair (input, output)); None for the system's default. ``latency``: PortAudio's
        suggestion, "high" for fewer dropouts (the station keeps a second of output queued
        anyway)."""
        super().__init__()
        sd = _sounddevice()
        self.sample_rate = sample_rate
        self.block = int(round(block_s * sample_rate))
        self.input_channel = input_channel
        self._ring = InputRing(int(ring_s * sample_rate))
        self.driver_overflows = 0  # PortAudio's own reports (the count of samples unknown)
        self.driver_underflows = 0
        inputs = self._channels(sd, device, "input")
        if input_channel >= inputs:
            raise ValueError(f"input channel {input_channel} of a {inputs}-channel input")
        self._stream = sd.Stream(
            samplerate=sample_rate,
            blocksize=0,  # whatever suits the driver: the ring evens it out
            device=device,
            channels=(inputs, self._channels(sd, device, "output")),
            dtype="float32",
            latency=latency,
            callback=self._callback,
        )
        self._stream.start()

    @staticmethod
    def _channels(sd, device, kind: str) -> int:
        """Channels to open: the input one is chosen later; output goes to all (at most 2)."""
        which = device[0 if kind == "input" else 1] if isinstance(device, (tuple, list)) else device
        info = sd.query_devices(which, kind)
        return max(1, min(int(info[f"max_{kind}_channels"]), 2))

    def _callback(self, indata, outdata, frames, time, status) -> None:
        """PortAudio's thread: move one block in and one block out, nothing more."""
        if status.input_overflow:
            self.driver_overflows += 1
        if status.output_underflow:
            self.driver_underflows += 1
        self._ring.put(np.asarray(indata[:, self.input_channel], dtype=float).astype(complex))
        x = self._take_output(frames)
        outdata[:] = np.clip(x.real, -1.0, 1.0)[:, None]

    def _read(self) -> np.ndarray | None:
        x = self._ring.get(self.block, timeout=max(2.0, 4 * self.block / self.sample_rate))
        if x is None:
            if self._ring.closed:
                return None
            raise RuntimeError("the sound card delivers no input (stopped, or unplugged?)")
        self.stats.overruns = self._ring.dropped
        return x

    def interrupt(self) -> None:
        self._ring.close()

    def close(self) -> None:
        self.discard_output()
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._ring.close()
