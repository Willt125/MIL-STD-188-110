"""Measure how well the receiver tells zero interleave from short interleave (S4, INT-012).

Usage:
    python tools/interleave_check.py margins              # about 5 min on 4 processes
    python tools/interleave_check.py end-to-end           # about 11 min
    python tools/interleave_check.py margins --rates 2400 --seeds 1 --jobs 1

margins: the evidence behind rx/autointerleave.py's DECIDE_MARGIN. Every rate, both
interleaves sent, a noise-only channel and Table XVI's commonest fading channel (2 paths,
2 ms, 1 Hz), -9 to 18 dB. The received symbols go to a short-interleave decoder and a
zero-interleave decoder, and the difference of their Viterbi fits (true minus wrong) is
recorded after one, two and three blocks, with whether the message decodes at all (bit error
rate below 1e-3 under the true interleave). The rule is safe if every decodable message is
ahead by more than DECIDE_MARGIN after one block.

end-to-end: the complete receiver with interleave="auto" against one told the truth, on
every Table XVI row below 4800 bps, from 4 dB below to 3 dB above the row's SNR, both
interleaves, 10-s messages. Counts wrong modes and messages auto received worse.

Results are in docs/PERFORMANCE.md (S4). Seeds are fixed, so a rerun gives the same numbers.
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from m110c.bits import bytes_to_bits
from m110c.channel.watterson import ChannelSpec, WattersonChannel
from m110c.modulate import modulate
from m110c.rx.acquire import PreambleSearch
from m110c.rx.decode import DataDecoder
from m110c.rx.equalize import EqualizingDemodulator
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.spec import tables as T
from m110c.tx import transmit_bytes

RATES = (2400, 1200, 600, 300, 150, 75)
BLOCKS = 3


def _signal(msg, m):
    return np.concatenate(
        [np.zeros(3000, complex), modulate(transmit_bytes(msg, m)), np.zeros(9600, complex)]
    )


def margins_job(args):
    """Fit(true) - fit(wrong) after each of the first blocks, and whether it decodes."""
    rate, sent, paths, snr, seed = args
    m = mode(rate, sent)
    msg = bytes(np.random.default_rng(seed).integers(0, 256, max(64, rate // 2), dtype=np.uint8))
    spec = ChannelSpec(2, 2.0, 1.0, snr) if paths == 2 else ChannelSpec(1, 0.0, 0.0, snr)
    y = WattersonChannel(spec, seed=seed + 1).process(_signal(msg, m))
    search = PreambleSearch()
    detection = search.process(y) or search.flush()
    if detection is None:
        return args, None
    start = int(detection.segment_start) - 8
    demod = EqualizingDemodulator(detection.mode, detection, start_index=start)
    parts = [demod.process(y[start:]), demod.finish()]
    z = np.concatenate([p[0] for p in parts])
    var = np.concatenate([p[1] for p in parts])
    block = m.block_symbols
    fits = {}
    for hypothesis in ("short", "zero"):
        decoder = DataDecoder(mode(rate, hypothesis))
        fits[hypothesis] = []
        for b in range(min(BLOCKS, z.size // block)):
            decoder.process(z[b * block : (b + 1) * block], var[b * block : (b + 1) * block])
            fits[hypothesis].append(decoder._viterbi.fit)
    wrong = "zero" if sent == "short" else "short"
    margins = [t - w for t, w in zip(fits[sent], fits[wrong], strict=True)]
    decoder = DataDecoder(m)
    bits = np.concatenate([decoder.process(z, var), decoder.finish()])
    ref = bytes_to_bits(msg)
    k = min(bits.size, ref.size)
    errors = int(np.sum(bits[:k] != ref[:k])) + ref.size - k
    return args, {"margins": margins, "decodable": errors / ref.size < 1e-3}


def end_to_end_job(args):
    """Bit error rate with interleave="auto" and with the truth, and the mode auto chose."""
    rate, sent, row, relative_db, seed = args
    r = T.TABLE_XVI[row]
    m = mode(rate, sent)
    msg = bytes(
        np.random.default_rng(seed).integers(0, 256, max(16, rate * 10 // 8), dtype=np.uint8)
    )
    spec = ChannelSpec(
        r.paths, r.multipath_ms or 0.0, r.fading_bw_hz or 0.0, r.snr_db + relative_db
    )
    y = WattersonChannel(spec, seed=seed + 3).process(_signal(msg, m))
    out = {}
    for name, interleave in (("auto", "auto"), ("truth", sent)):
        rx = Receiver(interleave=interleave)
        got = b"".join(rx.process(y[i : i + 9600]) for i in range(0, y.size, 9600)) + rx.finish()
        a = bytes_to_bits(got[: len(msg)].ljust(len(msg), b"\0"))
        out[name] = float(np.mean(a != bytes_to_bits(msg)))
        if name == "auto":
            out["mode"] = str(rx.messages[0].mode) if rx.messages else None
    out["expected"] = str(m)
    return args, out


def run(jobs, fn, n_jobs):
    if n_jobs == 1:
        return [fn(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=n_jobs) as pool:
        return list(pool.map(fn, jobs, chunksize=2))


def report_margins(results) -> None:
    print("rate  blocks | decodable: n, smallest margin | not decodable: n, smallest, largest")
    rows = {}
    for (rate, *_), o in results:
        if o is None:
            continue
        for b, margin in enumerate(o["margins"]):
            rows.setdefault((rate, b + 1), []).append((margin, o["decodable"]))
    for (rate, b), values in sorted(rows.items(), key=lambda kv: (-kv[0][0], kv[0][1])):
        good = [m for m, ok in values if ok]
        bad = [m for m, ok in values if not ok]

        def fmt(xs, f):
            return f"{f(xs):+.4f}" if xs else "   n/a "

        print(
            f"{rate:5d}  {b}      | {len(good):4d} {fmt(good, min)}"
            f"                 | {len(bad):4d} {fmt(bad, min)} {fmt(bad, max)}"
        )


def report_end_to_end(results) -> None:
    n = len(results)
    wrong = sum(o["mode"] is not None and o["mode"] != o["expected"] for _, o in results)
    worse = sum(o["auto"] > o["truth"] + 1e-12 for _, o in results)
    decodable = sum(o["truth"] < 1e-3 for _, o in results)
    print(
        f"{n} messages ({decodable} decodable when told the truth): {wrong} received in the "
        f"wrong mode, {worse} received worse with interleave=auto"
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("experiment", choices=("margins", "end-to-end"))
    p.add_argument("--rates", type=lambda s: [int(x) for x in s.split(",")], default=RATES)
    p.add_argument("--seeds", type=int, help="messages per point (6 margins, 8 end to end)")
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    args = p.parse_args(argv)
    started = time.time()
    if args.experiment == "margins":
        seeds = args.seeds or 6
        jobs = [
            (rate, sent, paths, snr, 100 * k + 7)
            for rate in args.rates
            for sent in ("short", "zero")
            for paths in (1, 2)
            for snr in (-9, -6, -3, 0, 3, 6, 9, 12, 18)
            for k in range(seeds)
        ]
        report_margins(run(jobs, margins_job, args.jobs))
    else:
        seeds = args.seeds or 8
        jobs = [
            (r.rate, sent, i, relative, 1000 + 17 * k)
            for i, r in enumerate(T.TABLE_XVI)
            if r.rate in args.rates and r.rate != 4800
            for sent in ("short", "zero")
            for relative in (-4, -2, 0, 3)
            for k in range(seeds)
        ]
        report_end_to_end(run(jobs, end_to_end_job, args.jobs))
    print(f"({len(jobs)} messages, {time.time() - started:.0f} s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
