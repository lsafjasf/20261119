"""Frame-chain parsing, corruption diagnostics, and stream-level accounting."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .header import FrameHeader, HeaderError, parse_header
from .sideinfo import SideInfo, SideInfoError, parse_side_info, validate_side_info
from .tags import TagInfo, leading_tags, trailing_tags


@dataclass
class Diagnostic:
    code: str
    severity: str
    message: str
    offset: int | None = None
    frame_index: int | None = None
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.offset is not None:
            result["offset"] = self.offset
        if self.frame_index is not None:
            result["frame_index"] = self.frame_index
        if self.details:
            result["details"] = self.details
        return result


@dataclass(frozen=True)
class ReservoirRange:
    main_data_begin: int
    available_history: int
    reservoir_limit: int
    source_start: int
    source_end: int
    history_frame_start: int | None
    history_frame_end: int | None
    underflow: bool
    prefill: bool
    overrun: bool
    limit_exceeded: bool
    crosses_gap: bool

    @property
    def uses_previous_frames(self) -> bool:
        return self.main_data_begin > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "main_data_begin": self.main_data_begin,
            "available_history": self.available_history,
            "reservoir_limit": self.reservoir_limit,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "history_frame_start": self.history_frame_start,
            "history_frame_end": self.history_frame_end,
            "underflow": self.underflow,
            "prefill": self.prefill,
            "overrun": self.overrun,
            "limit_exceeded": self.limit_exceeded,
            "crosses_gap": self.crosses_gap,
        }


@dataclass
class FrameRecord:
    index: int
    header: FrameHeader
    side_info: SideInfo | None
    payload_start: int
    payload_end: int
    next_offset: int | None = None
    length_drift: int | None = None
    reservoir: ReservoirRange | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def damaged(self) -> bool:
        return any(diagnostic.severity == "error" for diagnostic in self.diagnostics)

    @property
    def status(self) -> str:
        return "damaged" if self.damaged else "ok"

    def header_dict(self) -> dict[str, object]:
        header = self.header
        return {
            "version": header.version_name,
            "layer": header.layer_name,
            "bitrate": header.bitrate,
            "bitrate_kbps": header.bitrate // 1000,
            "sample_rate": header.sample_rate,
            "padding": header.padding,
            "channel_mode": header.channel_mode_name,
            "channels": header.channels,
            "crc": header.has_crc,
            "frame_length": header.frame_length,
        }

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "index": self.index,
            "offset": self.header.offset,
            "end_offset": self.header.end_offset,
            "header": self.header_dict(),
            "payload_start": self.payload_start,
            "payload_end": self.payload_end,
            "next_offset": self.next_offset,
            "length_drift": self.length_drift,
            "status": self.status,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }
        if self.side_info is not None:
            result["side_info"] = {
                "main_data_begin": self.side_info.main_data_begin,
                "private_bits": self.side_info.private_bits,
                "scfsi": [list(bands) for bands in self.side_info.scfsi],
                "part2_3_bits": self.side_info.part2_3_bits,
                "part2_3_bytes": self.side_info.part2_3_bytes,
                "granules": [
                    {
                        "granule": granule.granule,
                        "channel": granule.channel,
                        "part2_3_length": granule.part2_3_length,
                        "big_values": granule.big_values,
                        "global_gain": granule.global_gain,
                        "scalefac_compress": granule.scalefac_compress,
                        "window_switching_flag": granule.window_switching_flag,
                        "block_type": granule.block_type,
                        "mixed_block_flag": granule.mixed_block_flag,
                        "table_select": list(granule.table_select),
                        "subblock_gain": list(granule.subblock_gain),
                        "region0_count": granule.region0_count,
                        "region1_count": granule.region1_count,
                        "preflag": granule.preflag,
                        "scalefac_scale": granule.scalefac_scale,
                        "count1table_select": granule.count1table_select,
                    }
                    for granule in self.side_info.granules
                ],
            }
        if self.reservoir is not None:
            result["reservoir"] = self.reservoir.to_dict()
        return result


@dataclass
class AuditResult:
    source: str
    file_size: int
    audio_start: int
    audio_end: int
    tags: list[TagInfo]
    frames: list[FrameRecord]
    diagnostics: list[Diagnostic]

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def total_frame_bytes(self) -> int:
        return sum(frame.header.frame_length for frame in self.frames)

    @property
    def duration(self) -> float:
        return sum(
            frame.header.samples_per_frame / frame.header.sample_rate
            for frame in self.frames
        )

    @property
    def actual_bitrate(self) -> float:
        return self.total_frame_bytes * 8 / self.duration if self.duration else 0.0

    @property
    def bitrate_histogram(self) -> Counter[int]:
        return Counter(frame.header.bitrate for frame in self.frames)

    @property
    def stream_kind(self) -> str:
        modes = set(self.bitrate_histogram)
        if not modes:
            return "unknown"
        return "CBR" if len(modes) == 1 else "VBR"

    @property
    def damaged_frames(self) -> list[FrameRecord]:
        return [frame for frame in self.frames if frame.damaged]

    @property
    def error_count(self) -> int:
        return sum(diagnostic.severity == "error" for diagnostic in self.diagnostics)

    @property
    def warning_count(self) -> int:
        return sum(diagnostic.severity == "warning" for diagnostic in self.diagnostics)

    def to_dict(self) -> dict[str, object]:
        histogram = self.bitrate_histogram
        return {
            "source": self.source,
            "file_size": self.file_size,
            "audio_start": self.audio_start,
            "audio_end": self.audio_end,
            "stream_kind": self.stream_kind,
            "frame_count": self.frame_count,
            "frame_bytes": self.total_frame_bytes,
            "duration_seconds": round(self.duration, 9),
            "actual_bitrate": round(self.actual_bitrate, 3),
            "actual_bitrate_kbps": round(self.actual_bitrate / 1000, 3),
            "bitrate_histogram": {
                str(bitrate // 1000): count
                for bitrate, count in sorted(histogram.items())
            },
            "damaged_frame_count": len(self.damaged_frames),
            "damaged_frames": [frame.index for frame in self.damaged_frames],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "tags": [tag.to_dict() for tag in self.tags],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "frames": [frame.to_dict() for frame in self.frames],
        }


def _same_stream(first: FrameHeader, candidate: FrameHeader) -> bool:
    return (
        first.version == candidate.version
        and first.layer == candidate.layer
        and first.sample_rate == candidate.sample_rate
    )


def _header_at(data: bytes, offset: int, audio_end: int) -> FrameHeader | None:
    if offset < 0 or offset + 4 > audio_end:
        return None
    try:
        header = parse_header(data, offset)
    except HeaderError:
        return None
    return header if header.end_offset <= audio_end else None


def _chain_score(
    data: bytes,
    offset: int,
    audio_end: int,
    *,
    max_frames: int = 4,
    signature: FrameHeader | None = None,
) -> int:
    """Count consecutive, size-consistent headers starting at *offset*."""

    first = signature
    position = offset
    score = 0
    while position < audio_end and score < max_frames:
        header = _header_at(data, position, audio_end)
        if header is None:
            break
        if first is None:
            first = header
        elif not _same_stream(first, header):
            break
        score += 1
        if header.end_offset == audio_end:
            break
        position = header.end_offset
    return score


def _candidate_ok(
    data: bytes,
    header: FrameHeader,
    audio_end: int,
    *,
    signature: FrameHeader | None = None,
) -> bool:
    if signature is not None and not _same_stream(signature, header):
        return False
    if header.end_offset == audio_end:
        return True
    if header.end_offset + 4 <= audio_end:
        try:
            following = parse_header(data, header.end_offset)
        except HeaderError:
            following = None
        if following is not None and _same_stream(
            signature or header, following
        ):
            return True
    return _chain_score(
        data,
        header.offset,
        audio_end,
        max_frames=3,
        signature=signature or header,
    ) >= 2


def _sync_candidates(data: bytes, start: int, end: int) -> Iterable[int]:
    position = start
    while position + 1 < end:
        found = data.find(b"\xff", position, end)
        if found < 0 or found + 1 >= end:
            return
        if data[found + 1] & 0xE0 == 0xE0:
            yield found
        position = found + 1


def _find_candidate(
    data: bytes,
    start: int,
    audio_end: int,
    *,
    signature: FrameHeader | None = None,
    preferred: int | None = None,
    search_limit: int | None = None,
) -> FrameHeader | None:
    """Find a header backed by a consistent following frame.

    A sync word alone is deliberately insufficient.  The candidate must either
    end exactly at the audio boundary or be followed by another legal header at
    its header-derived end offset.
    """

    scan_start = max(0, start)
    scan_end = audio_end
    if search_limit is not None and preferred is not None:
        scan_start = max(scan_start, preferred - search_limit)
        scan_end = min(scan_end, preferred + search_limit)

    best: tuple[tuple[int, int], FrameHeader] | None = None
    for position in _sync_candidates(data, scan_start, scan_end):
        header = _header_at(data, position, audio_end)
        if header is None or not _candidate_ok(
            data, header, audio_end, signature=signature
        ):
            continue
        score = _chain_score(
            data,
            position,
            audio_end,
            max_frames=4,
            signature=signature or header,
        )
        distance = abs(position - preferred) if preferred is not None else position
        rank = (score, -distance)
        if best is None or rank > best[0]:
            best = (rank, header)
            if preferred is None:
                break
    return best[1] if best else None


def _tag_diagnostics(data: bytes, tags: list[TagInfo]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if data.startswith(b"ID3") and not any(tag.offset == 0 for tag in tags):
        diagnostics.append(
            Diagnostic(
                "invalid_id3v2_header",
                "error",
                "file begins with ID3 but the tag header/size is invalid or truncated",
                offset=0,
            )
        )
    for tag in tags:
        if tag.end_offset > len(data):
            diagnostics.append(
                Diagnostic(
                    "truncated_tag",
                    "error",
                    f"{tag.kind} extends beyond end of file",
                    offset=tag.offset,
                    details={"declared_size": tag.size},
                )
            )
    return diagnostics


def _parse_frames(
    data: bytes, audio_start: int, audio_end: int
) -> tuple[list[FrameRecord], list[Diagnostic]]:
    frames: list[FrameRecord] = []
    diagnostics: list[Diagnostic] = []

    first = _find_candidate(data, audio_start, audio_end)
    if first is None:
        sync_count = sum(1 for _ in _sync_candidates(data, audio_start, audio_end))
        diagnostics.append(
            Diagnostic(
                "no_valid_frames",
                "error",
                "sync candidates were not backed by a consistent frame boundary",
                offset=audio_start,
                details={"sync_candidates": sync_count},
            )
        )
        return frames, diagnostics

    if first.offset > audio_start:
        diagnostics.append(
            Diagnostic(
                "leading_junk",
                "warning",
                "skipped bytes before the first validated frame",
                offset=audio_start,
                details={"bytes": first.offset - audio_start},
            )
        )

    position = first.offset
    while position < audio_end:
        try:
            header = parse_header(data, position)
        except HeaderError as exc:
            diagnostics.append(
                Diagnostic(
                    "invalid_frame_header",
                    "error",
                    f"expected frame boundary does not contain a legal header: {exc.reason}",
                    offset=position,
                    details={
                        "reason": exc.reason,
                        "had_sync": exc.sync,
                        "bytes_skipped": 0,
                    },
                )
            )
            signature = frames[-1].header if frames else None
            replacement = _find_candidate(
                data,
                position + 1,
                audio_end,
                signature=signature,
            )
            if replacement is None:
                diagnostics.append(
                    Diagnostic(
                        "unparsed_trailing_data",
                        "error",
                        "bytes after the last valid frame do not form a validated frame chain",
                        offset=position,
                        details={"bytes": audio_end - position},
                    )
                )
                break
            diagnostics[-1].details["bytes_skipped"] = replacement.offset - position
            diagnostics.append(
                Diagnostic(
                    "resynchronised",
                    "warning",
                    "resynchronised at a header with a consistent following boundary",
                    offset=replacement.offset,
                )
            )
            position = replacement.offset
            continue

        frame_end = header.end_offset
        frame_diagnostics: list[Diagnostic] = []
        side_info: SideInfo | None = None
        if frame_end > audio_end:
            frame_diagnostics.append(
                Diagnostic(
                    "truncated_frame",
                    "error",
                    "header-derived frame length extends past the audio boundary",
                    offset=position,
                    frame_index=len(frames),
                    details={
                        "expected_end": frame_end,
                        "audio_end": audio_end,
                    },
                )
            )
        elif header.has_side_info:
            side_start = position + header.header_size
            try:
                side_info = parse_side_info(
                    data[side_start : min(frame_end, audio_end)], header
                )
                for issue in validate_side_info(side_info):
                    frame_diagnostics.append(
                        Diagnostic(
                            "bad_side_info_semantics",
                            "error",
                            (
                                f"granule {issue.granule}, channel {issue.channel}: "
                                f"{issue.message}"
                            ),
                            offset=side_start,
                            frame_index=len(frames),
                            details={
                                "reason": issue.code,
                                "granule": issue.granule,
                                "channel": issue.channel,
                            },
                        )
                    )
            except SideInfoError as exc:
                frame_diagnostics.append(
                    Diagnostic(
                        "bad_side_info",
                        "error",
                        str(exc),
                        offset=side_start,
                        frame_index=len(frames),
                    )
                )

        record = FrameRecord(
            index=len(frames),
            header=header,
            side_info=side_info,
            payload_start=position + header.main_data_offset,
            payload_end=min(frame_end, audio_end),
            diagnostics=frame_diagnostics,
        )
        frames.append(record)

        if frame_end >= audio_end:
            record.next_offset = audio_end
            if frame_end > audio_end:
                break
            position = audio_end
            continue

        next_header: FrameHeader | None = None
        if frame_end + 4 <= audio_end:
            try:
                next_header = parse_header(data, frame_end)
            except HeaderError:
                next_header = None
        if next_header is not None and _same_stream(header, next_header):
            record.next_offset = frame_end
            position = frame_end
            continue

        replacement = _find_candidate(
            data,
            position + header.main_data_offset,
            audio_end,
            signature=header,
            preferred=frame_end,
            search_limit=262144,
        )
        if replacement is None:
            record.diagnostics.append(
                Diagnostic(
                    "missing_valid_next_frame",
                    "error",
                    "no validated frame starts where the header-derived length ends",
                    offset=frame_end,
                    frame_index=record.index,
                )
            )
            record.next_offset = None
            position = audio_end
            continue

        drift = replacement.offset - frame_end
        record.next_offset = replacement.offset
        record.length_drift = drift
        record.diagnostics.append(
            Diagnostic(
                "frame_length_drift",
                "error",
                "next validated frame is not at the header-derived boundary",
                offset=frame_end,
                frame_index=record.index,
                details={
                    "expected_next": frame_end,
                    "actual_next": replacement.offset,
                    "drift": drift,
                },
            )
        )
        position = replacement.offset

    return frames, diagnostics


def _interval_physical(
    intervals: list[tuple[int, int, int, int]], logical: int, *, end: bool = False
) -> int:
    for interval_start, interval_end, physical_start, _index in intervals:
        if interval_start <= logical < interval_end:
            return physical_start + logical - interval_start
        if end and logical == interval_end:
            return physical_start + interval_end - interval_start
    if intervals:
        first = intervals[-1]
        last = intervals[0]
        if logical <= first[0]:
            return first[2]
        if logical >= last[1]:
            return last[2] + last[1] - last[0]
    return 0


def _analyse_reservoir(frames: list[FrameRecord]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    payload_lengths = [
        max(0, frame.payload_end - frame.payload_start) for frame in frames
    ]

    for index, frame in enumerate(frames):
        side_info = frame.side_info
        if side_info is None:
            continue

        intervals: list[tuple[int, int, int, int]] = [
            (0, payload_lengths[index], frame.payload_start, index)
        ]
        logical_cursor = 0
        cumulative = 0
        for previous in range(index - 1, -1, -1):
            logical_cursor -= payload_lengths[previous]
            intervals.append(
                (
                    logical_cursor,
                    logical_cursor + payload_lengths[previous],
                    frames[previous].payload_start,
                    previous,
                )
            )
            cumulative += payload_lengths[previous]
            if cumulative >= side_info.reservoir_limit:
                break
        intervals.sort(key=lambda item: item[0])

        logical_start = -side_info.main_data_begin
        logical_end = logical_start + side_info.part2_3_bytes
        available = min(cumulative, side_info.reservoir_limit)
        underflow = side_info.main_data_begin > available
        limit_exceeded = side_info.main_data_begin > side_info.reservoir_limit
        overrun = logical_end > payload_lengths[index]
        crosses_gap = any(
            frames[current].header.offset != frames[current - 1].header.end_offset
            for current in range(max(1, index - 1), index + 1)
        )

        touched = [
            interval
            for interval in intervals
            if interval[0] < logical_end and interval[1] > logical_start
        ]
        history_indices = [item[3] for item in touched]
        source_start = _interval_physical(intervals, logical_start)
        source_end = _interval_physical(intervals, logical_end, end=True)
        reservoir = ReservoirRange(
            main_data_begin=side_info.main_data_begin,
            available_history=available,
            reservoir_limit=side_info.reservoir_limit,
            source_start=source_start,
            source_end=source_end,
            history_frame_start=min(history_indices) if history_indices else None,
            history_frame_end=max(history_indices) if history_indices else None,
            underflow=underflow,
            prefill=index == 0 and side_info.main_data_begin > 0,
            overrun=overrun,
            limit_exceeded=limit_exceeded,
            crosses_gap=crosses_gap,
        )
        frame.reservoir = reservoir

        if underflow:
            code = "prefill_missing_history" if reservoir.prefill else "reservoir_underflow"
            message = (
                "first frame refers to bytes before the captured stream (prefill/truncated start)"
                if reservoir.prefill
                else "main_data_begin exceeds the contiguous history from previous frames"
            )
            diagnostic = Diagnostic(
                code,
                "error",
                message,
                offset=frame.header.offset,
                frame_index=index,
                details={
                    "main_data_begin": side_info.main_data_begin,
                    "available_history": available,
                },
            )
            frame.diagnostics.append(diagnostic)
            diagnostics.append(diagnostic)
        if limit_exceeded:
            diagnostic = Diagnostic(
                "reservoir_limit_exceeded",
                "error",
                "main_data_begin is outside the MPEG Layer III reservoir window",
                offset=frame.header.offset,
                frame_index=index,
                details={
                    "main_data_begin": side_info.main_data_begin,
                    "limit": side_info.reservoir_limit,
                },
            )
            frame.diagnostics.append(diagnostic)
            diagnostics.append(diagnostic)
        if overrun:
            diagnostic = Diagnostic(
                "main_data_overrun",
                "error",
                "part2_3 data extends beyond this frame's main-data payload",
                offset=frame.header.offset,
                frame_index=index,
                details={
                    "part2_3_bytes": side_info.part2_3_bytes,
                    "available_from_source": payload_lengths[index]
                    + side_info.main_data_begin,
                },
            )
            frame.diagnostics.append(diagnostic)
            diagnostics.append(diagnostic)
        if reservoir.crosses_gap:
            diagnostic = Diagnostic(
                "reservoir_crosses_gap",
                "warning",
                "reservoir history crosses a non-adjacent frame boundary",
                offset=frame.header.offset,
                frame_index=index,
            )
            frame.diagnostics.append(diagnostic)
            diagnostics.append(diagnostic)

    return diagnostics


def audit_bytes(data: bytes | bytearray | memoryview, source: str = "<bytes>") -> AuditResult:
    """Audit an in-memory MP3 stream without decoding audio."""

    raw = bytes(data)
    tags = leading_tags(raw)
    audio_start = tags[-1].end_offset if tags else 0
    trailing = trailing_tags(raw, audio_start)
    audio_end = trailing[0].offset if trailing else len(raw)
    tags.extend(trailing)

    diagnostics = _tag_diagnostics(raw, tags)
    frames: list[FrameRecord] = []
    if audio_start >= audio_end:
        if not diagnostics:
            diagnostics.append(
                Diagnostic(
                    "no_audio_payload",
                    "error",
                    "no MPEG audio payload remains after tag handling",
                    offset=audio_start,
                )
            )
    else:
        frames, frame_diagnostics = _parse_frames(raw, audio_start, audio_end)
        diagnostics.extend(frame_diagnostics)
        diagnostics.extend(_analyse_reservoir(frames))
        seen = {id(diagnostic) for diagnostic in diagnostics}
        for frame in frames:
            for diagnostic in frame.diagnostics:
                if id(diagnostic) not in seen:
                    diagnostics.append(diagnostic)
                    seen.add(id(diagnostic))
    diagnostics.sort(
        key=lambda diagnostic: (
            diagnostic.offset if diagnostic.offset is not None else 1 << 62,
            diagnostic.frame_index if diagnostic.frame_index is not None else -1,
            diagnostic.code,
        )
    )

    return AuditResult(
        source=source,
        file_size=len(raw),
        audio_start=audio_start,
        audio_end=audio_end,
        tags=tags,
        frames=frames,
        diagnostics=diagnostics,
    )


def audit_file(path: str | Path) -> AuditResult:
    source = str(path)
    return audit_bytes(Path(path).read_bytes(), source=source)
