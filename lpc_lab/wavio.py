"""Minimal PCM WAV input/output using only the standard library."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Sequence


def _signed_sample(raw: bytes, width: int) -> int:
    return int.from_bytes(raw, "little", signed=True)


def read_wav(path: str | Path) -> tuple[list[float], int]:
    """Read PCM WAV and return mono float samples in [-1, 1]."""

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        compression = handle.getcomptype()
        if compression != "NONE":
            raise ValueError(f"unsupported WAV compression: {compression}")
        if sample_width not in {1, 2, 3, 4}:
            raise ValueError(f"unsupported PCM sample width: {sample_width}")
        payload = handle.readframes(frame_count)

    if sample_width == 1:
        values = [(value - 128) / 128.0 for value in payload]
    else:
        scale = float(1 << (8 * sample_width - 1))
        values = [
            _signed_sample(payload[offset : offset + sample_width], sample_width)
            / scale
            for offset in range(0, len(payload), sample_width)
        ]

    if channels > 1:
        mono = []
        for offset in range(0, len(values), channels):
            group = values[offset : offset + channels]
            mono.append(sum(group) / len(group))
        values = mono
    return values, sample_rate


def _clip_int16(value: float) -> int:
    if value >= 1.0:
        return 32767
    if value <= -1.0:
        return -32768
    return max(-32768, min(32767, int(round(value * 32767.0))))


def write_wav(path: str | Path, samples: Sequence[float], sample_rate: int) -> None:
    """Write mono signed 16-bit PCM WAV."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray()
    for value in samples:
        payload += _clip_int16(float(value)).to_bytes(2, "little", signed=True)
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(payload))

