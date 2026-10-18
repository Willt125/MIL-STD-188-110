"""Forward error correction: the K=7 rate-1/2 convolutional code of §5.3.2.3.3.

Transmit side
    :class:`ConvolutionalEncoder` turns each input bit into two output bits, T1 then T2
    (Figure 4). :func:`repeat_pairs` implements the 150/300-bps repetition (Table V).

Receive side
    :func:`combine_pairs` undoes the repetition by soft combining.
    :class:`ViterbiDecoder` is a streaming soft-decision maximum-likelihood decoder.

Soft-value convention (used throughout the receiver)
    A *soft bit* is a log-likelihood ratio ``L = log P(b=0) / P(b=1)``. Positive means
    "probably 0", negative means "probably 1", and 0 means "no information" (an erasure).
    A noiseless BPSK-like mapping is therefore ``0 -> +1`` and ``1 -> -1``; see
    :func:`hard_to_soft`.
"""

import numpy as np

from .spec import tables as T

K = T.CONSTRAINT_LENGTH  # 7
MEMORY = K - 1  # 6 previous input bits make up the encoder state
NUM_STATES = 1 << MEMORY  # 64


def _taps_from_exponents(exponents: tuple[int, ...]) -> np.ndarray:
    """Generator polynomial exponents -> tap vector indexed by delay (INT-002).

    Figure 4 shows the input I(X) entering the cell labelled x^6, so the x^6 cell holds
    the newest bit (delay 0) and the cell labelled 1 holds the oldest (delay 6). The tap
    for exponent e is therefore at delay ``6 - e``.
    """
    taps = np.zeros(K, dtype=np.uint8)
    for e in exponents:
        taps[MEMORY - e] = 1
    return taps


#: Tap vectors indexed by delay 0..6. T1 = 1011011 (octal 133), T2 = 1111001 (octal 171).
TAPS_T1 = _taps_from_exponents(T.GENERATOR_T1_EXPONENTS)
TAPS_T2 = _taps_from_exponents(T.GENERATOR_T2_EXPONENTS)


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------


class ConvolutionalEncoder:
    """Streaming rate-1/2 encoder. Call :meth:`encode` repeatedly; state carries over.

    The register starts cleared (all zero) at the start of a transmission (INT-023).
    The coder flush of §5.3.2.3.2 is not done here: the framer feeds 144 zero bits through
    :meth:`encode` like any other input, which returns the register to zero.
    """

    def __init__(self):
        # The last MEMORY input bits, oldest first, so that history + new input is a
        # contiguous time series that can be convolved with the taps.
        self._history = np.zeros(MEMORY, dtype=np.uint8)

    def encode(self, bits: np.ndarray) -> np.ndarray:
        """Encode ``bits``; return twice as many bits, interleaved T1, T2, T1, T2, ..."""
        bits = np.asarray(bits, dtype=np.uint8)
        if bits.size == 0:
            return np.zeros(0, dtype=np.uint8)
        series = np.concatenate([self._history, bits])
        # Each output is the mod-2 sum of the taps applied to the newest K bits. "valid"
        # convolution gives exactly one output per new input bit.
        t1 = np.convolve(series, TAPS_T1, mode="valid") % 2
        t2 = np.convolve(series, TAPS_T2, mode="valid") % 2
        self._history = series[-MEMORY:].copy()
        out = np.empty(2 * bits.size, dtype=np.uint8)
        out[0::2] = t1  # "the upper output bit T1(x) being taken first" (§5.3.2.3.3 a)
        out[1::2] = t2
        return out

    @property
    def state(self) -> int:
        """Encoder state in the decoder's numbering (see :class:`ViterbiDecoder`)."""
        return _state_from_history(self._history)


def _state_from_history(history_oldest_first: np.ndarray) -> int:
    """State number: bit 5 = most recent input, bit 0 = oldest of the six."""
    state = 0
    for bit in history_oldest_first:  # oldest first, so it ends up in bit 0
        state = (state >> 1) | (int(bit) << (MEMORY - 1))
    return state


def encode(bits: np.ndarray) -> np.ndarray:
    """One-shot encode from a cleared register."""
    return ConvolutionalEncoder().encode(bits)


