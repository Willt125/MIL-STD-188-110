"""Toy link-layer traffic over m110c: what a station's transmissions would contain.

An experiment for S8 (docs/PLAN.md), not part of the modem. It builds the transmissions a
station would make for several kinds of traffic, sends each through the Appendix E channel
simulator and the complete receiver, and reports what arrived, the air time, and the
throughput. Each pattern can also be saved as two WAV recordings: as sent, and as a
third station would hear the whole session through the channel.

Usage:
    python tools/toystack.py                       # every pattern (a few minutes)
    python tools/toystack.py --quick               # smaller bulk file
    python tools/toystack.py --patterns chat email --out /tmp/toy
    python tools/toystack.py --patterns negotiated --file some.txt
    python tools/toystack.py --bulk-snr 14 --no-wav

THE FRAME FORMAT IS A DRAFT FOR EXPERIMENTS. S8 has decided nothing about it:

    sync    2   1A CF
    type    1   see TYPES
    flags   1   bit 0 OVER (your turn), bit 1 ACK wanted
    from    7   callsign, 6 ASCII characters space-padded, then an SSID byte
    to      7   the same; "QST" = everyone
    seq     2   per-sender sequence number
    length  2   payload bytes
    payload n
    crc     4   CRC-32 (IEEE, as zlib.crc32) over type..payload

A transmission is one 110 message holding any number of frames. The receiver hunts for
the sync word, so a damaged frame costs only itself. Multi-byte fields are big-endian.

Each session runs on one continuous channel (m110c.channel.watterson.TimeVaryingChannel):
the fading carries on through every transmission and silence, and the SNR and path powers
can follow schedules (m110c.channel.schedule: straight lines or random walks between
points). The negotiated pattern's drifting scenarios are stress tests: real conditions
drift over minutes to hours, not tens of seconds.

Assumptions to keep in mind when reading the numbers: the turnaround and gap times are
guesses, not measurements; the fountain code is a toy (random XOR combinations), where real
codes such as RaptorQ need fewer extra blocks; one run is one sample of the fading channel,
not a measurement.
"""

import argparse
import os
import struct
import sys
import time
import zlib

import numpy as np

from m110c import io
from m110c.bits import bytes_to_bits
from m110c.channel import schedule as sched
from m110c.channel.watterson import ChannelSpec, TimeVaryingChannel
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit

SYNC = b"\x1a\xcf"
TYPES = {
    0x01: "BEACON",
    0x02: "CHAT",
    0x03: "TELEMETRY",
    0x10: "HELLO",
    0x11: "PROPOSE",
    0x12: "REPLY",
    0x13: "MSGBLOCK",
    0x14: "MSGACK",
    0x15: "BYE",
    0x20: "MANIFEST",
    0x21: "BLOCK",
    0x22: "STATUS",
    0x23: "OFFER",
    0x24: "MODE",
    0x25: "POLL",
}
CODE = {name: code for code, name in TYPES.items()}
OVER, ACKREQ = 1, 2
HEADER = struct.Struct(">BB7s7sHH")  # type, flags, from, to, seq, length
OVERHEAD = len(SYNC) + HEADER.size + 4  # 26 bytes per frame

FS = 9600
CHANNEL = (2, 2.0, 1.0)  # paths, delay (ms), Doppler spread (Hz): Table XVI's commonest
TURNAROUND_S = 0.5  # assumed radio switching time per change of direction
GAP_S = 0.3  # assumed pause between two transmissions from the same station
LEAD_S = 0.5  # silence before the first transmission of a session
TAIL_S = 0.3  # silence after each transmission (transmitter hang time) before the next gap
WAV_GAIN = 0.5  # headroom: in a fade the two paths can add in phase
MAX_ROUNDS = 20  # give up on a transfer after this many repair rounds

