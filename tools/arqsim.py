"""Prototype of the link layer of docs/NETWORK.md (D3): selective-repeat ARQ sessions over 110.

An experiment, not part of the modem. Two stations hold a session over one continuous
simulated HF channel (Appendix E, m110c.channel), every transmission going through the
modulator, the channel and the complete receiver. The application is a B2F-shaped exchange
(Winlink's message protocol, emulated by its pattern of turns and sizes, not implemented):
handshake, proposals, accepts, compressed messages each way, closing lines. The link layer
moves each turn's bytes reliably; the next turn starts when they have arrived.

Usage:
    python tools/arqsim.py session                       # one session, every transmission
    python tools/arqsim.py session --design stopwait --snr 12
    python tools/arqsim.py compare                       # designs x SNRs x seeds (minutes)
    python tools/arqsim.py compare --designs sr-short sr-long --snrs 9 15 --seeds 4
    python tools/arqsim.py goodput                       # frames delivered per mode and SNR

THE FRAME FORMAT IS A DRAFT FOR EXPERIMENTS (NETWORK.md D3; nothing is decided):

    frame   sync 1A CF | kind 1 | session 1 | length 2 | payload | CRC-32 4   (10 + payload)
    HEAD    from 7 | to 7 | flags 1              (first in every transmission: identifies it)
    ACK     next 4 | n 1 | n x (start 4, end 4) | snr 2 (0.1 dB) | rate 2 | interleave 1
    DATA    offset 4 | bytes                     (a piece of the sender's byte stream)

A transmission is one 110 message: HEAD, then ACK, then DATA frames up to a length cap. The
receiver hunts for the sync word, so a damaged frame costs only itself. Each direction is a
byte stream. Every transmission acknowledges the last one heard from the peer: everything
below ``next``, and up to 8 ranges beyond it (selective acknowledgement), and reports the
SNR measured on it and the mode the peer should send in. The stations take turns strictly
(half duplex); a transmission not heard at all still costs its air time, and the other
station then goes again. Unacknowledged data is sent again, from the lowest offset, in the
next transmission (with one transmission in flight there is no ambiguity about what an
acknowledgement covers).

Assumptions to keep in mind: turnaround and gap times are guesses, not measurements; one
run is one sample of the fading channel; the B2F sizes are plausible, not measured.
"""

import argparse
import os
import struct
import sys
import time
import zlib
from dataclasses import dataclass, field

import numpy as np

from m110c.bits import bytes_to_bits
from m110c.channel.watterson import ChannelSpec, TimeVaryingChannel
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit

SYNC = b"\x1a\xcf"
KINDS = {0x01: "HEAD", 0x02: "ACK", 0x03: "DATA"}
CODE = {name: code for code, name in KINDS.items()}
FRAME = struct.Struct(">BBH")  # kind, session, length
OVERHEAD = len(SYNC) + FRAME.size + 4  # 10 bytes
HEAD = struct.Struct(">7s7sB")
ACK_FIXED = struct.Struct(">IB")
ACK_TAIL = struct.Struct(">hHc")
MAX_SACK = 8
FS = 9600

TURNAROUND_S = 0.5  # assumed radio switching time per change of direction
GAP_S = 0.3  # between transmissions of the same station (not used: stations alternate)
CONTROL = mode(600, "short")  # transmissions that carry little or no data

# Mode choice from the SNR the receiver measures (NETWORK.md §3; tools/toystack.py): the
# estimate reads about 3.5 dB below the channel SNR. Required channel SNRs per rate, long
# interleave (Table XVI); short interleave needs more on fading channels (SHORT_PENALTY_DB,
# a guess to be measured here).
CAL_OFFSET_DB = 3.5
MARGIN_DB = 2.0
SHORT_PENALTY_DB = 3.0
LADDER = ((2400, 18.0), (1200, 11.0), (600, 7.0), (300, 7.0), (150, 5.0), (75, 2.0))
# For ARQ the aim is the most data delivered per second, not a BER of 1e-5: frames lost are
# sent again. Measured with `goodput` (2 paths 2 ms 1 Hz, short interleave, 200-byte frames,
# 20-s transmissions; NETWORK.md): the best rate from these channel SNRs up. 300 bps never won.
ARQ_SHORT = ((2400, 14.0), (1200, 8.0), (600, 4.5), (150, 2.0), (75, -99.0))