# ---------------------------------------------------------------------------
# Repetition (150 and 300 bps)
# ---------------------------------------------------------------------------


def repeat_pairs(coded: np.ndarray, repetitions: int) -> np.ndarray:
    """Repeat each (T1, T2) pair as a unit: T1 T2 T1 T2 ... (INT-008, §5.3.2.3.3 b)."""
    pairs = np.asarray(coded).reshape(-1, 2)
    return np.repeat(pairs, repetitions, axis=0).reshape(-1)


def combine_pairs(soft: np.ndarray, repetitions: int) -> np.ndarray:
    """Inverse of :func:`repeat_pairs` for soft bits: add the copies of each pair.

    Adding log-likelihood ratios of independent observations of the same bit is the
    optimal combination when the copies see the same noise level.
    """
    soft = np.asarray(soft, dtype=float)
    if soft.size % (2 * repetitions):
        raise ValueError("soft-bit count is not a whole number of repeated pairs")
    return soft.reshape(-1, repetitions, 2).sum(axis=1).reshape(-1)


def hard_to_soft(bits: np.ndarray, amplitude: float = 1.0) -> np.ndarray:
    """Noiseless soft bits for ``bits``: 0 -> +amplitude, 1 -> -amplitude."""
    return amplitude * (1.0 - 2.0 * np.asarray(bits, dtype=float))


# ---------------------------------------------------------------------------
# Trellis
# ---------------------------------------------------------------------------
# State numbering: the six previous input bits, most recent in bit 5. Feeding input u in
# state s moves to state (u << 5) | (s >> 1). Equivalently, each next state ns has two
# predecessors, ((ns & 31) << 1) | b for b in {0, 1}, and was reached with input ns >> 5.


def _branch_outputs(state: int, u: int) -> tuple[int, int]:
    """(T1, T2) emitted when input ``u`` arrives in ``state``."""
    window = [u] + [(state >> (MEMORY - d)) & 1 for d in range(1, K)]  # by delay 0..6
    # int(): the taps are uint8, and uint8 arithmetic would make 1 - 2*t wrap to 255.
    t1 = sum(w & int(t) for w, t in zip(window, TAPS_T1, strict=True)) % 2
    t2 = sum(w & int(t) for w, t in zip(window, TAPS_T2, strict=True)) % 2
    return t1, t2


