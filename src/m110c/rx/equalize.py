"""Equalising demodulator for multipath fading channels (M10).

Why the M8 demodulator is not enough
    On an HF channel the signal arrives over several paths, a few milliseconds apart, each
    fading on its own (Appendix E). 2 ms is about 5 symbols and 5 ms about 12, so every
    received sample is a mixture of a dozen neighbouring symbols. The M8 tracker models the
    channel as one complex gain, which is right for a single path only.

The model used here
    After the matched filter, the sample taken at symbol k's time is

        r_k = sum_j h_j s_{k-j} + n_k,          j = -A .. B          (*)

    a short convolution of the transmitted symbols s with a *channel impulse response* h
    (one complex tap per symbol of delay), plus noise of variance N. h changes as the paths
    fade, so it is re-estimated for every unit of known symbols (every frame in the data
    phase) and interpolated in between. Precursor taps (j < 0) are there because a
    band-limited pulse is not zero before its peak; postcursor taps cover the delayed paths.

    The demodulator repeats three steps.

    1. **Channel estimation** (:func:`estimate_channel`). Over a window where every symbol is
       known (preamble, probes) or already decided (data), (*) is linear in h, so h follows
       from least squares. Plain least squares is noisy when there are many taps, so the
       estimate is the *LMMSE* one, which also uses the long-term average power of each tap
       (the *power-delay profile*, PDP): taps that are usually empty are pulled towards zero,
       busy taps are left free. The PDP changes slowly (path delays are stable, only their
       gains fade), so it is averaged over about a second of full least-squares estimates.

    2. **Equalisation** (:func:`mmse_dfe`). For symbol k, everything already decided (the
       past) is subtracted from the samples that contain s_k, using h. What remains is s_k,
       some not-yet-decided future symbols and noise. A linear MMSE filter picks s_k out of
       that. Together this is the classic decision-feedback equaliser (DFE), but with its
       coefficients *computed* from the channel estimate rather than adapted step by step.
       Adaptive algorithms (LMS, RLS) cannot follow 5-Hz fading with a dozen taps
       (docs/PERFORMANCE.md); interpolating estimates made on both sides of the data can.

    3. **Two passes per frame.** The data of frame n lies between the probe of frame n-1
       and the probe of frame n. The first pass equalises it with h *extrapolated* from
       earlier estimates, which is good enough for tentative decisions. Those decisions plus
       the new probe give a fresh estimate of h. The second pass equalises the frame again
       with h *interpolated* between the two estimates; only its output is used.

    The equaliser output is an unbiased estimate of each symbol, together with its error
    variance. The decoder uses that variance to weight its soft bits, so symbols received
    in a fade count for less (channel-state information).

Timing, carrier, alignment
    * When the demodulator starts, the remaining preamble is fully known. The first
      ``ANCHOR_SYMBOLS`` of it give a wide least-squares estimate, j = -B .. B, which starts
      the delay profile.
    * Alignment: tap 0 should be the *leading edge* of the channel (the first tap within
      10 dB of the strongest in the profile), so delayed paths land on postcursor taps
      whichever path the acquisition locked to. When the profile shows an earlier path, or
      the first one has faded out, the numbering moves by whole symbols (:meth:`_realign`).
    * Timing: a proportional-integral loop keeps the strongest path's peak on a tap. The
      detector is first order in the error (:func:`peak_offset`); when fading makes another
      path the strongest, the reference jumps with it rather than counting as an error. So
      the loop follows transmitter clock offsets, not fades. The clock offset reported is a
      straight-line fit through where the units really arrived (:class:`LineFit`).
    * Carrier: the phase advance of h from one estimate to the next is residual frequency.
      A gentle loop removes it. Between estimates, interpolating h follows the rest.

Loss of signal
    Each unit gets an estimate of the signal's share of the received power, from the probe
    alone (:meth:`_probe_fraction`; decided data would fit noise too) or, at 75 bps and in
    the preamble, from the channel fit. A fade is short, so the signal is declared lost
    only when the *average* over ``LOSS_SECONDS`` is below that of a signal at
    ``LOSS_SNR_DB``. Output is held back that long, so the bad units at the end can be
    withheld, and the message's end located for the next search.
"""

from collections import deque

import numpy as np

from .. import scrambler, symbols
from .._blas import single_threaded_blas
from ..modulate import rrc_taps
from ..spec import tables as T
from ..spec.modes import ModeSpec
from . import demap
from .acquire import SLOT_TRIBITS, Detection
from .track import UNIT_HISTORY, interpolate