def callsign(sign: str, ssid: int = 0) -> bytes:
    return sign.ljust(6).encode()[:6] + bytes([ssid])


def session_id(a: bytes, b: bytes) -> int:
    return zlib.crc32(b"".join(sorted((a, b)))) & 0xFF


# --- frames ------------------------------------------------------------------------------


def frame(kind: str, session: int, payload: bytes) -> bytes:
    body = FRAME.pack(CODE[kind], session, len(payload)) + payload
    return SYNC + body + struct.pack(">I", zlib.crc32(body))


def parse(stream: bytes, session: int):
    """The frames of ``session`` in a received byte stream, as (kind, payload)."""
    frames, i = [], 0
    while True:
        i = stream.find(SYNC, i)
        if i < 0 or i + OVERHEAD > len(stream):
            return frames
        kind, sid, n = FRAME.unpack_from(stream, i + len(SYNC))
        end = i + len(SYNC) + FRAME.size + n
        body = stream[i + len(SYNC) : end]
        if end + 4 <= len(stream) and struct.pack(">I", zlib.crc32(body)) == stream[end : end + 4]:
            if sid == session and kind in KINDS:
                frames.append((KINDS[kind], body[FRAME.size :]))
            i = end + 4
        else:
            i += 1


@dataclass
class Ack:
    next: int  # every byte below this has arrived
    ranges: list  # received ranges beyond it, [start, end)
    snr_db: float | None  # measured on the transmission acknowledged
    rate: int  # the mode the peer should send data in
    interleave: str

    def pack(self) -> bytes:
        ranges = self.ranges[:MAX_SACK]
        out = ACK_FIXED.pack(self.next, len(ranges))
        out += b"".join(struct.pack(">II", s, e) for s, e in ranges)
        snr = -32768 if self.snr_db is None else int(round(10 * self.snr_db))
        return out + ACK_TAIL.pack(snr, self.rate, self.interleave[0].upper().encode())

    @classmethod
    def unpack(cls, payload: bytes) -> "Ack":
        nxt, n = ACK_FIXED.unpack_from(payload)
        at = ACK_FIXED.size
        ranges = [list(struct.unpack_from(">II", payload, at + 8 * i)) for i in range(n)]
        snr, rate, il = ACK_TAIL.unpack_from(payload, at + 8 * n)
        interleave = {"S": "short", "L": "long", "Z": "zero"}[il.decode()]
        return cls(nxt, ranges, None if snr == -32768 else snr / 10, rate, interleave)


# --- byte streams ------------------------------------------------------------------------


def add_range(ranges: list, start: int, end: int) -> list:
    """``ranges`` (sorted, disjoint [start, end) pairs) with [start, end) merged in."""
    out = []
    for s, e in sorted([*ranges, [start, end]]):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


class Outgoing:
    """One direction's sending side: the stream, and what the peer has acknowledged."""

    def __init__(self):
        self.data = bytearray()
        self.acked = []  # ranges the peer has acknowledged

    def write(self, b: bytes) -> None:
        self.data += b

    def on_ack(self, ack: Ack) -> None:
        if ack.next:
            self.acked = add_range(self.acked, 0, ack.next)
        for s, e in ack.ranges:
            self.acked = add_range(self.acked, s, e)

    @property
    def done(self) -> bool:
        return not self.missing()

    def missing(self) -> list:
        """The unacknowledged ranges, lowest first."""
        gaps, at = [], 0
        for s, e in self.acked:
            if s > at:
                gaps.append([at, s])
            at = max(at, e)
        if at < len(self.data):
            gaps.append([at, len(self.data)])
        return gaps

    def pieces(self, budget: int, size: int) -> list:
        """DATA frames for up to ``budget`` bytes of frames, each carrying at most ``size``
        bytes, from the lowest unacknowledged offset."""
        out = []
        for s, e in self.missing():
            while s < e:
                n = min(size, e - s)
                if budget < OVERHEAD + 4 + n:
                    return out
                out.append((s, bytes(self.data[s : s + n])))
                budget -= OVERHEAD + 4 + n
                s += n
        return out


