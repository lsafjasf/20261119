"""MPEG Layer III side-information parsing and bit-reservoir accounting."""

from __future__ import annotations

from dataclasses import dataclass

from .header import FrameHeader


class SideInfoError(ValueError):
    """Raised when a frame does not contain a complete Layer III side info."""


class _BitReader:
    def __init__(self, data: bytes | bytearray | memoryview):
        self.data = data
        self.bit_position = 0

    def read(self, width: int) -> int:
        if width < 0:
            raise ValueError("negative bit width")
        if self.bit_position + width > len(self.data) * 8:
            raise SideInfoError("truncated side information")
        value = 0
        for _ in range(width):
            byte_index = self.bit_position >> 3
            bit_index = 7 - (self.bit_position & 7)
            value = (value << 1) | ((self.data[byte_index] >> bit_index) & 1)
            self.bit_position += 1
        return value


@dataclass(frozen=True)
class GranuleInfo:
    granule: int
    channel: int
    part2_3_length: int
    big_values: int
    global_gain: int
    scalefac_compress: int
    window_switching_flag: int
    block_type: int
    mixed_block_flag: int
    table_select: tuple[int, ...]
    subblock_gain: tuple[int, ...]
    region0_count: int
    region1_count: int
    preflag: int
    scalefac_scale: int
    count1table_select: int


@dataclass(frozen=True)
class SideInfo:
    main_data_begin: int
    private_bits: int
    scfsi: tuple[tuple[int, ...], ...]
    granules: tuple[GranuleInfo, ...]
    size: int
    part2_3_bits: int
    part2_3_bytes: int
    reservoir_limit: int


@dataclass(frozen=True)
class SideInfoIssue:
    code: str
    message: str
    granule: int
    channel: int


def validate_side_info(side_info: SideInfo) -> list[SideInfoIssue]:
    """Return values that are representable but forbidden by Layer III syntax."""

    issues: list[SideInfoIssue] = []
    for granule in side_info.granules:
        if granule.window_switching_flag and granule.block_type == 0:
            issues.append(
                SideInfoIssue(
                    "invalid_block_type",
                    "window_switching_flag is set with block_type 0",
                    granule.granule,
                    granule.channel,
                )
            )
        if granule.mixed_block_flag and granule.block_type != 2:
            issues.append(
                SideInfoIssue(
                    "invalid_mixed_block",
                    "mixed_block_flag is only legal for short block_type 2",
                    granule.granule,
                    granule.channel,
                )
            )
        if granule.big_values > 288:
            issues.append(
                SideInfoIssue(
                    "invalid_big_values",
                    "big_values exceeds the Layer III maximum of 288",
                    granule.granule,
                    granule.channel,
                )
            )
    return issues


def _read_granule(
    reader: _BitReader,
    *,
    granule: int,
    channel: int,
    version: int,
) -> GranuleInfo:
    mpeg1 = version == 3
    part2_3_length = reader.read(12)
    big_values = reader.read(9)
    global_gain = reader.read(8)
    scalefac_compress = reader.read(4 if mpeg1 else 9)
    window_switching_flag = reader.read(1)
    block_type = 0
    mixed_block_flag = 0
    table_select: tuple[int, ...] = (0, 0, 0)
    subblock_gain: tuple[int, ...] = (0, 0, 0)
    region0_count = 0
    region1_count = 0

    if window_switching_flag:
        block_type = reader.read(2)
        mixed_block_flag = reader.read(1)
        table_select = (reader.read(5), reader.read(5), 0)
        subblock_gain = (reader.read(3), reader.read(3), reader.read(3))
    else:
        table_select = (reader.read(5), reader.read(5), reader.read(5))
        region0_count = reader.read(4)
        region1_count = reader.read(3)

    preflag = reader.read(1) if mpeg1 else 0
    scalefac_scale = reader.read(1)
    count1table_select = reader.read(1)
    return GranuleInfo(
        granule=granule,
        channel=channel,
        part2_3_length=part2_3_length,
        big_values=big_values,
        global_gain=global_gain,
        scalefac_compress=scalefac_compress,
        window_switching_flag=window_switching_flag,
        block_type=block_type,
        mixed_block_flag=mixed_block_flag,
        table_select=table_select,
        subblock_gain=subblock_gain,
        region0_count=region0_count,
        region1_count=region1_count,
        preflag=preflag,
        scalefac_scale=scalefac_scale,
        count1table_select=count1table_select,
    )


def parse_side_info(data: bytes | bytearray | memoryview, header: FrameHeader) -> SideInfo | None:
    """Parse Layer III side information, or return None for Layer I/II."""

    if header.layer != 1:
        return None
    if len(data) < header.side_info_size:
        raise SideInfoError(
            f"need {header.side_info_size} side-info bytes, have {len(data)}"
        )

    reader = _BitReader(data[: header.side_info_size])
    mpeg1 = header.version == 3
    main_data_begin = reader.read(9 if mpeg1 else 8)
    private_bits = reader.read(
        (5 if header.is_mono else 3) if mpeg1 else (1 if header.is_mono else 2)
    )
    scfsi: list[tuple[int, ...]] = []
    if mpeg1:
        for _channel in range(header.channels):
            scfsi.append(tuple(reader.read(1) for _band in range(4)))

    granules: list[GranuleInfo] = []
    granule_count = 2 if mpeg1 else 1
    for granule in range(granule_count):
        for channel in range(header.channels):
            granules.append(
                _read_granule(
                    reader,
                    granule=granule,
                    channel=channel,
                    version=header.version,
                )
            )

    part2_3_bits = sum(granule.part2_3_length for granule in granules)
    return SideInfo(
        main_data_begin=main_data_begin,
        private_bits=private_bits,
        scfsi=tuple(scfsi),
        granules=tuple(granules),
        size=header.side_info_size,
        part2_3_bits=part2_3_bits,
        part2_3_bytes=(part2_3_bits + 7) // 8,
        reservoir_limit=511 if mpeg1 else 255,
    )
