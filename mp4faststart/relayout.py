"""Move a trailing ``moov`` before media data and repair chunk offsets."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import tempfile
from typing import Any, Iterable

from .boxes import (
    UINT32_MAX,
    BoxNode,
    TopLevelBox,
    parse_box_tree,
    parse_top_level,
    walk_nodes,
)
from .errors import FragmentedMP4Error, MP4Error
from .streamcopy import copy_range

FRAGMENT_TOP_LEVEL_TYPES = {"moof", "mfra", "styp", "sidx"}


@dataclass(eq=False)
class ChunkTable:
    track_index: int
    node: BoxNode
    original_type: str
    version_flags: bytes
    entries: list[int]
    new_entries: list[int] | None = None
    upgraded: bool = False

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def new_type(self) -> str:
        if self.upgraded:
            return "co64"
        return self.original_type


@dataclass
class FastStartResult:
    report: dict[str, Any]
    already_faststart: bool
    upgraded_tables: int

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.report, indent=indent, sort_keys=True)


def faststart(source: os.PathLike[str] | str, destination: os.PathLike[str] | str) -> FastStartResult:
    """Relayout ``source`` into ``destination``.

    ``source`` and ``destination`` must differ.  The output is written through
    a temporary file and atomically replaced while all media payloads are
    copied in bounded chunks.
    """
    src_path = Path(source)
    dst_path = Path(destination)
    if not src_path.is_file():
        raise MP4Error(f"input file does not exist: {src_path}")
    if os.path.realpath(src_path) == os.path.realpath(dst_path):
        raise MP4Error("input and output paths must differ")

    boxes, file_size = parse_top_level(src_path)
    _reject_fragmented_top_level(boxes)
    moov_boxes = [box for box in boxes if box.type == "moov"]
    if len(moov_boxes) != 1:
        raise MP4Error(f"expected exactly one top-level moov box, found {len(moov_boxes)}")
    moov_box = moov_boxes[0]
    mdat_boxes = [box for box in boxes if box.type == "mdat"]
    if not mdat_boxes:
        raise MP4Error("no top-level mdat box found")

    with open(src_path, "rb") as source_stream:
        source_stream.seek(moov_box.offset)
        moov_raw = source_stream.read(moov_box.size)
    if len(moov_raw) != moov_box.size:
        raise MP4Error("truncated moov box")
    moov_node = parse_box_tree(moov_raw, base_offset=moov_box.offset)[0]
    _reject_fragmented_moov(moov_node)

    insertion_offset = min(box.offset for box in mdat_boxes)
    if moov_box.offset < insertion_offset:
        return _copy_already_faststart(
            src_path, dst_path, boxes, file_size, moov_box, moov_node
        )

    chunk_tables = _collect_chunk_tables(moov_node)
    _validate_source_chunk_offsets(
        chunk_tables, file_size, insertion_offset, moov_box.offset, moov_box.end
    )
    upgraded = _find_upgrades(
        chunk_tables,
        old_moov_size=moov_box.size,
        insertion=insertion_offset,
        moov_start=moov_box.offset,
        moov_end=moov_box.end,
    )
    _apply_chunk_updates(
        chunk_tables,
        upgraded,
        old_moov_size=moov_box.size,
        insertion=insertion_offset,
        moov_start=moov_box.offset,
        moov_end=moov_box.end,
    )
    _mark_dirty_ancestors(moov_node)
    new_moov_raw = moov_node.to_bytes()
    expected_moov_size = moov_box.size + 4 * sum(
        table.entry_count for table in upgraded
    )
    if len(new_moov_raw) != expected_moov_size:
        raise MP4Error(
            "internal error: recalculated moov size does not match serialized size "
            f"({expected_moov_size} != {len(new_moov_raw)})"
        )

    _write_relayout(
        src_path,
        dst_path,
        file_size=file_size,
        insertion=insertion_offset,
        moov_start=moov_box.offset,
        moov_end=moov_box.end,
        old_moov_size=moov_box.size,
        new_moov_raw=new_moov_raw,
    )
    report = _build_report(
        src_path,
        dst_path,
        boxes,
        file_size=file_size,
        insertion=insertion_offset,
        moov_box=moov_box,
        new_moov_size=len(new_moov_raw),
        moved=True,
        chunk_tables=chunk_tables,
    )
    return FastStartResult(
        report=report,
        already_faststart=False,
        upgraded_tables=len(upgraded),
    )


def _reject_fragmented_top_level(boxes: Iterable[TopLevelBox]) -> None:
    found = sorted({box.type for box in boxes if box.type in FRAGMENT_TOP_LEVEL_TYPES})
    if found:
        raise FragmentedMP4Error(
            "fragmented MP4 is not supported; found top-level box(es): "
            + ", ".join(found)
        )


def _reject_fragmented_moov(moov: BoxNode) -> None:
    found = sorted({node.type for node in walk_nodes([moov]) if node.type == "mvex"})
    if found:
        raise FragmentedMP4Error(
            "fragmented MP4 is not supported; moov contains mvex"
        )


def _child(node: BoxNode, box_type: str) -> BoxNode | None:
    if node.children is None:
        return None
    matches = [child for child in node.children if child.type == box_type]
    if len(matches) > 1:
        raise MP4Error(f"box {node.type!r} contains multiple {box_type!r} children")
    return matches[0] if matches else None


def _collect_chunk_tables(moov: BoxNode) -> list[ChunkTable]:
    if moov.children is None:
        raise MP4Error("moov box has no children")
    tables: list[ChunkTable] = []
    track_index = 0
    for trak in (child for child in moov.children if child.type == "trak"):
        track_index += 1
        mdia = _child(trak, "mdia")
        minf = _child(mdia, "minf") if mdia is not None else None
        stbl = _child(minf, "stbl") if minf is not None else None
        if stbl is None or stbl.children is None:
            continue
        for node in stbl.children:
            if node.type in {"stco", "co64"}:
                tables.append(_parse_chunk_table(node, track_index))
    return tables


def _parse_chunk_table(node: BoxNode, track_index: int) -> ChunkTable:
    payload = node.payload[node.header_size :]
    if len(payload) < 8:
        raise MP4Error(f"truncated {node.type} box")
    version_flags = payload[:4]
    entry_count = struct.unpack_from(">I", payload, 4)[0]
    width = 4 if node.type == "stco" else 8
    expected = 8 + entry_count * width
    if len(payload) != expected:
        raise MP4Error(
            f"{node.type} entry count {entry_count} requires {expected} payload bytes, "
            f"found {len(payload)}"
        )
    fmt = ">" + ("I" if width == 4 else "Q") * entry_count
    entries = list(struct.unpack_from(fmt, payload, 8)) if entry_count else []
    if width == 4 and any(entry > UINT32_MAX for entry in entries):
        raise MP4Error("invalid stco entry")
    return ChunkTable(
        track_index=track_index,
        node=node,
        original_type=node.type,
        version_flags=version_flags,
        entries=entries,
    )


def _validate_source_chunk_offsets(
    tables: Iterable[ChunkTable],
    file_size: int,
    insertion: int,
    moov_start: int,
    moov_end: int,
) -> None:
    del insertion
    for table in tables:
        for entry in table.entries:
            if entry >= file_size:
                raise MP4Error(
                    f"chunk offset {entry} in track {table.track_index} is outside the file"
                )
            if moov_start <= entry < moov_end:
                raise MP4Error(
                    f"chunk offset {entry} in track {table.track_index} points inside moov"
                )


def _remap_position(
    position: int,
    *,
    insertion: int,
    moov_start: int,
    moov_end: int,
    new_moov_size: int,
    old_moov_size: int,
) -> int:
    if position < insertion:
        return position
    if position < moov_start:
        return position + new_moov_size
    if position < moov_end:
        raise MP4Error(f"offset {position} points inside the relocated moov")
    return position + new_moov_size - old_moov_size


def _find_upgrades(
    tables: Iterable[ChunkTable],
    *,
    old_moov_size: int,
    insertion: int,
    moov_start: int,
    moov_end: int,
) -> set[ChunkTable]:
    upgraded: set[ChunkTable] = set()
    candidates = [table for table in tables if table.original_type == "stco"]
    while True:
        new_moov_size = old_moov_size + 4 * sum(
            table.entry_count for table in upgraded
        )
        newly_upgraded: set[ChunkTable] = set()
        for table in candidates:
            if table in upgraded:
                continue
            for entry in table.entries:
                new_offset = _remap_position(
                    entry,
                    insertion=insertion,
                    moov_start=moov_start,
                    moov_end=moov_end,
                    new_moov_size=new_moov_size,
                    old_moov_size=old_moov_size,
                )
                if new_offset > UINT32_MAX:
                    newly_upgraded.add(table)
                    break
        if not newly_upgraded:
            return upgraded
        upgraded.update(newly_upgraded)


def _apply_chunk_updates(
    tables: Iterable[ChunkTable],
    upgraded: set[ChunkTable],
    *,
    old_moov_size: int,
    insertion: int,
    moov_start: int,
    moov_end: int,
) -> None:
    new_moov_size = old_moov_size + 4 * sum(table.entry_count for table in upgraded)
    for table in tables:
        table.upgraded = table in upgraded
        table.new_entries = [
            _remap_position(
                entry,
                insertion=insertion,
                moov_start=moov_start,
                moov_end=moov_end,
                new_moov_size=new_moov_size,
                old_moov_size=old_moov_size,
            )
            for entry in table.entries
        ]
        new_type = table.new_type
        if new_type == "stco" and any(entry > UINT32_MAX for entry in table.new_entries):
            raise MP4Error("stco overflow was not upgraded to co64")
        fmt = ">" + ("I" if new_type == "stco" else "Q") * len(table.new_entries)
        entries = struct.pack(fmt, *table.new_entries) if table.new_entries else b""
        payload = (
            table.version_flags
            + struct.pack(">I", len(table.new_entries))
            + entries
        )
        table.node.replacement_type = new_type
        table.node.replacement_payload = payload
        table.node.dirty = True


def _mark_dirty_ancestors(node: BoxNode) -> bool:
    if node.children is None:
        return node.dirty
    child_dirty = False
    for child in node.children:
        if _mark_dirty_ancestors(child):
            child_dirty = True
    if child_dirty:
        node.dirty = True
    return node.dirty


def _copy_already_faststart(
    src_path: Path,
    dst_path: Path,
    boxes: list[TopLevelBox],
    file_size: int,
    moov_box: TopLevelBox,
    moov_node: BoxNode,
) -> FastStartResult:
    _atomic_write(
        src_path,
        dst_path,
        lambda dst, src_fd: _copy_whole_file(dst, src_fd, file_size),
    )
    tables = _collect_chunk_tables(moov_node)
    for table in tables:
        table.new_entries = list(table.entries)
    report = _build_report(
        src_path,
        dst_path,
        boxes,
        file_size=file_size,
        insertion=min((box.offset for box in boxes if box.type == "mdat"), default=0),
        moov_box=moov_box,
        new_moov_size=moov_box.size,
        moved=False,
        chunk_tables=tables,
    )
    return FastStartResult(
        report=report,
        already_faststart=True,
        upgraded_tables=0,
    )


def _write_relayout(
    src_path: Path,
    dst_path: Path,
    *,
    file_size: int,
    insertion: int,
    moov_start: int,
    moov_end: int,
    old_moov_size: int,
    new_moov_raw: bytes,
) -> None:
    new_file_size = file_size - old_moov_size + len(new_moov_raw)

    def write(dst: Any, src_fd: int) -> None:
        copy_range(src_fd, dst, 0, insertion)
        dst.write(new_moov_raw)
        copy_range(src_fd, dst, insertion, moov_start - insertion)
        copy_range(src_fd, dst, moov_end, file_size - moov_end)
        dst.flush()
        dst.truncate(new_file_size)

    _atomic_write(src_path, dst_path, write)


def _copy_whole_file(dst: Any, src_fd: int, file_size: int) -> None:
    copy_range(src_fd, dst, 0, file_size)
    dst.flush()
    dst.truncate(file_size)


def _atomic_write(src_path: Path, dst_path: Path, writer: Any) -> None:
    parent = dst_path.parent if str(dst_path.parent) else Path(".")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{dst_path.name}.", suffix=".tmp", dir=parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w+b") as dst, open(src_path, "rb") as source_stream:
            writer(dst, source_stream.fileno())
            dst.flush()
        os.replace(temporary_path, dst_path)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _build_report(
    src_path: Path,
    dst_path: Path,
    boxes: list[TopLevelBox],
    *,
    file_size: int,
    insertion: int,
    moov_box: TopLevelBox,
    new_moov_size: int,
    moved: bool,
    chunk_tables: Iterable[ChunkTable],
) -> dict[str, Any]:
    table_rows: list[dict[str, Any]] = []
    for box in boxes:
        if moved and box.offset == moov_box.offset:
            new_offset = insertion
        elif not moved:
            new_offset = box.offset
        else:
            new_offset = _remap_position(
                box.offset,
                insertion=insertion,
                moov_start=moov_box.offset,
                moov_end=moov_box.end,
                new_moov_size=new_moov_size,
                old_moov_size=moov_box.size,
            )
        new_size = new_moov_size if box.type == "moov" else box.size
        if box.type == "moov" and moved:
            action = "moved"
        elif new_offset == box.offset and new_size == box.size:
            action = "unchanged"
        else:
            action = "shifted"
        table_rows.append(
            {
                "type": box.type,
                "old_offset": box.offset,
                "new_offset": new_offset,
                "old_size": box.size,
                "new_size": new_size,
                "action": action,
            }
        )

    chunk_rows: list[dict[str, Any]] = []
    for table in chunk_tables:
        chunk_rows.append(
            {
                "track": table.track_index,
                "old_type": table.original_type,
                "new_type": table.new_type,
                "entry_count": table.entry_count,
                "upgraded": table.upgraded,
                "first_old_offset": table.entries[0] if table.entries else None,
                "first_new_offset": (
                    table.new_entries[0] if table.new_entries else None
                ),
            }
        )

    output_file_size = file_size - moov_box.size + new_moov_size if moved else file_size
    return {
        "schema_version": 1,
        "input": str(src_path),
        "output": str(dst_path),
        "fragmented": False,
        "already_faststart": not moved,
        "input_size": file_size,
        "output_size": output_file_size,
        "moov": {
            "old_offset": moov_box.offset,
            "new_offset": insertion if moved else moov_box.offset,
            "old_size": moov_box.size,
            "new_size": new_moov_size,
            "moved": moved,
        },
        "boxes": table_rows,
        "chunk_tables": chunk_rows,
        "upgraded_stco_count": sum(1 for row in chunk_rows if row["upgraded"]),
    }