class Incoming:
    """One direction's receiving side: what has arrived, and what can be read in order."""

    def __init__(self):
        self.buffer = {}  # offset -> bytes
        self.ranges = []
        self.read_to = 0

    def on_data(self, offset: int, b: bytes) -> None:
        self.buffer[offset] = b
        self.ranges = add_range(self.ranges, offset, offset + len(b))

    @property
    def next(self) -> int:
        return self.ranges[0][1] if self.ranges and self.ranges[0][0] == 0 else 0

    def ack_ranges(self) -> list:
        return [r for r in self.ranges if r[0] > 0][:MAX_SACK]

    def read(self) -> bytes:
        """Everything new that has arrived in order."""
        out = bytearray()
        for off in sorted(self.buffer):
            b = self.buffer[off]
            if off <= self.read_to < off + len(b):
                out += b[self.read_to - off :]
                self.read_to = off + len(b)
        return bytes(out)


# --- modes and transmission sizes ----------------------------------------------------------


def seconds_for(n_bytes: int, m) -> float:
    """Air time of a 110 transmission carrying ``n_bytes`` (preamble to EOM and flush)."""
    return transmit(bytes_to_bits(bytes(n_bytes)), m).size / 2400


def capacity(m, cap_s: float) -> int:
    """The most bytes a transmission in mode ``m`` carries within ``cap_s`` seconds (at least
    what fits in its first interleaver block)."""
    lo, hi = 0, max(64, int(m.rate * cap_s / 8) + 64)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if seconds_for(mid, m) <= cap_s:
            lo = mid
        else:
            hi = mid - 1
    return max(lo, int(m.rate * (4.8 if m.interleave == "long" else 0.6) / 8) - 16)


def choose(measured_db: float | None, interleave: str):
    """The fastest rate the measured SNR supports with a margin (the slowest if none)."""
    if measured_db is None:
        return mode(LADDER[-1][0], interleave)
    estimate = measured_db + CAL_OFFSET_DB
    penalty = SHORT_PENALTY_DB if interleave != "long" else 0.0
    for rate, needed in LADDER:
        if estimate >= needed + penalty + MARGIN_DB:
            return mode(rate, interleave)
    return mode(LADDER[-1][0], interleave)


def choose_arq(measured_db: float, current_rate: int, delivered: float | None, measured_rate):
    """The rate the peer should send its data in next (short interleave), from the SNR
    measured on its last transmission and the share of its DATA frames that arrived.

    Rates go up at most one rung at a time, and only after a transmission whose frames all
    arrived; down as far as the SNR says, and at least one rung when half or more were lost.
    The SNR measured on a 75-bps transmission is not used: without probes it saturates near
    4-5 dB however good the channel (measured with `goodput`)."""
    rates = [rate for rate, _ in ARQ_SHORT]
    here = rates.index(current_rate) if current_rate in rates else rates.index(600)
    if measured_rate == 75:
        target = here - 1 if delivered == 1.0 else here
    else:
        estimate = measured_db + CAL_OFFSET_DB
        target = next(i for i, (_, needed) in enumerate(ARQ_SHORT) if estimate >= needed)
    if target < here and delivered is not None and delivered < 1.0:
        target = here  # no step up after losses
    target = max(target, here - 1)  # one rung up at a time
    if delivered is not None and delivered <= 0.5:
        target = max(target, here + 1)  # half or more lost: down at least one rung
    return rates[min(max(target, 0), len(rates) - 1)]


# --- designs -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Design:
    name: str
    frame_bytes: int = 200  # data bytes per DATA frame
    cap_s: float = 20.0  # longest transmission
    max_frames: int | None = None  # DATA frames per transmission (None: as many as fit)
    interleave: str = "short"
    rate: int | None = 1200  # fixed data rate, or None: adapt from the peer's reports
    description: str = ""


