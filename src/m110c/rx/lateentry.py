"""Late entry: joining a transmission already in progress, without its preamble (S5).

§5.3.2.3.7.1.1 note: "The receive modem shall use the modification of the known data at
interleaver boundaries to synchronize without a preamble and determine the correct data rate
and mode of operation." At 150-4800 bps the modified known data are the D1/D2 probes before
each interleaver block (§5.3.2.3.7.1.2); at 75 bps, which has no probes, the exceptional sets
(Table Xb) that end each block (INT-010). How, the note does not say: the decisions are
INT-029. Three steps, each measured (docs/PERFORMANCE.md, S5):

1. Search (:class:`LateEntrySearch`)
    Every probe is the all-zero pattern scrambled by the data scrambler (period 160), so the
    probes repeat every lcm(frame, 160) symbols: 480 with 48-symbol frames (2400, 4800 bps),
    160 with 40-symbol frames (150-1200 bps). For each 25-Hz carrier bin (+/-400 Hz) and each
    sampling phase, each probe chunk of the period is correlated coherently, and the powers
    are added over about 0.3 s of frames, for every frame offset and every place in the
    period (as the preamble search adds its slots). At 75 bps the chunks are whole 32-symbol
    sets, each the best of the four Table Xa patterns, under the five scrambler phases of a
    160-symbol period. The metric, like the preamble's, is near 1 for a clean signal and
    near 1/K for noise. The best of the three families wins; it fixes timing, carrier and
    the scrambler phase, not yet the mode or the blocks.

2. Boundary (:class:`LateEntry`)
    The equalising demodulator starts at once, in its late mode
    (``EqualizingDemodulator(late=True)``): it estimates the channel from the probes alone
    (or, at 75 bps, from sets decided at the strongest path), decides each probe among the
    zero and D4-D7 patterns (each 75-bps set among all eight patterns), and records what it
    read. Two consecutive confident D probes naming a mode of this frame length mark the end
    of a block. At 75 bps a confident exceptional set does, and the spacing of the next ones
    says the interleave: another 45 sets away is short (or zero); confidently normal sets
    there are long, whose blocks are 360 sets.

3. Verification
    A candidate is accepted only if decoding its first block says so: the Viterbi fit
    (:attr:`m110c.fec.ViterbiDecoder.fit`) of the block deinterleaved from the candidate
    boundary against that of zero interleave, the S4 rule. A true boundary gives a code
    sequence and wins by ``DECIDE_MARGIN``; a false one is a wrong permutation, like no
    interleave at all, and is dropped; a zero-interleave transmission makes zero win. This
    also keeps false locks on noise from producing output.

Then the demodulator learns the mode and the block start (:meth:`set_block_start`), and the
receiver decodes from that block on, the Viterbi decoder starting in an unknown state.
"""

from dataclasses import dataclass
from math import lcm

import numpy as np

from .. import scrambler
from ..modulate import rrc_taps
from ..spec import tables as T
from ..spec.modes import ModeSpec, mode, mode_from_d1_d2
from . import demap
from .autointerleave import DECIDE_MARGIN
from .decode import DataDecoder
from .equalize import LATE_ANCHOR_SYMBOLS, EqualizingDemodulator

SET = T.TRIBITS_PER_75BPS_SET

#: Frame families: frame length, unknown symbols per frame (75 bps: 32-symbol sets, no
#: probes), how many frames (sets) the search adds, and the provisional mode the late
#: demodulator decides with (the largest constellation the family can carry).
FAMILIES = {
    "48": (48, 32, 20, mode(2400, "short")),
    "40": (40, 20, 16, mode(1200, "short")),
    "75": (SET, 0, 24, mode(75, "short")),
}
WINDOW_SECONDS = 0.6  # each search looks at this much signal
HOP_SECONDS = 0.5  # and the next one starts this much later (0.13 s of CPU a window)
MAX_OFFSET_HZ = 400.0
BIN_HZ = 25.0
#: Search threshold. Measured: noise alone peaked at 0.16 (200 windows); the weakest signal
#: in 396 receptions, 2400S at -6 dB, at 0.22. A false lock costs only a failed attempt.
THRESHOLD = 0.20
D_MARGIN = 3.0  # a D probe counts when it beats the runner-up by this many noise std
SET_MARGIN = 4.0  # an exceptional (or normal) 75-bps set, the same
SHORT_SETS = 45  # 75 bps: sets per short (and zero) interleave block; long: 360
GIVE_UP_SYMBOLS = 30_000  # 12.5 s: two and a half long blocks without a verified boundary
#: Decoding starts at a block boundary at least this far into the demodulator's output: its
#: first channel estimate comes from the probes alone, and on a fading channel it takes about
#: a second to settle (measured: bit errors in the first two 2400S blocks, none after).
SETTLE_SYMBOLS = 2400


