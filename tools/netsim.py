"""Many stations on one frequency: the ARQ design of tools/arqsim.py in a shared net.

An experiment, not part of the modem (docs/NETWORK.md §7). arqsim puts two stations through
the real modem and channel; that is far too slow for twenty stations over hours, so this is
an event-level model instead, built on what arqsim measured:

- A transmission is a 110 message: preamble, then HEAD, ACK and DATA frames (arqsim's
  draft format), its air time from the real transmitter (arqsim.seconds_for).
- A frame arrives with the probability arqsim's ``goodput`` measured for its mode at the
  SNR (moderate channel, 200-byte frames; FRAME_SUCCESS), at the signal to interference
  plus noise ratio its receiver sees over it: every other transmission overlapping the
  frame at all counts as noise at its full power (pessimistic for short overlaps).
- A station transmitting hears nothing (half duplex). A receiver hears a transmission only
  if it catches its preamble; a receiver already following another station's signal when
  a preamble begins misses it (the receiver tracks one message at a time; "capture").
- Sessions follow arqsim's rt-ack2 design: the caller transmits, the called station replies
  to what it understood; the caller times out on silence, polls after an unreadable reply,
  gives up after MAX_RETRIES failures in a row. The rate is the best ARQ_SHORT rate for the
  link's SNR (an adaptation that has settled; arqsim measures the climb). A station holds
  one session at a time and ignores other sessions' callers until it is free.

Channel access (ACCESS) is the variable under test, including the busy detector that does
not exist yet: none at all (ALOHA), listen before talk with a detector of a given
sensitivity, a quiet period long enough to span a session's turns, and "virtual carrier
sense": a station that decodes another session's HEAD frame defers for a while.

Usage:
    python tools/netsim.py sweep                                   # the default grid
    python tools/netsim.py sweep --stations 2 5 10 20 --access aloha lbt lbt-nav
    python tools/netsim.py sweep --topology mesh --hidden 0 0.3 --load 1 2
    python tools/netsim.py single --snrs 9 15 21                  # one link, vs arqsim

Assumptions to keep in mind: interference is treated as noise (a 110 interferer at equal
power is worse than that for acquisition, better for some frames); SNRs are drawn at
random, not from propagation; HF fading is folded into the measured frame table, so a
session sees no long fades; the B2F exchange is arqsim's shape, every session the same.
"""

import argparse
import heapq
import os
import pathlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from functools import cache

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import arqsim  # noqa: E402  (tools/ is not a package)

from m110c.spec import mode  # noqa: E402

