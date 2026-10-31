"""``m110c-modem``: a station on a sound card, or on files (PLAN.md P1).

It receives continuously, writing what it decodes to stdout (or ``--received``), and
transmits what it is given: files (``--send``), or each line typed (``--chat``). Diagnostics
go to stderr: each message as it starts and ends, each transmission, the input level, and
anything the device lost. ::

    m110c-modem --list-audio
    m110c-modem --audio --callsign N0CALL --chat          # type to transmit; Ctrl-C to stop
    m110c-modem --audio "USB Audio" --rate 600 --send report.txt --callsign N0CALL
    m110c-modem --input recording.cf32                     # like m110c-rx, through the station
    m110c-modem --send msg.txt --output tx.cf32 --idle skip   # like m110c-tx
    m110c-modem --sdr driver=rtlsdr --dial 14.1e6 --sdr-tx off    # an SDR, receive only

Settings come from the built-in defaults, then a TOML file (``--config``), then the command
line. The file's sections and keys::

    [station]   callsign, profile, rolloff, carrier, full_duplex
    [audio]     device, sample_rate, input_channel
    [sdr]       args, dial, rate, if, rx_gain, tx_gain, tx_mode, ppm
    [transmit]  rate, interleave, drive_db, tx_ppm
    [receive]   interleave, late_entry

On the air, the amateur profile (the default) transmits only with a callsign configured;
the transmissions themselves are not identified until the link layer (PLAN.md P5), so the
operator's messages must carry it (docs/NETWORK.md D1).
"""

import argparse
import contextlib
import sys
import threading
import tomllib

from . import io, profile
from .cli import _describe, _describe_start, _open_in, _open_out
from .spec.modes import INTERLEAVE_SETTINGS
from .station import Fault, Level, MessageEnded, MessageStarted, Received, TxEnded, TxStarted

PROG = "m110c-modem"
# Every setting: (section in the config file, key, built-in default).
SETTINGS = {
    "callsign": ("station", "callsign", None),
    "profile": ("station", "profile", "amateur"),
    "rolloff": ("station", "rolloff", None),
    "carrier": ("station", "carrier", 1800.0),
    "full_duplex": ("station", "full_duplex", False),
    "audio": ("audio", "device", None),
    "sample_rate": ("audio", "sample_rate", None),
    "input_channel": ("audio", "input_channel", 0),
    "sdr": ("sdr", "args", None),
    "dial": ("sdr", "dial", None),
    "sdr_rate": ("sdr", "rate", 240000),
    "if_hz": ("sdr", "if", None),
    "rx_gain": ("sdr", "rx_gain", None),
    "tx_gain": ("sdr", "tx_gain", None),
    "sdr_tx": ("sdr", "tx_mode", "bench"),
    "sdr_ppm": ("sdr", "ppm", 0.0),
    "rate": ("transmit", "rate", 1200),
    "tx_interleave": ("transmit", "interleave", "short"),
    "drive_db": ("transmit", "drive_db", 0.0),
    "tx_ppm": ("transmit", "tx_ppm", 0.0),
    "interleave": ("receive", "interleave", "auto"),
    "late_entry": ("receive", "late_entry", False),
}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description="A MIL-STD-188-110C station: receive continuously, transmit on request.",
    )
    p.add_argument("--config", help="a TOML file of settings (see the module's help)")
    d = p.add_argument_group("device (a sound card, an SDR, or files)")
    d.add_argument(
        "--audio",
        nargs="?",
        const="",
        default=None,
        help="use a sound card: a name or index from --list-audio (none: the default card)",
    )
    d.add_argument("--list-audio", action="store_true", help="list sound devices and stop")
    d.add_argument("--input", help="read samples from a file ('-': stdin) instead")
    d.add_argument("--output", help="write the output timeline to a file ('-': stdout)")
    d.add_argument("-f", "--format", default="cf32", choices=io.FORMATS, help="file format")
    d.add_argument(
        "--idle",
        choices=("silence", "skip"),
        default="silence",
        help="the output file: the whole timeline, or the transmissions only",
    )
    d.add_argument(
        "-s",
        "--sample-rate",
        type=int,
        help="samples per second (sound card default 48000, files 9600)",
    )
    d.add_argument("--input-channel", type=int, help="the sound card's input channel (0)")
    d.add_argument(
        "--sdr",
        metavar="ARGS",
        help='use an SDR through SoapySDR, e.g. "driver=lime" or "driver=rtlsdr"',
    )
    d.add_argument("--list-sdr", action="store_true", help="list the SDRs found and stop")
    d.add_argument(
        "--dial",
        type=float,
        metavar="HZ",
        help="SDR: the dial frequency (upper sideband; the signal is 300-3300 Hz above)",
    )
    d.add_argument("--sdr-rate", type=int, help="SDR: its sample rate, a multiple of 9600 (240000)")
    d.add_argument(
        "--if", dest="if_hz", type=float, help="SDR: tuning offset, Hz (a quarter of the rate)"
    )
    d.add_argument("--rx-gain", type=float, help="SDR: receive gain, dB (default automatic)")
    d.add_argument(
        "--tx-gain", type=float, help="SDR: transmit gain, dB (only with --sdr-tx filtered)"
    )
    d.add_argument(
        "--sdr-tx",
        choices=profile.SDR_TX_MODES,
        help="SDR transmitter: off, bench (lowest gain, the default), or filtered "
        "(a filter follows it: gain as set)",
    )
    d.add_argument("--sdr-ppm", type=float, help="SDR: its frequency error, ppm (0)")
    s = p.add_argument_group("station")
    s.add_argument("--callsign", help="the station's callsign (needed to transmit on the air)")
    s.add_argument(
        "--profile",
        choices=list(profile.PROFILES),
        help="operating profile (default amateur; docs/NETWORK.md D7)",
    )
    s.add_argument("--rolloff", type=float, help="pulse roll-off, both directions (0.15)")
    s.add_argument("--carrier", type=float, help="carrier frequency, Hz (1800)")
    s.add_argument(
        "--full-duplex",
        action="store_true",
        default=None,
        help="keep receiving while transmitting (a loopback cable)",
    )
    t = p.add_argument_group("transmit")
    t.add_argument(
        "--send",
        action="append",
        default=[],
        metavar="FILE",
        help="send a file as one message (repeatable)",
    )
    t.add_argument("--chat", action="store_true", help="send each line typed on stdin as a message")
    t.add_argument("-r", "--rate", type=int, help="data rate, bps (1200)")
    t.add_argument("--tx-interleave", choices=INTERLEAVE_SETTINGS, help="short (default)")
    t.add_argument(
        "--drive-db",
        type=float,
        help="output level relative to the default, dB (0; above about +2 clips)",
    )
    t.add_argument(
        "--tx-ppm", type=float, help="the output clock's error, ppm, positive if it runs fast (0)"
    )
    r = p.add_argument_group("receive")
    r.add_argument("--received", default="-", help="where decoded data goes ('-': stdout)")
    r.add_argument(
        "--interleave",
        choices=("auto", "short", "zero"),
        help="zero or short interleave: told from the data (auto) or assumed",
    )
    r.add_argument(
        "--late-entry",
        action="store_true",
        default=None,
        help="also join transmissions already in progress",
    )
    o = p.add_argument_group("running")
    o.add_argument(
        "--duration", type=float, help="stop after this many seconds of the device's time"
    )
    o.add_argument(
        "--levels",
        type=float,
        default=None,
        metavar="SECONDS",
        help="report the input level this often (sound card default 10; 0: off)",
    )
    o.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="no diagnostics (the authorized notice still prints)",
    )
    return p