PRECURSOR_TAPS = 4  # A: pulse tails before the leading path
POSTCURSOR_TAPS = 20  # B: delayed paths up to ~7 ms, plus pulse tails
LOOKAHEAD = 6  # extra future samples the MMSE filter may use
ANCHOR_SYMBOLS = 256  # known symbols for the first, wide channel estimate
LEADING_EDGE_DB = 10.0
SUPPORT_DB = 15.0  # taps within this of the strongest count as "where the channel is"
PROFILE_SECONDS = 2.0  # averaging time of the power-delay profile
# Loop bandwidths.
TIMING_BANDWIDTH_HZ = 0.2
TRAINING_TIMING_HZ = 2.0  # faster while all symbols are known, to learn a clock offset
PATH_SWITCH_TAPS = 1.5  # a bigger jump in the strongest path's position is a different path
TIMING_DAMPING = 0.7
FREQUENCY_TIME_CONSTANT_S = 1.0
SUB_BLOCK = 8  # symbols per equaliser coefficient update
RIDGE = 1e-6  # least squares: regularisation relative to the average symbol energy
LOSS_SECONDS = 1.0
# 75 bps has no probes, so in the data phase the loops learn from decided symbols, through
# fades. Over a long message that let the timing loop's period run away (docs/PERFORMANCE.md,
# M13). There: units with less than this share of the power as signal teach no loop, and
# the period (the clock offset, learned in the preamble) adapts this much more slowly.
TRACK_FRACTION_75 = 0.1
PERIOD_GAIN_75 = 0.1
# 75 bps, data phase: a realignment must land where the set-correlation profile (which does
# not depend on decisions) sees a path, within this many dB of its strongest.
SET_CONFIRM_DB = 6.0
LOSS_SNR_DB = -6.0  # as a power share, 0.2, averaged over LOSS_SECONDS; noise alone gives ~0


def convolution_matrix(s: np.ndarray, first: int, ks: np.ndarray, offsets: np.ndarray):
    """Rows k in ``ks``, columns j in ``offsets``: s_{k-j}, where ``s[i]`` holds s_{first+i}."""
    return s[ks[:, None] - offsets[None, :] - first]


def estimate_channel(r, S, noise_var=None, prior=None):
    """Channel taps h from observations ``r = S h + n``.

    Without a ``prior`` this is least squares, h = (S^H S)^-1 S^H r. With one (the expected
    power of each tap), it is the LMMSE estimate (S^H S + N diag(1/prior))^-1 S^H r, which
    shrinks taps the prior says are weak. Returns (h, residual noise variance per
    observation).

    Data symbols are pseudo-random, so S^H S is close to a multiple of the identity. Known
    symbols need not be: at the end of a long preamble the count slots are identical and
    every slot is scrambled alike, so the symbols repeat every 32, and two taps 32 apart
    have identical columns. S^H S is then singular, and the solution in that direction is
    whatever the rounding of the BLAS in use produces (a phantom path 15 symbols early, on
    one CPU and not another). A tiny ridge, 1e-6 of the average diagonal, sets it to zero
    instead: the data say nothing there, and every other tap moves by about 1e-6.
    """
    gram = S.conj().T @ S
    if prior is None:
        scale = np.real(np.trace(gram)) / gram.shape[0]
        gram = gram + RIDGE * max(scale, 1e-300) * np.eye(gram.shape[0])
        dof = max(1, S.shape[0] - S.shape[1])
    else:
        gram = gram + noise_var * np.diag(1 / prior)
        dof = S.shape[0]
    try:
        h = np.linalg.solve(gram, S.conj().T @ r)
    except np.linalg.LinAlgError:  # e.g. all-zero symbols
        h = np.linalg.lstsq(S, r, rcond=None)[0]
    residual = r - S @ h
    return h, float(np.sum(np.abs(residual) ** 2)) / dof


def mmse_dfe(h: np.ndarray, noise_var: float, precursor: int, lookahead: int):
    """Equaliser coefficients for the channel ``h`` (taps j = -precursor .. len(h)-1-precursor).

    For symbol k the filter sees the samples r_{k+m}, m = -A .. B + lookahead. Writing them
    as  r = H_future s_future + H_past s_past + n  (s_future = s_k, s_k+1, ...), the past
    part is known and subtracted, and the MMSE filter for s_k is

        f = (H_future H_future^H + N I)^-1 h_0,        h_0 = H_future[:, 0]

    Returns (f, g, gamma): the estimate of s_k is (f^H r - g^H s_past) / gamma, which is
    unbiased, with error variance (1 - gamma) / gamma. g = H_past^H f are the familiar
    feedback taps, past = (s_k-1, s_k-2, ...).
    """
    a = precursor
    b = h.size - 1 - precursor
    m = np.arange(-a, b + lookahead + 1)[:, None]  # sample offsets
    future = np.arange(0, a + b + lookahead + 1)[None, :]
    past = np.arange(1, a + b + 1)[None, :]

    def tap_matrix(j):
        inside = (j >= -a) & (j <= b)
        return np.where(inside, h[np.clip(j + a, 0, h.size - 1)], 0)

    h_future = tap_matrix(m - future)
    h_past = tap_matrix(m + past)
    covariance = h_future @ h_future.conj().T + noise_var * np.eye(m.size)
    f = np.linalg.solve(covariance, h_future[:, 0])
    gamma = float(np.real(np.vdot(f, h_future[:, 0])))
    return f, h_past.conj().T @ f, gamma


def _db(linear: float) -> float:
    """10 log10, with -inf for anything not positive (an SNR estimate can be negative)."""
    return float(10 * np.log10(linear)) if linear > 0 else float("-inf")


def _shift(taps: np.ndarray, edge: int) -> np.ndarray:
    """Taps re-indexed so that old tap ``edge`` becomes tap 0 (zeros shifted in)."""
    out = np.zeros_like(taps)
    if edge >= 0:
        out[: taps.size - edge] = taps[edge:]
    else:
        out[-edge:] = taps[: taps.size + edge]
    return out


