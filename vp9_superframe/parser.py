from __future__ import annotations

from pathlib import Path

from .bitreader import BitReader, BitReaderError
from .ivf import IvfPacket, read_ivf
from .model import HeaderInfo, PacketReport, SubframeInfo


class HeaderError(ValueError):
    pass


def parse_frame_header(data: bytes) -> HeaderInfo:
    reader = BitReader(data)
    try:
        if reader.read_bits(2) != 0b10:
            raise HeaderError("invalid VP9 frame marker")

        profile = reader.read_bit() | (reader.read_bit() << 1)
        if profile == 3 and reader.read_bit() != 0:
            raise HeaderError("reserved bit is set for VP9 profile 3")

        if reader.read_bit():
            return HeaderInfo(
                profile=profile,
                show_existing_frame=True,
                show_frame=None,
                frame_type=None,
                error_resilient=None,
                frame_to_show_map_idx=reader.read_bits(3),
            )

        frame_type = reader.read_bit()
        show_frame = bool(reader.read_bit())
        error_resilient = bool(reader.read_bit())
        return HeaderInfo(
            profile=profile,
            show_existing_frame=False,
            show_frame=show_frame,
            frame_type=frame_type,
            error_resilient=error_resilient,
        )
    except BitReaderError as exc:
        raise HeaderError(str(exc)) from exc


def _header_or_error(
    data: bytes, report: PacketReport, subframe_index: int
) -> HeaderInfo | None:
    try:
        return parse_frame_header(data)
    except HeaderError as exc:
        report.errors.append(f"subframe {subframe_index}: invalid frame header: {exc}")
        report.corrupted = True
        return None


def _add_subframe(
    report: PacketReport,
    offset: int,
    size: int,
    source: bytes,
    indexed: bool = True,
    salvage_reason: str | None = None,
) -> None:
    index = len(report.subframes)
    frame_bytes = source[offset : offset + size]
    header = _header_or_error(frame_bytes, report, index)
    report.subframes.append(
        SubframeInfo(
            index=index,
            offset=offset,
            size=size,
            header=header,
            indexed=indexed,
            salvage_reason=salvage_reason,
        )
    )


def inspect_packet(packet: IvfPacket) -> PacketReport:
    data = packet.data
    report = PacketReport(
        sequence=packet.sequence,
        timestamp=packet.timestamp,
        packet_size=len(data),
        is_superframe=False,
        payload_size=len(data),
        size_sum=len(data),
        sizes=[len(data)],
    )
    if not data:
        report.corrupted = True
        report.errors.append("empty packet")
        return report

    marker = data[-1]
    if (marker & 0xE0) != 0xC0:
        _add_subframe(report, 0, len(data), data)
        return report

    report.is_superframe = True
    report.marker = marker
    report.declared_frame_count = (marker & 0x07) + 1
    magnitude = ((marker >> 3) & 0x03) + 1
    report.index_size = 2 + magnitude * report.declared_frame_count

    if len(data) < report.index_size:
        report.corrupted = True
        report.errors.append(
            f"truncated superframe index: need {report.index_size} bytes, "
            f"packet has {len(data)}"
        )
        return report

    index_offset = len(data) - report.index_size
    report.index_offset = index_offset
    if data[index_offset] != marker:
        report.corrupted = True
        report.errors.append(
            f"superframe index marker mismatch: prefix=0x{data[index_offset]:02x}, "
            f"suffix=0x{marker:02x}"
        )
        return report

    encoded_sizes = data[index_offset + 1 : index_offset + 1 + magnitude * report.declared_frame_count]
    report.sizes = [
        int.from_bytes(encoded_sizes[pos : pos + magnitude], "little")
        for pos in range(0, len(encoded_sizes), magnitude)
    ]
    report.size_sum = sum(report.sizes)
    report.payload_size = index_offset

    if report.size_sum != report.payload_size:
        report.corrupted = True
        relation = "unindexed tail" if report.size_sum < report.payload_size else "missing payload"
        report.errors.append(
            f"frame-size sum {report.size_sum} != indexed payload "
            f"{report.payload_size} ({abs(report.payload_size - report.size_sum)} bytes "
            f"of {relation})"
        )

    cursor = 0
    for size in report.sizes:
        if size == 0:
            report.corrupted = True
            report.errors.append(f"subframe {len(report.subframes)} has zero size")
            break
        if cursor + size > report.payload_size:
            report.corrupted = True
            report.errors.append(
                f"subframe {len(report.subframes)} extends past indexed payload"
            )
            break
        _add_subframe(report, cursor, size, data[:report.payload_size])
        cursor += size

    if report.size_sum < report.payload_size and cursor < report.payload_size:
        tail_size = report.payload_size - cursor
        _add_subframe(
            report,
            cursor,
            tail_size,
            data[:report.payload_size],
            indexed=False,
            salvage_reason="bytes remain after the final indexed frame",
        )

    return report


def inspect_ivf(path: str | Path) -> tuple[bytes, list[PacketReport]]:
    header, packets = read_ivf(path)
    return header, [inspect_packet(packet) for packet in packets]
