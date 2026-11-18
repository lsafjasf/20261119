#!/usr/bin/env python3
"""Convert uncompressed RIFF/WAVE and RF64 files without rewriting audio."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Iterable, Sequence


UINT32_MAX = 0xFFFFFFFF
RIFF_ID = b"RIFF"
RF64_ID = b"RF64"
WAVE_ID = b"WAVE"
DATA_ID = b"data"
DS64_ID = b"ds64"
COPY_BLOCK_SIZE = 4 * 1024 * 1024
DEFAULT_VERIFY_BYTES = 4096


class WavError(Exception):
    """Raised when an input is not a safe, consistent WAVE container."""


@dataclass(frozen=True)
class Ds64Info:
    offset: int
    size: int
    riff_size: int
    data_size: int
    sample_count: int
    table_length: int
    table: tuple[tuple[bytes, int], ...]


@dataclass(frozen=True)
class Chunk:
    index: int
    chunk_id: bytes
    offset: int
    size_field: int
    size: int
    data_offset: int
    pad_size: int
    size_source: str

    @property
    def total_size(self) -> int:
        return 8 + self.size + self.pad_size

    @property
    def data_end(self) -> int:
        return self.data_offset + self.size


@dataclass(frozen=True)
class WaveInfo:
    path: str
    container: str
    file_size: int
    riff_size: int
    riff_end: int
    ds64: Ds64Info | None
    chunks: tuple[Chunk, ...]
    audio_format: int | None
    channels: int | None
    sample_rate: int | None
    block_align: int | None
    bits_per_sample: int | None


@dataclass
class OutputPlan:
    container: str
    total_size: int
    chunks: list[Chunk] = field(default_factory=list)
    source_chunks: list[Chunk | None] = field(default_factory=list)
    ds64: Ds64Info | None = None
    ds64_payload: bytes | None = None


def _u16(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _chunk_id_text(chunk_id: bytes) -> str:
    return chunk_id.decode("ascii", errors="backslashreplace")


def _pread_exact(fd: int, size: int, offset: int, what: str) -> bytes:
    if size < 0 or offset < 0:
        raise WavError(f"invalid read request for {what}")
    parts: list[bytes] = []
    remaining = size
    cursor = offset
    while remaining:
        data = os.pread(fd, min(remaining, COPY_BLOCK_SIZE), cursor)
        if not data:
            break
        parts.append(data)
        cursor += len(data)
        remaining -= len(data)
    if remaining:
        found = size - remaining
        raise WavError(
            f"{what} at offset {offset} is truncated "
            f"(need {size} bytes, found {found})"
        )
    return b"".join(parts)


def _check_riff_end(container: str, riff_end: int, file_size: int) -> None:
    if riff_end > file_size:
        raise WavError(
            f"{container} declared size ends at byte {riff_end}, "
            f"past input EOF at byte {file_size}"
        )


def _read_ds64(fd: int, offset: int, size_field: int, riff_end: int) -> Ds64Info:
    if size_field == UINT32_MAX:
        raise WavError("ds64 chunk size cannot use the 0xffffffff placeholder")
    if size_field < 28:
        raise WavError(f"ds64 chunk at offset {offset} is shorter than 28 bytes")
    data_offset = offset + 8
    if data_offset + size_field > riff_end:
        raise WavError(
            f"ds64 chunk at offset {offset} declares {size_field} bytes, "
            "past the RF64 container end"
        )

    fixed = _pread_exact(fd, 28, data_offset, "ds64 header")
    riff_size = _u64(fixed, 0)
    data_size = _u64(fixed, 8)
    sample_count = _u64(fixed, 16)
    table_length = _u32(fixed, 24)
    table_bytes = 12 * table_length
    if 28 + table_bytes > size_field:
        raise WavError(
            f"ds64 table needs {28 + table_bytes} bytes, "
            f"but its chunk contains only {size_field}"
        )
    table_entries: list[tuple[bytes, int]] = []
    entries_per_read = 4096
    for entry_start in range(0, table_length, entries_per_read):
        entry_count = min(entries_per_read, table_length - entry_start)
        raw_table = _pread_exact(
            fd,
            12 * entry_count,
            data_offset + 28 + 12 * entry_start,
            "ds64 table",
        )
        table_entries.extend(
            (raw_table[pos : pos + 4], _u64(raw_table, pos + 4))
            for pos in range(0, len(raw_table), 12)
        )
    table = tuple(table_entries)
    return Ds64Info(
        offset=offset,
        size=size_field,
        riff_size=riff_size,
        data_size=data_size,
        sample_count=sample_count,
        table_length=table_length,
        table=table,
    )


def parse_wave(path: str) -> WaveInfo:
    """Parse chunk boundaries and reject any declaration crossing EOF."""

    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise WavError(f"cannot open input {path!r}: {exc}") from exc
    try:
        file_size = os.fstat(fd).st_size
        header = _pread_exact(fd, 12, 0, "RIFF/RF64 file header")
        magic = header[:4]
        form = header[8:12]
        if magic not in (RIFF_ID, RF64_ID):
            raise WavError("input is not a RIFF or RF64 file")
        if form != WAVE_ID:
            raise WavError(f"unsupported RIFF form {_chunk_id_text(form)!r}")

        container = "RF64" if magic == RF64_ID else "RIFF"
        declared32 = _u32(header, 4)
        ds64: Ds64Info | None = None
        first_chunk_offset = 12

        if container == "RF64":
            if declared32 != UINT32_MAX:
                raise WavError(
                    "RF64 header must use 0xffffffff as its RIFF size field"
                )
            if file_size < 20:
                raise WavError("RF64 header has no ds64 chunk")
            ds64_id, ds64_size = struct.unpack(
                "<4sI", _pread_exact(fd, 8, 12, "ds64 chunk header")
            )
            if ds64_id != DS64_ID:
                raise WavError("RF64 file must start with a ds64 chunk")
            # The provisional end is the real EOF until ds64 gives the boundary.
            ds64 = _read_ds64(fd, 12, ds64_size, file_size)
            riff_size = ds64.riff_size
            riff_end = 8 + riff_size
            _check_riff_end(container, riff_end, file_size)
            if ds64.offset + 8 + ds64.size > riff_end:
                raise WavError("ds64 chunk extends past the RF64 container end")
            first_chunk_offset = ds64.offset + 8 + ds64.size + (ds64.size & 1)
            if first_chunk_offset > riff_end:
                raise WavError("ds64 padding extends past the RF64 container end")
        else:
            riff_size = declared32
            riff_end = 8 + riff_size
            _check_riff_end(container, riff_end, file_size)

        chunks: list[Chunk] = []
        fmt_payload: bytes | None = None
        table_uses: dict[bytes, int] = {}
        offset = first_chunk_offset
        index = 0

        if ds64 is not None:
            ds64_chunk = Chunk(
                index=index,
                chunk_id=DS64_ID,
                offset=ds64.offset,
                size_field=ds64.size,
                size=ds64.size,
                data_offset=ds64.offset + 8,
                pad_size=ds64.size & 1,
                size_source="field",
            )
            chunks.append(ds64_chunk)
            index += 1

        while offset < riff_end:
            if riff_end - offset < 8:
                raise WavError(
                    f"truncated chunk header at offset {offset}: "
                    f"only {riff_end - offset} bytes remain"
                )
            chunk_id, size_field = struct.unpack(
                "<4sI", _pread_exact(fd, 8, offset, "chunk header")
            )
            if chunk_id == DS64_ID:
                raise WavError(
                    f"unexpected ds64 chunk at offset {offset}; "
                    "RF64 requires exactly one leading ds64 chunk"
                )
            data_offset = offset + 8
            size_source = "field"

            if size_field == UINT32_MAX and container == "RF64":
                if chunk_id == DATA_ID:
                    size = ds64.data_size  # type: ignore[union-attr]
                    size_source = "ds64:data"
                else:
                    occurrence = table_uses.get(chunk_id, 0)
                    matches = [
                        value
                        for item_id, value in ds64.table  # type: ignore[union-attr]
                        if item_id == chunk_id
                    ]
                    if occurrence >= len(matches):
                        raise WavError(
                            f"chunk {_chunk_id_text(chunk_id)!r} at offset "
                            f"{offset} uses a 64-bit size missing from ds64"
                        )
                    size = matches[occurrence]
                    table_uses[chunk_id] = occurrence + 1
                    size_source = "ds64:table"
            else:
                size = size_field

            data_end = data_offset + size
            if data_end > riff_end:
                raise WavError(
                    f"chunk {_chunk_id_text(chunk_id)!r} at offset {offset} "
                    f"declares data through byte {data_end}, past the container "
                    f"end at byte {riff_end}"
                )
            if size & 1:
                pad_size = 1 if data_end < riff_end else 0
            else:
                pad_size = 0
            if data_end + pad_size > riff_end:
                raise WavError(
                    f"chunk {_chunk_id_text(chunk_id)!r} at offset {offset} "
                    "has no room for its required pad byte"
                )

            chunk = Chunk(
                index=index,
                chunk_id=chunk_id,
                offset=offset,
                size_field=size_field,
                size=size,
                data_offset=data_offset,
                pad_size=pad_size,
                size_source=size_source,
            )
            chunks.append(chunk)
            if chunk_id == b"fmt " and fmt_payload is None:
                fmt_payload = _pread_exact(
                    fd, min(size, 64 * 1024), data_offset, "fmt chunk"
                )
            offset = data_end + pad_size
            index += 1

        if offset != riff_end:
            raise WavError(f"chunk scan ended at {offset}, expected {riff_end}")
    finally:
        os.close(fd)

    audio_format = channels = sample_rate = block_align = bits = None
    if fmt_payload is not None and len(fmt_payload) >= 16:
        audio_format = _u16(fmt_payload, 0)
        channels = _u16(fmt_payload, 2)
        sample_rate = _u32(fmt_payload, 4)
        block_align = _u16(fmt_payload, 12)
        bits = _u16(fmt_payload, 14)

    return WaveInfo(
        path=path,
        container=container,
        file_size=file_size,
        riff_size=riff_size,
        riff_end=riff_end,
        ds64=ds64,
        chunks=tuple(chunks),
        audio_format=audio_format,
        channels=channels,
        sample_rate=sample_rate,
        block_align=block_align,
        bits_per_sample=bits,
    )


def _kept_chunks(info: WaveInfo) -> list[Chunk]:
    return [chunk for chunk in info.chunks if chunk.chunk_id != DS64_ID]


def _rf64_table_entries(chunks: Iterable[Chunk]) -> list[tuple[bytes, int]]:
    return [
        (chunk.chunk_id, chunk.size)
        for chunk in chunks
        if chunk.chunk_id != DATA_ID and chunk.size >= UINT32_MAX
    ]


def _planned_total(info: WaveInfo, container: str) -> int:
    chunks = _kept_chunks(info)
    total = 12 + sum(8 + chunk.size + chunk.pad_size for chunk in chunks)
    if container == "RF64":
        total += 8 + 28 + 12 * len(_rf64_table_entries(chunks))
    return total


def _choose_container(info: WaveInfo, requested: str) -> str:
    if requested == "auto":
        return info.container
    if requested == "rf64":
        return "RF64"
    if requested != "riff":
        raise WavError(f"unknown target container {requested!r}")
    total = _planned_total(info, "RIFF")
    if total - 8 > UINT32_MAX:
        raise WavError(
            f"RIFF cannot hold the converted file ({total} bytes); "
            "choose RF64 instead"
        )
    for chunk in _kept_chunks(info):
        if chunk.size >= UINT32_MAX:
            raise WavError(
                f"RIFF cannot encode the {_chunk_id_text(chunk.chunk_id)!r} "
                "chunk without a ds64 table"
            )
    return "RIFF"


def _sample_count(info: WaveInfo, data_size: int) -> int:
    if info.block_align and info.block_align > 0 and data_size % info.block_align == 0:
        return data_size // info.block_align
    if info.ds64 is not None:
        return info.ds64.sample_count
    return 0


def build_output_plan(info: WaveInfo, requested: str) -> OutputPlan:
    container = _choose_container(info, requested)
    source_chunks = _kept_chunks(info)
    data_size = sum(
        chunk.size for chunk in source_chunks if chunk.chunk_id == DATA_ID
    )
    total = _planned_total(info, container)
    table = _rf64_table_entries(source_chunks) if container == "RF64" else []
    ds64_payload: bytes | None = None
    ds64_info: Ds64Info | None = None

    if container == "RF64":
        ds64_payload = (
            struct.pack(
                "<QQQI",
                total - 8,
                data_size,
                _sample_count(info, data_size),
                len(table),
            )
            + b"".join(
                chunk_id + struct.pack("<Q", size) for chunk_id, size in table
            )
        )
        ds64_info = Ds64Info(
            offset=12,
            size=len(ds64_payload),
            riff_size=total - 8,
            data_size=data_size,
            sample_count=_sample_count(info, data_size),
            table_length=len(table),
            table=tuple(table),
        )

    plan = OutputPlan(
        container=container,
        total_size=total,
        ds64=ds64_info,
        ds64_payload=ds64_payload,
    )
    offset = 12
    if ds64_info is not None and ds64_payload is not None:
        plan.chunks.append(
            Chunk(
                index=0,
                chunk_id=DS64_ID,
                offset=offset,
                size_field=len(ds64_payload),
                size=len(ds64_payload),
                data_offset=offset + 8,
                pad_size=0,
                size_source="generated",
            )
        )
        plan.source_chunks.append(None)
        offset += 8 + len(ds64_payload)

    for source in source_chunks:
        size_field = (
            UINT32_MAX
            if container == "RF64" and source.size >= UINT32_MAX
            else source.size
        )
        if size_field != source.size:
            size_source = "ds64:data" if source.chunk_id == DATA_ID else "ds64:table"
        else:
            size_source = "field"
        chunk = Chunk(
            index=len(plan.chunks),
            chunk_id=source.chunk_id,
            offset=offset,
            size_field=size_field,
            size=source.size,
            data_offset=offset + 8,
            pad_size=source.pad_size,
            size_source=size_source,
        )
        plan.chunks.append(chunk)
        plan.source_chunks.append(source)
        offset += chunk.total_size

    if offset != total:
        raise WavError(
            f"internal output plan mismatch: planned {total} bytes, got {offset}"
        )
    return plan


def _pwrite_all(fd: int, data: bytes, offset: int, what: str) -> None:
    written = 0
    while written < len(data):
        count = os.pwrite(fd, data[written:], offset + written)
        if count <= 0:
            raise OSError(f"short write while writing {what}")
        written += count


def _copy_range(
    src_fd: int, dst_fd: int, src_offset: int, dst_offset: int, size: int
) -> None:
    done = 0
    copy_file_range = getattr(os, "copy_file_range", None)
    if copy_file_range is not None:
        while done < size:
            try:
                count = copy_file_range(
                    src_fd,
                    dst_fd,
                    min(COPY_BLOCK_SIZE, size - done),
                    src_offset + done,
                    dst_offset + done,
                )
            except OSError as exc:
                if exc.errno not in (
                    errno.EXDEV,
                    errno.EINVAL,
                    errno.ENOSYS,
                    errno.EOPNOTSUPP,
                ):
                    raise
                break
            if count <= 0:
                break
            done += count

    while done < size:
        block = os.pread(
            src_fd, min(COPY_BLOCK_SIZE, size - done), src_offset + done
        )
        if not block:
            raise WavError(
                f"input ended while copying {size} bytes at offset {src_offset}"
            )
        _pwrite_all(dst_fd, block, dst_offset + done, "audio payload")
        done += len(block)


def _copy_zero_aware(
    src_fd: int, dst_fd: int, src_offset: int, dst_offset: int, size: int
) -> None:
    done = 0
    while done < size:
        block = os.pread(
            src_fd, min(COPY_BLOCK_SIZE, size - done), src_offset + done
        )
        if not block:
            raise WavError(
                f"input ended while copying {size} bytes at offset {src_offset}"
            )
        if block.count(0) != len(block):
            _pwrite_all(dst_fd, block, dst_offset + done, "audio payload")
        done += len(block)


def copy_sparse_range(
    src_fd: int, dst_fd: int, src_offset: int, dst_offset: int, size: int
) -> None:
    """Copy logical bytes, leaving input holes as holes in the output."""

    if size <= 0:
        return
    end = src_offset + size
    cursor = src_offset
    seek_data = getattr(os, "SEEK_DATA", None)
    seek_hole = getattr(os, "SEEK_HOLE", None)
    if seek_data is None or seek_hole is None:
        _copy_zero_aware(src_fd, dst_fd, src_offset, dst_offset, size)
        return

    while cursor < end:
        try:
            data_start = os.lseek(src_fd, cursor, seek_data)
        except OSError as exc:
            if exc.errno == errno.ENXIO:
                break
            if exc.errno in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                _copy_zero_aware(src_fd, dst_fd, src_offset, dst_offset, size)
                return
            raise
        if data_start >= end:
            break
        data_start = max(cursor, data_start)
        try:
            hole_start = os.lseek(src_fd, data_start, seek_hole)
        except OSError as exc:
            if exc.errno in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                _copy_zero_aware(src_fd, dst_fd, src_offset, dst_offset, size)
                return
            raise
        extent_end = min(end, hole_start)
        _copy_range(
            src_fd,
            dst_fd,
            data_start,
            dst_offset + (data_start - src_offset),
            extent_end - data_start,
        )
        cursor = extent_end


def _write_output(src_fd: int, dst_fd: int, plan: OutputPlan) -> None:
    if plan.container == "RF64":
        _pwrite_all(
            dst_fd,
            RF64_ID + struct.pack("<I", UINT32_MAX) + WAVE_ID,
            0,
            "RF64 header",
        )
    else:
        _pwrite_all(
            dst_fd,
            RIFF_ID + struct.pack("<I", plan.total_size - 8) + WAVE_ID,
            0,
            "RIFF header",
        )

    for output, source in zip(plan.chunks, plan.source_chunks):
        if source is None:
            if plan.ds64_payload is None:
                raise WavError("internal error: missing generated ds64 payload")
            _pwrite_all(
                dst_fd,
                DS64_ID + struct.pack("<I", len(plan.ds64_payload)),
                output.offset,
                "ds64 header",
            )
            _pwrite_all(
                dst_fd,
                plan.ds64_payload,
                output.data_offset,
                "ds64 payload",
            )
            continue

        _pwrite_all(
            dst_fd,
            source.chunk_id + struct.pack("<I", output.size_field),
            output.offset,
            f"{_chunk_id_text(source.chunk_id)} chunk header",
        )
        copy_sparse_range(
            src_fd,
            dst_fd,
            source.data_offset,
            output.data_offset,
            source.size,
        )
        if source.pad_size:
            copy_sparse_range(
                src_fd,
                dst_fd,
                source.data_end,
                output.data_offset + source.size,
                source.pad_size,
            )

    os.ftruncate(dst_fd, plan.total_size)
    os.fsync(dst_fd)


def _validate_output_shape(actual: WaveInfo, plan: OutputPlan) -> None:
    if actual.container != plan.container:
        raise WavError(
            f"output container is {actual.container}, expected {plan.container}"
        )
    if actual.file_size != plan.total_size or actual.riff_end != plan.total_size:
        raise WavError(
            f"output size is {actual.file_size}, expected {plan.total_size}"
        )
    if actual.riff_size != plan.total_size - 8:
        raise WavError("output RIFF/RF64 size field does not match file size")
    if len(actual.chunks) != len(plan.chunks):
        raise WavError("output chunk count changed unexpectedly")
    for number, (got, expected) in enumerate(zip(actual.chunks, plan.chunks)):
        if (
            got.chunk_id != expected.chunk_id
            or got.offset != expected.offset
            or got.size != expected.size
            or got.pad_size != expected.pad_size
        ):
            raise WavError(f"output chunk {number} does not match the plan")
    if plan.container == "RF64":
        if actual.ds64 is None or plan.ds64 is None:
            raise WavError("output is missing its ds64 chunk")
        if actual.ds64.riff_size != plan.ds64.riff_size:
            raise WavError("output ds64 RIFF size is inconsistent")
        if actual.ds64.data_size != plan.ds64.data_size:
            raise WavError("output ds64 data size is inconsistent")
        if actual.ds64.table != plan.ds64.table:
            raise WavError("output ds64 size table is inconsistent")
    elif any(chunk.chunk_id == DS64_ID for chunk in actual.chunks):
        raise WavError("RIFF output unexpectedly contains a ds64 chunk")


def _payload_edge_checks(
    source: WaveInfo, output: WaveInfo, sample_bytes: int
) -> dict[str, object]:
    if sample_bytes < 1:
        raise WavError("verification sample size must be positive")
    source_data = [chunk for chunk in source.chunks if chunk.chunk_id == DATA_ID]
    output_data = [chunk for chunk in output.chunks if chunk.chunk_id == DATA_ID]
    if len(source_data) != len(output_data):
        raise WavError("data chunk count changed during conversion")

    checks: list[dict[str, object]] = []
    src_fd = os.open(source.path, os.O_RDONLY)
    out_fd = os.open(output.path, os.O_RDONLY)
    try:
        for number, (before, after) in enumerate(zip(source_data, output_data)):
            if before.size != after.size:
                raise WavError(f"data chunk {number} changed length")
            first_size = min(sample_bytes, before.size)
            last_size = min(sample_bytes, before.size)
            before_first = _pread_exact(
                src_fd, first_size, before.data_offset, "input first payload sample"
            )
            before_last = _pread_exact(
                src_fd,
                last_size,
                before.data_offset + before.size - last_size,
                "input last payload sample",
            )
            after_first = _pread_exact(
                out_fd, first_size, after.data_offset, "output first payload sample"
            )
            after_last = _pread_exact(
                out_fd,
                last_size,
                after.data_offset + after.size - last_size,
                "output last payload sample",
            )
            match = before_first == after_first and before_last == after_last
            checks.append(
                {
                    "data_chunk_index": number,
                    "length": before.size,
                    "first_sample_bytes": first_size,
                    "last_sample_bytes": last_size,
                    "input_first_sample_sha256": hashlib.sha256(
                        before_first
                    ).hexdigest(),
                    "input_last_sample_sha256": hashlib.sha256(
                        before_last
                    ).hexdigest(),
                    "first_sample_sha256": hashlib.sha256(after_first).hexdigest(),
                    "last_sample_sha256": hashlib.sha256(after_last).hexdigest(),
                    "equal": match,
                }
            )
            if not match:
                raise WavError(
                    f"data chunk {number} first/last payload verification failed"
                )
    finally:
        os.close(src_fd)
        os.close(out_fd)

    return {
        "method": "compare first and last payload samples",
        "sample_bytes": sample_bytes,
        "data_chunks": checks,
        "all_equal": all(bool(check["equal"]) for check in checks),
    }


def _chunk_manifest(chunk: Chunk) -> dict[str, object]:
    return {
        "index": chunk.index,
        "id": _chunk_id_text(chunk.chunk_id),
        "id_hex": chunk.chunk_id.hex(),
        "offset": chunk.offset,
        "data_offset": chunk.data_offset,
        "length": chunk.size,
        "header_length": 8,
        "padding": chunk.pad_size,
        "total_length": chunk.total_size,
        "size_field": chunk.size_field,
        "size_source": chunk.size_source,
    }


def _wave_manifest(info: WaveInfo, display_path: str) -> dict[str, object]:
    ds64 = None
    if info.ds64 is not None:
        ds64 = {
            "offset": info.ds64.offset,
            "length": info.ds64.size,
            "riff_size": info.ds64.riff_size,
            "data_size": info.ds64.data_size,
            "sample_count": info.ds64.sample_count,
            "table_length": info.ds64.table_length,
            "table": [
                {
                    "id": _chunk_id_text(chunk_id),
                    "id_hex": chunk_id.hex(),
                    "length": size,
                }
                for chunk_id, size in info.ds64.table
            ],
        }
    return {
        "path": display_path,
        "container": info.container,
        "file_size": info.file_size,
        "riff_size": info.riff_size,
        "chunks": [_chunk_manifest(chunk) for chunk in info.chunks],
        "ds64": ds64,
        "audio": {
            "audio_format": info.audio_format,
            "channels": info.channels,
            "sample_rate": info.sample_rate,
            "block_align": info.block_align,
            "bits_per_sample": info.bits_per_sample,
        },
    }


def _fsync_directory(path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _prepare_json_file(path: str, payload: bytes) -> str:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory
    )
    try:
        _pwrite_all(fd, payload, 0, "manifest")
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    os.close(fd)
    return temporary


def _same_path(left: str, right: str) -> bool:
    return os.path.abspath(left) == os.path.abspath(right)


def convert(
    input_path: str,
    output_path: str,
    requested_format: str = "auto",
    manifest_path: str | None = None,
    verify_bytes: int = DEFAULT_VERIFY_BYTES,
) -> tuple[dict[str, object], bytes | None]:
    """Convert into a temporary file, verify it, then atomically replace output."""

    if output_path == "-" or input_path == "-":
        raise WavError("standard input/output are not supported for atomic conversion")
    if manifest_path not in (None, "-") and (
        _same_path(manifest_path, output_path)
        or _same_path(manifest_path, input_path)
    ):
        raise WavError("manifest path must differ from input and output paths")

    source = parse_wave(input_path)
    plan = build_output_plan(source, requested_format)
    output_directory = os.path.dirname(os.path.abspath(output_path)) or "."
    basename = os.path.basename(output_path)
    temporary_path: str | None = None
    temporary_fd: int | None = None
    source_fd: int | None = None
    manifest_temporary: str | None = None

    try:
        try:
            temporary_fd, temporary_path = tempfile.mkstemp(
                prefix=f".{basename}.", suffix=".tmp", dir=output_directory
            )
        except OSError as exc:
            raise WavError(
                f"cannot create a temporary output next to {output_path!r}: {exc}"
            ) from exc

        source_fd = os.open(input_path, os.O_RDONLY)
        try:
            os.fchmod(temporary_fd, os.fstat(source_fd).st_mode & 0o777)
        except OSError:
            pass
        try:
            _write_output(source_fd, temporary_fd, plan)
        finally:
            os.close(source_fd)
            source_fd = None
            os.close(temporary_fd)
            temporary_fd = None

        actual = parse_wave(temporary_path)
        _validate_output_shape(actual, plan)
        verification = _payload_edge_checks(source, actual, verify_bytes)
        manifest: dict[str, object] = {
            "schema": "rf64conv.manifest.v1",
            "before": _wave_manifest(source, input_path),
            "after": _wave_manifest(actual, output_path),
            "verification": verification,
        }
        manifest_bytes = (
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

        if manifest_path not in (None, "-"):
            manifest_temporary = _prepare_json_file(manifest_path, manifest_bytes)

        os.replace(temporary_path, output_path)
        temporary_path = None
        _fsync_directory(output_path)

        if manifest_temporary is not None and manifest_path is not None:
            os.replace(manifest_temporary, manifest_path)
            manifest_temporary = None
            _fsync_directory(manifest_path)
        return manifest, manifest_bytes
    except BaseException:
        if source_fd is not None:
            os.close(source_fd)
        if temporary_fd is not None:
            os.close(temporary_fd)
        for path in (temporary_path, manifest_temporary):
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        raise


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert uncompressed RIFF/WAVE and RF64 containers while copying "
            "all chunk payloads and preserving holes, unknown chunks and padding."
        )
    )
    parser.add_argument("input", help="source RIFF/WAVE or RF64 file")
    parser.add_argument("output", help="destination file (replaced atomically)")
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument(
        "--format",
        choices=("auto", "riff", "rf64"),
        default="auto",
        help=(
            "target header: auto preserves the input container; "
            "riff requires every size to fit (default: auto)"
        ),
    )
    format_group.add_argument(
        "--to-rf64",
        dest="format",
        action="store_const",
        const="rf64",
        help="write an RF64 header and a fresh ds64 chunk",
    )
    format_group.add_argument(
        "--to-riff",
        dest="format",
        action="store_const",
        const="riff",
        help="write a plain RIFF/WAVE header (fails if a size does not fit)",
    )
    manifest_group = parser.add_mutually_exclusive_group()
    manifest_group.add_argument(
        "--manifest",
        metavar="PATH",
        help="JSON chunk manifest path, or '-' for stdout "
        "(default: OUTPUT.chunks.json)",
    )
    manifest_group.add_argument(
        "--no-manifest",
        action="store_true",
        help="do not write a chunk manifest",
    )
    parser.add_argument(
        "--verify-bytes",
        type=int,
        default=DEFAULT_VERIFY_BYTES,
        metavar="N",
        help=(
            "bytes to compare at each end of every data chunk "
            f"(default: {DEFAULT_VERIFY_BYTES})"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the success summary on stderr",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)
    if args.verify_bytes < 1:
        parser.error("--verify-bytes must be positive")
    if args.no_manifest:
        manifest_path = None
    elif args.manifest is not None:
        manifest_path = args.manifest
    else:
        manifest_path = args.output + ".chunks.json"

    try:
        manifest, manifest_bytes = convert(
            args.input,
            args.output,
            requested_format=args.format,
            manifest_path=manifest_path,
            verify_bytes=args.verify_bytes,
        )
    except WavError as exc:
        print(f"rf64conv: error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"rf64conv: error: {exc}", file=sys.stderr)
        return 1

    if manifest_path == "-" and manifest_bytes is not None:
        sys.stdout.buffer.write(manifest_bytes)
        sys.stdout.buffer.flush()
    if not args.quiet:
        before = manifest["before"]
        after = manifest["after"]
        assert isinstance(before, dict) and isinstance(after, dict)
        print(
            f"{before['container']} -> {after['container']}, "
            f"{after['file_size']} bytes, {len(after['chunks'])} chunks"
            + (
                ""
                if manifest_path in (None, "-")
                else f"; manifest: {manifest_path}"
            ),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
