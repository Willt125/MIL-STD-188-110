"""Tests for sample formats and the WAV writer (M6)."""

import io as pyio
import struct
import wave

import numpy as np
import pytest

from m110c import io

SAMPLES = 0.5 * np.exp(1j * np.linspace(0, 20, 1001))


def test_cf32_round_trip_is_float32_exact():
    data, clipped = io.encode_samples(SAMPLES, "cf32")
    assert len(data) == 8 * SAMPLES.size and clipped == 0
    back = io.decode_samples(data, "cf32")
    assert np.allclose(back, SAMPLES.astype(np.complex64), atol=0)


def test_cf32_is_interleaved_i_then_q():
    data, _ = io.encode_samples(np.array([0.25 - 0.5j]), "cf32")
    assert struct.unpack("<ff", data) == (0.25, -0.5)


def test_cs16_round_trip():
    back = io.decode_samples(io.encode_samples(SAMPLES, "cs16")[0], "cs16")
    assert np.max(np.abs(back - SAMPLES)) < 1 / 32767


@pytest.mark.parametrize("fmt", ["s16", "wav"])
def test_real_formats_carry_the_real_part(fmt):
    back = io.decode_samples(io.encode_samples(SAMPLES, fmt)[0], fmt)
    assert np.max(np.abs(back.real - SAMPLES.real)) < 1 / 32767
    assert not back.imag.any()


def test_integer_formats_saturate_and_count_clipping():
    data, clipped = io.encode_samples(np.array([2.0, -3.0, 0.1]), "s16")
    assert clipped == 2
    assert struct.unpack("<hhh", data)[:2] == (32767, -32767)


def test_unknown_format_rejected():
    with pytest.raises(ValueError):
        io.encode_samples(SAMPLES, "mp3")


def test_wav_to_seekable_file_has_exact_sizes(tmp_path):
    path = tmp_path / "x.wav"
    with open(path, "wb") as f, io.SampleWriter(f, "wav", 9600) as w:
        w.write(SAMPLES[:500])
        w.write(SAMPLES[500:])
    with wave.open(str(path)) as r:  # the standard library's reader as an independent check
        assert (r.getnchannels(), r.getsampwidth(), r.getframerate()) == (1, 2, 9600)
        assert r.getnframes() == SAMPLES.size
    data = path.read_bytes()
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8  # RIFF size: all after it
    assert struct.unpack("<I", data[40:44])[0] == len(data) - 44  # data size
    rate, back = io.read_wav(data)
    assert rate == 9600 and back.size == SAMPLES.size


class Pipe(pyio.BytesIO):
    """A stream that, like a pipe, cannot seek."""

    def seekable(self):
        return False


def test_wav_to_pipe_marks_length_unknown():
    pipe = Pipe()
    with io.SampleWriter(pipe, "wav", 48000) as w:
        w.write(SAMPLES)
    data = pipe.getvalue()
    assert struct.unpack("<I", data[4:8])[0] == 0xFFFFFFFF
    assert struct.unpack("<I", data[40:44])[0] == 0xFFFFFFFF
    rate, back = io.read_wav(data)
    assert rate == 48000 and back.size == SAMPLES.size


def test_writer_counts_samples_and_clipping():
    w = io.SampleWriter(pyio.BytesIO(), "cs16", 9600)
    w.write(np.array([0.1, 5.0]))
    assert w.samples_written == 2 and w.clipped == 1  # the real part clips; imag is 0
