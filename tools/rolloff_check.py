"""Measure the transmit filter's roll-off: occupied bandwidth, and what it costs the receiver.

Usage:
    python tools/rolloff_check.py bandwidth       # seconds
    python tools/rolloff_check.py ber             # about 30 min on 4 processes
    python tools/rolloff_check.py ber --rates 2400 --seeds 4 --jobs 1

bandwidth: the modulator's spectrum (a 2400S message of random bytes, at audio) for each
roll-off, measured two ways. 110C's "occupied bandwidth" is the band holding 99% of the power
(at most 3 kHz). The US amateur rules define bandwidth at the points 26 dB below the mean
power density within the band, and limit HF data to 2.8 kHz; here the reference is the mean
over 600-3000 Hz, the flat top of the spectrum. Also with the filter twice as long (span 16
symbols each side), to see whether the skirts or the roll-off set the -26 dB width.

ber: the complete receiver against transmit roll-offs 0.15 (the amateur limit), 0.25 (the
default) and 0.35 (what 110C's appendices suggest; wider than the amateur limit, for use
where authorised), each with the receiver's filter left at 0.25 and matched to the
transmitter. 2400S, 1200S, 600S and 75S, on a noise-only channel 8 and 11 dB below the
mode's Table XVI point, and on its Table XVI fading channel (2 paths, 2 ms, 1 Hz; 75 bps:
5 ms, 5 Hz) at that point and 3 dB below (at the Table XVI points alone, errors are too rare
to tell the filters apart). Reports the bit error rate and how many messages arrived without
error.

Results are in docs/PERFORMANCE.md (roll-off). Seeds are fixed.
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from m110c.bits import bytes_to_bits
from m110c.channel.watterson import ChannelSpec, WattersonChannel
from m110c.modulate import Modulator
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.spec import tables as T
from m110c.tx import transmit, transmit_bytes

FS = 9600
ROLLOFFS = (0.10, 0.15, 0.20, 0.25, 0.35)
MODES = [(2400, "short"), (1200, "short"), (600, "short"), (75, "short")]
PAIRS = [(0.15, 0.25), (0.15, 0.15), (0.25, 0.25), (0.35, 0.25), (0.35, 0.35)]  # (TX, RX)


def table_xvi_snr(rate: int) -> float:
    return float(min(r.snr_db for r in T.TABLE_XVI if r.rate == rate and r.fading))


def channel_snr(rate: int, fading: bool, relative_db: float) -> float:
    return table_xvi_snr(rate) + relative_db - (0 if fading else 8)


def modulated(tribits, rolloff, span=8):
    m = Modulator(rolloff=rolloff, span=span)
    return np.concatenate([m.process(tribits), m.flush()])


def bandwidths(x: np.ndarray) -> tuple[float, float]:
    """(99% power bandwidth, -26 dB bandwidth) in Hz, from an averaged periodogram."""
    n = 4096
    window = np.hanning(n)
    psd = np.mean(
        [np.abs(np.fft.fft(x[i : i + n] * window)) ** 2 for i in range(0, x.size - n, n // 2)],
        axis=0,
    )
    f = np.fft.fftfreq(n, 1 / FS)
    order = np.argsort(f)
    f, psd = f[order], psd[order]
    cumulative = np.cumsum(psd) / psd.sum()
    occupied = f[np.searchsorted(cumulative, 0.995)] - f[np.searchsorted(cumulative, 0.005)]
    reference = psd[(f > 600) & (f < 3000)].mean()
    above = f[psd > reference * 10 ** (-26 / 10)]
    return float(occupied), float(above.max() - above.min())


def bandwidth_report() -> None:
    rng = np.random.default_rng(0)
    tribits = transmit_bytes(bytes(rng.integers(0, 256, 6000, dtype=np.uint8)), mode(2400, "short"))
    print("roll-off | 99% power (Hz) | -26 dB (Hz) | -26 dB, span 16 (Hz)")
    for r in ROLLOFFS:
        occupied, width = bandwidths(modulated(tribits, r))
        _, width16 = bandwidths(modulated(tribits, r, span=16))
        print(f"  {r:.2f}   |   {occupied:6.0f}       |  {width:6.0f}     |  {width16:6.0f}")


def ber_job(args):
    rate, interleave, fading, relative_db, tx, rx_rolloff, seed = args
    m = mode(rate, interleave)
    rng = np.random.default_rng(seed)
    msg = bytes(rng.integers(0, 256, max(200, rate * 20 // 8), dtype=np.uint8))
    x = np.concatenate(
        [np.zeros(3000, complex), modulated(transmit(bytes_to_bits(msg), m), tx), np.zeros(FS)]
    )
    x *= np.exp(2j * np.pi * rng.uniform(-100, 100) * np.arange(x.size) / FS)
    snr = channel_snr(rate, fading, relative_db)
    if not fading:
        spec = ChannelSpec(1, 0.0, 0.0, snr)
    elif rate == 75:
        spec = ChannelSpec(2, 5.0, 5.0, snr)
    else:
        spec = ChannelSpec(2, 2.0, 1.0, snr)
    y = WattersonChannel(spec, seed=seed + 5).process(x)
    rx = Receiver(interleave=interleave, rolloff=rx_rolloff)
    out = b"".join(rx.process(y[i : i + FS]) for i in range(0, y.size, FS)) + rx.finish()
    n = min(len(out), len(msg))
    got = np.unpackbits(np.frombuffer(out[:n], np.uint8))
    want = np.unpackbits(np.frombuffer(msg[:n], np.uint8))
    errors = int(np.count_nonzero(got != want)) + 8 * (len(msg) - n)  # missing bytes count
    return args, errors, 8 * len(msg)


def ber_report(results) -> None:
    rows = {}
    for (rate, interleave, fading, rel, tx, rx_rolloff, _), errors, bits in results:
        key = (rate, interleave, fading, rel, tx, rx_rolloff)
        rows.setdefault(key, []).append((errors, bits))
    print("mode    channel   SNR | TX   RX   | BER      | messages without error")
    for (rate, interleave, fading, rel, tx, rx_rolloff), v in sorted(
        rows.items(), key=lambda kv: (-kv[0][0], kv[0][2], kv[0][3], kv[0][4], kv[0][5])
    ):
        errors = sum(e for e, _ in v)
        bits = sum(b for _, b in v)
        clean = sum(1 for e, _ in v if e == 0)
        snr = channel_snr(rate, fading, rel)
        name = str(mode(rate, interleave))
        channel = "fading" if fading else "noise "
        print(
            f"{name:6s}  {channel}  {snr:4.0f} | {tx:.2f} {rx_rolloff:.2f} | "
            f"{errors / bits:.1e}  | {clean}/{len(v)}"
        )


def run(fn, jobs, n_jobs):
    if n_jobs == 1:
        return [fn(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=n_jobs) as pool:
        return list(pool.map(fn, jobs, chunksize=1))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("experiment", choices=("bandwidth", "ber"))
    p.add_argument("--rates", type=lambda s: {int(x) for x in s.split(",")})
    p.add_argument("--seeds", type=int, default=12)
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    args = p.parse_args(argv)
    started = time.time()
    if args.experiment == "bandwidth":
        bandwidth_report()
    else:
        jobs = [
            (rate, interleave, fading, rel, tx, rx_rolloff, 100 * k + 7)
            for rate, interleave in MODES
            if args.rates is None or rate in args.rates
            for fading in (False, True)
            for rel in (-3, 0)
            for tx, rx_rolloff in PAIRS
            for k in range(args.seeds)
        ]
        ber_report(run(ber_job, jobs, args.jobs))
    print(f"({time.time() - started:.0f} s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
