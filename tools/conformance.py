"""Generate docs/CONFORMANCE.md: the receiver against Table XVI of MIL-STD-188-110C.

Usage:
    python tools/conformance.py                 # the full run (about 1.5 CPU-hours)
    python tools/conformance.py --quick         # 20x fewer bits, to try the tool out
    python tools/conformance.py --jobs 4 -o docs/CONFORMANCE.md

Every row of Table XVI is run through the Appendix E channel simulator (``m110c-chan``'s
model) and the complete receiver (``m110c-rx``'s, no genie): preamble search, tracking,
equalisation, decoding. Each row is split into many messages of about a minute, each on an
independent channel realisation, and the bits are counted over all of them.

A row *meets* its requirement when the 95% upper confidence bound on its BER is below the
requirement: with no errors in N bits that bound is about 3/N, so the 1e-5 rows need over
3e5 bits; they get 1e6. A row whose measured BER is below the requirement but whose bound is
not is reported as *consistent*. The bound treats bits as independent; on a fading channel
errors come in bursts (one fade), so it is optimistic there, and the number of independent
channels is reported next to it.

Everything is seeded: the same command gives the same report on the same code.
"""

import argparse
import datetime
import math
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

import m110c
from m110c.channel.awgn import add_awgn
from m110c.channel.watterson import DEFAULT_SIGNAL_POWER, ChannelSpec, WattersonChannel
from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.spec import tables as T
from m110c.tx import transmit_bytes

FS = 9600
MESSAGE_SECONDS = 60  # data per message (each message is one channel realisation)
LEAD, TAIL = 4000, 9600  # silence around each transmission, samples
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "CONFORMANCE.md")


def target_bits(max_ber: float) -> int:
    """Bits to run per row: enough that no errors bounds the BER at a third of the
    requirement (95%), and at least 1e5."""
    return max(100_000, int(10 / max_ber))


def row_mode(row):
    return mode(row.rate, "zero" if row.rate == 4800 else "long")  # Table XVI: long


def row_spec(row):
    if row.fading:
        return ChannelSpec(2, row.multipath_ms, row.fading_bw_hz, row.snr_db)
    return ChannelSpec(1, 0.0, 0.0, row.snr_db)


def bit_errors(out: bytes, msg: bytes) -> int:
    n = min(len(out), len(msg))
    a = np.frombuffer(out[:n], np.uint8)
    b = np.frombuffer(msg[:n], np.uint8)
    return int(np.unpackbits(a ^ b).sum()) + 8 * abs(len(out) - len(msg))


def run_message(job):
    """One message through one channel realisation. Returns a dict of counts."""
    kind, index, row_index, seed, n_bytes, snr_db = job
    rng = np.random.default_rng(10_000 * row_index + seed)
    msg = bytes(rng.integers(0, 256, n_bytes, dtype=np.uint8))
    if kind == "xvi":
        row = T.TABLE_XVI[row_index]
        m = row_mode(row)
    else:
        m = mode(4800)
    x = np.concatenate(
        [np.zeros(LEAD, complex), modulate(transmit_bytes(msg, m)), np.zeros(TAIL, complex)]
    )
    if kind == "xvi":
        y = WattersonChannel(row_spec(row), seed=seed).process(x)
    else:
        y = add_awgn(x, snr_db, FS, rng, signal_power=DEFAULT_SIGNAL_POWER)
    rx = Receiver()
    out = b""
    for k in range(0, y.size, 9600):  # a stream, as m110c-rx reads it
        out += rx.process(y[k : k + 9600])
    out += rx.finish()
    return {
        "index": index,
        "bits": 8 * n_bytes,
        "errors": bit_errors(out, msg),
        "messages": len(rx.messages),
        "eom": sum(m.eom_found for m in rx.messages),
        "seconds": x.size / FS,
    }


def upper_bound(errors: int, bits: int, confidence: float = 0.95) -> float:
    """Clopper-Pearson upper bound on a binomial proportion (exact, by bisection)."""
    if bits == 0:
        return 1.0
    alpha = 1 - confidence

    def cdf(p):  # P(X <= errors) for X ~ Binomial(bits, p)
        if p <= 0:
            return 1.0
        if p >= 1:
            return 0.0 if errors < bits else 1.0
        log_p, log_q = math.log(p), math.log1p(-p)
        terms = [
            math.lgamma(bits + 1)
            - math.lgamma(i + 1)
            - math.lgamma(bits - i + 1)
            + i * log_p
            + (bits - i) * log_q
            for i in range(errors + 1)
        ]
        top = max(terms)
        return math.exp(top) * sum(math.exp(t - top) for t in terms)

    lo, hi = errors / bits, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > alpha else (lo, mid)
    return hi


def theory_4800(snr_db: float) -> float:
    """Uncoded 8-PSK, Gray-like labels: BER ~ (2/3) Q(sqrt(2 Es/N0) sin(pi/8)), with
    Es/N0 = SNR x 3000/2400 (SNR in 3 kHz, 2400 symbols/s)."""
    es_n0 = 10 ** (snr_db / 10) * 3000 / T.SYMBOL_RATE
    x = math.sqrt(2 * es_n0) * math.sin(math.pi / 8)
    return (2 / 3) * 0.5 * math.erfc(x / math.sqrt(2))


def fmt_ber(x: float) -> str:
    if x == 0:
        return "0"
    exponent = math.floor(math.log10(x))
    return f"{x / 10**exponent:.1f}e{exponent}"


