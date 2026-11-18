"""Minimal WAV/raw PCM helpers used by the command-line interface."""

from __future__ import annotations

from dataclasses import dataclass
import io
import struct


class PcmFormatError(ValueError):
    pass


@dataclass(frozen=True)
class WavPcm:
    sample_rate: int
    channels: int
    bits_per_sample: int
    frames: tuple[tuple[int, ...], ...]

    @property
    def frame_count(self) -> int:
        return len(self.frames[0]) if self.frames else 0

    def interleaved(self) -> tuple[int, ...]:
        return tuple(
            self.frames[channel][frame]
            for frame in range(self.frame_count)
            for channel in range(self.channels)
        )


def _iter_chunks(data: bytes):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise PcmFormatError("not a RIFF/WAVE file")
    position = 12
    while position + 8 <= len(data):
        chunk_id = data[position : position + 4]
        chunk_size = struct.unpack_from("<I", data, position + 4)[0]
        start = position + 8
        end = start + chunk_size
        if end > len(data):
            raise PcmFormatError(f"truncated {chunk_id!r} chunk")
        yield chunk_id, data[start:end]
        position = end + (chunk_size & 1)


def _pcm_width(format_tag: int, bits: int, fmt: bytes) -> int:
    if format_tag == 0xFFFE:
        if len(fmt) < 40:
            raise PcmFormatError("truncated WAVE_FORMAT_EXTENSIBLE header")
        subformat = fmt[24:40]
        pcm_guid = bytes.fromhex("0100000000001000800000aa00389b71")
        if subformat != pcm_guid:
            raise PcmFormatError("only integer PCM subformats are supported")
    elif format_tag != 1:
        raise PcmFormatError(f"unsupported WAV format tag 0x{format_tag:04x}")
    if bits not in (8, 16, 24, 32):
        raise PcmFormatError(f"unsupported PCM bit depth: {bits}")
    return bits // 8


def _sample_to_int16(raw: bytes, width: int) -> int:
    if width == 1:
        return (raw[0] - 128) << 8
    if width == 2:
        return int.from_bytes(raw, "little", signed=True)
    if width == 3:
        return int.from_bytes(raw, "little", signed=True) >> 8
    return int.from_bytes(raw, "little", signed=True) >> 16


def read_wav(data: bytes) -> WavPcm:
    """Read integer PCM WAV and normalize it to signed 16-bit samples."""

    fmt = None
    payload = None
    for chunk_id, chunk in _iter_chunks(data):
        if chunk_id == b"fmt ":
            fmt = chunk
        elif chunk_id == b"data":
            payload = chunk
            break
    if fmt is None or payload is None:
        raise PcmFormatError("WAV must contain both fmt and data chunks")
    if len(fmt) < 16:
        raise PcmFormatError("truncated fmt chunk")
    (
        format_tag,
        channels,
        sample_rate,
        _byte_rate,
        block_align,
        bits_per_sample,
    ) = struct.unpack_from("<HHIIHH", fmt, 0)
    if channels < 1:
        raise PcmFormatError("WAV has zero channels")
    width = _pcm_width(format_tag, bits_per_sample, fmt)
    if block_align != channels * width:
        raise PcmFormatError("WAV block alignment is inconsistent")
    if block_align == 0:
        raise PcmFormatError("WAV has zero block alignment")

    complete_bytes = len(payload) - (len(payload) % block_align)
    frame_count = complete_bytes // block_align
    frames: list[list[int]] = [[] for _ in range(channels)]
    for frame in range(frame_count):
        base = frame * block_align
        for channel in range(channels):
            start = base + channel * width
            frames[channel].append(_sample_to_int16(payload[start : start + width], width))
    return WavPcm(
        sample_rate=sample_rate,
        channels=channels,
        bits_per_sample=bits_per_sample,
        frames=tuple(tuple(channel) for channel in frames),
    )


def write_wav_pcm16(
    sample_rate: int,
    channels: int,
    frames: tuple[tuple[int, ...], ...] | list[list[int]],
) -> bytes:
    """Serialize channel-major signed 16-bit PCM as a canonical RIFF/WAVE file."""

    if not 1 <= channels <= 65535:
        raise PcmFormatError(f"invalid channel count: {channels}")
    if len(frames) != channels:
        raise PcmFormatError("frame/channel count mismatch")
    frame_count = len(frames[0]) if frames else 0
    if any(len(channel) != frame_count for channel in frames):
        raise PcmFormatError("all channels must have the same frame count")
    pcm = bytearray()
    for frame in range(frame_count):
        for channel in range(channels):
            value = int(frames[channel][frame])
            if not -32768 <= value <= 32767:
                raise PcmFormatError(f"sample outside signed 16-bit range: {value}")
            pcm.extend(struct.pack("<h", value))

    block_align = channels * 2
    byte_rate = sample_rate * block_align
    fmt = struct.pack(
        "<HHIIHH",
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        16,
    )
    riff_size = 4 + (8 + len(fmt)) + (8 + len(pcm))
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(pcm))
        + bytes(pcm)
    )


def read_raw_pcm16(data: bytes, *, channels: int) -> tuple[tuple[int, ...], ...]:
    """Read interleaved little-endian signed 16-bit PCM, dropping a partial frame."""

    if channels < 1:
        raise PcmFormatError("channels must be positive")
    block_align = channels * 2
    frame_count = len(data) // block_align
    frames: list[list[int]] = [[] for _ in range(channels)]
    for frame in range(frame_count):
        for channel in range(channels):
            offset = (frame * channels + channel) * 2
            frames[channel].append(struct.unpack_from("<h", data, offset)[0])
    return tuple(tuple(channel) for channel in frames)


def write_raw_pcm16(frames: tuple[tuple[int, ...], ...]) -> bytes:
    """Write channel-major frames as interleaved little-endian signed 16-bit PCM."""

    channels = len(frames)
    frame_count = len(frames[0]) if frames else 0
    output = io.BytesIO()
    for frame in range(frame_count):
        for channel in range(channels):
            value = int(frames[channel][frame])
            output.write(struct.pack("<h", max(-32768, min(32767, value))))
    return output.getvalue()