@dataclass
class LateLock:
    """A transmission found without its preamble. Symbol 0 is a frame (75 bps: set) start
    whose data-phase index is a multiple of the probe pattern's period. Has the attributes
    of a :class:`~m110c.rx.acquire.Detection` that the demodulator and the reports use."""

    segment_start: float  # sample index where symbol 0's pulse starts
    carrier_hz: float
    metric: float
    family: str  # "48", "40" or "75"
    data_start_symbol: int = 0  # once verified: the symbol where decoding starts (a block)

    @property
    def provisional_mode(self) -> ModeSpec:
        return FAMILIES[self.family][3]


def _templates(family: str) -> np.ndarray:
    """The chunks of one period, rows (Q, K): probe chunks (scrambled zeros) for the probe
    families; for 75 bps (5 scrambler phases x 4 patterns, 32)."""
    if family not in _TEMPLATES:
        if family == "75":
            rows = [
                demap._NORMAL[v] * np.exp(1j * np.pi / 4 * scrambler.data_sequence(q * SET, SET))
                for q in range(5)
                for v in range(4)
            ]
        else:
            frame, unknown, _, _ = FAMILIES[family]
            rows = [
                np.exp(
                    1j * np.pi / 4 * scrambler.data_sequence(q * frame + unknown, frame - unknown)
                )
                for q in range(lcm(frame, 160) // frame)
            ]
        _TEMPLATES[family] = np.array(rows)
    return _TEMPLATES[family]


_TEMPLATES: dict = {}


def search_symbols(r: np.ndarray, family: str):
    """Best (metric, row, n, q0) over the rows of ``r`` (symbol-rate sample streams, one per
    carrier bin and sampling phase): chunk q0 of the period starts at ``n`` (a probe, or a
    set), with n within the first frame (set)."""
    r = np.atleast_2d(r)
    frame, unknown, frames, _ = FAMILIES[family]
    tmpl = _templates(family)
    k = tmpl.shape[1]
    span = frames * frame + frame + k
    if r.shape[1] < span:
        return 0.0, 0, 0, 0
    r = r[:, :span]
    windows = np.lib.stride_tricks.sliding_window_view(r, k, axis=1)  # (rows, positions, k)
    power = np.abs(windows @ tmpl.conj().T) ** 2  # (rows, positions, templates)
    if family == "75":
        power = power.reshape(*power.shape[:2], 5, 4).max(axis=3)  # best pattern per phase
    q = power.shape[2]
    energy = np.concatenate([np.zeros((r.shape[0], 1)), np.cumsum(np.abs(r) ** 2, axis=1)], axis=1)
    pos = np.arange(frame)[:, None] + frame * np.arange(frames)[None, :]  # (n, j)
    chunk_energy = (energy[:, pos + k] - energy[:, pos]).sum(axis=2)  # (rows, n)
    rows = (np.arange(q)[:, None] + np.arange(frames)[None, :]) % q  # (q0, j)
    total = power[:, pos[:, None, :], rows[None, :, :]].sum(axis=3)  # (rows, n, q0)
    metric = total / (k * chunk_energy[:, :, None] + 1e-30)
    row, n, q0 = np.unravel_index(int(np.argmax(metric)), metric.shape)
    return float(metric[row, n, q0]), int(row), int(n), int(q0)


def refine_frequency(r: np.ndarray, family: str, n: int, q0: int) -> float:
    """Residual carrier offset (Hz) from the phase advance of successive chunk correlations
    (one frame, 20 or 16.7 ms, or one set, 13.3 ms, apart: unambiguous to +/-25 Hz or more,
    twice the half-bin error)."""
    frame, _, frames, _ = FAMILIES[family]
    tmpl = _templates(family)
    k = tmpl.shape[1]
    q = 5 if family == "75" else tmpl.shape[0]
    x = []
    for j in range(frames):
        seg = r[n + j * frame : n + j * frame + k]
        if family == "75":
            c = tmpl[((q0 + j) % q) * 4 : ((q0 + j) % q) * 4 + 4].conj() @ seg
            x.append(c[np.argmax(np.abs(c))])
        else:
            x.append(np.vdot(tmpl[(q0 + j) % q], seg))
    x = np.array(x)
    step = np.angle(np.sum(x[1:] * np.conj(x[:-1])))
    return float(step / (2 * np.pi * frame / T.SYMBOL_RATE))


class LateEntrySearch:
    """Streaming late-entry search. Feed samples; it returns a :class:`LateLock` or None.
    Each search covers WINDOW_SECONDS and the next starts HOP_SECONDS later."""

    def __init__(
        self,
        sample_rate: int = 9600,
        carrier_hz: float = T.CARRIER_HZ,
        max_offset_hz: float = MAX_OFFSET_HZ,
        threshold: float = THRESHOLD,
        start: int = 0,
        rolloff: float = 0.25,
    ):
        self.sample_rate = sample_rate
        self.sps = sample_rate // T.SYMBOL_RATE
        self.carrier_hz = carrier_hz
        self.threshold = threshold
        self.taps = rrc_taps(self.sps, rolloff)
        self.bins = np.arange(-max_offset_hz, max_offset_hz + BIN_HZ / 2, BIN_HZ)
        self.window = int(WINDOW_SECONDS * sample_rate)
        self.hop = int(HOP_SECONDS * sample_rate)
        self._buffer = np.zeros(0, dtype=complex)
        self._buffer_start = start  # global sample index of _buffer[0]
        self.last = None  # the best result of the last window (diagnostics)

    def process(self, samples: np.ndarray):
        self._buffer = np.concatenate([self._buffer, np.asarray(samples, dtype=complex)])
        while self._buffer.size >= self.window:
            lock = self._search(self._buffer[: self.window], self._buffer_start)
            if lock is not None:
                return lock
            self._buffer = self._buffer[self.hop :]
            self._buffer_start += self.hop
        return None

    def samples_from(self, index: int) -> np.ndarray:
        """Buffered samples from global ``index`` on (for starting the demodulator), with
        zeros for any before the buffer: the lock's symbol 0 can lie before the window that
        found it (it is put on a whole probe period), and the demodulator counts samples
        from ``index``, so dropping them would shift its time base by as many."""
        skip = index - self._buffer_start
        if skip >= 0:
            return self._buffer[skip:]
        return np.concatenate([np.zeros(-skip, dtype=complex), self._buffer])

    def _search(self, x: np.ndarray, start: int):
        n = start + np.arange(x.size)
        delay = (self.taps.size - 1) // 2
        streams = []  # one symbol-rate stream per (carrier bin, sampling phase)
        for f in self.bins:
            cycles = np.mod((self.carrier_hz + f) * n, self.sample_rate) / self.sample_rate
            y = np.convolve(x * np.exp(-2j * np.pi * cycles), self.taps)[: x.size]
            streams += [y[p :: self.sps][: x.size // self.sps] for p in range(self.sps)]
        streams = np.array(streams)
        best = (0.0, None)
        for family in FAMILIES:
            metric, row, m, q0 = search_symbols(streams, family)
            if metric > best[0]:
                f, p = self.bins[row // self.sps], row % self.sps
                best = (metric, (f, p, m, q0, family, streams[row]))
        metric, found = best
        self.last = metric
        if found is None or metric < self.threshold:
            return None
        f, p, m, q0, family, r = found
        df = refine_frequency(r, family, m, q0)
        frame, unknown, _, _ = FAMILIES[family]
        period = 160 if family == "75" else lcm(frame, 160)
        first = (m - unknown - q0 * frame) % period  # a symbol with data index 0 mod period
        # y[t] is the matched-filter output peaking at t for a pulse that starts at t - 2*delay.
        segment_start = start + p + first * self.sps - 2 * delay
        return LateLock(segment_start, self.carrier_hz + f + df, metric, family)


class LateEntry:
    """One attempt to join a transmission from a :class:`LateLock`: runs the late
    demodulator, finds and verifies a block boundary. :meth:`process` returns True once it
    has; then :attr:`mode`, :attr:`block_start` and :meth:`symbols_from_block` hand over."""

    def __init__(self, lock: LateLock, sample_rate: int = 9600, rolloff: float = 0.25):
        self.lock = lock
        self.start_index = int(np.floor(lock.segment_start)) - 8
        self.demod = EqualizingDemodulator(
            lock.provisional_mode,
            lock,
            sample_rate,
            rolloff=rolloff,
            start_index=self.start_index,
            late=True,
        )
        self._z: list = []
        self._v: list = []
        self._n_out = 0  # symbols output so far, from LATE_ANCHOR_SYMBOLS on
        self._probe_seen = 0
        self._candidates: list = []  # (block start symbol, mode) awaiting verification
        self._tried: set = set()
        self.mode: ModeSpec | None = None
        self.block_start: int | None = None
        self.margin: float | None = None
        self.failed = False

    # -- feeding ------------------------------------------------------------------------

    def process(self, samples: np.ndarray) -> bool:
        if self.failed:
            return False
        z, v = self.demod.process(samples)
        self._z.append(z)
        self._v.append(v)
        self._n_out += z.size
        if self.mode is None:
            self._collect_candidates()
            self._verify()
            if self.mode is None and (self.demod.lost or self._n_out > GIVE_UP_SYMBOLS):
                self.failed = True
                return False
        # Ready once the output reaches the block decoding starts at.
        ready = self.mode is not None and self._n_out >= self.block_start - LATE_ANCHOR_SYMBOLS
        if not ready and self.demod.lost:  # the signal ended first: nothing to deliver
            self.failed = True
        return ready

    def symbols_from_block(self):
        """The demodulator's output from the verified block start on: (symbols, noise_var)."""
        z, v = np.concatenate(self._z), np.concatenate(self._v)
        i0 = self.block_start - LATE_ANCHOR_SYMBOLS
        return z[i0:], v[i0:]

    # -- candidates ---------------------------------------------------------------------

    def _collect_candidates(self) -> None:
        if self.lock.family == "75":
            self._set_candidates()
            return
        frame = FAMILIES[self.lock.family][0]
        readings = self.demod.probe_readings
        for i in range(max(self._probe_seen - 1, 0), len(readings) - 1):
            (k1, d1, m1), (_, d2, m2) = readings[i], readings[i + 1]
            if d1 is None or d2 is None or min(m1, m2) < D_MARGIN:
                continue
            found = mode_from_d1_d2(d1, d2, False)
            if found is not None and not found.is_75bps and found.frame_symbols == frame:
                self._add((k1 + 2 * frame, found))
        self._probe_seen = len(readings)

    def _set_candidates(self) -> None:
        """75 bps: a confident exceptional set ends a block; its neighbours 45 sets away say
        whether blocks are 45 sets (short, or zero) or 360 (long)."""
        readings = {
            k0 // SET: (exceptional, margin) for k0, exceptional, margin in self.demod.set_readings
        }
        for s, (exceptional, margin) in readings.items():
            if not exceptional or margin < SET_MARGIN:
                continue
            near = [readings.get(s + d * SHORT_SETS) for d in (-2, -1, 1, 2)]
            seen = [r for r in near if r is not None and r[1] >= SET_MARGIN]
            if any(e for e, _ in seen):
                self._add(((s + 1) * SET, mode(75, "short")))
            elif len(seen) >= 2:  # confidently normal where a short block would end
                self._add(((s + 1) * SET, mode(75, "long")))

    def _add(self, candidate) -> None:
        if candidate not in self._tried:
            self._tried.add(candidate)
            self._candidates.append(candidate)

    # -- verification -------------------------------------------------------------------

    def _verify(self) -> None:
        if not self._candidates:
            return
        z, v = np.concatenate(self._z), np.concatenate(self._v)
        self._z, self._v = [z], [v]
        waiting = []
        for k_b, found in self._candidates:
            i0 = k_b - LATE_ANCHOR_SYMBOLS
            if i0 < 0:
                continue  # before the demodulator's first output: cannot be decoded
            if i0 + found.block_symbols > z.size:
                waiting.append((k_b, found))
                continue
            accepted, margin = verify(
                found, z[i0 : i0 + found.block_symbols], v[i0 : i0 + found.block_symbols]
            )
            if accepted is not None:
                self.demod.set_block_start(k_b, accepted)
                # Decode from the first block start after the demodulator has settled.
                block = accepted.block_symbols
                settled = LATE_ANCHOR_SYMBOLS + SETTLE_SYMBOLS
                start = k_b + max(0, -(-(settled - k_b) // block)) * block
                self.mode, self.block_start, self.margin = accepted, start, margin
                self.lock.data_start_symbol = start
                self._candidates = []
                return
        self._candidates = waiting


def verify(found: ModeSpec, z: np.ndarray, v: np.ndarray):
    """(accepted mode or None, margin): one block of equalised symbols, deinterleaved as
    ``found`` from its first symbol, against no interleaver (zero interleave). 4800 bps has
    neither interleaver nor code: its D1/D2 alone decide."""
    if found.rate == 4800:
        return found, None
    zero = mode(found.rate, "zero")
    fits = []
    for m in (found, zero):
        decoder = DataDecoder(m, start_state=None)
        decoder.process(z, v)
        fits.append(decoder._viterbi.fit)
    margin = fits[0] - fits[1]
    if margin >= DECIDE_MARGIN:
        return found, margin
    if margin <= -DECIDE_MARGIN:
        return zero, margin
    return None, margin
