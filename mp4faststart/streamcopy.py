"""Bounded-memory file range copying with sparse-hole preservation."""

from __future__ import annotations

import errno
import os
from typing import BinaryIO

COPY_CHUNK = 1024 * 1024
_SEEK_FALLBACK_ERRNOS = {
    getattr(errno, "EINVAL", 22),
    getattr(errno, "ENOTSUP", 95),
    getattr(errno, "EOPNOTSUPP", 95),
}


def copy_range(src_fd: int, dst: BinaryIO, src_offset: int, length: int) -> None:
    """Copy ``length`` bytes at ``src_offset`` to the current destination.

    Data extents discovered with ``SEEK_DATA``/``SEEK_HOLE`` are copied in
    bounded chunks.  Hole extents are reproduced as holes, which keeps sparse
    inputs sparse and avoids allocating multi-gigabyte zero regions.
    """
    if length < 0:
        raise ValueError("negative copy length")
    dest_start = dst.tell()
    if length == 0:
        return
    end = src_offset + length
    if _copy_sparse_extents(src_fd, dst, src_offset, end, dest_start):
        dst.seek(dest_start + length)
        return
    dst.seek(dest_start)
    _copy_plain(src_fd, dst, src_offset, length)


def _copy_sparse_extents(
    src_fd: int, dst: BinaryIO, start: int, end: int, dest_start: int
) -> bool:
    seek_data = getattr(os, "SEEK_DATA", None)
    seek_hole = getattr(os, "SEEK_HOLE", None)
    if seek_data is None or seek_hole is None:
        return False
    position = start
    while position < end:
        try:
            data_offset = os.lseek(src_fd, position, seek_data)
        except OSError as exc:
            if exc.errno == errno.ENXIO:
                break
            if exc.errno in _SEEK_FALLBACK_ERRNOS:
                return False
            raise
        if data_offset >= end:
            break
        try:
            hole_offset = os.lseek(src_fd, data_offset, seek_hole)
        except OSError as exc:
            if exc.errno in _SEEK_FALLBACK_ERRNOS:
                return False
            raise
        data_end = min(hole_offset, end)
        dst.seek(dest_start + (data_offset - start))
        _copy_plain(src_fd, dst, data_offset, data_end - data_offset)
        position = data_end
    return True


def _copy_plain(src_fd: int, dst: BinaryIO, src_offset: int, length: int) -> None:
    remaining = length
    position = src_offset
    while remaining:
        chunk = os.pread(src_fd, min(COPY_CHUNK, remaining), position)
        if not chunk:
            raise OSError(errno.EIO, "unexpected EOF while copying MP4 data")
        dst.write(chunk)
        position += len(chunk)
        remaining -= len(chunk)
