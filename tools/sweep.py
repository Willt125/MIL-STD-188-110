"""Measure which mode suits which channel: the data behind the channel notebook's section
"Matching the mode to the conditions" (docs/channel.py), saved to docs/data/sweep.json.

Usage:
    python tools/sweep.py --estimate                  # what a run would cost; runs nothing
    python tools/sweep.py                             # every experiment (hours)
    python tools/sweep.py --experiments delay doppler --channels 3 --jobs 4

Four experiments, each a grid of points; every point is several one-minute messages, each on
its own channel realisation (Appendix E Watterson channel, SNR in 3 kHz), through the complete
receiver, exactly as tools/conformance.py runs them:

* interleave: 1200 bps at zero, short and long interleave, on 0.5, 1 and 5 Hz fading, against
  SNR. Does an interleaver help only when its block is longer than a fade?
* rate: every rate (long interleave; 4800 bps has none) on 2 paths, 2 ms, 1 Hz, against SNR.
  Where does each rate stop being the best choice?
* delay: 2400L and 600L at a high SNR against delay spread, 0-12 ms (1 Hz fading). The probes
  span 16 symbols, 6.7 ms at 2400 bps: does the equaliser give out near there?
* doppler: 2400L, 600L and 75L at a high SNR against fading bandwidth, 0.5-30 Hz (2 ms).
  Does denser probing (600 bps) or none at all (75 bps) change how fast a channel can fade?

For each point it records bits, bit errors, and 256-byte frames sent and received intact (a
frame with any error, or missing, is lost), from which the notebook computes the bit error
rate and the goodput (rate x fraction of frames intact): what a link layer would get.

The run is resumable: results are saved after every message, and a rerun only does what is
missing, so it can be split across sessions or machines (merge with --merge). The numbers
are this receiver's, not limits of the standard.
"""

import argparse
import datetime
import json
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

import m110c
from m110c.channel.watterson import ChannelSpec, WattersonChannel
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit_bytes

FS = 9600
MESSAGE_SECONDS = 60
FRAME_BYTES = 256
LEAD, TAIL = 4000, 9600
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "data", "sweep.json")
HIGH_SNR_DB = 25.0  # "noise is not the problem": isolates delay or Doppler


def experiments():
    """Every point, as dicts: experiment, rate, interleave, paths, delay_ms, doppler_hz, snr_db."""
    points = []

    def add(exp, rate, interleave, delay, doppler, snr):
        points.append(
            {
                "experiment": exp,
                "rate": rate,
                "interleave": "zero" if rate == 4800 else interleave,
                "delay_ms": float(delay),
                "doppler_hz": float(doppler),
                "snr_db": float(snr),
            }
        )

    for doppler in (0.5, 1.0, 5.0):
        for interleave in ("zero", "short", "long"):
            for snr in range(4, 24, 2):
                add("interleave", 1200, interleave, 2.0, doppler, snr)
    for rate in (4800, 2400, 1200, 600, 300, 150, 75):
        for snr in range(-4, 28, 2):
            add("rate", rate, "long", 2.0, 1.0, snr)
    for rate in (2400, 600):
        for delay in range(0, 13):
            add("delay", rate, "long", delay, 1.0, HIGH_SNR_DB)
    for rate in (2400, 600, 75):
        for doppler in (0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0):
            add("doppler", rate, "long", 2.0, doppler, HIGH_SNR_DB)
    return points


def key(point) -> str:
    return (
        f"{point['experiment']}|{point['rate']}{point['interleave'][0].upper()}|"
        f"{point['delay_ms']:g}ms|{point['doppler_hz']:g}Hz|{point['snr_db']:g}dB"
    )


def seed_for(point_key: str, k: int) -> int:
    """A stable seed per point and message (Python's hash() is salted per process)."""
    h = 0
    for ch in point_key:
        h = (h * 131 + ord(ch)) % 1_000_003
    return h * 1000 + k


