"""Tracking demodulator: from a detected preamble, follow timing, carrier and gain to the end.

The genie receiver (M7) is *told* where symbols are and what the channel does. A real
receiver must keep estimating, because the transmitter's clock and carrier never exactly
match the receiver's: measured recordings drift by up to ~900 ppm (docs/VALIDATION.md).

How it works
    The signal is mixed down at the acquired carrier and passed through the matched filter.
    The full-rate output u[n] is kept, so each symbol can be read at a *fractional* sample
    time by cubic (4-point Lagrange) interpolation; u is band-limited to 1500 Hz and
    sampled at least 4 times per symbol, which makes that interpolation accurate.

    Symbols are processed in *units* that end in known symbols:

    * the rest of the preamble, one 32-symbol slot at a time (every symbol is known: the
      counts run down to zero, so the remaining segments are fully predictable),
    * in the data phase, one frame at a time (unknown symbols, then a known probe),
    * at 75 bps, one 32-symbol set at a time (no probes, so the set is first *decided*
      and then treated as known: decision-directed tracking).

    From each unit's known symbols a_k and received samples y_k:

    * gain:   g = sum y_k conj(a_k) / sum |a_k|^2 (least squares). Data symbols are
              divided by the gain interpolated between the previous unit and this one.
    * timing: |gain| is re-measured half a sample early and late; a parabola through the
              three values gives the timing error e (samples). A proportional-integral loop
              applies it: the next symbol time moves by Kp*e, and the symbol period by
              Ki*e/n, which learns a constant clock-rate offset with no steady-state error.
    * carrier: the phase advance of g from one unit to the next is residual frequency; it
              updates a per-symbol phase rotation (a first-order loop).

    The quality of each unit (energy in the gain against the rest, i.e. the SNR on the
    known symbols) is watched. Several bad units in a row mean the signal is gone. Output
    is held back by that many units, so noise after a vanished signal is never decoded.

Output: normalised data-phase symbols (on the unit circle when noiseless), in the form
:class:`~m110c.rx.decode.DataDecoder` expects, including probes.
"""

from collections import deque

import numpy as np

from .. import scrambler, symbols
from ..modulate import rrc_taps
from ..spec import tables as T
from ..spec.modes import ModeSpec
from . import demap
from .acquire import SLOT_TRIBITS, Detection

LOSS_UNITS = 3  # consecutive bad units that mean "signal lost"
# A unit is bad below this SNR on its known symbols. Noise alone measures about 1/n
# (-12 dB for a 16-symbol probe, -15 dB for a 32-symbol slot), while the weakest
# decodable signal (75 bps at -6 dB in 3 kHz) is about -5 dB per symbol.
LOSS_SNR_DB = -9.0


