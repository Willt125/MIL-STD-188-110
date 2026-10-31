"""Operating profiles: which settings the programs allow (docs/NETWORK.md, D1 and D7).

``amateur`` (the default) keeps every setting within what amateur rules allow, taking the
US rules as the reference: the obvious way to use the software is then the legal way. Its
limits are listed in :data:`AMATEUR`; a setting beyond them is refused, with a message that
names the limit and the profile that lifts it.

``authorized`` lifts every such limit: the full breadth of settings that 110C (and later the
link stack) offers. It is for stations whose licence or authorisation covers them (MARS,
SHARES, government, commercial or experimental stations, or other countries' rules), and
the programs print :data:`AUTHORIZED_NOTICE` whenever it is used. Checking that a station is
authorised for what it transmits is the operator's job, not the software's.
"""

from dataclasses import dataclass

from .modulate import DEFAULT_ROLLOFF


@dataclass(frozen=True)
class Profile:
    name: str
    max_rolloff: float | None  # None: no limit
    description: str


AMATEUR = Profile(
    "amateur",
    max_rolloff=0.15,  # 2.75 kHz at -26 dB; US amateur HF data: 2.8 kHz (PERFORMANCE.md, roll-off)
    description="settings within amateur limits (US rules as the reference)",
)
AUTHORIZED = Profile(
    "authorized",
    max_rolloff=None,
    description="all limits lifted; for authorised stations only",
)
PROFILES = {p.name: p for p in (AMATEUR, AUTHORIZED)}
DEFAULT = AMATEUR

AUTHORIZED_NOTICE = """\
*** PROFILE "authorized": ALL SAFETIES ARE OFF ***
This profile lifts every limit the default "amateur" profile enforces, and makes the full
breadth of MIL-STD-188-110C's settings (and of the link stack, as it is built) available:
bandwidths, pulse shapes and modes that amateur rules do not allow, among them emissions
wider than 2.8 kHz. Nothing checks that any of it is legal where and how you transmit.
Before transmitting, YOU must make sure that your licence or authorisation covers every
band, frequency, bandwidth, mode and kind of operation you use. The authors accept no
responsibility for its use (see LICENSE: the software is provided "as is", without
warranty of any kind)."""


def get(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown profile {name!r} (choose from: {', '.join(PROFILES)})") from None


def check_rolloff(profile: Profile, rolloff: float | None) -> float:
    """The transmit roll-off to use: ``rolloff``, or the default when None. Raises ValueError
    if the profile does not allow it."""
    if rolloff is None:
        return DEFAULT_ROLLOFF
    if profile.max_rolloff is not None and rolloff > profile.max_rolloff:
        raise ValueError(
            f"roll-off {rolloff:g} is wider than the {profile.name} profile allows "
            f"(at most {profile.max_rolloff:g}: 2.8 kHz at -26 dB, the US amateur limit for HF "
            "data); --profile authorized lifts the limit, for authorised stations only"
        )
    return rolloff


def check_transmit(profile: Profile, callsign: str | None, on_air: bool) -> None:
    """Raise ValueError if ``profile`` does not allow a transmission: in the amateur profile,
    nothing goes on the air without the station's callsign configured (D1). Files and
    virtual devices put nothing on the air and need none.

    Configuring the callsign does not identify the transmissions themselves: until the link
    layer sends it in every transmission (PLAN.md P5), raw messages from the modem program
    must carry their identification in their content, as the operator writes it."""
    if not on_air or profile is not AMATEUR:
        return
    if not callsign or not valid_callsign(callsign):
        raise ValueError(
            "the amateur profile transmits on the air only with the station's callsign "
            "configured (--callsign); it is the station's identification (docs/NETWORK.md D1)"
        )


def valid_callsign(callsign: str) -> bool:
    """Shaped like an amateur callsign: 3-7 letters and digits with at least one of each
    (an optional -SSID or /suffix aside). A sanity check against typos, not a lookup."""
    base = callsign.upper().split("-")[0].split("/")[0]
    return (
        3 <= len(base) <= 7
        and base.isalnum()
        and any(c.isdigit() for c in base)
        and any(c.isalpha() for c in base)
    )


SDR_TX_MODES = ("off", "bench", "filtered")
SDR_NOTICE = """\
*** SDR transmitter: output cap lifted ("filtered") ***
An SDR has no output filter: its harmonics and spurious emissions are only as low as the
filter (and any amplifier) after it makes them. You have stated that one is fitted. Make
sure the station meets the spurious-emission limits that apply to you before it reaches an
antenna."""


def check_sdr_transmit(tx_mode: str, tx_gain: float | None) -> None:
    """An SDR transmits only as a bench device ("bench": its lowest gain, a reduced digital
    level, for coax and attenuators; docs/ONAIR.md bench D) unless the operator states that
    a filter follows it ("filtered", which prints SDR_NOTICE), or not at all ("off").

    This is a limit of the hardware, not of a service, so it holds in every profile: an
    SDR's output is unfiltered whoever operates it. Raises ValueError for an unknown mode,
    or a gain asked for while capped."""
    if tx_mode not in SDR_TX_MODES:
        raise ValueError(f"SDR transmit mode must be one of {', '.join(SDR_TX_MODES)}")
    if tx_mode == "bench" and tx_gain is not None:
        raise ValueError(
            "an SDR transmits at its lowest gain unless a filter follows it: set the transmit "
            'mode to "filtered" to choose a gain (after fitting one; docs/ONAIR.md)'
        )