def pulse_slope(sps: int, rolloff: float) -> float:
    """Slope of the end-to-end pulse (transmit and receive filters, peak 1) one symbol after
    its peak, per symbol. For a raised cosine with roll-off 0.25 it is about -0.94."""
    taps = rrc_taps(sps, rolloff)
    pulse = np.convolve(taps, taps)
    pulse /= pulse.max()
    one_symbol = pulse.size // 2 + sps
    return float((pulse[one_symbol + 1] - pulse[one_symbol - 1]) / 2 * sps)


def peak_offset(h: np.ndarray, slope: float) -> float:
    """Where the strongest path really is, relative to its tap, in symbols (-0.5 .. 0.5).

    A path that arrives mu symbols late puts h(t) = p(t - mu) on the taps, for the pulse p.
    The pulse is zero one symbol either side of its peak, so with no timing error the two
    neighbouring taps are zero, and for a small error they are p(+-1 - mu) = -+ slope*mu:
    equal and opposite. Their difference measures mu to first order:

        mu = Re{ (h_+1 - h_-1) conj(h_0) } / (-2 slope |h_0|^2)

    (Their *powers* are equal for either sign of mu, so a detector built on |h|^2 alone is
    blind to small errors.)"""
    i = int(np.argmax(np.abs(h)))
    if i == 0 or i == h.size - 1:
        return 0.0
    centre = h[i]
    mu = np.real((h[i + 1] - h[i - 1]) * np.conj(centre)) / (-2 * slope * abs(centre) ** 2)
    return float(np.clip(mu, -0.5, 0.5))


def leading_edge(power: np.ndarray, offsets: np.ndarray) -> int:
    """The first tap within LEADING_EDGE_DB of the strongest."""
    strong = power >= power.max() * 10 ** (-LEADING_EDGE_DB / 10)
    return int(offsets[np.argmax(strong)])


class LineFit:
    """Running least-squares straight line y = a + b x (bounded memory)."""

    def __init__(self):
        self.n = 0
        self._origin = None
        self._sums = np.zeros(4)  # x, y, xx, xy

    def at(self, x: float) -> float | None:
        """The fitted line's value at x (None before two points)."""
        slope = self.slope()
        if slope is None:
            return None
        sx, sy = self._sums[:2]
        return float(self._origin[1] + sy / self.n + slope * (x - self._origin[0] - sx / self.n))

    def add(self, x: float, y: float) -> None:
        if self._origin is None:
            self._origin = (x, y)  # sums around the first point keep the precision
        dx, dy = x - self._origin[0], y - self._origin[1]
        self._sums += (dx, dy, dx * dx, dx * dy)
        self.n += 1

    def slope(self) -> float | None:
        sx, sy, sxx, sxy = self._sums
        denominator = self.n * sxx - sx * sx
        if self.n < 2 or denominator <= 0:
            return None
        return float((self.n * sxy - sx * sy) / denominator)


