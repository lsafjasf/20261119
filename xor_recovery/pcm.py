"""项目自生成 PCM 数据源（单声道 16-bit little-endian）。"""

from __future__ import annotations

import math
import struct
import wave


def generate_pcm(seconds: float = 2.5, sample_rate: int = 8000) -> bytes:
    """生成确定性的非静音 PCM：多频率正弦叠加 + 慢速幅度包络。"""
    sample_count = int(seconds * sample_rate)
    frames = bytearray()
    amp = 0.8 * 32767
    for i in range(sample_count):
        t = i / sample_rate
        envelope = 0.85 + 0.15 * math.sin(2 * math.pi * 2.0 * t)
        value = (
            0.55 * math.sin(2 * math.pi * 440.0 * t)
            + 0.30 * math.sin(2 * math.pi * 660.0 * t)
            + 0.15 * math.sin(2 * math.pi * 990.0 * t)
        )
        sample = int(amp * envelope * value)
        sample = max(-32768, min(32767, sample))
        frames += struct.pack("<h", sample)
    return bytes(frames)


def write_wav(path: str, pcm: bytes, sample_rate: int = 8000) -> None:
    """同步写一份 WAV 方便试听，PCM 本身仍是恢复演示的基准数据。"""
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
