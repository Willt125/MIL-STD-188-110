"""Command-line tools.

``m110c-tx``: data in (file or stdin), samples out (file or stdout)::

    m110c-tx --rate 2400 --interleave long message.txt -o signal.cf32
    cat message.txt | m110c-tx --format wav > signal.wav
    m110c-tx --async 8N1 --format s16 < message.txt | aplay -f S16_LE -r 9600

The whole chain streams: input is read in pieces as it arrives, and samples are written as
soon as they are final, so the tool works in a pipe. The output depends only on the input
bytes, never on how a pipe happened to deliver them. Diagnostics go to stderr.
"""

import argparse
import contextlib
import os
import sys

from . import framing, io
from .modulate import Modulator
from .spec import tables as T
from .spec.modes import INTERLEAVE_SETTINGS, mode
from .tx import Transmitter

READ_SIZE = 1 << 12  # small reads keep peak memory low; output does not depend on it


def _open_in(path: str):
    """A context manager for the input. Standard input is used but never closed."""
    return contextlib.nullcontext(sys.stdin.buffer) if path == "-" else open(path, "rb")


def _open_out(path: str):
    """A context manager for the output. Standard output is used but never closed."""
    return contextlib.nullcontext(sys.stdout.buffer) if path == "-" else open(path, "wb")


def _read_pieces(stream):
    """Yield the input as it becomes available (a short read does not mean end of file)."""
    read = getattr(stream, "read1", stream.read)
    while True:
        piece = read(READ_SIZE)
        if not piece:
            return
        yield piece


def tx_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m110c-tx",
        description="MIL-STD-188-110C serial-tone transmitter: data in, baseband samples out.",
    )
    p.add_argument("input", nargs="?", default="-", help="input file (default: stdin)")
    p.add_argument("-o", "--output", default="-", help="output file (default: stdout)")
    p.add_argument(
        "-r",
        "--rate",
        type=int,
        default=2400,
        choices=T.DATA_RATES,
        help="user bit rate (default 2400)",
    )
    p.add_argument(
        "-i",
        "--interleave",
        default="short",
        choices=INTERLEAVE_SETTINGS,
        help="interleaver: zero, short (0.6 s), long (4.8 s); ignored at 4800 bps (default short)",
    )
    p.add_argument(
        "-f", "--format", default="cf32", choices=io.FORMATS, help="sample format (default cf32)"
    )
    p.add_argument(
        "-s",
        "--sample-rate",
        type=int,
        default=9600,
        help="samples per second, a multiple of 2400 (default 9600)",
    )
    p.add_argument(
        "--carrier",
        type=float,
        default=T.CARRIER_HZ,
        help="carrier frequency in Hz; 0 = centred baseband (default 1800)",
    )
    p.add_argument(
        "--rolloff", type=float, default=0.25, help="root-raised-cosine roll-off (default 0.25)"
    )
    framing_group = p.add_mutually_exclusive_group()
    framing_group.add_argument(
        "--async",
        dest="async_format",
        metavar="FORMAT",
        type=framing.AsyncFormat.parse,
        help="send asynchronous characters, e.g. 8N1 or 7E1 (Appendix A send-all mode)",
    )
    framing_group.add_argument(
        "--msb-first", action="store_true", help="synchronous bytes, MSB first (default LSB first)"
    )
    p.add_argument("-q", "--quiet", action="store_true", help="no summary on stderr")
    return p


def tx_main(argv=None) -> int:
    args = tx_parser().parse_args(argv)
    m = mode(args.rate, args.interleave)
    try:
        modulator = Modulator(
            sample_rate=args.sample_rate, carrier_hz=args.carrier, rolloff=args.rolloff
        )
    except ValueError as exc:
        print(f"m110c-tx: {exc}", file=sys.stderr)
        return 2
    if args.format in ("s16", "wav") and args.carrier == 0:
        print(
            "m110c-tx: real output with --carrier 0 discards the quadrature component",
            file=sys.stderr,
        )

    tx = Transmitter(m)
    n_bytes = 0
    try:
        source = _open_in(args.input)
    except OSError as exc:
        print(f"m110c-tx: {exc}", file=sys.stderr)
        return 1
    with source as infile, _open_out(args.output) as sink:
        writer = io.SampleWriter(sink, args.format, args.sample_rate)
        for piece in _read_pieces(infile):
            n_bytes += len(piece)
            if args.async_format is not None:
                user_bits = framing.async_encode(piece, args.async_format)
            else:
                user_bits = framing.sync_encode(piece, msb_first=args.msb_first)
            # Feed about one second of signal at a time. At 75 bps one input byte becomes
            # over a thousand samples, so a whole read at once would cost memory in
            # proportion to the read size; per-second steps keep it constant at every rate.
            step = m.rate
            for i in range(0, user_bits.size, step):
                writer.write(modulator.process(tx.process(user_bits[i : i + step])))
        writer.write(modulator.process(tx.finish()))
        writer.write(modulator.flush())
        writer.close()

    if not args.quiet:
        seconds = writer.samples_written / args.sample_rate
        framing_note = f"async {args.async_format}" if args.async_format else "sync"
        print(
            f"m110c-tx: {m} ({framing_note}), {n_bytes} bytes -> {writer.samples_written} samples "
            f"({seconds:.2f} s at {args.sample_rate} Hz, {args.format})",
            file=sys.stderr,
        )
    if writer.clipped:
        print(f"m110c-tx: warning: {writer.clipped} samples clipped", file=sys.stderr)
    return 0


EXIT_BROKEN_PIPE = 128 + 13  # what a shell reports for a process killed by SIGPIPE


def main_tx() -> None:  # console-script entry point
    try:
        sys.exit(tx_main())
    except BrokenPipeError:
        # The reader went away (e.g. "m110c-tx | head"). Stop quietly, as Unix tools do.
        # Point stdout at /dev/null so the interpreter's final flush cannot fail again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
        sys.exit(EXIT_BROKEN_PIPE)


if __name__ == "__main__":
    main_tx()