DESIGNS = {
    d.name: d
    for d in (
        Design(
            "stopwait",
            frame_bytes=128,
            max_frames=1,
            description="ARDOP-like: one 128-byte frame per transmission, 1200S",
        ),
        Design("sr-short", description="selective repeat, 200-byte frames, 20-s cap, 1200S"),
        Design("sr-long", interleave="long", description="the same, 1200L"),
        Design(
            "sr-adapt",
            rate=None,
            description="selective repeat, short interleave, rate from the peer's reports",
        ),
        Design(
            "sr-adapt-long",
            rate=None,
            interleave="long",
            description="selective repeat, long interleave, rate from the peer's reports",
        ),
    )
}


# --- the application: a B2F-shaped exchange -----------------------------------------------


def b2f_turns(seed: int = 0) -> list:
    """(station, bytes) in order: who speaks and how much, in the shape of a Winlink B2F
    session (sizes plausible, contents random): A is the client, B the server."""
    rng = np.random.default_rng(seed)

    def blob(n):
        return rng.integers(0, 256, n, dtype=np.uint8).tobytes()

    return [
        ("A", blob(60)),  # SID and FC proposals for two messages
        ("B", blob(40)),  # SID, FS accept both, its own proposal for one
        ("A", blob(1500) + blob(2400)),  # two compressed messages
        ("B", blob(30)),  # FS answer to A's... and its own message follows
        ("B", blob(1800)),  # B's message (same direction: one turn, two writes)
        ("A", blob(20)),  # FF: nothing more
        ("B", blob(10)),  # FQ: quit
    ]


# --- the radio ---------------------------------------------------------------------------


@dataclass
class Station:
    name: str
    call: bytes
    out: Outgoing = field(default_factory=Outgoing)
    inc: Incoming = field(default_factory=Incoming)
    data_mode: object = None  # what the peer last asked for
    last_snr: float | None = None  # measured on the peer's last transmission heard
    last_rate: int | None = None  # the rate of that transmission
    last_delivered: float | None = None  # share of its DATA frames that arrived
    asked: object = None  # the data mode this station last asked its peer for


