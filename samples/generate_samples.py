"""Generate the MP4 fixtures used by the test suite.

The fixtures are intentionally small except for the overflow case, whose media
area is a sparse hole.  None of the generator functions load media payloads
into the relayout process's memory.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct

UINT32_MAX = (1 << 32) - 1


def box(box_type: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    if size > UINT32_MAX:
        raise ValueError("box too large for 32-bit header")
    return struct.pack(">I4s", size, box_type) + payload


def full_box(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(box_type, struct.pack(">I", (version << 24) | flags) + payload)


def ftyp() -> bytes:
    return box(b"ftyp", b"isom" + struct.pack(">I", 0x200) + b"isomiso2mp41")


def mvhd() -> bytes:
    payload = struct.pack(">II", 0, 0) + struct.pack(">I", 1000)
    payload += struct.pack(">I", 0) + b"\x00" * 80
    return full_box(b"mvhd", 0, 0, payload)


def tkhd(track_id: int) -> bytes:
    payload = struct.pack(">II", 0, 0)
    payload += struct.pack(">I", track_id) + b"\x00" * 4
    payload += struct.pack(">I", 0) + b"\x00" * 8
    payload += struct.pack(">hhhh", 0, 0, 0, 0)
    payload += struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    payload += struct.pack(">II", 0, 0)
    return full_box(b"tkhd", 0, 7, payload)


def mdhd() -> bytes:
    payload = struct.pack(">II", 0, 0) + struct.pack(">I", 1000)
    payload += struct.pack(">I", 0) + struct.pack(">H", 0x55C4) + struct.pack(">H", 0)
    return full_box(b"mdhd", 0, 0, payload)


def hdlr(handler: bytes) -> bytes:
    payload = struct.pack(">I", 0) + handler + b"\x00" * 12 + b"handler\x00"
    return full_box(b"hdlr", 0, 0, payload)


def stsd() -> bytes:
    return full_box(b"stsd", 0, 0, struct.pack(">I", 0))


def stts(sample_count: int) -> bytes:
    payload = struct.pack(">I", 1) + struct.pack(">II", sample_count, 1000)
    return full_box(b"stts", 0, 0, payload)


def stsz(sizes: list[int]) -> bytes:
    payload = struct.pack(">II", 0, len(sizes))
    payload += struct.pack(">" + "I" * len(sizes), *sizes)
    return full_box(b"stsz", 0, 0, payload)


def stsc(entries: list[tuple[int, int]]) -> bytes:
    payload = struct.pack(">I", len(entries))
    for first_chunk, samples_per_chunk in entries:
        payload += struct.pack(">III", first_chunk, samples_per_chunk, 1)
    return full_box(b"stsc", 0, 0, payload)


def chunk_table(offsets: list[int], use_co64: bool = False) -> bytes:
    payload = struct.pack(">I", len(offsets))
    if use_co64:
        payload += struct.pack(">" + "Q" * len(offsets), *offsets)
        return full_box(b"co64", 0, 0, payload)
    payload += struct.pack(">" + "I" * len(offsets), *offsets)
    return full_box(b"stco", 0, 0, payload)


def stbl(
    sizes: list[int], entries: list[tuple[int, int]], offsets: list[int], use_co64: bool
) -> bytes:
    return box(
        b"stbl",
        stsd()
        + stts(len(sizes))
        + stsc(entries)
        + stsz(sizes)
        + chunk_table(offsets, use_co64),
    )


def trak(
    track_id: int,
    handler: bytes,
    sizes: list[int],
    entries: list[tuple[int, int]],
    offsets: list[int],
    use_co64: bool,
) -> bytes:
    return box(
        b"trak",
        tkhd(track_id)
        + box(
            b"mdia",
            mdhd()
            + hdlr(handler)
            + box(
                b"minf",
                stbl(sizes, entries, offsets, use_co64),
            ),
        ),
    )


def moov(
    tracks: list[bytes],
    *,
    mvex: bool = False,
    unknown: bool = False,
) -> bytes:
    payload = mvhd() + b"".join(tracks)
    if unknown:
        payload += box(b"zzzz", b"unknown-moov-payload")
    if mvex:
        payload += box(b"mvex", box(b"trex", b"\x00" * 20))
    return box(b"moov", payload)


def make_multitrack_unknown(path: os.PathLike[str] | str) -> Path:
    """Two tracks, variable samples, stco plus co64, and unknown boxes."""
    output = Path(path)
    t1_sizes = [5, 7, 9, 11, 13]
    t2_sizes = [3, 4, 6, 8, 10]
    t1_payloads = [
        bytes([0x10 + index]) * size for index, size in enumerate(t1_sizes)
    ]
    t2_payloads = [
        bytes([0x80 + index]) * size for index, size in enumerate(t2_sizes)
    ]
    t1_chunks = [
        b"".join(t1_payloads[0:2]),
        t1_payloads[2],
        b"".join(t1_payloads[3:5]),
    ]
    t2_chunks = [
        t2_payloads[0],
        b"".join(t2_payloads[1:3]),
        b"".join(t2_payloads[3:5]),
    ]
    prefix = ftyp() + box(b"abcd", b"unknown-top-level-prefix")
    mdat_start = len(prefix)
    payload_start = mdat_start + 8
    placements = [
        (1, 0),
        (2, 0),
        (1, 1),
        (2, 1),
        (1, 2),
        (2, 2),
    ]
    chunks = {(1, 0): t1_chunks[0], (1, 1): t1_chunks[1], (1, 2): t1_chunks[2],
              (2, 0): t2_chunks[0], (2, 1): t2_chunks[1], (2, 2): t2_chunks[2]}
    mdat_payload = bytearray()
    absolute_offsets: dict[tuple[int, int], int] = {}
    for key in placements:
        absolute_offsets[key] = payload_start + len(mdat_payload)
        mdat_payload.extend(chunks[key])
    track1 = trak(
        1,
        b"vide",
        t1_sizes,
        [(1, 2), (2, 1), (3, 2)],
        [absolute_offsets[(1, index)] for index in range(3)],
        use_co64=False,
    )
    track2 = trak(
        2,
        b"soun",
        t2_sizes,
        [(1, 1), (2, 2), (3, 2)],
        [absolute_offsets[(2, index)] for index in range(3)],
        use_co64=True,
    )
    trailing_unknown = box(b"junk", b"unknown-top-level-after-mdat")
    file_bytes = (
        prefix
        + box(b"mdat", bytes(mdat_payload))
        + trailing_unknown
        + moov([track1, track2], unknown=True)
    )
    output.write_bytes(file_bytes)
    return output


def make_fragmented(path: os.PathLike[str] | str) -> Path:
    """A clearly fragmented file with both mvex and a top-level moof."""
    output = Path(path)
    file_bytes = (
        ftyp()
        + moov([], mvex=True)
        + box(b"mdat", b"fragment-media")
        + box(b"moof", box(b"mfhd", b"\x00\x00\x00\x00\x00\x00\x00\x01"))
    )
    output.write_bytes(file_bytes)
    return output


def make_stco_overflow_sparse(
    path: os.PathLike[str] | str,
    *,
    sample_offset: int = 0xFFFFFF00,
) -> Path:
    """Create a sparse trailing-moov file whose final stco offset exceeds 2^32-1.

    The media sample sits near the 32-bit boundary.  Relayout inserts the moov
    before mdat, so the repaired chunk offset crosses the boundary and the
    writer must grow the moov by promoting stco to co64.
    """
    output = Path(path)
    sample = b"overflow-sample-" + bytes(range(48))
    prefix = ftyp()
    mdat_start = len(prefix)
    mdat_header_size = 16
    if sample_offset < mdat_start + mdat_header_size:
        raise ValueError("sample_offset is too small")
    moov_start = sample_offset + 128
    mdat_size = moov_start - mdat_start
    track = trak(
        1,
        b"vide",
        [len(sample)],
        [(1, 1)],
        [sample_offset],
        use_co64=False,
    )
    moov_bytes = moov([track])
    with open(output, "w+b") as stream:
        stream.write(prefix)
        stream.write(struct.pack(">I4sQ", 1, b"mdat", mdat_size))
        stream.seek(sample_offset)
        stream.write(sample)
        stream.seek(moov_start)
        stream.write(moov_bytes)
        stream.truncate(moov_start + len(moov_bytes))
    return output


def generate_all(
    directory: os.PathLike[str] | str, *, include_sparse: bool = True
) -> list[Path]:
    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        make_multitrack_unknown(output_dir / "multitrack_unknown.mp4"),
        make_fragmented(output_dir / "fragmented.mp4"),
    ]
    if include_sparse:
        paths.append(
            make_stco_overflow_sparse(output_dir / "stco_overflow_sparse.mp4")
        )
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        default=str(Path(__file__).resolve().parent),
        help="output directory (default: samples/)",
    )
    parser.add_argument(
        "--no-sparse",
        action="store_true",
        help="skip the 4 GiB sparse overflow fixture",
    )
    args = parser.parse_args()
    for path in generate_all(args.directory, include_sparse=not args.no_sparse):
        stat = path.stat()
        allocated = getattr(stat, "st_blocks", 0) * 512
        print(f"{path}: logical={stat.st_size}, allocated={allocated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
