"""What a station reports, as it happens. Every event carries ``time``: seconds of the
device's clock (input samples read / sample rate), not wall-clock time."""

from dataclasses import dataclass, field

from ..rx.receiver import Message


@dataclass
class TxRequest:
    """A message queued for transmission (:meth:`Station.send`)."""

    data: bytes
    mode: object  # ModeSpec
    async_format: object = None  # framing.AsyncFormat, or None for synchronous
    msb_first: bool = False
    at: float | None = None  # device time to start at (None: as soon as possible)
    number: int = 0  # 1, 2, ... in the order queued


@dataclass
class Event:
    time: float


@dataclass
class MessageStarted(Event):
    message: Message


@dataclass
class Received(Event):
    """Bytes of a message, in order, as they are decoded."""

    message: Message
    data: bytes


@dataclass
class MessageEnded(Event):
    message: Message  # its diagnostics are final now (mode, SNR, fading, clock, EOM)


@dataclass
class TxStarted(Event):
    request: TxRequest


@dataclass
class TxEnded(Event):
    request: TxRequest
    seconds: float  # air time, from the first sample played to the last
    complete: bool = True  # False: cut short (the station stopped mid-transmission)
    # The output's peak relative to full scale, and samples beyond it (clipped by the
    # device): the real part on a sound card, both parts on a complex output.
    peak_dbfs: float = float("-inf")
    clipped: int = 0


@dataclass
class Level(Event):
    """The input over the last interval: RMS and peak in dB relative to full scale, and
    samples at full scale (clipping). The station is deaf while it transmits (half
    duplex), so those blocks are not counted."""

    rms_dbfs: float
    peak_dbfs: float
    clipped: int


@dataclass
class Fault(Event):
    """Something the device lost: ``kind`` is "overrun" (input samples dropped because the
    program fell behind) or "underrun" (silence played in the middle of a transmission);
    ``samples`` how many since the last report."""

    kind: str
    samples: int


@dataclass
class Notice(Event):
    text: str
    details: dict = field(default_factory=dict)
