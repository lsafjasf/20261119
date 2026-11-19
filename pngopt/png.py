"""Low level PNG parsing, scanline filtering and re-serialization.

Only non-interlaced (interlace method 0) images are supported.  Every input
chunk is kept except IDAT, whose compressed payload is regenerated after the
filter strategy has been re-chosen; therefore pixel data, color information and
all ancillary chunks are preserved byte-for-byte.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

FILTER_NONE = 0
FILTER_SUB = 1
FILTER_UP = 2
FILTER_AVERAGE = 3
FILTER_PAETH = 4
FILTER_NAMES = ("none", "sub", "up", "average", "paeth")

# color type -> (allowed bit depths, number of channels)
_COLOR_TYPES = {
    0: ((1, 2, 4, 8, 16), 1),
    2: ((8, 16), 3),
    3: ((1, 2, 4, 8), 1),
    4: ((8, 16), 2),
    6: ((8, 16), 4),
}


class PNGError(ValueError):
    """Raised for malformed or non-conformant PNG input."""


class UnsupportedInterlaceError(PNGError):
    """Raised when an Adam7 (interlace method 1) PNG is encountered.

    The optimizer deliberately refuses interlaced images instead of emitting a
    silently incorrect file.
    """


@dataclass(frozen=True)
class ParsedPNG:
    width: int
    height: int
    bit_depth: int
    color_type: int
    interlace: int
    channels: int
    row_stride: int          # bytes of pixel data in one scanline
    bpp: int                 # bytes per pixel for filter purposes (>= 1)
    raw: bytes               # decompressed scanline stream (with filter bytes)
    chunks: tuple            # ordered (type, data) of every source chunk

    @property
    def rows(self) -> list[bytes]:
        """Decompressed scanlines, each including its leading filter byte."""
        step = self.row_stride + 1
        return [self.raw[i * step:(i + 1) * step] for i in range(self.height)]


def _read_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(PNG_SIGNATURE):
        raise PNGError("not a PNG file (bad signature)")
    pos = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    seen_iend = False
    while pos < len(data):
        if len(data) - pos < 12:
            raise PNGError("truncated chunk header")
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        body_start = pos + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            raise PNGError(f"truncated {ctype!r} chunk")
        body = data[body_start:body_end]
        stored_crc = int.from_bytes(data[body_end:body_end + 4], "big")
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != stored_crc:
            raise PNGError(f"CRC mismatch in {ctype.decode('latin1')!r} chunk")
        chunks.append((ctype, body))
        if ctype == b"IEND":
            seen_iend = True
            pos = body_end + 4
            break
        pos = body_end + 4
    if not seen_iend:
        raise PNGError("missing IEND chunk")
    if pos != len(data):
        raise PNGError("trailing bytes after IEND")
    if not chunks or chunks[0][0] != b"IHDR":
        raise PNGError("IHDR must be the first chunk")
    return chunks


def parse_png(data: bytes) -> ParsedPNG:
    chunks = _read_chunks(data)
    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        raise PNGError("IHDR must be 13 bytes")
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    bit_depth = ihdr[8]
    color_type = ihdr[9]
    compression = ihdr[10]
    filter_method = ihdr[11]
    interlace = ihdr[12]
    if width == 0 or height == 0:
        raise PNGError("zero-width/zero-height images are not supported")
    if color_type not in _COLOR_TYPES:
        raise PNGError(f"unsupported color type {color_type}")
    allowed_depths, channels = _COLOR_TYPES[color_type]
    if bit_depth not in allowed_depths:
        raise PNGError(
            f"bit depth {bit_depth} invalid for color type {color_type}")
    if compression != 0:
        raise PNGError(f"unsupported compression method {compression}")
    if filter_method != 0:
        raise PNGError(f"unsupported filter method {filter_method}")
    if interlace != 0:
        raise UnsupportedInterlaceError(
            "interlace method 1 (Adam7) is not supported; refusing to "
            "process the image rather than emitting an incorrect file")

    idat = b"".join(body for ctype, body in chunks if ctype == b"IDAT")
    if not idat:
        raise PNGError("missing IDAT chunk")
    try:
        raw = zlib.decompress(idat)
    except zlib.error as exc:
        raise PNGError(f"IDAT decompression failed: {exc}") from exc

    bits_per_pixel = channels * bit_depth
    row_stride = math.ceil(width * bits_per_pixel / 8)
    expected = height * (row_stride + 1)
    if len(raw) != expected:
        raise PNGError(
            f"decompressed stream has {len(raw)} bytes, expected {expected}")
    bpp = max(1, math.ceil(bits_per_pixel / 8))
    return ParsedPNG(
        width=width, height=height, bit_depth=bit_depth,
        color_type=color_type, interlace=interlace, channels=channels,
        row_stride=row_stride, bpp=bpp, raw=raw, chunks=tuple(chunks))


def paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def encode_row(raw: bytes, prev: bytes | None, stride: int, bpp: int,
               method: int) -> bytes:
    """Return one filtered scanline (leading filter byte included)."""
    out = bytearray(stride + 1)
    out[0] = method
    for i in range(stride):
        x = raw[i]
        a = raw[i - bpp] if i >= bpp else 0
        b = prev[i] if prev is not None else 0
        c = prev[i - bpp] if prev is not None and i >= bpp else 0
        if method == FILTER_NONE:
            value = x
        elif method == FILTER_SUB:
            value = x - a
        elif method == FILTER_UP:
            value = x - b
        elif method == FILTER_AVERAGE:
            value = x - ((a + b) >> 1)
        else:  # FILTER_PAETH
            value = x - paeth_predictor(a, b, c)
        out[i + 1] = value & 0xFF
    return bytes(out)


def reconstruct_row(filtered: bytes, prev: bytes | None, stride: int,
                    bpp: int) -> bytes:
    """Reverse a filtered scanline, returning the raw pixel bytes."""
    method = filtered[0]
    if method > FILTER_PAETH:
        raise PNGError(f"invalid scanline filter type {method}")
    rec = bytearray(stride)
    for i in range(stride):
        x = filtered[i + 1]
        a = rec[i - bpp] if i >= bpp else 0
        b = prev[i] if prev is not None else 0
        c = prev[i - bpp] if prev is not None and i >= bpp else 0
        if method == FILTER_NONE:
            value = x
        elif method == FILTER_SUB:
            value = x + a
        elif method == FILTER_UP:
            value = x + b
        elif method == FILTER_AVERAGE:
            value = x + ((a + b) >> 1)
        else:  # FILTER_PAETH
            value = x + paeth_predictor(a, b, c)
        rec[i] = value & 0xFF
    return bytes(rec)


def make_chunk(ctype: bytes, body: bytes) -> bytes:
    return (len(body).to_bytes(4, "big") + ctype + body
            + (zlib.crc32(ctype + body) & 0xFFFFFFFF).to_bytes(4, "big"))


def build_png(parsed: ParsedPNG, idat_payload: bytes,
              idat_limit: int = 8192) -> bytes:
    """Serialize a PNG, keeping source chunks and replacing IDAT/IEND."""
    parts = [PNG_SIGNATURE]
    idat_written = False
    for ctype, body in parsed.chunks:
        if ctype in (b"IDAT", b"IEND"):
            if not idat_written:
                for off in range(0, len(idat_payload), idat_limit):
                    parts.append(make_chunk(
                        b"IDAT", idat_payload[off:off + idat_limit]))
                idat_written = True
            continue
        parts.append(make_chunk(ctype, body))
    if not idat_written:
        raise PNGError("source PNG has no IDAT slot to replace")
    parts.append(make_chunk(b"IEND", b""))
    return b"".join(parts)
