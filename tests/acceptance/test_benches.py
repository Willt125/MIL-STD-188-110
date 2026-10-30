"""Bench tests on real hardware (docs/ONAIR.md), skipped unless their environment variable
says the hardware is connected. Nothing here reaches an antenna: the paths are cables.

``M110C_AUDIO_LOOPBACK``: an audio interface whose output is cabled back to its own input
(the UMC22 from a main output to input 1, Direct Monitor off; or a Digirig from its
transmit audio to its receive audio), named as ``m110c-modem --list-audio`` shows it
("default" for the system's default). Every mode is sent and received full duplex
(bench C).

``M110C_SDR_LOOP``: two SoapySDR argument strings, the transmitter's and the receiver's,
separated by ";" (e.g. "driver=lime;driver=rtlsdr"), joined by coax with at least 40 dB of
attenuation, and ``M110C_SDR_DIAL`` the dial frequency (default 915e6) (bench D).
"""

import os
import threading

import pytest

from m110c.station import MessageEnded, Received, Station

AUDIO = os.environ.get("M110C_AUDIO_LOOPBACK")
SDRS = os.environ.get("M110C_SDR_LOOP")
MODES = [(2400, "short"), (1200, "short"), (600, "short"), (150, "short"), (75, "short")]
TEXT = b"Bench test: the quick brown fox jumps over the lazy dog 0123456789.\n"


def exchange(station, rate, interleave, seconds):
    log = []
    station.on(log.append)
    station.send(TEXT, rate=rate, interleave=interleave)
    stopper = threading.Timer(seconds, station.stop)
    stopper.start()
    station.run()
    stopper.cancel()
    received = b"".join(e.data for e in log if isinstance(e, Received))
    ended = [e.message for e in log if isinstance(e, MessageEnded)]
    return received, ended


@pytest.mark.skipif(not AUDIO, reason="M110C_AUDIO_LOOPBACK not set")
@pytest.mark.parametrize(("rate", "interleave"), MODES)
def test_audio_loopback(rate, interleave):
    from m110c.audio.soundcard import SoundCard

    device = SoundCard(None if AUDIO == "default" else AUDIO, sample_rate=48000)
    try:
        station = Station(device, callsign="N0CALL", full_duplex=True)
        seconds = 4 + 1.5 * len(TEXT) * 8 / rate
        received, ended = exchange(station, rate, interleave, seconds)
    finally:
        device.close()
    assert received == TEXT
    assert ended and ended[0].eom_found
    print(
        f"\n{rate}{interleave[0].upper()}: SNR {ended[0].snr_db:.1f} dB, "
        f"clock {ended[0].clock_ppm:+.0f} ppm (one card: about 0)"
    )


@pytest.mark.skipif(not SDRS, reason="M110C_SDR_LOOP not set")
@pytest.mark.parametrize(("rate", "interleave"), MODES[:3])
def test_sdr_loop(rate, interleave):
    """The transmitter's station sends; a second station on the receiving SDR hears it,
    the two clocks independent (the RTL-SDR's error shows in the reported offsets)."""
    from m110c.audio.sdr import SdrDevice

    tx_args, rx_args = SDRS.split(";")
    dial = float(os.environ.get("M110C_SDR_DIAL", "915e6"))
    sender = SdrDevice(tx_args, dial_hz=dial, sdr_rate=960000, tx_mode="bench")
    listener = SdrDevice(rx_args, dial_hz=dial, sdr_rate=240000, tx_mode="off")
    try:
        receiving = Station(listener)
        log = []
        receiving.on(log.append)
        thread = threading.Thread(target=receiving.run, daemon=True)
        thread.start()
        seconds = 4 + 1.5 * len(TEXT) * 8 / rate
        exchange(Station(sender, callsign="N0CALL"), rate, interleave, seconds)
        receiving.stop()
        thread.join()
    finally:
        sender.close()
        listener.close()
    assert b"".join(e.data for e in log if isinstance(e, Received)) == TEXT
    ended = [e.message for e in log if isinstance(e, MessageEnded)]
    print(
        f"\n{rate}{interleave[0].upper()}: carrier {ended[0].detection.carrier_hz:.1f} Hz, "
        f"clock {ended[0].clock_ppm:+.0f} ppm, SNR {ended[0].snr_db:.1f} dB"
    )