def git_describe() -> str:
    """The commit, marked "+changes" if the code (src/, tools/) differs from it. Edits to
    the docs cannot change the results, so they do not count."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        commit = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        changed = subprocess.run(
            ["git", "-C", root, "diff", "--quiet", "HEAD", "--", "src", "tools"]
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return commit + ("+changes" if changed.returncode else "")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-o", "--output", default=OUT)
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    p.add_argument("--quick", action="store_true", help="20x fewer bits (a trial run)")
    args = p.parse_args(argv)
    scale = 20 if args.quick else 1

    jobs, plan = [], []
    for r, row in enumerate(T.TABLE_XVI):
        per_message = max(8, row.rate * MESSAGE_SECONDS // 8)  # bytes
        n_messages = math.ceil(target_bits(row.max_ber) / scale / (8 * per_message))
        plan.append(("xvi", r, n_messages))
        for seed in range(n_messages):
            jobs.append(("xvi", len(plan) - 1, r, seed, per_message, None))
    awgn_snrs = (11.0, 14.0, 17.0)
    for snr in awgn_snrs:
        n_messages = math.ceil(4 / scale)
        plan.append(("awgn", snr, n_messages))
        for seed in range(n_messages):
            jobs.append(("awgn", len(plan) - 1, 99, seed, 4800 * 30 // 8, snr))

    totals = [
        {"bits": 0, "errors": 0, "messages": 0, "eom": 0, "n": 0, "seconds": 0.0} for _ in plan
    ]
    started = time.time()
    # Longest jobs first, so the pool is not left waiting on one at the end.
    jobs.sort(key=lambda j: -j[4] / (T.TABLE_XVI[j[2]].rate if j[0] == "xvi" else 4800))
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for done, result in enumerate(pool.map(run_message, jobs, chunksize=1), 1):
            t = totals[result["index"]]
            for key in ("bits", "errors", "messages", "eom", "seconds"):
                t[key] += result[key]
            t["n"] += 1
            if done % 25 == 0 or done == len(jobs):
                print(
                    f"{done}/{len(jobs)} messages, {time.time() - started:.0f} s", file=sys.stderr
                )
    elapsed = time.time() - started

    lines = [
        "# Conformance report",
        "",
        "Generated by `tools/conformance.py`"
        + (" `--quick`" if args.quick else "")
        + ". Do not edit by hand: rerun the tool.",
        "",
        f"- Code: m110c {m110c.__version__}, `{git_describe()}`",
        f"- Run: {datetime.date.today().isoformat()}, Python {platform.python_version()}, "
        f"NumPy {np.__version__}, {args.jobs} processes, {elapsed / 60:.0f} min",
        "- Receiver: the complete `m110c-rx` receiver (no genie), fed in 1-s pieces",
        "- Channel: the Appendix E Watterson simulator (`m110c-chan`), SNR in 3 kHz",
        "",
        "## Table XVI (§5.3.2.5): long interleave, 4800 bps uninterleaved",
        "",
        "Each row is many messages of about a minute, each on its own channel realisation.",
        "*Meets*: the 95% upper confidence bound on the BER is below the requirement.",
        "*Consistent*: the measured BER is below it, the bound is not. The bound treats bits",
        "as independent, which is optimistic on fading channels (errors come a fade at a",
        "time); the number of channels says how many independent fading histories were run.",
        "",
        "| Rate | Channel | SNR | Required | Bits | Channels | Errors | BER | 95% bound "
        "| Messages with EOM | Result |",
        "|-----:|---------|----:|---------:|-----:|---------:|-------:|----:|----------:"
        "|------------------:|--------|",
    ]
    for (kind, key, _), t in zip(plan, totals, strict=True):
        if kind != "xvi":
            continue
        row = T.TABLE_XVI[key]
        channel = (
            f"2 paths, {row.multipath_ms:g} ms, {row.fading_bw_hz:g} Hz"
            if row.fading
            else "1 fixed path"
        )
        ber = t["errors"] / t["bits"]
        bound = upper_bound(t["errors"], t["bits"])
        result = (
            "meets" if bound <= row.max_ber else "consistent" if ber <= row.max_ber else "**fails**"
        )
        lines.append(
            f"| {row.rate} | {channel} | {row.snr_db:g} dB | {fmt_ber(row.max_ber)} | "
            f"{t['bits']:,} | {t['n']} | {t['errors']} | {fmt_ber(ber)} | {fmt_ber(bound)} | "
            f"{t['eom']}/{t['n']} | {result} |"
        )
    lines += [
        "",
        "## 4800 bps on AWGN against theory",
        "",
        "Uncoded 8-PSK, so the bit error rate has a closed form:",
        "BER ≈ (2/3)·Q(√(2·Es/N0)·sin(π/8)), Es/N0 = SNR × 3000/2400. The gap to theory is",
        "what acquisition, tracking and equalisation cost on a channel that needs none of them.",
        "",
        "| SNR | Bits | Errors | Measured BER | Theory |",
        "|----:|-----:|-------:|-------------:|-------:|",
    ]
    for (kind, key, _), t in zip(plan, totals, strict=True):
        if kind == "awgn":
            lines.append(
                f"| {key:g} dB | {t['bits']:,} | {t['errors']} | "
                f"{fmt_ber(t['errors'] / t['bits'])} | {fmt_ber(theory_4800(key))} |"
            )
    lines.append("")
    with open(args.output, "w") as f:
        f.write("\n".join(lines))
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