def run_message(job):
    point, point_key, k = job
    m = mode(point["rate"], point["interleave"])
    rng = np.random.default_rng(seed_for(point_key, k))
    n_bytes = max(FRAME_BYTES, m.rate * MESSAGE_SECONDS // 8 // FRAME_BYTES * FRAME_BYTES)
    msg = bytes(rng.integers(0, 256, n_bytes, dtype=np.uint8))
    x = np.concatenate(
        [np.zeros(LEAD, complex), modulate(transmit_bytes(msg, m)), np.zeros(TAIL, complex)]
    )
    paths = 2 if point["delay_ms"] > 0 or point["doppler_hz"] > 0 else 1
    spec = ChannelSpec(paths, point["delay_ms"], point["doppler_hz"], point["snr_db"])
    y = WattersonChannel(spec, seed=seed_for(point_key, k) + 7).process(x)
    # The mode is given, not told from the data (S4): this measures the mode itself.
    told = "zero" if m.interleave == "zero" and m.rate != 4800 else "short"
    rx = Receiver(interleave=told)
    out = b""
    for i in range(0, y.size, FS):
        out += rx.process(y[i : i + FS])
    out += rx.finish()
    n = min(len(out), n_bytes)
    a = np.frombuffer(out[:n], np.uint8)
    b = np.frombuffer(msg[:n], np.uint8)
    errors = int(np.unpackbits(a ^ b).sum()) + 8 * (n_bytes - n) + 8 * max(0, len(out) - n_bytes)
    frames = n_bytes // FRAME_BYTES
    padded = out[:n_bytes].ljust(n_bytes, b"\0")  # missing bytes never match random data
    good = sum(
        padded[f * FRAME_BYTES : (f + 1) * FRAME_BYTES]
        == msg[f * FRAME_BYTES : (f + 1) * FRAME_BYTES]
        for f in range(frames)
    )
    return point_key, {
        "bits": 8 * n_bytes,
        "errors": errors,
        "frames": frames,
        "frames_ok": int(good),
        "channels": 1,
        "seconds": x.size / FS,
    }


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"meta": {}, "points": {}}


def save(data, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def add_counts(entry, counts):
    for name in ("bits", "errors", "frames", "frames_ok", "channels", "seconds"):
        entry[name] = entry.get(name, 0) + counts[name]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    names = ("interleave", "rate", "delay", "doppler")
    p.add_argument("--experiments", nargs="+", choices=names, default=list(names))
    p.add_argument("--channels", type=int, default=6, help="one-minute messages per point")
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    p.add_argument("-o", "--output", default=OUT)
    p.add_argument("--estimate", action="store_true", help="print the cost of a run and stop")
    p.add_argument("--merge", nargs="+", default=[], help="add the counts of other sweep files")
    args = p.parse_args(argv)

    data = load(args.output)
    for other in args.merge:
        for k, entry in load(other)["points"].items():
            mine = data["points"].setdefault(
                k,
                {
                    kk: v
                    for kk, v in entry.items()
                    if kk not in ("bits", "errors", "frames", "frames_ok", "channels", "seconds")
                },
            )
            add_counts(mine, entry)
    if args.merge:
        save(data, args.output)
        print(f"merged {len(args.merge)} file(s) into {args.output}")
        return 0

    jobs = []
    for point in experiments():
        if point["experiment"] not in args.experiments:
            continue
        k = key(point)
        entry = data["points"].setdefault(k, dict(point))
        done = entry.get("channels", 0)
        jobs += [(point, k, done + i) for i in range(max(0, args.channels - done))]
    signal_s = len(jobs) * (MESSAGE_SECONDS + 2)
    print(
        f"{len(jobs)} one-minute messages to run ({signal_s / 3600:.1f} h of signal); at the "
        f"receiver's 0.1-0.4 s of CPU per second of signal, about "
        f"{0.3 * signal_s / 3600 / args.jobs:.1f} h on {args.jobs} processes",
        file=sys.stderr,
    )
    if args.estimate or not jobs:
        return 0
    jobs.sort(key=lambda j: -j[0]["snr_db"])  # spread the slow (lossy) points across the run
    data["meta"] = {
        "m110c": m110c.__version__,
        "date": datetime.date.today().isoformat(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "message_seconds": MESSAGE_SECONDS,
        "frame_bytes": FRAME_BYTES,
        "channel": "Appendix E Watterson, 2 paths (1 if no delay and no fading), SNR in 3 kHz",
    }
    started, last_save = time.time(), 0.0

    def results():
        if args.jobs == 1:  # in-process: for tests and debugging
            yield from map(run_message, jobs)
            return
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futures = [pool.submit(run_message, j) for j in jobs]
            for fut in as_completed(futures):
                yield fut.result()

    for done, (k, counts) in enumerate(results(), 1):
        add_counts(data["points"][k], counts)
        if time.time() - last_save > 30 or done == len(jobs):
            save(data, args.output)
            last_save = time.time()
            print(f"{done}/{len(jobs)} messages, {time.time() - started:.0f} s", file=sys.stderr)
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