def interpolate(u: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Cubic Lagrange interpolation of ``u`` at fractional indices ``t`` (needs t-1..t+2)."""
    i = np.floor(t).astype(int)
    mu = t - i
    um1, u0, u1, u2 = u[i - 1], u[i], u[i + 1], u[i + 2]
    c_m1 = -mu * (mu - 1) * (mu - 2) / 6
    c_0 = (mu + 1) * (mu - 1) * (mu - 2) / 2
    c_1 = -(mu + 1) * mu * (mu - 2) / 2
    c_2 = (mu + 1) * mu * (mu - 1) / 6
    return c_m1 * um1 + c_0 * u0 + c_1 * u1 + c_2 * u2


class TrackingDemodulator:
    def __init__(
        self,
        mode: ModeSpec,
        detection: Detection,
        sample_rate: int = 9600,
        rolloff: float = 0.25,
        start_index: int = 0,
        kp: float = 0.4,
        ki: float = 0.1,
        kf: float = 0.2,
    ):
        """``start_index`` is the global index of the first sample that will be fed in;
        it must be at or before the detected segment start."""
        self.mode = mode
        self.sample_rate = sample_rate
        self.sps = sample_rate // T.SYMBOL_RATE
        self.carrier_hz = detection.carrier_hz
        self.kp, self.ki, self.kf = kp, ki, kf
        self.taps = rrc_taps(self.sps, rolloff)
        delay = (self.taps.size - 1) // 2
        # Matched-filter state. u[n] (global n) uses mixed inputs n - L + 1 .. n.
        self._history = np.zeros(self.taps.size - 1, dtype=complex)
        self._samples_in = start_index
        self._u = np.zeros(0, dtype=complex)
        self._u_start = start_index
        # Timing model: next symbol at fractional sample time t_next, period T samples.
        self.t_next = detection.segment_start + 2 * delay  # matched-filter peak of symbol 0
        self.period = float(self.sps)
        # Carrier: residual phase rotation applied per symbol.
        self.phase = 0.0
        self.dphase = 0.0
        self.gain = None  # last unit's complex gain
        self._smoothed_gain = None  # 75 bps: gain learned from previous sets
        # What comes next: the rest of the preamble (known), then the data phase.
        remaining = detection.data_start_symbol
        pre = symbols.preamble_tribits(mode)[-remaining:]
        self._training = np.exp(
            1j * np.pi / 4 * scrambler.scramble(pre, scrambler.sync_sequence(0, pre.size))
        )
        self._training_done = 0
        self._data_symbols = 0  # data-phase symbols produced (scrambler position)
        self._frames = 0
        self._sets = 0
        # Quality and loss handling.
        self.unit_snr_db: list[float] = []
        self.lost = False
        self._bad_run = 0
        self._held: deque[np.ndarray] = deque()

    # -- public ------------------------------------------------------------------------

    @property
    def in_data_phase(self) -> bool:
        return self._training_done >= self._training.size

    @property
    def position(self) -> int:
        """Global sample index the demodulator has reached (for resuming a search)."""
        return int(self.t_next)

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Accept raw samples; return normalised data-phase symbols that are settled."""
        self._filter(np.asarray(samples))
        out = []
        while not self.lost:
            unit = self._next_unit_length()
            needed = self.t_next + (unit - 1) * self.period + 3  # interpolation + early/late
            if needed >= self._u_start + self._u.size:
                break
            produced = self._unit(unit)
            if produced is not None:
                out.append(produced)
            self._trim()
        return np.concatenate(out) if out else np.zeros(0, dtype=complex)

    def finish(self) -> np.ndarray:
        """End of input: release the held-back symbols, except trailing bad units."""
        out = self._release_good()
        return out if out is not None else np.zeros(0, dtype=complex)

    # -- matched filter ---------------------------------------------------------------

    def _filter(self, x: np.ndarray) -> None:
        n = self._samples_in + np.arange(x.size)
        self._samples_in += x.size
        cycles = np.mod(self.carrier_hz * n, self.sample_rate) / self.sample_rate
        mixed = np.concatenate([self._history, x * np.exp(-2j * np.pi * cycles)])
        self._history = mixed[mixed.size - self._history.size :]
        self._u = np.concatenate([self._u, np.convolve(mixed, self.taps[::-1], mode="valid")])

    def _trim(self) -> None:
        keep_from = int(np.floor(self.t_next)) - 4
        drop = max(0, keep_from - self._u_start)
        self._u = self._u[drop:]
        self._u_start += drop

    def _sample(self, t: np.ndarray) -> np.ndarray:
        return interpolate(self._u, t - self._u_start)

    # -- units ------------------------------------------------------------------------

    def _next_unit_length(self) -> int:
        if not self.in_data_phase:
            return min(SLOT_TRIBITS, self._training.size - self._training_done)  # one slot
        if self.mode.is_75bps:
            return T.TRIBITS_PER_75BPS_SET
        return self.mode.frame_symbols

    def _unit(self, n: int) -> np.ndarray | None:
        training = not self.in_data_phase  # decided before the unit is consumed
        k = np.arange(n)
        times = self.t_next + k * self.period
        rotation = np.exp(-1j * (self.phase + k * self.dphase))
        y = self._sample(times) * rotation

        if training:
            known_mask = np.ones(n, dtype=bool)
            known = self._training[self._training_done : self._training_done + n]
            self._training_done += n
        elif self.mode.is_75bps:
            known_mask = np.ones(n, dtype=bool)
            known = self._decide_set(y)
        else:
            known_mask = np.zeros(n, dtype=bool)
            known_mask[self.mode.unknown_per_frame :] = True
            known = self._probe_symbols()

        gain = np.vdot(known, y[known_mask]) / np.sum(np.abs(known) ** 2)
        residual = y[known_mask] - gain * known
        signal_energy = abs(gain) ** 2 * np.sum(np.abs(known) ** 2)
        noise_energy = float(np.sum(np.abs(residual) ** 2))
        if signal_energy <= 1e-12 * max(noise_energy, 1e-30) or signal_energy == 0:
            # No usable signal (e.g. silence after the transmission): record a bad unit and
            # leave every loop untouched, so nothing becomes NaN.
            self.unit_snr_db.append(float("-inf"))
            self.t_next = times[-1] + self.period
            if training:
                return self._hold(None, bad=True)
            return self._hold(self._advance_counters(y), bad=True)
        snr = 10 * np.log10(signal_energy / max(noise_energy, 1e-30))
        self.unit_snr_db.append(float(snr))

        # Timing error from early/late re-measurement of |gain| on the known symbols.
        def gain_at(offset):
            yy = self._sample(times[known_mask] + offset) * rotation[known_mask]
            return abs(np.vdot(known, yy))

        early, centre, late = gain_at(-0.5), abs(gain) * np.sum(np.abs(known) ** 2), gain_at(0.5)
        denom = early - 2 * centre + late
        error = 0.0 if denom >= 0 else float(np.clip(0.5 * (early - late) / denom * 0.5, -0.5, 0.5))

        # Carrier: phase advance of the gain since the previous unit.
        if self.gain is not None:
            advance = np.angle(gain / self.gain)
            self.dphase += self.kf * advance / n
        self.phase += n * self.dphase
        self.phase = float(np.angle(np.exp(1j * self.phase)))

        # Timing loop (proportional-integral).
        self.t_next = times[-1] + self.period + self.kp * error
        self.period += self.ki * error / n

        previous = self.gain
        self.gain = gain
        output = None if training else self._normalise(y, gain, previous)
        return self._hold(output, bad=snr < LOSS_SNR_DB)

    # -- data-phase helpers -----------------------------------------------------------

    def _probe_symbols(self) -> np.ndarray:
        position = self._frames % self.mode.frames_per_block
        d1, d2 = self.mode.d1_d2
        d_value = {self.mode.frames_per_block - 2: d1, self.mode.frames_per_block - 1: d2}
        probe = symbols.probe_tribits(self.mode, d_value.get(position))
        start = self._data_symbols + self.mode.unknown_per_frame
        return np.exp(
            1j * np.pi / 4 * scrambler.scramble(probe, scrambler.data_sequence(start, probe.size))
        )

    def _decide_set(self, y: np.ndarray) -> np.ndarray:
        """75 bps: decide which pattern this set is (non-coherently), return it as phasors."""
        seq = scrambler.data_sequence(self._data_symbols, y.size)
        clean = demap.descramble(y, seq)
        exceptional = symbols.is_exceptional_set(self._sets, self.mode)
        candidates = demap._EXCEPTIONAL if exceptional else demap._NORMAL
        v = int(np.argmax(np.abs(candidates.conj() @ clean)))
        return candidates[v] * np.exp(1j * np.pi / 4 * seq)

    def _normalise(self, y, gain, previous):
        """Scale a data-phase unit to the unit circle and advance data-phase counters."""
        if self.mode.is_75bps:
            # Use the gain learned from *previous* sets, so the decoder's coherent metric is
            # not biased towards this set's own decision.
            reference = self._smoothed_gain if self._smoothed_gain is not None else gain
            self._smoothed_gain = (
                gain if self._smoothed_gain is None else 0.7 * self._smoothed_gain + 0.3 * gain
            )
            out = y / reference
            self._sets += 1
        else:
            reference = gain if previous is None else 0.5 * (gain + previous)
            out = y.copy()
            out[: self.mode.unknown_per_frame] /= reference
            out[self.mode.unknown_per_frame :] /= gain
            self._frames += 1
        self._data_symbols += y.size
        return out

    def _advance_counters(self, y: np.ndarray) -> np.ndarray:
        """Count a data-phase unit that carried no signal (its symbols are worthless)."""
        if self.mode.is_75bps:
            self._sets += 1
        else:
            self._frames += 1
        self._data_symbols += y.size
        return np.zeros_like(y)

    def _hold(self, output, bad: bool):
        """Delay output by LOSS_UNITS units, so that when the signal is lost the units that
        were already bad can be withheld. Good units are never discarded."""
        self._bad_run = self._bad_run + 1 if bad else 0
        if output is not None:
            self._held.append((output, bad))
        if self._bad_run >= LOSS_UNITS:
            self.lost = True
            return self._release_good()
        if len(self._held) > LOSS_UNITS:
            return self._held.popleft()[0]
        return None

    def _release_good(self) -> np.ndarray | None:
        """Release held units up to the last good one; drop the trailing bad ones."""
        units = list(self._held)
        self._held.clear()
        while units and units[-1][1]:
            units.pop()
        return np.concatenate([u for u, _ in units]) if units else None
