"""An SDR as the station's radio: an upper-sideband transceiver in software, through
SoapySDR (PLAN.md P1; its Python bindings and the device's module, e.g. LimeSuite's or
SoapyRTLSDR, usually come from the operating system's packages).

The station works at 9600 S/s on the analytic signal of the audio band (the modulator's
output, carrier at 1800 Hz): exactly what an upper-sideband receiver hands a sound card,
without the sound card. The SDR is tuned ``if_hz`` below the dial frequency, so its own DC
spike and the leakage of its local oscillator stay clear of the signal:

- receive: the SDR's samples (at ``sdr_rate``, centred at dial - if_hz) are moved down by
  if_hz, so the dial frequency is at 0 Hz and the 300-3300 Hz audio band at 300-3300 Hz,
  low-passed and decimated to 9600 S/s (:class:`m110c.resample.Resampler`). The
  receiver's own filters keep only the band around its carrier, so what lies below the dial
  (the other sideband) does not reach it;
- transmit: the station's output is interpolated to ``sdr_rate`` and moved up by if_hz.
  The modulator's output has no negative frequencies, so this is a single sideband,
  above the dial.

Two threads move samples, as a sound card's callback does: one reads the SDR into an
:class:`InputRing`, the other writes the output queue to it. Keying is enabling the SDR's
transmitter: its stream is active only while there is something to send.

Transmit power: ``tx_mode`` "bench" (the default) uses the device's lowest transmit gain
and a digital level 20 dB down, for coax and attenuators into a receiver (docs/ONAIR.md,
bench D); "filtered" uses ``tx_gain`` and full level, for an SDR followed by a filter (and
an amplifier); "off" makes it receive only. See :func:`m110c.profile.check_sdr_transmit`.
"""

import threading
import time

import numpy as np

from .. import profile
from ..resample import Resampler
from .device import Device, InputRing

INSTALL_HINT = (
    "SDRs need SoapySDR's Python bindings and the device's SoapySDR module (e.g. the "
    "python3-soapysdr and soapysdr-module-lms7 or soapysdr-module-rtlsdr packages)"
)
STATION_RATE = 9600
BENCH_LEVEL_DB = -20.0  # digital level of a bench transmitter, below full scale
CHUNK_S = 0.05  # samples moved per call to the SDR


def _soapy():
    try:
        import SoapySDR
    except ImportError as exc:
        raise RuntimeError(INSTALL_HINT) from exc
    return SoapySDR


def list_sdrs() -> list[dict]:
    """The SDRs SoapySDR finds (each a dict of its arguments: driver, label, serial...)."""
    return [dict(found) for found in _soapy().Device.enumerate()]


