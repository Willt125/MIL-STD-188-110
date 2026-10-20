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


def test_the_cap_is_short_until_the_rate_settles():
    """With settle_cap_s, transmissions are capped shorter until the peer has asked for the
    same rate twice running."""
    design = arq.replace(arq.DESIGNS["sr-short"], cap_s=40.0, settle_cap_s=10.0)
    link = arq.Link(design, snr_db=25.0, seed=1)
    link.a.out.write(bytes(20_000))
    link.transmit(link.a)  # not settled yet: at most 10 s
    assert link.log[-1]["seconds"] <= 10.0
    link.a.settled = True
    link.transmit(link.a)
    assert 30.0 < link.log[-1]["seconds"] <= 40.0


def test_a_failed_step_up_returns_to_the_rate_that_worked_and_holds():
    """Back-off: a step up that lost frames sends the rate back to where it was and keeps it
    below the failed rate for Design.backoff transmissions, doubling at each such failure;
    an ordinary loss at a steady rate does not trigger it."""
    link = arq.Link(arq.replace(arq.DESIGNS["sr-grow"], backoff=3), snr_db=20.0, seed=1)
    s = link.a
    s.asked, s.last_rate, s.last_delivered, s.last_snr = arq.mode(1200, "short"), 1200, 1.0, 20.0
    assert link.proposal(s).rate == 2400 and s.probing == 2400  # a step up: a probe
    s.asked, s.last_rate, s.last_delivered, s.last_snr = arq.mode(2400, "short"), 2400, 0.6, 5.0
    assert link.proposal(s).rate == 1200  # failed: back to 1200, not as low as 5 dB says
    assert (s.ceiling, s.hold, s.backoff) == (2400, 2, 3)
    s.asked, s.last_rate, s.last_delivered, s.last_snr = arq.mode(1200, "short"), 1200, 1.0, 20.0
    assert [link.proposal(s).rate for _ in range(2)] == [1200, 1200]  # held
    assert link.proposal(s).rate == 2400  # the hold is over: try again
    s.asked, s.last_rate, s.last_delivered = arq.mode(2400, "short"), 2400, 0.5
    link.proposal(s)
    assert s.backoff == 6  # failed again: twice as long
    # A loss at a rate already settled is not a failed probe.
    t = link.b
    t.asked, t.last_rate, t.last_delivered, t.last_snr = arq.mode(1200, "short"), 1200, 0.8, 9.0
    assert link.proposal(t).rate == 1200 and t.hold == 0


def scripted(link, outcomes):
    """Replace the radio: each transmission's outcome (did the peer understand it?) comes
    from ``outcomes`` per station; delivery itself is perfect when understood."""
    calls = []

    def fake(s, poll=False):
        ok = outcomes[s.name].pop(0) if outcomes[s.name] else True
        calls.append((s.name, poll, ok))
        p = link.peer(s)
        if ok and not poll:
            for off, b in s.out.pieces(10_000, 200):
                p.inc.on_data(off, b)
        if ok:
            p.out.on_ack(arq.Ack(s.inc.next, s.inc.ack_ranges(), 10.0, 1200, "short"))
        link.seconds += 1.0
        return ok

    link.transmit = fake
    return calls


@pytest.mark.parametrize("retry", ["resend", "poll"])
def test_master_turns_time_out_on_silence_and_poll_after_an_unreadable_reply(retry):
    design = arq.replace(arq.DESIGNS["rt-poll"], retry=retry)
    link = arq.Link(design, snr_db=20.0, seed=1)
    # A's first transmission is not heard (B stays silent: a timeout); the second is heard
    # but B's reply is unreadable; then everything works.
    calls = scripted(link, {"A": [False, True], "B": [False]})
    r = link.run([("A", b"x" * 300)])
    assert r["ok"] and r["timeouts"] == 1 and r["unreadable"] == 1
    assert calls[0] == ("A", False, False)  # silence: B did not transmit
    assert calls[1][0] == "A" and calls[2] == ("B", False, False)  # heard; reply lost
    assert calls[3] == ("A", retry == "poll", True)  # then poll (or resend)
    assert r["polls"] == (1 if retry == "poll" else 0)


def test_master_turns_give_up():
    link = arq.Link(arq.DESIGNS["rt-poll"], snr_db=20.0, seed=1)
    scripted(link, {"A": [False] * 20, "B": []})
    r = link.run([("A", b"x" * 300)])
    assert not r["ok"] and r["gave_up"] and r["timeouts"] == arq.MAX_RETRIES


def test_a_second_ack_copy_follows_the_data(monkeypatch):
    sent = []
    real = arq.bytes_to_bits
    monkeypatch.setattr(arq, "bytes_to_bits", lambda b: sent.append(b) or real(b))
    link = arq.Link(arq.DESIGNS["rt-ack2"], snr_db=25.0, seed=1)
    link.a.out.write(bytes(600))
    link.transmit(link.a)
    kinds = [k for k, _ in arq.parse(sent[-1], link.sid)]
    assert kinds[:2] == ["HEAD", "ACK"] and kinds[-1] == "ACK" and kinds.count("DATA") == 3
    link.b.out.write(b"")
    link.transmit(link.b)  # nothing to send but the acknowledgement: one copy
    assert [k for k, _ in arq.parse(sent[-1], link.sid)] == ["HEAD", "ACK"]
