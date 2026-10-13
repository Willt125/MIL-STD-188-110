"""Acquisition: find a sync preamble, its timing, carrier offset and mode (§5.3.2.3.7.2).

Every 200-ms preamble segment starts with nine fixed channel symbols, 0 1 3 0 1 3 1 2 0, each
sent as a 32-tribit Table XIII pattern scrambled by the 32-symbol sync sequence. Because the
segment boundaries line up with the sync sequence (480 = 15 x 32), every 32-symbol *slot* of
every segment is scrambled identically. So each slot is one of just eight known waveforms
``W_v`` (v = the channel symbol), whatever the mode.

Search (:class:`PreambleSearch`)
    The signal is mixed down by the nominal carrier. For each candidate offset f on a grid
    (default +/-400 Hz in 25-Hz steps) and each prefix waveform, a correlation c_{v,f}[n]
    measures how well the signal at sample n matches W_v shifted by f. The detection
    metric adds the nine slot correlations of the prefix, 32 symbols apart:

        rho(n, f) = sum_j |c_{prefix_j, f}[n + j*S]|^2  /  ( ||W||^2 * E(n) )

    where S is the slot length in samples and E(n) the signal energy in those nine windows.
    rho is near 1 for a clean preamble and near 1/32 for noise. Each slot is only 13.3 ms
    long, so a 12.5-Hz residual offset (half a grid step) costs about a radian over a slot,
    a small loss. Adding |.|^2 across slots, rather than complex values, keeps the metric
    insensitive to the phase drift between slots.

Refinement and reading (:func:`read_segment`)
    At the best (n, f): timing is refined by fitting a parabola through rho at n-1, n, n+1.
    The residual frequency comes from the phase progression of the nine prefix correlations,
    which are 13.3 ms apart, so offsets up to +/-37.5 Hz are unambiguous. Then all fifteen
    slots are classified coherently against the eight waveforms: the prefix (as a check), D1,
    D2, the three count symbols and the final 0. After the prefix, each slot's phase
    reference is the slot before it, so the reading follows a fading channel's phase. The
    count says how many segments remain, and so where the data phase starts.

A detection is accepted only if the prefix reads back correctly (at most one slot wrong),
D1/D2 name a mode we implement (INT-019) and the count symbols are valid (INT-001).
Anything else is treated as a false alarm and the search continues.

A fade can also turn the count slots into another *valid* count, and a wrong count puts the
data phase in the wrong place: the message is lost. So the count is checked against the
next segments, which must count down from it. The first of the next two that reads validly
decides; if neither does, the count stands (the last segment, count 0, has nothing after it
to check against).
"""

from dataclasses import dataclass

import numpy as np

from .. import scrambler
from ..modulate import rrc_taps
from ..spec import tables as T
from ..spec.modes import ModeSpec, mode, mode_from_d1_d2

SLOT_TRIBITS = 32
SLOTS_PER_SEGMENT = T.PREAMBLE_CHANNEL_SYMBOLS_PER_SEGMENT  # 15
PREFIX = np.array(T.PREAMBLE_FIXED_PREFIX)  # 0 1 3 0 1 3 1 2 0
PREFIX_VALUES = sorted(set(PREFIX.tolist()))  # the waveforms the search needs: 0, 1, 2, 3

#: rho threshold. Measured with in-band normalisation: noise alone reaches at most 0.046
#: (10 s, all positions and bins, at 9600 and 48000 S/s); a preamble gives about 0.6 at
#: +10 dB, 0.3 at 0 dB, 0.12 at -6 dB and 0.06 at -9 dB SNR (3 kHz).
DETECTION_THRESHOLD = 0.08

#: How many following segments may confirm or contradict a count, and how many prefix
#: slots such a segment may have wrong: its position is known, so the prefix serves less as
#: a false-alarm check there (docs/PERFORMANCE.md, M11).
CONFIRM_SEGMENTS = 2
CONFIRM_PREFIX_ERRORS = 3


@dataclass
class Detection:
    """A preamble segment found in the signal."""

    segment_start: float  # sample index of the segment's first symbol (start of its pulse)
    carrier_hz: float  # refined carrier frequency
    metric: float  # rho at the peak
    mode: ModeSpec
    d1: int
    d2: int
    count: int  # segments still to come after this one
    prefix_errors: int  # prefix slots that read back wrong (0 or 1)

    @property
    def data_start_symbol(self) -> int:
        """Data-phase start, in symbols after this segment's first symbol."""
        return (self.count + 1) * SLOTS_PER_SEGMENT * SLOT_TRIBITS


