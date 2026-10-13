"""Block interleaver of §5.3.2.3.4 (load) and §5.3.2.3.5 (fetch).

The interleaver is a matrix of ``rows x cols`` bits (Table VI). Bits are *loaded* down the
columns in a scrambled row order and *fetched* along diagonals, so that a burst of channel
errors (a fade) is spread across the whole block when the receiver reverses the process.
That turns bursts into the scattered errors the Viterbi decoder handles well.

Load (§5.3.2.3.4)
    Start at column 0. The first bit goes in row 0, and each next bit goes ``step`` rows
    further down, modulo ``rows`` (step 9 mod 40; step 7 mod 20 or mod 10 at 75 bps,
    INT-007).
    Because the step and row count are coprime, ``rows`` bits fill the column exactly once.
    Then move to the next column and repeat.

Fetch (§5.3.2.3.5)
    Start at row 0, column 0. Each next bit is one row down and ``decrement`` columns to the
    left, modulo ``cols`` (17, or 7 at 75 bps). After the last row, return to row 0 and to
    one column right of where the previous diagonal started. The last row is fetched
    before the reset (INT-007).

Every row is visited once per diagonal and every diagonal starts in a different column, so
the fetch reads each cell exactly once. The whole interleaver is therefore just a fixed
permutation of a block of bits, which is how it is implemented here: ``out = block[perm]``.

The spec describes two matrices used alternately (one loading while the other is fetched);
that is a real-time buffering detail and needs no counterpart in this implementation.

Observation: with 18 columns (600, 300 and 150 bps, short interleave), decrementing by 17
is the same as incrementing by 1 (mod 18). The fetch is then a plain diagonal, and bits that
are adjacent on the channel come from load positions only 9 apart. That is weaker than the
other settings, but it is what the spec's parameters produce, not a misreading.
"""

import numpy as np

from .spec.modes import ModeSpec


def load_positions(rows: int, cols: int, row_step: int) -> np.ndarray:
    """Flat matrix index (row * cols + col) of the k-th loaded bit, for every k."""
    k = np.arange(rows * cols)
    row = (k * row_step) % rows
    col = k // rows
    return row * cols + col


def fetch_positions(rows: int, cols: int, col_decrement: int) -> np.ndarray:
    """Flat matrix index of the k-th fetched bit, for every k."""
    diagonal = np.repeat(np.arange(cols), rows)  # starting column of each diagonal
    row = np.tile(np.arange(rows), cols)
    col = (diagonal - col_decrement * row) % cols
    return row * cols + col


def permutation(mode: ModeSpec) -> np.ndarray:
    """``perm`` such that the interleaver output is ``block[perm]``.

    Fetched bit k sits in the cell that was loaded by input bit ``perm[k]``.
    """
    rows, cols = mode.interleaver_rows, mode.interleaver_cols
    load = load_positions(rows, cols, mode.interleaver_load_row_step)
    fetch = fetch_positions(rows, cols, mode.interleaver_fetch_col_decrement)
    input_index_at_cell = np.empty(rows * cols, dtype=np.intp)
    input_index_at_cell[load] = np.arange(rows * cols)
    return input_index_at_cell[fetch]


class _BlockPermuter:
    """Collects input into whole blocks and applies a fixed permutation to each.

    Streaming: :meth:`process` accepts any amount of input and returns every block that
    is now complete; at most one partial block is ever held. With ``perm=None`` (zero
    interleave, 4800 bps) input passes straight through.
    """

    def __init__(self, perm: np.ndarray | None):
        self._perm = perm
        self._pending: list[np.ndarray] = []
        self._pending_count = 0

    @property
    def block_size(self) -> int | None:
        return None if self._perm is None else self._perm.size

    @property
    def pending(self) -> int:
        """Number of input values waiting for their block to complete."""
        return self._pending_count

    def process(self, values: np.ndarray) -> np.ndarray:
        values = np.asarray(values)
        if self._perm is None:
            return values.copy()
        self._pending.append(values)
        self._pending_count += values.size
        if self._pending_count < self._perm.size:
            # Only join the pieces once a block is complete: joining on every call would
            # make tiny writes (one bit at a time down a pipe) quadratic in block size.
            return np.zeros(0, dtype=values.dtype)
        n_blocks = self._pending_count // self._perm.size
        buffered = np.concatenate(self._pending)
        cut = n_blocks * self._perm.size
        remainder = buffered[cut:]
        self._pending = [remainder]
        self._pending_count = remainder.size
        blocks = buffered[:cut].reshape(n_blocks, self._perm.size)
        return blocks[:, self._perm].reshape(-1)

    def flush(self) -> None:
        """Check that the stream ended on a block boundary.

        The transmitter pads the final block with flush bits (§5.3.2.3.2), so a partial
        block left over at the end means the caller lost or added data.
        """
        if self._pending_count:
            raise ValueError(f"{self._pending_count} values left over: not a whole block")


class Interleaver(_BlockPermuter):
    """Transmit-side interleaver for ``mode`` (bypassed when the mode has none)."""

    def __init__(self, mode: ModeSpec):
        super().__init__(permutation(mode) if mode.has_interleaver else None)


class Deinterleaver(_BlockPermuter):
    """Receive-side inverse: works on bits or on soft values of any dtype."""

    def __init__(self, mode: ModeSpec):
        if mode.has_interleaver:
            perm = permutation(mode)
            inverse = np.empty_like(perm)
            inverse[perm] = np.arange(perm.size)
            super().__init__(inverse)
        else:
            super().__init__(None)
