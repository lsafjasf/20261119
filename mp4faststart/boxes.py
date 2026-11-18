"""Low-level ISO BMFF box parsing.

The top-level parser only reads box headers, so media payloads are never loaded
into memory.  The in-memory tree parser is used for ``moov``, which is metadata
and normally small.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import struct
from typing import BinaryIO, Iterable

from .errors import MP4Error

UINT32_MAX = (1 << 32) - 1
CONTAINER_TYPES = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


@dataclass(frozen=True)
class TopLevelBox:
    type: str
    offset: int
    size: int
    header_size: int
    payload_offset: int
    payload_size: int
    extended_size: bool

    @property
    def end(self) -> int:
        return self.offset + self.size


def read_box_header(stream: BinaryIO, offset: int, file_size: int) -> tuple[str, int, int, bool]:
    """Read one box header and return ``(type, size, header_size, extended)``."""
    if offset < 0 or offset + 8 > file_size:
        raise MP4Error(f"truncated box header at offset {offset}")
    stream.seek(offset)
    header = stream.read(8)
    if len(header) != 8:
        raise MP4Error(f"truncated box header at offset {offset}")
    size32 = struct.unpack_from(">I", header, 0)[0]
    box_type = header[4:8].decode("latin-1")
    extended = False
    if size32 == 1:
        extra = stream.read(8)
        if len(extra) != 8:
            raise MP4Error(f"truncated extended-size box at offset {offset}")
        size = struct.unpack(">Q", extra)[0]
        header_size = 16
        extended = True
    elif size32 == 0:
        size = file_size - offset
        header_size = 8
    else:
        size = size32
        header_size = 8
    if size < header_size:
        raise MP4Error(f"invalid box size {size} for {box_type!r} at offset {offset}")
    if offset + size > file_size:
        raise MP4Error(
            f"box {box_type!r} at offset {offset} extends past end of file "
            f"({offset + size} > {file_size})"
        )
    return box_type, size, header_size, extended


def parse_top_level_stream(stream: BinaryIO) -> tuple[list[TopLevelBox], int]:
    """Parse sequential top-level box headers without reading payloads."""
    stream.seek(0, os.SEEK_END)
    file_size = stream.tell()
    boxes: list[TopLevelBox] = []
    offset = 0
    while offset < file_size:
        if file_size - offset < 8:
            raise MP4Error(f"{file_size - offset} trailing byte(s) after the last box")
        box_type, size, header_size, extended = read_box_header(stream, offset, file_size)
        box = TopLevelBox(
            type=box_type,
            offset=offset,
            size=size,
            header_size=header_size,
            payload_offset=offset + header_size,
            payload_size=size - header_size,
            extended_size=extended,
        )
        boxes.append(box)
        offset += size
    return boxes, file_size


def parse_top_level(path: os.PathLike[str] | str) -> tuple[list[TopLevelBox], int]:
    with open(path, "rb") as stream:
        return parse_top_level_stream(stream)


@dataclass
class BoxNode:
    """A mutable metadata box tree node.

    Leaf boxes retain their original bytes.  Only an ``stco``/``co64`` node is
    rewritten, and its ancestors are serialized again so every enclosing box
    size is recalculated.
    """

    type: str
    offset: int
    size: int
    header_size: int
    payload: bytes
    children: list["BoxNode"] | None = None
    dirty: bool = False
    replacement_type: str | None = None
    replacement_payload: bytes | None = None

    @property
    def serialized_size(self) -> int:
        if self.replacement_payload is not None:
            return self.header_size + len(self.replacement_payload)
        if not self.dirty or self.children is None:
            return self.size
        return self.header_size + sum(child.serialized_size for child in self.children)

    def to_bytes(self) -> bytes:
        if self.replacement_payload is not None:
            box_type = (self.replacement_type or self.type).encode("latin-1")
            return _serialize_header(
                self.header_size, box_type.decode("latin-1"), len(self.replacement_payload)
            ) + self.replacement_payload
        if not self.dirty:
            return self.payload
        if self.children is None:
            return self.payload
        body = b"".join(child.to_bytes() for child in self.children)
        return _serialize_header(self.header_size, self.type, len(body)) + body


def _serialize_header(header_size: int, box_type: str, payload_size: int) -> bytes:
    size = header_size + payload_size
    if header_size == 16:
        return struct.pack(">I4sQ", 1, box_type.encode("latin-1"), size)
    if size > UINT32_MAX:
        raise MP4Error(f"box {box_type!r} grew beyond 32-bit size")
    return struct.pack(">I4s", size, box_type.encode("latin-1"))


def parse_box_tree(data: bytes, *, base_offset: int = 0) -> list[BoxNode]:
    """Parse a complete box byte string, descending only into known containers."""
    nodes: list[BoxNode] = []
    offset = 0
    length = len(data)
    while offset < length:
        if length - offset < 8:
            raise MP4Error(f"{length - offset} trailing byte(s) in box payload")
        size32 = struct.unpack_from(">I", data, offset)[0]
        box_type = data[offset + 4 : offset + 8].decode("latin-1")
        header_size = 8
        if size32 == 1:
            if length - offset < 16:
                raise MP4Error(f"truncated extended-size box at offset {base_offset + offset}")
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header_size = 16
        elif size32 == 0:
            size = length - offset
        else:
            size = size32
        if size < header_size or offset + size > length:
            raise MP4Error(
                f"invalid box size {size} for {box_type!r} at offset "
                f"{base_offset + offset}"
            )
        raw = data[offset : offset + size]
        payload = raw[header_size:]
        raw_type = box_type.encode("latin-1")
        children = None
        if raw_type in CONTAINER_TYPES:
            children = parse_box_tree(payload, base_offset=base_offset + offset + header_size)
        nodes.append(
            BoxNode(
                type=box_type,
                offset=offset,
                size=size,
                header_size=header_size,
                payload=raw,
                children=children,
            )
        )
        offset += size
    return nodes


def walk_nodes(nodes: Iterable[BoxNode]) -> Iterable[BoxNode]:
    for node in nodes:
        yield node
        if node.children is not None:
            yield from walk_nodes(node.children)
