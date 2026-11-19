"""Deterministic MPEG-1 Layer III fixture construction for boundary tests."""

from __future__ import annotations

from dataclasses import dataclass


MPEG1_LAYER3_BITRATES = {
    1: 32,
    2: 40,
    3: 48,
    4: 56,
    5: 64,
    6: 80,
    7: 96,
    8: 112,
    9: 128,
    10: 160,
    11: 192,
    12: 224,
    13: 256,
    14: 320,
}


class BitWriter:
    def __init__(self) -> None:
        self._value = 0
        self._bits = 0
        self._out = bytearray()

    def write(self, value: int, width: int) -> None:
        if value < 0 or value >= (1 << width):
            raise ValueError(f"{value} does not fit in {width} bits")
        self._value = (self._value << width) | value
        self._bits += width
        while self._bits >= 8:
            shift = self._bits - 8
            self._out.append((self._value >> shift) & 0xFF)
            self._bits -= 8
            self._value &= (1 << self._bits) - 1 if self._bits else 0

    def finish(self) -> bytes:
        if self._bits:
            self._out.append((self._value << (8 - self._bits)) & 0xFF)
            self._bits = 0
            self._value = 0
        return bytes(self._out)


def header_bytes(
    *,
    bitrate_index: int = 9,
    sample_rate_index: int = 0,
    padding: int = 0,
    mode: int = 0,
    version: int = 3,
    layer: int = 1,
    has_crc: bool = False,
) -> bytes:
    value = 0x7FF << 21
    value |= version << 19
    value |= layer << 17
    value |= (0 if has_crc else 1) << 16
    value |= bitrate_index << 12
    value |= sample_rate_index << 10
    value |= padding << 9
    value |= mode << 6
    return value.to_bytes(4, "big")


def side_info_bytes(
    *,
    main_data_begin: int = 0,
    part2_3_length: int = 128,
    version: int = 3,
    mode: int = 0,
    big_values: int = 0,
    window_switching_flag: int = 0,
    block_type: int = 0,
    mixed_block_flag: int = 0,
) -> bytes:
    channels = 1 if mode == 3 else 2
    granules = 2 if version == 3 else 1
    writer = BitWriter()
    if version == 3:
        writer.write(main_data_begin, 9)
        writer.write(0, 5 if channels == 1 else 3)
        for _channel in range(channels):
            writer.write(0, 4)
    else:
        writer.write(main_data_begin, 8)
        writer.write(0, 1 if channels == 1 else 2)

    for _granule in range(granules):
        for _channel in range(channels):
            writer.write(part2_3_length, 12)
            writer.write(big_values, 9)
            writer.write(0, 8)
            writer.write(0, 4 if version == 3 else 9)
            writer.write(window_switching_flag, 1)
            if window_switching_flag:
                writer.write(block_type, 2)
                writer.write(mixed_block_flag, 1)
                writer.write(0, 10)
                writer.write(0, 9)
            else:
                writer.write(0, 15)
                writer.write(0, 4)
                writer.write(0, 3)
            if version == 3:
                writer.write(0, 1)
            writer.write(0, 1)
            writer.write(0, 1)
    return writer.finish()


def frame(
    *,
    bitrate_index: int = 9,
    padding: int = 0,
    main_data_begin: int = 0,
    part2_3_length: int = 128,
    mode: int = 0,
    big_values: int = 0,
    window_switching_flag: int = 0,
    block_type: int = 0,
    mixed_block_flag: int = 0,
) -> bytes:
    header = header_bytes(
        bitrate_index=bitrate_index,
        padding=padding,
        mode=mode,
    )
    frame_length = 144 * (MPEG1_LAYER3_BITRATES[bitrate_index] * 1000) // 44100
    frame_length += padding
    side = side_info_bytes(
        main_data_begin=main_data_begin,
        part2_3_length=part2_3_length,
        mode=mode,
        big_values=big_values,
        window_switching_flag=window_switching_flag,
        block_type=block_type,
        mixed_block_flag=mixed_block_flag,
    )
    if len(header) + len(side) > frame_length:
        raise ValueError("fixture frame is too short")
    payload = bytes(frame_length - len(header) - len(side))
    return header + side + payload