def settings(args) -> dict:
    """Defaults, then the config file, then the command line."""
    merged = {name: default for name, (_, _, default) in SETTINGS.items()}
    if args.config:
        with open(args.config, "rb") as f:
            config = tomllib.load(f)
        known = {(section, key) for section, key, _ in SETTINGS.values()}
        for section, values in config.items():
            for key in values:
                if (section, key) not in known:
                    raise ValueError(f"{args.config}: unknown setting [{section}] {key}")
        for name, (section, key, _) in SETTINGS.items():
            if key in config.get(section, {}):
                merged[name] = config[section][key]
    for name in SETTINGS:
        value = getattr(args, name, None)
        if value is not None and not (name == "audio" and value == ""):
            merged[name] = value
    if args.audio is not None and merged["audio"] is None:
        merged["audio"] = ""  # --audio alone: the default card
    return merged


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.list_sdr:
        from .audio.sdr import list_sdrs

        try:
            found = list_sdrs()
        except RuntimeError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 1
        for found_args in found:
            print(", ".join(f"{k}={v}" for k, v in found_args.items()))
        if not found:
            print(f"{PROG}: no SDR found", file=sys.stderr)
        return 0
    if args.list_audio:
        from .audio.soundcard import list_devices

        try:
            print(list_devices())
        except RuntimeError as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        cfg = settings(args)
        chosen = profile.get(cfg["profile"])
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    if chosen is profile.AUTHORIZED:
        print(profile.AUTHORIZED_NOTICE, file=sys.stderr)  # always, even with --quiet
    devices = [cfg["audio"] is not None, cfg["sdr"] is not None, bool(args.input or args.output)]
    if sum(devices) > 1:
        print(f"{PROG}: one device: a sound card, an SDR or files", file=sys.stderr)
        return 2
    if cfg["sdr"] is not None and cfg["dial"] is None:
        print(f"{PROG}: an SDR needs the dial frequency (--dial)", file=sys.stderr)
        return 2
    if cfg["sdr"] is not None and cfg["sdr_tx"] == "filtered":
        print(profile.SDR_NOTICE, file=sys.stderr)  # always, like the authorized notice
    with contextlib.ExitStack() as stack:
        try:
            device = _open_device(args, cfg, stack)
            station = _station(device, cfg, chosen)
            for path in args.send:
                with open(path, "rb") as f:
                    station.send(f.read(), rate=cfg["rate"], interleave=cfg["tx_interleave"])
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 2
        sink = stack.enter_context(_open_out(args.received))
        report = Reporter(station, sink, args.quiet)
        station.on(report)
        if args.chat:
            _chat(station, cfg, report)
        levels = args.levels if args.levels is not None else (0 if devices[2] else 10.0)
        report.level_every = levels
        files_only = cfg["audio"] is None and cfg["sdr"] is None and args.input is None
        try:
            station.run(until=args.duration, until_idle=files_only and not args.chat)
        except KeyboardInterrupt:
            pass
        except RuntimeError as exc:  # e.g. a sound card that stopped delivering input
            print(f"{PROG}: {exc}", file=sys.stderr)
            return 1
    return 0


