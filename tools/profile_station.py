"""Where a station's processor time goes: the receiver's cost per second of signal, by stage
(docs/PLAN.md, the modem program's performance goal; docs/PERFORMANCE.md, P1).

Usage:
    python tools/profile_station.py                      # every scenario, noise only
    python tools/profile_station.py --fading             # on 2 paths, 2 ms, 1 Hz fading
    python tools/profile_station.py --scenarios idle idle-late 2400S --seconds 20

Each scenario feeds the receiver ``--seconds`` of signal as a station gets it from a sound
card: 48000 S/s, brought down to 9600 first (Receiver(working_rate=9600), as the station
does), in 0.1-s blocks. Receiving is nearly all of a station's cost (transmitting measured
0.01-0.02 of a core), and profiling the receiver directly avoids the station's threads,
which a profiler does not follow.

Two measurements per scenario:

* processor seconds per second of signal, without the profiler: the figure the budget
  holds (on the reference machine, docs/ONAIR.md: the 3020e laptops);
* the same time split by stage, from a second run under cProfile. Time spent inside NumPy
  (or any code outside m110c) is charged to the m110c function that called it, following
  the profiler's caller records upward, so a stage's share includes the library work it
  asked for. The profiler slows everything down and not evenly, so read the shares as a
  ranking, not as exact fractions.

Stages, by source file:

    search       rx/acquire.py              the preamble search (runs while idle)
    late entry   rx/lateentry.py            joining without a preamble (--late-entry)
    demodulator  rx/equalize.py, track.py,  channel estimates, equaliser, tracking loops
                 frontend.py
    decoder      rx/decode.py, demap.py,    soft demapping, deinterleaving, Viterbi, EOM
                 autointerleave.py, eom.py,
                 fec.py, interleave.py, ...
    resampling   resample.py                48000 -> 9600
    receiver     rx/receiver.py             bookkeeping between the stages
"""

import argparse
import cProfile
import pathlib
import pstats
import sys
import time

import numpy as np

from m110c.channel.watterson import ChannelSpec, WattersonChannel
from m110c.modulate import modulate
from m110c.resample import Resampler
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit_bytes

CARD_RATE = 48000
# name: (rate, interleave, bytes) of the message (bytes None: enough to fill the scenario),
# or None for noise alone.
SCENARIOS = {
    "idle": None,
    "idle-late": None,  # with late entry on
    "2400S": (2400, "short", None),
    "2400L": (2400, "long", None),
    "1200S": (1200, "short", None),
    "600S": (600, "short", None),
    "150S": (150, "short", None),
    "75S": (75, "short", None),
    "600S-60B": (600, "short", 60),  # a link layer's short control transmission (1.8 s)
}
STAGES = {
    "acquire": "search",
    "lateentry": "late entry",
    "equalize": "demodulator",
    "track": "demodulator",
    "frontend": "demodulator",
    "receiver": "receiver",
    "resample": "resampling",
}
ORDER = ["search", "late entry", "demodulator", "decoder", "resampling", "receiver", "other"]
PACKAGE = str(pathlib.Path(__import__("m110c").__file__).parent)


def stage_of(filename: str) -> str | None:
    """The stage a source file belongs to, or None if it is not part of m110c."""
    if not filename.startswith(PACKAGE):
        return None
    name = pathlib.Path(filename).stem
    if name in STAGES:
        return STAGES[name]
    if name in (
        "decode",
        "demap",
        "autointerleave",
        "eom",
        "fec",
        "interleave",
        "framing",
        "bits",
        "scrambler",
        "mgd",
        "symbols",
    ):
        return "decoder"
    return "other"


