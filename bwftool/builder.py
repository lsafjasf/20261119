"""合成 Broadcast WAVE 的测试夹具构造器。

用于构造奇数长度 chunk、缺失 iXML、矛盾时间参考等边界场景，
生产代码不依赖本模块。
"""

from __future__ import annotations


def fmt_chunk(
    sample_rate: int = 48000,
    channels: int = 2,
    bits_per_sample: int = 24,
    format_tag: int = 1,
) -> bytes:
    block_align = channels * bits_per_sample // 8
    avg = sample_rate * block_align
    body = b"fmt "
    body += (16).to_bytes(4, "little")
    body += format_tag.to_bytes(2, "little")
    body += channels.to_bytes(2, "little")
    body += sample_rate.to_bytes(4, "little")
    body += avg.to_bytes(4, "little")
    body += block_align.to_bytes(2, "little")
    body += bits_per_sample.to_bytes(2, "little")
    return body


def bext_chunk(
    description: str = "",
    originator: str = "TestRig",
    originator_reference: str = "REF-0001",
    date: str = "2026-11-19",
    time_of_day: str = "10:00:00",
    time_reference: int = 0,
    version: int = 1,
    coding_history: str = "",
) -> bytes:
    def fixed(value: str, length: int) -> bytes:
        encoded = value.encode("latin-1")
        if len(encoded) > length:
            raise ValueError("bext 定长字段超长")
        return encoded.ljust(length, b"\x00")

    data = b""
    data += fixed(description, 256)
    data += fixed(originator, 32)
    data += fixed(originator_reference, 32)
    data += fixed(date, 10)
    data += fixed(time_of_day, 8)
    data += int(time_reference).to_bytes(8, "little")
    data += version.to_bytes(2, "little")
    data += b"\x00" * 64  # UMID
    data += b"\x00" * 190  # v1 保留 + 响度字段区域
    data += coding_history.encode("latin-1")
    frame = b"bext" + len(data).to_bytes(4, "little") + data
    if len(data) % 2 == 1:
        frame += b"\x00"
    return frame


def ixml_chunk(
    scene: str = "SC01",
    take: str = "3",
    tape: str = "TAPE-A",
    note: str = "ok",
    sample_rate: int = 48000,
    bit_depth: int = 24,
    channels: int = 2,
    take_timestamp: str = "2026-11-19T10:00:00Z",
    offset_samples: int = 0,
    extra_loudness_meta: bool = False,
) -> bytes:
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<BWFXML>"
        f"<SCENE>{scene}</SCENE>"
        f"<TAKE>{take}</TAKE>"
        f"<TAPE>{tape}</TAPE>"
        f"<NOTE>{note}</NOTE>"
        f"<TRACK_COUNT>{channels}</TRACK_COUNT>"
        f"<FILE_SAMPLE_WIDTH>{bit_depth}</FILE_SAMPLE_WIDTH>"
        f"<BIT_DEPTH>{bit_depth}</BIT_DEPTH>"
        f"<FILE_CHANNEL_COUNT>{channels}</FILE_CHANNEL_COUNT>"
        f"<SAMPLE_RATE>{sample_rate}.0</SAMPLE_RATE>"
        f"<TAKE_TIMESTAMP>{take_timestamp}</TAKE_TIMESTAMP>"
        f"<BWF_START_TIME_OFFSET_SAMPLES>{offset_samples}"
        "</BWF_START_TIME_OFFSET_SAMPLES>"
        "</BWFXML>"
    )
    payload = text.encode("utf-8")
    if extra_loudness_meta:
        payload += b"\n"
    frame = b"iXML" + len(payload).to_bytes(4, "little") + payload
    if len(payload) % 2 == 1:
        frame += b"\x00"
    return frame


def data_chunk(samples: bytes) -> bytes:
    frame = b"data" + len(samples).to_bytes(4, "little") + samples
    if len(samples) % 2 == 1:
        frame += b"\x00"
    return frame


def unknown_chunk(chunk_id: bytes, payload: bytes) -> bytes:
    """构造未知 chunk；payload 为奇数时自动补一个 0x00。"""
    frame = chunk_id + len(payload).to_bytes(4, "little") + payload
    if len(payload) % 2 == 1:
        frame += b"\x00"
    return frame


def build_wave(chunk_frames: list[bytes]) -> bytes:
    # RIFF 大小字段 = 后续全部字节数，包含 "WAVE" 四字节标识。
    body = b"WAVE" + b"".join(chunk_frames)
    return b"RIFF" + len(body).to_bytes(4, "little") + body
