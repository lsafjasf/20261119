"""Dependency-free YUV conversion, masking, and PNG output."""

from __future__ import annotations

import struct
import zlib

from .decode import DecodedFrame
from .models import Tile


def _clamp(value: float) -> int:
    return max(0, min(255, int(round(value))))


def yuv420_to_rgb(frame: DecodedFrame) -> bytes:
    if frame.chroma_format != 1 or frame.bytes_per_sample != 1:
        raise ValueError("visual conversion currently expects 8-bit 4:2:0")
    width, height = frame.width, frame.height
    cw = (width + 1) // 2
    ch = (height + 1) // 2
    luma_size = width * height
    chroma_size = cw * ch
    data = frame.data
    rgb = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            luma = data[y * width + x]
            u = data[luma_size + (y // 2) * cw + x // 2]
            v = data[luma_size + chroma_size + (y // 2) * cw + x // 2]
            c = luma - 16
            d = u - 128
            e = v - 128
            offset = (y * width + x) * 3
            rgb[offset] = _clamp((298 * c + 409 * e + 128) / 256)
            rgb[offset + 1] = _clamp((298 * c - 100 * d - 208 * e + 128) / 256)
            rgb[offset + 2] = _clamp((298 * c + 516 * d + 128) / 256)
    return bytes(rgb)


def compose_masked(
    partial: DecodedFrame, tiles: list[Tile], available: set[int]
) -> bytes:
    rgb = bytearray(yuv420_to_rgb(partial))
    width = partial.width
    height = partial.height
    mask = bytearray(width * height)
    for tile in tiles:
        if tile.id not in available:
            continue
        for y in range(tile.y, min(height, tile.y + tile.height)):
            start = y * width + tile.x
            end = start + min(tile.width, width - tile.x)
            mask[start:end] = b"\x01" * (end - start)

    missing_a = (34, 21, 38)
    missing_b = (65, 28, 72)
    for y in range(height):
        row = y * width
        for x in range(width):
            index = row + x
            if mask[index]:
                continue
            color = (
                missing_a
                if ((x + y) // 12) % 2 == 0
                else missing_b
            )
            pixel = index * 3
            rgb[pixel : pixel + 3] = bytes(color)
    return bytes(rgb)


def write_png(path, rgb: bytes, width: int, height: int) -> None:
    if len(rgb) != width * height * 3:
        raise ValueError("RGB buffer does not match the requested dimensions")
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride : (y + 1) * stride])

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    encoded = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(encoded)
