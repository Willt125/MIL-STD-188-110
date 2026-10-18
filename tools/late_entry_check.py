"""Measure late entry: joining transmissions without their preamble (S5).

Usage:
    python tools/late_entry_check.py join            # about 10 min on 4 processes
    python tools/late_entry_check.py noise           # minutes of noise alone
    python tools/late_entry_check.py harm            # preamble messages, late entry on and off
    python tools/late_entry_check.py join --rates 2400,75 --seeds 1 --jobs 1

join: every rate and interleave, cut at a random point well into the data (the preamble and
some data lost), at SNRs around and below each mode's Table XVI point, noise only and
fading (2 paths 2 ms 1 Hz; 5 ms 5 Hz at 75 bps). The late-entry receiver gets the cut
stream; a preamble receiver gets the whole one. Reports whether the late one joined, in the
right mode, how much signal it needed, and the bit error rate of what it delivered, against
the preamble receiver's over the same bytes (the end of the message, which both deliver).

noise: noise alone through a late-entry receiver: it must deliver nothing.

harm: transmissions with their preambles, received with late entry on and off: the output
must be the same.

Results are in docs/PERFORMANCE.md (S5). Seeds are fixed.
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from m110c.bits import bytes_to_bits
from m110c.channel.watterson import ChannelSpec, WattersonChannel
from m110c.framing import AsyncFormat, async_encode
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.spec import tables as T
from m110c.tx import transmit

FS = 9600
MODES = [
    (4800, "zero"), (2400, "short"), (2400, "long"), (2400, "zero"), (1200, "short"),
    (1200, "long"), (600, "short"), (600, "long"), (300, "short"), (300, "long"),
    (150, "short"), (150, "long"), (75, "short"), (75, "long"),
]  # fmt: skip
ASYNC = AsyncFormat.parse("8N1")


def table_xvi_snr(rate: int) -> float:
    """The mode's Table XVI SNR on the 2-path fading channel (4800: its fading row too)."""
    return float(min(r.snr_db for r in T.TABLE_XVI if r.rate == rate and r.fading))


def receive(y, **kwargs):
    rx = Receiver(**kwargs)
    out = b"".join(rx.process(y[i : i + FS]) for i in range(0, y.size, FS)) + rx.finish()
    return out, rx