def _build_trellis():
    pred = np.zeros((NUM_STATES, 2), dtype=np.intp)
    sign1 = np.zeros((NUM_STATES, 2))
    sign2 = np.zeros((NUM_STATES, 2))
    for ns in range(NUM_STATES):
        u = ns >> (MEMORY - 1)
        for b in range(2):
            p = ((ns & (NUM_STATES // 2 - 1)) << 1) | b
            t1, t2 = _branch_outputs(p, u)
            pred[ns, b] = p
            sign1[ns, b] = 1 - 2 * t1  # +1 if the branch emits 0, -1 if it emits 1
            sign2[ns, b] = 1 - 2 * t2
    return pred, sign1, sign2


#: PREDECESSOR[ns, b] is the b-th state leading to ns; SIGN1/SIGN2 hold the branch's
#: output bits as +1 (bit 0) or -1 (bit 1), the form in which they meet soft bits.
PREDECESSOR, SIGN1, SIGN2 = _build_trellis()


# ---------------------------------------------------------------------------
# Viterbi decoder
# ---------------------------------------------------------------------------


class ViterbiDecoder:
    """Streaming soft-decision Viterbi decoder for the code above.

    Feed soft bits (pairs T1, T2) with :meth:`decode`, which returns decoded bits as soon
    as they are settled; call :meth:`flush` at the end to get the rest.

    Memory is bounded: survivor decisions are kept for at most ``2 * traceback_depth``
    trellis steps. Whenever that many accumulate, the decoder traces back from the
    currently best state and releases the oldest ``traceback_depth`` bits. Because this
    depends only on the number of steps, not on how input is chunked, the output is
    identical however the soft bits are split across calls.

    Path metric: the correlation between soft bits and the branch's (+1/-1) outputs,
    which is maximised. For Gaussian noise this is equivalent to minimum Euclidean
    distance, i.e. maximum likelihood.
    """

    def __init__(self, traceback_depth: int = 96, start_state: int | None = 0):
        """``start_state=None`` means the starting state is unknown (all equally likely).

        A traceback depth of 5 to 6 constraint lengths (35-42) is the textbook minimum
        for negligible loss; 96 leaves a wide margin at a modest memory cost.
        """
        if traceback_depth < 1:
            raise ValueError("traceback_depth must be positive")
        self.traceback_depth = traceback_depth
        self.start_state = start_state
        self._metrics = np.zeros(NUM_STATES)
        if start_state is not None:
            self._metrics[:] = -np.inf
            self._metrics[start_state] = 0.0
        self._decisions: list[np.ndarray] = []  # one (64,) array of 0/1 per step
        self._leftover = np.zeros(0)  # an odd trailing soft bit waiting for its partner
        # The best path's metric so far, and the most any path could have scored (the sum of
        # |soft|): their ratio, :attr:`fit`, says how well the input looks like this code.
        self.path_metric = 0.0
        self.soft_magnitude = 0.0

    def decode(self, soft: np.ndarray) -> np.ndarray:
        """Process more soft bits; return any decoded bits that are now settled."""
        soft = np.concatenate([self._leftover, np.asarray(soft, dtype=float)])
        usable = soft.size - soft.size % 2
        self._leftover = soft[usable:]
        out = []
        rows = np.arange(NUM_STATES)
        for s1, s2 in soft[:usable].reshape(-1, 2):
            # Add-compare-select for all 64 states at once.
            candidates = self._metrics[PREDECESSOR] + s1 * SIGN1 + s2 * SIGN2
            choice = np.argmax(candidates, axis=1)
            self._metrics = candidates[rows, choice]
            best = self._metrics.max()
            self._metrics -= best  # keep numbers bounded
            self.path_metric += best
            self.soft_magnitude += abs(s1) + abs(s2)
            self._decisions.append(choice.astype(np.uint8))
            if len(self._decisions) == 2 * self.traceback_depth:
                bits = self._traceback(int(np.argmax(self._metrics)))
                out.append(bits[: self.traceback_depth])
                del self._decisions[: self.traceback_depth]
        return np.concatenate(out) if out else np.zeros(0, dtype=np.uint8)

    @property
    def fit(self) -> float | None:
        """Best path metric over the most any path could score, in [-1, 1]: 1 when every soft
        bit agrees with one code sequence. Soft bits that are not this code's output (noise,
        or a code sequence permuted by the wrong deinterleaver) fit well below 1, because the
        decoder can only choose among code sequences. None before any input."""
        return self.path_metric / self.soft_magnitude if self.soft_magnitude > 0 else None

    def flush(self, terminated: bool = True) -> np.ndarray:
        """Return all remaining decoded bits and reset for a new message.

        ``terminated=True`` assumes the encoder was driven back to state 0 (as the coder
        flush bits of §5.3.2.3.2 do) and traces back from that state; otherwise the
        traceback starts from the best state.
        """
        if self._leftover.size:
            raise ValueError("odd number of soft bits: a T1/T2 pair is incomplete")
        end = 0 if terminated else int(np.argmax(self._metrics))
        bits = self._traceback(end)
        self.__init__(self.traceback_depth, self.start_state)
        return bits

    def _traceback(self, state: int) -> np.ndarray:
        """Decoded bits for every stored step, oldest first, ending in ``state``."""
        bits = np.empty(len(self._decisions), dtype=np.uint8)
        for i in range(len(self._decisions) - 1, -1, -1):
            bits[i] = state >> (MEMORY - 1)  # the input that led into this state
            state = PREDECESSOR[state, self._decisions[i][state]]
        return bits


def viterbi_decode(soft: np.ndarray, terminated: bool = True, **kwargs) -> np.ndarray:
    """One-shot decode of a whole message of soft bits."""
    decoder = ViterbiDecoder(**kwargs)
    return np.concatenate([decoder.decode(soft), decoder.flush(terminated=terminated)])
