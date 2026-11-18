"""Minimal mono 16-bit PCM WAV I/O built on the stdlib ``wave`` module.

Samples cross the API boundary as Python floats in ``[-1.0, 1.0]`` so the
rendering engine stays format agnostic.  Quantization is applied in exactly
one place (``write_wav``) and mirrored by ``read_wav`` so round-tripped
files can be compared sample-for-sample against audition output.
"""

import array
import wave

_INT16_MAX = 32767
_INT16_MIN = -32768


def quantize_int16(sample):
    """Quantize one float sample to the exact int16 value written to disk."""
    scaled = int(round(sample * _INT16_MAX))
    if scaled > _INT16_MAX:
        scaled = _INT16_MAX
    elif scaled < _INT16_MIN:
        scaled = _INT16_MIN
    return scaled


def write_wav(path, samples, sample_rate):
    """Write a mono 16-bit PCM WAV file from float samples."""
    pcm = array.array("h", (quantize_int16(s) for s in samples))
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def read_wav(path):
    """Read a mono WAV file, returning ``(float_samples, sample_rate)``."""
    with wave.open(path, "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if channels != 1:
        raise ValueError("only mono WAV files are supported")
    if sample_width == 2:
        pcm = array.array("h")
        pcm.frombytes(raw)
        return [v / _INT16_MAX for v in pcm], sample_rate
    if sample_width == 1:
        # 8-bit PCM is unsigned in WAV.
        return [(b - 128) / _INT16_MAX for b in raw], sample_rate
    if sample_width == 4:
        pcm = array.array("i")
        pcm.frombytes(raw)
        return [v / 2147483648.0 for v in pcm], sample_rate
    raise ValueError(f"unsupported sample width: {sample_width} bytes")