def lowpass_taps(sample_rate: int, cutoff_hz: float, transition_hz: float) -> np.ndarray:
    """Linear-phase low-pass FIR (Hamming-windowed sinc), odd length, unity gain at DC."""
    n = int(np.ceil(3.3 * sample_rate / transition_hz)) | 1  # Hamming: ~3.3/transition taps
    t = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * cutoff_hz / sample_rate * t) * np.hamming(n)
    return h / h.sum()


def slot_waveforms(sps: int, rolloff: float = 0.25) -> np.ndarray:
    """The eight possible 32-symbol preamble slots as transmitted (pulse-shaped, unit-
    amplitude phasors, carrier removed). Row v starts at the first tap of the first pulse."""
    taps = rrc_taps(sps, rolloff)
    seq = scrambler.sync_sequence(0, SLOT_TRIBITS)
    out = []
    for v in range(8):
        tribits = scrambler.scramble(np.array(T.TABLE_XIII[v], dtype=np.uint8), seq)
        impulses = np.zeros(SLOT_TRIBITS * sps, dtype=complex)
        impulses[::sps] = np.exp(1j * np.pi / 4 * tribits)
        out.append(np.convolve(impulses, taps))
    return np.array(out)


class PreambleSearch:
    """Streaming preamble detector. Feed samples; it returns a :class:`Detection` or None."""

    def __init__(
        self,
        sample_rate: int = 9600,
        carrier_hz: float = T.CARRIER_HZ,
        max_offset_hz: float = 400.0,
        bin_hz: float = 25.0,
        threshold: float = DETECTION_THRESHOLD,
        rolloff: float = 0.25,
        block: int = 4096,
        zero_interleave: bool = False,
    ):
        if sample_rate % T.SYMBOL_RATE:
            raise ValueError(f"sample rate must be a multiple of {T.SYMBOL_RATE} Hz")
        self.sample_rate = sample_rate
        self.sps = sample_rate // T.SYMBOL_RATE
        self.carrier_hz = carrier_hz
        self.threshold = threshold
        self.zero_interleave = zero_interleave
        self.waveforms = slot_waveforms(self.sps, rolloff)
        self.ref_energy = float(np.sum(np.abs(self.waveforms[0]) ** 2))
        self.slot = SLOT_TRIBITS * self.sps  # S: slot length in samples
        self.ref_len = self.waveforms.shape[1]
        self.bins = np.arange(-max_offset_hz, max_offset_hz + bin_hz / 2, bin_hz)
        self.block = block
        # Band-limit before searching, so that rho measures in-band quality whatever the
        # sample rate, and the mirror image of a real (audio) input is removed.
        # Passband must reach 1500 Hz (signal half-bandwidth) + the largest offset; the
        # windowed-sinc cutoff sits mid-transition, so add half the transition width.
        self.prefilter = lowpass_taps(sample_rate, 1500 + max_offset_hz + 200, transition_hz=400)
        self.prefilter_delay = (self.prefilter.size - 1) // 2
        self._history = np.zeros(self.prefilter.size - 1, dtype=complex)
        # Samples needed beyond a position n to judge it AND read its whole segment, and
        # the segments that confirm its count.
        self.lookahead = (
            ((CONFIRM_SEGMENTS + 1) * SLOTS_PER_SEGMENT - 1) * self.slot + self.ref_len + 2
        )
        self._z = np.zeros(0, dtype=complex)  # mixed-down signal
        self._z_start = 0  # global index of _z[0]
        self._samples_in = 0
        self._next = 0  # next position to evaluate
        self._nfft = 1 << int(
            np.ceil(np.log2(block + (len(PREFIX) - 1) * self.slot + self.ref_len))
        )
        m = np.arange(self.ref_len)
        # Frequency-shifted references, FFT'd once: refs[f][v] for the prefix waveforms.
        self._ref_fft = {
            f: {
                v: np.conj(
                    np.fft.fft(
                        self.waveforms[v] * np.exp(2j * np.pi * f * m / sample_rate), self._nfft
                    )
                )
                for v in PREFIX_VALUES
            }
            for f in self.bins
        }

    # -- streaming -------------------------------------------------------------------

    def restart(self, position: int) -> None:
        """Resume searching at global sample ``position`` (e.g. after a message ended)."""
        self._next = max(self._next, position)
        self._trim()

    def process(self, samples: np.ndarray) -> Detection | None:
        x = np.asarray(samples)
        n = self._samples_in + np.arange(x.size)
        self._samples_in += x.size
        cycles = np.mod(self.carrier_hz * n, self.sample_rate) / self.sample_rate
        mixed = np.concatenate([self._history, x * np.exp(-2j * np.pi * cycles)])
        self._history = mixed[mixed.size - self._history.size :]
        # "valid" convolution: one output per new input sample. Output index k holds the
        # filtered signal delayed by prefilter_delay samples.
        self._z = np.concatenate([self._z, np.convolve(mixed, self.prefilter, mode="valid")])
        return self._search(final=False)

    def flush(self) -> Detection | None:
        """End of input: evaluate the remaining positions, even if less than a block."""
        return self._search(final=True)

    def _search(self, final: bool) -> Detection | None:
        while True:
            end = self._z_start + self._z.size  # first global index not yet available
            count = min(self.block, end - self.lookahead - self._next)
            # Each evaluation costs the same whatever its size (one FFT per bin and
            # waveform), so wait for a whole block unless the input has ended.
            if count <= 0 or (count < self.block and not final):
                return None
            detection, advanced = self._search_block(self._next, count)
            if detection is not None:
                return detection
            if not advanced:
                return None  # a candidate needs more samples; try again next call
            self._trim()

    def _trim(self) -> None:
        drop = max(0, min(self._next, self._z_start + self._z.size) - self._z_start)
        self._z = self._z[drop:]
        self._z_start += drop

    # -- the metric --------------------------------------------------------------------

    def _rho(self, start: int, count: int) -> np.ndarray:
        """rho for positions start .. start+count-1, all frequency bins: shape (bins, count)."""
        a = start - self._z_start
        span = count + (len(PREFIX) - 1) * self.slot + self.ref_len
        z = self._z[a : a + span]
        z_fft = np.fft.fft(z, self._nfft)
        # Energy in each slot window, for normalisation.
        csum = np.concatenate([[0.0], np.cumsum(np.abs(z) ** 2)])
        win_energy = csum[self.ref_len :] - csum[: -self.ref_len]
        energy = sum(win_energy[j * self.slot : j * self.slot + count] for j in range(len(PREFIX)))
        # Where there is essentially no signal (e.g. digital silence), both correlation and
        # energy are rounding noise and their ratio is meaningless. A *relative* floor
        # (an absolute one like 1e-30 would be swamped by FFT rounding) sets rho to 0 there.
        silent = energy <= 1e-12 * max(float(energy.max()), 1e-300)
        rho = np.empty((len(self.bins), count))
        for i, f in enumerate(self.bins):
            corr = {v: np.fft.ifft(z_fft * self._ref_fft[f][v]) for v in PREFIX_VALUES}
            total = np.zeros(count)
            for j, v in enumerate(PREFIX):
                total += np.abs(corr[v][j * self.slot : j * self.slot + count]) ** 2
            # One huge sample (garbage read as cf32 reaches 3.4e38) swamps the block: its
            # energy dwarfs everything after it, and the ratio may overflow. That block's
            # values are meaningless and the preamble is found in a later segment.
            with np.errstate(over="ignore", invalid="ignore"):
                ratio = total / (self.ref_energy * np.maximum(energy, 1e-300))
            rho[i] = np.where(silent, 0.0, ratio)
        return rho

    def _search_block(self, start: int, count: int) -> tuple[Detection | None, bool]:
        """Evaluate positions start .. start+count-1. Returns (detection, advanced): whether
        the search position moved on. It does not when a candidate needs more samples."""
        rho = self._rho(start, count)
        best = rho.max(axis=0)
        above = np.flatnonzero(best >= self.threshold)
        if above.size == 0:
            self._next = start + count
            return None, True
        # The true peak can lie up to a slot after the first threshold crossing (a slot-
        # shifted alignment also matches partially), so examine one slot from the crossing.
        first = start + int(above[0])
        available = self._z_start + self._z.size - self.lookahead - first
        if available < self.slot:
            self._next = first  # wait for enough samples to see the whole slot
            return None, first > start
        region = self._rho(first, self.slot)
        i_bin, i_pos = np.unravel_index(np.argmax(region), region.shape)
        peak = first + int(i_pos)
        detection = self._refine_and_read(peak, int(i_bin))
        if detection is None:
            # False alarm (or a mode we do not implement): continue past this peak. A true
            # correlation peak is about one symbol wide, so skip a symbol.
            self._next = peak + self.sps
            return None, True
        self._next = peak + SLOTS_PER_SEGMENT * self.slot
        return detection, True

    def _refine_and_read(self, peak: int, i_bin: int) -> Detection | None:
        # Timing: parabola through rho at peak-1, peak, peak+1.
        rho3 = self._rho(peak - 1, 3)[i_bin] if peak - 1 >= self._z_start else None
        frac = 0.0
        if rho3 is not None:
            denom = rho3[0] - 2 * rho3[1] + rho3[2]
            if denom < 0:
                frac = float(np.clip(0.5 * (rho3[0] - rho3[2]) / denom, -0.5, 0.5))
        coarse_f = float(self.bins[i_bin])
        reading = read_segment(
            self._z, peak - self._z_start, coarse_f, self.waveforms, self.slot, self.sample_rate
        )
        fields = segment_fields(reading)
        if fields is None:
            return None
        found, d1, d2, count = fields
        _, residual_hz, prefix_errors = reading
        for j in range(1, min(CONFIRM_SEGMENTS, count) + 1):
            after = read_segment(
                self._z,
                peak - self._z_start + j * SLOTS_PER_SEGMENT * self.slot,
                coarse_f,
                self.waveforms,
                self.slot,
                self.sample_rate,
            )
            following = segment_fields(after, CONFIRM_PREFIX_ERRORS)
            if following is None or following[1:3] != (d1, d2):
                continue  # unreadable (or another mode's): look one segment further
            if following[3] != count - j:
                return None
            break
        if self.zero_interleave and found.interleave == "short":
            found = mode(found.rate, "zero")  # INT-012: zero and short share D1/D2
        return Detection(
            segment_start=peak + frac - self.prefilter_delay,
            carrier_hz=self.carrier_hz + coarse_f + residual_hz,
            metric=float(self._rho(peak, 1)[i_bin][0]),
            mode=found,
            d1=d1,
            d2=d2,
            count=count,
            prefix_errors=prefix_errors,
        )


