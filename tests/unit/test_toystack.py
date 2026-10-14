"""Tests for tools/toystack.py, the S8 link-layer experiment.

The tool's numbers are only as good as its frame parser and its fountain code, so those are
tested directly; one short pattern is run end to end through the modem.
"""

import importlib.util
import pathlib

import numpy as np
import pytest

from m110c import io

PATH = pathlib.Path(__file__).parents[2] / "tools" / "toystack.py"
spec = importlib.util.spec_from_file_location("toystack", PATH)
toy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toy)


def test_frames_round_trip_through_the_parser():
    a = toy.Station(toy.A)
    frames = [a.frame("CHAT", toy.B, b"hello", toy.OVER), a.frame("BYE", toy.QST)]
    got, bad = toy.parse(b"\x00junk" + b"".join(frames) + b"tail")
    assert bad == 0
    assert got == [
        ("CHAT", toy.OVER, toy.A, toy.B, 1, b"hello"),
        ("BYE", 0, toy.A, toy.QST, 2, b""),
    ]
    assert len(frames[1]) == toy.OVERHEAD


def test_a_damaged_frame_costs_only_itself():
    a = toy.Station(toy.A)
    frames = [a.frame("CHAT", toy.B, bytes([i]) * 30) for i in range(3)]
    damaged = bytearray(frames[1])
    damaged[20] ^= 0x40
    got, bad = toy.parse(frames[0] + bytes(damaged) + frames[2])
    assert [f[4] for f in got] == [1, 3] and bad >= 1


def test_a_sync_word_inside_a_payload_is_not_a_frame():
    a = toy.Station(toy.A)
    frame = a.frame("CHAT", toy.B, toy.SYNC * 5)
    got, _ = toy.parse(frame + frame)
    assert len(got) == 2 and got[0][5] == toy.SYNC * 5


def test_fountain_decodes_from_any_sufficient_subset():
    rng = np.random.default_rng(0)
    k = 12
    source = [rng.integers(0, 256, 16, dtype=np.uint8) for _ in range(k)]
    seeds = rng.permutation(60)[:30]  # a mix of systematic and coded blocks, out of order
    blocks = {int(s): toy.fountain_block(source, int(s)) for s in seeds}
    decoded = toy.fountain_decode(k, blocks)
    assert decoded is not None
    assert all(np.array_equal(d, s) for d, s in zip(decoded, source, strict=True))


def test_fountain_reports_too_few_blocks():
    source = [np.full(4, i, np.uint8) for i in range(5)]
    blocks = {s: toy.fountain_block(source, s) for s in range(4)}  # 4 of 5 source blocks
    assert toy.fountain_decode(5, blocks) is None


@pytest.mark.parametrize("k", [1, 2, 10])
def test_fountain_masks_are_reproducible_and_never_empty(k):
    """With k = 1 or 2 a random mask would be empty a quarter of the time or more."""
    for s in range(k, k + 200):
        m = toy.fountain_mask(k, s)
        assert m.any() and np.array_equal(m, toy.fountain_mask(k, s))


def test_a_pattern_end_to_end_with_recordings(tmp_path):
    """The beacon through the channel and the receiver, and its two recordings."""
    air, result = toy.beacon(snr_db=20.0, quiet=True)
    assert result["frames_received"] == result["frames_sent"] == 1
    assert result["useful_bytes"] > 0
    notes = air.save_wav(str(tmp_path), "beacon")
    lead = int(toy.LEAD_S * toy.FS)
    for kind in ("clean", "air"):
        rate, x = io.read_wav((tmp_path / f"beacon_{kind}.wav").read_bytes())
        assert rate == toy.FS and x.size > lead
        silent = not np.any(x[:lead])
        assert silent == (kind == "clean")  # the air recording has noise before the signal
    assert not any("clipped" in n for n in notes)


def test_bulk_rebuilds_the_file_through_losses():
    """2400L well below its Table XVI SNR loses frames; the fountain code still delivers."""
    _, result = toy.bulk(rate=2400, snr_db=10.0, size=2_000, quiet=True)
    assert result["frames_received"] < result["frames_sent"]  # losses happened
    assert result["useful_bytes"] == 2_000  # and the file arrived intact
