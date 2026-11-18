"""Synthetic int16 mono WAV generator for benchmarking the peak index."""
import math
import os
import random
import struct
import wave
from array import array


def _base_block(sample_rate, seconds=10, seed=7):
    """A textured 10s block: tremolo sine + noise, deterministic."""
    rng = random.Random(seed)
    n = sample_rate * seconds
    out = array("h", bytes(2 * n))
    for i in range(n):
        t = i / sample_rate
        trem = 0.5 + 0.5 * math.sin(2 * math.pi * 0.1 * t)
        v = 0.45 * trem * math.sin(2 * math.pi * 440 * t)
        v += 0.05 * (rng.random() * 2 - 1)
        out[i] = int(max(-1.0, min(1.0, v)) * 32767)
    return out


def generate_wav(path, seconds=3600, sample_rate=44100, impulses=()):
    """Write a PCM16 mono WAV of `seconds` length.

    `impulses`: iterable of (sample_index, value) overlaid after the base
    pattern, e.g. a single-sample +32767 spike. Returns num_samples.
    """
    block = _base_block(sample_rate)
    block_bytes = block.tobytes()
    total = seconds * sample_rate
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        written = 0
        while written < total:
            take = min(total - written, len(block))
            if take == len(block):
                wav.writeframesraw(block_bytes)
            else:
                wav.writeframesraw(block_bytes[: 2 * take])
            written += take
    os.replace(tmp, path)
    if impulses:
        off = wav_data_offset(path)
        fd = os.open(path, os.O_WRONLY)
        try:
            for pos, value in impulses:
                os.pwrite(fd, struct.pack("<h", value), off + 2 * pos)
        finally:
            os.close(fd)
    return total


def wav_data_offset(path):
    """Byte offset of the PCM data chunk inside a RIFF/WAV file."""
    with open(path, "rb") as fh:
        riff = fh.read(12)
        if riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise ValueError("not a RIFF/WAV file")
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                raise ValueError("no data chunk found")
            cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            if cid == b"data":
                return fh.tell()
            fh.seek(size + (size & 1), os.SEEK_CUR)
