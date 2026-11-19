"""Deterministic synthetic test patterns and a minimal PNG writer.

The writer here is intentionally independent of :mod:`pngopt.png.build_png`
so that test inputs are produced by separate code paths.
"""

from __future__ import annotations

import math
import zlib

from .png import make_chunk


class _Rng:
    """Small deterministic LCG so generated noise is reproducible."""

    def __init__(self, seed: int = 1234567):
        self.state = seed & 0xFFFFFFFF

    def next_u8(self) -> int:
        self.state = (self.state * 1103515245 + 12345) & 0x7FFFFFFF
        return (self.state >> 15) & 0xFF


def _bit_pack(rows: list[list[int]], bit_depth: int, width: int) -> bytes:
    out = bytearray()
    mask = (1 << bit_depth) - 1
    for values in rows:
        buf = 0
        bits = 0
        packed = bytearray()
        for value in values[:width]:
            buf = (buf << bit_depth) | (value & mask)
            bits += bit_depth
            if bits == 8:
                packed.append(buf)
                buf = 0
                bits = 0
        if bits:
            packed.append((buf << (8 - bits)) & 0xFF)
        out.extend(packed)
    return bytes(out)


def write_png(path: str, width: int, height: int, bit_depth: int,
              color_type: int, pixel_rows: list, interlace: int = 0,
              palette: bytes | None = None) -> None:
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    if bit_depth < 8:
        stream = _bit_pack(pixel_rows, bit_depth, width)
        stride = math.ceil(width * bit_depth / 8)
    else:
        stream = bytearray()
        stride = width * channels * (bit_depth // 8)
        for row in pixel_rows:
            stream.extend(bytes(row))
    filtered = bytearray()
    step = stride
    for r in range(height):
        filtered.append(0)  # every input starts with filter type None
        filtered.extend(stream[r * step:(r + 1) * step])

    ihdr = (width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + bytes((bit_depth, color_type, 0, 0, interlace)))
    parts = [b"\x89PNG\r\n\x1a\n", make_chunk(b"IHDR", ihdr)]
    if palette is not None:
        parts.append(make_chunk(b"PLTE", palette))
    # Add an ancillary chunk to prove non-IDAT chunks are preserved.
    parts.append(make_chunk(b"tEXt", b"generator\x00pngopt synthetic"))
    co = zlib.compressobj(9)
    payload = co.compress(bytes(filtered)) + co.flush()
    parts.append(make_chunk(b"IDAT", payload))
    parts.append(make_chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(b"".join(parts))


def gradient_rgb(width: int, height: int) -> list[list[int]]:
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            row.extend((x * 255 // max(1, width - 1),
                        y * 255 // max(1, height - 1),
                        (x + y) * 255 // max(1, width + height - 2)))
        rows.append(row)
    return rows


def noise_rgb(width: int, height: int, seed: int = 42) -> list[list[int]]:
    rng = _Rng(seed)
    return [[rng.next_u8() for _ in range(width * 3)] for _ in range(height)]


def alpha_pattern(width: int, height: int) -> list[list[int]]:
    rng = _Rng(seed=99)
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            checker = ((x // 16) + (y // 16)) % 2
            alpha = 0 if (x < width // 4) else (
                255 if x > 3 * width // 4 else (x * 255 // width))
            row.extend((
                (rng.next_u8() & 0x3F) + checker * 64,
                (y * 255 // max(1, height - 1)),
                (x * 255 // max(1, width - 1)),
                alpha,
            ))
        rows.append(row)
    return rows


def indexed_pattern(width: int, height: int) -> tuple[list[list[int]], bytes]:
    palette = bytes((
        0, 0, 0,  255, 0, 0,  0, 255, 0,  0, 0, 255,
        255, 255, 0,  0, 255, 255,  255, 0, 255,  255, 255, 255,
    ))
    rows = []
    for y in range(height):
        rows.append([(x // 8 + y // 8) % 8 for x in range(width)])
    return rows, palette


def gradient_gray16(width: int, height: int) -> list[list[int]]:
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            value = ((x + y) * 65535 // max(1, width + height - 2))
            row.extend((value >> 8, value & 0xFF))
        rows.append(row)
    return rows


def sub_byte_gray(bit_depth: int, width: int,
                  height: int) -> list[list[int]]:
    levels = 1 << bit_depth
    return [[(x + y) % levels for x in range(width)]
            for y in range(height)]