@dataclass(frozen=True)
class Fixture:
    data: bytes
    frame_offsets: tuple[int, ...]


def cbr_stream(*, frame_count: int = 12) -> Fixture:
    chunks: list[bytes] = []
    offsets: list[int] = []
    offset = 0
    for index in range(frame_count):
        chunk = frame(
            bitrate_index=9,
            padding=1 if index % 3 == 2 else 0,
            main_data_begin=0,
        )
        offsets.append(offset)
        chunks.append(chunk)
        offset += len(chunk)
    return Fixture(b"".join(chunks), tuple(offsets))


def vbr_stream(*, frame_count: int = 13) -> Fixture:
    bitrate_indices = [9, 10, 11, 8, 12, 9, 13, 10, 7, 11, 9, 12, 8, 10]
    chunks: list[bytes] = []
    offsets: list[int] = []
    offset = 0
    for index in range(frame_count):
        chunk = frame(
            bitrate_index=bitrate_indices[index % len(bitrate_indices)],
            padding=index % 2,
            main_data_begin=0 if index == 0 else 32 + (index % 3) * 16,
            part2_3_length=128 + (index % 4) * 32,
        )
        offsets.append(offset)
        chunks.append(chunk)
        offset += len(chunk)
    return Fixture(b"".join(chunks), tuple(offsets))


def _synchsafe(value: int) -> bytes:
    return bytes(
        (
            (value >> 21) & 0x7F,
            (value >> 14) & 0x7F,
            (value >> 7) & 0x7F,
            value & 0x7F,
        )
    )


def id3v2_tag(payload_size: int = 120) -> bytes:
    payload = bytearray(payload_size)
    payload[:4] = b"TIT2"
    payload[4:8] = (payload_size - 10).to_bytes(4, "big")
    payload[8] = 0
    payload[9] = 0
    return b"ID3\x03\x00\x00" + _synchsafe(payload_size) + bytes(payload)


def id3v1_tag() -> bytes:
    return b"TAG" + b"\x00" * 125


def id3_stream(*, frame_count: int = 10) -> Fixture:
    base = cbr_stream(frame_count=frame_count)
    prefix = id3v2_tag()
    suffix = id3v1_tag()
    offsets = tuple(offset + len(prefix) for offset in base.frame_offsets)
    return Fixture(prefix + base.data + suffix, offsets)


def prefill_stream() -> Fixture:
    chunks = [
        frame(main_data_begin=64),
        frame(main_data_begin=0),
        frame(main_data_begin=32),
        frame(main_data_begin=0),
    ]
    offsets = []
    offset = 0
    for chunk in chunks:
        offsets.append(offset)
        offset += len(chunk)
    return Fixture(b"".join(chunks), tuple(offsets))


def drift_stream(*, insert_after_frame: int = 3, junk: bytes = b"\x00\x01\x02") -> Fixture:
    base = cbr_stream(frame_count=10)
    split = base.frame_offsets[insert_after_frame] + (
        base.frame_offsets[insert_after_frame + 1]
        - base.frame_offsets[insert_after_frame]
    )
    adjusted = tuple(
        offset + (len(junk) if offset >= split else 0)
        for offset in base.frame_offsets
    )
    return Fixture(base.data[:split] + junk + base.data[split:], adjusted)


def false_sync_stream() -> Fixture:
    base = cbr_stream(frame_count=8)
    prefix = b"\xff\xfb\x90\x00" + b"\x00" * 12
    offsets = tuple(offset + len(prefix) for offset in base.frame_offsets)
    return Fixture(prefix + base.data, offsets)


def semantic_damage_stream() -> Fixture:
    frames = [
        frame(),
        frame(window_switching_flag=1, block_type=0),
        frame(),
    ]
    offsets = []
    offset = 0
    for chunk in frames:
        offsets.append(offset)
        offset += len(chunk)
    return Fixture(b"".join(frames), tuple(offsets))
