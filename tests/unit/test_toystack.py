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


def test_the_timeline_places_each_transmission_where_the_channel_carried_it():
    """The timeline the playground notebook plots: one entry per transmission, in order,
    never overlapping, separated by the assumed gaps, with the log's counts."""
    air, _ = toy.chat(snr_db=20.0, quiet=True)
    tl = air.timeline
    assert [e["sender"] for e in tl] == ["A", "B", "A"]
    assert tl[0]["start_s"] == toy.LEAD_S
    for before, after in zip(tl, tl[1:], strict=False):
        gap = after["start_s"] - (before["start_s"] + before["seconds"])
        assert abs(gap - (toy.TAIL_S + toy.TURNAROUND_S)) < 1e-3  # hang time, then turn round
    assert [(e["sent"], e["received"]) for e in tl] == [(e[1], e[2]) for e in air.log]
    assert all(e["mode"] == "1200S" and e["rate"] == 1200 for e in tl)
    assert all(e["snr_db"] == 20.0 and e["measured_db"] is not None for e in tl)


def test_bulk_rebuilds_the_file_through_losses():
    """2400L well below its Table XVI SNR loses frames; the fountain code still delivers."""
    _, result = toy.bulk(rate=2400, snr_db=10.0, size=2_000, quiet=True)
    assert result["frames_received"] < result["frames_sent"]  # losses happened
    assert result["useful_bytes"] == 2_000  # and the file arrived intact


# --- the negotiated transfer -------------------------------------------------------------


def measured_for(estimate_db):
    """The reported SNR that the tool turns into this channel estimate."""
    return estimate_db - toy.CAL_OFFSET_DB


def test_choose_mode_picks_the_fastest_mode_with_the_margin_to_spare():
    for _, needed in toy.MODE_LADDER:
        m, estimate = toy.choose_mode(measured_for(needed + toy.MARGIN_DB + 1e-9))
        assert m.interleave == "long" and estimate == pytest.approx(needed + toy.MARGIN_DB)
        # Exactly at a requirement: that mode or a faster one sharing it (300 and 600 do).
        assert dict(toy.MODE_LADDER)[m.rate] == needed
    m, _ = toy.choose_mode(measured_for(toy.MODE_LADDER[0][1] + toy.MARGIN_DB - 0.01))
    assert m.rate == toy.MODE_LADDER[1][0]  # just short of 2400L
    assert toy.choose_mode(-20.0)[0].rate == toy.MODE_LADDER[-1][0]  # nothing fits: slowest


def test_after_a_clean_round_the_mode_moves_one_rung_at_most():
    at_1200 = toy.mode(1200, "long")
    assert toy.next_mode(at_1200, 40.0, delivered=1.0)[0].rate == 2400  # up one, not more
    assert toy.next_mode(at_1200, -20.0, delivered=1.0)[0].rate == 600  # down one, not to 75
    assert toy.next_mode(at_1200, measured_for(14.0), delivered=0.95)[0].rate == 1200


def test_after_losses_the_mode_drops_to_the_snr_choice_and_never_rises():
    at_1200 = toy.mode(1200, "long")
    assert toy.next_mode(at_1200, -20.0, delivered=0.5)[0].rate == 75  # straight down
    assert toy.next_mode(at_1200, 40.0, delivered=0.5)[0].rate == 1200  # no step up
    assert toy.next_mode(at_1200, None, delivered=0.0)[0].rate == 75  # heard nothing


def ordered_file(lines=120):
    """Numbered lines of digits: easy to eyeball, and several blocks long once compressed."""
    rng = np.random.default_rng(1)
    return b"".join(
        b"line %03d: %s\n" % (i, b" ".join(b"%04d" % v for v in rng.integers(0, 10_000, 8)))
        for i in range(lines)
    )


def test_negotiated_transfer_follows_the_receiver_and_delivers_the_file(monkeypatch):
    """A strong channel negotiates 2400L; when the receiver then proposes another mode, the
    sender switches to it; and the file, several rounds long, arrives exactly."""
    monkeypatch.setattr(
        toy, "next_mode", lambda current, snr, delivered: (toy.mode(600, "long"), 0)
    )
    data = ordered_file()
    assert len(toy.zlib.compress(data, 9)) > toy.ROUND_BLOCKS * 200  # more than one round
    _, result = toy.negotiated(snr_db=26.0, data=data, quiet=True)
    assert result["modes"][:2] == ["2400L", "600L"]
    assert result["useful_bytes"] == len(data)  # rebuilt, decompressed and compared


def test_a_file_that_does_not_rebuild_exactly_is_not_counted(monkeypatch):
    real = toy.zlib.decompress
    monkeypatch.setattr(toy.zlib, "decompress", lambda b: real(b) + b"!")
    _, result = toy.negotiated(snr_db=26.0, data=ordered_file(5), quiet=True)  # one block
    assert result["useful_bytes"] == 0


def test_a_session_runs_on_one_continuous_scheduled_channel():
    """Every transmission and every silence goes through the same channel, whose time runs
    on, so a schedule spans the session and the fading is continuous across turns."""
    from m110c.channel import schedule as sched

    air = toy.Air(sched.linear(20.0, 5.0, duration_s=10.0), seed=0, quiet=True)
    channel = air.channel
    a = toy.Station(toy.A)
    for _ in range(2):
        air.send(toy.A, [a.frame("CHAT", toy.B, b"x" * 20)], toy.mode(1200, "short"), "chat")
    assert air.channel is channel
    assert channel.samples_done == sum(len(c) for c in air.clean) == sum(len(h) for h in air.heard)
    assert channel.samples_done / toy.FS > 2 * (toy.LEAD_S + toy.TAIL_S)