# Frames delivered (200-byte DATA frames, short interleave, 2 paths 2 ms 1 Hz) against the
# channel SNR, measured with `arqsim.py goodput` (NETWORK.md §7). Interpolated linearly in
# dB; none below the first column.
SNR_GRID = np.array([-3.0, 0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 18.0, 21.0])
FRAME_SUCCESS = {
    2400: [0.0, 0.0, 0.0, 0.01, 0.12, 0.50, 0.80, 0.95, 1.0],
    1200: [0.0, 0.0, 0.04, 0.37, 0.77, 0.98, 1.0, 1.0, 1.0],
    600: [0.0, 0.0, 0.29, 0.79, 1.0, 1.0, 1.0, 1.0, 1.0],
    150: [0.0, 0.25, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    75: [0.0, 0.25, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
}
TABLE_BYTES = 214  # the measured frames: 200 data bytes, offset and framing
PREAMBLE_S = 0.6  # short interleave
HEAD_BYTES = arqsim.OVERHEAD + arqsim.HEAD.size
ACK_BYTES = arqsim.OVERHEAD + arqsim.ACK_FIXED.size + arqsim.ACK_TAIL.size + 8  # one range
LOCK_DB = 0.0  # a receiver follows a signal this strong (it would detect its preamble)
IDLE_S = 60.0  # a called station gives a silent session up after this long
ATTEMPTS = 3  # a caller tries a session this many times before reporting failure
RETRY_S = (60.0, 300.0)  # waiting before trying again (uniform)
ROUTINE_SNR = (6.0, 24.0)  # the SNRs of links that carry sessions (uniform, dB)


@dataclass(frozen=True)
class Access:
    name: str
    detect_db: float | None = None  # the busy detector hears signals this strong (None: no
    clear_s: float = 0.0  # detector); the channel must have been quiet this long first
    nav_s: float = 0.0  # after decoding another session's HEAD, defer this long (0: off)
    backoff_s: float = 10.0  # busy: check again after a random wait up to this long
    description: str = ""


ACCESS = {
    a.name: a
    for a in (
        Access("aloha", description="no busy detector: transmit when there is something to send"),
        Access(
            "lbt",
            detect_db=-3.0,
            clear_s=4.0,
            description="listen before talk: quiet for 4 s (longer than a turnaround or "
            "timeout), detector sensitive to -3 dB",
        ),
        Access(
            "lbt-now",
            detect_db=-3.0,
            description="lbt checking only the moment before transmitting (no quiet period)",
        ),
        Access(
            "lbt-poor",
            detect_db=6.0,
            clear_s=4.0,
            description="the same with a poor detector (hears only signals above 6 dB)",
        ),
        Access(
            "lbt-nav",
            detect_db=-3.0,
            clear_s=4.0,
            nav_s=40.0,
            description="lbt, and a decoded HEAD of another session defers for 40 s",
        ),
        Access(
            "lbt-long",
            detect_db=-3.0,
            clear_s=35.0,
            description="lbt with a 35-s quiet period: longer than any transmission, so a "
            "session in progress is always heard from at least one end",
        ),
    )
}


@cache
def air_seconds(n_bytes: int, rate: int) -> float:
    return arqsim.seconds_for(n_bytes, mode(rate, "short"))


@cache
def capacity(rate: int, cap_s: float) -> int:
    return arqsim.capacity(mode(rate, "short"), cap_s)


def frame_success(rate: int, sinr_db: float, n_bytes: int = TABLE_BYTES) -> float:
    """Chance a frame of ``n_bytes`` arrives: the measured 200-byte figure, scaled as if
    errors were independent per byte (shorter frames fare a little better)."""
    p = float(np.interp(sinr_db, SNR_GRID, FRAME_SUCCESS[rate], left=0.0))
    return p ** (n_bytes / TABLE_BYTES)


def arq_rate(snr_db: float) -> int:
    return next(rate for rate, needed in arqsim.ARQ_SHORT if snr_db >= needed)


def db_sum(levels_db) -> float:
    return 10 * np.log10(sum(10 ** (x / 10) for x in levels_db)) if levels_db else -np.inf


# --- the net -----------------------------------------------------------------------------


@dataclass
class Node:
    i: int
    gateway: bool = False
    session: int | None = None  # the session it is in (called stations: bound on first
    last_heard: float = 0.0  # understanding the caller; freed when done or idle)
    nav_until: float = 0.0
    queue: list = field(default_factory=list)  # the sessions it will call, oldest first
    busy: bool = False  # running one of its own sessions


@dataclass
class Tx:
    src: int
    dst: int
    sid: int
    rate: int
    frames: list  # (kind, bytes, start s, end s)
    start: float
    end: float


def topology(kind: str, stations: int, gateways: int, hidden: float, rng):
    """SNR matrix (dB; symmetric; -inf: cannot hear each other) and who calls whom.

    hub: ``gateways`` stations everyone can reach (6-24 dB) and the rest clients calling
    their best gateway; two clients hear each other at 0-24 dB, or not at all with chance
    ``hidden``. mesh: any two stations at 6-24 dB, or not at all with chance ``hidden``;
    sessions between random pairs that can hear each other."""
    n = stations + (gateways if kind == "hub" else 0)
    snr = np.full((n, n), -np.inf)
    for i in range(n):
        for j in range(i + 1, n):
            both_clients = kind == "hub" and i >= gateways and j >= gateways
            if kind == "hub" and i < gateways <= j:
                s = rng.uniform(*ROUTINE_SNR)
            elif rng.random() < hidden:
                s = -np.inf
            elif both_clients or (kind == "hub" and j < gateways):
                s = rng.uniform(0.0, 24.0)
            else:
                s = rng.uniform(*ROUTINE_SNR)
            snr[i, j] = snr[j, i] = s
    if kind == "hub":
        callers = list(range(gateways, n))
        targets = {c: [int(np.argmax(snr[c, :gateways]))] for c in callers}
    else:
        callers = list(range(n))
        targets = {
            c: [j for j in range(n) if j != c and snr[c, j] >= ROUTINE_SNR[0]] for c in callers
        }
        callers = [c for c in callers if targets[c]]
    return snr, callers, targets


class Net:
    def __init__(self, snr, access: Access, seed: int, cap_s: float = 30.0, gateways=0):
        self.snr = snr
        self.access = access
        self.cap_s = cap_s
        self.rng = np.random.default_rng(seed)
        self.nodes = [Node(i, gateway=i < gateways) for i in range(len(snr))]
        self.t = 0.0
        self.events = []
        self.seq = 0
        self.txs = []
        self.sessions = []
        self.next_sid = 1
        self.capture = True

    # The event loop: processes are generators that yield ("wait", s), ("tx", Tx) or
    # ("access", node); a transmission resumes its process at its end with what each
    # receiver got.

    def at(self, t, fn):
        heapq.heappush(self.events, (t, self.seq, fn))
        self.seq += 1

    def start(self, gen):
        self._step(gen, None)

    def _step(self, gen, value):
        try:
            cmd = gen.send(value)
        except StopIteration:
            return
        if cmd[0] == "wait":
            self.at(self.t + cmd[1], lambda: self._step(gen, None))
        elif cmd[0] == "tx":
            tx = cmd[1]
            self.txs.append(tx)
            self.at(tx.end, lambda: self._step(gen, self._receive(tx)))
        elif cmd[0] == "access":
            self._access(gen, cmd[1])

    def run(self, until: float):
        while self.events and self.events[0][0] <= until:
            self.t, _, fn = heapq.heappop(self.events)
            fn()

    # --- the channel ---

    def _transmitting(self, r: int, a: float, b: float) -> bool:
        return any(t.src == r and t.start < b and t.end > a for t in self._near(a, b))

    def _near(self, a, b):
        # Transmissions overlapping [a, b] (the list is in start order; old ones skipped).
        out = []
        for t in reversed(self.txs):
            if t.start < b and t.end > a:
                out.append(t)
            if t.end < a - 2 * self.cap_s - 60:
                break
        return out

    def _sinr(self, tx: Tx, r: int, a: float, b: float) -> float:
        others = [
            self.snr[t.src, r]
            for t in self._near(a, b)
            if t is not tx and t.src not in (tx.src, r) and np.isfinite(self.snr[t.src, r])
        ]
        signal = self.snr[tx.src, r]
        return signal - db_sum([0.0, *others]) if others else signal

    def _locked_elsewhere(self, tx: Tx, r: int) -> bool:
        """Was ``r`` following another station's signal when ``tx``'s preamble began?"""
        if not self.capture:
            return False
        for t in self._near(tx.start, tx.start):
            if t is tx or t.src == r or t.start >= tx.start:
                continue
            if self.snr[t.src, r] >= LOCK_DB and not self._transmitting(r, t.start, t.start):
                return True
        return False

    def _receive(self, tx: Tx) -> dict:
        """What every station made of ``tx``: {station: [decoded? for each frame]} for those
        that caught its preamble."""
        got = {}
        for r in range(len(self.nodes)):
            if r == tx.src or not np.isfinite(self.snr[tx.src, r]):
                continue
            if r != tx.dst and not self.access.nav_s:
                continue  # nobody else needs to decode it
            pre = tx.start + PREAMBLE_S
            if self._transmitting(r, tx.start, pre) or self._locked_elsewhere(tx, r):
                continue
            if self.rng.random() >= frame_success(75, self._sinr(tx, r, tx.start, pre)):
                continue
            ok = [
                not self._transmitting(r, a, b)
                and self.rng.random() < frame_success(tx.rate, self._sinr(tx, r, a, b), n)
                for _, n, a, b in tx.frames
            ]
            got[r] = ok
            if r != tx.dst and ok[0] and self.access.nav_s:  # its HEAD
                node = self.nodes[r]
                node.nav_until = max(node.nav_until, tx.end + self.access.nav_s)
        return got

    def _busy(self, n: int) -> bool:
        a = self.access
        if self.nodes[n].nav_until > self.t:
            return True
        if a.detect_db is None:
            return False
        return any(
            t.src != n and self.snr[t.src, n] >= a.detect_db
            for t in self._near(self.t - a.clear_s, self.t)
        )

    def _access(self, gen, n: int):
        if self._busy(n):
            self.at(
                self.t + self.rng.uniform(1.0, self.access.backoff_s), lambda: self._access(gen, n)
            )
        else:
            self._step(gen, None)

    # --- stations ---

    def transmission(self, src, dst, sid, rate, data_frames, acks=1) -> Tx:
        """HEAD, ACK, DATA frames (sizes) and a second ACK after any data, in ``rate``."""
        frames = [("HEAD", HEAD_BYTES), ("ACK", ACK_BYTES)]
        frames += [("DATA", arqsim.OVERHEAD + 4 + b) for b in data_frames]
        if data_frames and acks > 1:
            frames.append(("ACK", ACK_BYTES))
        total = sum(n for _, n in frames)
        start = self.t
        end = start + air_seconds(total, rate)
        a, span = start + PREAMBLE_S, max(end - start - PREAMBLE_S - 0.2, 0.1)
        placed = []
        for kind, n in frames:
            b = a + span * n / total
            placed.append((kind, n, a, b))
            a = b
        return Tx(src, dst, sid, rate, placed, start, end)

    def offer(self, caller: int, callee: int, turns):
        """Traffic arrives at ``caller``: a session with ``callee`` to run when it can."""
        node = self.nodes[caller]
        record = {
            "caller": caller,
            "callee": callee,
            "arrived": self.t,
            "done": None,
            "ok": False,
            "attempts": 0,
            "air_s": 0.0,
        }
        self.sessions.append(record)
        node.queue.append((callee, turns, record))
        if not node.busy:
            self.start(self._caller(node))

    def _caller(self, node: Node):
        node.busy = True
        while node.queue:
            callee, turns, record = node.queue.pop(0)
            for attempt in range(ATTEMPTS):
                while self._engaged(node):  # called by someone else meanwhile (mesh)
                    yield ("wait", self.rng.uniform(5.0, 15.0))
                record["attempts"] = attempt + 1
                ok = yield from self._session(node.i, callee, turns, record)
                if ok:
                    record["ok"], record["done"] = True, self.t
                    break
                if attempt + 1 < ATTEMPTS:
                    yield ("wait", self.rng.uniform(*RETRY_S))
            if not record["ok"]:
                record["done"] = self.t
        node.busy = False

    def _engaged(self, node: Node) -> bool:
        return node.session is not None and self.t - node.last_heard < IDLE_S

    def _session(self, a: int, b: int, turns, record):
        """One attempt at a session (arqsim's rt-ack2 with a settled rate): True if every
        turn arrived."""
        sid = self.next_sid
        self.next_sid += 1
        A, B = self.nodes[a], self.nodes[b]
        rate = arq_rate(self.snr[a, b])
        room = capacity(rate, self.cap_s) - HEAD_BYTES - 2 * ACK_BYTES
        # Each direction: its frames (sizes) in turn order, the turn each belongs to, those
        # that arrived, those the sender knows arrived.
        frames, turn_of = {a: [], b: []}, {a: [], b: []}
        for k, (who, n) in enumerate(turns):
            src = a if who == "A" else b
            sizes = [200] * (n // 200) + ([n % 200] if n % 200 else [])
            frames[src] += sizes
            turn_of[src] += [k] * len(sizes)
        arrived = {a: set(), b: set()}
        known = {a: set(), b: set()}
        snapshot = {a: set(), b: set()}  # what the ACK in each station's last tx reported

        def released(src):
            # A turn is written once every frame of the earlier turns has arrived.
            k = 0
            while k < len(turns):
                pending = [
                    i
                    for d in (a, b)
                    for i, t in enumerate(turn_of[d])
                    if t == k and i not in arrived[d]
                ]
                if pending:
                    break
                k += 1
            return [i for i, t in enumerate(turn_of[src]) if t <= k]

        def finished():
            return all(len(arrived[d]) == len(frames[d]) == len(known[d]) for d in (a, b))

        def send(src, dst, poll=False):
            other = b if src == a else a
            snapshot[src] = set(arrived[other])
            ids = [] if poll else [i for i in released(src) if i not in known[src]]
            chosen, used = [], 0
            for i in ids:
                n = arqsim.OVERHEAD + 4 + frames[src][i]
                if used + n > room:
                    break
                chosen.append(i)
                used += n
            r = rate if chosen else arqsim.CONTROL.rate
            yield ("wait", arqsim.TURNAROUND_S)
            tx = self.transmission(src, dst, sid, r, [frames[src][i] for i in chosen], acks=2)
            record["air_s"] += tx.end - tx.start
            got = yield ("tx", tx)
            ok = got.get(dst)
            if not ok or not any(ok):
                return False  # nothing of this session understood
            arrived[src].update(i for i, hit in zip(chosen, ok[2:], strict=False) if hit)
            if any(hit for (kind, *_), hit in zip(tx.frames, ok, strict=False) if kind == "ACK"):
                known[dst] |= snapshot[src]
            return True

        A.session, A.last_heard = sid, np.inf  # a caller answers nobody else meanwhile
        fails, poll, first = 0, False, True
        while True:
            if finished():
                break
            if first or fails:
                yield ("access", a)  # a new call, or after a failure: listen first
            understood = yield from send(a, b, poll)
            poll = False
            if understood and (B.session == sid or not self._engaged(B)):
                B.session, B.last_heard = sid, self.t
                first = False
                if finished():
                    break
                if (yield from send(b, a)):
                    fails = 0
                else:
                    fails += 1
                    poll = True
            else:
                yield ("wait", arqsim.TIMEOUT_S)
                fails += 1
            if fails >= arqsim.MAX_RETRIES:
                break
        A.session = None
        done = finished()
        if done and B.session == sid:
            B.session = None  # the closing exchange (not modelled) frees it; otherwise it
        return done  # waits IDLE_S after the last thing it understood


# --- experiments -------------------------------------------------------------------------


def b2f_sizes():
    return [(who, len(b)) for who, b in arqsim.b2f_turns(0)]


def net_job(args):
    kind, stations, gateways, hidden, load, access, seed, hours, capture = args
    rng = np.random.default_rng(seed)
    snr, callers, targets = topology(kind, stations, gateways, hidden, rng)
    net = Net(snr, ACCESS[access], seed + 1, gateways=gateways if kind == "hub" else 0)
    net.capture = capture
    # Poisson arrivals: ``load`` sessions per hour per calling station.
    turns = b2f_sizes()
    for c in callers:
        t = rng.exponential(3600 / load)
        while t < hours * 3600:
            callee = targets[c][rng.integers(len(targets[c]))]
            net.at(t, lambda c=c, callee=callee: net.offer(c, callee, turns))
            t += rng.exponential(3600 / load)
    net.run(hours * 3600 + 3 * 3600)  # time to finish what was offered
    busy = sorted((t.start, t.end) for t in net.txs if t.start < hours * 3600)
    on, last = 0.0, 0.0
    for s, e in busy:
        s, e = max(s, last), min(e, hours * 3600)
        if e > s:
            on += e - s
            last = e
    return args, net.sessions, on / (hours * 3600), len(callers)


def net_report(results) -> None:
    rows = {}
    for (kind, st, gw, hidden, load, access, *_), sessions, busy, n in results:
        rows.setdefault((kind, st, gw, hidden, load, access), []).append((sessions, busy, n))
    print(
        "topology stations gw hidden load/h access   | offered/h | done   | session s: "
        "median  p90 | attempts | air s/session | channel busy"
    )
    for (kind, st, gw, hidden, load, access), v in sorted(rows.items()):
        sessions = [s for ss, _, _ in v for s in ss]
        ok = [s for s in sessions if s["ok"]]
        hours = results[0][0][7]
        offered = len(sessions) / len(v) / hours
        took = sorted(s["done"] - s["arrived"] for s in ok)
        med = f"{np.median(took):6.0f} {np.percentile(took, 90):5.0f}" if took else "     -     -"
        att = np.mean([s["attempts"] for s in sessions]) if sessions else 0
        air = np.mean([s["air_s"] for s in sessions]) if sessions else 0
        print(
            f"{kind:8s} {st:8d} {gw:2d} {hidden:6.1f} {load:6.1f} {access:8s} | "
            f"{offered:9.1f} | {len(ok) / max(len(sessions), 1):5.0%} | {med}        | "
            f"{att:8.2f} | {air:13.0f} | {np.mean([b for _, b, _ in v]):5.0%}"
        )


def single_job(args):
    snr, seed = args
    net = Net(np.array([[-np.inf, snr], [snr, -np.inf]]), ACCESS["aloha"], seed)
    net.at(0.0, lambda: net.offer(0, 1, b2f_sizes()))
    net.run(3600)
    s = net.sessions[0]
    return args, s


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("experiment", choices=("sweep", "single"))
    p.add_argument("--topology", choices=("hub", "mesh"), default="hub")
    p.add_argument("--stations", nargs="+", type=int, default=[2, 5, 10, 20])
    p.add_argument("--gateways", type=int, default=1)
    p.add_argument("--hidden", nargs="+", type=float, default=[0.0, 0.3])
    p.add_argument("--load", nargs="+", type=float, default=[1.0], help="sessions/h/station")
    p.add_argument("--access", nargs="+", default=list(ACCESS), choices=list(ACCESS))
    p.add_argument("--hours", type=float, default=4.0)
    p.add_argument("--snrs", nargs="+", type=float, default=[6.0, 9.0, 15.0, 21.0])
    p.add_argument(
        "--no-capture",
        dest="capture",
        action="store_false",
        help="a receiver following one signal still catches a later preamble",
    )
    p.add_argument("--seeds", type=int, default=8)
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    args = p.parse_args(argv)
    started = time.time()
    if args.experiment == "single":
        jobs = [(s, k + 1) for s in args.snrs for k in range(args.seeds)]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(single_job, jobs))
        print("SNR | done | session s, median [range] | air s")
        for snr in args.snrs:
            v = [s for (x, _), s in results if x == snr]
            took = sorted(s["done"] - s["arrived"] for s in v if s["ok"])
            span = f"{np.median(took):6.1f} [{took[0]:.0f}-{took[-1]:.0f}]" if took else "-"
            air = np.median([s["air_s"] for s in v])
            print(f"{snr:3.0f} | {len(took)}/{len(v)} | {span:22s} | {air:5.0f}")
    else:
        jobs = [
            (args.topology, st, args.gateways, h, ld, a, 1000 * k + 1, args.hours, args.capture)
            for st in args.stations
            for h in args.hidden
            for ld in args.load
            for a in args.access
            for k in range(args.seeds)
        ]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            net_report(list(pool.map(net_job, jobs, chunksize=1)))
    print(f"({time.time() - started:.0f} s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
