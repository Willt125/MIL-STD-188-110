"""Constants transcribed from MIL-STD-188-110C w/Change 1, section 5.3.2 (serial tone).

Rules for this module:

* Values are typed in from the spec as printed. Nothing is derived or computed here;
  derived quantities live in :mod:`m110c.spec.modes`, where tests check them against
  the numbers the spec states in prose.
* Every constant cites the paragraph, table or figure it comes from.
* Where the printed spec is wrong or ambiguous, the constant is still transcribed as
  printed and the comment points to the relevant entry in ``docs/SPEC_NOTES.md``.
* Only fixed-frequency operation is transcribed. Frequency-hopping rows (e.g. "75H")
  are deliberately omitted.
"""

from fractions import Fraction
from typing import NamedTuple

# ---------------------------------------------------------------------------
# General waveform parameters
# ---------------------------------------------------------------------------

#: §5.3.2.1: "constant 2400-symbols-per-second waveform regardless of ... throughput rate".
SYMBOL_RATE = 2400

#: §5.3.2.3.9 a: tribit numbers are 45-degree increments of an 1800-Hz sinewave.
CARRIER_HZ = 1800

#: §5.3.2.3.9 a: 0 -> 0 degrees, 1 -> 45 degrees, 2 -> 90 degrees, ...
DEGREES_PER_TRIBIT = 45

#: §5.3.1.1 / Table XV: the user bit rates of the fixed-frequency serial-tone waveform.
DATA_RATES = (4800, 2400, 1200, 600, 300, 150, 75)

#: §5.3.2.3.4: interleave settings and their block durations in seconds.
#: "zero" is the short interleaver switched to 0.0 s (bypass), see §5.3.2.3.7.2.1 note.
INTERLEAVE_SECONDS = {"zero": Fraction(0), "short": Fraction(6, 10), "long": Fraction(48, 10)}

# ---------------------------------------------------------------------------
# EOM and flush (§5.3.2.3.1, §5.3.2.3.2)
# ---------------------------------------------------------------------------

#: §5.3.2.3.1: EOM sequence, transmitted most significant digit first (INT-011).
EOM_SEQUENCE = 0x4B65A5B2
EOM_BITS = 32

#: §5.3.2.3.2: "144 bits (for coder flush)". Flush bits are "0".
CODER_FLUSH_BITS = 144

# ---------------------------------------------------------------------------
# FEC encoder (§5.3.2.3.3, Figure 4, Table V)
# ---------------------------------------------------------------------------

#: Figure 4: constraint length 7.
CONSTRAINT_LENGTH = 7

#: Figure 4 generator polynomials, as the exponents that appear in each polynomial.
#: T1: X^6 + X^4 + X^3 + X + 1;  T2: X^6 + X^5 + X^4 + X^3 + 1.
#: How exponents map onto register delays is an interpretation, see INT-002 and fec.py.
GENERATOR_T1_EXPONENTS = (6, 4, 3, 1, 0)
GENERATOR_T2_EXPONENTS = (6, 5, 4, 3, 0)


class CodingRow(NamedTuple):
    """One row of Table V (fixed-frequency error-correcting coding)."""

    effective_code_rate: Fraction | None  # None means "no coding"
    repetitions: int  # how many times each (T1, T2) pair is sent


#: Table V: Error-correcting coding, fixed frequency operation.
TABLE_V = {
    4800: CodingRow(None, 1),  # "(no coding)"; §5.3.2.3.3 c: FEC encoder bypassed
    2400: CodingRow(Fraction(1, 2), 1),  # "Rate 1/2"
    1200: CodingRow(Fraction(1, 2), 1),  # "Rate 1/2 code"
    600: CodingRow(Fraction(1, 2), 1),  # "Rate 1/2 code"
    300: CodingRow(Fraction(1, 4), 2),  # "Rate 1/2 code repeated 2 times"
    150: CodingRow(Fraction(1, 8), 4),  # "Rate 1/2 code repeated 4 times"
    75: CodingRow(Fraction(1, 2), 1),  # "Rate 1/2"
}

# ---------------------------------------------------------------------------
# Interleaver (§5.3.2.3.4, §5.3.2.3.5, Table VI)
# ---------------------------------------------------------------------------


class InterleaverDims(NamedTuple):
    rows: int
    cols: int


