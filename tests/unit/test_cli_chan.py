"""Tests for the m110c-chan command (M9), run as real subprocesses."""

import subprocess
import sys

import numpy as np

from m110c import io

CLI = [sys.executable, "-m", "m110c.cli"]
MESSAGE = b"Through the channel simulator 0123456789\n" * 2


def run(args, stdin=b""):
    return subprocess.run(args, input=stdin, capture_output=True, timeout=300)


def tx(*args):
    return run([*CLI, "-q", *args], MESSAGE).stdout


def test_tx_chan_rx_on_a_noisy_fixed_path():
    signal = tx("-r", "1200", "-i", "short")
    noisy = run([*CLI, "chan", "-q", "--preset", "1 fixed", "--snr", "15"], signal)
    assert noisy.returncode == 0
    out = run([*CLI, "rx", "-q"], noisy.stdout)
    assert out.stdout == MESSAGE


def test_same_seed_same_output_different_seed_different_output():
    signal = tx("-r", "600")
    a = run([*CLI, "chan", "-q", "--snr", "5", "--seed", "11"], signal).stdout
    b = run([*CLI, "chan", "-q", "--snr", "5", "--seed", "11"], signal).stdout
    c = run([*CLI, "chan", "-q", "--snr", "5", "--seed", "12"], signal).stdout
    assert a == b != c and len(a) == len(signal)


def test_offset_option_shifts_the_carrier():
    signal = tx("-r", "2400")
    shifted = run([*CLI, "chan", "-q", "--preset", "1 fixed", "--offset-hz", "150"], signal).stdout
    rx = run([*CLI, "rx"], shifted)
    assert rx.stdout == MESSAGE and b"+150.0" in rx.stderr


def test_real_audio_through_the_channel(tmp_path):
    """WAV in, WAV out: the real input is made analytic (E.4.2) and the real part output."""
    wav = tmp_path / "in.wav"
    assert run([*CLI, "-q", "-f", "wav", "-o", str(wav)], MESSAGE).returncode == 0
    out_wav = tmp_path / "out.wav"
    res = run(
        [
            *CLI,
            "chan",
            "-q",
            "-f",
            "wav",
            "--preset",
            "1 fixed",
            "--snr",
            "20",
            str(wav),
            "-o",
            str(out_wav),
        ]
    )
    assert res.returncode == 0
    rate, samples = io.read_wav(out_wav.read_bytes())
    assert rate == 9600 and samples.size > 1000 and np.all(samples.imag == 0)
    assert run([*CLI, "rx", "-q", "-f", "wav", str(out_wav)]).stdout == MESSAGE


def test_summary_and_bad_preset():
    res = run([*CLI, "chan", "--preset", "2 fading 2 ms 1 Hz", "--snr", "18"], tx("-r", "75"))
    assert res.returncode == 0 and b"2 path(s), 2 ms, 1 Hz fading, SNR 18 dB" in res.stderr
    assert run([*CLI, "chan", "--preset", "nonsense"]).returncode == 2


def test_real_audio_output_is_aligned_and_complete(tmp_path):
    """The Hilbert filter's delay is removed and its tail flushed: same length as the input."""
    wav = tmp_path / "in.wav"
    run([*CLI, "-q", "-f", "wav", "-o", str(wav)], MESSAGE)
    out_wav = tmp_path / "out.wav"
    run([*CLI, "chan", "-q", "-f", "wav", "--preset", "1 fixed", str(wav), "-o", str(out_wav)])
    _, before = io.read_wav(wav.read_bytes())
    _, after = io.read_wav(out_wav.read_bytes())
    assert after.size == before.size
    assert np.max(np.abs(after.real - before.real)) < 0.02  # fixed path, no noise: unchanged
