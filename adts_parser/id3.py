"""Minimal ID3v2 header recognition used for stream boundary detection.

An ID3v2 tag starts with the ASCII magic ``"ID3"`` followed by version
(major + revision), a flags byte and a 4-byte *synchsafe* size (each byte
holds 7 bits, most significant byte first).  When the footer flag (bit 4)
is set another 10-byte footer mirrors the header and must be counted too.
"""

from dataclasses import dataclass

ID3V2_MAGIC = b"ID3"
ID3V2_HEADER_SIZE = 10
ID3V2_FOOTER_SIZE = 10
ID3V2_FOOTER_FLAG = 0x10


class ID3ParseError(ValueError):
    """Raised when an ID3v2 header is malformed or truncated."""


@dataclass(frozen=True)
class ID3Tag:
    offset: int
    major_version: int
    revision: int
    flags: int
    body_size: int          # synchsafe-decoded payload size
    total_size: int         # header + body (+ footer when flagged)
    has_footer: bool


def decode_synchsafe(data: bytes) -> int:
    """Decode four 7-bit synchsafe bytes into an integer."""

    if len(data) != 4 or any(byte & 0x80 for byte in data):
        raise ID3ParseError(f"invalid synchsafe integer: {data.hex()}")
    return (data[0] << 21) | (data[1] << 14) | (data[2] << 7) | data[3]


def parse_id3v2_header(buf: bytes | bytearray | memoryview, offset: int = 0) -> ID3Tag:
    """Parse an ID3v2 header located at ``buf[offset]``."""

    data = bytes(buf)
    if offset < 0 or len(data) - offset < ID3V2_HEADER_SIZE:
        raise ID3ParseError(
            f"truncated ID3v2 header at offset {offset}: need "
            f"{ID3V2_HEADER_SIZE} bytes, have {len(data) - offset}"
        )
    if data[offset : offset + 3] != ID3V2_MAGIC:
        raise ID3ParseError(f"missing ID3v2 magic at offset {offset}")

    major_version = data[offset + 3]
    revision = data[offset + 4]
    flags = data[offset + 5]
    body_size = decode_synchsafe(data[offset + 6 : offset + 10])
    has_footer = bool(flags & ID3V2_FOOTER_FLAG)
    total_size = ID3V2_HEADER_SIZE + body_size + (
        ID3V2_FOOTER_SIZE if has_footer else 0
    )

    if offset + total_size > len(data):
        raise ID3ParseError(
            f"ID3v2 tag at offset {offset} declares {total_size} bytes but only "
            f"{len(data) - offset} remain"
        )

    return ID3Tag(
        offset=offset,
        major_version=major_version,
        revision=revision,
        flags=flags,
        body_size=body_size,
        total_size=total_size,
        has_footer=has_footer,
    )


def looks_like_id3v2(buf: bytes | bytearray | memoryview, offset: int = 0) -> bool:
    """Return whether ``offset`` holds an ID3v2 magic with room for a header."""

    data = bytes(buf)
    return (
        len(data) - offset >= ID3V2_HEADER_SIZE
        and data[offset : offset + 3] == ID3V2_MAGIC
    )
