from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class IvfError(ValueError):
    pass


@dataclass(frozen=True)
class IvfPacket:
    sequence: int
    timestamp: int
    data: bytes


def read_ivf(path: str | Path) -> tuple[bytes, list[IvfPacket]]:
    raw = Path(path).read_bytes()
    if len(raw) < 32:
        raise IvfError("IVF file is shorter than its 32-byte header")
    if raw[:4] != b"DKIF":
        raise IvfError("not an IVF file (missing DKIF signature)")
    header_size = struct.unpack_from("<H", raw, 6)[0]
    if header_size < 32 or header_size > len(raw):
        raise IvfError(f"invalid IVF header size: {header_size}")

    packets: list[IvfPacket] = []
    offset = header_size
    while offset < len(raw):
        if len(raw) - offset < 12:
            raise IvfError(f"truncated IVF packet header at offset {offset}")
        size, timestamp = struct.unpack_from("<Iq", raw, offset)
        payload_offset = offset + 12
        payload_end = payload_offset + size
        if payload_end > len(raw):
            raise IvfError(
                f"truncated IVF packet {len(packets)} at offset {offset}: "
                f"declared {size} bytes, have {len(raw) - payload_offset}"
            )
        packets.append(IvfPacket(len(packets), timestamp, raw[payload_offset:payload_end]))
        offset = payload_end
    return raw[:header_size], packets


def write_ivf(
    path: str | Path, header: bytes, packets: list[tuple[int, bytes]]
) -> None:
    if len(header) < 32 or header[:4] != b"DKIF":
        raise IvfError("write_ivf requires a valid IVF file header")
    output = bytearray(header)
    output[24:28] = len(packets).to_bytes(4, "little")
    for timestamp, payload in packets:
        output += struct.pack("<Iq", len(payload), timestamp)
        output += payload
    Path(path).write_bytes(output)