class SdrDevice(Device):
    complex_output = True

    def __init__(
        self,
        args: str = "",
        dial_hz: float = 915e6,
        sdr_rate: int = 240000,
        if_hz: float | None = None,
        rx_gain: float | None = None,
        tx_gain: float | None = None,
        tx_mode: str = "bench",
        ppm: float = 0.0,
        channel: int = 0,
        ring_s: float = 5.0,
    ):
        """``args``: SoapySDR device arguments, e.g. "driver=lime" or "driver=rtlsdr".
        ``dial_hz``: the dial frequency (the suppressed carrier of the sideband; the signal
        occupies dial + 300 ... 3300 Hz). ``sdr_rate``: a multiple of 9600 the device
        supports (240000 suits an RTL-SDR; a LimeSDR may need 960000 or more). ``if_hz``:
        default a quarter of sdr_rate. ``ppm``: the SDR's frequency error, corrected in
        tuning (positive: its oscillator runs fast). ``rx_gain``: None for the device's
        automatic gain if it has one."""
        super().__init__()
        profile.check_sdr_transmit(tx_mode, tx_gain)
        if sdr_rate % STATION_RATE:
            raise ValueError(f"the SDR's rate must be a multiple of {STATION_RATE} S/s")
        soapy = _soapy()
        self._soapy = soapy
        self.sample_rate = STATION_RATE
        self.block = STATION_RATE // 10
        self.sdr_rate = sdr_rate
        self.if_hz = if_hz if if_hz is not None else sdr_rate / 4
        if not 3400 < self.if_hz < sdr_rate / 2 - 3400:
            raise ValueError("the IF must lie between 3.4 kHz and half the SDR's rate less that")
        self.dial_hz, self.ppm, self.channel = dial_hz, ppm, channel
        self.tx_mode = tx_mode
        self.can_transmit = tx_mode != "off"
        self.on_air = self.can_transmit  # an SDR can reach an antenna
        self._level = 10 ** (BENCH_LEVEL_DB / 20) if tx_mode == "bench" else 1.0
        self.sdr = soapy.Device(args)
        rx, tx = soapy.SOAPY_SDR_RX, soapy.SOAPY_SDR_TX
        centre = (dial_hz - self.if_hz) / (1 + ppm * 1e-6)
        directions = (rx, tx) if self.can_transmit else (rx,)
        for direction in directions:
            self.sdr.setSampleRate(direction, channel, sdr_rate)
            self.sdr.setFrequency(direction, channel, centre)
        if rx_gain is None:
            if self.sdr.hasGainMode(rx, channel):
                self.sdr.setGainMode(rx, channel, True)
        else:
            self.sdr.setGain(rx, channel, rx_gain)
        if self.can_transmit:
            gains = self.sdr.getGainRange(tx, channel)
            gain = gains.minimum() if tx_mode == "bench" or tx_gain is None else tx_gain
            self.sdr.setGain(tx, channel, gain)
            self.tx_gain = gain
        self._down = Resampler(sdr_rate, STATION_RATE)
        self._up = Resampler(STATION_RATE, sdr_rate)
        self._n_in = self._n_out = 0  # SDR-rate samples through each mixer (phase)
        self._ring = InputRing(int(ring_s * STATION_RATE))
        self._rx = self.sdr.setupStream(rx, soapy.SOAPY_SDR_CF32, [channel])
        self._tx = None
        if self.can_transmit:
            self._tx = self.sdr.setupStream(tx, soapy.SOAPY_SDR_CF32, [channel])
        self.keyed = False
        self.read_errors = 0
        self._running = True
        self.sdr.activateStream(self._rx)
        self._threads = [threading.Thread(target=self._receive, daemon=True)]
        if self.can_transmit:
            self._threads.append(threading.Thread(target=self._transmit, daemon=True))
        for thread in self._threads:
            thread.start()

    # -- frequency conversion ---------------------------------------------------------

    def _mix(self, x: np.ndarray, start: int, sign: float) -> np.ndarray:
        cycles = np.mod(self.if_hz * (start + np.arange(x.size)), self.sdr_rate) / self.sdr_rate
        return x * np.exp(sign * 2j * np.pi * cycles)

    def downconvert(self, x: np.ndarray) -> np.ndarray:
        """SDR samples -> the station's (dial at 0 Hz, 9600 S/s)."""
        y = self._mix(np.asarray(x, dtype=complex), self._n_in, -1.0)
        self._n_in += y.size
        return self._down.process(y)

    def upconvert(self, x: np.ndarray) -> np.ndarray:
        """The station's samples -> the SDR's (the dial if_hz above its centre)."""
        y = self._up.process(np.asarray(x, dtype=complex))
        out = self._mix(y, self._n_out, 1.0)
        self._n_out += out.size
        return out

    # -- threads ----------------------------------------------------------------------

    def _receive(self) -> None:
        n = int(CHUNK_S * self.sdr_rate)
        buffer = np.zeros(n, np.complex64)
        while self._running:
            result = self.sdr.readStream(self._rx, [buffer], n, timeoutUs=1_000_000)
            if result.ret > 0:
                self._ring.put(self.downconvert(buffer[: result.ret]))
            elif result.ret < 0:  # an overflow, a timeout: nothing to count it in samples
                self.read_errors += 1

    def _transmit(self) -> None:
        n = int(CHUNK_S * STATION_RATE)
        while self._running:
            if not (self.queued or self._out.expecting):
                if self.keyed:
                    self.sdr.deactivateStream(self._tx)
                    self.keyed = False
                    self._up = Resampler(STATION_RATE, self.sdr_rate)  # fresh for the next
                time.sleep(0.01)
                continue
            if not self.keyed:
                self.sdr.activateStream(self._tx)
                self.keyed = True
            y = self.upconvert(self._take_output(n) * self._level).astype(np.complex64)
            while y.size and self._running:
                result = self.sdr.writeStream(self._tx, [y], y.size, timeoutUs=1_000_000)
                if result.ret > 0:
                    y = y[result.ret :]

    # -- Device -----------------------------------------------------------------------

    def _read(self) -> np.ndarray | None:
        x = self._ring.get(self.block, timeout=2.0)
        if x is None:
            if self._ring.closed:
                return None
            raise RuntimeError("the SDR delivers no samples (unplugged, or stopped?)")
        self.stats.overruns = self._ring.dropped
        return x

    def write(self, x: np.ndarray) -> None:
        if not self.can_transmit:
            raise ValueError("this SDR is receive only (transmit mode off)")
        super().write(x)

    def interrupt(self) -> None:
        self._ring.close()

    def close(self) -> None:
        self.discard_output()
        self._running = False
        for thread in self._threads:
            thread.join(timeout=3)
        self._ring.close()
        if self._tx is not None:
            if self.keyed:
                self.sdr.deactivateStream(self._tx)
                self.keyed = False
            self.sdr.closeStream(self._tx)
        self.sdr.deactivateStream(self._rx)
        self.sdr.closeStream(self._rx)