# Mode negotiation. The receiver reports SNR measured on the probes; on this channel that
# reads 3-4 dB below the channel SNR in 3 kHz (measured: median offset 3.1 dB for 100-byte
# 600S messages, 3.5-4 dB for 1500-byte 1200L ones, at every SNR from 4 to 22 dB), and a
# single short message is good to about +/-1.5 dB (10-90%). So the estimate adds the offset,
# and a mode is chosen only with a margin to spare.
CAL_OFFSET_DB = 3.5
MARGIN_DB = 2.0
# Channel SNR each mode needs for a BER of 1e-5 with long interleave: Table XVI (2400-600 on
# 2 paths, 2 ms, 1 Hz; 300-75 on the harsher 5 ms, 5 Hz, so conservative here).
MODE_LADDER = ((2400, 18.0), (1200, 11.0), (600, 7.0), (300, 7.0), (150, 5.0), (75, 2.0))
# Blocks per data transmission in the negotiated transfer: short enough that feedback (and a
# new mode) comes back every few tens of seconds, as conditions change.
ROUND_BLOCKS = 6
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def callsign(sign: str, ssid: int = 0) -> bytes:
    return sign.ljust(6).encode()[:6] + bytes([ssid])


A, B, QST = callsign("N0CALL", 1), callsign("N1CALL", 2), callsign("QST")
NAMES = {A: "A", B: "B", QST: "QST"}


class Station:
    """Builds frames from one callsign, numbering them."""

    def __init__(self, me: bytes):
        self.me, self.seq = me, 0

    def frame(self, kind: str, to: bytes, payload: bytes = b"", flags: int = 0) -> bytes:
        self.seq = (self.seq + 1) & 0xFFFF
        body = HEADER.pack(CODE[kind], flags, self.me, to, self.seq, len(payload)) + payload
        return SYNC + body + struct.pack(">I", zlib.crc32(body))


def parse(stream: bytes):
    """The frames in a received byte stream, as (type, flags, from, to, seq, payload), and the
    number of sync words that did not start a valid frame."""
    frames, bad, i = [], 0, 0
    while True:
        i = stream.find(SYNC, i)
        if i < 0 or i + OVERHEAD > len(stream):
            return frames, bad
        kind, flags, frm, to, seq, n = HEADER.unpack_from(stream, i + len(SYNC))
        end = i + len(SYNC) + HEADER.size + n
        body = stream[i + len(SYNC) : end]
        if end + 4 <= len(stream) and struct.pack(">I", zlib.crc32(body)) == stream[end : end + 4]:
            frames.append((TYPES.get(kind, hex(kind)), flags, frm, to, seq, body[HEADER.size :]))
            i = end + 4
        else:
            bad += 1
            i += 1


# --- a toy fountain code: random XOR combinations, decoded by elimination over GF(2) -------


def fountain_mask(k: int, seed: int) -> np.ndarray:
    """Which of the k source blocks coded block ``seed`` combines. Seeds below k are the
    source blocks themselves (a systematic code)."""
    mask = np.zeros(k, bool)
    if seed < k:
        mask[seed] = True
        return mask
    mask = np.random.default_rng(seed).integers(0, 2, k).astype(bool)
    mask[np.random.default_rng(seed + 1).integers(k)] = True  # never empty
    return mask


def fountain_block(source: list, seed: int) -> np.ndarray:
    out = np.zeros_like(source[0])
    for j in np.flatnonzero(fountain_mask(len(source), seed)):
        out ^= source[j]
    return out


def fountain_decode(k: int, blocks: dict):
    """The k source blocks from {seed: coded block}, or None if they do not determine them
    yet."""
    rows = [(fountain_mask(k, s), p.copy()) for s, p in blocks.items()]
    for col in range(k):
        r = next((i for i in range(col, len(rows)) if rows[i][0][col]), None)
        if r is None:
            return None
        rows[col], rows[r] = rows[r], rows[col]
        pivot_mask, pivot = rows[col]
        for i in range(len(rows)):
            if i != col and rows[i][0][col]:
                rows[i] = (rows[i][0] ^ pivot_mask, rows[i][1] ^ pivot)
    return [rows[i][1] for i in range(k)]


# --- the radio: frames -> one 110 transmission -> channel -> receiver -> frames -----------


