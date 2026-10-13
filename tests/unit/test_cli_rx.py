"""Tests for the m110c-rx command (M8), run as real subprocesses."""

import struct
import subprocess
import sys

import numpy as np
import pytest

from m110c import io
from m110c.modulate import modulate
from m110c.spec import mode
from m110c.tx import transmit_bytes

MESSAGE = b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789\r\n" * 3
CLI = [sys.executable, "-m", "m110c.cli"]


def run(args, stdin=b""):
    return subprocess.run(args, input=stdin, capture_output=True, timeout=300)


def test_tx_piped_into_rx_through_real_os_pipes():
    tx = subprocess.Popen(
        [*CLI, "-q", "-r", "600", "-i", "long"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    rx = subprocess.Popen(
        [*CLI, "rx"], stdin=tx.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    tx.stdout.close()  # so rx sees end of input when tx exits
    tx.stdin.write(MESSAGE)
    tx.stdin.close()
    out, err = rx.communicate(timeout=300)
    assert tx.wait(timeout=60) == 0 and rx.returncode == 0
    assert out == MESSAGE
    assert b"600L" in err and b"EOM found" in err


def test_async_round_trip(tmp_path):
    sig = tmp_path / "s.cf32"
    assert run([*CLI, "-q", "--async", "8N1", "-o", str(sig)], MESSAGE).returncode == 0
    out = run([*CLI, "rx", "-q", "--async", "8N1", str(sig)])
    assert out.returncode == 0 and out.stdout == MESSAGE


def wav_bytes(samples, rate, channels=1, extra_chunk=False):
    """A 16-bit PCM WAV built by hand (independent of io.SampleWriter)."""
    pcm = np.clip(np.round(samples * 32767), -32767, 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm, 2)
    data = pcm.tobytes()
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * 2 * channels, 2 * channels, 16)
    chunks = b"fmt " + struct.pack("<I", 16) + fmt
    if extra_chunk:
        chunks += b"LIST" + struct.pack("<I", 5) + b"hello" + b"\x00"  # odd size, padded
    chunks += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def test_wav_input_stereo_with_extra_chunks(tmp_path):
    """Real recordings are often stereo and carry LIST chunks before the data."""
    x = modulate(transmit_bytes(MESSAGE, mode(1200, "short")), sample_rate=48000).real
    path = tmp_path / "rec.wav"
    path.write_bytes(wav_bytes(np.concatenate([np.zeros(5000), x, np.zeros(5000)]), 48000, 2, True))
    out = run([*CLI, "rx", "-f", "wav", str(path)])
    assert out.returncode == 0, out.stderr
    assert out.stdout == MESSAGE


def test_wav_written_by_m110c_tx(tmp_path):
    path = tmp_path / "x.wav"
    assert run([*CLI, "-q", "-f", "wav", "-r", "2400", "-o", str(path)], MESSAGE).returncode == 0
    assert run([*CLI, "rx", "-q", "-f", "wav", str(path)]).stdout == MESSAGE


def test_sample_rate_not_a_multiple_of_2400(tmp_path):
    path = tmp_path / "cd.wav"
    path.write_bytes(wav_bytes(np.zeros(1000), 44100))
    out = run([*CLI, "rx", "-f", "wav", str(path)])
    assert out.returncode == 2 and b"resample" in out.stderr


def test_noise_only_reports_nothing_found():
    noise = np.random.default_rng(0).standard_normal(9600 * 3) * 0.1
    out = run([*CLI, "rx", "-f", "s16"], io.encode_samples(noise, "s16")[0])
    assert out.returncode == 0 and out.stdout == b""
    assert b"no transmission found" in out.stderr


def test_quiet():
    data = io.encode_samples(modulate(transmit_bytes(b"hi", mode(2400, "short"))), "cf32")[0]
    out = run([*CLI, "rx", "-q"], data)
    assert out.stdout == b"hi" and out.stderr == b""


def test_not_a_wav_file():
    out = run([*CLI, "rx", "-f", "wav"], b"definitely not RIFF")
    assert out.returncode == 1 and b"not a WAV" in out.stderr


def test_no_equalizer_selects_the_m8_receiver():
    """Same result on a clean signal; on 2 ms of multipath only the equaliser decodes."""
    x = modulate(transmit_bytes(MESSAGE, mode(2400, "short")))
    clean = io.encode_samples(x, "cf32")[0]
    assert run([*CLI, "rx", "-q", "--no-equalizer"], clean).stdout == MESSAGE
    late = np.concatenate([np.zeros(19, complex), x[:-19]])  # 2 ms at 9600 S/s
    multipath = io.encode_samples((x + late) / np.sqrt(2), "cf32")[0]
    assert run([*CLI, "rx", "-q"], multipath).stdout == MESSAGE
    assert run([*CLI, "rx", "-q", "--no-equalizer"], multipath).stdout != MESSAGE


def test_a_line_when_each_message_starts_and_one_when_it_ends():
    tx = run([*CLI, "-q", "-r", "1200"], b"hello")
    out = run([*CLI, "rx"], tx.stdout + tx.stdout)
    lines = out.stderr.decode().splitlines()
    assert out.stdout == b"hellohello"
    assert [line.endswith("receiving") for line in lines] == [True, False, True, False]
    assert "1200S preamble at 0.00 s" in lines[0] and "EOM found" in lines[1]


@pytest.mark.parametrize("channels, rate", [(0, 9600), (1, 0)])
def test_wav_header_with_no_channels_or_zero_rate(channels, rate):
    """Both used to end in a traceback (a rate of 0 even passed the multiple-of-2400 check)."""
    data = b"\0" * 1000
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, rate, 2 * rate, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
    )
    out = run([*CLI, "rx", "-f", "wav"], header + data)
    assert out.returncode == 1 and b"Traceback" not in out.stderr
    assert b"no channels or a zero sample rate" in out.stderr
