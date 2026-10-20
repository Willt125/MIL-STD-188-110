"""``m110c-modem`` (PLAN.md P1): on files, and on a fake sound card."""

import sys
import threading
import time
import types

import numpy as np
import pytest

from m110c.cli import tx_main
from m110c.modem import main
from m110c.modulate import modulate
from m110c.spec import mode
from m110c.tx import transmit_bytes

TEXT = b"m110c-modem receives this. " * 4


def cf32_file(path, x):
    data = np.empty(2 * x.size, "<f4")
    data[0::2], data[1::2] = x.real, x.imag
    path.write_bytes(data.tobytes())


@pytest.fixture
def recording(tmp_path):
    x = modulate(transmit_bytes(TEXT, mode(1200, "short")))
    x = np.concatenate([np.zeros(3000, complex), x, np.zeros(9600 * 2, complex)])
    path = tmp_path / "in.cf32"
    cf32_file(path, x)
    return path


def test_receiving_from_a_file(tmp_path, recording, capsys):
    out = tmp_path / "out.bin"
    assert main(["--input", str(recording), "--received", str(out)]) == 0
    assert out.read_bytes() == TEXT
    err = capsys.readouterr().err
    assert "m110c-modem: 1200 bps, zero or short interleave, preamble at" in err
    assert "m110c-modem: 1200S at" in err and "EOM found" in err


def test_sending_to_a_file_is_what_m110c_tx_writes(tmp_path):
    src = tmp_path / "msg.txt"
    src.write_bytes(TEXT)
    reference = tmp_path / "ref.cf32"
    assert tx_main([str(src), "-o", str(reference), "-r", "600", "-q"]) == 0
    out = tmp_path / "out.cf32"
    code = main(["--send", str(src), "-r", "600", "--output", str(out), "--idle", "skip", "-q"])
    assert code == 0 and out.read_bytes() == reference.read_bytes()


def test_the_config_file_and_the_command_line(tmp_path):
    src = tmp_path / "msg.txt"
    src.write_bytes(b"config")
    config = tmp_path / "modem.toml"
    config.write_text('[transmit]\nrate = 2400\ninterleave = "zero"\n[station]\ncarrier = 1800\n')
    out = tmp_path / "a.cf32"
    main(
        ["--config", str(config), "--send", str(src), "--output", str(out), "--idle", "skip", "-q"]
    )
    ref = tmp_path / "ref.cf32"
    tx_main([str(src), "-o", str(ref), "-r", "2400", "-i", "zero", "-q"])
    assert out.read_bytes() == ref.read_bytes()
    # The command line wins over the file.
    main(
        [
            "--config",
            str(config),
            "-r",
            "150",
            "--send",
            str(src),
            "--output",
            str(out),
            "--idle",
            "skip",
            "-q",
        ]
    )
    tx_main([str(src), "-o", str(ref), "-r", "150", "-i", "zero", "-q"])
    assert out.read_bytes() == ref.read_bytes()


def test_a_config_file_with_an_unknown_setting(tmp_path, capsys):
    config = tmp_path / "modem.toml"
    config.write_text("[station]\ncalsign = 'N0CALL'\n")
    assert main(["--config", str(config)]) == 2
    assert "unknown setting [station] calsign" in capsys.readouterr().err


def test_profiles(tmp_path, capsys):
    src = tmp_path / "msg.txt"
    src.write_bytes(b"x")
    out = str(tmp_path / "o.cf32")
    assert main(["--rolloff", "0.35", "--send", str(src), "--output", out, "-q"]) == 2
    assert "authorized" in capsys.readouterr().err
    args = ["--profile", "authorized", "--rolloff", "0.35", "--send", str(src), "--output", out]
    assert main([*args, "-q"]) == 0
    assert "ALL SAFETIES ARE OFF" in capsys.readouterr().err


# --- on a (fake) sound card ---------------------------------------------------------------


