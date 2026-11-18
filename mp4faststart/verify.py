"""Independent sample-table parser used to verify relayout output.

This module deliberately does not import :mod:`mp4faststart.boxes` or the
relayout writer.  It reads the sample tables from scratch and compares sample
payload byte ranges directly, which catches both stale chunk offsets and
corrupted media payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import struct
from typing import BinaryIO, Iterable

from .errors import MP4Error, VerificationError

COMPARE_CHUNK = 1024 * 1024
CONTAINER_TYPES = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


@dataclass(frozen=True)
class SampleRef:
    track_id: int
    sample_index: int
    offset: int
    size: int


@dataclass(frozen=True)
class TrackSamples:
    track_id: int
    samples: tuple[SampleRef, ...]


@dataclass(frozen=True)
class _TopBox:
    type: str
    offset: int
    size: int
    header_size: int


@dataclass(frozen=True)
class _Box:
    type: str
    header_size: int
    size: int
    raw: bytes

    @property
    def payload(self) -> bytes:
        return self.raw[self.header_size :]


def compare_sample_bytes(
    original: os.PathLike[str] | str, rebuilt: os.PathLike[str] | str
) -> dict[str, object]:
    """Parse both files independently and compare every sample byte."""
    original_tracks = read_samples(original)
    rebuilt_tracks = read_samples(rebuilt)
    original_by_id = {track.track_id: track for track in original_tracks}
    rebuilt_by_id = {track.track_id: track for track in rebuilt_tracks}
    if len(original_by_id) != len(original_tracks):
        raise VerificationError("duplicate track IDs in original file")
    if len(rebuilt_by_id) != len(rebuilt_tracks):
        raise VerificationError("duplicate track IDs in rebuilt file")
    if set(original_by_id) != set(rebuilt_by_id):
        raise VerificationError(
            f"track IDs differ: original={sorted(original_by_id)}, "
            f"rebuilt={sorted(rebuilt_by_id)}"
        )

    byte_count = 0
    sample_count = 0
    track_rows: list[dict[str, int]] = []
    with open(original, "rb") as original_stream, open(rebuilt, "rb") as rebuilt_stream:
        for track_id in sorted(original_by_id):
            before = original_by_id[track_id]
            after = rebuilt_by_id[track_id]
            if len(before.samples) != len(after.samples):
                raise VerificationError(
                    f"track {track_id} sample count differs: "
                    f"{len(before.samples)} != {len(after.samples)}"
                )
            track_bytes = 0
            for expected, actual in zip(before.samples, after.samples):
                if expected.size != actual.size:
                    raise VerificationError(
                        f"track {track_id} sample {expected.sample_index} size differs: "
                        f"{expected.size} != {actual.size}"
                    )
                if not _equal_range(
                    original_stream,
                    rebuilt_stream,
                    expected.offset,
                    actual.offset,
                    expected.size,
                ):
                    raise VerificationError(
                        f"track {track_id} sample {expected.sample_index} bytes differ"
                    )
                track_bytes += expected.size
            byte_count += track_bytes
            sample_count += len(before.samples)
            track_rows.append(
                {
                    "track_id": track_id,
                    "samples": len(before.samples),
                    "bytes": track_bytes,
                }
            )
    return {
        "ok": True,
        "track_count": len(track_rows),
        "sample_count": sample_count,
        "byte_count": byte_count,
        "tracks": track_rows,
    }


def read_samples(path: os.PathLike[str] | str) -> list[TrackSamples]:
    """Read sample byte locations using only the ISO BMFF sample tables."""
    file_path = Path(path)
    with open(file_path, "rb") as stream:
        stream.seek(0, os.SEEK_END)
        file_size = stream.tell()
        top_level = list(_read_top_boxes(stream, file_size))
    moov = [box for box in top_level if box.type == "moov"]
    if len(moov) != 1:
        raise MP4Error(f"expected one moov box in {file_path}, found {len(moov)}")
    moov_box = moov[0]
    with open(file_path, "rb") as stream:
        stream.seek(moov_box.offset)
        moov_raw = stream.read(moov_box.size)
    if len(moov_raw) != moov_box.size:
        raise MP4Error("truncated moov box while reading samples")
    root_boxes = _parse_tree(moov_raw)
    if len(root_boxes) != 1 or root_boxes[0].type != "moov":
        raise MP4Error("invalid moov box")
    tracks: list[TrackSamples] = []
    for track_index, trak in enumerate(_children(root_boxes[0], "trak"), start=1):
        tracks.append(_read_track(trak, track_index, file_size))
    return tracks


def _read_top_boxes(stream: BinaryIO, file_size: int) -> Iterable[_TopBox]:
    offset = 0
    while offset < file_size:
        if file_size - offset < 8:
            raise MP4Error("trailing bytes after the last top-level box")
        stream.seek(offset)
        header = stream.read(8)
        if len(header) != 8:
            raise MP4Error(f"truncated top-level box header at {offset}")
        size32 = struct.unpack_from(">I", header)[0]
        box_type = header[4:8].decode("latin-1")
        header_size = 8
        if size32 == 1:
            extra = stream.read(8)
            if len(extra) != 8:
                raise MP4Error(f"truncated extended-size box at {offset}")
            size = struct.unpack(">Q", extra)[0]
            header_size = 16
        elif size32 == 0:
            size = file_size - offset
        else:
            size = size32
        if size < header_size or offset + size > file_size:
            raise MP4Error(f"invalid {box_type!r} box size {size} at {offset}")
        yield _TopBox(box_type, offset, size, header_size)
        offset += size


def _parse_tree(data: bytes) -> list[_Box]:
    boxes: list[_Box] = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 8:
            raise MP4Error("trailing bytes inside a container box")
        size32 = struct.unpack_from(">I", data, offset)[0]
        box_type = data[offset + 4 : offset + 8].decode("latin-1")
        header_size = 8
        if size32 == 1:
            if len(data) - offset < 16:
                raise MP4Error("truncated extended-size child box")
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header_size = 16
        elif size32 == 0:
            size = len(data) - offset
        else:
            size = size32
        if size < header_size or offset + size > len(data):
            raise MP4Error(f"invalid child box {box_type!r} size {size}")
        boxes.append(_Box(box_type, header_size, size, data[offset : offset + size]))
        offset += size
    return boxes


def _children(container: _Box, box_type: str) -> list[_Box]:
    if container.type.encode("latin-1") not in CONTAINER_TYPES:
        return []
    return [box for box in _parse_tree(container.payload) if box.type == box_type]


def _one_child(container: _Box, box_type: str) -> _Box | None:
    matches = _children(container, box_type)
    if len(matches) > 1:
        raise MP4Error(f"{container.type} has multiple {box_type} children")
    return matches[0] if matches else None


def _read_track(trak: _Box, track_index: int, file_size: int) -> TrackSamples:
    track_id = _track_id(trak, track_index)
    mdia = _one_child(trak, "mdia")
    minf = _one_child(mdia, "minf") if mdia is not None else None
    stbl = _one_child(minf, "stbl") if minf is not None else None
    if stbl is None:
        return TrackSamples(track_id, ())
    stsz = _one_child(stbl, "stsz")
    stz2 = _one_child(stbl, "stz2")
    if stsz is not None and stz2 is not None:
        raise MP4Error(f"track {track_id} has both stsz and stz2")
    sample_sizes = _sample_sizes(stsz) if stsz is not None else (
        _compact_sample_sizes(stz2) if stz2 is not None else []
    )
    stsc = _one_child(stbl, "stsc")
    if stsc is None:
        if not sample_sizes:
            return TrackSamples(track_id, ())
        raise MP4Error(f"track {track_id} has samples but no stsc")
    chunk_box = _one_child(stbl, "stco")
    if chunk_box is None:
        chunk_box = _one_child(stbl, "co64")
    if chunk_box is None:
        raise MP4Error(f"track {track_id} has no stco or co64")
    chunk_offsets = _chunk_offsets(chunk_box)
    entries = _stsc_entries(stsc)
    samples = _expand_samples(
        track_id, sample_sizes, entries, chunk_offsets, file_size
    )
    return TrackSamples(track_id, tuple(samples))


def _track_id(trak: _Box, fallback: int) -> int:
    tkhd = _one_child(trak, "tkhd")
    if tkhd is None:
        return fallback
    payload = tkhd.payload
    if len(payload) < 4:
        raise MP4Error("truncated tkhd")
    version = payload[0]
    offset = 20 if version == 1 else 12
    if len(payload) < offset + 4:
        raise MP4Error("truncated tkhd track ID")
    return struct.unpack_from(">I", payload, offset)[0]


def _sample_sizes(stsz: _Box) -> list[int]:
    payload = stsz.payload
    if len(payload) < 12:
        raise MP4Error("truncated stsz")
    sample_size, sample_count = struct.unpack_from(">II", payload, 4)
    if sample_size:
        return [sample_size] * sample_count
    expected = 12 + sample_count * 4
    if len(payload) != expected:
        raise MP4Error(
            f"stsz sample count {sample_count} requires {expected} bytes, "
            f"found {len(payload)}"
        )
    return list(struct.unpack_from(">" + "I" * sample_count, payload, 12))


def _compact_sample_sizes(stz2: _Box) -> list[int]:
    payload = stz2.payload
    if len(payload) < 12:
        raise MP4Error("truncated stz2")
    field_size = payload[7]
    sample_count = struct.unpack_from(">I", payload, 8)[0]
    if field_size not in {4, 8, 16}:
        raise MP4Error(f"unsupported stz2 field size {field_size}")
    entry_bytes = (sample_count * field_size + 7) // 8
    if len(payload) < 12 + entry_bytes:
        raise MP4Error("truncated stz2 sample table")
    entries = payload[12 : 12 + entry_bytes]
    sizes: list[int] = []
    if field_size == 4:
        for byte in entries:
            sizes.extend((byte >> 4, byte & 0x0F))
    elif field_size == 8:
        sizes.extend(entries)
    else:
        sizes.extend(
            struct.unpack_from(">" + "H" * (entry_bytes // 2), entries)
        )
    return sizes[:sample_count]


def _stsc_entries(stsc: _Box) -> list[tuple[int, int]]:
    payload = stsc.payload
    if len(payload) < 8:
        raise MP4Error("truncated stsc")
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    expected = 8 + entry_count * 12
    if len(payload) != expected:
        raise MP4Error(
            f"stsc entry count {entry_count} requires {expected} bytes, "
            f"found {len(payload)}"
        )
    result: list[tuple[int, int]] = []
    previous_first = 0
    for index in range(entry_count):
        first_chunk, samples_per_chunk, _description = struct.unpack_from(
            ">III", payload, 8 + index * 12
        )
        if first_chunk < 1 or first_chunk <= previous_first:
            raise MP4Error("stsc first_chunk values must increase from 1")
        if index == 0 and first_chunk != 1:
            raise MP4Error("first stsc entry must start at chunk 1")
        result.append((first_chunk, samples_per_chunk))
        previous_first = first_chunk
    return result


def _chunk_offsets(chunk_box: _Box) -> list[int]:
    payload = chunk_box.payload
    if len(payload) < 8:
        raise MP4Error(f"truncated {chunk_box.type}")
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    width = 4 if chunk_box.type == "stco" else 8
    expected = 8 + entry_count * width
    if len(payload) != expected:
        raise MP4Error(
            f"{chunk_box.type} entry count {entry_count} requires {expected} bytes, "
            f"found {len(payload)}"
        )
    if not entry_count:
        return []
    code = "I" if width == 4 else "Q"
    return list(struct.unpack_from(">" + code * entry_count, payload, 8))


def _expand_samples(
    track_id: int,
    sample_sizes: list[int],
    stsc: list[tuple[int, int]],
    chunk_offsets: list[int],
    file_size: int,
) -> list[SampleRef]:
    if not chunk_offsets and sample_sizes:
        raise MP4Error(f"track {track_id} has samples but no chunks")
    if chunk_offsets and not stsc and sample_sizes:
        raise MP4Error(f"track {track_id} has chunks but no stsc entries")
    samples: list[SampleRef] = []
    sample_index = 0
    stsc_index = 0
    for chunk_number, chunk_offset in enumerate(chunk_offsets, start=1):
        while (
            stsc_index + 1 < len(stsc)
            and stsc[stsc_index + 1][0] <= chunk_number
        ):
            stsc_index += 1
        samples_per_chunk = stsc[stsc_index][1] if stsc else 0
        cursor = chunk_offset
        for _ in range(samples_per_chunk):
            if sample_index >= len(sample_sizes):
                raise MP4Error(
                    f"track {track_id} stsc maps more samples than stsz declares"
                )
            size = sample_sizes[sample_index]
            if cursor + size > file_size:
                raise MP4Error(
                    f"track {track_id} sample {sample_index} exceeds file size"
                )
            samples.append(
                SampleRef(track_id, sample_index, cursor, size)
            )
            cursor += size
            sample_index += 1
    if sample_index != len(sample_sizes):
        raise MP4Error(
            f"track {track_id} stco/stsc cover {sample_index} of "
            f"{len(sample_sizes)} samples"
        )
    return samples


def _equal_range(
    first: BinaryIO,
    second: BinaryIO,
    first_offset: int,
    second_offset: int,
    size: int,
) -> bool:
    remaining = size
    first_position = first_offset
    second_position = second_offset
    first_fd = first.fileno()
    second_fd = second.fileno()
    while remaining:
        amount = min(COMPARE_CHUNK, remaining)
        first_chunk = os.pread(first_fd, amount, first_position)
        second_chunk = os.pread(second_fd, amount, second_position)
        if first_chunk != second_chunk or len(first_chunk) != amount:
            return False
        first_position += amount
        second_position += amount
        remaining -= amount
    return True