#: Table VI: interleaver matrix dimensions (fixed-frequency rows; 75 is the "75N" row).
#: 4800 bps has no entry: the interleaver is bypassed (§5.3.2.3.4 note).
TABLE_VI = {
    2400: {"long": InterleaverDims(40, 576), "short": InterleaverDims(40, 72)},
    1200: {"long": InterleaverDims(40, 288), "short": InterleaverDims(40, 36)},
    600: {"long": InterleaverDims(40, 144), "short": InterleaverDims(40, 18)},
    300: {"long": InterleaverDims(40, 144), "short": InterleaverDims(40, 18)},
    150: {"long": InterleaverDims(40, 144), "short": InterleaverDims(40, 18)},
    75: {"long": InterleaverDims(20, 36), "short": InterleaverDims(10, 9)},
}

#: §5.3.2.3.4: on load, "the row location for the bits increases by 9 modulo 40".
INTERLEAVE_LOAD_ROW_STEP = 9
INTERLEAVE_LOAD_ROW_MODULUS = 40
#: §5.3.2.3.4 a, b: at 75 bps fixed-frequency, "advanced by 7 modulo 20" (long) and
#: "advanced by 7 modulo 10" (short). The moduli are transcribed separately from Table VI
#: so that a rows/columns mix-up in Table VI is caught by a test.
INTERLEAVE_LOAD_ROW_STEP_75 = 7
INTERLEAVE_LOAD_ROW_MODULUS_75 = {"long": 20, "short": 10}

#: §5.3.2.3.5: on fetch, the row increments by one and the column "decrement[s] ... by 17".
INTERLEAVE_FETCH_COL_DECREMENT = 17
#: §5.3.2.3.5 b: "the decrement value of the column number shall be 7 rather than 17".
INTERLEAVE_FETCH_COL_DECREMENT_75 = 7

#: Table VII: bits fetched per channel symbol (fixed-frequency rows; 75 is "75N").
#: 4800 bps is not listed in Table VII; Table XV gives 3 bits per channel symbol.
TABLE_VII = {2400: 3, 1200: 2, 600: 1, 300: 1, 150: 1, 75: 2}

# ---------------------------------------------------------------------------
# Modified-Gray decoder (§5.3.2.3.6, Tables VIII and IX)
# ---------------------------------------------------------------------------

#: Table VIII: Modified Gray decoding at 2400 and 4800 bps.
#: (first bit, middle bit, last bit) -> decoded value.
TABLE_VIII = {
    (0, 0, 0): 0b000,
    (0, 0, 1): 0b001,
    (0, 1, 0): 0b011,
    (0, 1, 1): 0b010,
    (1, 0, 0): 0b111,
    (1, 0, 1): 0b110,
    (1, 1, 0): 0b100,
    (1, 1, 1): 0b101,
}

#: Table IX: Modified-Gray decoding at 75 bps (fixed frequency) and 1200 bps.
#: (first bit, last bit) -> decoded value.
TABLE_IX = {
    (0, 0): 0b00,
    (0, 1): 0b01,
    (1, 0): 0b11,
    (1, 1): 0b10,
}

# ---------------------------------------------------------------------------
# Symbol formation (§5.3.2.3.7, Figure 5, Tables X and XIII)
# ---------------------------------------------------------------------------

#: Figure 5: one-bit channel symbols (0) and (1) sit at tribit numbers 0 and 4 (INT-020).
FIGURE_5_ONE_BIT = {0: 0, 1: 4}
#: Figure 5: two-bit channel symbols sit at tribit numbers 0, 2, 4, 6 (INT-020).
FIGURE_5_TWO_BIT = {0b00: 0, 0b01: 2, 0b10: 4, 0b11: 6}

#: Table X a: channel symbol mapping for 75 bps, normal sets (32 tribits each).
TABLE_X_NORMAL = {
    0b00: (0, 0, 0, 0) * 8,
    0b01: (0, 4, 0, 4) * 8,
    0b10: (0, 0, 4, 4) * 8,
    0b11: (0, 4, 4, 0) * 8,
}

#: Table X b: channel symbol mapping for 75 bps, exceptional sets.
TABLE_X_EXCEPTIONAL = {
    0b00: (0, 0, 0, 0, 4, 4, 4, 4) * 4,
    0b01: (0, 4, 0, 4, 4, 0, 4, 0) * 4,
    0b10: (0, 0, 4, 4, 4, 4, 0, 0) * 4,
    0b11: (0, 4, 4, 0, 4, 0, 0, 4) * 4,
}

#: §5.3.2.3.7.1.1: exceptional sets are "every 45th set ... if short interleave is
#: selected, and every 360th set ... if long interleave is selected" (INT-010).
EXCEPTIONAL_SET_PERIOD = {"short": 45, "long": 360}