class FeedingStream:
    """A fake sounddevice.Stream that runs: from start() to stop() its thread calls the
    callback with blocks of silence, about ten times faster than real time."""

    played: list = []

    def __init__(self, samplerate, blocksize, device, channels, dtype, latency, callback):
        self.callback, self.channels = callback, channels
        self._run = threading.Event()

    def start(self):
        self._run.set()
        threading.Thread(target=self._feed, daemon=True).start()

    def _feed(self):
        status = types.SimpleNamespace(input_overflow=False, output_underflow=False)
        while self._run.is_set():
            out = np.zeros((480, self.channels[1]), np.float32)
            self.callback(np.zeros((480, self.channels[0]), np.float32), out, 480, None, status)
            FeedingStream.played.append(out[:, 0].copy())
            time.sleep(0.005)

    def stop(self):
        self._run.clear()

    def close(self):
        pass


@pytest.fixture
def fake_card(monkeypatch):
    module = types.ModuleType("sounddevice")
    module.Stream = FeedingStream
    module.query_devices = lambda device=None, kind=None: (
        {"max_input_channels": 1, "max_output_channels": 2} if kind else "0 Fake card"
    )
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    FeedingStream.played = []


def test_list_audio(fake_card, capsys):
    assert main(["--list-audio"]) == 0
    assert "Fake card" in capsys.readouterr().out


def test_on_the_air_without_a_callsign(fake_card, tmp_path, capsys):
    src = tmp_path / "msg.txt"
    src.write_bytes(b"x")
    assert main(["--audio", "--send", str(src), "--duration", "0.5"]) == 2
    assert "callsign" in capsys.readouterr().err


def test_sending_through_the_sound_card(fake_card, tmp_path, capsys):
    src = tmp_path / "msg.txt"
    src.write_bytes(TEXT)
    args = ["--audio", "--callsign", "N0CALL", "--send", str(src), "-s", "9600", "-r", "2400"]
    assert main([*args, "--duration", "4"]) == 0
    err = capsys.readouterr().err
    assert "transmitting 2400S" in err and "sent message 1" in err
    assert "lost" not in err and "silence" not in err
    played = np.concatenate(FeedingStream.played)
    expected = modulate(transmit_bytes(TEXT, mode(2400, "short"))).real
    start = np.flatnonzero(np.abs(played) > 1e-6)[0]
    assert np.allclose(played[start : start + expected.size], expected, atol=1e-6)


# --- on a (fake) SDR -------------------------------------------------------------------------


@pytest.fixture
def fake_soapy(monkeypatch):
    class Quiet:
        """A SoapySDR.Device whose receive stream is silence, a little faster than real time."""

        def __init__(self, args):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: None

        def hasGainMode(self, *a):
            return False

        def getGainRange(self, *a):
            return types.SimpleNamespace(minimum=lambda: 0.0)

        def readStream(self, stream, buffers, n, timeoutUs=0):
            time.sleep(0.005)
            buffers[0][:n] = 0
            return types.SimpleNamespace(ret=n)

        def writeStream(self, stream, buffers, n, timeoutUs=0):
            return types.SimpleNamespace(ret=n)

    module = types.ModuleType("SoapySDR")
    module.Device = Quiet
    module.Device.enumerate = staticmethod(lambda: [{"driver": "lime", "serial": "123"}])
    module.SOAPY_SDR_RX, module.SOAPY_SDR_TX, module.SOAPY_SDR_CF32 = 1, 0, "CF32"
    monkeypatch.setitem(sys.modules, "SoapySDR", module)


def test_list_sdr(fake_soapy, capsys):
    assert main(["--list-sdr"]) == 0
    assert "driver=lime, serial=123" in capsys.readouterr().out


def test_sdr_settings(fake_soapy, capsys):
    assert main(["--sdr", "driver=lime"]) == 2
    assert "--dial" in capsys.readouterr().err
    assert main(["--sdr", "driver=lime", "--dial", "915e6", "--tx-gain", "30"]) == 2
    assert "filtered" in capsys.readouterr().err  # a gain needs a filter
    args = ["--sdr", "driver=lime", "--dial", "915e6", "--sdr-tx", "filtered", "--tx-gain", "30"]
    assert main([*args, "--duration", "0.3", "-q"]) == 0
    assert "output cap lifted" in capsys.readouterr().err  # printed even with --quiet
    assert main(["--sdr", "x", "--audio", "--dial", "1e6"]) == 2
    assert "one device" in capsys.readouterr().err
