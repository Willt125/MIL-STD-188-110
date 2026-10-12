"""Tests for the m110c-tx command (M6), run as real subprocesses."""

import struct
import subprocess
import sys

import numpy as np
import pytest

from m110c import io
from m110c.framing import AsyncFormat
from m110c.modulate import modulate
from m110c.spec import mode
from m110c.tx import transmit_bytes

MESSAGE = b"The quick brown fox jumps over the lazy dog 0123456789\n" * 6


def run_tx(*args, stdin=b""):
    return subprocess.run(
        [sys.executable, "-m", "m110c.cli", *args], input=stdin, capture_output=True, timeout=120
    )


def test_stdin_to_stdout_matches_the_library():
    out = run_tx("--rate", "1200", "--interleave", "long", stdin=MESSAGE)
    assert out.returncode == 0, out.stderr
    expected = modulate(transmit_bytes(MESSAGE, mode(1200, "long")))
    assert out.stdout == io.encode_samples(expected, "cf32")[0]


def test_file_to_file(tmp_path):
    src, dst = tmp_path / "in.bin", tmp_path / "out.cs16"
    src.write_bytes(MESSAGE)
    out = run_tx(str(src), "-o", str(dst), "-f", "cs16", "-r", "300")
    assert out.returncode == 0, out.stderr
    expected = modulate(transmit_bytes(MESSAGE, mode(300, "short")))
    assert dst.read_bytes() == io.encode_samples(expected, "cs16")[0]


def test_output_does_not_depend_on_how_the_pipe_delivers_input(tmp_path):
    """Feed stdin one byte at a time through a real OS pipe, flushing each byte, and
    compare with reading the same bytes from a file in one go."""
    src, from_file, from_pipe = tmp_path / "in.bin", tmp_path / "a.cf32", tmp_path / "b.cf32"
    src.write_bytes(MESSAGE)
    assert run_tx(str(src), "-o", str(from_file), "-r", "2400", "-i", "long").returncode == 0
    with subprocess.Popen(
        [sys.executable, "-m", "m110c.cli", "-o", str(from_pipe), "-r", "2400", "-i", "long"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:  # the context manager closes every pipe, even if an assertion fails
        for byte in MESSAGE:
            proc.stdin.write(bytes([byte]))
            proc.stdin.flush()
        proc.stdin.close()
        stderr = proc.stderr.read()
        assert proc.wait(timeout=120) == 0, stderr
    assert from_pipe.read_bytes() == from_file.read_bytes()


def test_async_option():
    out = run_tx("--async", "8N1", "-r", "600", stdin=MESSAGE)
    assert out.returncode == 0, out.stderr
    expected = modulate(transmit_bytes(MESSAGE, mode(600, "short"), async_format=AsyncFormat()))
    assert out.stdout == io.encode_samples(expected, "cf32")[0]
    assert "async 8N1" in out.stderr.decode()


def test_wav_to_a_pipe_and_to_a_file(tmp_path):
    piped = run_tx("-f", "wav", "-q", stdin=b"hi")
    assert piped.returncode == 0 and piped.stderr == b""
    assert struct.unpack("<I", piped.stdout[40:44])[0] == 0xFFFFFFFF  # length unknown
    dst = tmp_path / "x.wav"
    assert run_tx("-f", "wav", "-o", str(dst), "-s", "48000", stdin=b"hi").returncode == 0
    data = dst.read_bytes()
    assert struct.unpack("<I", data[40:44])[0] == len(data) - 44  # patched to the exact size
    assert io.read_wav(data)[0] == 48000


def test_empty_input_still_produces_a_transmission():
    out = run_tx("-r", "75", "-i", "zero", "-q")
    assert out.returncode == 0
    samples = io.decode_samples(out.stdout, "cf32")
    assert samples.size > mode(75, "zero").preamble_symbols * 4  # preamble + EOM + flush


def test_summary_on_stderr():
    out = run_tx("-r", "150", "-i", "long", stdin=b"abc")
    assert out.returncode == 0
    assert "150L" in out.stderr.decode() and "3 bytes" in out.stderr.decode()


@pytest.mark.parametrize(
    "args",
    [["--rate", "9600"], ["--interleave", "medium"], ["--async", "9Z9"], ["--format", "mp3"]],
)
def test_invalid_arguments_exit_with_usage_error(args):
    out = run_tx(*args)
    assert out.returncode == 2 and out.stdout == b""


def test_async_and_msb_first_are_mutually_exclusive():
    assert run_tx("--async", "8N1", "--msb-first").returncode == 2


def test_sample_rate_not_a_multiple_of_2400():
    out = run_tx("-s", "8000")
    assert out.returncode == 2 and b"multiple of 2400" in out.stderr


def test_missing_input_file():
    out = run_tx("/nonexistent/input.bin")
    assert out.returncode == 1 and b"No such file" in out.stderr


def test_real_output_is_the_real_part_of_the_complex_output():
    cf32 = io.decode_samples(run_tx("-q", stdin=b"x").stdout, "cf32")
    s16 = io.decode_samples(run_tx("-q", "-f", "s16", stdin=b"x").stdout, "s16")
    assert np.max(np.abs(s16.real - cf32.real)) < 1 / 32767


def test_reader_going_away_is_not_an_error(tmp_path):
    """Like "m110c-tx < big | head -c 100": the tool must stop quietly with the
    conventional SIGPIPE status, not print a traceback."""
    src = tmp_path / "big.bin"
    src.write_bytes(bytes(range(256)) * 400)
    with subprocess.Popen(
        [sys.executable, "-c", "from m110c.cli import main_tx; main_tx()", "-r", "4800", str(src)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        proc.stdout.read(100)
        proc.stdout.close()
        stderr = proc.stderr.read()
        assert proc.wait(timeout=120) == 141
    assert b"Traceback" not in stderr


def _peak_memory_of_tx(tmp_path, n_bytes, rate, interleave):
    import tracemalloc

    from m110c.cli import tx_main

    src, dst = tmp_path / f"in{n_bytes}.bin", tmp_path / f"out{n_bytes}.cf32"
    src.write_bytes(bytes(range(256)) * (n_bytes // 256))
    tracemalloc.start()
    assert tx_main(["-q", "-r", str(rate), "-i", interleave, str(src), "-o", str(dst)]) == 0
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak


@pytest.mark.parametrize("rate, interleave", [(75, "long"), (2400, "long")])
def test_memory_does_not_grow_with_message_length(tmp_path, rate, interleave):
    """Bounded memory for pipes: 4x more input must not raise the peak. At 75 bps one byte
    becomes over a thousand samples, which once made a single read cost ~100 MB."""
    small = _peak_memory_of_tx(tmp_path, 1024, rate, interleave)
    large = _peak_memory_of_tx(tmp_path, 4096, rate, interleave)
    assert large < 1.3 * small
    assert large < 20e6  # bytes traced by Python, excluding the interpreter itself


def test_standard_output_is_not_closed(capsysbinary):
    """Library use: tx_main writing to "-" twice in one process must work both times, so
    it must never close sys.stdout."""
    from m110c.cli import tx_main

    assert tx_main(["-q", "-r", "2400", "/dev/null"]) == 0
    first = capsysbinary.readouterr().out
    assert tx_main(["-q", "-r", "2400", "/dev/null"]) == 0
    assert capsysbinary.readouterr().out == first and len(first) > 0
