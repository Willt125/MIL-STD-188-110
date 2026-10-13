"""Sample-stream formats for files and pipes.

========  ==========================================================  =====================
Format    Layout                                                      Typical consumer
========  ==========================================================  =====================
``cf32``  complex: interleaved float32 I, Q, little-endian            GNU Radio, SDR tools
``cs16``  complex: interleaved int16 I, Q, little-endian, full scale  SDR tools
``s16``   real: int16 little-endian, the real part (the audio)        ``aplay``, ``sox``
``wav``   real: 16-bit mono WAV, the real part                        anything
========  ==========================================================  =====================

The complex formats carry the analytic signal; with the default 1800-Hz carrier its real part
is the audio waveform for an upper-sideband transmitter. The real formats carry exactly that
real part.

Integer formats saturate at full scale; the number of clipped samples is reported so a
caller can warn. The modulator's default level leaves about 3 dB of headroom.
"""

import struct

import numpy as np

FORMATS = ("cf32", "cs16", "s16", "wav")
COMPLEX_FORMATS = ("cf32", "cs16")


def _to_int16(x: np.ndarray) -> tuple[np.ndarray, int]:
    scaled = np.round(x * 32767.0)
    clipped = int(np.count_nonzero(np.abs(scaled) > 32767))
    return np.clip(scaled, -32767, 32767).astype("<i2"), clipped


def encode_samples(x: np.ndarray, fmt: str) -> tuple[bytes, int]:
    """Samples -> bytes in ``fmt`` (WAV: the sample data only). Returns (bytes, n_clipped)."""
    x = np.asarray(x, dtype=complex)
    if fmt == "cf32":
        iq = np.empty(2 * x.size, dtype="<f4")
        iq[0::2], iq[1::2] = x.real, x.imag
        return iq.tobytes(), 0
    if fmt == "cs16":
        iq = np.empty(2 * x.size)
        iq[0::2], iq[1::2] = x.real, x.imag
        data, clipped = _to_int16(iq)
        return data.tobytes(), clipped
    if fmt in ("s16", "wav"):
        data, clipped = _to_int16(x.real)
        return data.tobytes(), clipped
    raise ValueError(f"unknown format {fmt!r}; choose from {FORMATS}")


def decode_samples(data: bytes, fmt: str) -> np.ndarray:
    """Bytes in ``fmt`` -> complex samples (real formats give a zero imaginary part).

    For WAV, pass the sample data only (see :func:`read_wav`).
    """
    if fmt == "cf32":
        with np.errstate(invalid="ignore"):  # NaNs in the input are the receiver's to handle
            iq = np.frombuffer(data, dtype="<f4").astype(float)
        return iq[0::2] + 1j * iq[1::2]
    if fmt == "cs16":
        iq = np.frombuffer(data, dtype="<i2").astype(float) / 32767.0
        return iq[0::2] + 1j * iq[1::2]
    if fmt in ("s16", "wav"):
        return np.frombuffer(data, dtype="<i2").astype(float) / 32767.0 + 0j
    raise ValueError(f"unknown format {fmt!r}; choose from {FORMATS}")


def bytes_per_sample(fmt: str) -> int:
    return {"cf32": 8, "cs16": 4, "s16": 2, "wav": 2}[fmt]


# --- WAV -------------------------------------------------------------------------

_UNKNOWN = 0xFFFFFFFF  # "length unknown" marker accepted by sox and ffmpeg for streamed WAV


def wav_header(sample_rate: int, data_bytes: int | None) -> bytes:
    """44-byte PCM WAV header for 16-bit mono. ``data_bytes=None`` means unknown (a pipe)."""
    data_size = _UNKNOWN if data_bytes is None else data_bytes
    riff_size = _UNKNOWN if data_bytes is None else 36 + data_bytes
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, 2 * sample_rate, 2, 16)
        + b"data"
        + struct.pack("<I", data_size)
    )


def read_wav(data: bytes) -> tuple[int, np.ndarray]:
    """Parse a 16-bit mono PCM WAV written by this module: (sample_rate, samples)."""
    if data[:4] != b"RIFF" or data[8:16] != b"WAVEfmt ":
        raise ValueError("not a WAV file")
    _, fmt_tag, channels, sample_rate, _, _, bits = struct.unpack("<IHHIIHH", data[16:36])
    if (fmt_tag, channels, bits) != (1, 1, 16):
        raise ValueError("only 16-bit mono PCM WAV is supported")
    return sample_rate, decode_samples(data[44:], "wav")


class SampleWriter:
    """Writes samples to a binary stream in one of :data:`FORMATS`.

    Use as a context manager, or call :meth:`close`; for WAV, closing fixes up the header
    when the stream is seekable.
    """

    def __init__(self, stream, fmt: str, sample_rate: int):
        if fmt not in FORMATS:
            raise ValueError(f"unknown format {fmt!r}; choose from {FORMATS}")
        self.stream, self.fmt, self.sample_rate = stream, fmt, sample_rate
        self.samples_written = 0
        self.clipped = 0
        self._data_bytes = 0
        if fmt == "wav":
            self.stream.write(wav_header(sample_rate, None))

    def write(self, x: np.ndarray) -> None:
        data, clipped = encode_samples(x, self.fmt)
        self.stream.write(data)
        self.samples_written += len(x)
        self.clipped += clipped
        self._data_bytes += len(data)

    def close(self) -> None:
        if self.fmt == "wav" and _seekable(self.stream):
            end = self.stream.tell()
            self.stream.seek(0)
            self.stream.write(wav_header(self.sample_rate, self._data_bytes))
            self.stream.seek(end)
        self.stream.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _seekable(stream) -> bool:
    try:
        return stream.seekable()
    except (AttributeError, ValueError, OSError):
        return False


def read_wav_header(stream) -> tuple[int, int]:
    """Read a PCM WAV header from a stream, stopping at the start of the sample data.

    Walks the RIFF chunk list (other chunks, e.g. LIST, are skipped), so it works on
    pipes too. Returns (sample_rate, channels). Only 16-bit PCM is supported.
    """
    head = stream.read(12)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    sample_rate = channels = None
    while True:
        chunk = stream.read(8)
        if len(chunk) < 8:
            raise ValueError("WAV file has no data chunk")
        kind, size = chunk[:4], struct.unpack("<I", chunk[4:8])[0]
        if kind == b"data":
            if sample_rate is None:
                raise ValueError("WAV data chunk before fmt chunk")
            return sample_rate, channels
        body = stream.read(size + (size & 1))  # chunks are padded to even length
        if kind == b"fmt ":
            fmt_tag, channels, sample_rate = struct.unpack("<HHI", body[:8])
            bits = struct.unpack("<H", body[14:16])[0]
            if fmt_tag not in (1, 0xFFFE) or bits != 16:
                raise ValueError("only 16-bit PCM WAV is supported")
            if channels == 0 or sample_rate == 0:
                raise ValueError("WAV header gives no channels or a zero sample rate")