def signal(name: str, seconds: float, fading: bool, seed: int = 1) -> np.ndarray:
    """``seconds`` of what a sound card would deliver: the scenario's message (or nothing)
    in noise or fading, at 48000 S/s."""
    n = int(seconds * 9600)
    if SCENARIOS[name] is None:
        x = np.zeros(n, complex)
    else:
        rate, interleave, n_bytes = SCENARIOS[name]
        n_bytes = n_bytes or rate * int(seconds) // 8
        data = bytes(np.random.default_rng(seed).integers(0, 256, n_bytes, dtype=np.uint8))
        x = modulate(transmit_bytes(data, mode(rate, interleave)))
        x = np.concatenate([np.zeros(2400, complex), x])[:n]
        x = np.concatenate([x, np.zeros(n - x.size, complex)])
    spec = ChannelSpec(2, 2.0, 1.0, 15.0) if fading else ChannelSpec(1, 0.0, 0.0, 30.0)
    x = WattersonChannel(spec, seed=seed + 7).process(x)
    up = Resampler(9600, CARD_RATE)
    return np.concatenate([up.process(x), up.flush()])


def receive(x: np.ndarray, late_entry: bool) -> float | None:
    """Run the receiver over ``x``; return the processor seconds spent up to the block in
    which the first message was detected (None if none was)."""
    rx = Receiver(sample_rate=CARD_RATE, working_rate=9600, late_entry=late_entry)
    block = CARD_RATE // 10
    start, detected = time.process_time(), None
    for i in range(0, x.size, block):
        rx.process(x[i : i + block])
        if detected is None and (rx.receiving is not None or rx.messages):
            detected = time.process_time() - start
    rx.finish()
    return detected


def by_stage(profile: cProfile.Profile) -> dict[str, float]:
    """Each function's own time, charged to its stage; time outside m110c is charged up
    its callers (in proportion to the time each caller accounted for) to the first m110c
    function found."""
    stats = pstats.Stats(profile).stats
    totals = dict.fromkeys(ORDER, 0.0)

    def charge(func, seconds, depth=0, seen=()):
        stage = stage_of(func[0])
        callers = stats.get(func, (0, 0, 0, 0, {}))[4]
        if stage is not None or not callers or depth > 30:
            totals[stage or "other"] += seconds
            return
        shares = {c: v[2] + 1e-12 for c, v in callers.items() if c not in seen}
        if not shares:
            totals["other"] += seconds
            return
        whole = sum(shares.values())
        for caller, share in shares.items():
            charge(caller, seconds * share / whole, depth + 1, (*seen, func))

    for func, (_, _, own, _, _) in stats.items():
        if own:
            charge(func, own)
    return totals


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--scenarios", nargs="+", choices=list(SCENARIOS), default=list(SCENARIOS))
    p.add_argument("--seconds", type=float, default=10.0, help="signal per scenario (10)")
    p.add_argument("--fading", action="store_true", help="2 paths, 2 ms, 1 Hz at 15 dB")
    args = p.parse_args(argv)
    channel = "2 paths, 2 ms, 1 Hz, 15 dB" if args.fading else "noise, 30 dB"
    print(
        f"Receiver at a sound card's {CARD_RATE} S/s (worked at 9600), {channel}; "
        f"{args.seconds:g} s per scenario."
    )
    print(
        "CPU: processor seconds per second of signal, unprofiled. Detect: processor seconds "
        "up to\nthe block in which the message was found (once per message). Stages: shares "
        "of the profiled time.\n"
    )
    header = f"{'scenario':10s} {'CPU':>6s} {'detect':>6s} | "
    print(header + " | ".join(f"{s:>11s}" for s in ORDER))
    for name in args.scenarios:
        x = signal(name, args.seconds, args.fading)
        late = name == "idle-late"
        start = time.process_time()
        detected = receive(x, late)
        cpu = (time.process_time() - start) / args.seconds
        profile = cProfile.Profile()
        profile.enable()
        receive(x, late)
        profile.disable()
        totals = by_stage(profile)
        whole = sum(totals.values()) or 1.0
        shares = " | ".join(f"{100 * totals[s] / whole:10.0f}%" for s in ORDER)
        detect = "-" if detected is None else f"{detected:.2f}"
        print(f"{name:10s} {cpu:6.3f} {detect:>6s} | {shares}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