def _open_device(args, cfg, stack):
    if cfg["sdr"] is not None:
        from .audio.sdr import SdrDevice

        device = SdrDevice(
            cfg["sdr"],
            dial_hz=cfg["dial"],
            sdr_rate=cfg["sdr_rate"],
            if_hz=cfg["if_hz"],
            rx_gain=cfg["rx_gain"],
            tx_gain=cfg["tx_gain"],
            tx_mode=cfg["sdr_tx"],
            ppm=cfg["sdr_ppm"],
        )
        stack.callback(device.close)
        return device
    if cfg["audio"] is not None:
        from .audio.soundcard import SoundCard

        name = cfg["audio"]
        device = SoundCard(
            device=int(name) if str(name).isdigit() else (name or None),
            sample_rate=cfg["sample_rate"] or 48000,
            input_channel=cfg["input_channel"],
        )
        stack.callback(device.close)
        return device
    from .audio import FileDevice

    rate = cfg["sample_rate"] or 9600
    infile = stack.enter_context(_open_in(args.input)) if args.input else None
    channels = 1
    if infile is not None and args.format == "wav":
        rate, channels = io.read_wav_header(infile)
    outfile = stack.enter_context(_open_out(args.output)) if args.output else None
    device = FileDevice(
        infile, outfile, args.format, sample_rate=rate, channels=channels, idle=args.idle
    )
    stack.callback(device.close)
    return device


def _station(device, cfg, chosen):
    from .station import Station

    return Station(
        device,
        profile=chosen,
        callsign=cfg["callsign"],
        rolloff=cfg["rolloff"],
        carrier_hz=cfg["carrier"],
        drive_db=cfg["drive_db"],
        tx_ppm=cfg["tx_ppm"],
        full_duplex=cfg["full_duplex"],
        receiver_options={"interleave": cfg["interleave"], "late_entry": cfg["late_entry"]},
    )


def _chat(station, cfg, report) -> None:
    """Each line typed is sent as one message (in a thread: input blocks)."""

    def read_lines():
        for line in sys.stdin.buffer:
            try:
                station.send(line, rate=cfg["rate"], interleave=cfg["tx_interleave"])
            except ValueError as exc:
                report.say(str(exc))

    threading.Thread(target=read_lines, daemon=True).start()


class Reporter:
    """Turns the station's events into stderr lines and received data into the sink."""

    def __init__(self, station, sink, quiet: bool):
        self.rate = station.receiver.sample_rate  # positions in reports count at this rate
        self.sink, self.quiet = sink, quiet
        self.level_every = 0.0
        self._last_level = float("-inf")

    def say(self, text: str) -> None:
        if not self.quiet:
            print(f"{PROG}: {text}", file=sys.stderr, flush=True)

    def __call__(self, e) -> None:
        if isinstance(e, Received):
            self.sink.write(e.data)
            self.sink.flush()
        elif self.quiet:
            return
        elif isinstance(e, MessageStarted):
            print(_describe_start(e.message, self.rate, PROG), file=sys.stderr, flush=True)
        elif isinstance(e, MessageEnded):
            print(_describe(e.message, self.rate, PROG), file=sys.stderr, flush=True)
        elif isinstance(e, TxStarted):
            r = e.request
            self.say(f"transmitting {r.mode}, {len(r.data)} bytes (message {r.number})")
        elif isinstance(e, TxEnded):
            cut = "" if e.complete else ", cut short"
            clip = f", {e.clipped} samples clipped: lower --drive-db" if e.clipped else ""
            self.say(
                f"sent message {e.request.number}: {e.seconds:.1f} s, "
                f"peak {e.peak_dbfs:.1f} dBFS{clip}{cut}"
            )
        elif isinstance(e, Fault):
            what = {
                "overrun": "input lost (the program fell behind)",
                "underrun": "silence in a transmission (the program fell behind)",
            }
            self.say(f"{what.get(e.kind, e.kind)}: {e.samples} samples at {e.time:.1f} s")
        elif isinstance(e, Level):
            if self.level_every and e.time - self._last_level >= self.level_every:
                self._last_level = e.time
                clip = f", {e.clipped} samples at full scale" if e.clipped else ""
                self.say(f"input level {e.rms_dbfs:.1f} dBFS RMS, peak {e.peak_dbfs:.1f}{clip}")


def main_modem() -> None:  # console-script entry point
    from .cli import _run

    _run(main)


if __name__ == "__main__":
    main_modem()