#: Table XV: at 75 bps, 32 eight-phase symbols per channel symbol.
TRIBITS_PER_75BPS_SET = 32

#: §5.3.2.3.7.1.2: "The block length shall be 1440 tribit channel symbols for short
#: interleave setting and 11520 tribit channels symbols for the long interleave setting."
#: Zero interleave and 4800 bps use the short value (note in the same paragraph).
KNOWN_DATA_BLOCK_SYMBOLS = {"short": 1440, "long": 11520}

#: §5.3.2.3.7.1.2: the D1/D2 known patterns are 16 tribits (Table XIII repeated twice).
D_PATTERN_TRIBITS = 16

#: Table XIII: Channel symbol mapping for sync preamble (32 tribits each).
TABLE_XIII = {
    0b000: (0, 0, 0, 0, 0, 0, 0, 0) * 4,
    0b001: (0, 4, 0, 4, 0, 4, 0, 4) * 4,
    0b010: (0, 0, 4, 4, 0, 0, 4, 4) * 4,
    0b011: (0, 4, 4, 0, 0, 4, 4, 0) * 4,
    0b100: (0, 0, 0, 0, 4, 4, 4, 4) * 4,
    0b101: (0, 4, 0, 4, 4, 0, 4, 0) * 4,
    0b110: (0, 0, 4, 4, 4, 4, 0, 0) * 4,
    0b111: (0, 4, 4, 0, 4, 0, 0, 4) * 4,
}

# ---------------------------------------------------------------------------
# Sync preamble (§5.3.2.2.1, §5.3.2.3.7.2, Tables XI and XII)
# ---------------------------------------------------------------------------

#: §5.3.2.3.7.2.1: each 200-ms segment is "0, 1, 3, 0, 1, 3, 1, 2, 0, D1, D2, C1, C2, C3, 0".
#: These are the fixed channel symbols that precede D1.
PREAMBLE_FIXED_PREFIX = (0, 1, 3, 0, 1, 3, 1, 2, 0)
#: ... and the fixed channel symbol that follows C3.
PREAMBLE_FIXED_SUFFIX = (0,)
#: "Each 200-ms segment shall consist of a transmission of 15 three bit channel symbols".
PREAMBLE_CHANNEL_SYMBOLS_PER_SEGMENT = 15
PREAMBLE_SEGMENT_SECONDS = Fraction(2, 10)

#: §5.3.2.3.7.2.1: "either three or twenty four 200 millisecond (ms) segments".
PREAMBLE_SEGMENTS = {"zero": 3, "short": 3, "long": 24}

#: §5.3.2.3.7.2.1: the count starts "at 2 for the zero and short ... and 23 for the long".
PREAMBLE_COUNT_START = {"zero": 2, "short": 2, "long": 23}

#: Table XI: assignment of designation symbols D1 and D2, keyed by (rate, interleave).
#: "Short" also covers zero interleave (the spec assigns them the same values, INT-012).
#: 2400 bps digital voice (7, 7) is excluded: not a data mode. Long-interleave 4800 and
#: digital voice are "reserved" and have no entry.
TABLE_XI = {
    (4800, "short"): (7, 6),
    (2400, "short"): (6, 4),
    (2400, "long"): (4, 4),
    (1200, "short"): (6, 5),
    (1200, "long"): (4, 5),
    (600, "short"): (6, 6),
    (600, "long"): (4, 6),
    (300, "short"): (6, 7),
    (300, "long"): (4, 7),
    (150, "short"): (7, 4),
    (150, "long"): (5, 4),
    (75, "short"): (7, 5),
    (75, "long"): (5, 5),
}

#: Table XI: 2400 bps digital voice, short interleave: the ``2400V`` mode (INT-019).
TABLE_XI_DIGITAL_VOICE = (7, 7)
#: Table XI note: "The D1, D2 combinations 5,6 and 5,7 are reserved for application-specific use."
TABLE_XI_APPLICATION_SPECIFIC = ((5, 6), (5, 7))

#: Table XII *as printed*: two-bit count value -> three-bit sync symbol.
#: WARNING: this contradicts the text and the worked note of §5.3.2.3.7.2.1 (INT-001).
#: Do not use it for encoding; it is kept so the contradiction is pinned by a test.
TABLE_XII_AS_PRINTED = {0b00: 4, 0b10: 5, 0b01: 6, 0b11: 7}