def join_job(args):
    rate, interleave, fading, relative_db, seed = args
    m = mode(rate, interleave)
    rng = np.random.default_rng(seed)
    seconds = 30 if interleave == "long" else 12
    msg = bytes(rng.integers(32, 127, max(200, rate * seconds // 8), dtype=np.uint8))
    whole_bytes = rate in (4800, 2400, 1200, 600) or interleave == "long"
    framing = {} if whole_bytes else {"async_format": ASYNC}
    bits = bytes_to_bits(msg) if whole_bytes else async_encode(msg, ASYNC)
    lead = 3000
    x = np.concatenate(
        [np.zeros(lead, complex), modulate(transmit(bits, m)), np.zeros(FS, complex)]
    )
    x *= np.exp(2j * np.pi * rng.uniform(-300, 300) * np.arange(x.size) / FS)
    snr = (table_xvi_snr(rate) if fading else table_xvi_snr(rate) - 8) + relative_db
    if not fading:
        spec = ChannelSpec(1, 0.0, 0.0, snr)
    elif rate == 75:
        spec = ChannelSpec(2, 5.0, 5.0, snr)
    else:
        spec = ChannelSpec(2, 2.0, 1.0, snr)
    y = WattersonChannel(spec, seed=seed + 5).process(x)
    cut = lead + (m.preamble_symbols + 600) * 4 + int(rng.integers(0, 2 * FS))
    late, rx = receive(y[cut:], late_entry=True, **framing)
    full, _ = receive(y, **framing)
    msgs = [mm for mm in rx.messages if mm.late_entry]
    out = {"joined": bool(msgs), "mode": str(msgs[0].mode) if msgs else None, "expected": str(m)}
    if msgs:
        out["join_s"] = (
            msgs[0].detection.segment_start + msgs[0].detection.data_start_symbol * 4
        ) / FS
    n = min(len(late), len(full), len(msg)) - 2  # the message's end: both deliver it
    if n > 16:

        def ber(got):  # the last n bytes delivered against the message's last n
            a = bytes_to_bits(got[-n:])
            b = bytes_to_bits(msg[len(msg) - n :])
            return float(np.mean(a != b))

        out.update(ber_late=ber(late[: len(msg)]), ber_full=ber(full[: len(msg)]), bytes=n)
    return args, out


def noise_job(seed):
    rng = np.random.default_rng(50_000 + seed)
    y = (rng.standard_normal(60 * FS) + 1j * rng.standard_normal(60 * FS)) * 0.3
    out, rx = receive(y, late_entry=True)
    return len(out), len(rx.messages)


def harm_job(args):
    rate, interleave, seed = args
    m = mode(rate, interleave)
    rng = np.random.default_rng(seed)
    msg = bytes(rng.integers(0, 256, max(64, rate * 4 // 8), dtype=np.uint8))
    x = np.concatenate(
        [np.zeros(3000, complex), modulate(transmit(bytes_to_bits(msg), m)), np.zeros(FS, complex)]
    )
    y = WattersonChannel(ChannelSpec(2, 2.0, 1.0, table_xvi_snr(rate)), seed=seed).process(x)
    on, _ = receive(y, late_entry=True)
    off, _ = receive(y)
    return on == off


def run(fn, jobs, n_jobs):
    if n_jobs == 1:
        return [fn(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=n_jobs) as pool:
        return list(pool.map(fn, jobs, chunksize=1))


def report_join(results) -> None:
    rows = {}
    for (rate, interleave, fading, rel, _), o in results:
        rows.setdefault((rate, interleave, fading, rel), []).append(o)
    print("mode    channel   SNR  | joined, right mode | join time (s) | BER late / preamble")
    for (rate, _interleave, fading, rel), v in sorted(
        rows.items(), key=lambda kv: (-kv[0][0], kv[0][1], kv[0][2], kv[0][3])
    ):
        joined = [o for o in v if o["joined"]]
        right = [o for o in joined if o["mode"] == o["expected"]]
        t = np.mean([o["join_s"] for o in right]) if right else float("nan")
        bl = [o["ber_late"] for o in right if "ber_late" in o]
        bf = [o["ber_full"] for o in right if "ber_full" in o]
        snr = (table_xvi_snr(rate) if fading else table_xvi_snr(rate) - 8) + rel
        print(
            f"{v[0]['expected']:6s}  {'fading' if fading else 'noise '}  {snr:4.0f} | "
            f"{len(joined):2d}/{len(v)}, {len(right):2d} | {t:5.1f} | "
            f"{np.mean(bl) if bl else float('nan'):.1e} / {np.mean(bf) if bf else float('nan'):.1e}"
        )
    wrong = sum(1 for _, o in results if o["joined"] and o["mode"] != o["expected"])
    print(f"joined in the wrong mode: {wrong} of {len(results)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("experiment", choices=("join", "noise", "harm"))
    p.add_argument("--rates", type=lambda s: {int(x) for x in s.split(",")})
    p.add_argument("--seeds", type=int, default=4)
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    args = p.parse_args(argv)
    modes = [m for m in MODES if args.rates is None or m[0] in args.rates]
    started = time.time()
    if args.experiment == "join":
        jobs = [
            (rate, interleave, fading, rel, 100 * k + 11)
            for rate, interleave in modes
            for fading in (False, True)
            for rel in (-3, 0, 3)
            for k in range(args.seeds)
        ]
        report_join(run(join_job, jobs, args.jobs))
    elif args.experiment == "noise":
        res = run(noise_job, range(args.seeds), args.jobs)
        print(
            f"{len(res)} minutes of noise: {sum(b for b, _ in res)} bytes delivered, "
            f"{sum(n for _, n in res)} messages"
        )
    else:
        jobs = [(rate, interleave, 7 + k) for rate, interleave in modes for k in range(args.seeds)]
        res = run(harm_job, jobs, args.jobs)
        print(f"{len(res)} preamble messages: {sum(res)} received identically with late entry on")
    print(f"({time.time() - started:.0f} s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