def segment_fields(reading, max_prefix_errors=1):
    """(mode, D1, D2, count) from a :func:`read_segment` result, or None if it is not a
    valid preamble segment."""
    if reading is None:
        return None
    symbols, _, prefix_errors = reading
    if prefix_errors > max_prefix_errors:
        return None
    d1, d2 = symbols[9], symbols[10]
    found = mode_from_d1_d2(d1, d2)
    counts = symbols[11:14]
    if found is None or min(counts) < 4 or symbols[14] != 0:
        return None
    count = ((counts[0] - 4) << 4) | ((counts[1] - 4) << 2) | (counts[2] - 4)  # INT-001
    if count >= found.preamble_segments:
        return None
    return found, d1, d2, count


def read_segment(z, a, coarse_f, waveforms, slot, sample_rate):
    """Classify the 15 slots of a segment starting at index ``a`` of ``z``.

    Returns (slot symbols, residual frequency in Hz, number of prefix slots read wrong), or
    None if the segment runs past the end of ``z``.
    """
    ref_len = waveforms.shape[1]
    if a < 0 or a + (SLOTS_PER_SEGMENT - 1) * slot + ref_len > z.size:
        return None
    m = np.arange(ref_len)
    # Coherent correlation of each slot with all 8 waveforms, at the coarse frequency.
    corr = np.empty((SLOTS_PER_SEGMENT, 8), dtype=complex)
    for j in range(SLOTS_PER_SEGMENT):
        start = a + j * slot
        # Mix out the coarse offset with an absolute time reference, so phases line up
        # from slot to slot.
        seg = z[start : start + ref_len] * np.exp(
            -2j * np.pi * coarse_f * (j * slot + m) / sample_rate
        )
        corr[j] = waveforms.conj() @ seg
    # Residual frequency from the prefix: phase advance per slot, by least squares.
    prefix_corr = corr[np.arange(len(PREFIX)), PREFIX]
    phase = np.unwrap(np.angle(prefix_corr))
    slope, intercept = np.polyfit(np.arange(len(PREFIX)), phase, 1)  # radians per slot
    residual_hz = slope / (2 * np.pi * slot / sample_rate)
    # Decide the slots coherently. In the prefix the phase reference is the fitted ramp.
    # After it, each slot is judged against the phase of the slot before it (as decided),
    # advanced by the residual frequency: on a fading channel the phase wanders, and a
    # ramp extrapolated from the prefix misread the count slots, up to 190 ms later, often
    # enough to miss up to 1 in 6 short preambles at 5-Hz fading (docs/PERFORMANCE.md, M11).
    rotation = np.exp(-1j * (intercept + slope * np.arange(len(PREFIX))))
    decided = np.zeros(SLOTS_PER_SEGMENT, dtype=int)
    decided[: len(PREFIX)] = np.argmax(np.real(corr[: len(PREFIX)] * rotation[:, None]), axis=1)
    for j in range(len(PREFIX), SLOTS_PER_SEGMENT):
        reference = corr[j - 1, decided[j - 1]] * np.exp(1j * slope)
        decided[j] = int(np.argmax(np.real(corr[j] * np.conj(reference))))
    prefix_errors = int(np.count_nonzero(decided[: len(PREFIX)] != PREFIX))
    return [int(v) for v in decided], float(residual_hz), prefix_errors