class Link:
    """Two stations on one continuous channel, taking turns."""

    def __init__(self, design: Design, snr_db: float, seed: int, channel=(2, 2.0, 1.0)):
        self.d = design
        self.channel = TimeVaryingChannel(ChannelSpec(*channel), snr_db=snr_db, seed=seed)
        self.a = Station("A", callsign("N0CALL", 1))
        self.b = Station("B", callsign("N1CALL", 2))
        self.sid = session_id(self.a.call, self.b.call)
        start = mode(design.rate, design.interleave) if design.rate else CONTROL
        self.a.data_mode = self.b.data_mode = start
        self.seconds = 0.0
        self.log = []

    def peer(self, s: Station) -> Station:
        return self.b if s is self.a else self.a

    def proposal(self, s: Station):
        if self.d.rate:
            return mode(self.d.rate, self.d.interleave)
        if s.last_snr is None and s.last_delivered is None:
            return CONTROL  # nothing heard yet: no grounds for any other choice
        if self.d.interleave == "long":
            return choose(s.last_snr, "long")
        current = (s.asked or CONTROL).rate
        if s.last_snr is None:  # the last transmission was not heard at all
            rates = [rate for rate, _ in ARQ_SHORT]
            i = rates.index(current) if current in rates else rates.index(600)
            return mode(rates[min(i + 1, len(rates) - 1)], "short")
        rate = choose_arq(s.last_snr, current, s.last_delivered, s.last_rate)
        return mode(rate, "short")

    def transmit(self, s: Station) -> bool:
        """``s`` sends HEAD, ACK and what DATA fits; the peer receives what it can.
        Returns whether the peer heard the transmission at all."""
        p = self.peer(s)
        prop = self.proposal(s)
        s.asked = prop
        ack = Ack(s.inc.next, s.inc.ack_ranges(), s.last_snr, prop.rate, prop.interleave)
        control = [
            frame("HEAD", self.sid, HEAD.pack(s.call, p.call, 0)),
            frame("ACK", self.sid, ack.pack()),
        ]
        m = s.data_mode
        room = capacity(m, self.d.cap_s) - sum(len(f) for f in control)
        pieces = s.out.pieces(room, self.d.frame_bytes)
        if self.d.max_frames is not None:
            pieces = pieces[: self.d.max_frames]
        if not pieces:
            m = CONTROL  # nothing to send but the acknowledgement: robust and short
        frames = control + [
            frame("DATA", self.sid, struct.pack(">I", off) + b) for off, b in pieces
        ]
        data = b"".join(frames)
        x = modulate(transmit(bytes_to_bits(data), m))
        gap = np.zeros(int(TURNAROUND_S * FS), complex)
        y = self.channel.process(np.concatenate([gap, x, np.zeros(FS // 3, complex)]))
        self.seconds += TURNAROUND_S + x.size / FS
        rx = Receiver(interleave="short" if m.interleave != "long" else "auto")
        got = parse(rx.process(y) + rx.finish(), self.sid)
        heard = bool(rx.messages)
        p.last_snr = rx.messages[0].snr_db if heard else None
        p.last_rate = m.rate
        p.last_delivered = None  # unless DATA was sent (below)
        n_data = 0
        for kind, payload in got:
            if kind == "ACK":
                a = Ack.unpack(payload)
                p.out.on_ack(a)
                p.data_mode = mode(a.rate, a.interleave)
            elif kind == "DATA":
                p.inc.on_data(struct.unpack(">I", payload[:4])[0], payload[4:])
                n_data += 1
        if pieces:
            p.last_delivered = n_data / len(pieces)
        self.log.append(
            {
                "from": s.name,
                "mode": str(m),
                "seconds": x.size / FS,
                "frames": len(pieces),
                "received": n_data,
                "heard": heard,
                "snr": p.last_snr,
            }
        )
        return heard

    def run(self, turns: list, max_transmissions: int = 100) -> dict:
        """Play the application's turns; returns the session's totals."""
        queue = list(turns)
        expected = {"A": 0, "B": 0}  # bytes each station's peer must receive
        speaker = self.a
        transmissions = 0
        while transmissions < max_transmissions:
            # The application writes the next turn when everything before it has arrived.
            while queue:
                who, b = queue[0]
                s = self.a if who == "A" else self.b
                if self.b.inc.next < expected["A"] or self.a.inc.next < expected["B"]:
                    break
                s.out.write(b)
                expected[who] += len(b)
                queue.pop(0)
            delivered = self.b.inc.next >= expected["A"] and self.a.inc.next >= expected["B"]
            if not queue and delivered and self.a.out.done and self.b.out.done:
                break
            self.transmit(speaker)
            transmissions += 1
            speaker = self.peer(speaker)
        ok = not queue and self.b.inc.next >= expected["A"] and self.a.inc.next >= expected["B"]
        useful = expected["A"] + expected["B"]
        return {
            "ok": ok,
            "seconds": self.seconds,
            "transmissions": transmissions,
            "bytes": useful,
            "bps": 8 * useful / self.seconds if ok else 0.0,
            "lost": sum(1 for e in self.log if not e["heard"]),
            "modes": sorted({e["mode"] for e in self.log}),
        }


# --- experiments -------------------------------------------------------------------------


def session_report(design: Design, snr: float, seed: int) -> None:
    link = Link(design, snr, seed)
    r = link.run(b2f_turns(seed))
    print(f"{design.name}: {design.description}; channel 2 paths 2 ms 1 Hz, {snr:g} dB")
    t = 0.0
    for e in link.log:
        t += TURNAROUND_S + e["seconds"]
        snr_txt = "unheard" if not e["heard"] else f"measured {e['snr']:.1f} dB"
        print(
            f"  t={t:6.1f}  {e['from']}  {e['mode']:5s} {e['seconds']:5.1f} s  "
            f"data {e['received']}/{e['frames']}  ({snr_txt})"
        )
    print(
        f"{'complete' if r['ok'] else 'FAILED'}: {r['bytes']} bytes in {r['seconds']:.1f} s, "
        f"{r['transmissions']} transmissions ({r['lost']} unheard), {r['bps']:.0f} bps"
    )


def goodput_job(args):
    """One 20-s transmission of 200-byte DATA frames in mode ``m`` at ``snr``: how many
    frames arrived, and what the receiver measured."""
    rate, interleave, snr, seed = args
    m = mode(rate, interleave)
    rng = np.random.default_rng(seed)
    sid = 0x42
    n = max(1, (capacity(m, 20.0) - 64) // (OVERHEAD + 204))
    payloads = [rng.integers(0, 256, 200, np.uint8).tobytes() for _ in range(n)]
    frames = [frame("DATA", sid, struct.pack(">I", 200 * i) + p) for i, p in enumerate(payloads)]
    x = modulate(transmit(bytes_to_bits(b"".join(frames)), m))
    channel = TimeVaryingChannel(ChannelSpec(2, 2.0, 1.0), snr_db=snr, seed=seed + 11)
    y = channel.process(np.concatenate([np.zeros(FS // 2, complex), x, np.zeros(FS // 3)]))
    rx = Receiver(interleave=interleave)
    got = parse(rx.process(y) + rx.finish(), sid)
    snr_measured = rx.messages[0].snr_db if rx.messages else None
    return args, len(got), n, x.size / FS, snr_measured


def goodput_report(results) -> None:
    rows = {}
    for (rate, interleave, snr, _), got, n, seconds, measured in results:
        rows.setdefault((snr, rate, interleave), []).append((got, n, seconds, measured))
    print("SNR  mode  | frames delivered | data bps delivered | measured SNR (median)")
    for (snr, rate, interleave), v in sorted(rows.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        got = sum(g for g, _, _, _ in v)
        n = sum(k for _, k, _, _ in v)
        seconds = sum(t for _, _, t, _ in v)
        measured = [x for *_, x in v if x is not None]
        med = f"{np.median(measured):5.1f}" if measured else "    -"
        name = str(mode(rate, interleave))
        bps = 8 * 200 * got / seconds
        print(f"{snr:3.0f}  {name:5s} | {got:3d}/{n:3d} {got / n:5.0%} | {bps:6.0f} | {med}")


def compare_job(args):
    name, snr, seed = args
    link = Link(DESIGNS[name], snr, seed)
    return args, link.run(b2f_turns(seed))


def compare_report(results) -> None:
    rows = {}
    for (name, snr, _), r in results:
        rows.setdefault((name, snr), []).append(r)
    print("design         SNR | done | session (s), median [range] | transmissions | bps")
    for (name, snr), v in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        ok = [r for r in v if r["ok"]]
        secs = sorted(r["seconds"] for r in ok)
        span = f"{np.median(secs):6.1f} [{secs[0]:.0f}-{secs[-1]:.0f}]" if secs else "     -"
        tx = np.median([r["transmissions"] for r in v])
        bps = np.median([r["bps"] for r in v])
        print(
            f"{name:13s} {snr:4.0f} | {len(ok):2d}/{len(v)} | {span:20s} | {tx:5.0f} | {bps:5.0f}"
        )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("experiment", choices=("session", "compare", "goodput"))
    p.add_argument("--design", default="sr-adapt", choices=list(DESIGNS))
    p.add_argument("--designs", nargs="+", default=list(DESIGNS), choices=list(DESIGNS))
    p.add_argument("--snr", type=float, default=15.0)
    p.add_argument("--snrs", nargs="+", type=float, default=[9.0, 15.0, 21.0])
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--seeds", type=int, default=4)
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    args = p.parse_args(argv)
    started = time.time()
    from concurrent.futures import ProcessPoolExecutor

    if args.experiment == "session":
        session_report(DESIGNS[args.design], args.snr, args.seed)
    elif args.experiment == "goodput":
        jobs = [
            (rate, "short", snr, 100 * k + 1)
            for rate in (2400, 1200, 600, 300, 150, 75)
            for snr in (0, 3, 6, 9, 12, 15, 18, 21, 24)
            for k in range(args.seeds)
        ]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            goodput_report(list(pool.map(goodput_job, jobs, chunksize=1)))
    else:
        jobs = [(d, s, k + 1) for d in args.designs for s in args.snrs for k in range(args.seeds)]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            compare_report(list(pool.map(compare_job, jobs, chunksize=1)))
    print(f"({time.time() - started:.0f} s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
