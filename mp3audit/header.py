"""Strict MPEG audio frame-header parsing.

A sync word by itself is never considered a frame.  This module validates the
reserved version, layer, bitrate, sample-rate, and emphasis fields and derives
the exact frame size from the header.
"""

from __future__ import annotations

from dataclasses import dataclass


VERSION_NAMES = {
    3: "MPEG-1",
    2: "MPEG-2",
    0: "MPEG-2.5",
}
LAYER_NAMES = {
    3: "Layer I",
    2: "Layer II",
    1: "Layer III",
}
CHANNEL_MODE_NAMES = {
    0: "stereo",
    1: "joint-stereo",
    2: "dual-channel",
    3: "mono",
}
EMPHASIS_NAMES = {
    0: "none",
    1: "50/15 ms",
    2: "reserved",
    3: "CCITT J.17",
}

_BITRATES_KBPS = {
    (3, 3): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0),
    (3, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0),
    (3, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),
    (2, 3): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),
}
_BITRATES_KBPS[(2, 1)] = _BITRATES_KBPS[(2, 2)]
_BITRATES_KBPS[(0, 3)] = _BITRATES_KBPS[(2, 3)]
_BITRATES_KBPS[(0, 2)] = _BITRATES_KBPS[(2, 2)]
_BITRATES_KBPS[(0, 1)] = _BITRATES_KBPS[(2, 2)]

_SAMPLE_RATES = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),
}


class HeaderError(ValueError):
    """Raised when bytes at an offset do not form a usable frame header."""

    def __init__(self, reason: str, offset: int, *, sync: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.offset = offset
        self.sync = sync


@dataclass(frozen=True)
class FrameHeader:
    offset: int
    raw: int
    version: int
    version_name: str
    layer: int
    layer_name: str
    has_crc: bool
    bitrate_index: int
    bitrate: int
    sample_rate_index: int
    sample_rate: int
    padding: int
    private_bit: int
    channel_mode: int
    channel_mode_name: str
    channels: int
    mode_extension: int
    copyright_bit: int
    original_bit: int
    emphasis: int
    frame_length: int
    samples_per_frame: int
    header_size: int
    side_info_size: int
    main_data_offset: int

    @property
    def end_offset(self) -> int:
        return self.offset + self.frame_length

    @property
    def is_mono(self) -> bool:
        return self.channel_mode == 3

    @property
    def has_side_info(self) -> bool:
        return self.layer == 1


def _is_sync(raw: int) -> bool:
    return (raw >> 21) & 0x7FF == 0x7FF


def _side_info_size(version: int, layer: int, mode: int) -> int:
    if layer != 1:
        return 0
    if version == 3:
        return 17 if mode == 3 else 32
    return 9 if mode == 3 else 17


def _frame_length(version: int, layer: int, bitrate: int, sample_rate: int, padding: int) -> int:
    if layer == 3:
        return (12 * bitrate // sample_rate + padding) * 4
    if layer == 2 or version == 3:
        return 144 * bitrate // sample_rate + padding
    return 72 * bitrate // sample_rate + padding


def _samples_per_frame(version: int, layer: int) -> int:
    if layer == 3:
        return 384
    if layer == 2 or version == 3:
        return 1152
    return 576


def parse_header(data: bytes | bytearray | memoryview, offset: int = 0) -> FrameHeader:
    """Parse one MPEG audio frame header at *offset*.

    The caller still has to validate that the following boundary is consistent.
    A successful return here only means that the 32 header bits are legal.
    """

    if offset < 0 or offset + 4 > len(data):
        raise HeaderError("truncated header", offset, sync=False)
    raw = int.from_bytes(data[offset : offset + 4], "big")
    if not _is_sync(raw):
        raise HeaderError("missing 11-bit sync word", offset, sync=False)

    version = (raw >> 19) & 0x3
    if version == 1:
        raise HeaderError("reserved MPEG version", offset, sync=True)
    layer = (raw >> 17) & 0x3
    if layer == 0:
        raise HeaderError("reserved layer", offset, sync=True)

    bitrate_index = (raw >> 12) & 0xF
    if bitrate_index == 15:
        raise HeaderError("reserved bitrate index", offset, sync=True)
    if bitrate_index == 0:
        raise HeaderError("free-format bitrate is not supported", offset, sync=True)
    bitrate = _BITRATES_KBPS[(version, layer)][bitrate_index] * 1000
    if not bitrate:
        raise HeaderError("invalid bitrate", offset, sync=True)

    sample_rate_index = (raw >> 10) & 0x3
    if sample_rate_index == 3:
        raise HeaderError("reserved sample-rate index", offset, sync=True)
    sample_rate = _SAMPLE_RATES[version][sample_rate_index]

    emphasis = raw & 0x3
    if emphasis == 2:
        raise HeaderError("reserved emphasis", offset, sync=True)

    padding = (raw >> 9) & 0x1
    channel_mode = (raw >> 6) & 0x3
    has_crc = ((raw >> 16) & 0x1) == 0
    header_size = 6 if has_crc else 4
    side_info_size = _side_info_size(version, layer, channel_mode)
    frame_length = _frame_length(version, layer, bitrate, sample_rate, padding)
    if frame_length < header_size + side_info_size:
        raise HeaderError("frame too short for header and side information", offset, sync=True)

    return FrameHeader(
        offset=offset,
        raw=raw,
        version=version,
        version_name=VERSION_NAMES[version],
        layer=layer,
        layer_name=LAYER_NAMES[layer],
        has_crc=has_crc,
        bitrate_index=bitrate_index,
        bitrate=bitrate,
        sample_rate_index=sample_rate_index,
        sample_rate=sample_rate,
        padding=padding,
        private_bit=(raw >> 8) & 0x1,
        channel_mode=channel_mode,
        channel_mode_name=CHANNEL_MODE_NAMES[channel_mode],
        channels=1 if channel_mode == 3 else 2,
        mode_extension=(raw >> 4) & 0x3,
        copyright_bit=(raw >> 3) & 0x1,
        original_bit=(raw >> 2) & 0x1,
        emphasis=emphasis,
        frame_length=frame_length,
        samples_per_frame=_samples_per_frame(version, layer),
        header_size=header_size,
        side_info_size=side_info_size,
        main_data_offset=header_size + side_info_size,
    )
