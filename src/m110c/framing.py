"""User-data framing: how bytes become the bit stream that enters the modem (INT-016, INT-025).

Two framings are supported, matching the two over-the-air options of MIL-STD-188-110C
Appendix A (A.5.1.2.8):

Synchronous / "data only" (the default)
    Each byte is sent as 8 bits, least significant first (INT-016). Wavecom calls this
    "ASCII SYNC". Every bit on the air is user data.

Asynchronous "standard mode (send-all)" (A.5.1.2.8.2.1)
    Each character is sent as a UART frame: a start bit (0), N data bits least significant
    first, an optional parity bit, then 1 or 2 stop bits (1). The idle line is 1 (stop bits).
    Table A-VIII allows N = 5..8 data bits, 1 or 2 stop bits and no/even/odd parity; with
    N < 8, the least significant N bits of each byte are used. Real 188-110A recordings
    carried 8N1 frames (docs/VALIDATION.md). Wavecom calls this "ASCII ASYNC".

Formats are written in the usual short form: data bits, parity letter (N/E/O), stop bits,
e.g. ``8N1``, ``7E1``, ``8N2``.
"""

from dataclasses import dataclass

import numpy as np

from . import bits as bitops


@dataclass(frozen=True)
class AsyncFormat:
    """One asynchronous character format (Table A-VIII)."""

    data_bits: int = 8
    parity: str = "N"  # "N" none, "E" even, "O" odd
    stop_bits: int = 1

    def __post_init__(self):
        if self.data_bits not in (5, 6, 7, 8):
            raise ValueError("data bits must be 5, 6, 7 or 8 (Table A-VIII)")
        if self.parity not in ("N", "E", "O"):
            raise ValueError("parity must be N, E or O (Table A-VIII)")
        if self.stop_bits not in (1, 2):
            raise ValueError("stop bits must be 1 or 2 (Table A-VIII)")

    @classmethod
    def parse(cls, text: str) -> "AsyncFormat":
        """Parse a format such as ``8N1`` or ``7e2``."""
        text = text.strip().upper()
        if len(text) != 3 or not text[0].isdigit() or not text[2].isdigit():
            raise ValueError(f"async format must look like 8N1, got {text!r}")
        return cls(int(text[0]), text[1], int(text[2]))

    def __str__(self) -> str:
        return f"{self.data_bits}{self.parity}{self.stop_bits}"

    @property
    def frame_bits(self) -> int:
        """Bits per character on the air: start + data + parity + stop."""
        return 1 + self.data_bits + (self.parity != "N") + self.stop_bits

    def parity_bit(self, data: np.ndarray) -> int | None:
        """Parity bit for a character's data bits, or None without parity."""
        if self.parity == "N":
            return None
        ones = int(np.sum(data))
        return ones % 2 if self.parity == "E" else 1 - ones % 2


def sync_encode(data: bytes, msb_first: bool = False) -> np.ndarray:
    """Synchronous framing: just the bits of each byte (INT-016)."""
    return bitops.bytes_to_bits(data, msb_first=msb_first)


def async_encode(data: bytes, fmt: AsyncFormat) -> np.ndarray:
    """Asynchronous framing: one UART frame per byte (A.5.1.2.8.2.1, INT-025)."""
    octets = np.frombuffer(data, dtype=np.uint8)
    if octets.size == 0:
        return np.zeros(0, dtype=np.uint8)
    # Data bits, least significant first; for N < 8 only the N low bits are used.
    data_bits = np.unpackbits(octets[:, None], axis=1, bitorder="little")[:, : fmt.data_bits]
    frames = [np.zeros((octets.size, 1), dtype=np.uint8), data_bits]  # start bit = 0
    if fmt.parity != "N":
        parity = np.array([[fmt.parity_bit(row)] for row in data_bits], dtype=np.uint8)
        frames.append(parity)
    frames.append(np.ones((octets.size, fmt.stop_bits), dtype=np.uint8))  # stop bit(s) = 1
    return np.concatenate(frames, axis=1).reshape(-1)


#: Late entry: characters' worth of bits held to find the character phase (S5).
PHASE_FRAMES = 32


class AsyncDecoder:
    """Streaming receiver-side deframer: bits in, characters out.

    The line idles at 1. A 0 is taken as a start bit; the following data, parity and stop
    bits are read and checked. As a hardware UART does, a character whose stop bit is 0 (a
    framing error) or whose parity is wrong is still delivered, and counted in
    :attr:`framing_errors` / :attr:`parity_errors`. Hunting resumes after the frame. Because
    the line idles at 1, the decoder falls back into step at the next idle gap.
    """

    def __init__(self, fmt: AsyncFormat, find_phase: bool = False):
        """``find_phase``: the bits start somewhere inside a character stream (a late entry,
        S5). Characters sent back to back leave no idle gap to fall into step at, and a
        start-bit hunt can lock onto a wrong phase for good. So the first
        ``PHASE_FRAMES`` characters' worth of bits are held, and the phase at which the most
        frames have a 0 start bit and 1 stop bits is taken."""
        self.fmt = fmt
        self.framing_errors = 0
        self.parity_errors = 0
        self._pending = np.zeros(0, dtype=np.uint8)
        self._find_phase = find_phase

    def process(self, bits: np.ndarray, final: bool = False) -> bytes:
        """Accept more bits; return every complete character. ``final``: no more bits will
        come (a phase still being looked for is then chosen from what there is)."""
        bits = np.concatenate([self._pending, np.asarray(bits, dtype=np.uint8)])
        if self._find_phase:
            if bits.size < PHASE_FRAMES * self.fmt.frame_bits and not final:
                self._pending = bits
                return b""
            bits = bits[self._best_phase(bits) :]
            self._find_phase = False
        out = bytearray()
        n_frame = self.fmt.frame_bits
        i = 0
        while True:
            # Hunt for a start bit.
            while i < bits.size and bits[i] == 1:
                i += 1
            if i + n_frame > bits.size:
                break
            frame = bits[i : i + n_frame]
            data = frame[1 : 1 + self.fmt.data_bits]
            expected_parity = self.fmt.parity_bit(data)
            if expected_parity is not None and frame[1 + self.fmt.data_bits] != expected_parity:
                self.parity_errors += 1
            if not frame[n_frame - self.fmt.stop_bits :].all():
                self.framing_errors += 1
            out.append(int(np.sum(data.astype(int) << np.arange(data.size))))
            i += n_frame
        self._pending = bits[i:].copy()
        return bytes(out)

    def _best_phase(self, bits: np.ndarray) -> int:
        """The bit offset (within one frame) at which the most frames look like frames."""
        n = self.fmt.frame_bits
        scores = []
        for phase in range(n):
            frames = bits[phase : phase + (bits.size - phase) // n * n].reshape(-1, n)
            good = (frames[:, 0] == 0) & frames[:, n - self.fmt.stop_bits :].all(axis=1)
            scores.append(int(good.sum()))
        return int(np.argmax(scores))
