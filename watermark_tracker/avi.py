"""Minimal uncompressed grayscale AVI writer (no external dependencies)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Sequence

from .image import GrayImage


def _chunk(fourcc: bytes, payload: bytes) -> bytes:
    padding = b"\x00" if len(payload) % 2 else b""
    return fourcc + struct.pack("<I", len(payload)) + payload + padding


def _list(list_type: bytes, children: bytes) -> bytes:
    payload = list_type + children
    padding = b"\x00" if len(payload) % 2 else b""
    return b"LIST" + struct.pack("<I", len(payload)) + payload + padding


def _dib_frame(image: GrayImage) -> bytes:
    row_size = (image.width * 3 + 3) & ~3
    padding = b"\x00" * (row_size - image.width * 3)
    rows: list[bytes] = []
    for y in range(image.height - 1, -1, -1):
        start = y * image.width
        row = bytearray()
        for value in image.data[start : start + image.width]:
            row.extend((value, value, value))
        rows.append(bytes(row) + padding)
    return b"".join(rows)


def write_avi(
    path: str | Path,
    images: Sequence[GrayImage],
    fps: int = 25,
) -> None:
    if not images:
        raise ValueError("AVI export needs at least one frame")
    width = images[0].width
    height = images[0].height
    if any(image.width != width or image.height != height for image in images):
        raise ValueError("all AVI frames must have identical dimensions")
    frame_payloads = [_dib_frame(image) for image in images]
    frame_size = len(frame_payloads[0])
    microseconds = max(1, round(1_000_000 / fps))

    avih = struct.pack(
        "<IIIIIIIIII4I",
        microseconds,
        frame_size * fps,
        0,
        0x10,
        len(images),
        0,
        1,
        frame_size,
        width,
        height,
        0,
        0,
        0,
        0,
    )
    strh = struct.pack(
        "<4s4sIHHIIIIIIIIhhhh",
        b"vids",
        b"DIB ",
        0,
        0,
        0,
        0,
        1,
        fps,
        0,
        len(images),
        frame_size,
        0xFFFFFFFF,
        0,
        0,
        0,
        width,
        height,
    )
    strf = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        24,
        0,
        frame_size,
        0,
        0,
        0,
        0,
    )
    stream_list = _list(
        b"strl", _chunk(b"strh", strh) + _chunk(b"strf", strf)
    )
    header_list = _list(
        b"hdrl", _chunk(b"avih", avih) + stream_list
    )

    movi_payload = bytearray()
    index_entries = bytearray()
    offset = 4
    for payload in frame_payloads:
        chunk = _chunk(b"00db", payload)
        index_entries.extend(
            struct.pack("<4sIII", b"00db", 0x10, offset, len(payload))
        )
        movi_payload.extend(chunk)
        offset += len(chunk)
    movi_list = _list(b"movi", bytes(movi_payload))
    index_chunk = _chunk(b"idx1", bytes(index_entries))
    riff_payload = b"AVI " + header_list + movi_list + index_chunk
    output = b"RIFF" + struct.pack("<I", len(riff_payload)) + riff_payload
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)