class Air:
    """One session on one channel: sends transmissions, keeps the books and the audio."""

    def __init__(self, snr_db, seed: int, quiet: bool = False, path_db=None):
        """One continuous channel carries the whole session, silences included, so the
        fading is continuous from one transmission to the next. ``snr_db`` and each entry of
        ``path_db`` are a number or a schedule (m110c.channel.schedule) of dB against
        seconds since the session began."""
        self.seed, self.quiet = seed, quiet
        self.snr = sched.as_schedule(snr_db)
        self.channel = TimeVaryingChannel(
            ChannelSpec(*CHANNEL), snr_db=snr_db, path_db=path_db, seed=seed
        )
        self.measured_snr_db = None  # what the receiving station measured, last transmission
        self.total_s = 0.0
        self.turns = 0
        self.last_sender = None
        self.log = []  # (label, frames sent, frames received, seconds, payload bytes)
        self.clean, self.heard = [], []

    def say(self, text: str) -> None:
        if not self.quiet:
            print(text)

    def send(self, sender: bytes, frames: list, m, label: str, show: int = 3) -> list:
        gap = LEAD_S
        if self.last_sender not in (None, sender):
            self.turns += 1
            self.total_s += TURNAROUND_S
            gap = TURNAROUND_S
        elif self.last_sender is not None:
            gap = GAP_S
        self.last_sender = sender
        data = b"".join(frames)
        x = modulate(transmit(bytes_to_bits(data), m))
        seconds = x.size / FS
        self.total_s += seconds
        # The silences go through the channel too: the fading runs on, and the recording
        # has noise between transmissions, as a listener would hear.
        start_s = self.channel.samples_done / FS + gap
        snr = float(self.snr.value(start_s + seconds / 2))  # mid-transmission, for the log
        sent = np.concatenate([np.zeros(int(gap * FS), complex), x, np.zeros(int(TAIL_S * FS))])
        y = self.channel.process(sent)
        self.clean.append(sent)
        self.heard.append(y)
        rx = Receiver()
        got, bad = parse(rx.process(y) + rx.finish())
        self.measured_snr_db = rx.messages[0].snr_db if rx.messages else None
        payload = sum(len(f) - OVERHEAD for f in frames)
        self.say(
            f"\n== {label}: {NAMES[sender]} sends {len(frames)} frame(s), {len(data)} bytes "
            f"({payload} payload) as {m} at {snr:.1f} dB, t = {start_s:.0f} s: "
            f"{seconds:.1f} s on air "
            f"({8 * payload / seconds:.0f} bps of payload)"
        )
        for f in frames[:show]:
            kind, flags, frm, to, seq, n = HEADER.unpack_from(f, len(SYNC))
            p = f[len(SYNC) + HEADER.size : -4]
            shown = p[:24].hex(" ") + (" ..." if len(p) > 24 else "")
            marks = ("O" if flags & OVER else "-") + ("A" if flags & ACKREQ else "-")
            self.say(
                f"   {TYPES[kind]:9s} {NAMES[frm]}->{NAMES[to]:3s} seq {seq:<4d} flags {marks} "
                f"len {n:<4d} | {shown}"
            )
        if len(frames) > show:
            self.say(f"   ... {len(frames) - show} more")
        lost = len(frames) - len(got)
        self.say(
            f"   received: {len(got)}/{len(frames)} frames"
            + (f", {lost} lost" if lost else "")
            + (f" ({bad} damaged sync hits)" if bad else "")
        )
        self.log.append((label, len(frames), len(got), seconds, payload))
        return got

    def save_wav(self, out_dir: str, name: str) -> list:
        os.makedirs(out_dir, exist_ok=True)
        notes = []
        for kind, parts in (("clean", self.clean), ("air", self.heard)):
            path = os.path.join(out_dir, f"{name}_{kind}.wav")
            with open(path, "wb") as f:
                w = io.SampleWriter(f, "wav", FS)
                w.write(WAV_GAIN * np.concatenate([*parts, np.zeros(FS // 2, complex)]))
                w.close()
            notes.append(path + (f" ({w.clipped} samples clipped)" if w.clipped else ""))
        self.say("   recordings: " + ", ".join(notes))
        return notes

    def summary(self, title: str, useful_bytes: int) -> dict:
        bps = 8 * useful_bytes / self.total_s
        self.say(
            f"\n-- {title}: {self.total_s:.1f} s including {self.turns} turnaround(s) of "
            f"{TURNAROUND_S} s (assumed); {useful_bytes} useful bytes -> {bps:.0f} bps end to end"
        )
        return {
            "title": title,
            "seconds": self.total_s,
            "turns": self.turns,
            "useful_bytes": useful_bytes,
            "bps": bps,
            "transmissions": len(self.log),
            "frames_sent": sum(e[1] for e in self.log),
            "frames_received": sum(e[2] for e in self.log),
        }


# --- the traffic patterns -------------------------------------------------------------------
# Each returns (Air, result dict). Modes are what the traffic would plausibly use.


def beacon(snr_db=10.0, seed=0, quiet=False):
    """Presence and identification: one short broadcast."""
    a, air = Station(A), Air(snr_db, seed, quiet)
    text = b"N0CALL-1 m110c 75-2400 S/L FM19 QRV 14.109"
    got = air.send(A, [a.frame("BEACON", QST, text)], mode(600, "short"), "beacon")
    return air, air.summary("beacon", len(text) if got else 0)


def chat(snr_db=14.0, seed=10, quiet=False):
    """Keyboard-to-keyboard: one short transmission per over."""
    a, b, air = Station(A), Station(B), Air(snr_db, seed, quiet)
    lines = [
        (A, b"N1CALL DE N0CALL GM, TESTING M110C 1200S. HW CPY?"),
        (B, b"N0CALL DE N1CALL FB, SOLID COPY HERE, SNR 13 DB. OVER"),
        (A, b"TNX. SENDING A FILE NEXT. 73"),
    ]
    useful = 0
    for who, text in lines:
        station, to = (a, B) if who == A else (b, A)
        got = air.send(who, [station.frame("CHAT", to, text, OVER)], mode(1200, "short"), "chat")
        useful += len(text) if got else 0
    return air, air.summary("keyboard chat", useful)


def telemetry(snr_db=14.0, seed=20, quiet=False):
    """Status data riding along with other traffic in the same transmission."""
    a, air = Station(A), Air(snr_db, seed, quiet)
    frames = [
        a.frame("CHAT", B, b"WX REPORT FOLLOWS", OVER),
        a.frame("TELEMETRY", QST, struct.pack(">hHHH", 215, 1013, 13_800, 47)),
    ]
    got = air.send(
        A, frames, mode(1200, "short"), "chat with telemetry riding along (temp, hPa, mV, %)"
    )
    useful = sum(len(f[5]) for f in got)
    return air, air.summary("piggybacked telemetry", useful)


def email(snr_db=13.0, seed=30, quiet=False):
    """Store-and-forward mail, in turns: hello, proposals, accept/reject, transfer, ack."""
    a, b, air = Station(A), Station(B), Air(snr_db, seed, quiet)
    words = (
        "net schedule moves to frequency report station antenna power check relay please "
        "confirm traffic weather battery tomorrow morning tonight copy received update "
        "supplies water fuel road open closed team arrives departs"
    ).split()
    text = " ".join(np.random.default_rng(3).choice(words, 260)).encode()  # compresses ~2:1
    outbox = {
        b"MID-7F3A": b"Subject: net schedule\r\n\r\n" + text,
        b"MID-91C2": b"Subject: status\r\n\r\nAll stations report green.\r\n" * 3,
    }
    already_have = {b"MID-91C2"}  # B has this one: it is never sent again
    neg, bulk_mode = mode(1200, "short"), mode(1200, "long")
    hello = b"m110c-mail/0 rates=75-2400 il=S,L snr="
    air.send(A, [a.frame("HELLO", B, hello + b"12", OVER)], neg, "email: hello")
    air.send(B, [b.frame("HELLO", A, hello + b"14", OVER)], neg, "email: hello back")
    packed = {mid: zlib.compress(body, 9) for mid, body in outbox.items()}
    props = b"".join(
        mid + struct.pack(">HH", len(outbox[mid]), len(z)) for mid, z in packed.items()
    )
    air.send(A, [a.frame("PROPOSE", B, props, OVER)], neg, "email: proposals (id, size, packed)")
    reply = b"".join(mid + (b"-" if mid in already_have else b"+") for mid in packed)
    air.send(B, [b.frame("REPLY", A, reply, OVER)], neg, "email: accept + / reject -")
    wanted = [mid for mid in packed if mid not in already_have]
    blob = b"".join(struct.pack(">8sH", mid, len(packed[mid])) + packed[mid] for mid in wanted)
    chunks = [blob[i : i + 200] for i in range(0, len(blob), 200)]

    def block(i, last):
        head = struct.pack(">HH", i, len(chunks))
        return a.frame("MSGBLOCK", B, head + chunks[i], ACKREQ | (OVER if last else 0))

    have, todo = set(), list(range(len(chunks)))
    for _ in range(MAX_ROUNDS):
        frames = [block(i, i == todo[-1]) for i in todo]
        got = air.send(A, frames, bulk_mode, f"email: transfer {len(todo)} block(s)")
        have |= {struct.unpack(">H", f[5][:2])[0] for f in got if f[0] == "MSGBLOCK"}
        todo = [i for i in range(len(chunks)) if i not in have]
        ack = struct.pack(f">H{len(todo)}H", len(todo), *todo)
        status = f"missing {todo}" if todo else "all received"
        air.send(B, [b.frame("MSGACK", A, ack, OVER)], neg, f"email: ack ({status})")
        if not todo:
            break
    air.send(A, [a.frame("BYE", B)], neg, "email: bye (B would now propose its own)")
    useful = sum(len(outbox[mid]) for mid in wanted) if not todo else 0
    return air, air.summary("email session", useful)


def bulk(rate=2400, snr_db=11.0, size=16_000, seed=40, quiet=False):
    """A file as a fountain-coded carousel: manifest, coded blocks, rare status replies.
    The default 2400L at 11 dB is 7 dB below its Table XVI point, so frames are lost."""
    a, b, air = Station(A), Station(B), Air(snr_db, seed, quiet)
    data = np.random.default_rng(7).integers(0, 256, size, dtype=np.uint8).tobytes()
    bs, file_id = 200, 0x0F11
    padded = data + bytes(-len(data) % bs)
    source = [np.frombuffer(padded[i : i + bs], np.uint8).copy() for i in range(0, len(padded), bs)]
    k = len(source)
    manifest = struct.pack(">HIHH", file_id, size, bs, k) + zlib.crc32(data).to_bytes(4, "big")
    m = mode(rate, "long")

    def blocks(seeds):
        return [
            a.frame(
                "BLOCK", QST, struct.pack(">HI", file_id, s) + fountain_block(source, s).tobytes()
            )
            for s in seeds
        ]

    def take(got):
        for f in got:
            if f[0] == "BLOCK":
                received[struct.unpack(">I", f[5][2:6])[0]] = np.frombuffer(f[5][6:], np.uint8)

    def status(label):
        payload = struct.pack(">HHB", file_id, len(received), decoded is not None)
        air.send(B, [b.frame("STATUS", A, payload, OVER)], mode(600, "short"), label)

    received = {}
    first = int(k * 1.1)  # the file plus 10%
    take(
        air.send(
            A,
            [a.frame("MANIFEST", QST, manifest), *blocks(range(first))],
            m,
            f"bulk: manifest + {first} coded blocks (k = {k})",
        )
    )
    decoded = fountain_decode(k, received)
    status(f"bulk: status (have {len(received)}, {k} needed)")
    seed_next = first
    for _ in range(MAX_ROUNDS):
        if decoded is not None:
            break
        extra = max(4, int((k - len(received)) * 1.5))
        take(air.send(A, blocks(range(seed_next, seed_next + extra)), m, f"bulk: {extra} more"))
        seed_next += extra
        decoded = fountain_decode(k, received)
        status(f"bulk: status ({len(received)} blocks, {'complete' if decoded else 'not yet'})")
    ok = decoded is not None and b"".join(x.tobytes() for x in decoded)[:size] == data
    air.say(
        f"   file rebuilt from {len(received)} blocks of {k}: {'identical' if ok else 'FAILED'}"
    )
    result = air.summary(f"bulk transfer (fountain-coded, {m} at {snr_db:g} dB)", size if ok else 0)
    result["blocks_used"] = len(received)
    return air, result


def choose_mode(measured_db):
    """The fastest long-interleave mode the measured SNR supports with the margin to spare
    (the slowest if none does), and the channel SNR estimated from the measurement."""
    estimate = measured_db + CAL_OFFSET_DB
    for rate, needed in MODE_LADDER:
        if estimate >= needed + MARGIN_DB:
            return mode(rate, "long"), estimate
    return mode(MODE_LADDER[-1][0], "long"), estimate


def next_mode(current, measured_db, delivered):
    """The receiver's proposal after a round: the SNR choice, tempered by what was actually
    delivered. One estimate is good to a dB or two, so after a clean round (90% or more of
    the blocks) the mode moves at most one rung either way; only real losses let it jump
    straight down to the SNR's choice. Steps up need a clean round."""
    rates = [rate for rate, _ in MODE_LADDER]  # fastest first
    if measured_db is None:  # heard nothing at all: the most robust mode
        return mode(rates[-1], "long"), None
    target, estimate = choose_mode(measured_db)
    here, there = rates.index(current.rate), rates.index(target.rate)
    if delivered >= 0.9:
        there = min(max(there, here - 1), here + 1)
    elif there < here:
        there = here  # losses: never step up
    return mode(rates[there], "long"), estimate


def negotiated(
    snr_db=13.0, data=None, seed=50, quiet=False, title="negotiated transfer", path_db=None
):
    """A file whose transfer mode is negotiated from measured SNR, then renegotiated after
    every round from the SNR measured on the data and from how much of it arrived. Losses
    are still repaired with the fountain code. ``data`` defaults to the repository's README.

    Each station acts only on what it received: the sender changes mode only when it hears
    the receiver's MODE frame, and polls (up to three times) when a status reply is lost."""
    if data is None:
        with open(os.path.join(REPO, "README.md"), "rb") as f:
            data = f.read()
    a, b, air = Station(A), Station(B), Air(snr_db, seed, quiet, path_db)
    control = mode(600, "short")
    packed = zlib.compress(data, 9)
    bs, file_id = 200, 0x0F12
    padded = packed + bytes(-len(packed) % bs)
    source = [np.frombuffer(padded[i : i + bs], np.uint8).copy() for i in range(0, len(padded), bs)]
    k = len(source)
    offer = struct.pack(">HIIHH", file_id, len(data), len(packed), bs, k)
    offer += zlib.crc32(data).to_bytes(4, "big") + b"README.md"

    def mode_frame(chosen, measured_db):
        payload = struct.pack(
            ">HHch",
            file_id,
            chosen.rate,
            chosen.interleave[0].upper().encode(),
            round(10 * measured_db),
        )
        return b.frame("MODE", A, payload, OVER)

    def heard_mode(frames):
        for f in frames:
            if f[0] == "MODE":
                return mode(struct.unpack(">H", f[5][2:4])[0], "long")
        return None

    # 1. The offer goes out robustly; B measures it and answers with a mode.
    m = None
    for offer_mode in (control, mode(150, "short"), mode(75, "short")):
        got = air.send(A, [a.frame("OFFER", B, offer, OVER)], offer_mode, f"offer as {offer_mode}")
        if not got or air.measured_snr_db is None:
            continue
        measured = air.measured_snr_db
        proposal, estimate = choose_mode(measured)
        note = f"measured {measured:.1f} dB -> channel ~{estimate:.1f} dB -> use {proposal}"
        m = heard_mode(air.send(B, [mode_frame(proposal, measured)], control, note))
        if m is not None:
            break
    if m is None:
        return air, {**air.summary(title, 0), "modes": []}
    modes = [str(m)]

    received, decoded, next_seed, done = {}, None, 0, False
    count = min(ROUND_BLOCKS, k)
    for _ in range(MAX_ROUNDS):
        frames = [
            a.frame(
                "BLOCK", B, struct.pack(">HI", file_id, s) + fountain_block(source, s).tobytes()
            )
            for s in range(next_seed, next_seed + count)
        ]
        next_seed += count
        got = air.send(A, frames, m, f"transfer: {count} blocks (k = {k})")
        data_snr = air.measured_snr_db
        blocks = [f for f in got if f[0] == "BLOCK"]
        for f in blocks:
            received[struct.unpack(">I", f[5][2:6])[0]] = np.frombuffer(f[5][6:], np.uint8)
        decoded = fountain_decode(k, received)
        status = b.frame("STATUS", A, struct.pack(">HHB", file_id, len(received), bool(decoded)))
        reply, note = [status], "complete" if decoded is not None else f"{len(received)} of {k}"
        if decoded is None:
            proposal, estimate = next_mode(m, data_snr, len(blocks) / count)
            reply.append(mode_frame(proposal, data_snr if data_snr is not None else -99.0))
            note += f", {len(blocks)}/{count} delivered"
            if estimate is not None:
                note += f", measured {data_snr:.1f} dB -> ~{estimate:.1f} dB"
            note += f" -> use {proposal}"
        heard = air.send(B, reply, control, f"status ({note})")
        for _ in range(3):  # A polls until it hears the status
            if heard:
                break
            air.send(A, [a.frame("POLL", B, struct.pack(">H", file_id), OVER)], control, "poll")
            heard = air.send(B, reply, control, f"status again ({note})")
        if not heard:  # still nothing: assume the path got worse
            rates = [rate for rate, _ in MODE_LADDER]
            m = mode(rates[min(rates.index(m.rate) + 1, len(rates) - 1)], "long")
        elif any(f[0] == "STATUS" and f[5][4] for f in heard):
            done = True
            break
        elif (new := heard_mode(heard)) is not None:
            m = new
        if str(m) != modes[-1]:
            modes.append(str(m))
        count = min(ROUND_BLOCKS, max(3, int((k - len(received)) * 1.3) + 1))
    ok = False
    if decoded is not None:
        rebuilt = zlib.decompress(b"".join(x.tobytes() for x in decoded)[: len(packed)])
        ok = rebuilt == data
    if done:
        air.send(A, [a.frame("BYE", B)], control, "bye")
    air.say(
        f"   {len(data)} bytes ({len(packed)} compressed) rebuilt from {len(received)} "
        f"blocks of {k}: {'identical' if ok else 'FAILED'}; modes used: {', '.join(modes)}"
    )
    result = air.summary(title, len(data) if ok else 0)
    result["modes"] = modes
    return air, result


PATTERNS = ("beacon", "chat", "telemetry", "email", "bulk", "negotiated")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--patterns", nargs="+", choices=PATTERNS, default=list(PATTERNS))
    p.add_argument("--out", default="toystack-out", help="folder for the WAV recordings")
    p.add_argument("--no-wav", action="store_true", help="do not write recordings")
    p.add_argument("--quick", action="store_true", help="a 6-kB bulk file instead of 16 kB")
    p.add_argument("--bulk-snr", type=float, default=11.0, help="SNR of the bulk runs (dB)")
    p.add_argument("--file", help="file for the negotiated transfer (default: README.md)")
    args = p.parse_args(argv)
    started = time.time()
    print(f"channel: {CHANNEL[0]} paths, {CHANNEL[1]:g} ms, {CHANNEL[2]:g} Hz fading")
    runs = []
    for name in args.patterns:
        if name == "bulk":
            size = 6_000 if args.quick else 16_000
            # The same file at 2400L and at 1200L, the rate Table XVI says 11 dB supports.
            for rate in (2400, 1200):
                runs.append((f"5_bulk_{rate}L", bulk(rate, args.bulk_snr, size)))
        elif name == "negotiated":
            data = None
            if args.file:
                with open(args.file, "rb") as f:
                    data = f.read()
            # Stress tests, not typical evenings: real drift takes minutes to hours.
            scenarios = [
                ("6_negotiated", 13.0, None, "negotiated, steady 13 dB"),
                (
                    "6_negotiated_fall",
                    sched.linear(17.0, 9.0, duration_s=30.0, start_s=15.0),
                    None,
                    "negotiated, 17 dB sliding to 9 over 30 s",
                ),
                (
                    "6_negotiated_walk",
                    sched.walk(15.0, 10.0, 150.0, volatility=1.0, seed=8, bounds=(3, 25)),
                    None,
                    "negotiated, random walk 15 -> 10 dB",
                ),
                (
                    "6_negotiated_path",
                    14.0,
                    (0.0, sched.linear(0.0, -20.0, duration_s=40.0, start_s=10.0)),
                    "negotiated, 14 dB, path 2 fading out",
                ),
            ]
            for label, snr, paths, title in scenarios:
                runs.append((label, negotiated(snr, data, seed=60, title=title, path_db=paths)))
        else:
            index = PATTERNS.index(name) + 1
            runs.append((f"{index}_{name}", globals()[name]()))
    if not args.no_wav:
        for label, (air, _) in runs:
            air.save_wav(args.out, label)
    print(f"\n{'run':18s} {'seconds':>8s} {'frames':>9s} {'bps':>6s}  pattern")
    for label, (_, r) in runs:
        frames = f"{r['frames_received']}/{r['frames_sent']}"
        modes = f" (modes: {', '.join(r['modes'])})" if r.get("modes") else ""
        print(f"{label:18s} {r['seconds']:8.1f} {frames:>9s} {r['bps']:6.0f}  {r['title']}{modes}")
    print(f"(simulated in {time.time() - started:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
