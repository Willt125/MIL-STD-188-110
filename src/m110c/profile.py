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
