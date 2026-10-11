"""Operating modes of the fixed-frequency serial-tone waveform, with derived parameters.

A *mode* is a (user bit rate, interleave setting) pair. Everything else the transmitter
and receiver need to know about a mode, such as frame layout, block sizes and preamble
length, is computed here from the tables in :mod:`m110c.spec.tables`, never typed in a
second time. The unit tests then check these derived values against the numbers the spec
states in prose (e.g. "1440 tribit channel symbols", "0.6 s"), so a transcription error
in one table shows up as a disagreement with another part of the spec.

There are 19 modes: 75..2400 bps with zero, short or long interleave (18), plus 4800 bps.
4800 bps has no interleaver; we represent it as ``interleave="zero"`` because it shares
the short-interleave preamble and D1/D2 column, exactly as zero interleave does (INT-022).
"""

from dataclasses import dataclass
from fractions import Fraction

from . import tables as T

INTERLEAVE_SETTINGS = ("zero", "short", "long")


@dataclass(frozen=True)
class ModeSpec:
    """One fixed-frequency serial-tone mode. Construct with :func:`mode`."""

    rate: int  # user bit rate, bps
    interleave: str  # "zero", "short" or "long"

    def __post_init__(self):
        if self.rate not in T.DATA_RATES:
            raise ValueError(f"unsupported rate {self.rate} bps; choose from {T.DATA_RATES}")
        if self.interleave not in INTERLEAVE_SETTINGS:
            raise ValueError(f"interleave must be one of {INTERLEAVE_SETTINGS}")
        if self.rate == 4800 and self.interleave != "zero":
            # §5.3.2.3.4 note: "The interleaver shall be bypassed for 4800-bps".
            # Table XI: long-interleave 4800 is "reserved".
            raise ValueError("4800 bps has no interleaver; use interleave='zero' (INT-022)")

    # -- naming ------------------------------------------------------------

    @property
    def name(self) -> str:
        """Short name such as ``2400L``, ``600S``, ``75Z`` or ``4800``."""
        if self.rate == 4800:
            return "4800"
        return f"{self.rate}{self.interleave[0].upper()}"

    def __str__(self) -> str:
        return self.name

    # -- sync type -----------------------------------------------------------

    @property
    def sync_setting(self) -> str:
        """The interleave column used for preamble length, D1/D2 and probe-block period.

        Zero interleave and 4800 bps both use the short-interleave values
        (§5.3.2.3.7.1.2 note; Table XI).
        """
        return "long" if self.interleave == "long" else "short"

    # -- FEC (Table V) -------------------------------------------------------

    @property
    def coded(self) -> bool:
        """False only at 4800 bps, where the FEC encoder is bypassed (§5.3.2.3.3 c)."""
        return T.TABLE_V[self.rate].effective_code_rate is not None

    @property
    def repetitions(self) -> int:
        """How many times each (T1, T2) output pair is sent (1, 2 or 4)."""
        return T.TABLE_V[self.rate].repetitions

    @property
    def effective_code_rate(self) -> Fraction:
        """User bits per channel bit. 1 for the uncoded 4800-bps mode."""
        r = T.TABLE_V[self.rate].effective_code_rate
        return Fraction(1) if r is None else r

    @property
    def channel_bit_rate(self) -> int:
        """Channel (coded, repeated) bits per second, derived from Table V."""
        return int(self.rate / self.effective_code_rate)

    # -- frame layout (Table XV, Table VII) ----------------------------------

    @property
    def is_75bps(self) -> bool:
        """75 bps uses 32-tribit orthogonal sets and no probes (§5.3.2.3.7.1.1)."""
        return self.rate == 75

    @property
    def bits_per_channel_symbol(self) -> int:
        """Bits fetched from the interleaver per channel symbol (Table VII; XV for 4800)."""
        if self.rate == 4800:
            return T.TABLE_XV[4800].bits_per_channel_symbol
        return T.TABLE_VII[self.rate]

    @property
    def tribits_per_channel_symbol(self) -> int:
        """Transmitted 8-PSK symbols per channel symbol: 32 at 75 bps, else 1 (Table XV)."""
        return T.TABLE_XV[self.rate].tribits_per_channel_symbol

    @property
    def unknown_per_frame(self) -> int | None:
        """Data symbols per frame, or None at 75 bps where there are no frames."""
        return T.TABLE_XV[self.rate].unknown_per_frame

    @property
    def known_per_frame(self) -> int:
        """Probe symbols per frame (0 at 75 bps)."""
        return T.TABLE_XV[self.rate].known_per_frame

    @property
    def frame_symbols(self) -> int | None:
        """Transmitted symbols per data+probe frame, or None at 75 bps."""
        if self.is_75bps:
            return None
        return self.unknown_per_frame + self.known_per_frame

    @property
    def channel_bits_per_frame(self) -> int | None:
        """Channel bits carried by one frame, or None at 75 bps."""
        if self.is_75bps:
            return None
        return self.unknown_per_frame * self.bits_per_channel_symbol

    # -- interleaver (Table VI) ----------------------------------------------

    @property
    def has_interleaver(self) -> bool:
        return self.interleave != "zero"

    @property
    def interleaver_rows(self) -> int | None:
        return T.TABLE_VI[self.rate][self.interleave].rows if self.has_interleaver else None

    @property
    def interleaver_cols(self) -> int | None:
        return T.TABLE_VI[self.rate][self.interleave].cols if self.has_interleaver else None

    @property
    def interleaver_bits(self) -> int | None:
        """Channel bits per interleaver block (rows x cols), or None if bypassed."""
        if not self.has_interleaver:
            return None
        return self.interleaver_rows * self.interleaver_cols

    @property
    def interleaver_load_row_step(self) -> int:
        """Row increment between successive loaded bits (§5.3.2.3.4)."""
        return T.INTERLEAVE_LOAD_ROW_STEP_75 if self.is_75bps else T.INTERLEAVE_LOAD_ROW_STEP

    @property
    def interleaver_fetch_col_decrement(self) -> int:
        """Column decrement between successive fetched bits (§5.3.2.3.5)."""
        if self.is_75bps:
            return T.INTERLEAVE_FETCH_COL_DECREMENT_75
        return T.INTERLEAVE_FETCH_COL_DECREMENT

    # -- block structure ------------------------------------------------------

    @property
    def block_symbols(self) -> int:
        """Transmitted symbols per block: the D1/D2 probe period, and (when interleaving)
        the duration of one interleaver block. Derived from Table XV and Table VI;
        compare with the spec's stated 1440 / 11520 (§5.3.2.3.7.1.2) in the tests."""
        if self.has_interleaver:
            channel_symbols = self.interleaver_bits // self.bits_per_channel_symbol
            if self.is_75bps:
                return channel_symbols * self.tribits_per_channel_symbol
            frames = channel_symbols // self.unknown_per_frame
            return frames * self.frame_symbols
        # Zero interleave and 4800: the 0.6 s pattern is used (§5.3.2.3.7.1.2 note).
        return int(T.SYMBOL_RATE * T.INTERLEAVE_SECONDS["short"])

    @property
    def frames_per_block(self) -> int | None:
        if self.is_75bps:
            return None
        return self.block_symbols // self.frame_symbols

    @property
    def exceptional_set_period(self) -> int | None:
        """At 75 bps, every Nth 32-tribit set uses Table X b (INT-010)."""
        if not self.is_75bps:
            return None
        return T.EXCEPTIONAL_SET_PERIOD[self.sync_setting]

    # -- preamble (§5.3.2.3.7.2) ---------------------------------------------

    @property
    def d1_d2(self) -> tuple[int, int]:
        """Designation symbols D1, D2 sent in the preamble and probes (Table XI)."""
        return T.TABLE_XI[(self.rate, self.sync_setting)]

    @property
    def preamble_segments(self) -> int:
        return T.PREAMBLE_SEGMENTS[self.interleave]

    @property
    def preamble_count_start(self) -> int:
        return T.PREAMBLE_COUNT_START[self.interleave]

    @property
    def preamble_symbols(self) -> int:
        """Transmitted symbols in the whole sync preamble."""
        per_channel_symbol = len(T.TABLE_XIII[0])
        return self.preamble_segments * T.PREAMBLE_CHANNEL_SYMBOLS_PER_SEGMENT * per_channel_symbol


def mode(rate: int, interleave: str = "short") -> ModeSpec:
    """Return the :class:`ModeSpec` for a rate and interleave setting.

    4800 bps ignores ``interleave`` (it is always bypassed, INT-022).
    """
    if rate == 4800:
        interleave = "zero"
    return ModeSpec(rate, interleave)


def all_modes() -> tuple[ModeSpec, ...]:
    """All 19 fixed-frequency data modes, highest rate first."""
    modes = [ModeSpec(4800, "zero")]
    for rate in T.DATA_RATES[1:]:
        for interleave in INTERLEAVE_SETTINGS:
            modes.append(ModeSpec(rate, interleave))
    return tuple(modes)


def mode_from_d1_d2(d1: int, d2: int) -> ModeSpec | None:
    """Mode signalled by a received D1, D2 pair, or None if not implemented (INT-019).

    Zero and short interleave share D1/D2 (INT-012); this returns the short setting.
    """
    for (rate, setting), pair in T.TABLE_XI.items():
        if pair == (d1, d2):
            return mode(rate, setting)
    return None