class EqualizingDemodulator:
    """Demodulator for every rate on multipath channels (75 bps: the RAKE of :meth:`_rake`).

    Same interface as :class:`~m110c.rx.track.TrackingDemodulator`: feed raw samples to
    :meth:`process`, call :meth:`finish` at the end. Both return ``(symbols, noise_var)``:
    unbiased data-phase symbol estimates (probes included) and the error variance of each.
    """

    def __init__(
        self,
        mode: ModeSpec,
        detection: Detection,
        sample_rate: int = 9600,
        rolloff: float = 0.25,
        start_index: int = 0,
        precursor: int = PRECURSOR_TAPS,
        postcursor: int = POSTCURSOR_TAPS,
        timing_bandwidth_hz: float = TIMING_BANDWIDTH_HZ,
    ):
        self.mode = mode
        self.sample_rate = sample_rate
        self.sps = sample_rate // T.SYMBOL_RATE
        self.carrier_hz = detection.carrier_hz
        self.timing_bandwidth_hz = timing_bandwidth_hz
        self.a, self.b = precursor, postcursor
        self.offsets = np.arange(-precursor, postcursor + 1)  # detection-span taps
        self.wide = np.arange(-postcursor, postcursor + 1)  # alignment-span taps
        self.taps = rrc_taps(self.sps, rolloff)
        self._slope = pulse_slope(self.sps, rolloff)
        self._peak_delay = 2 * ((self.taps.size - 1) // 2)  # symbol start -> filter peak
        delay = (self.taps.size - 1) // 2
        # Matched filter, as in TrackingDemodulator.
        self._history = np.zeros(self.taps.size - 1, dtype=complex)
        self._samples_in = start_index
        self._u = np.zeros(0, dtype=complex)
        self._u_start = start_index
        # Observations r_i: one per symbol time. Symbol k's leading path is r_{k + align}.
        # Observation i is taken at time t_base + (i - i_base) * period, with carrier phase
        # phase_base + (i - i_base) * dphase removed. The loops change period and dphase
        # between units, after moving the base to the next observation, so the times do not
        # depend on how the input was chunked.
        self._i_base = 0
        self._t_base = detection.segment_start + 2 * delay  # matched-filter peak of symbol 0
        self._phase_base = 0.0
        self.period = float(self.sps)
        self.dphase = 0.0
        self._obs = np.zeros(0, dtype=complex)
        self._obs_time = np.zeros(0)  # the sample time each observation was taken at
        self._obs_start = 0
        self.align = 0
        # Symbols: known (training, probes) or decided (data). Index 0 = segment start.
        self.n_train = detection.data_start_symbol
        pre = symbols.preamble_tribits(mode)[-self.n_train :]
        self._sym = np.exp(
            1j * np.pi / 4 * scrambler.scramble(pre, scrambler.sync_sequence(0, pre.size))
        )
        self._sym_start = 0
        self._next_symbol = 0  # first symbol not yet in a processed unit
        n = mode.bits_per_channel_symbol
        self._points = np.exp(1j * np.pi / 4 * symbols.CONSTELLATION[n].astype(float))
        # Channel state.
        self.profile = None  # PDP over the wide span
        self.set_profile = None  # 75 bps: set-correlation energy over the wide span
        self._estimates = deque(maxlen=2)  # (centre symbol, h, noise variance)
        self._peak_reference = None  # where the strongest path's peak should be (taps)
        self._clock = LineFit()  # (symbol, arrival time) for the clock-offset estimate
        self._last_peak = 0.0
        self.anchored = False
        # Loss handling.
        # Each unit's SNR in dB, for diagnostics: only the most recent are kept, so that
        # memory stays bounded however long the message.
        self.unit_snr_db: deque = deque(maxlen=UNIT_HISTORY)
        self.lost = False
        self._held: deque = deque()
        self._quality: deque = deque()  # (signal fraction, symbols, first symbol) per unit
        self.end_symbol = None  # once lost: where the signal is thought to have ended

    # -- public ------------------------------------------------------------------------

    @property
    def in_data_phase(self) -> bool:
        return self._next_symbol >= self.n_train

    @property
    def position(self) -> int:
        """Global sample index the demodulator has reached (for resuming a search)."""
        return int(self.t_next)

    @property
    def t_next(self) -> float:
        """Sample time of the next observation to be taken."""
        return self._t_base + (self._obs_start + self._obs.size - self._i_base) * self.period

    @single_threaded_blas
    def process(self, samples: np.ndarray):
        self._filter(np.asarray(samples))
        out = []
        while not self.lost:
            if not self.anchored:
                if not self._sample_through(ANCHOR_SYMBOLS + self.b):
                    break
                self._anchor()
                continue
            k0 = self._next_symbol
            k1 = k0 + self._unit_length()
            needed = k1 - 1 + self.b + LOOKAHEAD + self.align
            if not self._sample_through(needed):
                break
            produced = self._unit(k0, k1)
            if produced is not None:
                out.append(produced)
            self._trim()
        return self._join(out)

    @single_threaded_blas
    def finish(self):
        """End of input: equalise what can be (missing future samples count as zero), then
        release held units except trailing bad ones."""
        if not self.lost and self.anchored:
            # Let the last units through by padding the observations with silence.
            extra = []
            while True:
                k0 = self._next_symbol
                k1 = k0 + self._unit_length()
                needed = k1 - 1 + self.b + LOOKAHEAD + self.align
                have = self._obs_start + self._obs.size - 1
                if needed - have > self.b + LOOKAHEAD:  # the unit itself is incomplete
                    break
                if needed > have:
                    pad = needed - have
                    self._obs = np.concatenate([self._obs, np.zeros(pad, complex)])
                    later = self.t_next + self.period * np.arange(pad)
                    self._obs_time = np.concatenate([self._obs_time, later])
                produced = self._unit(k0, k1)
                if produced is not None:
                    extra.append(produced)
                if self.lost:
                    break
            out = extra
        else:
            out = []
        if not self.lost:
            released = self._release_good()
            if released is not None:
                out.append(released)
        return self._join(out)

    # -- front end ---------------------------------------------------------------------

    def _filter(self, x: np.ndarray) -> None:
        n = self._samples_in + np.arange(x.size)
        self._samples_in += x.size
        cycles = np.mod(self.carrier_hz * n, self.sample_rate) / self.sample_rate
        mixed = np.concatenate([self._history, x * np.exp(-2j * np.pi * cycles)])
        self._history = mixed[mixed.size - self._history.size :]
        self._u = np.concatenate([self._u, np.convolve(mixed, self.taps[::-1], mode="valid")])

    def _sample_through(self, index: int) -> bool:
        """Take observations up to ``index``, as far as the input allows. False if it does
        not reach that far yet."""
        first = self._obs_start + self._obs.size
        if first > index:
            return True
        i = np.arange(first, index + 1)
        t = self._t_base + (i - self._i_base) * self.period
        available = t + 3 < self._u_start + self._u.size  # cubic interpolation reads t+2
        n = i.size if available.all() else int(np.argmin(available))
        i, t = i[:n], t[:n]
        values = np.zeros(n, dtype=complex)
        inside = t - self._u_start >= 1  # before the stream began: silence
        values[inside] = interpolate(self._u, t[inside] - self._u_start)
        phase = self._phase_base + (i - self._i_base) * self.dphase
        self._obs = np.concatenate([self._obs, values * np.exp(-1j * phase)])
        self._obs_time = np.concatenate([self._obs_time, t])
        return n == index + 1 - first

    def _rebase(self) -> None:
        """Make the next observation the base, before a loop changes period or dphase."""
        nxt = self._obs_start + self._obs.size
        self._t_base += (nxt - self._i_base) * self.period
        phase = self._phase_base + (nxt - self._i_base) * self.dphase
        self._phase_base = float(np.angle(np.exp(1j * phase)))
        self._i_base = nxt

    def _observations(self, ks: np.ndarray) -> np.ndarray:
        """Aligned observations: the samples where symbols ``ks`` arrive on tap 0."""
        return self._obs[ks + self.align - self._obs_start]

    def _symbols(self, ks: np.ndarray, offsets: np.ndarray) -> np.ndarray:
        return convolution_matrix(self._sym, self._sym_start, ks, offsets)

    def _trim(self) -> None:
        # Keep what the estimators and the feedback filter can still reach.
        keep = self._next_symbol - 4 * (self.a + self.b) - 2 * self._unit_length() - 8
        drop = max(0, keep - self._sym_start)
        self._sym, self._sym_start = self._sym[drop:], self._sym_start + drop
        drop = max(0, keep + self.align - self._obs_start)
        self._obs, self._obs_start = self._obs[drop:], self._obs_start + drop
        self._obs_time = self._obs_time[drop:]
        keep_u = int(np.floor(self.t_next)) - 4
        drop = max(0, keep_u - self._u_start)
        self._u, self._u_start = self._u[drop:], self._u_start + drop

    # -- alignment ---------------------------------------------------------------------

    def _anchor(self) -> None:
        """Wide least-squares estimate over the first known symbols, to start the delay
        profile. (If an earlier path than the acquired one shows up, the first unit moves
        tap 0 to it: see _realign.)"""
        ks = np.arange(self.b, ANCHOR_SYMBOLS - self.b)
        h, noise = estimate_channel(self._observations(ks), self._symbols(ks, self.wide))
        power = np.abs(h) ** 2
        if power.sum() <= 1e-12 * max(noise, 1e-30) or not np.all(np.isfinite(h)):
            power = np.zeros_like(power)
            power[self.b] = 1.0  # nothing to go on: keep the acquisition's timing
        self.profile = power
        self.anchored = True
        self._next_symbol = (ANCHOR_SYMBOLS // SLOT_TRIBITS) * SLOT_TRIBITS

    # -- units -------------------------------------------------------------------------

    def _unit_length(self) -> int:
        if self._next_symbol < self.n_train:
            return min(SLOT_TRIBITS, self.n_train - self._next_symbol)
        if self.mode.is_75bps:
            return T.TRIBITS_PER_75BPS_SET
        return self.mode.frame_symbols

    def _unit(self, k0: int, k1: int):
        training = k1 <= self.n_train
        detect = self._rake if self.mode.is_75bps else self._equalise
        if not training:
            self._extend_symbols(k0, k1)
            if len(self._estimates) == 2:  # pass 1: tentative decisions
                detect(k0, k1, commit=False)
        h, noise = self._estimate(k1)
        if not np.all(np.isfinite(h)):
            h, noise = np.zeros_like(h), 0.0
        signal = float(np.sum(np.abs(h) ** 2))
        centre = k1 - self.a - self._window(k1) / 2
        usable = signal > 1e-12 * max(noise, 1e-30)
        # (75 bps, data phase: a unit that is mostly noise teaches the loops nothing.)
        learn = usable and not (
            self.mode.is_75bps and self.in_data_phase and self._fit_fraction < TRACK_FRACTION_75
        )
        if usable:
            if learn:
                self._track(h, centre)
            self._estimates.append((centre, h, max(noise, 1e-12 * signal)))
        self._next_symbol = k1
        output = None
        fraction = self._fit_fraction
        if not training:
            if usable and len(self._estimates) == 2:
                output = detect(k0, k1, commit=True)
                if not self.mode.is_75bps:  # 75 bps has no probes: keep the fit's fraction
                    fraction = self._probe_fraction(k0, k1)
            else:  # nothing received: zero symbols with "infinite" variance
                output = np.zeros(k1 - k0, complex), np.full(k1 - k0, 1e6)
                fraction = 0.0
        # SNR = signal / noise = f / (1 - f), for f the signal's share of the power.
        self.unit_snr_db.append(_db(fraction / (1 - fraction)) if fraction < 1 else float("inf"))
        edge = leading_edge(self.profile, self.wide)
        confirmed = True
        if self.mode.is_75bps and not training:
            # No probes: the profile above comes from decided symbols, and in a deep fade it
            # can show a phantom path 15-20 symbols away. The sets themselves say where the
            # signal is (docs/PERFORMANCE.md, M13).
            self._update_set_profile(k0, k1)
            confirmed = self._sets_confirm(edge)
        if learn and confirmed and not -2 < edge < 3:  # a path appeared earlier, or faded
            self._realign(edge)
        return self._hold(output, fraction, k0, k1)

    def _window(self, k1: int) -> int:
        """Observations per channel estimate: two slots in the preamble, one frame in the
        data phase. At 75 bps, three sets: they are all decided data, at low SNR."""
        if k1 <= self.n_train:
            return 2 * SLOT_TRIBITS
        if self.mode.is_75bps:
            return 3 * T.TRIBITS_PER_75BPS_SET
        return self.mode.frame_symbols

    def _estimate(self, k1: int):
        """LMMSE channel estimate from the window ending at symbol k1, plus a full
        least-squares estimate over a longer window to update the power-delay profile."""
        # Every observation before `end` involves only symbols before k1.
        end = k1 - self.b
        ks = np.arange(end - 3 * self.wide.size, end)
        full, noise = estimate_channel(self._observations(ks), self._symbols(ks, self.wide))
        self._least_squares = full
        noise_floor = noise / ks.size
        memory = np.exp(-self._unit_length() / (PROFILE_SECONDS * T.SYMBOL_RATE))
        # Tap powers with the estimation noise removed.
        excess = np.abs(full) ** 2 - noise_floor
        # The signal's share of the received power: the tap power beyond what fitting noise
        # alone would give, over that plus the noise. Pure noise measures about 0 when the
        # symbols are known (preamble); with decided symbols noise fits a little.
        signal = excess.sum()
        self._fit_fraction = float(signal / (signal + noise)) if signal > 0 else 0.0
        self.profile = memory * self.profile + (1 - memory) * np.abs(full) ** 2
        detect = self.profile[self.b - self.a :]  # the detection span, j = -A .. B
        prior = np.maximum(detect - noise_floor, 1e-6 * max(detect.max(), 1e-30))
        end = k1 - self.a
        ks = np.arange(end - self._window(k1), end)
        return estimate_channel(
            self._observations(ks), self._symbols(ks, self.offsets), max(noise, 1e-30), prior
        )

    def _track(self, h: np.ndarray, centre: float) -> None:
        """Carrier (rotation of h) and timing (peak of the strongest path) loops."""
        self._rebase()
        if len(self._estimates):
            previous_centre, previous, _ = self._estimates[-1]
            advance = np.angle(np.vdot(previous, h))
            elapsed = centre - previous_centre  # symbols
            gain = min(1.0, elapsed / (FREQUENCY_TIME_CONSTANT_S * T.SYMBOL_RATE))
            self.dphase += gain * advance / elapsed
        # Proportional-integral loop with natural frequency w (rad/s), per unit of n symbols:
        # the next sampling instant moves by kp * error and the period by ki * error / n.
        n = self._unit_length()
        bandwidth = self.timing_bandwidth_hz if self.in_data_phase else TRAINING_TIMING_HZ
        w_t = 2 * np.pi * bandwidth * n / T.SYMBOL_RATE
        kp, ki = 2 * TIMING_DAMPING * w_t, w_t**2
        if self.mode.is_75bps and self.in_data_phase:
            ki *= PERIOD_GAIN_75
        # The timing detector uses the plain least-squares estimate: the LMMSE one leans
        # towards the long-term profile, which lags behind a drifting clock.
        error = self._timing_error(self._least_squares) * self.period
        self._clock.add(centre, self._time_of(centre) + error)
        self._t_base += kp * error
        self.period += ki * error / n

    def _time_of(self, symbol: float) -> float:
        """Sample time at which symbol ``symbol`` (fractional) was observed on tap 0."""
        index = np.arange(self._obs.size) + self._obs_start - self.align
        return float(np.interp(symbol, index, self._obs_time))

    def symbol_time(self, k: float) -> float:
        """Sample index at which symbol k (0 = the detected segment's first symbol) starts:
        from the clock fit, or the nominal symbol rate before there is one."""
        fitted = self._clock.at(k)
        nominal = self._t_base + (k + self.align - self._i_base) * self.period
        return (fitted if fitted is not None else nominal) - self._peak_delay

    @property
    def clock_ppm(self) -> float:
        """Transmitter clock offset: a fast transmitter gives a shorter symbol period here.
        Measured by a straight-line fit through where each unit's symbols really arrived
        (sampling time plus the remaining timing error), which is more accurate than the
        loop's current period for short messages."""
        period = self._clock.slope()
        return 0.0 if period is None else (self.sps / period - 1) * 1e6

    def _timing_error(self, h: np.ndarray) -> float:
        """How far (in symbols) the strongest path's peak has moved from where it should be.

        Its position, tap index plus :func:`peak_offset`, moves smoothly with a clock offset
        (a fraction of a tap per unit). When fading makes a different path the strongest,
        the position jumps by the path spacing instead: that is not a timing error, so the
        reference jumps with it."""
        position = float(self.wide[np.argmax(np.abs(h))]) + peak_offset(h, self._slope)
        if self._peak_reference is None:
            self._peak_reference = float(round(position))
        elif abs(position - self._last_peak) > PATH_SWITCH_TAPS:
            self._peak_reference += position - self._last_peak
        self._last_peak = position
        return position - self._peak_reference

    def _realign(self, edge: int) -> None:
        """Move tap 0 by ``edge`` symbols: observation k + edge becomes symbol k's tap 0."""
        self.align += edge
        if self._peak_reference is not None:
            self._peak_reference -= edge
            self._last_peak -= edge
        self.profile = _shift(self.profile, edge)
        if self.set_profile is not None:
            self.set_profile = _shift(self.set_profile, edge)
        self._estimates = deque(((c, _shift(h, edge), n) for c, h, n in self._estimates), maxlen=2)

    def _h_at(self, k: float) -> np.ndarray:
        (c0, h0, _), (c1, h1, _) = self._estimates
        return h0 + (h1 - h0) * (k - c0) / (c1 - c0)

    def _equalise(self, k0: int, k1: int, commit: bool):
        """MMSE-DFE over symbols k0..k1-1; decisions go into the symbol store."""
        noise = self._estimates[-1][2]
        position = (k0 - self.n_train) % self.mode.frame_symbols
        unknown = self.mode.unknown_per_frame
        m = np.arange(-self.a, self.b + LOOKAHEAD + 1)
        past = np.arange(1, self.a + self.b + 1)
        z_out = np.zeros(k1 - k0, complex)
        var_out = np.zeros(k1 - k0)
        for b0 in range(k0, k1, SUB_BLOCK):
            b1 = min(b0 + SUB_BLOCK, k1)
            f, g, gamma = mmse_dfe(self._h_at((b0 + b1 - 1) / 2), noise, self.a, LOOKAHEAD)
            for k in range(b0, b1):
                r = self._observations(k + m)
                s_past = self._sym[k - past - self._sym_start]
                z = (np.vdot(f, r) - np.vdot(g, s_past)) / gamma
                if (position + k - k0) < unknown:
                    self._sym[k - self._sym_start] = self._decide(k, z)
                z_out[k - k0] = z
                var_out[k - k0] = max((1 - gamma) / gamma, 1e-6)
        return z_out, var_out

    def _rake(self, k0: int, k1: int, commit: bool):
        """75 bps: estimate the set's symbols by combining every path (a RAKE receiver),
        then decide which pattern the set is.

        At 75 bps every symbol of a 32-symbol set carries the same two bits, and the SNR is
        low, so the best linear estimate is close to the channel's matched filter: each
        symbol is read at every tap where it arrives and the copies are added, weighted by
        the channel (maximal-ratio combining):

            z_k = sum_j conj(h_j) r_{k+j} / sum |h_j|^2

        What neighbouring symbols contribute through the other paths is left as
        interference; the set correlation in the decoder spreads it like noise. (Subtracting
        the decided previous set's share first was measured: under 1 dB better SNR on a
        5-ms channel, and no change at the SNRs where 75 bps is used, so it was left out.)
        """
        h = self._h_at((k0 + k1 - 1) / 2)
        energy = float(np.sum(np.abs(h) ** 2))
        k = np.arange(k0, k1)
        z = self._observations(k[:, None] + self.offsets[None, :]) @ np.conj(h) / energy
        decided = self._decide_set(k0, z)
        self._sym[k0 - self._sym_start : k1 - self._sym_start] = decided
        var = max(float(np.mean(np.abs(z - decided) ** 2)), 1e-6)
        return z, np.full(z.size, var)

    def _update_set_profile(self, k0: int, k1: int) -> None:
        """75 bps: where the sets are, without the equaliser's help.

        Each set is one of four known 32-symbol patterns, scrambled by a sequence tied to
        the symbol count. Read at the right delay, a set correlates strongly with one
        pattern; read at a wrong one, even a whole set away, the scrambling no longer
        matches and it correlates with none. So the best pattern's energy, at every tap of
        the wide span, is a delay profile of the signal that does not depend on decisions
        or on the channel estimate (and is phase-free: the magnitude of the correlation).
        """
        k = np.arange(k0, k1)
        rotation = np.exp(1j * np.pi / 4 * self._scrambling[k - self._scrambling_start])
        obs = self._observations(k[None, :] + self.wide[:, None]) / rotation
        sets = obs.reshape(self.wide.size, -1, T.TRIBITS_PER_75BPS_SET)
        first = (k0 - self.n_train) // T.TRIBITS_PER_75BPS_SET
        energy = np.zeros(self.wide.size)
        for i in range(sets.shape[1]):
            exceptional = symbols.is_exceptional_set(first + i, self.mode)
            patterns = demap._EXCEPTIONAL if exceptional else demap._NORMAL
            energy += np.max(np.abs(sets[:, i, :] @ patterns.conj().T) ** 2, axis=1)
        memory = np.exp(-self._unit_length() / (PROFILE_SECONDS * T.SYMBOL_RATE))
        if self.set_profile is None:
            self.set_profile = energy
        else:
            self.set_profile = memory * self.set_profile + (1 - memory) * energy

    def _sets_confirm(self, edge: int) -> bool:
        """75 bps: does the set profile see a path where a realignment would put tap 0?
        A set read at a wrong delay still collects 32 symbols of signal and noise (and the
        best of four patterns more), so most taps sit on that floor: their median. A path
        shows as energy above it, a symbol or so wide (sampling falls between taps)."""
        excess = np.maximum(self.set_profile - np.median(self.set_profile), 0)
        if excess.max() <= 0:
            return False
        i = int(np.searchsorted(self.wide, edge))
        near = excess[max(0, i - 1) : i + 2]
        return bool(near.max() >= excess.max() * 10 ** (-SET_CONFIRM_DB / 10))

    def _decide_set(self, k0: int, z: np.ndarray) -> np.ndarray:
        """The pattern (normal or exceptional, by position) that best matches the set."""
        set_index = (k0 - self.n_train) // T.TRIBITS_PER_75BPS_SET
        rotation = np.exp(1j * np.pi / 4 * self._scrambling)
        correlations = demap.set_correlations(z / rotation, self.mode, set_index)[0]
        pattern = demap.set_patterns(self.mode, set_index, [int(np.argmax(correlations))])[0]
        return pattern * rotation

    def _probe_fraction(self, k0: int, k1: int) -> float:
        """The signal's share of the received power in a data frame, from its probe alone.

        Decided data cannot be used to tell signal from noise: the decisions are chosen to
        fit whatever was received, so noise fits them too. The probe's values are known in
        advance. Correlating it with the received samples at each tap j where the channel
        usually has energy,

            c_j = (1/P) sum_{k in probe} conj(s_k) r_{k+j},

        gives |c_j|^2 = |h_j|^2 + (received power)/P on average (the unknown data around the
        probe acts as noise). Subtracting the second term leaves the channel power: the
        signal power, or zero on average for noise alone. Divided by the received power, that
        is a fraction near SNR/(1+SNR). It is noisy for one frame (about +/-0.2) and can be
        negative, but it is nearly unbiased, so it averages well over the loss window.
        """
        probe = np.arange(k0 + self.mode.unknown_per_frame, k1)
        known = self._sym[probe - self._sym_start]
        detect = self.profile[self.b - self.a :]
        taps = self.offsets[detect >= detect.max() * 10 ** (-SUPPORT_DB / 10)]
        r = self._observations(probe[:, None] + taps[None, :])
        c = known.conj() @ r / probe.size
        power = float(np.mean(np.abs(r) ** 2))
        excess = float(np.sum(np.abs(c) ** 2)) - taps.size * power / probe.size
        return excess / power if power > 0 else 0.0

    # -- symbols -----------------------------------------------------------------------

    def _extend_symbols(self, k0: int, k1: int) -> None:
        """Append frame [k0, k1): probe symbols known, data symbols to be decided."""
        if self.mode.is_75bps:  # a set: all to be decided
            self._scrambling = scrambler.data_sequence(k0 - self.n_train, k1 - k0)
            self._scrambling_start = k0
            self._append_symbols(k0, np.zeros(k1 - k0, complex))
            return
        frame = (k0 - self.n_train) // self.mode.frame_symbols
        d1, d2 = self.mode.d1_d2
        last = self.mode.frames_per_block
        d_value = {last - 2: d1, last - 1: d2}.get(frame % last)
        probe = symbols.probe_tribits(self.mode, d_value)
        unknown = self.mode.unknown_per_frame
        start = k0 - self.n_train
        tribits = np.concatenate([np.zeros(unknown, dtype=np.uint8), probe])
        seq = scrambler.data_sequence(start, tribits.size)
        self._scrambling = seq
        self._scrambling_start = k0
        frame_symbols = np.exp(1j * np.pi / 4 * scrambler.scramble(tribits, seq))
        frame_symbols[:unknown] = 0
        self._append_symbols(k0, frame_symbols)

    def _append_symbols(self, k0: int, values: np.ndarray) -> None:
        self._sym = np.concatenate([self._sym[: k0 - self._sym_start], values])

    def _decide(self, k: int, z: complex) -> complex:
        """Nearest point of the mode's constellation, after removing the scrambling."""
        rotation = np.exp(1j * np.pi / 4 * self._scrambling[k - self._scrambling_start])
        return self._points[np.argmin(np.abs(z / rotation - self._points))] * rotation

    # -- loss handling -----------------------------------------------------------------

    def _hold(self, output, fraction: float, k0: int, k1: int):
        """Delay output by the loss window, so that when the signal is declared lost the
        units that were already bad can be withheld. Good units are never discarded.

        Lost means: averaged over the window, the signal's share of the received power is
        below that of a signal at LOSS_SNR_DB. The window, LOSS_SECONDS, is long enough to
        ride out fades. (A shorter window in the preamble, to drop false detections sooner,
        dropped real preambles in fades at low SNR instead.)"""
        snr = 10 ** (LOSS_SNR_DB / 10)
        threshold = snr / (1 + snr)
        bad = fraction < threshold
        self._quality.append((fraction, k1 - k0, k0))
        if output is not None:
            self._held.append((output, bad))
        span = LOSS_SECONDS * T.SYMBOL_RATE
        while sum(q[1] for q in self._quality) - self._quality[0][1] >= span:
            self._quality.popleft()
        if sum(q[1] for q in self._quality) >= span:
            values, weights, _ = zip(*self._quality, strict=True)
            if np.average(values, weights=weights) < threshold:
                self.lost = True
                # The signal ended (or faded for good) where the final run of bad units began.
                for value, _, start in reversed(self._quality):
                    if value >= threshold:
                        break
                    self.end_symbol = start
                return self._release_good()
        held_symbols = sum(u[0].size for u, _ in self._held)
        if self._held and held_symbols - self._held[0][0][0].size >= span:
            return self._held.popleft()[0]
        return None

    def _release_good(self):
        """Release the held units, except trailing bad ones."""
        units = list(self._held)
        self._held.clear()
        while units and units[-1][1]:
            units.pop()
        if not units:
            return None
        return (
            np.concatenate([u[0] for u, _ in units]),
            np.concatenate([u[1] for u, _ in units]),
        )

    @staticmethod
    def _join(parts):
        if not parts:
            return np.zeros(0, dtype=complex), np.zeros(0)
        return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])
