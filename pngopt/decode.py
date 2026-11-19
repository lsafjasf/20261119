"""Independent PNG decoder used for verification and decode timing.

This module deliberately performs its own signature/chunk/CRC parsing instead
of reusing :mod:`pngopt.png`, so that round-trip checks genuinely re-decode
the produced bytes from scratch.  Only non-interlaced method-0 images are
handled.
"""

from __future__ import annotations

import math
import time
import zlib

from .png import (
    PNG_SIGNATURE,
    UnsupportedInterlaceError,
    PNGError,
    reconstruct_row,
)

_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _iter_chunks(data: bytes):
    if not data.startswith(PNG_SIGNATURE):
        raise PNGError("decoder: bad PNG signature")
    pos = len(PNG_SIGNATURE)
    while pos < len(data):
        if len(data) - pos < 12:
            raise PNGError("decoder: truncated chunk")
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        end = pos + 8 + length
        if end + 4 > len(data):
            raise PNGError(f"decoder: truncated {ctype!r} chunk")
        body = data[pos + 8:end]
        crc = int.from_bytes(data[end:end + 4], "big")
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != crc:
            raise PNGError(f"decoder: CRC error in {ctype!r}")
        yield ctype, body
        pos = end + 4
        if ctype == b"IEND":
            return
    raise PNGError("decoder: missing IEND")


def decode_png(data: bytes) -> dict:
    """Decode a PNG and return image metadata plus reconstructed pixels."""
    idat = bytearray()
    ihdr = None
    for ctype, body in _iter_chunks(data):
        if ctype == b"IHDR":
            ihdr = body
        elif ctype == b"IDAT":
            idat.extend(body)
    if ihdr is None:
        raise PNGError("decoder: missing IHDR")
    if len(ihdr) != 13:
        raise PNGError("decoder: bad IHDR length")
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    bit_depth = ihdr[8]
    color_type = ihdr[9]
    if ihdr[10] != 0 or ihdr[11] != 0:
        raise PNGError("decoder: unsupported compression/filter method")
    if ihdr[12] != 0:
        raise UnsupportedInterlaceError(
            "decoder: Adam7 interlaced PNGs are intentionally unsupported")
    if color_type not in _CHANNELS:
        raise PNGError(f"decoder: bad color type {color_type}")

    channels = _CHANNELS[color_type]
    stride = math.ceil(width * channels * bit_depth / 8)
    bpp = max(1, math.ceil(channels * bit_depth / 8))
    stream = zlib.decompress(bytes(idat))
    if len(stream) != height * (stride + 1):
        raise PNGError("decoder: decompressed length mismatch")

    rows = []
    prev = None
    step = stride + 1
    for r in range(height):
        filtered = stream[r * step:(r + 1) * step]
        prev = reconstruct_row(filtered, prev, stride, bpp)
        rows.append(prev)
    return {
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "channels": channels,
        "row_stride": stride,
        "pixels": b"".join(rows),
    }


def time_decode(data: bytes, repeats: int = 20) -> tuple[float, float, dict]:
    """Return (mean seconds, best seconds, decoded result) over repeats."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    timings = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = decode_png(data)
        timings.append(time.perf_counter() - start)
    return sum(timings) / len(timings), min(timings), result
