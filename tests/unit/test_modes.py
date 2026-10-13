"""Tests for the derived per-mode parameters (M1).

Each derived quantity is compared with a number the spec states independently in prose,
so that the tables, our derivations and the prose must all agree.
"""

import pytest

from m110c.spec import ModeSpec, all_modes, mode, mode_from_d1_d2
from m110c.spec import tables as T

MODES = all_modes()
INTERLEAVED = [m for m in MODES if m.has_interleaver]
FRAMED = [m for m in MODES if not m.is_75bps]


def ids(modes):
    return [m.name for m in modes]


def test_there_are_19_modes_with_unique_names():
    assert len(MODES) == 19
    assert len({m.name for m in MODES}) == 19


def test_mode_names():
    assert mode(2400, "long").name == "2400L"
    assert mode(75, "zero").name == "75Z"
    assert mode(4800).name == "4800"


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_channel_bit_rate_matches_table_xv(m):
    assert m.channel_bit_rate == T.TABLE_XV[m.rate].channel_rate


@pytest.mark.parametrize("m", INTERLEAVED, ids=ids(INTERLEAVED))
def test_interleaver_holds_exactly_the_stated_duration_of_channel_bits(m):
    """§5.3.2.3.4: "block storage of 0.0, 0.6, or 4.8 s of receiving bits"."""
    assert m.interleaver_bits == m.channel_bit_rate * T.INTERLEAVE_SECONDS[m.interleave]


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_block_symbols_match_stated_1440_and_11520(m):
    """§5.3.2.3.7.1.2: "1440 tribit channel symbols for short interleave setting and
    11520 ... for the long". The derivation goes through Tables VI, VII and XV."""
    assert m.block_symbols == T.KNOWN_DATA_BLOCK_SYMBOLS[m.sync_setting]
    assert m.block_symbols == T.SYMBOL_RATE * T.INTERLEAVE_SECONDS[m.sync_setting]


@pytest.mark.parametrize("m", INTERLEAVED, ids=ids(INTERLEAVED))
def test_interleaver_block_divides_into_whole_channel_symbols(m):
    assert m.interleaver_bits % m.bits_per_channel_symbol == 0


@pytest.mark.parametrize("m", FRAMED, ids=ids(FRAMED))
def test_block_is_a_whole_number_of_frames(m):
    assert m.block_symbols % m.frame_symbols == 0
    if m.has_interleaver:
        assert m.interleaver_bits % m.channel_bits_per_frame == 0
        assert m.interleaver_bits // m.channel_bits_per_frame == m.frames_per_block


@pytest.mark.parametrize("m", FRAMED, ids=ids(FRAMED))
def test_frame_layout_matches_section_5_3_2_2_2(m):
    """§5.3.2.2.2 b: 32/16 at 2400 and 4800 bps; 20/20 at 150..1200 bps."""
    expected = (32, 16) if m.rate in (2400, 4800) else (20, 20)
    assert (m.unknown_per_frame, m.known_per_frame) == expected


@pytest.mark.parametrize("m", FRAMED, ids=ids(FRAMED))
def test_d_patterns_fit_in_a_probe(m):
    assert m.known_per_frame >= T.D_PATTERN_TRIBITS
    assert m.frames_per_block >= 2  # D1 and D2 need two probes per block (INT-005)


@pytest.mark.parametrize("m", [m for m in MODES if m.is_75bps], ids=lambda m: m.name)
def test_int_010_exceptional_set_is_last_set_of_each_block(m):
    sets_per_block = m.block_symbols // T.TRIBITS_PER_75BPS_SET
    assert m.exceptional_set_period == sets_per_block
    if m.has_interleaver:
        assert m.interleaver_bits // m.bits_per_channel_symbol == sets_per_block


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_preamble_duration(m):
    """§5.3.2.2.1: 0.6 s for zero and short interleave, 4.8 s for long."""
    seconds = {"zero": 0.6, "short": 0.6, "long": 4.8}[m.interleave]
    assert m.preamble_symbols == round(seconds * T.SYMBOL_RATE)


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_preamble_count_runs_down_to_zero(m):
    assert m.preamble_count_start == m.preamble_segments - 1


@pytest.mark.parametrize("m", MODES, ids=ids(MODES))
def test_d1_d2_round_trip(m):
    received = mode_from_d1_d2(*m.d1_d2)
    if m.interleave == "zero" and m.rate != 4800:
        # INT-012: zero interleave is indistinguishable from short at the receiver.
        assert received == mode(m.rate, "short")
    else:
        assert received == m


def test_int_019_unimplemented_designators_are_rejected():
    assert mode_from_d1_d2(*T.TABLE_XI_DIGITAL_VOICE) is None  # unless voice is asked for
    for pair in T.TABLE_XI_APPLICATION_SPECIFIC:
        assert mode_from_d1_d2(*pair) is None


def test_coding_parameters():
    assert not mode(4800).coded
    assert mode(2400).coded and mode(2400).repetitions == 1
    assert mode(300).repetitions == 2
    assert mode(150).repetitions == 4


def test_75bps_interleaver_uses_step_7():
    """INT-007."""
    m = mode(75, "long")
    assert (m.interleaver_load_row_step, m.interleaver_fetch_col_decrement) == (7, 7)
    m = mode(2400, "long")
    assert (m.interleaver_load_row_step, m.interleaver_fetch_col_decrement) == (9, 17)


@pytest.mark.parametrize(
    "rate, interleave",
    [(4800, "short"), (4800, "long"), (9600, "short"), (2400, "medium"), (50, "zero")],
)
def test_invalid_modes_raise(rate, interleave):
    with pytest.raises(ValueError):
        ModeSpec(rate, interleave)


def test_int_022_mode_factory_normalises_4800():
    """INT-022: 4800 bps is represented as zero interleave."""
    assert mode(4800, "long") == ModeSpec(4800, "zero")


# --- INT-019: 2400 bps digital voice -------------------------------------------------


VOICE = mode(2400, "short", voice=True)


def test_int_019_digital_voice_is_2400s_with_the_voice_designators():
    """Measured on a real recording (VALIDATION.md): the voice data phase is the 2400S
    chain, with D1/D2 = 7,7 in the preamble and in the D probes."""
    data = mode(2400, "short")
    assert VOICE.name == "2400V" and str(VOICE) == "2400V"
    assert VOICE.d1_d2 == T.TABLE_XI_DIGITAL_VOICE == (7, 7)
    for attribute in (
        "coded",
        "repetitions",
        "channel_bit_rate",
        "bits_per_channel_symbol",
        "unknown_per_frame",
        "known_per_frame",
        "interleaver_bits",
        "block_symbols",
        "frames_per_block",
        "preamble_segments",
        "sync_setting",
    ):
        assert getattr(VOICE, attribute) == getattr(data, attribute), attribute
    assert VOICE != data


def test_int_019_digital_voice_is_received_only_when_asked_for():
    assert mode_from_d1_d2(7, 7, voice=True) == VOICE
    assert mode_from_d1_d2(6, 4, voice=True) == mode(2400, "short")  # data is unaffected


def test_int_019_digital_voice_is_not_a_data_mode():
    assert VOICE not in MODES


@pytest.mark.parametrize(
    "rate, interleave", [(1200, "short"), (2400, "long"), (2400, "zero"), (4800, "zero")]
)
def test_int_019_digital_voice_exists_only_at_2400_short(rate, interleave):
    """Table XI: voice has a short-interleave pair only; the long column is "reserved".
    The recording's code check rejects zero interleave (VALIDATION.md)."""
    with pytest.raises(ValueError, match="INT-019"):
        mode(rate, interleave, voice=True)
