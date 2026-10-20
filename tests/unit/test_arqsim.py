"""Tests for tools/arqsim.py, the selective-repeat ARQ prototype (docs/NETWORK.md, D3): the
protocol logic without the radio, and one short session through it."""

import importlib.util
import pathlib

import pytest

PATH = pathlib.Path(__file__).parents[2] / "tools" / "arqsim.py"
spec = importlib.util.spec_from_file_location("arqsim", PATH)
arq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arq)


def test_frames_round_trip_and_damage_costs_only_the_damaged_frame():
    sid = 0x5A
    frames = [arq.frame("DATA", sid, bytes([i]) * 50) for i in range(3)]
    stream = bytearray(b"noise" + b"".join(frames) + b"tail")
    assert [p for _, p in arq.parse(bytes(stream), sid)] == [bytes([i]) * 50 for i in range(3)]
    stream[5 + len(frames[0]) + 20] ^= 0xFF  # damage the second frame's payload
    got = arq.parse(bytes(stream), sid)
    assert [p[0] for _, p in got] == [0, 2]
    assert arq.parse(bytes(stream), sid + 1) == []  # another session's frames are ignored


def test_ack_round_trip():
    ack = arq.Ack(1200, [[1400, 1600], [2000, 2200]], -3.4, 600, "long")
    assert arq.Ack.unpack(ack.pack()) == ack
    unheard = arq.Ack(0, [], None, 75, "short")
    assert arq.Ack.unpack(unheard.pack()) == unheard


def test_ranges_merge():
    r = []
    for s, e in [(10, 20), (30, 40), (20, 30), (50, 60), (0, 5)]:
        r = arq.add_range(r, s, e)
    assert r == [[0, 5], [10, 40], [50, 60]]


def test_selective_repeat_resends_only_what_was_lost():
    out, inc = arq.Outgoing(), arq.Incoming()
    out.write(bytes(range(256)) * 4)  # 1024 bytes
    pieces = out.pieces(budget=10_000, size=200)
    assert [off for off, _ in pieces] == [0, 200, 400, 600, 800, 1000]
    for off, b in pieces:
        if off not in (200, 600):  # two frames lost
            inc.on_data(off, b)
    out.on_ack(arq.Ack(inc.next, inc.ack_ranges(), 10.0, 1200, "short"))
    assert out.missing() == [[200, 400], [600, 800]]
    again = out.pieces(budget=10_000, size=200)
    assert [off for off, _ in again] == [200, 600]
    for off, b in again:
        inc.on_data(off, b)
    out.on_ack(arq.Ack(inc.next, inc.ack_ranges(), 10.0, 1200, "short"))
    assert out.done and inc.read() == bytes(range(256)) * 4


def test_the_budget_limits_a_transmission():
    out = arq.Outgoing()
    out.write(bytes(1000))
    pieces = out.pieces(budget=3 * (arq.OVERHEAD + 4 + 200) + 10, size=200)
    assert len(pieces) == 3


def test_capacity_respects_the_cap():
    m = arq.mode(1200, "short")
    n = arq.capacity(m, 10.0)
    assert arq.seconds_for(n, m) <= 10.0 < arq.seconds_for(n + 200, m)


def test_mode_choice():
    assert arq.choose(None, "long") == arq.mode(75, "long")
    assert arq.choose(30.0, "long") == arq.mode(2400, "long")
    # 11 dB measured = 14.5 dB channel: 1200 long (11 + 2 margin), short needs 3 dB more
    assert arq.choose(11.0, "long") == arq.mode(1200, "long")
    assert arq.choose(11.0, "short") == arq.mode(600, "short")


@pytest.mark.parametrize("design", ["sr-short", "stopwait"])
def test_a_short_session_completes(design):
    link = arq.Link(arq.DESIGNS[design], snr_db=20.0, seed=3)
    turns = [("A", bytes(range(200)) * 2), ("B", b"ok")]
    r = link.run(turns)
    assert r["ok"] and r["bytes"] == 402
    assert link.b.inc.read() == bytes(range(200)) * 2 and link.a.inc.read() == b"ok"


def test_arq_rate_choice():
    """From measured goodput: up one rung at a time and only after a clean transmission;
    down at least one when half the frames were lost; 75-bps SNR readings are not used."""
    up_one = arq.choose_arq(20.0, 600, 1.0, 600)
    assert up_one == 1200  # the SNR says 2400, but one rung at a time
    assert arq.choose_arq(20.0, 600, 0.8, 600) == 600  # losses: no step up
    assert arq.choose_arq(-1.0, 1200, 1.0, 1200) == 150  # down as far as the SNR says
    assert arq.choose_arq(12.0, 1200, 0.4, 1200) == 600  # heavy losses: down a rung
    assert arq.choose_arq(4.0, 75, 1.0, 75) == 150  # 75-bps SNR ignored; clean: up a rung
    assert arq.choose_arq(4.0, 75, 0.5, 75) == 75