#: §5.3.2.3.7.2.1: "Adding a '1' before the two-bit value does this so that this '1'
#: becomes the most significant bit."  This is the rule we follow (INT-001).
COUNT_SYMBOL_PREFIX = 0b100

# ---------------------------------------------------------------------------
# Scramblers (§5.3.2.3.8, Figure 6)
# ---------------------------------------------------------------------------

#: §5.3.2.3.8.1: initial pattern "101110101101 (binary) or BAD (hexadecimal)".
DATA_SCRAMBLER_INIT = 0xBAD
DATA_SCRAMBLER_BITS = 12
#: §5.3.2.3.8.1: "shifted eight times each time a new three bit number is required".
DATA_SCRAMBLER_SHIFTS_PER_SYMBOL = 8
#: §5.3.2.3.8.1: "After 160 transmit symbols, the shift register shall be reset to BAD".
DATA_SCRAMBLER_PERIOD = 160

#: Figure 6, read as a Galois register shifting toward the MSB (INT-003). The bit leaving
#: cell 11 is fed back into cell 0 and XORed into the inputs of these cells:
DATA_SCRAMBLER_FEEDBACK_CELLS = (1, 4, 6)
#: The same register written as a polynomial, as the exponents present (INT-003).
DATA_SCRAMBLER_POLYNOMIAL_EXPONENTS = (12, 6, 4, 1, 0)
#: Figure 6: the scrambler output tribit is taken from these cells as (MSB, middle, LSB).
DATA_SCRAMBLER_OUTPUT_CELLS = (2, 1, 0)

#: §5.3.2.3.8.2: sync preamble scrambling sequence, repeating every 32 symbols.
SYNC_SCRAMBLER_SEQUENCE = tuple(int(c) for c in "74305150221157435026216200505266")

# ---------------------------------------------------------------------------
# Waveform summary (§5.3.2.2.2, Table XV)
# ---------------------------------------------------------------------------


class WaveformRow(NamedTuple):
    """One row of Table XV (fixed-frequency operation waveform characteristics)."""

    coding_rate: Fraction | None  # None means "No coding"
    channel_rate: int  # coded bits per second
    bits_per_channel_symbol: int
    tribits_per_channel_symbol: int  # "8-Phase symbols/channel symbol"
    unknown_per_frame: int | None  # None means "All"
    known_per_frame: int


#: Table XV: Fixed-frequency operation waveform characteristics.
TABLE_XV = {
    4800: WaveformRow(None, 4800, 3, 1, 32, 16),
    2400: WaveformRow(Fraction(1, 2), 4800, 3, 1, 32, 16),
    1200: WaveformRow(Fraction(1, 2), 2400, 2, 1, 20, 20),
    600: WaveformRow(Fraction(1, 2), 1200, 1, 1, 20, 20),
    300: WaveformRow(Fraction(1, 4), 1200, 1, 1, 20, 20),
    150: WaveformRow(Fraction(1, 8), 1200, 1, 1, 20, 20),
    75: WaveformRow(Fraction(1, 2), 150, 2, 32, None, 0),
}

# ---------------------------------------------------------------------------
# Performance (§5.3.2.5, Table XVI)
# ---------------------------------------------------------------------------


class PerformanceRow(NamedTuple):
    rate: int
    paths: int
    fading: bool
    multipath_ms: float | None
    fading_bw_hz: float | None
    snr_db: float  # signal and noise both measured in a 3-kHz bandwidth
    max_ber: float


#: Table XVI: Serial (single-tone) mode minimum performance (long interleave, §5.3.2.5).
TABLE_XVI = (
    PerformanceRow(4800, 1, False, None, None, 17, 1.0e-3),
    PerformanceRow(4800, 2, True, 2, 0.5, 27, 1.0e-3),
    PerformanceRow(2400, 1, False, None, None, 10, 1.0e-5),
    PerformanceRow(2400, 2, True, 2, 1, 18, 1.0e-5),
    PerformanceRow(2400, 2, True, 2, 5, 30, 1.0e-3),
    PerformanceRow(2400, 2, True, 5, 1, 30, 1.0e-5),
    PerformanceRow(1200, 2, True, 2, 1, 11, 1.0e-5),
    PerformanceRow(600, 2, True, 2, 1, 7, 1.0e-5),
    PerformanceRow(300, 2, True, 5, 5, 7, 1.0e-5),
    PerformanceRow(150, 2, True, 5, 5, 5, 1.0e-5),
    PerformanceRow(75, 2, True, 5, 5, 2, 1.0e-5),
)
